"""5.6.37 : application Nextcloud 0.4.0 — interface AxiorHub affichée dans Nextcloud, instruction au Pilote sur un serveur.

Données d'essai fictives (domaines example.test).
"""
import base64
from datetime import date, datetime, timedelta, timezone
import io
import json
import os
from pathlib import Path
import re
import sqlite3
import tempfile
import time
import unittest
import unittest.mock

from agent.common import Stop
import test_v5634 as t34

ROOT = Path(__file__).resolve().parents[1]
NC = 'https://nuage.example.test'


def _users(directory, rows):
    """Comptes du cabinet fictifs (distribution autonome) : (id, courriel, actif, rôle, version des autorisations)."""
    db = sqlite3.connect(str(Path(directory) / 'users.sqlite3'))
    db.execute('CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, email TEXT UNIQUE, password_hash TEXT, salt TEXT, active INTEGER, created TEXT, '
               "role TEXT NOT NULL DEFAULT 'avocat', authz INTEGER NOT NULL DEFAULT 1)")
    for ident, email, active, role, authz in rows:
        db.execute('INSERT OR REPLACE INTO users(id,email,password_hash,salt,active,created,role,authz) VALUES(?,?,?,?,?,?,?,?)',
                   (ident, email, '', '00', active, '2026-10-12', role, authz))
    db.commit()
    db.close()


class _Linked(t34.Base):
    """Compte Nextcloud relié (clé personnelle) avec le compte du serveur de la recette."""

    def setUp(self):
        super().setUp()
        self.server = json.loads(Path(self.auth).read_text(encoding='utf-8'))
        self.n = 0
        empty = tempfile.TemporaryDirectory()
        self.addCleanup(empty.cleanup)
        p = unittest.mock.patch.dict(os.environ, {'AXIORHUB_AUTH_STATE': empty.name})   # installation serveur : pas de comptes propres
        p.start()
        self.addCleanup(p.stop)
        from agent import integration5636
        integration5636.set_enabled(self.desk, True)

    def pair(self):
        from agent.integration5634 import handle
        status, key = handle(self.desk, 'POST', '/integration/v1/pair', {'axiorhub.auth': self.server, 'REMOTE_ADDR': '192.0.2.20'},
                             json.dumps({'login': 'admin', 'password': self.password, 'label': 'alice'}).encode())
        self.assertEqual(status, '200 OK', key)
        return key

    def signed(self, key, method, path, body=None):
        from agent.integration5634 import handle, sign
        self.n += 1
        ctx = {'instance': 'personnel:' + key['user'], 'user': 'alice', 'scope': [], 'op': method + ' ' + path,
               'exp': (datetime.now(timezone.utc) + timedelta(minutes=2)).isoformat(), 'nonce': 'n%031d' % self.n}
        env = {'HTTP_X_AXIORHUB_CONTEXT': sign(key['secret'], ctx), 'HTTP_X_AXIORHUB_KEY': key['key_id'], 'axiorhub.auth': self.server}
        return handle(self.desk, method, path, env, json.dumps(body).encode() if body is not None else b'')

    def session_url(self, key, suite='/dossiers', origin=NC):
        status, out = self.signed(key, 'POST', '/integration/v1/session', {'suite': suite, 'origin': origin})
        self.assertEqual(status, '200 OK', out)
        return out

    def get(self, path, query='', cookie='', dest='', auth=False):
        r = {}
        env = {'REQUEST_METHOD': 'GET', 'PATH_INFO': path, 'QUERY_STRING': query, 'HTTP_HOST': 'cabinet.example.test',
               'HTTP_X_FORWARDED_PROTO': 'https', 'wsgi.url_scheme': 'http', 'wsgi.input': io.BytesIO(b''), 'CONTENT_LENGTH': '0'}
        if cookie:
            env['HTTP_COOKIE'] = cookie
        if dest:
            env['HTTP_SEC_FETCH_DEST'] = dest
        if auth:
            env['HTTP_AUTHORIZATION'] = 'Basic ' + base64.b64encode(('admin:' + self.password).encode()).decode()

        def start(status, headers):
            r.update(status=status, headers=dict(headers), raw_headers=headers)
        r['body'] = b''.join(self.app(env, start)).decode()
        return r


class MissionOwner(_Linked):
    def test_owner_is_the_cabinet_on_a_server_and_the_account_in_standalone_mode(self):
        from agent import missions567
        self.assertEqual(missions567.owner_for('admin'), 'cabinet')
        with tempfile.TemporaryDirectory() as d:
            sqlite3.connect(str(Path(d) / 'users.sqlite3')).close()
            with unittest.mock.patch.dict(os.environ, {'AXIORHUB_AUTH_STATE': d}):
                self.assertEqual(missions567.owner_for('avocat@example.test'), 'avocat@example.test')
                self.assertEqual(missions567.owner_for(''), 'cabinet')

    def test_instruction_from_nextcloud_passes_the_mission_authority_check(self):
        """Anomalie 0.3.0 : « autorisation_mission_non_verifiable » sur un serveur (compte relié = compte du serveur)."""
        from agent import missions567
        key = self.pair()
        created = {}
        real = missions567.create

        def spy(desk, data, owner='cabinet'):
            created['owner'] = owner
            return real(desk, data, owner)
        with unittest.mock.patch('agent.missions567.create', side_effect=spy):
            status, job = self.signed(key, 'POST', '/integration/v1/jobs', {'operation': 'instruction', 'matter': t34.MID,
                                                                            'question': 'Quelle est la date de l’audience fictive ?', 'idempotency_key': 'cle-5637-1'})
        self.assertEqual(status, '202 Accepted', job)
        self.assertEqual(created['owner'], 'cabinet')
        row = self.desk.db.execute("SELECT mission FROM integration_jobs5634 WHERE id=?", (job['job_id'],)).fetchone()
        self.assertTrue(row['mission'])
        missions567.check_authority(self.desk, {'mission_id': row['mission']})   # ne lève plus
        status, st = self.signed(key, 'GET', '/integration/v1/jobs/' + job['job_id'])
        self.assertEqual(status, '200 OK')
        self.assertNotEqual(st['errors'][:1], [{'code': 'mission_absente', 'message': 'Mission introuvable.'}])


