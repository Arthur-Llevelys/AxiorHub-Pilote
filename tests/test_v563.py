"""5.6.3 : authentification, permissions et flux SSE, vérifiés par de vrais échanges HTTP (serveur lancé dans le test).

Ces cas n'étaient pas couverts : flux d'activité retenu par le mandataire du mode autonome ou par Waitress, place de flux jamais rendue,
sessions non révocables, tentatives illimitées, réglages du cabinet modifiables par un avocat, chemins publics trop larges."""
import hashlib
import http.client
import json
import os
from pathlib import Path
import re
import socket
from socketserver import ThreadingMixIn
import sqlite3
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from urllib.parse import urlencode
from wsgiref.simple_server import make_server, WSGIRequestHandler, WSGIServer

from agent import live430, standalone_auth
from agent.common import private_json
from agent.desk import Desk
from agent.standalone_auth import StandaloneAuth, allowed, normalize
from agent.web import App, LiveStream, live_cursor
import test_desk

ROOT = Path(__file__).resolve().parents[1]
TOKEN = 'jeton-interne-de-test-' + 'x' * 30
try:
    import waitress
except ImportError:
    waitress = None


class Quiet(WSGIRequestHandler):
    def log_message(self, *args):
        pass


class Threaded(ThreadingMixIn, WSGIServer):
    daemon_threads = True


def serve(app):
    server = make_server('127.0.0.1', 0, app, server_class=Threaded, handler_class=Quiet)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def first_event(port, path, headers, limit=8.0):
    """Secondes écoulées avant la réception du premier événement « activity » (ou None)."""
    conn = http.client.HTTPConnection('127.0.0.1', port, timeout=limit)
    started = time.monotonic()
    conn.request('GET', path, headers=headers)
    resp = conn.getresponse()
    if resp.status != 200:
        conn.close()
        return None, resp.status
    seen = b''
    try:
        while time.monotonic() - started < limit:
            chunk = resp.fp.read1(4096) if hasattr(resp.fp, 'read1') else resp.read(1)
            if not chunk:
                break
            seen += chunk
            if b'event: activity' in seen:
                return time.monotonic() - started, 200
    except socket.timeout:
        pass
    finally:
        conn.close()
    return None, 200


class Base(unittest.TestCase):
    request = test_desk.WebTests.request

    def setUp(self):
        test_desk.WebTests.setUp(self)                     # mêmes données fictives, sans hériter des tests de WebTests
        auth = json.loads(self.auth.read_text(encoding='utf-8'))
        auth['api_token_sha256'] = hashlib.sha256(TOKEN.encode()).hexdigest()
        private_json(self.auth, auth)
        d = Desk(self.f.c)
        d.db.execute("INSERT INTO live_events_v430(at,kind,message,job_id,matter) VALUES(?,?,?,?,?)",
                     (d.now(), 'job', 'Événement de test', None, ''))
        d.db.commit()
        d.db.close()
        short = live430.stream
        patcher = patch('agent.live430.stream', lambda cfg, after=0, duration=25: short(cfg, after, 6))
        patcher.start()
        self.addCleanup(patcher.stop)

    def standalone(self):
        env = patch.dict(os.environ, {'AXIORHUB_INTERNAL_API_TOKEN': TOKEN, 'AXIORHUB_ALLOW_SIGNUP': 'false'})
        env.start()
        self.addCleanup(env.stop)
        auth = json.loads(self.auth.read_text(encoding='utf-8'))
        auth['prefix'] = ''
        auth['origin'] = 'https://agent.example.test'
        private_json(self.auth, auth)
        tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(tmp.cleanup)
        outer = StandaloneAuth(App(str(self.config), str(self.auth)), Path(tmp.name) / 'auth', 'https://agent.example.test')
        self.addCleanup(outer.db.close)
        return outer

    def member(self, outer, email, role, password='motdepasse-solide-1'):
        salt = os.urandom(16).hex()
        cur = outer.db.execute('INSERT INTO users(email,password_hash,salt,active,created,role,name) VALUES (?,?,?,?,?,?,?)',
                               (email, outer._hash(password, salt), salt, 1, '2026-10-04T08:00:00+00:00', role, email.split('@')[0]))
        outer.db.commit()
        return cur.lastrowid


