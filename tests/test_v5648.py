"""5.6.48 : page de connexion de la version serveur (connexion, premier compte, déconnexion, mot de passe oublié, comptes du cabinet).

Données d'essai fictives (domaine example.test, identifiants et mots de passe d'essai).
"""
import base64
from datetime import datetime, timedelta, timezone
import hashlib
import io
import json
from pathlib import Path
import unittest
from unittest.mock import patch
from urllib.parse import urlencode, urlsplit, parse_qs

from agent import connexion5648 as cx
from agent.common import private_json
import test_v440_web as tw

PREFIX = '/agent-courriel'


class Base(unittest.TestCase):
    """Application réelle (WSGI) en version serveur, compte administrateur d'essai."""
    PATH = tw.WebWorkshopTests.PATH
    setUp = tw.WebWorkshopTests.setUp
    call = tw.WebWorkshopTests.call

    @property
    def state(self):
        return Path(self.f.c['state_dir'])

    def request(self, path, method='GET', form=None, cookie='', query='', origin=True, extra=None):
        raw = urlencode(form or {}).encode()
        env = {'REQUEST_METHOD': method, 'PATH_INFO': PREFIX + path, 'QUERY_STRING': query, 'HTTP_HOST': 'cabinet.example.test',
               'HTTP_X_FORWARDED_PROTO': 'https', 'wsgi.url_scheme': 'http', 'wsgi.input': io.BytesIO(raw), 'CONTENT_LENGTH': str(len(raw)),
               'CONTENT_TYPE': 'application/x-www-form-urlencoded', 'REMOTE_ADDR': '192.0.2.10'}
        if cookie:
            env['HTTP_COOKIE'] = '%s=%s' % (cx.COOKIE, cookie)
        if origin:
            env['HTTP_ORIGIN'] = self.origin
        env.update(extra or {})
        out = {}

        def start(status, headers):
            out.update(status=status, headers=dict(headers), raw_headers=list(headers))
        out['body'] = b''.join(self.app(env, start)).decode('utf-8', 'replace')
        out['cookies'] = [v for n, v in out['raw_headers'] if n.lower() == 'set-cookie']
        return out

    def api(self, route, body, cookie):
        return self.call('/api440/' + route, 'POST', body, auth=False, extra={'HTTP_COOKIE': '%s=%s' % (cx.COOKIE, cookie)})

    def login(self, ident='admin', password=None, suite='', remember=False):
        form = {'ident': ident, 'password': self.password if password is None else password, 'suite': suite}
        if remember:
            form['remember'] = '1'
        return self.request('/connexion', 'POST', form)

    @staticmethod
    def token(response):
        for value in response['cookies']:
            name, _, rest = value.partition('=')
            if name == cx.COOKIE:
                return rest.split(';')[0]
        return ''

    def admin_cookie(self):
        r = self.login()
        self.assertEqual(r['status'], '303 See Other', r['body'])
        return self.token(r)


