"""5.6.108 (lot O) : serveur de documents EuroOffice / OnlyOffice — guide pas à pas, rappel accepté depuis Docker sur un poste,
« Tester la connexion » étape par étape.

O1 : guides français et anglais (serveur, poste Windows 11, poste Ubuntu 24.04), liés depuis les guides d'installation et l'aide intégrée.
O2 : rubrique de l'éditeur (aperçu en lecture seule sans serveur de documents, lien vers le guide, bouton « Tester la connexion »).
O3 : garde d'hôte — « host.docker.internal » admis sur un poste, depuis l'adresse de bouclage, sur le port configuré et pour les deux
     seules routes à jeton du serveur de documents ; tout le reste refusé (protection contre le DNS rebinding). Une mutation par garde-fou.
O4 : adresse http du serveur de documents admise seulement sur l'adresse de bouclage (poste) ; téléchargement des modifications
     limité au serveur enregistré ; jeton d'essai sans aucune donnée de dossier.

Données fictives (example.test, DOS-001). Aucun appel réseau : les sondes sont remplacées par de faux services.
"""
import base64
import hashlib
import inspect
import io
import json
import re
import textwrap
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlsplit

from agent.common import Stop, private_json
from agent.desk import Desk
from agent import office440
import agent.web as web
from test_v440_office import FakeNextcloud, docx
import test_agent as fixtures

ROOT = Path(__file__).resolve().parents[1]
TOKEN = 'eyJrIjoiZXNzYWkifQ.' + 'A' * 43
ORIGIN = urlsplit('http://127.0.0.1:8769')
POSTE = {'poste': True, 'prefix': '/agent-courriel'}


def env_for(path, host='host.docker.internal:8769', remote='127.0.0.1'):
    return {'HTTP_HOST': host, 'REMOTE_ADDR': remote, 'PATH_INFO': path}


class HostGuardTests(unittest.TestCase):
    """O3 : la garde d'hôte, au plus juste."""

    ACCEPTED = ('/agent-courriel/office/file/' + TOKEN, '/agent-courriel/office/callback/' + TOKEN,
                '/office/callback/' + TOKEN)
    REFUSED_PATHS = ('/agent-courriel/', '/agent-courriel/documents', '/agent-courriel/api440/office/config',
                     '/agent-courriel/office/plugin/config.json', '/agent-courriel/office/bridge/' + TOKEN,
                     '/agent-courriel/office/file/court', '/agent-courriel/office/file/' + TOKEN + '/../../parametres',
                     '/agent-courriel/office/callback/' + TOKEN + '/x', '/agent-courriel/office/autre/' + TOKEN,
                     '/autre-prefixe/office/file/' + TOKEN, '/agent-courriel/parametres?x=/office/file/' + TOKEN)

    def test_O3_docker_desktop_reaches_the_two_token_routes_on_a_workstation(self):
        for path in self.ACCEPTED:
            self.assertTrue(web.docker_alias(env_for(path), POSTE, ORIGIN), path)
        # IPv6 de bouclage et adresse IPv4 transposée : ce que voit Waitress selon la pile réseau du poste
        for remote in ('::1', '::ffff:127.0.0.1'):
            self.assertTrue(web.docker_alias(env_for(self.ACCEPTED[1], remote=remote), POSTE, ORIGIN), remote)

    def test_O3_every_other_path_stays_refused(self):
        for path in self.REFUSED_PATHS:
            self.assertFalse(web.docker_alias(env_for(path), POSTE, ORIGIN), path)

    def test_O3_server_version_other_port_other_source_other_name_refused(self):
        path = self.ACCEPTED[1]
        for env, auth in ((env_for(path), {'prefix': '/agent-courriel'}),                       # version serveur
                          (env_for(path), {'poste': False, 'prefix': '/agent-courriel'}),
                          (env_for(path, host='host.docker.internal:8770'), POSTE),             # autre port
                          (env_for(path, host='host.docker.internal'), POSTE),                  # port absent
                          (env_for(path, host='host.docker.internal:x'), POSTE),
                          (env_for(path, remote='192.0.2.20'), POSTE),                          # autre adresse source
                          (env_for(path, remote=''), POSTE),
                          (env_for(path, host='documents.example.test:8769'), POSTE),           # autre nom
                          (env_for(path, host='host.docker.internal.example.test:8769'), POSTE)):
            self.assertFalse(web.docker_alias(env, auth, ORIGIN), (env, auth))

    def test_O3_nextcloud_integration_alias_unchanged(self):
        self.assertTrue(web.docker_alias(env_for('/agent-courriel/integration/v1/health'), POSTE, ORIGIN))

    def _mutant(self, old, new):
        """Copie en mémoire de la garde avec une condition retirée (le code du dépôt n'est jamais modifié)."""
        source = textwrap.dedent(inspect.getsource(web.docker_alias))
        self.assertIn(old, source, 'garde-fou introuvable : ' + old)
        namespace = dict(vars(web))
        exec(compile(source.replace(old, new, 1), '<mutant>', 'exec'), namespace)
        return namespace['docker_alias']

    def test_O3_mutations_each_guard_is_needed(self):
        path = self.ACCEPTED[1]
        cases = (   # (condition retirée, remplacement, cas refusé qui deviendrait accepté)
            ("not (auth or {}).get('poste') or ", '', (env_for(path), {'prefix': '/agent-courriel'})),
            ("str(env.get('REMOTE_ADDR') or '') not in ('127.0.0.1','::1','::ffff:127.0.0.1')", 'False',
             (env_for(path, remote='192.0.2.20'), POSTE)),
            ("(port or None)==(origin.port or None)", 'True', (env_for(path, host='host.docker.internal:8770'), POSTE)),
            ("parts.hostname=='host.docker.internal' and ", '', (env_for(path, host='documents.example.test:8769'), POSTE)),
            ("not office_token_route(path,(auth or {}).get('prefix') or '/agent-courriel')", 'False',
             (env_for('/agent-courriel/documents'), POSTE)),
        )
        for old, new, (env, auth) in cases:
            self.assertFalse(web.docker_alias(env, auth, ORIGIN), old)
            self.assertTrue(self._mutant(old, new)(env, auth, ORIGIN), 'mutation non détectée : ' + old)

    def test_O3_route_pattern_mutation_detected(self):
        self.assertFalse(web.office_token_route('/agent-courriel/office/plugin/' + TOKEN))
        mutant = re.compile(r'/office/(?:file|callback|plugin)/[A-Za-z0-9_\-.]{20,2000}')
        with patch.object(web, 'OFFICE_TOKEN_ROUTE', mutant):
            self.assertTrue(web.office_token_route('/agent-courriel/office/plugin/' + TOKEN))