class NextcloudSession(_Linked):
    def test_contract_is_1_3(self):
        from agent.integration5634 import CONTRACT
        self.assertEqual(CONTRACT, '1.3')

    def test_one_time_link_opens_the_interface_in_the_nextcloud_frame(self):
        key = self.pair()
        out = self.session_url(key)
        self.assertTrue(out['url'].startswith('https://cabinet.example.test/agent-courriel/nextcloud-session?jeton='), out)
        self.assertEqual((out['base'], out['frame_origin'], out['standalone']), ('https://cabinet.example.test/agent-courriel', 'https://cabinet.example.test', False))
        token = re.search(r'jeton=([^&]+)', out['url']).group(1)
        stored = [dict(r) for r in self.desk.db.execute('SELECT * FROM integration_sessions5637')]
        self.assertEqual(len(stored), 1)
        self.assertNotIn(token, json.dumps(stored))                                         # seule l'empreinte est conservée
        query = out['url'].split('?', 1)[1]
        r = self.get('/nextcloud-session', query)
        self.assertEqual(r['status'], '303 See Other', r['body'])
        self.assertEqual(r['headers']['Location'], '/agent-courriel/dossiers')
        cookie = r['headers']['Set-Cookie']
        for part in ('axh_nc=', 'Path=/agent-courriel', 'HttpOnly', 'Secure', 'SameSite=None', 'Partitioned', 'Max-Age=43200'):
            self.assertIn(part, cookie)
        self.assertEqual(self.get('/nextcloud-session', query)['status'], '403 Forbidden')   # usage unique
        sid = cookie.split(';', 1)[0]
        page = self.get('/dossiers', cookie=sid, dest='iframe')
        self.assertEqual(page['status'], '200 OK')
        csp = page['headers']['Content-Security-Policy']
        self.assertIn("frame-ancestors 'self' " + NC, csp)
        self.assertNotIn("frame-ancestors 'none'", csp)
        self.assertNotIn('X-Frame-Options', page['headers'])
        self.assertRegex(page['body'], r'/static/v5637\.js\?v=[0-9.]+"></script>')   # 5.6.80 : adresses versionnées
        basic = self.get('/dossiers', auth=True)                                            # connexion par mot de passe : jamais dans un cadre
        self.assertEqual(basic['headers']['X-Frame-Options'], 'DENY')
        self.assertIn("frame-ancestors 'none'", basic['headers']['Content-Security-Policy'])

    def test_session_falls_with_the_key_or_the_authorisation(self):
        from agent import integration5636
        key = self.pair()
        r = self.get('/nextcloud-session', self.session_url(key)['url'].split('?', 1)[1])
        sid = r['headers']['Set-Cookie'].split(';', 1)[0]
        self.assertEqual(self.get('/aujourdhui', cookie=sid)['status'], '200 OK')
        integration5636.revoke(self.desk, key['key_id'])
        self.app.nc_cache.clear()                                                            # sinon au plus 30 s de mémoire
        self.assertIn(self.get('/aujourdhui', cookie=sid)['status'], ('401 Unauthorized', '303 See Other'))   # 5.6.48 : page de connexion
        self.assertEqual(self.desk.db.execute('SELECT COUNT(*) FROM integration_sessions5637').fetchone()[0], 0)
        key = self.pair()
        r = self.get('/nextcloud-session', self.session_url(key)['url'].split('?', 1)[1])
        sid = r['headers']['Set-Cookie'].split(';', 1)[0]
        integration5636.set_enabled(self.desk, False)
        self.app.nc_cache.clear()
        self.assertIn(self.get('/aujourdhui', cookie=sid)['status'], ('401 Unauthorized', '303 See Other'))   # 5.6.48 : page de connexion

    def test_unpair_ends_the_session(self):
        key = self.pair()
        r = self.get('/nextcloud-session', self.session_url(key)['url'].split('?', 1)[1])
        sid = r['headers']['Set-Cookie'].split(';', 1)[0]
        self.assertEqual(self.signed(key, 'POST', '/integration/v1/unpair', {})[1], {'unpaired': True})
        self.app.nc_cache.clear()
        self.assertIn(self.get('/aujourdhui', cookie=sid)['status'], ('401 Unauthorized', '303 See Other'))   # 5.6.48 : page de connexion

    def test_expired_link_is_refused(self):
        key = self.pair()
        query = self.session_url(key)['url'].split('?', 1)[1]
        self.desk.db.execute("UPDATE integration_sessions5637 SET expires=?", (time.time() - 1,))
        self.desk.db.commit()
        r = self.get('/nextcloud-session', query)
        self.assertEqual(r['status'], '403 Forbidden')
        self.assertNotIn('Set-Cookie', r['headers'])
        self.assertIn('axiorhub-integre', r['body'])
        self.assertNotIn('X-Frame-Options', r['headers'])                                   # page sans donnée : affichée dans le cadre

    def test_landing_page_and_origin_are_controlled(self):
        key = self.pair()
        for suite in ('//autre.example.test/x', 'https://autre.example.test', '/integration/v1/me', '\\\\x'):
            out = self.session_url(key, suite=suite)
            self.assertIn('suite=%2Faujourdhui', out['url'])
        for origin in ('https://nuage.example.test/chemin', 'javascript:alert(1)', 'https://*.example.test', 'nuage.example.test'):
            status, out = self.signed(key, 'POST', '/integration/v1/session', {'origin': origin})
            self.assertEqual((status, out['code']), ('400 Bad Request', 'origine_nextcloud_invalide'))
        r = self.get('/nextcloud-session', self.session_url(key, origin='')['url'].split('?', 1)[1])
        sid = r['headers']['Set-Cookie'].split(';', 1)[0]
        csp = self.get('/aujourdhui', cookie=sid)['headers']['Content-Security-Policy']
        self.assertIn("frame-ancestors 'self'", csp)                                       # sans origine déclarée : AxiorHub seul
        self.assertNotIn('https://nuage', csp)

    def test_instance_secret_and_assistant_role_get_no_session(self):
        from agent.integration5634 import handle, sign
        key = self.pair()
        with tempfile.TemporaryDirectory() as d:   # rôle ACTUEL du compte (comptes du cabinet) : assistant
            _users(d, [(1, 'admin', 1, 'assistant', 1)])
            with unittest.mock.patch.dict(os.environ, {'AXIORHUB_AUTH_STATE': d}):
                status, out = self.signed(key, 'POST', '/integration/v1/session', {'origin': NC})
        self.assertEqual((status, out['code']), ('403 Forbidden', 'role_insuffisant'))
        from agent.nextcloud5637 import create
        from agent.integration5634 import Refused
        with self.assertRaises(Refused) as ex:
            create(self.desk, {'_owner': 'cabinet', '_role': 'administrateur'}, {}, self.server)
        self.assertEqual(str(ex.exception), 'cle_personnelle_requise')
        self.assertTrue(sign)
        self.assertTrue(handle)

    def test_frame_without_session_asks_nextcloud_for_a_new_one(self):
        r = self.get('/aujourdhui', cookie='axh_nc=inconnu', dest='iframe')
        self.assertEqual(r['status'], '401 Unauthorized')
        self.assertNotIn('WWW-Authenticate', r['headers'])                                   # jamais de fenêtre de mot de passe dans le cadre
        self.assertIn('content="bloque"', r['body'])
        self.assertIn("frame-ancestors *", r['headers']['Content-Security-Policy'])
        self.assertNotIn('X-Frame-Options', r['headers'])
        js = self.get('/nextcloud-session/signal.js')
        self.assertEqual(js['status'], '200 OK')
        self.assertIn("postMessage({axiorhub:'session'", js['body'])
        self.assertIn('/connexion', self.get('/aujourdhui')['headers']['Location'])          # navigation directe : page de connexion (5.6.48)

    def test_standalone_distribution_lets_the_account_layer_log_in(self):
        key = self.pair()
        with tempfile.TemporaryDirectory() as d:
            _users(d, [(1, 'admin', 1, 'administrateur', 1)])
            with unittest.mock.patch.dict(os.environ, {'AXIORHUB_AUTH_STATE': d}):
                out = self.session_url(key, suite='/corporate')
        self.assertEqual((out['url'], out['standalone']), ('https://cabinet.example.test/agent-courriel/corporate', True))


