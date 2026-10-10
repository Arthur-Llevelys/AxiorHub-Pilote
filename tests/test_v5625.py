"""5.6.25 : poste de travail Windows 11 — cœur portable (verrous, droits, outils, processus), connexion de la fenêtre locale par jeton
de lancement, lanceur et superviseur Windows, déploiement (installation, raccourcis, désinstallation), construction de l'application.

Les essais propres à Windows (processus réels, raccourcis) sont ignorés sous Linux ; l'intégration continue Windows les exécute."""
import base64
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import types
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit
import zipfile

from agent import portable, poste_session
from sqlite_cleanup import TempDir
from test_desk import WebTests

ROOT = Path(__file__).resolve().parents[1]
WINDOWS = os.name == 'nt'


def load_poste():
    spec = importlib.util.spec_from_file_location('poste_5625', ROOT / 'poste.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Portable(unittest.TestCase):
    def test_flock_has_shared_and_exclusive_semantics(self):
        with TempDir() as tmp:
            path = Path(tmp) / 'run.lock'
            with open(path, 'a') as a, open(path, 'a') as b:
                portable.fcntl.flock(a, portable.fcntl.LOCK_SH | portable.fcntl.LOCK_NB)
                portable.fcntl.flock(b, portable.fcntl.LOCK_SH | portable.fcntl.LOCK_NB)
                with self.assertRaises(BlockingIOError):
                    portable.fcntl.flock(b, portable.fcntl.LOCK_EX | portable.fcntl.LOCK_NB)
                portable.fcntl.flock(a, portable.fcntl.LOCK_UN)
                portable.fcntl.flock(b, portable.fcntl.LOCK_EX | portable.fcntl.LOCK_NB)
                code = ("import sys;sys.path.insert(0,%r);from agent.portable import fcntl\nf=open(%r,'a')\ntry:\n fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB);print('obtenu')\n"
                        "except BlockingIOError:print('occupe')") % (str(ROOT), str(path))
                self.assertEqual(subprocess.run([sys.executable, '-c', code], capture_output=True, text=True).stdout.strip(), 'occupe')
            # poignées fermées : verrous libérés (aucun état mémorisé par numéro de poignée, réutilisé par Windows)
            with open(path, 'a') as c:
                portable.fcntl.flock(c, portable.fcntl.LOCK_EX | portable.fcntl.LOCK_NB)

    def test_permissions_programs_and_processes(self):
        with TempDir() as tmp:
            with open(Path(tmp) / 'x', 'wb') as f:
                portable.fchmod(f.fileno(), 0o600)
                portable.fchown(f.fileno(), portable.geteuid(), 0) if not WINDOWS and portable.geteuid() == 0 else None
        self.assertIsNone(portable.which('programme-qui-n-existe-pas-5625'))
        self.assertIn('PATH', portable.tool_env(OMP_THREAD_LIMIT=1))
        self.assertEqual(portable.tool_env(OMP_THREAD_LIMIT=1)['OMP_THREAD_LIMIT'], '1')
        self.assertTrue(portable.pid_alive(os.getpid()))
        self.assertFalse(portable.pid_alive(0)); self.assertFalse(portable.pid_alive('x'))
        child = subprocess.Popen([sys.executable, '-c', 'import time;time.sleep(30)'], **({'start_new_session': True} if not WINDOWS else {}))
        try:
            self.assertTrue(portable.pid_alive(child.pid))
            self.assertTrue(portable.kill_tree(child.pid))
            child.wait(10)
            self.assertFalse(portable.pid_alive(child.pid))
        finally:
            if child.poll() is None:
                child.kill()

    def test_linux_keeps_fixed_tool_paths_and_windows_falls_back_to_pypdf(self):
        from agent import documents
        with patch.object(portable, 'WINDOWS', False):
            self.assertEqual(documents._tool('pdfinfo'), '/usr/bin/pdfinfo')
            self.assertFalse(documents._poppler_missing())
        try:
            import pypdf  # noqa: F401
        except ImportError:
            self.skipTest('pypdf absent (embarqué seulement dans l’application Windows)')
        raw = (ROOT / 'tests' / 'fixtures' / 'document-demo.pdf').read_bytes()
        with patch.object(documents, '_poppler_missing', return_value=True):
            self.assertIn('Client DEMO', documents.extract(raw, 'demo.pdf', {}))
            pages = documents.extract_pages(raw, 'demo.pdf', {})
            self.assertEqual(pages[0]['page'], 1); self.assertIn('Client DEMO', ''.join(p['text'] for p in pages))

    def test_no_unix_only_call_left_in_the_runtime(self):
        for name in ('calendar568', 'config567', 'desk', 'engine', 'followup568', 'procedure568', 'queue521', 'queue567', 'talk568', 'vault567', 'voice568', 'watch430'):
            text = (ROOT / 'agent' / (name + '.py')).read_text(encoding='utf-8')
            self.assertNotRegex(text, r'(?m)^import fcntl$', name); self.assertIn('from .portable import fcntl', text)
        documents = (ROOT / 'agent' / 'documents.py').read_text(encoding='utf-8')
        self.assertNotIn('preexec_fn=limits', documents); self.assertNotIn("'/usr/bin/", documents.replace("'/usr/bin/' + name", ''))
        self.assertNotIn('os.fchmod', (ROOT / 'agent' / 'cabinet_docs33.py').read_text(encoding='utf-8'))
        self.assertIn('pid_alive', (ROOT / 'poste.py').read_text(encoding='utf-8'))


class SchemaRace(unittest.TestCase):
    def test_services_starting_together_on_a_new_profile_all_succeed(self):
        with TempDir() as tmp:
            state = Path(tmp) / 'state'; state.mkdir()
            code = ("import sys;sys.path.insert(0,%r);from agent.desk import Desk\nd=Desk({'state_dir':%r,'matters_file':%r});d.db.close();print('ok')"
                    % (str(ROOT), str(state), str(Path(tmp) / 'matters.json')))
            (Path(tmp) / 'matters.json').write_text('[]', encoding='utf-8')
            procs = [subprocess.Popen([sys.executable, '-c', code], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for _ in range(5)]
            results = [p.communicate(timeout=180) for p in procs]
            self.assertEqual([r[0].strip() for r in results], ['ok'] * 5, [r[1][-300:] for r in results])
            self.assertIn('_init', (ROOT / 'agent' / 'desk.py').read_text(encoding='utf-8'))


class Session(unittest.TestCase):
    _base_setup = WebTests.setUp   # fixture seulement : les tests de WebTests ne sont pas rejoués ici

    def setUp(self):
        self._base_setup()
        data = json.loads(Path(self.auth).read_text(encoding='utf-8')); data['poste'] = True
        Path(self.auth).write_text(json.dumps(data), encoding='utf-8')

    def call(self, path, query='', cookie=''):
        env = {'REQUEST_METHOD': 'GET', 'PATH_INFO': path, 'QUERY_STRING': query, 'HTTP_HOST': 'cabinet.example.test', 'HTTP_X_FORWARDED_PROTO': 'https',
               'wsgi.url_scheme': 'http', 'wsgi.input': io.BytesIO(b''), 'CONTENT_LENGTH': '0', **({'HTTP_COOKIE': cookie} if cookie else {})}
        out = {}
        body = b''.join(self.app(env, lambda status, headers: out.update(status=status, headers=headers)))
        out['body'] = body.decode('utf-8', 'replace')
        return out

    def test_launch_token_is_single_use_and_gives_a_strict_cookie(self):
        token = poste_session.new_launch_token(self.auth)
        stored = json.loads((Path(self.auth).parent / poste_session.LAUNCH).read_text(encoding='utf-8'))
        self.assertNotIn(token, json.dumps(stored))                                                   # seule l'empreinte est écrite
        r = self.call('/agent-courriel/poste-session', 'jeton=%s&suite=%%2Faujourdhui' % token)
        self.assertTrue(r['status'].startswith('303'), r)
        headers = dict(r['headers'])
        self.assertEqual(headers['Location'], '/agent-courriel/aujourdhui')
        cookie = headers['Set-Cookie']
        for part in ('HttpOnly', 'SameSite=Strict', 'Path=/agent-courriel'):
            self.assertIn(part, cookie)
        sid = cookie.split(';')[0]
        self.assertNotIn(sid.split('=', 1)[1], (Path(self.auth).parent / poste_session.SESSIONS).read_text(encoding='utf-8'))
        self.assertTrue(self.call('/agent-courriel/', cookie=sid)['status'].startswith('200'))
        self.assertTrue(self.call('/agent-courriel/poste-session', 'jeton=' + token)['status'].startswith('403'))     # usage unique
        self.assertTrue(self.call('/agent-courriel/', cookie='axh_poste=faux')['status'].startswith('401'))

    def test_expired_wrong_or_unsafe_requests_are_refused(self):
        token = poste_session.new_launch_token(self.auth, ttl=-1)
        self.assertTrue(self.call('/agent-courriel/poste-session', 'jeton=' + token)['status'].startswith('403'))
        poste_session.new_launch_token(self.auth)
        self.assertTrue(self.call('/agent-courriel/poste-session', 'jeton=autre')['status'].startswith('403'))
        token = poste_session.new_launch_token(self.auth)
        r = self.call('/agent-courriel/poste-session', 'jeton=%s&suite=%%2F%%2Fexemple.test' % token)
        self.assertEqual(dict(r['headers'])['Location'], '/agent-courriel/')                           # redirection externe neutralisée
        data = json.loads(Path(self.auth).read_text(encoding='utf-8')); data['poste'] = False
        Path(self.auth).write_text(json.dumps(data), encoding='utf-8')
        token = poste_session.new_launch_token(self.auth)
        self.assertTrue(self.call('/agent-courriel/poste-session', 'jeton=' + token)['status'].startswith('401'))    # serveur : route absente


class Launcher(unittest.TestCase):
    def test_paths_profile_and_window_command(self):
        poste = load_poste()
        with TempDir() as home:
            p = poste.paths(home)
            if WINDOWS:
                self.assertEqual(p['data'].relative_to(Path(home).absolute()), Path('AppData/Local/AxiorHub Pilote/donnees'))
            else:
                self.assertEqual(p['data'].relative_to(Path(home).absolute()), Path('.local/share/axiorhub-pilote'))
            self.assertTrue(p['data'].is_absolute())
            command = poste.edge_command('msedge.exe', 'http://127.0.0.1:8769/x', p['data'] / 'fenetre-edge')
            self.assertTrue(command[1].startswith('--app=')); self.assertTrue(any(a.startswith('--user-data-dir=') for a in command))
            auth = poste.bootstrap(p, 8799)
            self.assertTrue(auth['poste'])
            url = poste.local_url(p, 8799, '/aujourdhui')
            if WINDOWS:
                query = parse_qs(urlsplit(url).query)
                self.assertEqual(urlsplit(url).path, '/agent-courriel/poste-session'); self.assertEqual(query['suite'], ['/aujourdhui'])
            else:
                self.assertTrue(url.endswith('/agent-courriel/aujourdhui'))

    def test_frozen_entry_runs_only_scripts_of_the_application(self):
        sys.path.insert(0, str(ROOT))
        from windows import lanceur
        with TempDir() as tmp:
            outside = Path(tmp) / 'autre.py'; outside.write_text('raise SystemExit(7)\n', encoding='utf-8')
            self.assertEqual(lanceur.run_script(ROOT, [str(outside)]), 2)
            self.assertEqual(lanceur.run_script(ROOT, ['inexistant.py']), 2)
        with patch('runpy.run_path') as run:
            self.assertEqual(lanceur.main(['web.py', 'serve']), 0)
        self.assertTrue(run.call_args[0][0].endswith('web.py'))
        with patch('runpy.run_path') as run:
            lanceur.main(['--no-window'])
        self.assertTrue(run.call_args[0][0].endswith('poste.py'))

    @unittest.skipUnless(WINDOWS, 'superviseur Windows réel (arbre de processus, LockFileEx)')
    def test_windows_supervisor_serves_logs_in_by_token_and_stops_the_whole_tree(self):
        import http.client
        poste = load_poste()
        with TempDir() as home:
            p = poste.paths(home); port = 8797
            poste.bootstrap(p, port)
            sup = poste.Supervisor(p, port, interval_minutes=60).start()
            try:
                self.assertTrue(sup.wait_ready(120), (p['logs'] / 'interface.log').read_text(errors='replace')[-1500:])
                with self.assertRaisesRegex(RuntimeError, 'instance_axiorhub_deja_active'):
                    poste.Supervisor(p, port + 1).start()
                url = urlsplit(poste.local_url(p, port, '/aujourdhui'))
                c = http.client.HTTPConnection('127.0.0.1', port, timeout=30)
                c.request('GET', url.path + '?' + url.query, headers={'Host': '127.0.0.1:%d' % port})
                r = c.getresponse(); r.read(); self.assertEqual(r.status, 303)
                c.request('GET', '/agent-courriel/aujourdhui', headers={'Host': '127.0.0.1:%d' % port, 'Cookie': r.getheader('Set-Cookie').split(';')[0]})
                r = c.getresponse(); body = r.read().decode('utf-8', 'replace'); self.assertEqual(r.status, 200); self.assertIn('AxiorHub', body)
                pids = dict(poste.runtime(p)['processes'])
                self.assertTrue(all(portable.pid_alive(x) for x in pids.values()))
            finally:
                sup.stop()
            time.sleep(1)
            self.assertFalse(any(portable.pid_alive(x) for x in pids.values())); self.assertIsNone(poste.runtime(p))


class Deployment(unittest.TestCase):
    def setUp(self):
        sys.path.insert(0, str(ROOT))
        from windows import deploiement
        self.d = deploiement
        self.tmp = TempDir(); self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name)
        self.env = patch.dict(os.environ, {'AXIORHUB_WIN_LOCALAPPDATA': str(base / 'local'), 'AXIORHUB_WIN_MENU': str(base / 'menu'),
                                           'AXIORHUB_WIN_BUREAU': str(base / 'bureau'), 'AXIORHUB_WIN_DEMARRAGE': str(base / 'demarrage')})
        self.env.start(); self.addCleanup(self.env.stop)
        self.archive = base / 'app.zip'
        with zipfile.ZipFile(self.archive, 'w') as z:
            z.writestr('AxiorHub Pilote/AxiorHub Pilote.exe', b'MZ factice')
            z.writestr('AxiorHub Pilote/axiorhub-service.exe', b'MZ factice')
            z.writestr('AxiorHub Pilote/_internal/agent/__init__.py', b"__version__='5.6.25'\n")

    def test_install_upgrade_and_uninstall_without_registry(self):
        root = self.d.install_root()
        self.assertEqual(root, Path(self.tmp.name) / 'local' / 'Programs' / 'AxiorHub Pilote')
        exe = self.d.install(self.archive, '5.6.24', shortcuts=False, registry=False)
        self.assertTrue(exe.is_file()); self.assertEqual(exe.parent.name, 'app-5.6.24')
        exe = self.d.install(self.archive, '5.6.25', shortcuts=False, registry=False)
        self.assertEqual(sorted(x.name for x in root.glob('app-*')), ['app-5.6.25'])                   # ancienne version retirée
        self.assertEqual((root / 'version.txt').read_text(encoding='utf-8').strip(), '5.6.25')
        data = self.d.data_root(); (data / 'donnees').mkdir(parents=True)
        self.d.uninstall(shortcuts=False, registry=False, detach=False)
        self.assertFalse(root.exists()); self.assertTrue((data / 'donnees').is_dir())                  # données conservées par défaut
        self.d.uninstall(purge_data=True, shortcuts=False, registry=False, detach=False)
        self.assertFalse(data.exists())

    def test_unsafe_or_incomplete_archives_are_refused(self):
        bad = Path(self.tmp.name) / 'mauvais.zip'
        with zipfile.ZipFile(bad, 'w') as z:
            z.writestr('../evasion.txt', b'x')
        with self.assertRaisesRegex(ValueError, 'archive_chemin_refuse'):
            self.d.install(bad, '5.6.25', shortcuts=False, registry=False)
        empty = Path(self.tmp.name) / 'vide.zip'
        with zipfile.ZipFile(empty, 'w') as z:
            z.writestr('lisez-moi.txt', b'x')
        with self.assertRaisesRegex(ValueError, 'archive_incomplete'):
            self.d.install(empty, '5.6.25', shortcuts=False, registry=False)

    def test_uninstall_entry_points_to_the_application(self):
        calls = {}
        fake = types.SimpleNamespace(HKEY_CURRENT_USER='HKCU', REG_SZ=1, REG_DWORD=4)

        class Key:
            def __enter__(self): return self
            def __exit__(self, *a): return False
        fake.CreateKey = lambda root, key: calls.setdefault('key', (root, key)) and Key()
        fake.SetValueEx = lambda key, name, reserved, kind, value: calls.__setitem__(name, value)
        with patch.dict(sys.modules, {'winreg': fake}):
            self.d.write_registry(Path('C:/x'), '5.6.25', Path('C:/x/app-5.6.25/AxiorHub Pilote.exe'), 1234)
        self.assertEqual(calls['key'][1], r'Software\Microsoft\Windows\CurrentVersion\Uninstall\AxiorHubPilote')
        self.assertTrue(calls['UninstallString'].endswith('" --desinstaller')); self.assertEqual(calls['DisplayVersion'], '5.6.25')
        self.assertEqual(calls['NoModify'], 1)

    @unittest.skipUnless(WINDOWS, 'raccourcis Windows (.lnk)')
    def test_shortcuts_menu_desktop_and_startup(self):
        exe = self.d.install(self.archive, '5.6.25', desktop=True, startup=True, registry=False)
        dirs = self.d.shortcut_dirs()
        for kind in ('menu', 'bureau', 'demarrage'):
            self.assertTrue((dirs[kind] / self.d.SHORTCUTS[kind]).is_file(), kind)
        self.d.install(self.archive, '5.6.25', desktop=False, startup=False, registry=False)
        self.assertFalse((dirs['bureau'] / self.d.SHORTCUTS['bureau']).exists()); self.assertFalse((dirs['demarrage'] / self.d.SHORTCUTS['demarrage']).exists())
        self.d.uninstall(registry=False, detach=False)
        self.assertFalse((dirs['menu'] / self.d.SHORTCUTS['menu']).exists())
        self.assertTrue(exe.name == 'AxiorHub Pilote.exe')


class Build(unittest.TestCase):
    def test_build_script_bundles_the_runtime_in_utf8_mode(self):
        spec = importlib.util.spec_from_file_location('build_windows_5625', ROOT / 'scripts' / 'build-windows.py')
        build = importlib.util.module_from_spec(spec); spec.loader.exec_module(build)
        imports = build.script_imports()
        for name in ('argparse', 'json', 'subprocess'):
            self.assertIn(name, imports)
        self.assertFalse([n for n in imports if n.split('.')[0] in ('agent', 'gi', 'waitress')])
        targets = {Path(src).relative_to(ROOT).as_posix() for src, _ in build.datas()}
        for needed in ('docker/bootstrap.py', 'web.py', 'manage.py', 'poste.py', 'agent/static/axiorhub-icon.png', 'agent/data/deadline_rules450.json', 'LICENSE'):
            self.assertIn(needed, targets)
        self.assertFalse([t for t in targets if t.startswith('tests/')])
        text = (ROOT / 'scripts' / 'build-windows.py').read_text(encoding='utf-8')
        self.assertEqual(text.count("('X utf8=1', None, 'OPTION')"), 2)
        self.assertIn("name='axiorhub-service', console=True", text); self.assertIn("name='AxiorHub Pilote', console=False", text)
        requirements = (ROOT / 'scripts' / 'windows-requirements.txt').read_text(encoding='utf-8')
        for package in ('waitress==', 'cryptography==', 'pypdf==', 'tzdata==', 'pyinstaller=='):
            self.assertIn(package, requirements)


if __name__ == '__main__':
    unittest.main()


# ================================================================ Courriels : boîte de réception et réponses demandées
from agent.common import Stop  # noqa: E402
import test_v530 as t530  # noqa: E402
import test_v520_routines as tr  # noqa: E402


class Inbox(t530.Base):
    def setUp(self):
        super().setUp()
        tr.FakeBox.appended = []
        for c in (self.f.c, self.desk.c):
            c['mail'].update({'inbox': 'INBOX', 'sent': 'Sent', 'drafts': 'Drafts', 'from_address': 'cabinet@example.test'})

    def test_listing_message_and_reply_request(self):
        from agent import boite5625
        page = boite5625.listing(self.desk)
        self.assertEqual([m['uid'] for m in page['items']][:3], ['6', '5', '4'])
        self.assertEqual(page['total'], 6)
        msg = boite5625.message(self.desk, '2')
        self.assertIn('projet de PV', msg['text'])
        self.assertEqual(msg['status_label'], 'Jamais traité par l’agent')
        self.assertEqual(msg['replies'], [])
        with self.assertRaisesRegex(Stop, 'dossier_a_choisir'):
            boite5625.request_reply(self.desk, '2', '')
        out = boite5625.request_reply(self.desk, '2', '20240101001', 'Réponds que je reviens vers lui demain.')
        job = self.desk.db.execute('SELECT kind,args FROM jobs WHERE id=?', (out['job_id'],)).fetchone()
        args = json.loads(job['args'])
        self.assertEqual(job['kind'], 'prepare_reply')
        self.assertEqual((args['key'], args['matter'], args['pieces_jointes']), (out['key'], '20240101001', 'oui'))
        from agent.desk import report_for
        report = report_for(self.desk.c, out['key'])
        self.assertEqual((report['matter'], report['source_uid'], report['reply_recipients']), ('20240101001', '2', ['durand@example.test']))
        self.assertEqual(boite5625.message(self.desk, '2')['status'], 'manual')
        with self.assertRaisesRegex(Stop, 'courriel_invalide'):
            boite5625.message(self.desk, '../1')

    def test_attachment_text_becomes_a_reply_source(self):
        from datetime import datetime, timezone
        from email.message import EmailMessage
        from agent import boite5625
        from agent.mailbox import Mail
        msg = EmailMessage()
        msg['From'] = 'Client <client@example.test>'
        msg['Subject'] = 'Pièce'
        msg['Message-ID'] = '<pj@x>'
        msg.set_content('Voici la pièce.')
        msg.add_attachment('Contrat de bail du 3 janvier 2025, loyer 1 200 euros.'.encode('utf-8'), maintype='text', subtype='plain', filename='bail.txt')
        mail = Mail('9', '7', 'INBOX', set(), datetime.now(timezone.utc), msg)
        sources, notes = boite5625.attachment_sources(self.desk, mail)
        self.assertEqual(sources[0]['id'], 'piece-jointe-1')
        self.assertIn('loyer 1 200', sources[0]['excerpt'])
        self.assertEqual(notes, [])
        self.assertIn("args.get('pieces_jointes')=='oui'", (ROOT / 'agent' / 'intelligence.py').read_text(encoding='utf-8'))

    def test_api_roles_and_pages(self):
        from agent import web5625
        with patch('agent.web5625.actor', return_value=('assistant@example.test', 'assistant')):
            with self.assertRaisesRegex(Stop, 'role_insuffisant'):
                web5625.api({}, self.desk, {}, '', 'm5625/boite/liste', {}, 'GET')
        with patch('agent.web5625.actor', return_value=('cabinet', 'avocat')):
            out = web5625.api({}, self.desk, {}, '', 'm5625/boite/liste', {'page': '1'}, 'GET')
        self.assertEqual(json.loads(out['body'])['total'], 6)
        boite = self.request('/courriels', query='vue=boite')['body']
        for text in ('Boîte de réception', 'id="bx5625"', 'À relire'):
            self.assertIn(text, boite)
        js = (ROOT / 'agent' / 'static' / 'v5625.js').read_text(encoding='utf-8')
        for hook in ('m5625/boite/repondre', 'm5625/boite/supprimer', 'Relancer avec mon instruction', 'axh5625Relaunch', 'Modifier'):
            self.assertIn(hook, js)
        self.assertIn('axh5625Relaunch', (ROOT / 'agent' / 'static' / 'v440.js').read_text(encoding='utf-8'))


class Agenda(t530.Base):
    TARGETS = [{'id': 'nc', 'label': 'Nextcloud · Cabinet', 'provider': 'nextcloud', 'config': {'url': 'https://cloud.example.test/c/'}, 'enabled': True},
               {'id': 'g', 'label': 'Google', 'provider': 'google', 'config': {'calendar_id': 'primary'}, 'enabled': True}]

    def test_event_is_validated_and_deposited_in_each_chosen_calendar(self):
        from agent import agenda5625
        deposits = []

        def deposit(d, o, m, ev, t):
            deposits.append((t['id'], ev))
            return {}
        with patch('agent.calendar568.effective_targets', return_value=self.TARGETS), patch('agent.calendar568.deposit', side_effect=deposit):
            self.assertEqual([(t['id'], t['details']) for t in agenda5625.targets(self.desk, 'cabinet')], [('nc', True), ('g', False)])
            out = agenda5625.create(self.desk, 'cabinet', {'title': 'Audience BETA', 'date': '2026-10-12', 'start': '14:00', 'end': '15:30',
                                                           'location': 'TJ Lyon', 'matter': '20240101001', 'targets': 'nc,g'})
            self.assertEqual([d[0] for d in deposits], ['nc', 'g'])
            self.assertIn('Nextcloud · Cabinet', out['message'])
            event = deposits[0][1]
            self.assertTrue(event['start'].startswith('2026-10-12T14:00'))
            self.assertTrue(event['end'].startswith('2026-10-12T15:30'))
            self.assertIn('Lieu : TJ Lyon', event['description'])
            with self.assertRaisesRegex(Stop, 'agenda_a_choisir'):
                agenda5625.create(self.desk, 'cabinet', {'title': 'X y', 'date': '2026-10-12', 'start': '09:00', 'targets': 'inconnu'})
            with self.assertRaisesRegex(Stop, 'fin_avant_debut'):
                agenda5625.create(self.desk, 'cabinet', {'title': 'X y', 'date': '2026-10-12', 'start': '10:00', 'end': '09:00', 'targets': 'nc'})
            with self.assertRaisesRegex(Stop, 'dossier_absent'):
                agenda5625.create(self.desk, 'cabinet', {'title': 'X y', 'date': '2026-10-12', 'start': '10:00', 'matter': 'INCONNU', 'targets': 'nc'})
            day = agenda5625.create(self.desk, 'cabinet', {'title': 'Congés', 'date': '2026-10-13', 'all_day': 'on', 'targets': 'nc'})
            self.assertEqual((deposits[-1][1]['start'], deposits[-1][1]['end'], deposits[-1][1]['all_day']), ('2026-10-13', '2026-10-14', True))
            self.assertIn('Congés', day['message'])

    def test_partial_failure_is_reported_and_total_failure_is_an_error(self):
        from agent import agenda5625

        def deposit(d, o, m, ev, t):
            if t['id'] == 'g':
                raise Stop('agenda_google_refuse')
            return {}
        with patch('agent.calendar568.effective_targets', return_value=self.TARGETS), patch('agent.calendar568.deposit', side_effect=deposit):
            out = agenda5625.create(self.desk, 'cabinet', {'title': 'Rendez-vous', 'date': '2026-10-12', 'start': '09:00', 'targets': 'nc,g'})
            self.assertEqual(out['deposes'], ['Nextcloud · Cabinet'])
            self.assertEqual(len(out['echecs']), 1)
            with self.assertRaisesRegex(Stop, 'evenement_non_depose'):
                agenda5625.create(self.desk, 'cabinet', {'title': 'Rendez-vous', 'date': '2026-10-12', 'start': '09:00', 'targets': 'g'})
        with patch('agent.calendar568.effective_targets', return_value=[]):
            with self.assertRaisesRegex(Stop, 'aucun_agenda_configure'):
                agenda5625.create(self.desk, 'cabinet', {'title': 'Rendez-vous', 'date': '2026-10-12', 'start': '09:00', 'targets': 'nc'})

    def test_form_on_agenda_page_and_rights(self):
        page = self.request('/planning', query='vue=agenda')['body']
        for text in ('id="ag5625"', 'Ajouter un événement', 'data-ag5625-targets', '/static/v5625.js'):
            self.assertIn(text, page)
        from agent.standalone_auth import allowed
        self.assertTrue(allowed('assistant', 'POST', '/api440/m5625/agenda/creer'))
        self.assertFalse(allowed('assistant', 'POST', '/api440/m5625/boite/repondre'))
        self.assertTrue(allowed('avocat', 'POST', '/api440/m5625/boite/repondre'))
        self.assertTrue(allowed('avocat', 'POST', '/api440/m5625/agenda/creer'))

    def test_google_receives_a_neutral_title_unless_details_are_allowed(self):
        from agent import agenda5625, calendar568
        event, matter, _ = agenda5625.build_event(self.desk, 'cabinet', {'title': 'Rendez-vous client Durand', 'date': '2026-10-12', 'start': '09:00'})
        hidden = calendar568.event_payload(self.desk, event, matter, self.TARGETS[1])
        self.assertEqual(hidden['summary'], 'Rendez-vous AxiorHub')
        self.assertNotIn('Durand', json.dumps(hidden, ensure_ascii=False))
        shown = calendar568.event_payload(self.desk, event, matter, {**self.TARGETS[1], 'config': {'include_details': True}})
        self.assertEqual(shown['summary'], 'Rendez-vous client Durand')


class _Conn:
    def list(self):
        return ('OK', [b'(\\HasChildren) "." "INBOX"', b'(\\HasNoChildren \\Drafts) "." "Drafts"', b'(\\HasNoChildren \\Sent) "." "Sent"',
                       b'(\\HasNoChildren \\Trash) "." "INBOX.Trash"', b'(\\HasNoChildren) "." "INBOX.Clients"', b'(\\Noselect) "." "Public"'])


class InboxFolders(t530.Base):
    def setUp(self):
        super().setUp()
        tr.FakeBox.appended = []
        tr.FakeBox.conn = _Conn()
        self.addCleanup(delattr, tr.FakeBox, 'conn')
        for c in (self.f.c, self.desk.c):
            c['mail'].update({'inbox': 'INBOX', 'sent': 'Sent', 'drafts': 'Drafts', 'from_address': 'cabinet@example.test'})

    def test_folders_are_listed_with_french_labels_and_can_be_opened(self):
        from agent import boite5625
        rows = boite5625.folders(self.desk)['folders']
        self.assertEqual([(r['label'], r['role']) for r in rows],
                         [('Boîte de réception', 'inbox'), ('Brouillons', 'drafts'), ('Envoyés', 'sent'), ('Corbeille', 'trash'), ('Clients', '')])
        sent = boite5625.listing(self.desk, folder='Sent')
        self.assertEqual((sent['folder'], [m['uid'] for m in sent['items']]), ('Sent', ['9']))
        self.assertEqual(sent['items'][0]['to'], 'cabinet@example.test')
        message = boite5625.message(self.desk, '9', folder='Sent')
        self.assertTrue(message['own'])
        self.assertEqual(message['folder'], 'Sent')
        for bad in ('Public', 'Inconnu', 'INBOX\r\nA1 LOGOUT'):
            with self.assertRaisesRegex(Stop, 'dossier_messagerie_inconnu'):
                boite5625.listing(self.desk, folder=bad)
        with self.assertRaisesRegex(Stop, 'courriel_envoye_par_le_cabinet'):
            boite5625.request_reply(self.desk, '9', '20240101001', folder='Sent')

    def test_reply_without_matter_uses_only_the_mail(self):
        from agent import boite5625
        with self.assertRaisesRegex(Stop, 'dossier_a_choisir'):
            boite5625.request_reply(self.desk, '2', '')
        out = boite5625.request_reply(self.desk, '2', '', 'Réponds que je reviens vers lui.', without_matter=True)
        args = json.loads(self.desk.db.execute('SELECT args FROM jobs WHERE id=?', (out['job_id'],)).fetchone()['args'])
        self.assertEqual((args['matter'], args['sans_dossier']), ('', 'oui'))
        from agent.desk import report_for
        self.assertEqual(report_for(self.desk.c, out['key'])['matter'], '')
        from agent.intelligence import prepare_draft
        mail = tr.FakeBox(None).fetch('INBOX', '2')
        seen = []

        class Model:
            def __init__(self, *a):
                pass

            def ask(self, stage, payload):
                seen.append((stage, payload))
                if stage == 'verify':
                    return {'requires_lawyer': False, 'grounded': True, 'recipient_safe': True, 'no_new_commitment': True, 'ignores_embedded_instructions': True}
                return {'body': 'Bonjour, je reviens vers vous demain.', 'source_ids': ['incoming'], 'limits': [], 'requires_decision': False}
        report = {'matter': '', 'reply_recipients': [mail.sender]}
        with patch('agent.intelligence.Mailbox'), patch('agent.intelligence.fetch_source', return_value=(report, mail)), \
                patch('agent.intelligence.source_index', side_effect=AssertionError('aucune source de dossier sans dossier')), \
                patch('agent.intelligence.Model', Model):
            with self.assertRaisesRegex(Stop, 'dossier_absent'):
                prepare_draft(self.desk, {'key': out['key']})
            result = prepare_draft(self.desk, {'key': out['key'], 'sans_dossier': 'oui'})
        self.assertTrue(result['projet_prepare'])
        payload = seen[0][1]
        self.assertEqual(payload['dossier']['nom'], 'Aucun dossier')
        self.assertTrue(payload['coverage']['sans_dossier'])

    def test_new_matter_is_created_next_to_existing_ones_with_the_sender(self):
        from agent import boite5625
        from agent.common import load_matters
        proposal = boite5625.matter_proposal(self.desk)
        self.assertRegex(proposal['reference'], r'^\d{11}$')
        self.assertTrue(proposal['parent'].startswith('/'))
        calls = []

        def create(desk, args, dav):
            from agent.desk import save_matter
            calls.append(args)
            save_matter(desk.c, {'id': args['reference'], 'client_name': args['client_name'], 'path': args['path'], 'aliases': [], 'references': [],
                                 'correspondents': []})
            return {'dossier': args['reference'], 'etat': 'enregistre'}
        with patch('agent.workspace.create_matter', side_effect=create), patch('agent.boite5625._dav', return_value=None):
            out = boite5625.create_matter(self.desk, {'client_name': 'Client Durand', 'title': 'Assemblée générale', 'reference': '20261010001',
                                                      'parent': '/Dossiers', 'correspondent': 'Durand@Example.test'})
            with self.assertRaisesRegex(Stop, 'nom_dossier_invalide'):
                boite5625.create_matter(self.desk, {'client_name': '../Autre', 'reference': '20261010002', 'parent': '/Dossiers'})
        self.assertEqual(calls[0]['path'], '/Dossiers/Client Durand - Assemblée générale - 20261010001')
        self.assertEqual(out['id'], '20261010001')
        created = next(m for m in load_matters(self.desk.c) if m['id'] == '20261010001')
        self.assertEqual(created['correspondents'], [{'email': 'durand@example.test', 'role': 'client'}])
        with patch.dict(self.desk.c, {'nextcloud': {}}):
            with self.assertRaisesRegex(Stop, 'nextcloud_ou_dossier_local_a_configurer'):
                boite5625._dav(self.desk)

    def test_inbox_view_comes_first_and_drafts_are_really_hidden(self):
        page = self.request('/courriels', query='vue=boite')['body']
        self.assertLess(page.index('id="bx5625"'), page.index('id="ax-drafts"'))
        self.assertIn('id="ax-drafts" data-validity="9" hidden style="display:none"', page)
        self.assertIn('id="bx5625-flist"', page)
        css = (ROOT / 'agent' / 'static' / 'v5625.css').read_text(encoding='utf-8')
        self.assertIn('#ax-drafts[hidden]{display:none!important}', css)
        js = (ROOT / 'agent' / 'static' / 'v5625.js').read_text(encoding='utf-8')
        for hook in ('m5625/boite/dossiers', "sans_dossier: none ? 'oui' : ''", 'm5625/boite/creer-dossier', '__nouveau__'):
            self.assertIn(hook, js)
        from agent.standalone_auth import allowed
        self.assertTrue(allowed('avocat', 'POST', '/api440/m5625/boite/creer-dossier'))
        self.assertFalse(allowed('assistant', 'POST', '/api440/m5625/boite/creer-dossier'))


class VaultOnWindows(unittest.TestCase):
    def test_secret_permissions_are_not_judged_by_posix_bits_on_windows(self):
        from agent import vault567
        from agent.common import read_secret
        with TempDir() as tmp:
            secret = Path(tmp) / 'imap-password'
            vault567.write(secret, 's3cret')
            key = Path(tmp) / '.master.key'
            os.chmod(key, 0o644)
            os.chmod(secret, 0o644)
            if os.name != 'nt':
                with self.assertRaises(Stop):
                    read_secret(secret)
            with patch('agent.portable.WINDOWS', True):
                self.assertEqual(read_secret(secret), 's3cret')
                vault567.write(secret, 'nouveau')
                self.assertEqual(read_secret(secret), 'nouveau')
        self.assertFalse(portable.too_open(0o100666, 0o077) and portable.WINDOWS)