class WorkstationWsgiTests(unittest.TestCase):
    """O3 de bout en bout : la vraie application WSGI d'un poste, appelée comme le ferait le serveur de documents dans Docker Desktop."""
    PATH = '/Dossiers/DEMO/Projet.docx'
    SECRET = 'secret-essai-lot-o-' + 'x' * 20

    def setUp(self):
        self.f = fixtures.EngineTests('test_observation_has_no_mail_write')
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.config = self.f.base / 'config.json'
        private_json(self.config, self.f.c)
        self.auth = self.f.base / 'auth.json'
        salt = 'aa' * 16
        self.password = 'password-test-only-long'
        private_json(self.auth, {'username': 'admin', 'salt': salt, 'poste': True,
                                 'hash': hashlib.scrypt(self.password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1).hex(),
                                 'origin': 'http://127.0.0.1:8769', 'prefix': '/agent-courriel', 'csrf': 'test-csrf'})
        self.app = web.App(str(self.config), str(self.auth))
        self.nc = FakeNextcloud()
        self.nc.add(self.PATH, docx('v1'))
        q = patch('agent.office440.dav_client', lambda desk: self.nc)
        q.start()
        self.addCleanup(q.stop)
        self.desk = Desk(self.f.c)
        self.addCleanup(self.desk.db.close)
        office440.save_settings(self.desk, 'http://127.0.0.1:8080', self.SECRET, 'http://host.docker.internal:8769/agent-courriel', 'euro-office')

    def call(self, path, method='GET', body=b'', host='host.docker.internal:8769', remote='127.0.0.1', extra=None, auth=False):
        env = {'REQUEST_METHOD': method, 'PATH_INFO': '/agent-courriel' + path, 'QUERY_STRING': '', 'HTTP_HOST': host,
               'REMOTE_ADDR': remote, 'wsgi.url_scheme': 'http', 'wsgi.input': io.BytesIO(body), 'CONTENT_LENGTH': str(len(body)),
               'CONTENT_TYPE': 'application/json'}
        if auth:
            env['HTTP_AUTHORIZATION'] = 'Basic ' + base64.b64encode(('admin:' + self.password).encode()).decode()
        env.update(extra or {})
        out = {}

        def start(status, headers):
            out.update(status=status, headers=dict(headers))
        out['body'] = b''.join(self.app(env, start))
        return out

    def opened(self):
        auth = json.loads(self.auth.read_text(encoding='utf-8'))
        return office440.open_document(self.desk, auth, self.PATH, client=self.nc)['config']

    def test_O3_document_server_in_docker_desktop_downloads_and_calls_back(self):
        cfg = self.opened()
        self.assertTrue(cfg['document']['url'].startswith('http://host.docker.internal:8769/agent-courriel/office/file/'))
        file_tok = cfg['document']['url'].rsplit('/', 1)[1]
        cb_tok = cfg['editorConfig']['callbackUrl'].rsplit('/', 1)[1]
        got = self.call('/office/file/' + file_tok)
        self.assertTrue(got['status'].startswith('200'), got['status'])
        self.assertEqual(got['body'], self.nc.files[self.PATH])
        payload = {'key': cfg['document']['key'], 'status': 1}
        bearer = 'Bearer ' + office440.jwt_encode({'payload': payload}, self.SECRET)
        back = self.call('/office/callback/' + cb_tok, 'POST', json.dumps(payload).encode(), extra={'HTTP_AUTHORIZATION': bearer})
        self.assertEqual(json.loads(back['body']), {'error': 0})

    def test_O3_other_screens_still_refused_under_the_docker_name(self):
        for path in ('/', '/documents', '/parametres', '/api440/office/config'):
            r = self.call(path, auth=True)
            self.assertFalse(r['status'].startswith(('200', '302', '303')), (path, r['status']))
            self.assertIn('hote'.encode(), r['body'].lower().replace('ô'.encode(), b'o'))

    def test_O3_callback_refused_from_another_source_or_port(self):
        cfg = self.opened()
        cb_tok = cfg['editorConfig']['callbackUrl'].rsplit('/', 1)[1]
        payload = {'key': cfg['document']['key'], 'status': 1}
        bearer = 'Bearer ' + office440.jwt_encode({'payload': payload}, self.SECRET)
        for host, remote in (('host.docker.internal:8769', '192.0.2.20'), ('host.docker.internal:8770', '127.0.0.1')):
            r = self.call('/office/callback/' + cb_tok, 'POST', json.dumps(payload).encode(), host=host, remote=remote,
                          extra={'HTTP_AUTHORIZATION': bearer})
            self.assertNotEqual(r['body'], b'{"error": 0}', (host, remote))
            self.assertFalse(r['status'].startswith('200'), (host, remote))

    def test_O3_wrong_jwt_secret_refused_even_through_the_alias(self):
        cfg = self.opened()
        cb_tok = cfg['editorConfig']['callbackUrl'].rsplit('/', 1)[1]
        payload = {'key': cfg['document']['key'], 'status': 1}
        bearer = 'Bearer ' + office440.jwt_encode({'payload': payload}, 'mauvais-secret-' + 'y' * 20)
        r = self.call('/office/callback/' + cb_tok, 'POST', json.dumps(payload).encode(), extra={'HTTP_AUTHORIZATION': bearer})
        self.assertTrue(r['status'].startswith('403'))
        self.assertEqual(json.loads(r['body'])['error'], 1)

    def test_O4_test_route_is_post_admin_and_csrf_only(self):
        from agent.standalone_auth import allowed
        self.assertTrue(allowed('administrateur', 'POST', '/api440/settings/office/test'))
        for role in ('avocat', 'assistant'):
            self.assertFalse(allowed(role, 'POST', '/api440/settings/office/test'), role)
        # sans jeton CSRF : refusé avant toute sonde
        with patch('agent.office440._probe', side_effect=AssertionError('sonde appelée sans CSRF')):
            r = self.call('/api440/settings/office/test', 'POST', b'{}', host='127.0.0.1:8769', auth=True,
                          extra={'HTTP_ORIGIN': 'http://127.0.0.1:8769'})
        self.assertFalse(r['status'].startswith('200'), r['status'])