class EmbeddedLook(_Linked):
    def test_single_menu_in_nextcloud_and_all_functions_page(self):
        css = (ROOT / 'agent' / 'static' / 'v5637.css').read_text(encoding='utf-8')
        self.assertIn('.ax-integre aside#ws-sidebar,.ax-integre #ws-sidebar-toggle{display:none!important}', css)   # un seul menu : Nextcloud
        page = self.get('/toutes-les-fonctions', auth=True)
        self.assertEqual(page['status'], '200 OK')
        self.assertIn('p5637-panel', page['body'])
        self.assertIn('href="/agent-courriel/corporate"', page['body'])
        self.assertIn('href="/agent-courriel/guide-utilisation"', page['body'])

    def test_static_files_and_sidebar_rail(self):
        js = (ROOT / 'agent' / 'static' / 'v5637.js').read_text(encoding='utf-8')
        self.assertIn("classList.add('ax-integre')", js)
        self.assertIn("axiorhub: 'page'", js)
        v320 = (ROOT / 'agent' / 'static' / 'v320.js').read_text(encoding='utf-8')
        self.assertIn("const storedSidebar = integre ? 'collapsed' : read(keySidebar);", v320)   # volet fermé à chaque page dans Nextcloud
        self.assertIn("if (!integre) save(keySidebar", v320)
        from agent.shell501 import BUNDLE
        self.assertIn('v5637.css', BUNDLE)
        self.assertIn('.ax-integre', (ROOT / 'agent' / 'static' / 'app520.css').read_text(encoding='utf-8'))
        from agent.web440 import STATIC
        self.assertIn('/static/v5637.js', STATIC)
        from agent.ui_helpers import enhance
        html = enhance('<html><head></head><body></body></html>', '/agent-courriel', 'x', False)
        self.assertRegex(html, r'<script defer src="/agent-courriel/static/v5637\.js\?v=[0-9.]+"></script>')   # 5.6.80 : adresses versionnées
        self.assertLess(html.index('v5637.js'), html.index('v320.js'))



