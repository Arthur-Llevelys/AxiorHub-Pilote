"""5.6.88, lot sécurité : double authentification, jetons d'API par personne, journal d'audit, droits par action, ce que l'IA peut voir.

Application réelle (WSGI) en version serveur et passerelle de la distribution autonome ; données fictives (example.test, DOS-001).
L'application d'authentification du téléphone est remplacée par le calcul RFC 6238 du module : aucune application réelle n'est exercée.
"""
import csv
from datetime import datetime, timedelta, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from agent import acces5683, appareils5674 as ap, claude5675 as cl, connexion5648 as cx, integration5636, jetons5688 as jt, membres5650 as mb
from agent import connexions5652, securite5688 as sec, sources5614, totp5688 as totp, vueia5688 as vue
from agent.common import Stop, load_matters, private_json
from agent.desk import Desk
from agent.standalone_auth import StandaloneAuth, allowed as role_allowed
import test_agent as fixtures
import test_v5648 as t48

RFC_KEY = b'12345678901234567890'


class Clock:
    """Horloge de la double authentification : chaque essai se fait dans un pas choisi (un code consommé ne ressert pas)."""

    def __init__(self):
        self.now = 1_900_000_000.0

    def time(self):
        return self.now

    def next(self):
        self.now += totp.PERIOD
        return self


class Rfc(unittest.TestCase):
    def test_rfc6238_vectors(self):
        for at, code in ((59, '94287082'), (1111111109, '07081804'), (1111111111, '14050471'), (1234567890, '89005924'), (2000000000, '69279037'), (20000000000, '65353130')):
            self.assertEqual(totp.totp(RFC_KEY, at, 8), code)

    def test_window_is_one_period_each_side(self):
        secret = totp.new_secret()
        key = totp.key_of(secret)
        now = 1_800_000_000
        self.assertEqual(totp.matching_step(secret, totp.totp(key, now), now), totp.step_of(now))
        self.assertIsNotNone(totp.matching_step(secret, totp.totp(key, now - totp.PERIOD), now))
        self.assertIsNotNone(totp.matching_step(secret, totp.totp(key, now + totp.PERIOD), now))
        self.assertIsNone(totp.matching_step(secret, totp.totp(key, now + 2 * totp.PERIOD), now))
        self.assertIsNone(totp.matching_step(secret, totp.totp(key, now - 2 * totp.PERIOD), now))

    def test_uri_and_qr_code(self):
        link = totp.uri('ABCD', 'avocate@example.test')
        self.assertTrue(link.startswith('otpauth://totp/AxiorHub%20Pilote%3Aavocate%40example.test?secret=ABCD&issuer=AxiorHub%20Pilote'))
        self.assertIn('<svg', totp.qr_svg(link))

    def test_every_access_has_a_written_behaviour(self):
        names = ' '.join(a for a, _ in totp.ACCES)
        for needle in ('connexion', 'Se souvenir de moi', 'Téléphone', 'Claude', 'Jetons', 'Scripts', 'Nextcloud', 'service'):
            self.assertIn(needle, names)
        security = (Path(__file__).resolve().parents[1] / 'SECURITY.md').read_text(encoding='utf-8')
        for needle in ('Double authentification', 'Se souvenir de moi', 'Téléphone associé', 'Connecteur Claude', 'Jeton de service'):
            self.assertIn(needle, security)


class Base(t48.Base):
    def setUp(self):
        super().setUp()
        self.clock = Clock()
        p = patch.object(totp, 'time', self.clock)
        p.start()
        self.addCleanup(p.stop)
        self.addCleanup(mb.clear)

    def member(self, ident='avocate@example.test', role='avocat'):
        db = cx.connect(self.state)
        try:
            cx.add_user(db, 'cabinet', {'ident': ident, 'role': role, 'password': 'mot-de-passe-essai-12'})
            db.execute('UPDATE users SET must_change=0 WHERE ident=?', (ident,))
            db.commit()
        finally:
            db.close()
        return ident

    def member_cookie(self, ident='avocate@example.test', remember=False):
        r = self.login(ident, 'mot-de-passe-essai-12', remember=remember)
        self.assertEqual(r['status'], '303 See Other', r['body'])
        return self.token(r)

    def db(self):
        db = jt.connect(self.state)
        self.addCleanup(db.close)
        return db

    def code(self, owner='cabinet'):
        return totp.totp(totp.key_of(totp._read_secret(self.db(), owner)), self.clock.next().now)

    def post(self, route, body, cookie):
        out = self.api('m5688s/' + route, body, cookie)
        return out, json.loads(out['body'] or b'{}')

    def enrol(self, cookie, owner='cabinet'):
        self.assertEqual(self.post('2fa/debut', {}, cookie)[0]['status'], '200 OK')
        out, data = self.post('2fa/confirmer', {'code': self.code(owner)}, cookie)
        self.assertEqual(out['status'], '200 OK', out['body'])
        return data['text'].split('\n')

    def second(self, response, code=None, owner='cabinet'):
        """Saisit le code de l'étape ouverte par ``response`` ; renvoie la réponse de la page du code."""
        pending = next(v.split(';')[0].split('=', 1)[1] for v in response['cookies'] if v.startswith(cx.MFA_COOKIE + '='))
        return self.request('/connexion/double-authentification', 'POST', {'code': code or self.code(owner)},
                            extra={'HTTP_COOKIE': '%s=%s' % (cx.MFA_COOKIE, pending)})