class OfficeSettingsTests(unittest.TestCase):
    """O4 : adresses, téléchargement des modifications, jeton d'essai, test étape par étape."""

    def setUp(self):
        self.f = fixtures.EngineTests('test_observation_has_no_mail_write')
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.desk = Desk(self.f.c)
        self.addCleanup(self.desk.db.close)
        self.auth = {'origin': 'http://127.0.0.1:8769', 'prefix': '/agent-courriel', 'poste': True}

    def test_O4_http_server_address_only_on_loopback(self):
        for url, want in (('http://127.0.0.1:8080', 'http://127.0.0.1:8080'), ('http://localhost:8080/', 'http://localhost:8080'),
                          ('http://[::1]:8080', 'http://[::1]:8080'), ('https://documents.example.test/', 'https://documents.example.test')):
            self.assertEqual(office440.valid_server_url(url), want)
        for url in ('http://documents.example.test', 'http://192.0.2.10:8080', 'http://127.0.0.1.example.test:8080',
                    'ftp://127.0.0.1:8080', 'http://essai:x' + chr(64) + '127.0.0.1:8080', 'http://127.0.0.1:8080/?x=1'):
            with self.assertRaises(Stop, msg=url):
                office440.valid_server_url(url)

    def test_O4_edited_file_fetched_only_from_the_registered_server(self):
        office440.save_settings(self.desk, 'http://127.0.0.1:8080', 'secret-essai-' + 'z' * 20, '', 'euro-office')

        class Client:
            def request(self, method, url, limit):
                return b'PK-ok'
        self.assertEqual(office440._fetch_edited(self.desk, 'http://127.0.0.1:8080/cache/files/data/k/output.docx', Client()), b'PK-ok')
        for url in ('https://127.0.0.1:8080/cache/x', 'http://127.0.0.1:8081/cache/x', 'http://documents.example.test/cache/x',
                    'http://localhost:8080/cache/x'):
            with self.assertRaises(Stop, msg=url):
                office440._fetch_edited(self.desk, url, Client())
        office440.save_settings(self.desk, 'https://documents.example.test', '', '', 'onlyoffice')
        with self.assertRaises(Stop):   # serveur HTTPS : jamais de téléchargement en clair
            office440._fetch_edited(self.desk, 'http://documents.example.test/cache/x', Client())
        self.assertEqual(office440._fetch_edited(self.desk, 'https://documents.example.test/cache/x', Client()), b'PK-ok')

    def test_O4_probe_token_serves_a_fixed_test_file_and_nothing_else(self):
        token = office440.sign(self.desk, {'t': 'probe', 'k': 'essai'}, ttl=60)
        data, name = office440.serve_file(self.desk, token)
        self.assertEqual(data, office440.PROBE_BYTES)
        self.assertNotIn(b'DOS-001', data)
        self.assertTrue(name.endswith('.txt'))
        with self.assertRaises(Stop):   # un jeton d'essai n'ouvre jamais le rappel
            office440.handle_callback(self.desk, token, '', b'{"key":"essai","status":2}')
        expired = office440.sign(self.desk, {'t': 'probe', 'k': 'essai'}, ttl=-5)
        with self.assertRaises(Stop):
            office440.serve_file(self.desk, expired)

    def _fake_probe(self, convert_answer, record):
        def probe(url, method='GET', body=None, headers=None, timeout=12):
            record.append((url, method, body, dict(headers or {})))
            if url.endswith('/healthcheck'):
                return 200, {}, b'true'
            if url.endswith('/api.js'):
                return 200, {}, b'window.DocsAPI = {};'
            if url.endswith('/hosting/discovery'):
                return 200, {}, b'<wopi-discovery><net-zone><app><action name="edit"/></app></net-zone></wopi-discovery>'
            if url.endswith('/command'):
                return 200, {}, b'{"error":0,"version":"9.3.7"}'
            if '/ConvertService.ashx' in url:
                return 200, {}, json.dumps(convert_answer).encode()
            return 200, {}, b''
        return probe

    def test_O2_reachability_step_reports_success_and_sends_no_matter_data(self):
        office440.save_settings(self.desk, 'http://127.0.0.1:8080', 'secret-essai-' + 'z' * 20, 'http://host.docker.internal:8769/agent-courriel', 'euro-office')
        record = []
        checks = office440.diagnostic(self.desk, self.auth, probe=self._fake_probe({'endConvert': True, 'percent': 100, 'fileUrl': 'http://127.0.0.1:8080/x'}, record), reach=True)
        names = [c['name'] for c in checks]
        self.assertIn('Le serveur de documents joint AxiorHub', names)
        step = checks[names.index('Le serveur de documents joint AxiorHub')]
        self.assertTrue(step['ok'], step)
        convert = [r for r in record if '/ConvertService.ashx' in r[0]][0]
        sent = json.loads(convert[2])
        self.assertTrue(sent['url'].startswith('http://host.docker.internal:8769/agent-courriel/office/file/'))
        self.assertIn('token', sent)
        self.assertTrue(convert[3].get('Authorization', '').startswith('Bearer '))
        blob = json.dumps(record, default=str)
        for word in ('DOS-001', 'Dossiers', 'DEMO', 'secret-essai'):
            self.assertNotIn(word, blob)

    def test_O2_reachability_failure_is_explained(self):
        office440.save_settings(self.desk, 'http://127.0.0.1:8080', 'secret-essai-' + 'z' * 20, 'http://host.docker.internal:8769/agent-courriel', 'euro-office')
        for answer, word in (({'error': -4}, 'ALLOW_PRIVATE_IP_ADDRESS'), ({'error': -8}, 'secret'), ({'error': -3}, 'conversion')):
            checks = office440.diagnostic(self.desk, self.auth, probe=self._fake_probe(answer, []), reach=True)
            step = [c for c in checks if c['name'] == 'Le serveur de documents joint AxiorHub'][0]
            self.assertFalse(step['ok'], answer)
            self.assertIn(word, step['detail'] + step['fix'], answer)

    def test_O2_read_only_diagnostic_writes_nothing(self):
        office440.save_settings(self.desk, 'http://127.0.0.1:8080', '', '', 'euro-office')
        key = Path(self.f.c['state_dir']) / 'office440.key'
        if key.exists():
            key.unlink()
        record = []
        office440.diagnostic(self.desk, self.auth, probe=self._fake_probe({'endConvert': True}, record))
        self.assertFalse(key.exists())
        self.assertFalse(any('/ConvertService.ashx' in r[0] for r in record))

    def test_O2_settings_page_says_preview_without_server_and_links_the_guide(self):
        from agent import web440
        html = web440.settings_page(self.desk, {**self.auth, 'csrf': 'x'}, '/agent-courriel', {}, render=lambda t, b, *a: b)
        self.assertIn('Sans serveur de documents', html)
        self.assertIn('aperçu en lecture seule', html)
        self.assertIn('INSTALLATION-EDITEUR-DOCUMENTS.md', html)
        self.assertIn('Tester la connexion', html)
        self.assertIn('http://host.docker.internal:8769/agent-courriel', html)   # poste : adresse de rappel proposée
        server = web440.settings_page(self.desk, {'origin': 'https://cabinet.example.test', 'prefix': '/agent-courriel', 'csrf': 'x'},
                                      '/agent-courriel', {}, render=lambda t, b, *a: b)
        self.assertNotIn('host.docker.internal', server)
        js = (ROOT / 'agent' / 'static' / 'v440.js').read_text(encoding='utf-8')
        self.assertIn("api('settings/office/test', {})", js)


