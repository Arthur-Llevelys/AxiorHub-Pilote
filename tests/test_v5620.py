"""5.6.20 : mode poste (dossiers utilisateur, configuration sans secret, compte local, services en sous-processus, fenêtre) et
paquet Debian « poste » (amd64, entrée de menu, lanceur, unité utilisateur, arbre complet)."""
import configparser
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import sys
import tarfile
import tempfile
import unittest
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
CURRENT = re.search(r"__version__\s*=\s*'([^']+)'", (ROOT / 'agent' / '__init__.py').read_text(encoding='utf-8')).group(1)


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


def free_port():
    import socket
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0)); return s.getsockname()[1]


class Bootstrap(unittest.TestCase):
    def test_first_run_creates_secret_free_config_and_local_account_then_only_updates_origin(self):
        poste = load('poste_5620', ROOT / 'poste.py')
        from agent.common import load_config
        with tempfile.TemporaryDirectory() as home:
            p = poste.paths(home)
            auth = poste.bootstrap(p, 8790)
            self.assertTrue(p['config'].is_file()); self.assertTrue((p['data'] / 'secrets').is_dir())
            cfg = load_config(p['config'])
            self.assertEqual(Path(cfg['state_dir']).resolve(), (p['data'] / 'state').resolve())
            self.assertEqual(cfg['ollama']['url'], 'http://127.0.0.1:11434'); self.assertEqual(cfg['speech568']['url'], 'http://127.0.0.1:8880')
            self.assertNotIn('installation', cfg)                                              # l'assistant d'installation s'affiche
            self.assertEqual(auth['origin'], 'http://127.0.0.1:8790'); self.assertEqual(auth['username'], 'admin'); self.assertEqual(auth['prefix'], '/agent-courriel')
            user, password = poste.credentials(p)
            self.assertEqual(user, 'admin'); self.assertGreaterEqual(len(password), 20)
            self.assertEqual(auth['hash'], hashlib.scrypt(password.encode(), salt=bytes.fromhex(auth['salt']), n=16384, r=8, p=1).hex())
            if os.name == 'posix':
                self.assertEqual(p['password'].stat().st_mode & 0o777, 0o600); self.assertEqual(p['auth'].stat().st_mode & 0o777, 0o600)
            for secret in (p['data'] / 'secrets').glob('*.secret'):
                if secret.name != 'internal-api-token.secret':                                   # jeton interne aléatoire, le reste non configuré
                    self.assertEqual(secret.read_text().strip(), 'not-configured')
            before = p['config'].read_bytes()
            again = poste.bootstrap(p, 8791)
            self.assertEqual(again['origin'], 'http://127.0.0.1:8791'); self.assertEqual(again['hash'], auth['hash'])
            self.assertEqual(p['config'].read_bytes(), before); self.assertEqual(poste.credentials(p)[1], password)
            self.assertIsNone(poste.runtime(p))

    def test_interface_accepts_a_port(self):
        self.assertIn("def serve(config_path,auth_path,port=8769,host='127.0.0.1')", (ROOT / 'agent' / 'web.py').read_text(encoding='utf-8'))
        self.assertIn("'--port'", (ROOT / 'web.py').read_text(encoding='utf-8'))

    def test_desktop_entry_launcher_and_user_unit(self):
        entry = configparser.ConfigParser(interpolation=None); entry.read(ROOT / 'deploy' / 'poste' / 'axiorhub-pilote.desktop', encoding='utf-8')
        section = entry['Desktop Entry']
        self.assertEqual(section['Exec'], 'axiorhub-pilote'); self.assertEqual(section['Icon'], 'axiorhub-pilote'); self.assertIn('Office', section['Categories'])
        launcher = (ROOT / 'deploy' / 'poste' / 'axiorhub-pilote').read_text(encoding='utf-8')
        self.assertTrue(launcher.startswith('#!/bin/sh')); self.assertIn('/usr/lib/axiorhub-pilote/current/poste.py', launcher)
        unit = (ROOT / 'deploy' / 'poste' / 'axiorhub-pilote.service').read_text(encoding='utf-8')
        self.assertIn('poste.py --no-window', unit); self.assertIn('WantedBy=default.target', unit); self.assertNotIn('User=', unit)


@unittest.skipUnless(os.name == 'posix', 'services en sous-processus : Linux (fcntl, signaux)')
class Services(unittest.TestCase):
    def test_supervisor_starts_local_interface_with_basic_auth_and_stops_everything(self):
        try:
            import waitress  # noqa: F401
        except ImportError:
            self.skipTest('waitress absent')
        poste = load('poste_5620_services', ROOT / 'poste.py')
        with tempfile.TemporaryDirectory() as home:
            p = poste.paths(home); port = free_port(); poste.bootstrap(p, port)
            user, password = poste.credentials(p)
            supervisor = poste.Supervisor(p, port, interval_minutes=60).start()
            try:
                self.assertTrue(supervisor.wait_ready(90), (p['logs'] / 'interface.log').read_text(errors='replace')[-2000:])
                self.assertEqual(poste.probe(port), 401)
                request = urllib.request.Request(poste.origin_for(port) + '/agent-courriel/aujourdhui', headers={'Authorization': poste.basic_header(user, password)})
                with urllib.request.urlopen(request, timeout=30) as response:
                    body = response.read().decode('utf-8')
                self.assertEqual(response.status, 200); self.assertIn('AxiorHub', body)
                info = poste.runtime(p); self.assertEqual(info['port'], port); self.assertEqual(set(info['processes']), {'interface', 'worker', 'veille'})
                self.assertTrue(supervisor.alive()['interface'])
            finally:
                supervisor.stop()
            self.assertTrue(all(proc.poll() is not None for proc in supervisor.procs.values()))
            self.assertIsNone(poste.runtime(p))