class Sse(Base):
    def test_system_mode_stream_delivers_events_at_once(self):
        server = serve(self.app)
        self.addCleanup(server.shutdown)
        import base64
        basic = 'Basic ' + base64.b64encode(('admin:' + self.password).encode()).decode()
        delay, status = first_event(server.server_port, '/agent-courriel/live/events', {'Authorization': basic, 'Host': 'cabinet.example.test', 'X-Forwarded-Proto': 'https'})
        self.assertEqual(status, 200)
        self.assertIsNotNone(delay)
        self.assertLess(delay, 3.0)

    def test_standalone_proxy_streams_instead_of_buffering(self):
        outer = self.standalone()
        uid = self.member(outer, 'avocat@example.test', 'administrateur')
        cookie = 'axiorhub_session=' + outer._session(uid)
        server = serve(outer)
        self.addCleanup(server.shutdown)
        delay, status = first_event(server.server_port, '/live/events', {'Cookie': cookie})
        self.assertEqual(status, 200)
        self.assertIsNotNone(delay, 'aucun événement reçu')
        self.assertLess(delay, 3.0, 'le flux est retenu jusqu’à sa fermeture (6 s)')

    @unittest.skipUnless(waitress, 'Waitress absent (installé dans l’image Docker et sur GitHub)')
    def test_waitress_sends_each_event_immediately(self):
        from waitress.server import create_server
        # mêmes réglages qu'en production (agent/web.py : serve) : mandataire local de confiance, envois non retenus
        server = create_server(self.app, host='127.0.0.1', port=0, threads=4, send_bytes=1, trusted_proxy='127.0.0.1',
                               trusted_proxy_headers={'x-forwarded-proto', 'x-forwarded-for'}, clear_untrusted_proxy_headers=True)
        threading.Thread(target=server.run, daemon=True).start()
        self.addCleanup(server.close)
        import base64
        basic = 'Basic ' + base64.b64encode(('admin:' + self.password).encode()).decode()
        delay, status = first_event(server.effective_port, '/agent-courriel/live/events', {'Authorization': basic, 'Host': 'cabinet.example.test',
                                                                                         'X-Forwarded-Proto': 'https', 'X-Forwarded-For': '127.0.0.1'})
        self.assertEqual(status, 200)
        self.assertIsNotNone(delay)
        self.assertLess(delay, 3.0)

    def test_servers_are_configured_without_output_buffering(self):
        self.assertIn('send_bytes=1', (ROOT / 'standalone.py').read_text(encoding='utf-8'))
        self.assertIn('send_bytes=1', (ROOT / 'agent' / 'web.py').read_text(encoding='utf-8'))
        import importlib.util
        spec = importlib.util.spec_from_file_location('installer_ui', ROOT / 'install-interface.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        conf = module.apache_config('courriel.example.com')
        self.assertIn('<Location "/agent-courriel/live/events">', conf)
        self.assertIn('flushpackets=on', conf)
        self.assertIn('SetEnv no-gzip 1', conf)
        vps = (ROOT / 'deploy' / 'vps' / 'apache' / 'axiorhub.conf').read_text(encoding='utf-8')
        self.assertIn('flushpackets=on', vps)
        self.assertIn('no-gzip', vps)

    def test_stream_slot_is_returned_even_if_never_read(self):
        before = self.app.live_slots._value
        out = {}
        result = self.app({'REQUEST_METHOD': 'GET', 'PATH_INFO': '/agent-courriel/live/events', 'QUERY_STRING': '',
                           'HTTP_HOST': 'cabinet.example.test', 'HTTP_X_FORWARDED_PROTO': 'https', 'wsgi.url_scheme': 'http',
                           'HTTP_AUTHORIZATION': 'Bearer ' + TOKEN, 'HTTP_LAST_EVENT_ID': 'pas-un-nombre',
                           'wsgi.input': __import__('io').BytesIO(b'')}, lambda s, h, e=None: out.update(status=s))
        self.assertTrue(out['status'].startswith('200'))               # Last-Event-ID invalide : plus d'erreur 500
        self.assertIsInstance(result, LiveStream)
        self.assertEqual(self.app.live_slots._value, before - 1)
        result.close()                                                 # le navigateur a coupé avant le premier envoi
        result.close()
        self.assertEqual(self.app.live_slots._value, before)
        self.assertEqual(live_cursor('12'), 12)
        self.assertEqual(live_cursor('-3'), 0)
        self.assertEqual(live_cursor(None), 0)


class Authentication(Base):
    def call(self, outer, path, method='GET', data=None, cookie='', headers=None):
        raw = urlencode(data or {}).encode()
        out = {}
        env = {'PATH_INFO': path, 'REQUEST_METHOD': method, 'QUERY_STRING': path.partition('?')[2], 'HTTP_COOKIE': cookie,
               'CONTENT_LENGTH': str(len(raw)), 'CONTENT_TYPE': 'application/x-www-form-urlencoded', 'wsgi.input': __import__('io').BytesIO(raw),
               'HTTP_ORIGIN': 'https://agent.example.test', 'REMOTE_ADDR': '198.51.100.7', **(headers or {})}
        env['PATH_INFO'] = path.partition('?')[0]
        body = b''.join(outer(env, lambda s, h, e=None: out.update(status=s, headers=dict(h))))
        out['body'] = body.decode('utf-8', 'replace')
        return out

    def login(self, outer, email, password='motdepasse-solide-1'):
        r = self.call(outer, '/login', 'POST', {'email': email, 'password': password})
        self.assertTrue(r['status'].startswith('303'), r['status'] + r['body'][:200])
        return r['headers']['Set-Cookie'].split(';')[0]

    def test_logout_and_password_change_revoke_sessions(self):
        outer = self.standalone()
        self.member(outer, 'avocat@example.test', 'administrateur')
        a = self.login(outer, 'avocat@example.test')
        b = self.login(outer, 'avocat@example.test')
        self.assertNotIn('.', a.split('=', 1)[1][:10] + '')                       # jeton aléatoire, plus de données dans le cookie
        self.assertTrue(self.call(outer, '/comptes', cookie=a)['status'].startswith('200'))
        self.call(outer, '/logout', cookie=a)
        self.assertIn('Se connecter', self.call(outer, '/comptes', cookie=a)['body'])           # cookie volé après déconnexion : inutile
        csrf = re.search(r'name="csrf" value="([^"]+)"', self.call(outer, '/mot-de-passe', cookie=b)['body']).group(1)
        c = self.login(outer, 'avocat@example.test')
        self.call(outer, '/mot-de-passe', 'POST', {'csrf': csrf, 'password': 'nouveau-secret-123', 'confirm': 'nouveau-secret-123'}, cookie=b)
        self.assertIn('Se connecter', self.call(outer, '/comptes', cookie=c)['body'])           # autres appareils déconnectés
        self.assertTrue(self.call(outer, '/comptes', cookie=b)['status'].startswith('200'))     # l'appareil courant reste connecté

    def test_disabling_or_resetting_a_member_ends_their_sessions(self):
        outer = self.standalone()
        self.member(outer, 'admin@example.test', 'administrateur')
        uid = self.member(outer, 'collab@example.test', 'avocat')
        admin = self.login(outer, 'admin@example.test')
        collab = self.login(outer, 'collab@example.test')
        page = self.call(outer, '/comptes', cookie=admin)['body']
        csrf = re.search(r'name="csrf" value="([^"]+)"', page).group(1)
        self.call(outer, '/comptes', 'POST', {'csrf': csrf, 'op': 'reset', 'id': uid}, cookie=admin)
        self.assertIn('Se connecter', self.call(outer, '/', cookie=collab)['body'])
        self.assertEqual(outer.db.execute('SELECT COUNT(*) FROM sessions WHERE user_id=?', (uid,)).fetchone()[0], 0)

    def test_login_attempts_are_limited_and_timing_does_not_reveal_accounts(self):
        outer = self.standalone()
        self.member(outer, 'avocat@example.test', 'administrateur')
        for _ in range(5):
            self.assertIn('Identifiants incorrects', self.call(outer, '/login', 'POST', {'email': 'avocat@example.test', 'password': 'faux'})['body'])
        blocked = self.call(outer, '/login', 'POST', {'email': 'avocat@example.test', 'password': 'motdepasse-solide-1'})
        self.assertTrue(blocked['status'].startswith('429'))                     # même le bon mot de passe attend 15 minutes
        self.assertEqual(blocked['headers'].get('Retry-After'), '900')
        calls = []
        real = outer._hash
        with patch.object(outer, '_hash', side_effect=lambda p, s: calls.append(s) or real(p, s)):
            self.call(outer, '/login', 'POST', {'email': 'inconnu@example.test', 'password': 'x'})
        self.assertEqual(len(calls), 1)                                          # calcul effectué aussi pour une adresse inconnue

    def test_reset_link_requires_an_active_account_and_is_single_use(self):
        outer = self.standalone()
        uid = self.member(outer, 'collab@example.test', 'avocat')
        tokens = []
        with patch.object(outer, '_send_reset', side_effect=lambda email, token: tokens.append(token) or True):
            for _ in range(5):
                self.call(outer, '/forgot-password', 'POST', {'email': 'collab@example.test'})
        self.assertEqual(len(tokens), 3)                                         # 3 liens par heure au plus
        session = self.login(outer, 'collab@example.test')
        r = self.call(outer, '/reset-password?token=' + tokens[0], 'POST', {'password': 'nouveau-secret-123'})
        self.assertTrue(r['status'].startswith('303'))
        self.assertIn('Lien invalide', self.call(outer, '/reset-password?token=' + tokens[1])['body'])   # les autres liens tombent
        self.assertIn('Se connecter', self.call(outer, '/', cookie=session)['body'])                       # sessions révoquées
        outer.db.execute('INSERT INTO reset_tokens VALUES (?,?,?,0)', (hashlib.sha256(b'tok').hexdigest(), uid, '2099-01-01T00:00:00+00:00'))
        outer.db.execute('UPDATE users SET active=0 WHERE id=?', (uid,))
        outer.db.commit()
        self.assertIn('Lien invalide', self.call(outer, '/reset-password?token=tok')['body'])              # compte désactivé
        self.assertEqual(r['headers'].get('Referrer-Policy') or self.call(outer, '/login')['headers'].get('Referrer-Policy'), 'no-referrer')

    def test_public_paths_are_strict_and_internal_headers_are_dropped(self):
        outer = self.standalone()
        seen = []
        inner = outer.app
        outer.app = lambda env, start: seen.append({k: env.get(k) for k in ('PATH_INFO', 'HTTP_X_AXIORHUB_ROLE')}) or inner(env, start)
        r = self.call(outer, '/static/../parametres')
        self.assertTrue(r['status'].startswith('400'))
        self.assertIn('Se connecter', self.call(outer, '/static/v530.js', 'POST')['body'])                # écriture : connexion exigée
        self.call(outer, '/static/v530.js', headers={'HTTP_X_AXIORHUB_ROLE': 'administrateur'})
        self.assertEqual(seen[-1], {'PATH_INFO': '/static/v530.js', 'HTTP_X_AXIORHUB_ROLE': None})
        self.assertIsNone(normalize('/a/./b'))
        self.assertEqual(normalize('//parametres//x/'), '/parametres/x/')

    @unittest.skipIf(os.name == 'nt', 'droits POSIX')
    def test_account_database_is_private(self):
        outer = self.standalone()
        self.assertEqual(oct(os.stat(outer.state / 'users.sqlite3').st_mode & 0o777), '0o600')
        self.assertEqual(oct(os.stat(outer.state).st_mode & 0o777), '0o700')


class Permissions(unittest.TestCase):
    def test_cabinet_settings_are_reserved_to_the_administrator(self):
        for action in ('set_automation_level370', 'save_ecosystem_service380', 'register_lawve_extension', 'save_cabinet_profile',
                       'save_workspace_mapping', 'test_openrouter393', 'save_ai_provider'):
            self.assertFalse(allowed('avocat', 'POST', '/action', action), action)
            self.assertTrue(allowed('administrateur', 'POST', '/action', action), action)
        for api in ('/api440/m510/profile', '/api440/m510/template/upload', '/api440/m500/time/bareme', '/api440/m540/mode'):
            self.assertFalse(allowed('avocat', 'POST', api), api)
        self.assertTrue(allowed('avocat', 'POST', '/api440/m500/time/add'))
        self.assertTrue(allowed('avocat', 'POST', '/action', 'save_mail_rule370'))

    def test_integration_api_is_read_only_for_members(self):
        for role in ('avocat', 'assistant'):
            self.assertFalse(allowed(role, 'POST', '/api/v1/automation'))
            self.assertFalse(allowed(role, 'DELETE', '/api/v1/jobs/3'))
            self.assertTrue(allowed(role, 'GET', '/api/v1/openapi.json'))

    def test_ambiguous_paths_cannot_bypass_reserved_pages(self):
        for path in ('//parametres', '/parametres/', '///ia-externe//', '/comptes/', '/a/../parametres'):
            self.assertFalse(allowed('avocat', 'GET', path), path)


class Packaging(unittest.TestCase):
    def test_notification_dependency_is_installed_where_tests_and_docker_run(self):
        self.assertIn('cryptography==', (ROOT / 'docker' / 'Dockerfile').read_text(encoding='utf-8'))
        ci = (ROOT / '.github' / 'workflows' / 'tests.yml').read_text(encoding='utf-8')
        self.assertIn('cryptography==', ci)
        self.assertIn("Tests exécutés depuis l'archive livrée", ci)
        self.assertIn('scripts/ci-annotate.py', ci)


# ======================================================================================== 5.6.3 : arrêter, effacer, annuler
import test_v530 as t530
from agent import cockpit530 as ck, docrequest520 as dr, jobview561, watch430
from agent.common import Stop
from agent.dav import DAV


class StopAndClear(t530.Base):
    def test_a_requested_document_can_be_stopped_before_it_starts(self):
        out = ck.ask(self.desk, {'text': 'Prépare une note de synthèse pour ALPHA', 'mode': 'document'})
        html = ck.thread_html(self.desk, '/p')
        self.assertIn('data-stop="%s"' % out['ref'], html)
        self.assertIn('data-clear="1"', html)
        msg = ck.stop(self.desk, {'ref': out['ref']})['message']
        self.assertEqual(msg, 'Tâche arrêtée avant son démarrage.')
        job = ck.job_of(self.desk, out['ref'])
        self.assertEqual(self.desk.db.execute('SELECT status FROM jobs WHERE id=?', (job,)).fetchone()['status'], 'cancelled')
        html = ck.thread_html(self.desk, '/p')
        self.assertIn('Demande arrêtée à votre demande.', html)
        self.assertNotIn('data-stop=', html)
        self.assertEqual(ck.stop(self.desk, {'ref': out['ref']})['message'], 'Cette tâche est déjà terminée.')

    def test_a_question_can_be_stopped_and_a_running_job_gets_a_stop_request(self):
        out = ck.ask(self.desk, {'text': 'Quelle est la prochaine audience du dossier ALPHA ?'})
        self.assertTrue(out['ref'].startswith('ask:'))
        job = ck.job_of(self.desk, out['ref'])
        self.desk.db.execute("UPDATE jobs SET status='running' WHERE id=?", (job,))
        self.desk.db.commit()
        self.assertIn('Arrêt demandé', ck.stop(self.desk, {'ref': out['ref']})['message'])
        self.assertEqual(self.desk.db.execute('SELECT status FROM jobs WHERE id=?', (job,)).fetchone()['status'], 'cancel_requested')
        with self.assertRaises(Stop):
            ck.stop(self.desk, {'ref': 'docreq:inconnu'})

    def test_clear_discussion_keeps_the_work(self):
        out = ck.ask(self.desk, {'text': 'Prépare une note de synthèse pour ALPHA', 'mode': 'document'})
        jobs = self.desk.db.execute('SELECT COUNT(*) FROM jobs').fetchone()[0]
        self.assertEqual(ck.handle(self.desk, 'm530/clear', {}, 'POST')['message'], 'Discussion effacée.')
        self.assertEqual(self.desk.db.execute('SELECT COUNT(*) FROM cockpit530_messages').fetchone()[0], 0)
        self.assertEqual(self.desk.db.execute('SELECT COUNT(*) FROM jobs').fetchone()[0], jobs)
        self.assertNotIn('data-clear', ck.thread_html(self.desk, '/p'))
        self.assertTrue(ck.job_of(self.desk, out['ref']))

    def test_a_job_can_be_cancelled_from_its_detail(self):
        jid = self.desk.enqueue('analyze_deadline450', {'matter': '20240101001', 'path': '/Dossiers/DEMO/Pièces/x.pdf'})
        self.assertIn('data-cancel-job="%d"' % jid, jobview561.job_html(self.desk, '/p', jid))
        self.assertEqual(ck.handle(self.desk, 'm530/cancel', {'job': jid}, 'POST')['message'], 'Travail annulé.')
        self.assertNotIn('data-cancel-job', jobview561.job_html(self.desk, '/p', jid))
        self.assertTrue(allowed('assistant', 'POST', '/api440/m530/stop'))
        self.assertTrue(allowed('assistant', 'POST', '/api440/m530/clear'))
        self.assertFalse(allowed('assistant', 'POST', '/api440/m530/cancel'))
        js = (ROOT / 'agent' / 'static' / 'v530.js').read_text(encoding='utf-8')
        for marker in ("call('m530/stop'", "call('m530/clear'", "call('m530/cancel'", 'Effacer la discussion affichée ?'):
            self.assertIn(marker, js)


class DocumentSurveillance(unittest.TestCase):
    XML = (b'<?xml version="1.0"?><d:multistatus xmlns:d="DAV:">'
           b'<d:response><d:href>/remote.php/dav/files/u/Dossiers/A/</d:href><d:propstat><d:prop><d:resourcetype><d:collection/></d:resourcetype>'
           b'</d:prop><d:status>HTTP/1.1 200 OK</d:status></d:propstat></d:response>'
           b'<d:response><d:href>/remote.php/dav/files/u/Dossiers/A/Remise%2541.pdf</d:href><d:propstat><d:prop><d:getetag>"1"</d:getetag>'
           b'</d:prop><d:status>HTTP/1.1 200 OK</d:status></d:propstat></d:response>'
           b'<d:response><d:href>/remote.php/dav/files/u/Dossiers/A/Contrat.pdf</d:href><d:propstat><d:prop><d:getetag>"2"</d:getetag>'
           b'</d:prop><d:status>HTTP/1.1 200 OK</d:status></d:propstat></d:response></d:multistatus>')

    def test_an_ambiguous_file_name_is_skipped_not_fatal(self):
        import xml.etree.ElementTree as ET
        from types import SimpleNamespace
        dav = DAV.__new__(DAV)
        dav.http = SimpleNamespace(base='https://cloud.example.test')
        dav.files = 'https://cloud.example.test/remote.php/dav/files/u'
        dav.cfg = {'roots': ['/Dossiers']}
        dav.file_url = lambda p: dav.files + p
        dav.request_xml = lambda *a, **k: ET.fromstring(self.XML)
        items = dav.list_folder('/Dossiers/A')
        self.assertEqual([x['path'] for x in items], ['/Dossiers/A/Contrat.pdf'])
        self.assertEqual(len(dav.refused), 1)

    def test_one_unreadable_matter_no_longer_suspends_the_whole_surveillance(self):
        class Fake:
            refused = []
            def inventory_step(self, path, state=None):
                if path.endswith('DEMO'):
                    raise Stop('chemin_refuse')
                return {'root': path, 'pending': [], 'files': {}, 'visited': 1}, True
        base = t530.Base('setUp')
        base.setUp()
        self.addCleanup(base.doCleanups)
        events = []
        with patch('agent.watch430.emit', side_effect=lambda *a, **k: events.append(a[2])), patch('agent.watch430.heartbeat'),                 patch('agent.watch430.progress'), patch('agent.watch430.observe', return_value='unchanged'):
            out = watch430.documents_check(base.desk, Fake())
        self.assertEqual(out['failed'], 1)
        self.assertTrue(any('Dossier non lu (chemin_refuse)' in m for m in events))


if __name__ == '__main__':
    unittest.main()