# =============================================================================================== audit de la 5.6.36 (F01 à F16)
class AuditFacade(t34.Base):
    """F01 à F03 : liaison Nextcloud à travers la façade des comptes du cabinet (StandaloneAuth) puis l'API réelle du moteur."""

    PASSWORD = 'mot-de-passe-fictif-de-recette'

    def setUp(self):
        super().setUp()
        from agent.standalone_auth import StandaloneAuth
        from agent import integration5636
        self.state = tempfile.TemporaryDirectory()
        self.addCleanup(self.state.cleanup)
        p = unittest.mock.patch.dict(os.environ, {'AXIORHUB_AUTH_STATE': self.state.name, 'AXIORHUB_INTERNAL_API_TOKEN': 'jeton-interne-fictif'})
        p.start()
        self.addCleanup(p.stop)
        self.facade = StandaloneAuth(self.app, self.state.name, 'https://cabinet.example.test')
        self.addCleanup(lambda: self.facade.db.close())   # Windows : base fermée avant la suppression du dossier temporaire
        for email, role in (('associe@example.test', 'administrateur'), ('avocat@example.test', 'avocat'), ('assistant@example.test', 'assistant')):
            salt = '11' * 16
            self.facade.db.execute('INSERT INTO users(email,password_hash,salt,active,created,role) VALUES(?,?,?,?,?,?)',
                                   (email, self.facade._hash(self.PASSWORD, salt), salt, 1, '2026-10-12', role))
        self.facade.db.commit()
        integration5636.set_enabled(self.desk, True)
        self.n = 0

    def call(self, method, path, body=None, headers=None):
        raw = json.dumps(body).encode() if body is not None else b''
        env = {'REQUEST_METHOD': method, 'PATH_INFO': path, 'QUERY_STRING': '', 'HTTP_HOST': 'cabinet.example.test',
               'HTTP_X_FORWARDED_PROTO': 'https', 'wsgi.url_scheme': 'https', 'wsgi.input': io.BytesIO(raw), 'CONTENT_LENGTH': str(len(raw)),
               'CONTENT_TYPE': 'application/json', 'REMOTE_ADDR': '192.0.2.30', **(headers or {})}
        out = {}

        def start(status, hdrs, exc=None):
            out['status'] = status
        payload = b''.join(self.facade(env, start)).decode()
        return out['status'], json.loads(payload or '{}')

    def pair(self, email):
        status, key = self.call('POST', '/integration/v1/pair', {'login': email, 'password': self.PASSWORD, 'label': 'alice'})
        self.assertEqual(status, '200 OK', key)
        return key

    def signed(self, key, method, path, body=None, extra=None, secret=None):
        from agent.integration5634 import sign
        self.n += 1
        ctx = {'instance': 'personnel:' + key['user'], 'user': 'alice', 'scope': [], 'op': method + ' ' + path,
               'exp': (datetime.now(timezone.utc) + timedelta(minutes=2)).isoformat(), 'nonce': 'f%031d' % self.n}
        return self.call(method, path, body, {'HTTP_X_AXIORHUB_CONTEXT': sign(secret or key['secret'], ctx), 'HTTP_X_AXIORHUB_KEY': key['key_id'],
                                              **(extra or {})})

    def test_f01_signed_requests_cross_the_facade_and_spoofed_identity_is_ignored(self):
        key = self.pair('avocat@example.test')
        status, me = self.signed(key, 'GET', '/integration/v1/me')
        self.assertEqual((status, me['user'], me['role']), ('200 OK', 'avocat@example.test', 'avocat'))
        status, me = self.signed(key, 'GET', '/integration/v1/me', extra={'HTTP_X_AXIORHUB_USER': 'associe@example.test',
                                                                          'HTTP_X_AXIORHUB_ROLE': 'administrateur'})
        self.assertEqual((status, me['user'], me['role']), ('200 OK', 'avocat@example.test', 'avocat'))   # identité usurpée : ignorée
        status, out = self.signed(key, 'GET', '/integration/v1/matters')
        self.assertEqual(status, '200 OK')
        status, out = self.signed(key, 'GET', '/integration/v1/me', secret='mauvais-secret-fictif')
        self.assertEqual((status, out['code']), ('401 Unauthorized', 'signature_invalide'))
        self.assertEqual(self.signed(key, 'POST', '/integration/v1/unpair', {})[1], {'unpaired': True})
        self.assertEqual(self.signed(key, 'GET', '/integration/v1/me')[0], '401 Unauthorized')        # clé révoquée

    def test_f02_disabled_account_loses_its_key_and_reactivation_does_not_revive_it(self):
        key = self.pair('avocat@example.test')
        admin = self.facade.db.execute("SELECT * FROM users WHERE email='associe@example.test'").fetchone()
        target = self.facade.db.execute("SELECT id FROM users WHERE email='avocat@example.test'").fetchone()['id']
        self.facade._account_action(admin, {'op': 'disable', 'id': str(target)})
        status, out = self.signed(key, 'GET', '/integration/v1/me')
        self.assertEqual((status, out['code']), ('401 Unauthorized', 'compte_axiorhub_inactif_ou_modifie'))
        self.facade._account_action(admin, {'op': 'enable', 'id': str(target)})
        self.assertEqual(self.signed(key, 'GET', '/integration/v1/me')[0], '401 Unauthorized')        # relier de nouveau
        key2 = self.pair('avocat@example.test')
        self.assertEqual(self.signed(key2, 'GET', '/integration/v1/me')[0], '200 OK')
        self.facade._account_action(admin, {'op': 'role', 'id': str(target), 'role': 'assistant'})   # changement de rôle : immédiat
        self.assertEqual(self.signed(key2, 'GET', '/integration/v1/me')[0], '401 Unauthorized')

    def test_f03_assistant_cannot_decide_and_nothing_is_written(self):
        from agent.integration5634 import Refused, approve
        key = self.pair('assistant@example.test')
        status, me = self.signed(key, 'GET', '/integration/v1/me')
        self.assertEqual(me['role'], 'assistant')
        status, out = self.signed(key, 'POST', '/integration/v1/results/' + 'a' * 32 + '/approvals', {'version': 'x', 'decision': 'approuve'})
        self.assertEqual((status, out['code']), ('403 Forbidden', 'role_insuffisant'))
        for decision in ('approuve', 'refuse', 'correction_demandee'):
            with self.assertRaises(Refused) as ex:
                approve(self.desk, {'_role': 'assistant', 'scope': ['*']}, 'a' * 32, {'decision': decision, 'comment': 'texte fictif'})
            self.assertEqual(str(ex.exception), 'role_insuffisant')
        self.assertEqual(self.desk.db.execute('SELECT COUNT(*) FROM integration_decisions5635').fetchone()[0], 0)
        status, out = self.signed(key, 'POST', '/integration/v1/session', {'origin': NC})
        self.assertEqual((status, out['code']), ('403 Forbidden', 'role_insuffisant'))


class AuditManifest(_Linked):
    """F04 : identité et version des pièces du manifeste."""

    def test_f04_name_never_compensates_a_wrong_version_and_etag_never_selects_another_piece(self):
        t34._index(self.desk, [('/Dossiers/DEMO/Contrat.pdf', '2025-01-02', 'Contrat de prestation fictif', ''),
                               ('/Dossiers/DEMO/Facture.pdf', '2025-02-03', 'Facture fictive', '')])
        key = self.pair()
        status, man = self.signed(key, 'POST', '/integration/v1/source-manifests', {'matter': t34.MID, 'sources': [
            {'file_id': '11', 'etag': '"autre-version"', 'name': 'Contrat.pdf', 'size': 10, 'sha256': '0' * 64},
            {'file_id': '12', 'etag': '"e-at.pdf"', 'name': 'Facture.pdf', 'size': 10}]})
        self.assertEqual(status, '200 OK', man)
        self.assertEqual(man['accepted'], 0)
        self.assertEqual([e['error'] for e in man['errors']], ['version_differente_de_l_index', 'etag_d_une_autre_piece'])
        row = self.desk.db.execute('SELECT paths FROM integration_manifests5634 WHERE id=?', (man['manifest_id'],)).fetchone()
        self.assertEqual(json.loads(row['paths']), [])
        status, man = self.signed(key, 'POST', '/integration/v1/source-manifests', {'matter': t34.MID, 'sources': [
            {'file_id': '11', 'etag': '"e-at.pdf"', 'name': 'Contrat.pdf', 'size': 10, 'sha256_text': '1' * 64},
            {'file_id': '12', 'name': 'Facture.pdf', 'size': 10}]})
        self.assertEqual([e['error'] for e in man['errors']], ['empreinte_du_texte_differente'])
        self.assertEqual(man['reserves'], [{'source': '12', 'name': 'Facture.pdf', 'reserve': 'version_non_verifiee'}])
        status, man = self.signed(key, 'POST', '/integration/v1/source-manifests', {'matter': t34.MID, 'sources': [
            {'file_id': '11', 'etag': '"e-at.pdf"', 'name': 'Contrat.pdf', 'size': 10}]})
        self.assertEqual(man['accepted'], 1)
        from agent.index import DocumentIndex   # la pièce change entre la sélection et le travail : nouvelle sélection exigée
        idx = DocumentIndex(self.desk.c['state_dir'])
        idx.db.execute("UPDATE docs SET etag='e-nouvelle' WHERE path='/Dossiers/DEMO/Contrat.pdf'")
        idx.db.commit()
        idx.db.close()
        status, out = self.signed(key, 'POST', '/integration/v1/jobs', {'operation': 'fiche_dossier', 'matter': t34.MID,
                                                                         'idempotency_key': 'cle-f04-1', 'manifest_id': man['manifest_id']})
        self.assertEqual((status, out['code']), ('409 Conflict', 'piece_modifiee_depuis_la_selection'))