def fake_archive(root, version):
    installer_text = (ROOT / 'installer.py').read_text(encoding='utf-8')
    core_units = re.findall(r"'([^']+)'", installer_text.split('CORE_UNITS = (')[1].split(')')[0])
    tree = root / ('axiorhub-mail-agent-' + version)
    files = {'agent/__init__.py': "__version__ = '%s'\n" % version, 'installer.py': installer_text,
             'upgrade.py': (ROOT / 'upgrade.py').read_text(encoding='utf-8'), 'poste.py': (ROOT / 'poste.py').read_text(encoding='utf-8'),
             'docker/bootstrap.py': (ROOT / 'docker' / 'bootstrap.py').read_text(encoding='utf-8')}
    for name in ('axiorhub-mail', *core_units, 'debian/postinst', 'debian/prerm', 'debian/postrm', 'debian/copyright',
                 'poste/axiorhub-pilote', 'poste/axiorhub-pilote.desktop', 'poste/axiorhub-pilote.service'):
        files['deploy/' + name] = (ROOT / 'deploy' / name).read_text(encoding='utf-8')
    for name, content in files.items():
        (tree / name).parent.mkdir(parents=True, exist_ok=True); (tree / name).write_text(content, encoding='utf-8')
    icon = (ROOT / 'agent' / 'static' / 'axiorhub-icon-512.png').read_bytes()
    (tree / 'agent' / 'static').mkdir(parents=True, exist_ok=True); (tree / 'agent' / 'static' / 'axiorhub-icon-512.png').write_bytes(icon)
    names = sorted(files) + ['agent/static/axiorhub-icon-512.png']
    (tree / 'MANIFEST.sha256').write_text(''.join(hashlib.sha256((tree / name).read_bytes()).hexdigest() + '  ' + name + '\n' for name in sorted(names)), encoding='utf-8')
    archive = root / (tree.name + '.tar.gz')
    with tarfile.open(archive, 'w:gz') as tar:
        tar.add(tree, arcname=tree.name)
    return archive


class PostePackage(unittest.TestCase):
    def test_poste_variant_is_an_amd64_desktop_package_with_the_whole_tree(self):
        b = load('build_deb_5620', ROOT / 'scripts' / 'build-deb.py')
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); archive = fake_archive(root, CURRENT)
            result = b.build(root / 'axiorhub-pilote-poste_test_amd64.deb', archive, mtime=1_700_000_000, variant='poste')
            self.assertEqual(result['package'], 'axiorhub-pilote-poste')
            info = b.check(result['path'])
            fields = info['fields']
            self.assertEqual(fields['Architecture'], 'amd64'); self.assertEqual(fields['Version'], CURRENT)
            for dependency in ('python3-gi', 'gir1.2-gtk-3.0', 'gir1.2-webkit2-4.1', 'python3-waitress', 'python3-cryptography', 'espeak-ng', 'xdg-utils'):
                self.assertIn(dependency, fields['Depends'])
            self.assertNotIn('./postinst', info['control']); self.assertNotIn('./prerm', info['control'])
            data = info['data']
            self.assertEqual(data['./usr/bin/axiorhub-pilote']['mode'], 0o755)
            for path in ('./usr/share/applications/axiorhub-pilote.desktop', './usr/share/icons/hicolor/512x512/apps/axiorhub-pilote.png',
                         './usr/lib/systemd/user/axiorhub-pilote.service', './usr/lib/axiorhub-pilote/current/poste.py',
                         './usr/lib/axiorhub-pilote/current/MANIFEST.sha256', './usr/lib/axiorhub-pilote/current/docker/bootstrap.py',
                         './usr/share/doc/axiorhub-pilote-poste/copyright'):
                self.assertIn(path, data, path); self.assertEqual((data[path]['uid'], data[path]['gid']), (0, 0))
            self.assertNotIn('./usr/share/axiorhub-pilote/axiorhub-mail-agent-%s.tar.gz' % CURRENT, data)   # arbre déployé, pas de charge utile
            self.assertEqual(set(info['md5sums']), {name[2:] for name, entry in data.items() if not entry['dir']})
            # Le paquet serveur reste inchangé.
            server = b.build(root / 'axiorhub-pilote_test_all.deb', archive, mtime=1_700_000_000)
            self.assertEqual(b.read_deb(server['path'])['fields']['Package'], 'axiorhub-pilote')


if __name__ == '__main__':
    unittest.main()