class Connexion(Base):
    def test_password_alone_opens_no_session(self):
        admin = self.admin_cookie()
        self.enrol(admin)
        first = self.login()
        self.assertEqual(first['status'], '303 See Other')
        self.assertTrue(first['headers']['Location'].endswith('/connexion/double-authentification'))
        self.assertEqual(self.token(first), '')
        bad = self.second(first, '000000')
        self.assertEqual(bad['status'], '401 Unauthorized')
        self.assertEqual(self.token(bad), '')
        good = self.second(first)
        self.assertEqual(good['status'], '303 See Other')
        session = self.token(good)
        self.assertEqual(self.request('/courriels', cookie=session)['status'], '200 OK')

    def test_second_step_needs_the_pending_cookie(self):
        self.enrol(self.admin_cookie())
        r = self.request('/connexion/double-authentification', 'POST', {'code': self.code()})
        self.assertEqual(r['status'], '303 See Other')
        self.assertEqual(self.token(r), '')

    def test_consumed_code_is_refused(self):
        self.enrol(self.admin_cookie())
        code = self.code()
        self.assertEqual(self.second(self.login(), code)['status'], '303 See Other')
        self.assertEqual(self.second(self.login(), code)['status'], '401 Unauthorized')

    def test_attempts_are_limited_by_the_login_mechanism(self):
        self.enrol(self.admin_cookie())
        first = self.login()
        for _ in range(cx.LIMITS['mfa'][0]):
            self.assertEqual(self.second(first, '000000')['status'], '401 Unauthorized')
        blocked = self.second(first, self.code())
        self.assertEqual(blocked['status'], '429 Too Many Requests')
        self.assertEqual(self.token(blocked), '')

    def test_remember_me_keeps_the_session_opened_after_the_code_only(self):
        lawyer = self.member()
        old = self.member_cookie(remember=True)
        current = self.member_cookie()
        self.enrol(current, lawyer)
        self.assertEqual(self.request('/courriels', cookie=old)['status'], '303 See Other')   # ouverte avant l'activation : fermée
        self.assertEqual(self.request('/courriels', cookie=current)['status'], '200 OK')      # celle qui a activé reste ouverte
        first = self.login(lawyer, 'mot-de-passe-essai-12', remember=True)
        self.assertEqual(self.token(first), '')
        done = self.second(first, owner=lawyer)
        self.assertIn('Max-Age=%d' % (cx.REMEMBER_DAYS * 86400), ' '.join(done['cookies']))
        again = self.login(lawyer, 'mot-de-passe-essai-12', remember=True)
        self.assertEqual(self.token(again), '')   # jamais d'appareil de confiance : le code est redemandé

    def test_secret_is_encrypted_and_backup_codes_hashed_and_single_use(self):
        codes = self.enrol(self.admin_cookie())
        self.assertEqual(len(codes), 10)
        db = self.db()
        path = totp._secret_path(db, 'cabinet')
        raw = path.read_text(encoding='utf-8')
        self.assertTrue(raw.startswith('AXIORHUB-VAULT-V1:'))
        self.assertNotIn(totp._read_secret(db, 'cabinet'), raw)
        stored = ' '.join(r['empreinte'] for r in db.execute('SELECT empreinte FROM secours5688'))
        self.assertTrue(all(c.replace('-', '') not in stored and c not in stored for c in codes))
        self.assertEqual(self.second(self.login(), codes[0])['status'], '303 See Other')
        self.assertEqual(self.second(self.login(), codes[0])['status'], '401 Unauthorized')
        self.assertEqual(totp.status(self.db(), 'cabinet')['codes_restants'], 9)

    def test_activation_needs_a_first_right_code(self):
        admin = self.admin_cookie()
        self.post('2fa/debut', {}, admin)
        out, data = self.post('2fa/confirmer', {'code': '000000'}, admin)
        self.assertEqual(out['status'], '400 Bad Request')
        self.assertEqual(data['error'], 'code_double_authentification_incorrect')
        self.assertFalse(totp.active(self.db(), 'cabinet'))
        self.assertEqual(self.token(self.login()) != '', True)   # toujours facultative : connexion directe

    def test_account_page_shows_qr_without_address_and_never_the_secret_in_a_link(self):
        admin = self.admin_cookie()
        self.post('2fa/debut', {}, admin)
        page = self.request('/mon-compte', cookie=admin)
        self.assertEqual(page['status'], '200 OK')
        self.assertIn('<svg', page['body'])
        self.assertNotIn('otpauth', page['body'])

    def test_basic_header_stops_at_activation(self):
        self.assertEqual(self.call('/api440/drafts')['status'], '200 OK')
        self.enrol(self.admin_cookie())
        self.assertEqual(self.call('/api440/drafts')['status'], '401 Unauthorized')

    def test_phone_invitation_alone_does_not_pass(self):
        admin = self.admin_cookie()
        before = ap.connect(self.state)
        self.addCleanup(before.close)
        early = ap.invite(self.state, cx.admin_user(json.loads(self.auth.read_text(encoding='utf-8'))), 'https://cabinet.example.test', t48.PREFIX)
        self.enrol(admin)
        token = early['adresse'].split('jeton=')[1]
        self.assertIn('name="code"', self.request('/connexion/appareil', query='jeton=' + token)['body'])
        refused = self.request('/connexion/appareil', 'POST', {'jeton': token, 'nom': 'Téléphone fictif', 'code': '000000'})
        self.assertEqual(refused['status'], '401 Unauthorized')
        self.assertEqual(self.token(refused), '')
        done = self.request('/connexion/appareil', 'POST', {'jeton': token, 'nom': 'Téléphone fictif', 'code': self.code()})
        self.assertEqual(done['status'], '303 See Other')
        self.assertEqual(self.request('/courriels', cookie=self.token(done))['status'], '200 OK')

    def test_phone_paired_before_activation_is_disconnected(self):
        admin = self.admin_cookie()
        auth = json.loads(self.auth.read_text(encoding='utf-8'))
        link = ap.invite(self.state, cx.admin_user(auth), 'https://cabinet.example.test', t48.PREFIX)
        phone = self.token(self.request('/connexion/appareil', 'POST', {'jeton': link['adresse'].split('jeton=')[1], 'nom': 'Téléphone fictif'}))
        self.assertEqual(self.request('/mobile', cookie=phone)['status'], '200 OK')
        self.enrol(admin)
        self.assertEqual(self.request('/mobile', cookie=phone)['status'], '303 See Other')

    def test_claude_grant_does_not_survive_activation_or_reset(self):
        lawyer = self.member()
        auth = json.loads(self.auth.read_text(encoding='utf-8'))
        db = cl.connect(self.state)
        self.addCleanup(db.close)
        uid = db.execute('SELECT id FROM users WHERE ident=?', (lawyer,)).fetchone()['id']
        untouched = cx._user_stamp(db.execute('SELECT * FROM users WHERE id=?', (uid,)).fetchone())
        self.assertEqual(cl._user_stamp(db, auth, uid), untouched)   # sans activation : connexions existantes intactes
        self.enrol(self.member_cookie(), lawyer)
        after = cl._user_stamp(db, auth, uid)
        self.assertNotEqual(after, untouched)
        self.assertFalse(cl._grant_valid(db, auth, {'stamp': untouched, 'user_id': uid}))
        self.assertTrue(cl._grant_valid(db, auth, {'stamp': after, 'user_id': uid}))
        totp.reset(db, lawyer)
        self.assertFalse(cl._grant_valid(db, auth, {'stamp': after, 'user_id': uid}))

    def test_nextcloud_pairing_asks_for_the_code(self):
        admin = self.admin_cookie()
        self.enrol(admin)
        auth = json.loads(self.auth.read_text(encoding='utf-8'))
        env = {'axiorhub.auth': auth, 'REMOTE_ADDR': '192.0.2.10'}
        desk = Desk(self.f.c)
        self.assertTrue(integration5636._code_refused(desk, env, {}))
        self.assertFalse(integration5636._code_refused(desk, env, {'code': self.code()}))

    def test_keys_of_nextcloud_app_are_revoked_at_activation(self):
        admin = self.admin_cookie()
        desk = Desk(self.f.c)
        integration5636.schema(desk)
        desk.db.execute("INSERT INTO integration_keys5636(id,axiorhub_user,role,secret,created) VALUES('k1','admin','administrateur','x','2026-01-01')")
        desk.db.commit()
        self.enrol(admin)
        self.assertTrue(desk.db.execute("SELECT revoked FROM integration_keys5636 WHERE id='k1'").fetchone()[0])


