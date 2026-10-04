"""Document editor integration: tokens, JWT, callback saving, conflicts."""
import hashlib
import io
import json
import time
import unittest
import zipfile

from agent.common import Stop
from agent.desk import Desk
from agent import office440 as office
import test_agent as fixtures

AUTH = {'origin': 'https://courriel.example.test', 'prefix': '/agent-courriel', 'csrf': 'x'}


def docx(text):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as z:
        z.writestr('[Content_Types].xml', '<Types/>')
        z.writestr('word/document.xml', '<w:document>' + text + '</w:document>')
    return buf.getvalue()


class FakeNextcloud:
    def __init__(self):
        self.files = {}
        self.etags = {}
        self.counter = 0
        self.puts = []

    def add(self, path, data):
        self.counter += 1
        self.files[path] = data
        self.etags[path] = '"e%d"' % self.counter

    def stat(self, path):
        if path not in self.files:
            raise Stop('fichier_nextcloud_introuvable')
        return {'path': path, 'etag': self.etags[path], 'size': len(self.files[path]), 'modified': '', 'fileid': '1'}

    def download(self, item):
        return self.files[item['path']]

    def replace_file(self, path, data, etag):
        if self.etags.get(path) != etag:
            raise Stop('version_nextcloud_modifiee')
        self.add(path, data)
        self.puts.append(('replace', path))

    def put_file(self, path, data, content_type=''):
        if path in self.files:
            raise Stop('http_412')
        self.add(path, data)
        self.puts.append(('create', path))


