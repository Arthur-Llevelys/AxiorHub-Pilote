"""5.6.18 : paquet Debian — structure du paquet et installation à blanc (neuve puis mise à niveau) sans dpkg ni root."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tarfile
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
CURRENT = re.search(r"__version__\s*=\s*'([^']+)'", (ROOT / 'agent' / '__init__.py').read_text(encoding='utf-8')).group(1)


def builder():
    spec = importlib.util.spec_from_file_location('build_deb_5618', ROOT / 'scripts' / 'build-deb.py')
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


def fake_archive(root, version):
    """Arbre minimal mais vérifiable : installer.py et upgrade.py réels (version substituée), lanceur, unités, scripts Debian, manifeste."""
    installer_text = (ROOT / 'installer.py').read_text(encoding='utf-8')   # pas d'import : installer.py exige pwd/grp (Linux)
    core_units = re.findall(r"'([^']+)'", installer_text.split('CORE_UNITS = (')[1].split(')')[0])
    tree = root / ('axiorhub-mail-agent-' + version)
    (tree / 'agent').mkdir(parents=True); (tree / 'deploy' / 'debian').mkdir(parents=True)
    files = {'agent/__init__.py': "__version__ = '%s'\n" % version}
    for name in ('installer.py', 'upgrade.py'):
        text = (ROOT / name).read_text(encoding='utf-8')
        if version != CURRENT:
            text = text.replace(CURRENT, version)
            if name == 'upgrade.py':   # la version suivante accepte la version courante comme précédente
                last = _last_supported(text)
                text = text.replace("'%s')" % last, "'%s','%s')" % (last, CURRENT))
        files[name] = text
    for name in ('axiorhub-mail', *core_units, 'debian/postinst', 'debian/prerm', 'debian/postrm', 'debian/copyright'):
        files['deploy/' + name] = (ROOT / 'deploy' / name).read_text(encoding='utf-8')
    for name, content in files.items():
        (tree / name).parent.mkdir(parents=True, exist_ok=True); (tree / name).write_text(content, encoding='utf-8')
    (tree / 'MANIFEST.sha256').write_text(''.join(hashlib.sha256((tree / name).read_bytes()).hexdigest() + '  ' + name + '\n' for name in sorted(files)), encoding='utf-8')
    archive = root / (tree.name + '.tar.gz')
    with tarfile.open(archive, 'w:gz') as tar:
        tar.add(tree, arcname=tree.name)
    return archive


def _last_supported(text):
    return re.search(r"SUPPORTED_PREVIOUS = \((?:'[^']+',)*'([^']+)'\)", text).group(1)


class Structure(unittest.TestCase):
    def test_package_is_a_valid_ar_with_control_scripts_and_payload(self):
        b = builder()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); archive = fake_archive(root, CURRENT)
            result = b.build(root / 'out' / 'axiorhub-pilote_test_all.deb', archive, mtime=1_700_000_000)
            self.assertEqual(result['version'], CURRENT); self.assertEqual(result['package'], 'axiorhub-pilote')
            info = b.check(result['path'], archive.read_bytes())
            fields = info['fields']
            self.assertEqual(fields['Package'], 'axiorhub-pilote'); self.assertEqual(fields['Version'], CURRENT)
            self.assertEqual(fields['Architecture'], 'all'); self.assertIn('users.noreply.github.com', fields['Maintainer'])
            for dependency in ('python3-cryptography', 'espeak-ng', 'libreoffice-writer', 'tesseract-ocr-fra', 'minisign', 'python3-venv'):
                self.assertIn(dependency, fields['Depends'])
            self.assertIn('apache2', fields['Recommends']); self.assertTrue(fields['Description'].startswith('Agent IA local'))
            for name in ('postinst', 'prerm', 'postrm'):
                entry = info['control']['./' + name]
                self.assertEqual(entry['mode'], 0o755); self.assertTrue(entry['data'].startswith(b'#!/usr/bin/python3'))
                self.assertNotIn(b'@VERSION@', entry['data'])
            self.assertIn(("VERSION = '%s'" % CURRENT).encode(), info['control']['./postinst']['data'])
            self.assertEqual(set(info['md5sums']), {'usr/share/axiorhub-pilote/axiorhub-mail-agent-%s.tar.gz' % CURRENT,
                                                    'usr/share/doc/axiorhub-pilote/copyright', 'usr/share/doc/axiorhub-pilote/changelog.Debian'})
            for name, entry in info['data'].items():
                self.assertEqual((entry['uid'], entry['gid']), (0, 0), name); self.assertFalse(entry['mode'] & 0o022, name)
                self.assertTrue(name == '.' or name.startswith('./usr'), name)
            # Reproductible : même archive, même horodatage, même paquet.
            again = b.build(root / 'out' / 'again.deb', archive, mtime=1_700_000_000)
            self.assertEqual(again['sha256'], result['sha256'])

    def test_tampered_payload_or_foreign_archive_is_refused(self):
        b = builder()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); archive = fake_archive(root, CURRENT)
            with tarfile.open(archive) as tar: members = tar.getmembers()
            broken = root / 'broken.tar.gz'
            with tarfile.open(archive) as src, tarfile.open(broken, 'w:gz') as dst:
                for member in members:
                    data = src.extractfile(member).read() if member.isfile() else None
                    if member.name.endswith('/upgrade.py'):
                        data = data + b'\n# altere\n'; member.size = len(data)
                    dst.addfile(member, __import__('io').BytesIO(data) if data is not None else None)
            with self.assertRaisesRegex(RuntimeError, 'manifeste'):
                b.build(root / 'out' / 'broken.deb', broken)


@unittest.skipUnless(os.name == 'posix', 'installation à blanc : scripts de maintenance Linux (pwd, grp, liens symboliques)')
class BlankInstall(unittest.TestCase):
    def _unpack(self, b, deb, root):
        """Simule dpkg : dépose les fichiers de data.tar sous la racine simulée et renvoie les scripts de maintenance."""
        info = b.read_deb(deb); scripts = {}
        for name, entry in info['data'].items():
            target = root / name[2:]
            if entry['dir']: target.mkdir(parents=True, exist_ok=True)
            else: target.parent.mkdir(parents=True, exist_ok=True); target.write_bytes(entry['data'])
        for name in ('postinst', 'prerm', 'postrm'):
            path = root / ('DEBIAN-' + name); path.write_bytes(info['control']['./' + name]['data']); path.chmod(0o755); scripts[name] = path
        return scripts

    def _run(self, script, root, *args):
        env = {**os.environ, 'AXIORHUB_DEB_ROOT': str(root), 'AXIORHUB_DEB_DRY_RUN': '1', 'PYTHONDONTWRITEBYTECODE': '1'}
        done = subprocess.run([sys.executable, str(script), *args], env=env, capture_output=True, text=True)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        return done.stdout

    def test_fresh_install_then_cumulative_upgrade_keep_previous_release_and_data(self):
        b = builder()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'root'; work = Path(tmp) / 'work'; root.mkdir(); work.mkdir()
            try: (work / 'probe').symlink_to(work)
            except OSError: self.skipTest('liens symboliques indisponibles')
            first = fake_archive(work, CURRENT); deb1 = b.build(work / 'first.deb', first)['path']
            scripts = self._unpack(b, deb1, root)
            out = self._run(scripts['postinst'], root, 'configure')
            base = root / 'opt' / 'axiorhub-mail-agent'
            self.assertIn('installation neuve', out)
            self.assertTrue((base / 'releases' / CURRENT / 'MANIFEST.sha256').is_file())
            self.assertEqual((base / 'current').resolve(), (base / 'releases' / CURRENT).resolve())
            self.assertTrue((root / 'usr/local/bin/axiorhub-mail').is_file()); self.assertTrue((root / 'etc/systemd/system/axiorhub-mail-ui.service').is_file())
            self.assertTrue((root / 'etc/axiorhub-mail-agent/secrets').is_dir()); self.assertTrue((root / 'var/lib/axiorhub-mail-agent').is_dir())
            self.assertFalse(list(base.glob('.deb-*')))                                  # dossier de travail nettoyé
            journal = json.loads((base / ('deb-' + CURRENT + '.json')).read_text(encoding='utf-8'))
            self.assertTrue(journal['dry_run']); self.assertIn('systemctl daemon-reload', journal['journal'])
            self.assertFalse(any('systemctl stop' in line for line in journal['journal']))
            self.assertFalse(list((root / 'etc/axiorhub-mail-agent').glob('*.secret')))  # aucun secret créé
            # Mise à niveau cumulative : la configuration existe, la version précédente reste, le reçu permet le retour arrière.
            (root / 'etc/axiorhub-mail-agent/config.json').write_text(json.dumps({'state_dir': str(root / 'var/lib/axiorhub-mail-agent')}), encoding='utf-8')
            (root / 'var/lib/axiorhub-mail-agent/desk.sqlite3').write_bytes(b'')          # fichier non SQLite : ignoré par la sauvegarde
            nxt = _next(CURRENT); second = fake_archive(work, nxt); deb2 = b.build(work / 'second.deb', second)['path']
            scripts2 = self._unpack(b, deb2, root)
            out2 = self._run(scripts2['postinst'], root, 'configure', CURRENT)
            self.assertIn('mise à niveau cumulative vers ' + nxt, out2)
            self.assertEqual((base / 'current').resolve(), (base / 'releases' / nxt).resolve())
            self.assertTrue((base / 'releases' / CURRENT / 'MANIFEST.sha256').is_file())       # retour arrière possible
            self.assertTrue((base / ('upgrade-' + nxt + '.json')).is_file())
            self.assertTrue(list((root / 'etc/axiorhub-mail-agent/backups').glob('config-before-' + nxt + '-*.json')))
            journal2 = json.loads((base / ('deb-' + nxt + '.json')).read_text(encoding='utf-8'))
            self.assertIn('systemctl daemon-reload', journal2['journal']); self.assertIn('install-interface.py', ''.join(journal2['journal']))
            # Retrait : services désactivés, données conservées.
            self.assertIn('disable --now axiorhub-mail-ui.service', self._run(scripts2['prerm'], root, 'remove'))
            self.assertIn('conservés', self._run(scripts2['postrm'], root, 'purge'))
            self.assertTrue((root / 'var/lib/axiorhub-mail-agent').is_dir())


def _next(version):
    major, minor, patch = version.split('.')
    return '%s.%s.%d' % (major, minor, int(patch) + 1)


if __name__ == '__main__':
    unittest.main()