class Required(Base):
    def test_option_cannot_lock_out_the_administrator(self):
        admin = self.admin_cookie()
        out, data = self.post('admin/2fa/exiger', {'exiger': True}, admin)
        self.assertEqual(data['error'], 'activez_d_abord_votre_double_authentification')
        self.assertFalse(totp.required(self.db()))

    def test_required_opens_only_the_activation_page(self):
        lawyer = self.member()
        session = self.member_cookie()
        admin = self.admin_cookie()
        self.enrol(admin)
        admin = self.token(self.second(self.login()))
        self.assertEqual(self.post('admin/2fa/exiger', {'exiger': True}, admin)[0]['status'], '200 OK')
        page = self.request('/courriels', cookie=session)
        self.assertEqual(page['status'], '303 See Other')
        self.assertIn('/mon-compte', page['headers']['Location'])
        self.assertEqual(self.request('/mon-compte', cookie=session)['status'], '200 OK')
        api = self.call('/api440/drafts', auth=False, extra={'HTTP_COOKIE': '%s=%s' % (cx.COOKIE, session)})
        self.assertEqual(json.loads(api['body'])['error'], 'double_authentification_a_activer')
        self.enrol(session, lawyer)
        self.assertEqual(self.request('/courriels', cookie=session)['status'], '200 OK')

    def test_tokens_stop_while_activation_is_required(self):
        lawyer = self.member()
        made = self.make_token(self.member_cookie(), 'lecture')
        admin = self.admin_cookie()
        self.enrol(admin)
        admin = self.token(self.second(self.login()))
        self.assertEqual(self.bearer(made, '/api/v1/capabilities')['status'], '200 OK')
        self.post('admin/2fa/exiger', {'exiger': True}, admin)
        self.assertEqual(self.bearer(made, '/api/v1/capabilities')['status'], '401 Unauthorized')
        self.assertTrue(lawyer)

    def make_token(self, cookie, scope, days=30):
        out, data = self.post('jetons/creer', {'nom': 'Script fictif', 'portee': scope, 'jours': days}, cookie)
        self.assertEqual(out['status'], '200 OK', out['body'])
        return data['text']

    def bearer(self, token, path, method='GET', body=None, extra=None):
        return self.call(path, method, body, auth=False, csrf=False, extra={'HTTP_AUTHORIZATION': 'Bearer ' + token, **(extra or {})})

    def test_administrator_reset(self):
        lawyer = self.member()
        session = self.member_cookie()
        self.enrol(session, lawyer)
        token = self.make_token(session, 'lecture')
        other = self.member('assistant@example.test', 'assistant')
        out, _ = self.post('admin/2fa/reinitialiser', {'compte': lawyer}, self.member_cookie(other))
        self.assertEqual(out['status'], '403 Forbidden')
        admin = self.admin_cookie()
        self.assertEqual(self.post('admin/2fa/reinitialiser', {'compte': 'cabinet'}, admin)[1]['error'], 'droits_compte_invalide')   # jamais le sien
        self.assertEqual(self.post('admin/2fa/reinitialiser', {'compte': lawyer}, admin)[0]['status'], '200 OK')
        self.assertFalse(totp.active(self.db(), lawyer))
        self.assertEqual(self.request('/courriels', cookie=session)['status'], '303 See Other')
        self.assertEqual(self.bearer(token, '/api/v1/capabilities')['status'], '401 Unauthorized')
        row = Desk(self.f.c).db.execute("SELECT payload FROM audit_chain_v290 WHERE action='double_auth_reinitialisee'").fetchone()
        self.assertEqual(json.loads(row[0])['compte'], lawyer)
        self.assertEqual(json.loads(row[0])['par'], 'cabinet')

    def test_own_removal_needs_a_code_and_is_refused_when_required(self):
        admin = self.admin_cookie()
        self.enrol(admin)
        out, data = self.post('2fa/desactiver', {'code': '000000'}, admin)
        self.assertEqual(data['error'], 'code_double_authentification_incorrect')
        self.assertTrue(totp.active(self.db(), 'cabinet'))
        self.post('admin/2fa/exiger', {'exiger': True}, admin)
        self.assertEqual(self.post('2fa/desactiver', {'code': self.code()}, admin)[1]['error'], 'double_authentification_exigee')
        self.post('admin/2fa/exiger', {'exiger': False}, admin)
        self.assertEqual(self.post('2fa/desactiver', {'code': self.code()}, admin)[0]['status'], '200 OK')

    def test_server_command_clears_the_protection(self):
        admin = self.admin_cookie()
        self.enrol(admin)
        db = self.db()
        self.assertIn('retirée', cx.cli_reset_2fa(db, 'admin', 'admin'))
        self.assertFalse(totp.active(db, 'cabinet'))
        self.assertEqual(self.request('/courriels', cookie=admin)['status'], '303 See Other')