class OfficeTests(unittest.TestCase):
    PATH = '/Dossiers/DEMO/Projet conclusions.docx'

    def setUp(self):
        self.f = fixtures.EngineTests('test_observation_has_no_mail_write')
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.desk = Desk(self.f.c)
        self.nc = FakeNextcloud()
        self.nc.add(self.PATH, docx('version 1'))
        office.save_settings(self.desk, 'https://myoffice.example.test/welcome/', 'secret-jwt-123456')

    def opened(self):
        return office.open_document(self.desk, AUTH, self.PATH, client=self.nc)

    def callback_for(self, opened, status=2, url='https://myoffice.example.test/cache/files/x.docx', key=None):
        cfg = opened['config']
        token = cfg['editorConfig']['callbackUrl'].rsplit('/', 1)[1]
        body = {'key': key or cfg['document']['key'], 'status': status, 'url': url}
        auth = 'Bearer ' + office.jwt_encode({'payload': body}, 'secret-jwt-123456')
        return token, auth, json.dumps(body).encode()

    def test_settings_normalise_welcome_url_and_require_https(self):
        self.assertEqual(office.settings(self.desk)['server_url'], 'https://myoffice.example.test')
        with self.assertRaises(Stop):
            office.save_settings(self.desk, 'http://myoffice.example.test')
        with self.assertRaises(Stop):
            office.save_settings(self.desk, 'https://user:pw@myoffice.example.test')

    def test_open_builds_signed_config(self):
        opened = self.opened()
        cfg = opened['config']
        self.assertEqual(cfg['documentType'], 'word')
        self.assertTrue(cfg['document']['url'].startswith('https://courriel.example.test/agent-courriel/office/file/'))
        self.assertTrue(cfg['editorConfig']['callbackUrl'].startswith('https://courriel.example.test/agent-courriel/office/callback/'))
        claims = office.jwt_decode(cfg['token'], 'secret-jwt-123456')
        self.assertEqual(claims['document']['key'], cfg['document']['key'])
        # a new ETag means a new Document Server key
        self.nc.add(self.PATH, docx('autre'))
        self.assertNotEqual(self.opened()['config']['document']['key'], cfg['document']['key'])

    def test_unsupported_and_out_of_scope_paths(self):
        with self.assertRaises(Stop):
            office.open_document(self.desk, AUTH, '/Dossiers/x.exe', client=self.nc)
        with self.assertRaises(Stop):
            office.open_document(self.desk, AUTH, '/Dossiers/../etc/passwd.docx', client=self.nc)

    def test_file_route_requires_valid_token(self):
        opened = self.opened()
        token = opened['config']['document']['url'].rsplit('/', 1)[1]
        data, name = office.serve_file(self.desk, token, client=self.nc)
        self.assertEqual(data, self.nc.files[self.PATH])
        self.assertEqual(name, 'Projet conclusions.docx')
        for bad in (token[:-2] + 'aa', 'x.y', token.replace('.', 'x')):
            with self.assertRaises(Stop):
                office.serve_file(self.desk, bad, client=self.nc)
        # a callback token cannot download the file
        cb = opened['config']['editorConfig']['callbackUrl'].rsplit('/', 1)[1]
        with self.assertRaises(Stop):
            office.serve_file(self.desk, cb, client=self.nc)

    def test_expired_token(self):
        token = office.sign(self.desk, {'t': 'file', 'k': 'k', 'p': self.PATH}, ttl=-5)
        with self.assertRaises(Stop):
            office.verify(self.desk, token, 'file')

    def test_callback_replaces_file_when_unchanged(self):
        opened = self.opened()
        token, auth, body = self.callback_for(opened)
        edited = docx('version 2')
        result = office.handle_callback(self.desk, token, auth, body, client=self.nc, fetcher=lambda d, u: edited)
        self.assertEqual(result, {'error': 0})
        self.assertEqual(self.nc.files[self.PATH], edited)
        self.assertEqual(self.nc.puts, [('replace', self.PATH)])
        # same content posted again (forcesave then close) creates nothing new
        office.handle_callback(self.desk, token, auth, body, client=self.nc, fetcher=lambda d, u: edited)
        self.assertEqual(len(self.nc.puts), 1)
        # later edit in the same session still replaces (ETag was refreshed)
        later = docx('version 3')
        office.handle_callback(self.desk, token, auth, body, client=self.nc, fetcher=lambda d, u: later)
        self.assertEqual(self.nc.files[self.PATH], later)

    def test_conflict_creates_copy_and_never_overwrites(self):
        opened = self.opened()
        self.nc.add(self.PATH, docx('modifié ailleurs'))
        token, auth, body = self.callback_for(opened)
        office.handle_callback(self.desk, token, auth, body, client=self.nc, fetcher=lambda d, u: docx('mes modifs'))
        self.assertEqual(self.nc.files[self.PATH], docx('modifié ailleurs'))
        copies = [p for p in self.nc.files if '(modifié' in p]
        self.assertEqual(len(copies), 1)
        self.assertEqual(self.nc.files[copies[0]], docx('mes modifs'))
        row = self.desk.db.execute('SELECT outcome FROM office_saves440').fetchone()
        self.assertEqual(row['outcome'], 'copy')

    def test_callback_security(self):
        opened = self.opened()
        token, auth, body = self.callback_for(opened)
        with self.assertRaises(Stop):  # missing JWT
            office.handle_callback(self.desk, token, '', body, client=self.nc, fetcher=lambda d, u: docx('x'))
        forged = 'Bearer ' + office.jwt_encode({'payload': json.loads(body)}, 'mauvais-secret-000')
        with self.assertRaises(Stop):
            office.handle_callback(self.desk, token, forged, body, client=self.nc, fetcher=lambda d, u: docx('x'))
        wrong_key = self.callback_for(opened, key='ffff')
        with self.assertRaises(Stop):
            office.handle_callback(self.desk, *wrong_key, client=self.nc, fetcher=lambda d, u: docx('x'))
        with self.assertRaises(Stop):  # content that is not a zip container
            office.handle_callback(self.desk, token, auth, body, client=self.nc, fetcher=lambda d, u: b'MZ not a docx')
        self.assertEqual(self.nc.files[self.PATH], docx('version 1'))
        # status 1 (editing) and 4 (closed without change) only acknowledge
        for status in (1, 4):
            t, a, b = self.callback_for(opened, status=status, url='')
            self.assertEqual(office.handle_callback(self.desk, t, a, b, client=self.nc), {'error': 0})
        self.assertEqual(self.nc.puts, [])

    def test_download_url_must_belong_to_the_document_server(self):
        with self.assertRaises(Stop):
            office._fetch_edited(self.desk, 'https://evil.example.test/x.docx')
        with self.assertRaises(Stop):
            office._fetch_edited(self.desk, 'http://myoffice.example.test/x.docx')

    def test_without_jwt_secret_body_is_accepted_for_valid_token(self):
        office.clear_secret(self.desk)
        opened = self.opened()
        self.assertNotIn('token', opened['config'])
        cfg = opened['config']
        token = cfg['editorConfig']['callbackUrl'].rsplit('/', 1)[1]
        body = json.dumps({'key': cfg['document']['key'], 'status': 2, 'url': 'https://myoffice.example.test/f'}).encode()
        office.handle_callback(self.desk, token, '', body, client=self.nc, fetcher=lambda d, u: docx('v2'))
        self.assertEqual(self.nc.files[self.PATH], docx('v2'))

    def test_diagnostic_detects_frame_ancestors_and_bad_secret(self):
        def probe(url, method='GET', body=None, headers=None, timeout=12):
            if url.endswith('/healthcheck'):
                return 200, {}, b'true'
            if url.endswith('api.js'):
                return 200, {}, b'var DocsAPI = {};'
            if url.endswith('/hosting/discovery'):
                return 200, {}, b'<wopi-discovery><net-zone><app name="Word"><action name="edit"/></app></net-zone></wopi-discovery>'
            if url.endswith('/command'):
                return 200, {}, b'{"error":6}'
            return 200, {'Content-Security-Policy': "frame-ancestors 'self' https://autre.example.test"}, b''
        checks = {c['name']: c for c in office.diagnostic(self.desk, AUTH, probe=probe)}
        self.assertTrue(checks['Serveur joignable (healthcheck)']['ok'])
        self.assertFalse(checks['Intégration dans cette page (iframe)']['ok'])
        self.assertIn('frame-ancestors', checks['Intégration dans cette page (iframe)']['fix'])
        self.assertFalse(checks['Secret JWT et API de commande']['ok'])

    def test_tokens_and_secret_files_are_private(self):
        self.opened()
        for name in ('office440.key', 'office440.jwt'):
            mode = (office.Path(self.desk.c['state_dir']) / name).stat().st_mode & 0o777
            self.assertEqual(mode, 0o600)


if __name__ == '__main__':
    unittest.main()