class LoginPage(Base):
    def test_unauthenticated_page_redirects_to_login_and_api_answers_json(self):
        r = self.request('/courriels', query='vue=toutes')
        self.assertEqual(r['status'], '303 See Other')
        self.assertNotIn('WWW-Authenticate', r['headers'])
        location = r['headers']['Location']
        self.assertTrue(location.startswith(PREFIX + '/connexion?suite='))
        self.assertEqual(parse_qs(urlsplit(location).query)['suite'][0], PREFIX + '/courriels?vue=toutes')
        api = self.request('/api440/drafts')
        self.assertTrue(api['status'].startswith('401'))
        self.assertEqual(json.loads(api['body'])['error'], 'authentification_requise')
        self.assertNotIn('WWW-Authenticate', api['headers'])

    def test_login_page_is_complete_and_self_contained(self):
        r = self.request('/connexion')
        self.assertEqual(r['status'], '200 OK')
        for text in ('Se connecter', 'Mot de passe oublié', 'Se souvenir de moi', 'autocomplete="current-password"', 'connexion5648.css',
                     'hreflang="en"', 'hreflang="de"', 'hreflang="es"'):
            self.assertIn(text, r['body'])
        self.assertIn("frame-ancestors 'none'", r['headers']['Content-Security-Policy'])
        self.assertNotIn('<script', r['body'])
        css = self.request('/static/connexion5648.css')
        self.assertEqual(css['status'], '200 OK')
        self.assertIn('prefers-color-scheme: dark', css['body'])

    def test_login_opens_a_session_and_returns_to_the_requested_page(self):
        r = self.login(suite=PREFIX + '/documents')
        self.assertEqual(r['status'], '303 See Other')
        self.assertEqual(r['headers']['Location'], PREFIX + '/documents')
        cookie = r['cookies'][0]
        for part in ('HttpOnly', 'SameSite=Lax', 'Secure', 'Path=%s/' % PREFIX):
            self.assertIn(part, cookie)
        self.assertNotIn('Max-Age', cookie)                                  # session du navigateur sans « Se souvenir de moi »
        page = self.request('/courriels', cookie=self.token(r))
        self.assertTrue(page['status'].startswith('200'), page['status'])
        self.assertIn('Se déconnecter', page['body'])
        self.assertIn('action="%s/deconnexion"' % PREFIX, page['body'])
        db = cx.connect(self.state)
        try:
            stored = db.execute('SELECT token_hash FROM sessions').fetchall()
        finally:
            db.close()
        self.assertEqual([row[0] for row in stored], [hashlib.sha256(self.token(r).encode()).hexdigest()])   # jeton jamais stocké en clair

    def test_remember_me_sets_a_lasting_cookie(self):
        r = self.login(remember=True)
        self.assertIn('Max-Age=%d' % (cx.REMEMBER_DAYS * 86400), r['cookies'][0])

    def test_wrong_password_and_rate_limit(self):
        bad = self.login(password='mauvais-mot-de-passe')
        self.assertEqual(bad['status'], '401 Unauthorized')
        self.assertIn('Identifiant ou mot de passe incorrect', bad['body'])
        self.assertEqual(bad['cookies'], [])
        unknown = self.login(ident='inconnu@example.test', password='x')
        self.assertIn('Identifiant ou mot de passe incorrect', unknown['body'])   # même message, que le compte existe ou non
        for _ in range(4):
            self.login(password='mauvais-mot-de-passe')
        blocked = self.login()                                                    # bon mot de passe, mais trop de tentatives
        self.assertEqual(blocked['status'], '429 Too Many Requests')
        self.assertEqual(self.token(blocked), '')

    def test_return_address_stays_inside_axiorhub(self):
        for value in ('https://autre.example.test/', '//autre.example.test/x', PREFIX + '\\..\\x', '/ailleurs', 'javascript:alert(1)'):
            self.assertEqual(cx._next_path(PREFIX, value), PREFIX + '/aujourdhui', value)
        self.assertEqual(cx._next_path(PREFIX, PREFIX + '/dossiers?x=1'), PREFIX + '/dossiers?x=1')
        r = self.login(suite='https://autre.example.test/')
        self.assertEqual(r['headers']['Location'], PREFIX + '/aujourdhui')

    def test_cross_origin_form_is_refused(self):
        r = self.request('/connexion', 'POST', {'ident': 'admin', 'password': self.password}, extra={'HTTP_ORIGIN': 'https://autre.example.test'})
        self.assertEqual(r['status'], '403 Forbidden')
        self.assertEqual(self.token(r), '')

    def test_logout_closes_the_session(self):
        cookie = self.admin_cookie()
        self.assertEqual(self.request('/deconnexion', cookie=cookie)['status'], '303 See Other')   # GET : aucune déconnexion
        self.assertTrue(self.request('/courriels', cookie=cookie)['status'].startswith('200'))
        out = self.request('/deconnexion', 'POST', cookie=cookie)
        self.assertEqual(out['headers']['Location'], PREFIX + '/connexion?sortie=1')
        self.assertIn('Max-Age=0', out['cookies'][0])
        self.assertEqual(self.request('/courriels', cookie=cookie)['status'], '303 See Other')
        self.assertIn('Vous êtes déconnecté', self.request('/connexion', query='sortie=1')['body'])

    def test_login_pages_follow_the_chosen_language(self):
        for lang, words in (('en', ('Sign in', 'Forgot your password?', 'Remember me on this device (30 days)', 'free software (AGPL-3.0)')),
                            ('de', ('Anmelden', 'Passwort vergessen?')), ('es', ('Iniciar sesión', '¿Olvidó su contraseña?'))):
            r = self.request('/connexion', query='lang=' + lang)
            self.assertIn('<html lang="%s">' % lang, r['body'])
            self.assertIn('AxiorHub Pilote', r['body'])                                   # le nom du logiciel ne se traduit pas
            for word in words:
                self.assertIn(word, r['body'], (lang, word))
            self.assertTrue(any(v.startswith('axh_lang=' + lang) for v in r['cookies']))
        cookie = self.admin_cookie()
        page = self.request('/courriels', extra={'HTTP_COOKIE': '%s=%s; axh_lang=en' % (cx.COOKIE, cookie)})
        self.assertIn('>Sign out</button>', page['body'])

    def test_basic_header_still_accepted_for_scripts(self):
        self.assertTrue(self.call('/courriels')['status'].startswith('200'))

    def test_admin_password_change_closes_admin_sessions(self):
        cookie = self.admin_cookie()
        cx._write_admin(self.state, 'admin', 'nouveau-mot-de-passe-fictif-long')
        self.assertEqual(self.request('/courriels', cookie=cookie)['status'], '303 See Other')
        self.assertEqual(self.login(password='nouveau-mot-de-passe-fictif-long')['status'], '303 See Other')

    def test_other_modes_keep_their_own_access(self):
        self.assertFalse(cx.enabled({'poste': True, 'hash': 'x', 'salt': 'y'}))
        self.assertFalse(cx.enabled({'exapp': True}))
        self.assertFalse(cx.enabled(None))
        with patch('agent.comptes5642.standalone', return_value=True):
            self.assertFalse(cx.enabled({'hash': 'x', 'salt': 'y'}))
        self.assertTrue(cx.enabled({'hash': 'x', 'salt': 'y'}))