class AuditCorporate(_Linked):
    """F05 à F10 : chiffres du rapport de gestion, invariants du capital, transaction unique, projet distinct de la réalisation."""

    def setUp(self):
        super().setUp()
        import test_v510 as t510
        from agent import corporate5636 as co
        self.co, self.docs = co, t510.FakeDocs({})
        self.cid = co.company_save(self.desk, {'denomination': 'EXEMPLE SAS', 'forme': 'SAS', 'capital': '10000', 'nominal': '1',
                                               'siege': '10 rue du Test, 69002 Lyon', 'rcs': 'Lyon', 'siren': '123456789', 'matter': t34.MID})['id']
        co.subscription(self.desk, {'company': self.cid, 'name': 'Paul MARTIN', 'quantity': '6000'})
        co.subscription(self.desk, {'company': self.cid, 'name': 'Anne DUPONT', 'quantity': '4000'})

    def count(self, table):
        return self.desk.db.execute('SELECT COUNT(*) FROM %s' % table).fetchone()[0]

    def test_f05_figures_must_be_justified_by_their_own_excerpt(self):
        accounts = ('Comptes annuels de l’exercice 2025 (en euros)\nChiffre d’affaires net : 812 450 €\nRésultat net de l’exercice : 20 000 €\n'
                    'Capitaux propres : (12 500) €\nTotal du bilan : 1 204\n350 €')
        check = self.co.check_figure
        base = {'devise': 'EUR', 'unite': 'unites', 'exercice': '2025'}
        self.assertEqual(check({**base, 'libelle': 'Chiffre d’affaires', 'montant': '812 450 €', 'extrait': 'Chiffre d’affaires net : 812 450 €'},
                               accounts, '2025')[0], None)                                               # témoin conforme
        self.assertEqual(check({**base, 'libelle': 'Chiffre d’affaires', 'montant': '20 000 €', 'extrait': 'Chiffre d’affaires net : 812 450 €'},
                               accounts)[0], 'montant_absent_de_l_extrait')                              # N01 : valeur présente ailleurs
        self.assertEqual(check({**base, 'devise': 'USD', 'libelle': 'Chiffre d’affaires', 'montant': '20 000 USD',
                                'extrait': 'Résultat net de l’exercice : 20 000 €'}, accounts)[0], 'poste_different_dans_l_extrait')
        self.assertEqual(check({**base, 'devise': 'USD', 'libelle': 'Résultat net', 'montant': '20 000 USD',
                                'extrait': 'Résultat net de l’exercice : 20 000 €'}, accounts)[0], 'devise_differente_des_comptes')   # N02
        self.assertEqual(check({**base, 'libelle': 'Chiffre d’affaires', 'montant': '812 450 €', 'extrait': ''}, accounts)[0],
                         'extrait_absent_ou_trop_court')                                                 # N03 : preuve vide
        self.assertEqual(check({**base, 'libelle': 'Capitaux propres', 'montant': '-12 500 €', 'extrait': 'Capitaux propres : (12 500) €'},
                               accounts)[0], None)                                                       # parenthèses = négatif
        self.assertEqual(check({**base, 'libelle': 'Capitaux propres', 'montant': '12 500 €', 'extrait': 'Capitaux propres : (12 500) €'},
                               accounts)[0], 'montant_absent_de_l_extrait')                              # signe contrôlé
        self.assertEqual(check({**base, 'libelle': 'Total du bilan', 'montant': '1 204 350 €', 'extrait': 'Total du bilan : 1 204 350 €'},
                               accounts)[0], None)                                                       # chiffre réparti sur deux lignes
        self.assertEqual(check({**base, 'exercice': '2024', 'libelle': 'Résultat net', 'montant': '20 000 €',
                                'extrait': 'Résultat net de l’exercice : 20 000 €'}, accounts, '2025')[0], 'exercice_different')
        kilo = 'Comptes 2025 — en milliers d’euros\nChiffre d’affaires net : 812\nRésultat net : 20'
        self.assertEqual(check({**base, 'libelle': 'Chiffre d’affaires', 'montant': '812 450', 'extrait': 'Chiffre d’affaires net : 812'}, kilo)[0],
                         'unite_differente_des_comptes')                                                 # euros ≠ milliers d'euros
        self.assertEqual(check({**base, 'unite': 'milliers', 'libelle': 'Chiffre d’affaires', 'montant': '812',
                                'extrait': 'Chiffre d’affaires net : 812'}, kilo)[0], None)

    def test_f06_cumulated_cancellations_cannot_exceed_the_holding(self):
        co = self.co
        with self.assertRaises(Stop):
            co.operation_submit(self.desk, {'kind': 'diligence', 'company': self.cid, 'nature': 'reduction_capital',
                                            'annulations': 'Paul MARTIN ; 4000\nPaul MARTIN ; 4000', 'date_effet': '2026-11-15'})
        self.assertEqual((self.count('operations5636'), co.holdings(self.desk, self.cid)[1]), (0, 10000))
        out = co.operation_submit(self.desk, {'kind': 'diligence', 'company': self.cid, 'nature': 'reduction_capital', 'nouveau_capital': '4000',
                                              'annulations': 'Paul MARTIN ; 3000\nPaul MARTIN ; 3000', 'date_effet': '2026-11-15'})
        co.generate(self.desk, out['id'], dav=self.docs)
        co.realize(self.desk, {'id': out['id'], 'date': '2026-11-15', 'justificatif': 'PV fictif signé'})
        rows, total = co.holdings(self.desk, self.cid)
        self.assertEqual(({r['name']: r['quantity'] for r in rows}, total), ({'Anne DUPONT': 4000}, 4000))
        self.assertEqual(co.anomalies(self.desk, self.cid), [])

    def test_f07_negative_subscription_is_refused_without_any_trace(self):
        co = self.co
        before = (self.count('companies5636'), self.count('movements5636'), self.count('operations5636'))
        from agent.erreurs5634 import Qualified
        with self.assertRaises(Qualified) as ex:
            co.operation_submit(self.desk, {'kind': 'creation', 'forme': 'SAS', 'denomination': 'NEGATIVE SAS', 'capital': '1000', 'nominal': '10',
                                            'associes': 'A NEGATIF ; -100 ; -10\nB POSITIF ; 1100 ; 110', 'matter': t34.MID})
        self.assertEqual(str(ex.exception), 'associes_format')
        self.assertIn('ligne 1', ex.exception.detail['precision'])
        with self.assertRaises(Stop):
            co.operation_submit(self.desk, {'kind': 'creation', 'forme': 'SAS', 'denomination': 'MAL CALEE SAS', 'capital': '1000', 'nominal': '10',
                                            'associes': 'A ; 600 ; 50\nB ; 400 ; 50', 'matter': t34.MID})   # apport ≠ nombre × nominal
        with self.assertRaises(Stop):
            co.company_save(self.desk, {'denomination': 'ZERO SAS', 'forme': 'SAS', 'capital': '1000', 'nominal': '0'})
        self.assertEqual((self.count('companies5636'), self.count('movements5636'), self.count('operations5636')), before)

    def test_f08_failure_inside_the_operation_leaves_no_partial_write(self):
        co = self.co
        before = (self.count('companies5636'), self.count('contacts5636') if self._has('contacts5636') else 0)
        with unittest.mock.patch.object(self.desk, 'enqueue', side_effect=sqlite3.OperationalError('disk I/O error')):
            with self.assertRaises(sqlite3.OperationalError):
                co.operation_submit(self.desk, {'kind': 'creation', 'forme': 'SARL', 'denomination': 'CREATION INTERROMPUE', 'capital': '1000',
                                                'nominal': '10', 'associes': 'Paul MARTIN ; 1000 ; 100', 'matter': t34.MID})
        self.assertEqual((self.count('companies5636'), self.count('contacts5636') if self._has('contacts5636') else 0), before)
        self.assertEqual(self.count('operations5636'), 0)
        out = co.operation_submit(self.desk, {'kind': 'creation', 'forme': 'SARL', 'denomination': 'CREATION REPRISE', 'capital': '1000',
                                              'nominal': '10', 'associes': 'Paul MARTIN ; 1000 ; 100', 'matter': t34.MID})
        co.generate(self.desk, out['id'], dav=self.docs)
        real = co._movement
        calls = []

        def failing(*a, **k):
            calls.append(1)
            raise sqlite3.OperationalError('disk I/O error')
        with unittest.mock.patch.object(co, '_movement', side_effect=failing):
            with self.assertRaises(sqlite3.OperationalError):
                co.realize(self.desk, {'id': out['id'], 'date': '2026-10-12', 'justificatif': 'statuts signés (fictif)'})
        self.assertTrue(calls)
        self.assertEqual(co.holdings(self.desk, out['company'])[1], 0)
        self.assertEqual(co.registers(self.desk, out['company'])[1], [])                          # aucune décision partielle
        self.assertEqual(self.desk.db.execute('SELECT state FROM operations5636 WHERE id=?', (out['id'],)).fetchone()['state'], 'actes_prets')
        self.assertTrue(real)

    def _has(self, table):
        return bool(self.desk.db.execute("SELECT 1 FROM sqlite_master WHERE name=?", (table,)).fetchone())

    def test_f09_projects_never_write_registers_and_incoherent_allocation_is_refused(self):
        co = self.co
        with self.assertRaises(Stop) as ex:
            co.operation_submit(self.desk, {'kind': 'agoa', 'company': self.cid, 'exercice_clos': '2025-12-31', 'date_ag': '2026-06-15',
                                            'resultat': '1000', 'dividendes': '5000'})
        self.assertEqual(str(ex.exception), 'affectation_incoherente')
        self.assertEqual((self.count('operations5636'), co.registers(self.desk, self.cid)[1]), (0, []))
        out = co.operation_submit(self.desk, {'kind': 'agoa', 'company': self.cid, 'exercice_clos': '2025-12-31', 'date_ag': '2026-06-15',
                                              'resultat': '20000', 'dividendes': '5000'})
        co.generate(self.desk, out['id'], dav=self.docs)
        self.assertEqual(co.registers(self.desk, self.cid)[1], [])                                 # projet préparé ≠ AG tenue
        with self.assertRaises(Stop):
            co.realize(self.desk, {'id': out['id'], 'date': '2026-06-15', 'justificatif': ''})     # justificatif obligatoire
        co.realize(self.desk, {'id': out['id'], 'date': '2026-06-15', 'justificatif': 'PV d’AG signé (fictif)'})
        self.assertIn('Approbation des comptes', co.registers(self.desk, self.cid)[1][-1]['title'])
        with self.assertRaises(Stop):
            co.realize(self.desk, {'id': out['id'], 'date': '2026-06-15', 'justificatif': 'deuxième fois'})
        out = co.operation_submit(self.desk, {'kind': 'siege', 'company': self.cid, 'nouveau_siege': '5 place Fictive, 69003 Lyon', 'organe': 'dirigeant'})
        co.abandon(self.desk, {'id': out['id']})
        self.assertEqual(co.company(self.desk, self.cid)['siege'], '10 rue du Test, 69002 Lyon')
        page = self.request('/corporate', query='societe=%s&vue=actes' % self.cid)['body']
        self.assertIn('décision du 2026-06-15', page)
        self.assertNotIn('Enregistrer la réalisation', page)                                       # opérations closes : plus de bouton
        third = co.operation_submit(self.desk, {'kind': 'siege', 'company': self.cid, 'nouveau_siege': '7 quai Fictif, 69005 Lyon', 'organe': 'dirigeant'})
        self.assertIn('Les actes sont en préparation', self.request('/corporate', query='societe=%s&vue=actes' % self.cid)['body'])   # 5.6.39 (F03)
        co.generate(self.desk, third['id'], dav=self.docs)
        self.assertIn('Enregistrer la réalisation', self.request('/corporate', query='societe=%s&vue=actes' % self.cid)['body'])

    def test_f10_full_queue_leaves_nothing_and_repair_relaunches_once(self):
        co = self.co
        with unittest.mock.patch.object(self.desk, 'enqueue', side_effect=Stop('file_attente_pleine')):
            with self.assertRaises(Stop):
                co.operation_submit(self.desk, {'kind': 'siege', 'company': self.cid, 'nouveau_siege': '5 place Fictive, 69003 Lyon',
                                                'organe': 'dirigeant'})
        self.assertEqual(co.company(self.desk, self.cid)['siege'], '10 rue du Test, 69002 Lyon')
        self.assertEqual(self.count('operations5636'), 0)
        data = {'kind': 'siege', 'company': self.cid, 'nouveau_siege': '5 place Fictive, 69003 Lyon', 'organe': 'dirigeant', 'idem': 'cle-formulaire-1'}
        first = co.operation_submit(self.desk, dict(data))
        again = co.operation_submit(self.desk, dict(data))                                         # double clic : même opération
        self.assertEqual((first['id'], self.count('operations5636')), (again['id'], 1))
        with self.assertRaises(Stop):
            co.operation_submit(self.desk, {**data, 'nouveau_siege': 'autre adresse fictive'})     # même clé, autre contenu
        self.desk.db.execute('UPDATE operations5636 SET job=NULL WHERE id=?', (first['id'],))     # interruption d'une version antérieure
        self.desk.db.execute("DELETE FROM jobs WHERE kind='corporate5636'")
        self.desk.db.commit()
        co.repair(self.desk)
        self.assertEqual(self.desk.db.execute('SELECT state FROM operations5636 WHERE id=?', (first['id'],)).fetchone()['state'], 'mise_en_file_a_reprendre')
        co.relaunch(self.desk, {'id': first['id']})
        self.assertEqual(self.desk.db.execute("SELECT COUNT(*) FROM jobs WHERE kind='corporate5636'").fetchone()[0], 1)


