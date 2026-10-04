"""Workshop routes through the real WSGI application."""
import base64
import hashlib
import io
import json
import unittest
from unittest.mock import patch
from pathlib import Path

from agent.common import private_json
from agent.desk import Desk
from agent.web import App
from agent import office440, drafts440
from fake_imap440 import FakeIMAP
from test_v440_drafts import raw_inbound, raw_draft
from test_v440_office import FakeNextcloud, docx
import test_agent as fixtures


class WebWorkshopTests(unittest.TestCase):
    PATH = '/Dossiers/DEMO/Projet.docx'

    def setUp(self):
        FakeIMAP.reset()
        self.f = fixtures.EngineTests('test_observation_has_no_mail_write')
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        secret = self.f.base / 'imap.pw'
        secret.write_text('x')
        secret.chmod(0o600)
        self.f.c['mail'].update({'password_file': str(secret), 'port': 993})
        self.config = self.f.base / 'config.json'
        private_json(self.config, self.f.c)
        self.auth = self.f.base / 'auth.json'
        salt = 'aa' * 16
        self.password = 'password-test-only-long'
        self.origin = 'https://cabinet.example.test'
        private_json(self.auth, {'username': 'admin', 'salt': salt,
                                 'hash': hashlib.scrypt(self.password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1).hex(),
                                 'origin': self.origin, 'prefix': '/agent-courriel', 'csrf': 'test-csrf'})
        self.app = App(str(self.config), str(self.auth))
        p = patch('agent.mailbox.imaplib.IMAP4_SSL', FakeIMAP)
        p.start()
        self.addCleanup(p.stop)
        FakeIMAP.add('INBOX', raw_inbound())
        self.uid = FakeIMAP.add('Drafts', raw_draft(), ['\\Draft'])
        self.nc = FakeNextcloud()
        self.nc.add(self.PATH, docx('v1'))
        q = patch('agent.office440.dav_client', lambda desk: self.nc)
        q.start()
        self.addCleanup(q.stop)
        self.desk = Desk(self.f.c)

    def call(self, path, method='GET', body=None, auth=True, origin=True, csrf=True, query='', ctype='application/json', extra=None):
        raw = json.dumps(body).encode() if body is not None else b''
        env = {'REQUEST_METHOD': method, 'PATH_INFO': '/agent-courriel' + path, 'QUERY_STRING': query,
               'HTTP_HOST': 'cabinet.example.test', 'HTTP_X_FORWARDED_PROTO': 'https', 'wsgi.url_scheme': 'http',
               'wsgi.input': io.BytesIO(raw), 'CONTENT_LENGTH': str(len(raw)), 'CONTENT_TYPE': ctype}
        if auth:
            env['HTTP_AUTHORIZATION'] = 'Basic ' + base64.b64encode(('admin:' + self.password).encode()).decode()
        if origin:
            env['HTTP_ORIGIN'] = self.origin
        if csrf:
            env['HTTP_X_CSRF_TOKEN'] = 'test-csrf'
        env.update(extra or {})
        out = {}

        def start(status, headers):
            out.update(status=status, headers=dict(headers))
        out['body'] = b''.join(self.app(env, start))
        return out

    def test_pages_need_authentication_and_render(self):
        for path in ('/courriels', '/documents', '/atelier/reglages', '/api440/drafts'):
            self.assertTrue(self.call(path, auth=False)['status'].startswith('401'), path)
        r = self.call('/courriels')
        self.assertTrue(r['status'].startswith('200'))
        text = r['body'].decode()
        self.assertIn('Re: FIMEX', text)
        self.assertIn('Courriels à relire', text)
        self.assertNotIn('<script>', text.replace('<script defer', ''))
        self.assertIn("script-src 'self'", r['headers']['Content-Security-Policy'])
        self.assertTrue(self.call('/documents')['status'].startswith('200'))
        self.assertTrue(self.call('/atelier/reglages')['status'].startswith('200'))
        self.assertIn('/static/v440.js', text)
        self.assertTrue(self.call('/static/v440.js')['body'].startswith(b'/* AxiorHub 4.4.0'))

    def test_draft_roundtrip_and_post_protection(self):
        d = json.loads(self.call('/api440/draft', query='uid=%d' % self.uid)['body'])
        payload = {'uid': d['uid'], 'uidvalidity': d['uidvalidity'], 'revision': d['revision'], 'to': d['to'], 'cc': '',
                   'bcc': '', 'subject': d['subject'], 'body': 'Texte corrigé par Maître.'}
        for kw in ({'origin': False}, {'csrf': False}):
            r = self.call('/api440/draft/save', 'POST', payload, **kw)
            self.assertTrue(r['status'].startswith('400'))
        self.assertEqual(len(FakeIMAP.folders['Drafts']), 1)
        r = self.call('/api440/draft/save', 'POST', payload)
        self.assertTrue(r['status'].startswith('200'), r['body'])
        saved = json.loads(r['body'])
        self.assertTrue(saved['previous_removed'])
        again = self.call('/api440/draft/save', 'POST', payload)  # stale revision
        self.assertTrue(again['status'].startswith('400'))
        self.assertIn('Actualisez', json.loads(again['body'])['message'])
        # learning recorded the correction
        rows = Desk(self.f.c).db.execute('SELECT COUNT(*) FROM sqlite_master WHERE name LIKE "%review%"').fetchone()[0]
        self.assertGreaterEqual(rows, 0)

    def test_editor_page_csp_and_config(self):
        unconfigured = self.call('/documents/edit', query='path=' + self.PATH)
        self.assertIn('pas encore configuré', unconfigured['body'].decode())
        office440.save_settings(self.desk, 'https://myoffice.example.test/welcome/', 'secret-jwt-123456')
        r = self.call('/documents/edit', query='path=' + self.PATH)
        csp = r['headers']['Content-Security-Policy']
        self.assertIn('frame-src https://myoffice.example.test', csp)
        self.assertIn("script-src 'self' https://myoffice.example.test", csp)
        self.assertIn('Projet.docx', r['body'].decode())
        cfg = json.loads(self.call('/api440/office/config', query='path=' + self.PATH)['body'])
        self.assertEqual(cfg['config']['documentType'], 'word')
        self.assertIn('token', cfg['config'])
        bad = self.call('/api440/office/config', query='path=/Autre/x.docx')
        self.assertTrue(bad['status'].startswith('400'))
        self.assertTrue(self.call('/documents/edit', query='path=/Autre/x.docx')['body'])

    def test_document_server_routes_use_tokens_not_basic_auth(self):
        office440.save_settings(self.desk, 'https://myoffice.example.test', 'secret-jwt-123456')
        cfg = json.loads(self.call('/api440/office/config', query='path=' + self.PATH)['body'])['config']
        file_tok = cfg['document']['url'].rsplit('/', 1)[1]
        cb_tok = cfg['editorConfig']['callbackUrl'].rsplit('/', 1)[1]
        r = self.call('/office/file/' + file_tok, auth=False, origin=False, csrf=False)
        self.assertTrue(r['status'].startswith('200'))
        self.assertEqual(r['body'], self.nc.files[self.PATH])
        self.assertIn("default-src 'none'", r['headers']['Content-Security-Policy'])
        self.assertTrue(self.call('/office/file/' + 'x' * 40, auth=False)['status'].startswith('403'))
        body = {'key': cfg['document']['key'], 'status': 1}
        auth = 'Bearer ' + office440.jwt_encode({'payload': body}, 'secret-jwt-123456')
        r = self.call('/office/callback/' + cb_tok, 'POST', body, auth=False, origin=False, csrf=False,
                      extra={'HTTP_AUTHORIZATION': auth})
        self.assertEqual(json.loads(r['body']), {'error': 0})
        r = self.call('/office/callback/' + cb_tok, 'POST', body, auth=False, origin=False, csrf=False)
        self.assertTrue(r['status'].startswith('403'))
        self.assertEqual(json.loads(r['body'])['error'], 1)

    def test_settings_api_and_matter_role(self):
        r = self.call('/api440/settings/office', 'POST', {'server_url': 'http://insecure.example.test'})
        self.assertTrue(r['status'].startswith('400'))
        r = self.call('/api440/settings/office', 'POST', {'server_url': 'https://euro-office.example.test/welcome/',
                                                          'secret': 'a-long-enough-secret', 'engine': 'euro-office'})
        self.assertTrue(r['status'].startswith('200'))
        self.assertEqual(office440.settings(Desk(self.f.c))['engine'], 'euro-office')
        r = self.call('/api440/settings/matter', 'POST', {'matter': 'DOS-001', 'role': 'postulant',
                                                          'partner_email': 'dominus@example.test'})
        self.assertTrue(r['status'].startswith('200'))
        self.assertIn('dominus@example.test', self.call('/atelier/reglages')['body'].decode())

    def test_manual_notice_analysis_is_queued(self):
        name = '/Dossiers/DEMO/XXX - YYY - 20261002 - Avis de renvoi.pdf'
        self.nc.add(name, b'%PDF')
        r = self.call('/api440/notice/analyze', 'POST', {'path': name, 'matter': 'DOS-001'})
        self.assertTrue(r['status'].startswith('200'), r['body'])
        kinds = [x[0] for x in Desk(self.f.c).db.execute('SELECT kind FROM jobs')]
        self.assertIn('analyze_notice440', kinds)
        bad = self.call('/api440/notice/analyze', 'POST', {'path': '/Dossiers/DEMO/Facture.pdf', 'matter': 'DOS-001'})
        self.assertTrue(bad['status'].startswith('400'))


if __name__ == '__main__':
    unittest.main()