class FirstRun(Base):
    def setUp(self):
        super().setUp()
        data = json.loads(Path(self.auth).read_text(encoding='utf-8'))
        for key in ('salt', 'hash'):
            data.pop(key)
        private_json(self.auth, data)

    def test_first_visit_creates_the_admin_account_with_the_server_code(self):
        r = self.request('/courriels')
        self.assertEqual(r['headers']['Location'], PREFIX + '/connexion/premier-compte')
        self.assertEqual(self.request('/connexion')['headers']['Location'], PREFIX + '/connexion/premier-compte')
        self.assertTrue(self.call('/courriels')['status'].startswith(('401', '303')))      # aucun accès « Basic » sans compte
        page = self.request('/connexion/premier-compte')
        self.assertEqual(page['status'], '200 OK')
        self.assertIn('Créer le compte administrateur', page['body'])
        self.assertIn(cx.CODE_NAME, page['body'])
        code_file = self.state / cx.CODE_NAME
        code = code_file.read_text(encoding='utf-8').strip()
        self.assertNotIn(code, page['body'])                                               # le code ne s'affiche jamais
        password = 'premier-mot-de-passe-fictif'
        form = {'code': 'faux-code', 'ident': 'maitre', 'email': 'maitre@example.test', 'password': password, 'confirm': password}
        wrong = self.request('/connexion/premier-compte', 'POST', form)
        self.assertEqual(wrong['status'], '400 Bad Request')
        self.assertIn('Code d’installation incorrect', wrong['body'])
        short = self.request('/connexion/premier-compte', 'POST', {**form, 'code': code, 'password': 'court', 'confirm': 'court'})
        self.assertIn('16 caractères', short['body'])
        ok = self.request('/connexion/premier-compte', 'POST', {**form, 'code': code})
        self.assertEqual(ok['headers']['Location'], PREFIX + '/connexion?nouveau=1')
        self.assertFalse(code_file.exists())
        self.assertTrue((self.state / 'compte-interface5642.json').is_file())
        self.assertNotIn('hash', json.loads(Path(self.auth).read_text(encoding='utf-8')))   # ui-auth.json jamais réécrit
        again = self.request('/connexion/premier-compte', 'POST', {**form, 'code': code})
        self.assertEqual(again['headers']['Location'], PREFIX + '/connexion')               # une seule création
        r = self.request('/connexion', 'POST', {'ident': 'maitre@example.test', 'password': password})   # adresse acceptée comme identifiant
        self.assertEqual(r['status'], '303 See Other')
        self.assertTrue(self.request('/courriels', cookie=self.token(r))['status'].startswith('200'))