class GuideTests(unittest.TestCase):
    """O1 : le guide pas à pas existe, en deux langues, et il est lié."""

    FR = ROOT / 'docs' / 'INSTALLATION-EDITEUR-DOCUMENTS.md'
    EN = ROOT / 'docs' / 'INSTALLATION-EDITEUR-DOCUMENTS.en.md'

    def test_O1_both_guides_cover_the_three_installations_and_the_two_engines(self):
        for path in (self.FR, self.EN):
            text = path.read_text(encoding='utf-8')
            for needle in ('ghcr.io/euro-office/documentserver', 'onlyoffice/documentserver', 'openssl rand -hex 32', 'JWT_ENABLED=true',
                           'JWT_SECRET', '127.0.0.1:8080:80', 'http://127.0.0.1:8080', 'http://host.docker.internal:8769/agent-courriel',
                           'host-gateway', 'systemd-socket-proxyd', 'ALLOW_PRIVATE_IP_ADDRESS=true', 'Certbot', 'ProxyPass',
                           'Windows 11', 'Ubuntu 24.04', 'AGPL', 'docker stop', 'docker rm', 'sha256:', 'WSL'):
                self.assertIn(needle, text, (path.name, needle))
            self.assertNotRegex(text, r'\b(?:10\.\d+\.\d+\.\d+|192\.168\.\d+\.\d+|172\.(?:1[6-9]|2\d|3[01])\.\d+\.\d+)\b')
            self.assertNotRegex(text, r'JWT_SECRET=[0-9a-f]{32,}')   # jamais un vrai secret dans le guide
        fr = self.FR.read_text(encoding='utf-8')
        for needle in ('lecture seule', 'Tester la connexion', 'En cas de problème', 'selon la documentation de l'):
            self.assertIn(needle, fr)
        self.assertIn('read-only', self.EN.read_text(encoding='utf-8'))

    def test_O1_linked_from_install_guides_and_in_app_guide(self):
        for name in ('INSTALLATION-SERVEUR.md', 'INSTALLATION-WINDOWS.md'):
            self.assertIn('INSTALLATION-EDITEUR-DOCUMENTS.md', (ROOT / 'docs' / name).read_text(encoding='utf-8'), name)
        from agent import guide5631
        self.assertTrue(any('INSTALLATION-EDITEUR-DOCUMENTS.md' in url for _, url in guide5631.LINKS['stockage']))
        self.assertTrue(office440.guide_url().endswith('/docs/INSTALLATION-EDITEUR-DOCUMENTS.md'))


if __name__ == '__main__':
    unittest.main()