class AuditAutomations(_Linked):
    """F11 à F16 : initiatives, relances, Outlook."""

    def plan(self):
        import test_v5636 as t36
        from agent import initiatives5636 as i
        box = t36._MailBox(t36._SummonsMail())
        answer = [{'est_assignation': True, 'juridiction': 'tribunal judiciaire de Lyon', 'date_audience': '', 'heure_audience': '', 'demandeur': 'Paul MARTIN',
                   'defendeur': 'DUPONT SAS', 'objet': 'paiement'}]
        with unittest.mock.patch('agent.initiatives5636._model', return_value=t36._Model(answer)), unittest.mock.patch('agent.mailbox.Mailbox', return_value=box):
            out = i.propose_for_mail(self.desk, uid='41', folder='INBOX')
            i.analyse(self.desk, out['id'])
        return i._row(self.desk, out['id'])

    def test_f11_dismissed_initiative_creates_nothing(self):
        from agent import initiatives5636 as i
        row = self.plan()
        i.validate(self.desk, row['id'], {'id': row['id'], 'matter': t34.MID, 'step_agenda': True})
        job = self.desk.db.execute("SELECT args FROM jobs WHERE kind='initiative5636' AND json_extract(args,'$.action')='executer'").fetchone()
        version = json.loads(job['args'])['version']
        i.dismiss(self.desk, row['id'])
        i.execute(self.desk, row['id'], version=version)                                           # travail déjà en file
        self.assertEqual(i._row(self.desk, row['id'])['state'], 'ecartee')
        from agent import agenda5634
        agenda5634.schema(self.desk)
        self.assertIsNone(self.desk.db.execute("SELECT 1 FROM agenda5634 WHERE title LIKE 'Audience%'").fetchone())

    def test_f12_drafting_step_follows_its_child_job(self):
        from agent import initiatives5636 as i
        row = self.plan()
        i.validate(self.desk, row['id'], {'id': row['id'], 'matter': t34.MID, 'step_constitution': True, 'step_note_interne': True})
        i.execute(self.desk, row['id'], version=1)
        steps = {s['id']: s for s in i._row(self.desk, row['id'])['steps']}
        self.assertEqual((steps['constitution']['status'], i._row(self.desk, row['id'])['state']), ('en_file', 'redactions'))
        self.desk.db.execute("UPDATE docreq520 SET status='echec', result=? WHERE id=?", (json.dumps({'erreur': 'modele_indisponible'}),
                                                                                         steps['constitution']['request']))
        self.desk.db.execute("UPDATE docreq520 SET status='cree', path='/Dossiers/DEMO/Note interne.docx' WHERE id=?", (steps['note_interne']['request'],))
        self.desk.db.commit()
        i.refresh_children(self.desk, row['id'])
        steps = {s['id']: s for s in i._row(self.desk, row['id'])['steps']}
        self.assertEqual((steps['constitution']['status'], steps['note_interne']['status']), ('echec', 'prepare'))
        self.assertIn('modele_indisponible', steps['constitution']['result'])
        self.assertEqual(i._row(self.desk, row['id'])['state'], 'executee')

    def seed_invoice(self, balance=1200.0):
        import test_v5636 as t36
        t36.Relances.seed(self, '2026-09-01', balance)

    def test_f13_dismissed_reminder_does_not_raise_the_level(self):
        from agent import relances5636 as r
        self.seed_invoice()
        r.prepare(self.desk, today=date(2026, 10, 12), refresh=False)
        first = self.desk.db.execute('SELECT * FROM relances5636').fetchone()
        r.dismiss(self.desk, first['id'])
        self.assertEqual(r.prepare(self.desk, today=date.today() + timedelta(days=11), refresh=False)['created'], 1)
        again = self.desk.db.execute("SELECT * FROM relances5636 WHERE state='a_valider'").fetchone()
        self.assertEqual(again['level'], 1)
        self.assertNotIn('précédent rappel', again['body'])

    def test_f14_paid_invoice_reminder_is_withdrawn_and_partial_payment_updates_it(self):
        from agent import relances5636 as r
        from agent.erreurs5634 import Qualified
        self.seed_invoice()
        r.prepare(self.desk, today=date(2026, 10, 12), refresh=False)
        row = self.desk.db.execute('SELECT * FROM relances5636').fetchone()

        class Fake:
            base = 'https://factures.example.test'

            def __init__(self, balance):
                self.balance = balance

            def request(self, method, url, headers=None, limit=0):
                return json.dumps({'data': {'balance': self.balance, 'status_id': '2', 'due_date': '2026-09-01'}}).encode()
        drafts = []
        with unittest.mock.patch('agent.webmail5628.save', side_effect=lambda desk, d, owner='cabinet': drafts.append(d) or {'uid': '9'}):
            with self.assertRaises(Qualified) as ex:
                r.deposit(self.desk, row['id'], {}, http=Fake(500))                                # paiement partiel
            self.assertEqual(str(ex.exception), 'relance_solde_modifie')
            self.assertIn('500,00 €', self.desk.db.execute('SELECT body FROM relances5636 WHERE id=?', (row['id'],)).fetchone()['body'])
            self.desk.db.execute("UPDATE invoice_ninja_cache_v310 SET balance=0, balance_exact='0.00' WHERE id='inv1'")
            self.desk.db.commit()
            with self.assertRaises(Qualified) as ex:
                r.deposit(self.desk, row['id'], {})                                                # solde nul connu localement
            self.assertEqual(str(ex.exception), 'relance_perimee')
        self.assertEqual(drafts, [])
        self.assertEqual(self.desk.db.execute('SELECT state FROM relances5636 WHERE id=?', (row['id'],)).fetchone()['state'], 'perimee')

    def test_f15_full_queue_keeps_the_daily_routine_recoverable(self):
        from agent import relances5636 as r
        self.desk.c['invoice_ninja'] = {'enabled': True, 'base_url': 'https://factures.example.test', 'api_token_file': '/nonexistent'}
        stamp = datetime(2026, 10, 12, 9, 0, tzinfo=timezone.utc).timestamp()
        with unittest.mock.patch.object(self.desk, 'enqueue', side_effect=Stop('file_attente_pleine')):
            self.assertEqual(r.schedule(self.desk, stamp), [])
        self.assertEqual(r.schedule(self.desk, stamp + 300), [])                                  # avant le prochain essai
        later = r.schedule(self.desk, stamp + 3600)
        self.assertEqual(len(later), 1)
        self.assertEqual(r.schedule(self.desk, stamp + 7200), [])                                 # une seule fois par jour

    def test_f16_all_day_events_use_civil_dates_both_ways(self):
        from agent import microsoft5636 as ms
        self.desk.c.setdefault('calendar', {})['timezone'] = 'Europe/Paris'
        event = {'all_day': 1, 'starts': '2026-11-19T23:00:00+00:00', 'ends': '2026-11-20T23:00:00+00:00'}
        out = ms.graph_times(self.desk, event)
        self.assertEqual((out['start']['dateTime'], out['end']['dateTime']), ('2026-11-20T00:00:00', '2026-11-21T00:00:00'))
        self.assertEqual(out['start']['timeZone'], out['end']['timeZone'])
        summer = {'all_day': 1, 'starts': '2026-07-13T22:00:00+00:00', 'ends': '2026-07-15T22:00:00+00:00'}    # deux jours, heure d'été
        out = ms.graph_times(self.desk, summer)
        self.assertEqual((out['start']['dateTime'], out['end']['dateTime']), ('2026-07-14T00:00:00', '2026-07-16T00:00:00'))
        self.assertEqual(ms.graph_all_day(self.desk, '2026-11-19T23:00:00.0000000Z'), date(2026, 11, 20))
        self.assertEqual(ms.graph_all_day(self.desk, '2026-11-20T00:00:00.0000000'), date(2026, 11, 20))
        timed = {'all_day': 0, 'starts': '2026-11-20T08:30:00+00:00', 'ends': '2026-11-20T09:30:00+00:00'}
        self.assertEqual(ms.graph_times(self.desk, timed)['start'], {'dateTime': '2026-11-20T08:30:00', 'timeZone': 'UTC'})