class ForgottenPassword(Base):
    def setUp(self):
        super().setUp()
        self.sent = []
        p = patch('agent.connexion5648.send_mail', side_effect=lambda cfg, to, subject, body: self.sent.append((to, body)) or True)
        p.start()
        self.addCleanup(p.stop)

    def link(self):
        to, body = self.sent[-1]
        return to, next(line for line in body.splitlines() if '/connexion/nouveau?jeton=' in line).strip()

    def test_same_answer_whether_the_account_exists_or_not(self):
        cookie = self.admin_cookie()
        self.assertTrue(self.api('m5636/comptes/recuperation', {'email': 'administrateur@example.test'}, cookie)['status'].startswith('200'))
        known = self.request('/connexion/oubli', 'POST', {'ident': 'admin'})
        unknown = self.request('/connexion/oubli', 'POST', {'ident': 'personne@example.test'})
        self.assertEqual(known['status'], unknown['status'])
        self.assertEqual(known['body'], unknown['body'])
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(self.sent[0][0], 'administrateur@example.test')

    def test_link_sets_a_new_admin_password_once(self):
        old_session = self.admin_cookie()
        db = cx.connect(self.state)
        try:
            cx.set_admin_email(db, 'administrateur@example.test')
        finally:
            db.close()
        self.request('/connexion/oubli', 'POST', {'ident': 'administrateur@example.test'})
        to, link = self.link()
        self.assertTrue(link.startswith(self.origin + PREFIX + '/connexion/nouveau?jeton='))
        token = parse_qs(urlsplit(link).query)['jeton'][0]
        form = self.request('/connexion/nouveau', query='jeton=' + token)
        self.assertEqual(form['status'], '200 OK')
        self.assertIn('16 caractères', form['body'])
        new = 'mot-de-passe-retrouve-fictif'
        done = self.request('/connexion/nouveau', 'POST', {'jeton': token, 'password': new, 'confirm': new})
        self.assertEqual(done['headers']['Location'], PREFIX + '/connexion?nouveau=1')
        self.assertEqual(self.request('/courriels', cookie=old_session)['status'], '303 See Other')    # anciennes sessions fermées
        self.assertEqual(self.login()['status'], '401 Unauthorized')                                     # ancien mot de passe refusé
        self.assertEqual(self.login(password=new)['status'], '303 See Other')
        reuse = self.request('/connexion/nouveau', 'POST', {'jeton': token, 'password': new + 'x', 'confirm': new + 'x'})
        self.assertIn('n’est plus valable', reuse['body'])

    def test_expired_link_and_account_without_address(self):
        self.request('/connexion/oubli', 'POST', {'ident': 'admin'})
        self.assertEqual(self.sent, [])                                       # aucune adresse de récupération : rien n'est envoyé
        db = cx.connect(self.state)
        try:
            cx.set_admin_email(db, 'administrateur@example.test')
        finally:
            db.close()
        self.request('/connexion/oubli', 'POST', {'ident': 'admin'})
        token = parse_qs(urlsplit(self.link()[1]).query)['jeton'][0]
        db = cx.connect(self.state)
        try:
            db.execute('UPDATE resets SET expires=?', ((datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(),))
            db.commit()
        finally:
            db.close()
        r = self.request('/connexion/nouveau', query='jeton=' + token)
        self.assertEqual(r['status'], '400 Bad Request')
        self.assertIn('Demander un nouveau lien', r['body'])

    def test_reset_requests_are_limited(self):
        db = cx.connect(self.state)
        try:
            cx.set_admin_email(db, 'administrateur@example.test')
        finally:
            db.close()
        for _ in range(5):
            self.request('/connexion/oubli', 'POST', {'ident': 'admin'})
        self.assertEqual(len(self.sent), cx.LIMITS['reset'][0])


class CabinetAccounts(Base):
    def add(self, cookie, ident='avocate@example.test', role='avocat', password='provisoire-fictif-1'):
        r = self.api('m5636/comptes/ajouter', {'ident': ident, 'name': 'Me Avocate Fictive', 'role': role, 'password': password}, cookie)
        self.assertTrue(r['status'].startswith('200'), r['body'])
        return password

    def first_login(self, ident, provisional, final='personnel-fictif-22'):
        r = self.login(ident, provisional)
        self.assertEqual(r['headers']['Location'], PREFIX + '/connexion/changer')
        cookie = self.token(r)
        self.assertEqual(self.request('/courriels', cookie=cookie)['headers']['Location'], PREFIX + '/connexion/changer')
        changed = self.request('/connexion/changer', 'POST', {'password': final, 'confirm': final}, cookie=cookie)
        self.assertEqual(changed['headers']['Location'], PREFIX + '/connexion?nouveau=1')
        r = self.login(ident, final)
        self.assertEqual(r['status'], '303 See Other')
        return self.token(r)

    def test_settings_page_lists_accounts_for_the_admin(self):
        cookie = self.admin_cookie()
        page = self.request('/parametres', query='rubrique=comptes', cookie=cookie)['body']
        for text in ('Adresse de récupération', 'Comptes du cabinet', 'Ajouter un compte', 'page de connexion'):
            self.assertIn(text, page)
        self.add(cookie)
        page = self.request('/parametres', query='rubrique=comptes', cookie=cookie)['body']
        self.assertIn('avocate@example.test', page)
        self.assertIn('mot de passe à changer', page)
        dup = self.api('m5636/comptes/ajouter', {'ident': 'avocate@example.test', 'role': 'avocat', 'password': 'p' * 12}, cookie)
        self.assertIn('compte_existe_deja', dup['body'].decode())

    def test_lawyer_works_with_own_identity_and_limited_rights(self):
        admin = self.admin_cookie()
        provisional = self.add(admin)
        cookie = self.first_login('avocate@example.test', provisional)
        page = self.request('/courriels', cookie=cookie)
        self.assertTrue(page['status'].startswith('200'))
        self.assertIn('Me Avocate Fictive', page['body'])
        self.assertIn('Se déconnecter', page['body'])
        self.assertEqual(self.request('/parametres', cookie=cookie)['status'], '403 Forbidden')
        refused = self.api('m5636/comptes/ajouter', {'ident': 'autre@example.test', 'role': 'administrateur', 'password': 'p' * 12}, cookie)
        self.assertTrue(refused['status'].startswith('403'), refused['body'])

    def test_assistant_cannot_manage_accounts_and_deactivation_closes_sessions(self):
        admin = self.admin_cookie()
        provisional = self.add(admin, 'assistant.fictif', 'assistant')
        cookie = self.first_login('assistant.fictif', provisional)
        self.assertTrue(self.request('/courriels', cookie=cookie)['status'].startswith('200'))
        self.assertTrue(self.api('m5636/comptes/recuperation', {'email': 'x@example.test'}, cookie)['status'].startswith('403'))
        db = cx.connect(self.state)
        try:
            uid = db.execute('SELECT id FROM users WHERE ident=?', ('assistant.fictif',)).fetchone()[0]
        finally:
            db.close()
        self.assertTrue(self.api('m5636/comptes/modifier', {'id': uid, 'action': 'desactiver'}, admin)['status'].startswith('200'))
        self.assertEqual(self.request('/courriels', cookie=cookie)['status'], '303 See Other')
        self.assertEqual(self.login('assistant.fictif', 'personnel-fictif-22')['status'], '401 Unauthorized')

    def test_identity_headers_from_the_browser_are_never_trusted(self):
        auth = {'hash': 'h', 'salt': 's', 'origin': self.origin}
        admin = dict(cx.admin_user(auth), session='x')
        env = {'REQUEST_METHOD': 'GET', 'axiorhub.user': admin, 'HTTP_X_AXIORHUB_USER': 'autre', 'HTTP_X_AXIORHUB_ROLE': 'administrateur'}
        self.assertIsNone(self.app._identity5648(env, auth, PREFIX, '/courriels'))
        self.assertNotIn('HTTP_X_AXIORHUB_USER', env)
        self.assertEqual(auth['owner'], 'cabinet')
        lawyer = {'id': 3, 'ident': 'avocate@example.test', 'name': 'Me', 'role': 'avocat', 'owner': 'avocate@example.test', 'must_change': False}
        env = {'REQUEST_METHOD': 'GET', 'axiorhub.user': lawyer, 'HTTP_X_AXIORHUB_USER': 'cabinet', 'HTTP_X_AXIORHUB_ROLE': 'administrateur'}
        self.assertIsNone(self.app._identity5648(env, auth, PREFIX, '/courriels'))
        self.assertEqual((env['HTTP_X_AXIORHUB_USER'], env['HTTP_X_AXIORHUB_ROLE']), ('avocate@example.test', 'avocat'))
        self.assertEqual(self.app._identity5648(env, auth, PREFIX, '/parametres')['status'], '403 Forbidden')

    def test_user_block_escapes_names(self):
        block = cx.user_block(PREFIX, {'name': '<b>Me</b>', 'role': 'avocat'})
        self.assertIn('&lt;b&gt;Me&lt;/b&gt;', block)
        self.assertIn('method="post"', block)


if __name__ == '__main__':
    unittest.main()