class Standalone(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / 'auth'
        env = patch.dict(os.environ, {'AXIORHUB_AUTH_STATE': str(self.root)})
        env.start()
        self.addCleanup(env.stop)
        self.clock = Clock()
        p = patch.object(totp, 'time', self.clock)
        p.start()
        self.addCleanup(p.stop)
        self.seen = []

        def inner(env, start):
            self.seen.append(env.get('HTTP_X_AXIORHUB_USER'))
            start('200 OK', [('Content-Type', 'text/plain')])
            return [b'ok']
        self.app = StandaloneAuth(inner, self.root, 'https://agent.example.test')
        self.addCleanup(lambda: self.app.db.close())
        salt = 'ab' * 16
        self.password = 'mot-de-passe-essai-12'
        self.app.db.execute("INSERT INTO users(email,password_hash,salt,active,created,role,name) VALUES('avocate@example.test',?,?,1,'2026-01-01','administrateur','Essai')",
                            (self.app._hash(self.password, salt), salt))
        self.app.db.commit()

    def request(self, path, method='GET', form=None, cookie=''):
        from urllib.parse import urlencode
        raw = urlencode(form or {}).encode()
        env = {'REQUEST_METHOD': method, 'PATH_INFO': path, 'QUERY_STRING': '', 'wsgi.input': io.BytesIO(raw), 'CONTENT_LENGTH': str(len(raw)),
               'HTTP_ORIGIN': 'https://agent.example.test', 'REMOTE_ADDR': '192.0.2.30', 'HTTP_COOKIE': cookie}
        out = {}

        def start(status, headers):
            out.update(status=status, headers=headers)
        out['body'] = b''.join(self.app(env, start)).decode('utf-8', 'replace')
        out['cookies'] = {v.split('=')[0]: v.split('=', 1)[1].split(';')[0] for n, v in out['headers'] if n == 'Set-Cookie'}
        return out

    def activate(self):
        db = totp.connect('')
        self.addCleanup(db.close)
        totp.begin(db, 'avocate@example.test', 'Essai')
        code = totp.totp(totp.key_of(totp._read_secret(db, 'avocate@example.test')), self.clock.next().now)
        totp.confirm(db, 'avocate@example.test', code)
        return db

    def test_login_stops_at_the_code(self):
        db = self.activate()
        first = self.request('/login', 'POST', {'email': 'avocate@example.test', 'password': self.password})
        self.assertNotIn('axiorhub_session', first['cookies'])
        pending = first['cookies']['axiorhub_mfa']
        bad = self.request('/double-authentification', 'POST', {'code': '000000'}, 'axiorhub_mfa=' + pending)
        self.assertEqual(bad['status'], '401 Unauthorized')
        self.assertNotIn('axiorhub_session', bad['cookies'])
        code = totp.totp(totp.key_of(totp._read_secret(db, 'avocate@example.test')), self.clock.next().now)
        good = self.request('/double-authentification', 'POST', {'code': code}, 'axiorhub_mfa=' + pending)
        self.assertEqual(good['status'], '303 See Other')
        self.seen.clear()
        self.assertEqual(self.request('/courriels', cookie='axiorhub_session=' + good['cookies']['axiorhub_session'])['status'], '200 OK')
        self.assertEqual(self.seen, ['avocate@example.test'])

    def test_delegated_manager_never_touches_an_administrator(self):
        self.app.db.execute("INSERT INTO users(email,password_hash,salt,active,created,role,name) VALUES('assistant@example.test','x','ab',1,'2026-01-01','assistant','Essai 2')")
        self.app.db.commit()
        manager = {'id': 99, 'email': 'gestion@example.test', 'role': 'avocat'}
        admin_id = self.app.db.execute("SELECT id FROM users WHERE role='administrateur'").fetchone()[0]
        other = self.app.db.execute("SELECT id FROM users WHERE role='assistant'").fetchone()[0]
        self.assertIn('Réservé', self.app._account_action(manager, {'op': 'disable', 'id': str(admin_id)}))
        self.assertIn('Réservé', self.app._account_action(manager, {'op': 'role', 'id': str(other), 'role': 'administrateur'}))
        self.assertIn('Réservé', self.app._account_action(manager, {'op': 'create', 'email': 'n@example.test', 'role': 'administrateur'}))
        self.assertIn('désactivé', self.app._account_action(manager, {'op': 'disable', 'id': str(other)}))

    def test_session_before_activation_is_closed(self):
        old = self.request('/login', 'POST', {'email': 'avocate@example.test', 'password': self.password})['cookies']['axiorhub_session']
        self.request('/courriels', cookie='axiorhub_session=' + old)
        self.assertEqual(self.seen, ['avocate@example.test'])
        self.activate()
        self.seen.clear()
        self.request('/courriels', cookie='axiorhub_session=' + old)
        self.assertEqual(self.seen, [])   # la requête n'atteint pas l'application


class Tokens(Required):
    def set_service(self, token):
        data = json.loads(self.auth.read_text(encoding='utf-8'))
        data['api_token_sha256'] = hashlib.sha256(token.encode()).hexdigest()
        private_json(self.auth, data)

    def test_service_token_still_works_without_identity(self):
        self.set_service('jeton-de-service-fictif')
        r = self.bearer('jeton-de-service-fictif', '/api/v1/capabilities')
        self.assertEqual(r['status'], '200 OK')
        self.assertEqual(self.bearer('faux', '/api/v1/capabilities')['status'], '401 Unauthorized')

    def test_token_is_shown_once_and_stored_hashed(self):
        admin = self.admin_cookie()
        token = self.make_token(admin, 'lecture')
        self.assertTrue(token.startswith(jt.PREFIX))
        db = self.db()
        row = db.execute('SELECT * FROM jetons5688').fetchone()
        self.assertEqual(row['token_hash'], hashlib.sha256(token.encode()).hexdigest())
        self.assertNotIn(token, json.dumps(dict(row)))
        self.assertNotIn(token, self.request('/mon-compte', cookie=admin)['body'])

    def test_expiry_is_mandatory(self):
        admin = self.admin_cookie()
        out, data = self.post('jetons/creer', {'nom': 'Sans fin', 'portee': 'lecture', 'jours': 0}, admin)
        self.assertEqual(data['error'], 'jeton_duree_invalide')

    def test_token_opens_the_api_only(self):
        token = self.make_token(self.admin_cookie(), 'ecriture')
        self.assertEqual(self.bearer(token, '/api/v1/capabilities')['status'], '200 OK')
        for path in ('/api440/drafts', '/courriels', '/parametres/journal-audit', '/claude/mcp'):
            self.assertIn(self.bearer(token, path)['status'][:3], ('401', '303', '404', '405'), path)

    def test_read_scope_refuses_writes_but_allows_search(self):
        token = self.make_token(self.admin_cookie(), 'lecture')
        refused = self.bearer(token, '/api/v1/cabinet/business-tests', 'POST', {})
        self.assertEqual(refused['status'], '403 Forbidden')
        self.assertEqual(json.loads(refused['body'])['error'], 'jeton_lecture_seule')
        self.assertEqual(self.bearer(token, '/api/v1/search', 'POST', {'query': 'bail'})['status'], '200 OK')

    def test_write_scope_opens_writes(self):
        token = self.make_token(self.admin_cookie(), 'ecriture')
        self.assertEqual(self.bearer(token, '/api/v1/cabinet/business-tests', 'POST', {})['status'], '200 OK')

    def test_identity_reaches_actor_and_reserved_matters_apply(self):
        lawyer = self.member()
        token = self.make_token(self.member_cookie(), 'lecture')
        mid = load_matters(self.f.c)[0]['id']
        self.assertEqual(self.bearer(token, '/api/v1/matters/' + mid)['status'], '200 OK')
        mb.set_reserve(self.state, mid, ['autre@example.test'])
        refused = self.bearer(token, '/api/v1/matters/' + mid)
        self.assertEqual(refused['status'], '403 Forbidden')
        self.assertEqual(json.loads(refused['body'])['error'], 'dossier_reserve')
        found = json.loads(self.bearer(token, '/api/v1/search', 'POST', {'query': 'bail'})['body'])
        self.assertFalse([r for r in found['results'] if r['matter'] == mid])
        self.bearer(self.make_token(self.member_cookie(), 'ecriture'), '/api/v1/assistant', 'POST', {'question': 'Quels sont mes dossiers ?'})
        row = Desk(self.f.c).db.execute("SELECT payload FROM audit_chain_v290 WHERE action='jeton5688_appel' ORDER BY seq DESC").fetchone()
        self.assertEqual(json.loads(row[0])['auteur'], lawyer)

    def test_spoofed_identity_headers_are_ignored(self):
        self.member()
        token = self.make_token(self.member_cookie(), 'lecture')
        r = self.bearer(token, '/api/v1/cabinet/audit', extra={'HTTP_X_AXIORHUB_USER': 'cabinet', 'HTTP_X_AXIORHUB_ROLE': 'administrateur'})
        self.assertEqual(r['status'], '403 Forbidden')

    def test_person_token_is_limited_to_listed_routes(self):
        self.member()
        token = self.make_token(self.member_cookie(), 'ecriture')
        for path, method in (('/api/v1/threads', 'GET'), ('/api/v1/jobs', 'GET'), ('/api/v1/ecosystem/services', 'POST'), ('/api/v1/production/run', 'POST')):
            r = self.bearer(token, path, method, {} if method == 'POST' else None)
            self.assertEqual(json.loads(r['body'])['error'], 'jeton_route_refusee', path)

    def test_expired_revoked_disabled_or_changed_password_all_stop_the_token(self):
        lawyer = self.member()
        session = self.member_cookie()
        expired = self.make_token(session, 'lecture')
        revoked = self.make_token(session, 'lecture')
        changed = self.make_token(session, 'lecture')
        db = self.db()
        db.execute('UPDATE jetons5688 SET expire=? WHERE token_hash=?', ('2020-01-01T00:00:00+00:00', hashlib.sha256(expired.encode()).hexdigest()))
        db.commit()
        self.assertEqual(self.bearer(expired, '/api/v1/capabilities')['status'], '401 Unauthorized')
        ident = db.execute('SELECT id FROM jetons5688 WHERE token_hash=?', (hashlib.sha256(revoked.encode()).hexdigest(),)).fetchone()[0]
        self.assertEqual(self.post('jetons/revoquer', {'id': ident}, session)[0]['status'], '200 OK')
        self.assertEqual(self.bearer(revoked, '/api/v1/capabilities')['status'], '401 Unauthorized')
        self.assertEqual(self.bearer(changed, '/api/v1/capabilities')['status'], '200 OK')
        cx.update_user(db, 'cabinet', {'id': db.execute('SELECT id FROM users WHERE ident=?', (lawyer,)).fetchone()[0], 'action': 'desactiver'})
        self.assertEqual(self.bearer(changed, '/api/v1/capabilities')['status'], '401 Unauthorized')
        cx.update_user(db, 'cabinet', {'id': db.execute('SELECT id FROM users WHERE ident=?', (lawyer,)).fetchone()[0], 'action': 'activer'})
        self.assertEqual(self.bearer(changed, '/api/v1/capabilities')['status'], '200 OK')
        db.execute("UPDATE users SET hash='autre' WHERE ident=?", (lawyer,))
        db.commit()
        self.assertEqual(self.bearer(changed, '/api/v1/capabilities')['status'], '401 Unauthorized')

    def test_token_created_before_activation_stops(self):
        admin = self.admin_cookie()
        token = self.make_token(admin, 'lecture')
        self.enrol(admin)
        self.assertEqual(self.bearer(token, '/api/v1/capabilities')['status'], '401 Unauthorized')
        fresh = self.make_token(admin, 'lecture')
        self.assertEqual(self.bearer(fresh, '/api/v1/capabilities')['status'], '200 OK')

    def test_someone_else_cannot_revoke_a_token_and_service_token_manages_nothing(self):
        session = self.member_cookie(self.member())
        self.make_token(session, 'lecture')
        ident = self.db().execute('SELECT id FROM jetons5688').fetchone()[0]
        other = self.member_cookie(self.member('assistant@example.test', 'assistant'))
        self.assertEqual(self.post('jetons/revoquer', {'id': ident}, other)[1]['error'], 'jeton_absent_ou_deja_revoque')
        self.set_service('jeton-de-service-fictif')
        r = self.call('/api440/m5688s/jetons/creer', 'POST', {'nom': 'x', 'portee': 'lecture', 'jours': 30}, auth=False,
                      extra={'HTTP_AUTHORIZATION': 'Bearer jeton-de-service-fictif'})
        self.assertEqual(r['status'], '403 Forbidden')

    def test_calls_and_lifecycle_leave_a_trace(self):
        admin = self.admin_cookie()
        token = self.make_token(admin, 'ecriture')
        self.bearer(token, '/api/v1/cabinet/business-tests', 'POST', {})
        actions = {r[0] for r in Desk(self.f.c).db.execute('SELECT action FROM audit_chain_v290')}
        self.assertTrue({'jeton5688_cree', 'jeton5688_appel'} <= actions)


class Journal(Base):
    def seed(self):
        desk = Desk(self.f.c)
        mb.set_author('maitre@example.test')
        desk.audit('essai_journal', {'detail': 'DOS-001'})
        mb.clear()
        desk.audit('essai_systeme', {'detail': '=SOMME(A1)'})
        return desk

    def test_author_is_added_only_when_known(self):
        desk = self.seed()
        first = json.loads(desk.db.execute("SELECT payload FROM audit_chain_v290 WHERE action='essai_journal'").fetchone()[0])
        second = json.loads(desk.db.execute("SELECT payload FROM audit_chain_v290 WHERE action='essai_systeme'").fetchone()[0])
        self.assertEqual(first['auteur'], 'maitre@example.test')
        self.assertNotIn('auteur', second)
        mb.set_author('maitre@example.test')
        desk.audit('essai_liste', ['pas un objet'])          # appel existant : rien ne casse
        desk.audit('essai_nomme', {'auteur': 'deja-nomme'})
        mb.clear()
        self.assertEqual(json.loads(desk.db.execute("SELECT payload FROM audit_chain_v290 WHERE action='essai_nomme'").fetchone()[0])['auteur'], 'deja-nomme')
        from agent.cabinet_pilot import audit_status
        self.assertTrue(audit_status(desk)['valid'])

    def test_request_sets_the_author(self):
        lawyer = self.member()
        session = self.member_cookie()
        self.assertEqual(self.post('jetons/creer', {'nom': 'x', 'portee': 'lecture', 'jours': 30}, session)[0]['status'], '200 OK')
        row = Desk(self.f.c).db.execute("SELECT payload FROM audit_chain_v290 WHERE action='jeton5688_cree'").fetchone()
        self.assertEqual(json.loads(row[0])['auteur'], lawyer)

    def test_page_is_for_the_administrator_only(self):
        self.seed()
        lawyer = self.member_cookie(self.member())
        self.assertEqual(self.request('/parametres/journal-audit', cookie=lawyer)['status'], '403 Forbidden')
        self.assertEqual(self.call('/api440/m5688s/journal/csv', auth=False, extra={'HTTP_COOKIE': '%s=%s' % (cx.COOKIE, lawyer)})['status'], '403 Forbidden')
        self.assertEqual(self.call('/api/v1/cabinet/audit', auth=False, extra={'HTTP_COOKIE': '%s=%s' % (cx.COOKIE, lawyer)})['status'], '403 Forbidden')
        page = self.request('/parametres/journal-audit', cookie=self.admin_cookie())
        self.assertEqual(page['status'], '200 OK')
        for text in ('Journal d’audit', 'Chaîne intacte', 'essai journal', 'Exporter (CSV)'):
            self.assertIn(text, page['body'])

    def test_right_reglages_does_not_open_the_journal(self):
        lawyer = self.member()
        mb.set_rights(self.state, 'cabinet', lawyer, 'avocat', {'reglages': True})
        session = self.member_cookie()
        page = self.request('/parametres/journal-audit', cookie=session)
        self.assertIn(page['status'][:3], ('400', '403'))
        self.assertNotIn('Chaîne', page['body'])
        self.assertEqual(self.call('/api440/m5688s/journal/csv', auth=False, extra={'HTTP_COOKIE': '%s=%s' % (cx.COOKIE, session)})['status'], '403 Forbidden')

    def test_filters_and_pagination(self):
        desk = self.seed()
        self.assertEqual(sec.journal(desk, {'auteur': 'maitre@example.test'})['total'], 1)
        self.assertEqual(sec.journal(desk, {'action': 'essai_systeme'})['total'], 1)
        self.assertEqual(sec.journal(desk, {'q': 'DOS-001'})['total'], 1)
        self.assertEqual(sec.journal(desk, {'q': '100%'})['total'], 0)
        today = datetime.now(timezone.utc).date()
        self.assertGreaterEqual(sec.journal(desk, {'du': today.isoformat(), 'au': today.isoformat()})['total'], 2)
        self.assertEqual(sec.journal(desk, {'au': (today - timedelta(days=30)).isoformat()})['total'], 0)
        with self.assertRaises(Stop):
            sec.journal(desk, {'du': 'pas-une-date'})
        for n in range(sec.PER_PAGE + 3):
            desk.audit('essai_page', {'n': n})
        one, two = sec.journal(desk, {'action': 'essai_page'}), sec.journal(desk, {'action': 'essai_page', 'page': '2'})
        self.assertEqual((len(one['rows']), len(two['rows']), one['pages']), (sec.PER_PAGE, 3, 2))
        self.assertGreater(one['rows'][0]['seq'], two['rows'][0]['seq'])

    def test_csv_export_neutralises_formulas_and_is_traced(self):
        desk = self.seed()
        desk.audit('=FORMULE', {})
        out = sec._export(desk, {}, 'cabinet')
        rows = list(csv.reader(io.StringIO(out['body'].lstrip('﻿')), delimiter=';'))
        self.assertEqual(rows[0][:4], ['Numéro', 'Date', 'Action', 'Auteur'])
        self.assertIn("'=FORMULE", [r[2] for r in rows])
        self.assertTrue(desk.db.execute("SELECT 1 FROM audit_chain_v290 WHERE action='journal_audit_exporte'").fetchone())

    def test_tampering_is_shown(self):
        desk = self.seed()
        desk.db.execute("UPDATE audit_chain_v290 SET payload='{}' WHERE action='essai_journal'")
        desk.db.commit()
        desk.audit('essai_apres', {})   # nouvelle ligne : le résultat gardé en mémoire n'est pas réutilisé
        page = self.request('/parametres/journal-audit', cookie=self.admin_cookie())
        self.assertIn('Chaîne rompue', page['body'])
        self.assertNotIn('Chaîne intacte', page['body'])


class FineRights(unittest.TestCase):
    ROUTES = (('GET', '/api440/m500/time/export', ''), ('GET', '/api440/m5670/efacture/export', ''), ('POST', '/api440/m530/event/delete', ''), ('POST', '/api440/m5625/boite/supprimer', ''),
              ('POST', '/action', 'delete_mail_rule370'), ('POST', '/action', 'approve_cabinet_template'), ('POST', '/api440/m5653/document/transmettre', ''), ('GET', '/facturation', ''),
              ('GET', '/honoraires', ''), ('GET', '/api/v1/invoice-ninja/unpaid', ''), ('POST', '/api440/m5636/comptes/ajouter', ''), ('GET', '/courriels', ''), ('POST', '/api440/m5650/droits', ''))

    def setUp(self):
        self.f = fixtures.EngineTests(methodName='test_observation_has_no_mail_write')
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.state = self.f.c['state_dir']

    def test_nothing_changes_for_anyone_after_the_update(self):
        for role in ('avocat', 'assistant'):
            for method, path, action in self.ROUTES:
                self.assertEqual(mb.allowed(self.state, 'compte@example.test', role, method, path, action), bool(role_allowed(role, method, path, action)) and
                                 mb.family_allowed(self.state, 'compte@example.test', role, method, path, action), (role, path, action))

    def test_defaults_do_not_open_anything_new_to_an_assistant(self):
        self.assertFalse(mb.allowed(self.state, 'a@example.test', 'assistant', 'POST', '/api440/m5653/document/transmettre'))
        self.assertFalse(mb.allowed(self.state, 'a@example.test', 'assistant', 'POST', '/api440/m5625/boite/supprimer'))
        self.assertTrue(mb.allowed(self.state, 'a@example.test', 'assistant', 'POST', '/api440/m530/event/delete'))    # l'assistant le peut déjà : rien n'est retiré

    def test_withdrawn_right_refuses(self):
        owner = 'avocate@example.test'
        for right, method, path, action in (('exporter', 'GET', '/api440/m500/time/export', ''), ('supprimer', 'POST', '/api440/m5625/boite/supprimer', ''),
                                            ('valider_modele', 'POST', '/action', 'approve_cabinet_template'), ('transmettre_facture', 'POST', '/api440/m5653/document/transmettre', ''),
                                            ('voir_honoraires', 'GET', '/facturation', '')):
            self.assertTrue(mb.allowed(self.state, owner, 'avocat', method, path, action), right)
            mb.set_rights(self.state, 'cabinet', owner, 'avocat', {right: False})
            self.assertFalse(mb.allowed(self.state, owner, 'avocat', method, path, action), right)
            mb.set_rights(self.state, 'cabinet', owner, 'avocat', {})

    def test_granted_right_opens_the_lawyer_route_only(self):
        owner = 'assistant@example.test'
        mb.set_rights(self.state, 'cabinet', owner, 'assistant', {'supprimer': True, 'transmettre_facture': True})
        self.assertTrue(mb.allowed(self.state, owner, 'assistant', 'POST', '/api440/m5625/boite/supprimer'))
        self.assertTrue(mb.allowed(self.state, owner, 'assistant', 'POST', '/api440/m5653/document/transmettre'))
        self.assertFalse(mb.allowed(self.state, owner, 'assistant', 'POST', '/api440/m5650/droits'))

    def test_administrator_keeps_everything_and_registry_failure_refuses(self):
        self.assertTrue(mb.allowed(self.state, 'cabinet', 'administrateur', 'GET', '/facturation'))
        mb.set_rights(self.state, 'cabinet', 'x@example.test', 'avocat', {'exporter': False})
        (Path(self.state) / mb.FILE).write_text('{illisible', encoding='utf-8')
        (Path(self.state) / mb.MIRROR).write_text('{illisible', encoding='utf-8')
        self.assertFalse(mb.allowed(self.state, 'y@example.test', 'avocat', 'GET', '/api440/m500/time/export'))


class ManageAccounts(Base):
    def test_right_is_expressly_delegated_and_never_reaches_an_administrator(self):
        manager = self.member()
        target = self.member('assistant@example.test', 'assistant')
        session = self.member_cookie()
        body = {'ident': 'nouveau@example.test', 'role': 'assistant', 'password': 'mot-de-passe-essai-12'}
        self.assertEqual(self.api('m5636/comptes/ajouter', body, session)['status'], '403 Forbidden')
        mb.set_rights(self.state, 'cabinet', manager, 'avocat', {'gerer_comptes': True})
        self.assertEqual(self.api('m5636/comptes/ajouter', body, session)['status'], '200 OK')
        refused = self.api('m5636/comptes/ajouter', {**body, 'ident': 'chef@example.test', 'role': 'administrateur'}, session)
        self.assertEqual(json.loads(refused['body'])['error'], 'role_insuffisant')
        db = self.db()
        uid = lambda ident: db.execute('SELECT id FROM users WHERE ident=?', (ident,)).fetchone()[0]
        self.assertEqual(json.loads(self.api('m5636/comptes/modifier', {'id': uid(target), 'action': 'role', 'role': 'administrateur'}, session)['body'])['error'], 'role_insuffisant')
        self.assertEqual(json.loads(self.api('m5636/comptes/modifier', {'id': uid(manager), 'action': 'role', 'role': 'assistant'}, session)['body'])['error'], 'role_insuffisant')
        self.assertEqual(self.api('m5636/comptes/modifier', {'id': uid(target), 'action': 'desactiver'}, session)['status'], '200 OK')
        self.member('associe@example.test', 'administrateur')
        self.assertEqual(json.loads(self.api('m5636/comptes/modifier', {'id': uid('associe@example.test'), 'action': 'desactiver'}, session)['body'])['error'], 'role_insuffisant')
        self.assertEqual(self.api('m5650/droits', {'owner': manager, 'role': 'avocat'}, session)['status'], '403 Forbidden')
        self.assertEqual(self.api('m5636/comptes/recuperation', {'email': 'x@example.test'}, session)['status'], '403 Forbidden')
        self.assertNotIn('associe@example.test', self.request('/mon-compte', cookie=session)['body'])


class FakeCtx:
    def __init__(self, desk):
        self.desk = desk


class AiView(Base):
    def close(self, *categories):
        desk = Desk(self.f.c)
        vue.save(desk, {c: c not in categories for c in vue.IDS})
        return desk

    def test_default_is_everything_open(self):
        desk = Desk(self.f.c)
        self.assertEqual(vue.effective(desk), {c: True for c in vue.IDS})
        desk.setting(vue.SETTING, 'illisible')
        self.assertEqual(vue.cabinet(desk), {c: True for c in vue.IDS})

    def test_invalid_values_are_refused(self):
        with self.assertRaises(Stop):
            vue.save(Desk(self.f.c), {c: 'false' for c in vue.IDS})

    def test_mail_is_not_even_opened_when_closed(self):
        desk = self.close('courriels')
        with patch('agent.mailbox.Mailbox', side_effect=AssertionError('boîte ouverte')):
            sources, state = acces5683.read_mail(desk, 'résume mes mails d’aujourd’hui', box=None)
        self.assertEqual(sources, [])
        self.assertIn('Ce que l’IA peut voir', state['erreur'])
        self.assertTrue(any('non consultés' in x for x in acces5683.limits_of({'courriels': state})))

    def test_agenda_is_not_read_when_closed(self):
        desk = self.close('agenda')
        calls = []

        class Spy:
            db = desk.db
            c = desk.c
            settings = desk.settings
        desk.db.set_trace_callback(lambda sql: calls.append(sql))
        self.addCleanup(desk.db.set_trace_callback, None)
        sources, state = acces5683.read_agenda(Spy, 'mon agenda de demain')
        self.assertEqual(sources, [])
        self.assertFalse([s for s in calls if 'calendar_cache' in s or 'agenda5634' in s])
        self.assertTrue(any('non consulté' in x for x in acces5683.limits_of({'agenda': state})))

    def test_rights_are_the_upper_limit(self):
        lawyer = 'avocate@example.test'
        desk = Desk(self.f.c)
        mb.set_rights(self.state, 'cabinet', lawyer, 'avocat', {'voir_honoraires': False})
        self.assertEqual(mb.ai_limits(self.state, lawyer, 'avocat'), {'factures', 'temps'})
        self.assertEqual(mb.ai_limits(self.state, 'cabinet', 'administrateur'), frozenset())
        mb.set_ai_denied(mb.ai_limits(self.state, lawyer, 'avocat'))
        self.assertTrue(vue.blocked(desk, 'factures'))                                   # réglage du cabinet ouvert : les droits l'emportent
        self.assertFalse(vue.blocked(desk, 'documents'))
        mb.clear()
        self.assertFalse(vue.blocked(desk, 'factures'))

    def test_request_applies_the_person_limit_and_job_carries_it(self):
        from agent.integration import submit_question
        desk = Desk(self.f.c)
        mb.set_ai_denied(('factures', 'temps'))
        made = submit_question(desk, 'Quels sont les honoraires ?')
        args = json.loads(desk.db.execute('SELECT args FROM jobs WHERE id=?', (made['job_id'],)).fetchone()[0])
        self.assertEqual(args['vue_ia'], ['factures', 'temps'])

    def test_claude_tools_refuse_closed_categories(self):
        desk = self.close('documents', 'agenda', 'contacts')
        ctx = FakeCtx(desk)
        for tool, args in ((cl.t_lire, {'chemin': '/x.pdf'}), (cl.t_chercher, {'question': 'bail'}), (cl.t_echeances, {}), (cl.t_opportunites, {}), (cl.t_memoire, {'dossier': 'DOS-001'})):
            with self.assertRaises(Stop) as caught:
                tool(ctx, args)
            self.assertEqual(str(caught.exception), 'categorie_fermee_a_l_ia', tool.__name__)

    def test_tools_of_closed_categories_are_not_offered_to_the_model(self):
        desk = self.close('factures')
        seen = {}

        class Model:
            def complete(self, messages, **kwargs):
                seen['prompt'] = messages[0]['content']
                return '{"appels": []}'
        tools = [{'outil': 'facturation.etat', 'nature': 'lecture', 'fournisseur': 'x'}, {'outil': 'agenda.lire', 'nature': 'lecture', 'fournisseur': 'x'}]
        with patch.object(connexions5652, 'available_tools', return_value=tools):
            connexions5652.agent_research(desk, 'cabinet', 'administrateur', 'Où en est la facture ?', '', Model())
        self.assertIn('agenda.lire', seen['prompt'])
        self.assertNotIn('facturation.etat', seen['prompt'])

    def test_mission_inventory_respects_the_setting(self):
        desk = self.close('agenda', 'courriels', 'documents')
        mid = load_matters(self.f.c)[0]['id']
        columns = desk.db.execute('PRAGMA table_info(portfolio_mail_links)').fetchall()
        values = {c[1]: ('' if 'TEXT' in str(c[2]).upper() else 0) for c in columns}
        values.update(matter=mid, folder='INBOX', uid='1', sender='client@example.test', mail_date='2026-10-01')
        desk.db.execute('INSERT INTO portfolio_mail_links(%s) VALUES(%s)' % (','.join(values), ','.join('?' * len(values))), list(values.values()))
        desk.db.commit()

        class Dav:
            pass
        with patch('agent.document_projects._inventory', return_value=[{'path': load_matters(self.f.c)[0]['path'] + '/piece.pdf', 'etag': 'e', 'size': 1}]):
            data = sources5614.snapshot(desk, mid, dav=Dav())
        self.assertEqual((data['files'], data['mails'], data['events']), ([], [], []))
        self.assertTrue(any('Ce que l’IA peut voir' in n for n in data['notes']))

    def test_settings_page_is_for_the_administrator(self):
        lawyer = self.member_cookie(self.member())
        self.assertEqual(self.request('/parametres/ia-visible', cookie=lawyer)['status'], '403 Forbidden')
        body = {c: c != 'courriels' for c in vue.IDS}
        self.assertEqual(self.api('m5688s/admin/vue-ia', body, lawyer)['status'], '403 Forbidden')
        admin = self.admin_cookie()
        page = self.request('/parametres/ia-visible', cookie=admin)
        for label in vue.LABELS.values():
            self.assertIn(label, page['body'])
        self.assertIn('ne couvre pas', page['body'])
        self.assertEqual(self.api('m5688s/admin/vue-ia', body, admin)['status'], '200 OK')
        self.assertTrue(vue.blocked(Desk(self.f.c), 'courriels'))


class Menu(unittest.TestCase):
    def test_registry_is_complete(self):
        from agent import fonctions5631, fonctions5634
        problems = [p for p in fonctions5631.audit() if p[0] in ('mon_compte', 'journal_audit', 'vue_ia')]
        self.assertEqual(problems, [])
        for fid in ('mon_compte', 'journal_audit', 'vue_ia'):
            self.assertIn(fid, fonctions5634.MIGRATION)


if __name__ == '__main__':
    unittest.main()