class SessionWithRememberedLogin(_Linked):
    """5.6.38 : le navigateur envoie aussi l'identifiant Basic mémorisé d'AxiorHub (AxiorHub et Nextcloud sur le même domaine)."""

    def test_frame_is_allowed_even_with_the_remembered_basic_login(self):
        key = self.pair()
        r = self.get('/nextcloud-session', self.session_url(key)['url'].split('?', 1)[1])
        self.assertNotIn('X-Frame-Options', r['headers'])                                    # redirection affichable dans le cadre
        self.assertIn("frame-ancestors 'self' " + NC, r['headers']['Content-Security-Policy'])
        sid = r['headers']['Set-Cookie'].split(';', 1)[0]
        page = self.get('/aujourdhui', cookie=sid, dest='iframe', auth=True)                  # cookie Nextcloud + identifiant Basic
        self.assertEqual(page['status'], '200 OK')
        self.assertNotIn('X-Frame-Options', page['headers'])
        self.assertIn("frame-ancestors 'self' " + NC, page['headers']['Content-Security-Policy'])
        plain = self.get('/aujourdhui', auth=True)                                             # sans cookie Nextcloud : jamais de cadre
        self.assertEqual(plain['headers']['X-Frame-Options'], 'DENY')
        self.assertIn("frame-ancestors 'none'", plain['headers']['Content-Security-Policy'])
        bad = self.get('/aujourdhui', cookie='axh_nc=inconnu', auth=True)                     # cookie invalide : connexion habituelle
        self.assertEqual((bad['status'], bad['headers']['X-Frame-Options']), ('200 OK', 'DENY'))


if __name__ == '__main__':
    unittest.main()
