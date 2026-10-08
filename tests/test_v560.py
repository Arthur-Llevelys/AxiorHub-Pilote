"""5.6.0 « Distribution » : comptes et rôles du cabinet, assistant d'installation, ensemble VPS (Docker, Apache, Let's Encrypt),
mention d'auteur, identité du cabinet tirée de la configuration, absence de données personnelles dans le dépôt."""
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import urlencode
import zipfile

from agent import about560, cabinet560, routines520
from agent.standalone_auth import StandaloneAuth, allowed
import test_v510 as t510

ROOT = Path(__file__).resolve().parents[1]


def _scanner():
    spec = importlib.util.spec_from_file_location('privacy_scan', ROOT / 'scripts' / 'privacy-scan.py')
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class Roles(unittest.TestCase):
    def test_administrator_can_do_everything(self):
        for path in ('/parametres', '/comptes', '/installation', '/api440/m540/save'):
            self.assertTrue(allowed('administrateur', 'POST', path, 'save_ai_provider'))

    def test_lawyer_works_but_cannot_change_settings(self):
        self.assertTrue(allowed('avocat', 'GET', '/'))
        self.assertTrue(allowed('avocat', 'POST', '/action', 'approve_project'))
        self.assertTrue(allowed('avocat', 'POST', '/api440/m550/rule'))
        self.assertFalse(allowed('avocat', 'GET', '/parametres'))
        self.assertFalse(allowed('avocat', 'GET', '/ia-externe'))
        self.assertFalse(allowed('avocat', 'POST', '/action', 'save_ai_provider'))
        self.assertFalse(allowed('avocat', 'POST', '/api440/m540/provider'))
        self.assertFalse(allowed('avocat', 'GET', '/comptes'))

    def test_assistant_prepares_but_never_validates(self):
        self.assertTrue(allowed('assistant', 'GET', '/'))
        self.assertTrue(allowed('assistant', 'POST', '/action', 'create_agenda_event'))
        self.assertTrue(allowed('assistant', 'POST', '/api440/m530/ask'))
        self.assertTrue(allowed('assistant', 'POST', '/api440/m510/table'))
        self.assertFalse(allowed('assistant', 'POST', '/action', 'approve_project'))
        self.assertFalse(allowed('assistant', 'POST', '/api440/m550/rule'))
        self.assertFalse(allowed('assistant', 'POST', '/documents/edit'))
        self.assertFalse(allowed('inconnu', 'POST', '/action', 'create_agenda_event'))


class Inner:
    """Application interne factice : renvoie une page avec menu latéral et note les en-têtes reçus."""

    def __init__(self, config_path=None):
        if config_path:
            self.config_path = str(config_path)
        self.seen = []

    def __call__(self, env, start):
        self.seen.append({k: env.get(k) for k in ('PATH_INFO', 'HTTP_AUTHORIZATION', 'HTTP_X_AXIORHUB_USER', 'HTTP_X_AXIORHUB_ROLE')})
        start('200 OK', [('Content-Type', 'text/html; charset=utf-8')])
        return [b'<html><aside>menu</aside><main>travail</main></html>']


class Accounts(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        env = patch.dict(os.environ, {'AXIORHUB_ALLOW_SIGNUP': 'false', 'AXIORHUB_INTERNAL_API_TOKEN': 'jeton-interne-test-uniquement-synthetique-567'})
        env.start()
        self.addCleanup(env.stop)
        self.inner = Inner()
        self.app = StandaloneAuth(self.inner, self.tmp / 'auth', 'https://agent.example.test')
        self.addCleanup(self.app.db.close)

    def call(self, path, method='GET', data=None, cookie='', query=''):
        data = dict(data or {})
        if path == '/signup' and not self.app.db.execute('SELECT 1 FROM users').fetchone():
            data.setdefault('bootstrap_token', self.app.bootstrap.read_text().strip())
        raw = urlencode(data).encode()
        out = {}
        env = {'PATH_INFO': path, 'REQUEST_METHOD': method, 'QUERY_STRING': query, 'HTTP_COOKIE': cookie,
               'CONTENT_LENGTH': str(len(raw)), 'wsgi.input': io.BytesIO(raw), 'HTTP_ORIGIN': 'https://agent.example.test'}
        body = b''.join(self.app(env, lambda status, headers, exc=None: out.update(status=status, headers=dict(headers))))
        out['body'] = body.decode()
        return out

    def login(self, email, password):
        r = self.call('/login', 'POST', {'email': email, 'password': password})
        self.assertTrue(r['status'].startswith('303'), r['body'][:300])
        return r['headers']['Set-Cookie'].split(';')[0]

    def csrf(self, cookie, path='/comptes'):
        return re.search(r'name="csrf" value="([^"]+)"', self.call(path, cookie=cookie)['body']).group(1)

    def test_first_account_is_administrator_then_signup_closes(self):
        self.assertIn('Créer le compte administrateur', self.call('/login')['body'])
        r = self.call('/signup', 'POST', {'name': 'Camille Exemple', 'email': 'avocat@example.test', 'password': 'motdepasse-solide-1'})
        self.assertTrue(r['status'].startswith('303'))
        row = self.app.db.execute('SELECT role, active FROM users').fetchone()
        self.assertEqual((row['role'], row['active']), ('administrateur', 1))
        closed = self.call('/signup', 'POST', {'name': 'Intrus', 'email': 'intrus@example.test', 'password': 'motdepasse-solide-2'})
        self.assertIn('inscriptions sont fermées', closed['body'])
        self.assertEqual(self.app.db.execute('SELECT COUNT(*) FROM users').fetchone()[0], 1)

    def test_open_signup_creates_inactive_assistant(self):
        self.call('/signup', 'POST', {'name': 'A', 'email': 'admin@example.test', 'password': 'motdepasse-solide-1'})
        with patch.dict(os.environ, {'AXIORHUB_ALLOW_SIGNUP': 'true'}):
            r = self.call('/signup', 'POST', {'name': 'B', 'email': 'b@example.test', 'password': 'motdepasse-solide-2'})
        self.assertIn('administrateur du cabinet doit activer', r['body'])
        row = self.app.db.execute("SELECT role, active FROM users WHERE email='b@example.test'").fetchone()
        self.assertEqual((row['role'], row['active']), ('assistant', 0))
        self.assertIn('Identifiants incorrects', self.call('/login', 'POST', {'email': 'b@example.test', 'password': 'motdepasse-solide-2'})['body'])

    def test_administrator_creates_members_and_roles_are_enforced(self):
        self.call('/signup', 'POST', {'name': 'Camille', 'email': 'admin@example.test', 'password': 'motdepasse-solide-1'})
        admin = self.login('admin@example.test', 'motdepasse-solide-1')
        page = self.call('/comptes', cookie=admin)['body']
        self.assertIn('Ajouter un membre du cabinet', page)
        created = self.call('/comptes', 'POST', {'csrf': self.csrf(admin), 'op': 'create', 'name': 'Dominique', 'email': 'assistant@example.test',
                                                 'role': 'assistant'}, cookie=admin)['body']
        temp = re.search(r'<code>([^<]+)</code>', created).group(1)
        self.assertGreaterEqual(len(temp), 12)
        # formulaire sans jeton : refusé
        self.assertTrue(self.call('/comptes', 'POST', {'op': 'create', 'email': 'x@example.test', 'role': 'avocat'}, cookie=admin)['status'].startswith('400'))
        # première connexion : changement de mot de passe obligatoire
        member = self.login('assistant@example.test', temp)
        self.assertEqual(self.call('/', cookie=member)['headers']['Location'], '/mot-de-passe')
        done = self.call('/mot-de-passe', 'POST', {'csrf': self.csrf(member, '/mot-de-passe'), 'password': 'nouveau-secret-12', 'confirm': 'nouveau-secret-12'},
                         cookie=member)
        self.assertTrue(done['status'].startswith('303'))
        # rôle assistant(e) : pages et actions réservées refusées côté serveur, travail courant transmis à l'application
        self.assertTrue(self.call('/comptes', cookie=member)['status'].startswith('403'))
        self.assertTrue(self.call('/parametres', cookie=member)['status'].startswith('403'))
        self.assertTrue(self.call('/action', 'POST', {'action': 'approve_project'}, cookie=member)['status'].startswith('403'))
        api = self.call('/api440/m540/provider', 'POST', {'x': '1'}, cookie=member)
        self.assertTrue(api['status'].startswith('403'))
        self.assertEqual(json.loads(api['body'])['error'], 'role_insuffisant')
        ok = self.call('/action', 'POST', {'action': 'create_agenda_event', 'title': 'Audience'}, cookie=member)
        self.assertTrue(ok['status'].startswith('200'))
        last = self.inner.seen[-1]
        self.assertEqual((last['HTTP_X_AXIORHUB_USER'], last['HTTP_X_AXIORHUB_ROLE']), ('assistant@example.test', 'assistant'))
        self.assertEqual(last['HTTP_AUTHORIZATION'], 'Bearer jeton-interne-test-uniquement-synthetique-567')
        self.assertIn('Assistant(e)', ok['body'])
        self.assertNotIn('href="/comptes"', ok['body'])
        self.assertIn('href="/comptes"', self.call('/', cookie=admin)['body'])
        # changement de rôle, désactivation, journal
        uid = self.app.db.execute("SELECT id FROM users WHERE email='assistant@example.test'").fetchone()['id']
        self.call('/comptes', 'POST', {'csrf': self.csrf(admin), 'op': 'role', 'id': uid, 'role': 'avocat'}, cookie=admin)
        self.assertEqual(self.app.db.execute('SELECT role FROM users WHERE id=?', (uid,)).fetchone()['role'], 'avocat')
        self.call('/comptes', 'POST', {'csrf': self.csrf(admin), 'op': 'disable', 'id': uid}, cookie=admin)
        self.assertIn('/login', self.call('/', cookie=member)['body'] + self.call('/', cookie=member).get('headers', {}).get('Location', '/login'))
        self.assertIn('Se connecter', self.call('/', cookie=member)['body'])
        events = [r['event'] for r in self.app.db.execute('SELECT event FROM account_log ORDER BY id')]
        self.assertEqual(events[:2], ['inscription', 'creation'])
        self.assertIn('role', events)
        self.assertIn('disable', events)
        # son propre compte ne se modifie pas depuis la liste
        me = self.app.db.execute("SELECT id FROM users WHERE email='admin@example.test'").fetchone()['id']
        self.assertIn('votre propre compte', self.call('/comptes', 'POST', {'csrf': self.csrf(admin), 'op': 'disable', 'id': me}, cookie=admin)['body'])

    def test_static_files_and_about_page_need_no_login(self):
        self.assertTrue(self.call('/a-propos')['status'].startswith('200'))
        self.assertEqual(self.inner.seen[-1]['PATH_INFO'], '/a-propos')
        self.assertIn('créé par Timo RAINIO', self.call('/login')['body'])


class Installation(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(tmp.cleanup)
        self.data = Path(tmp.name) / 'data'
        env = dict(os.environ, AXIORHUB_DATA_DIR=str(self.data), AXIORHUB_INTERNAL_API_TOKEN='jeton-interne-test-uniquement-synthetique-567')
        subprocess.run([sys.executable, str(ROOT / 'docker' / 'bootstrap.py')], env=env, check=True, capture_output=True)
        cfg = json.loads((self.data / 'config.json').read_text())
        cfg['state_dir'] = str(self.data / 'state')
        (self.data / 'config.json').write_text(json.dumps(cfg))
        patcher = patch.dict(os.environ, {'AXIORHUB_ALLOW_SIGNUP': 'false', 'AXIORHUB_INTERNAL_API_TOKEN': 'jeton-interne-test-uniquement-synthetique-567'})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.inner = Inner(self.data / 'config.json')
        self.app = StandaloneAuth(self.inner, self.data / 'auth', 'https://agent.example.test')
        self.addCleanup(self.app.db.close)
        Accounts.call(self, '/signup', 'POST', {'bootstrap_token': self.app.bootstrap.read_text().strip(), 'name': 'Camille', 'email': 'admin@example.test', 'password': 'motdepasse-solide-1'})
        self.admin = Accounts.login(self, 'admin@example.test', 'motdepasse-solide-1')

    call = Accounts.call

    def cfg(self):
        return json.loads((self.data / 'config.json').read_text())

    def test_fresh_configuration_needs_the_wizard_and_existing_one_does_not(self):
        self.assertNotIn('installation', self.cfg())
        self.assertEqual(self.call('/', cookie=self.admin)['headers']['Location'], '/installation')
        # mise à jour d'une installation déjà renseignée : pas d'assistant
        cfg = self.cfg()
        cfg['mail']['host'] = 'imap.cabinet.test'
        (self.data / 'config.json').write_text(json.dumps(cfg))
        env = dict(os.environ, AXIORHUB_DATA_DIR=str(self.data))
        subprocess.run([sys.executable, str(ROOT / 'docker' / 'bootstrap.py')], env=env, check=True, capture_output=True)
        self.assertTrue(self.cfg()['installation']['done'])
        self.assertTrue(self.call('/', cookie=self.admin)['status'].startswith('200'))

    def test_wizard_saves_cabinet_connections_and_finishes(self):
        page = self.call('/installation', cookie=self.admin)['body']
        self.assertIn('1 · Cabinet', page)
        self.assertIn('pseudonymisées sur le serveur', page)
        csrf = re.search(r'name="csrf" value="([^"]+)"', page).group(1)
        form = {'csrf': csrf, 'op': 'save', 'prenom': 'Camille', 'nom': 'Exemple', 'barreau': 'Lyon', 'adresse': '1 place Fictive 69000 Lyon',
                'telephone': '04 00 00 00 00', 'email': 'cabinet@example.test', 'specialite': 'droit des affaires',
                'imap_host': 'imap.cabinet.test', 'imap_user': 'cabinet@example.test', 'imap_password': 'secret-imap',
                'nc_url': 'https://cloud.cabinet.test', 'nc_user': 'axiorhub', 'nc_password': 'secret-nextcloud', 'nc_root': 'Dossiers',
                'ai': 'mixte', 'provider': 'anthropic', 'provider_key': 'cle-de-test', 'mode': 'observe'}
        r = self.call('/installation', 'POST', form, cookie=self.admin)
        self.assertIn('cochez l’autorisation', r['body'])               # IA externe sans consentement : non activée
        cfg = self.cfg()
        self.assertEqual(cfg['mail']['host'], 'imap.cabinet.test')
        self.assertEqual(cfg['nextcloud']['roots'], ['/Dossiers'])
        self.assertEqual(cfg['mode'], 'observe')
        self.assertNotIn('secret-imap', json.dumps(cfg))
        self.assertEqual(__import__('agent.common',fromlist=['read_secret']).read_secret(cfg['mail']['password_file']), 'secret-imap')
        self.assertEqual(__import__('agent.common',fromlist=['read_secret']).read_secret(cfg['nextcloud']['password_file']), 'secret-nextcloud')
        from agent.common import load_config
        from agent.desk import Desk
        desk = Desk(load_config(str(self.data / 'config.json')))
        self.addCleanup(desk.db.close)
        ident = cabinet560.identity(desk)
        self.assertEqual(ident['title'], 'Maître Camille Exemple')
        self.assertEqual(ident['specialty'], 'droit des affaires')
        self.assertTrue(ident['configured'])
        # formulaire forgé ailleurs : refusé
        self.assertTrue(self.call('/installation', 'POST', dict(form, csrf='faux'), cookie=self.admin)['status'].startswith('400'))
        # fin de l'installation : la recette impose des lectures de connexion réelles.
        from agent import setup560
        with patch('agent.web520.check_imap', return_value={'message':'Dossiers IMAP relus'}), \
             patch('agent.dav.DAV') as dav, \
             patch('agent.queue521.check_ai', return_value={'message':'Réponse IA testée'}):
            dav.return_value.list_folder.return_value = []
            dav.return_value.calendars.return_value = []
            for connector in ('imap','nextcloud','ia'):
                self.assertTrue(setup560.test(self.app,connector)[0])
        csrf = re.search(r'name="csrf" value="([^"]+)"', self.call('/installation', cookie=self.admin)['body']).group(1)
        done = self.call('/installation', 'POST', dict(form, csrf=csrf, op='finish', ai='local'), cookie=self.admin)
        self.assertTrue(done['status'].startswith('303'), done['body'][:400])
        self.assertTrue(self.cfg()['installation']['done'])
        self.assertEqual(self.cfg()['installation']['by'], 'admin@example.test')
        self.assertTrue(self.call('/', cookie=self.admin)['status'].startswith('200'))

    def test_finish_refuses_example_connections(self):
        csrf = re.search(r'name="csrf" value="([^"]+)"', self.call('/installation', cookie=self.admin)['body']).group(1)
        r = self.call('/installation', 'POST', {'csrf': csrf, 'op': 'finish', 'prenom': 'C', 'nom': 'E', 'barreau': 'Lyon'}, cookie=self.admin)
        self.assertIn('À compléter avant de terminer : messagerie, Nextcloud.', r['body'])
        self.assertNotIn('installation', {k: v for k, v in self.cfg().items() if k == 'installation' and v.get('done')})


class Identity(t510.Base):
    def test_signature_and_prompts_come_from_the_profile(self):
        ident = cabinet560.identity(self.desk)
        self.assertEqual(ident['name'], 'Camille Exemple')
        self.assertIn('Avocat au Barreau de Lyon', ident['signature'])
        self.assertIn('Maître Camille Exemple, avocat au Barreau de Lyon', cabinet560.writer_intro(self.desk))
        s = routines520.settings(self.desk)
        self.assertEqual(s['ressort'], 'Lyon')
        self.assertIn('Camille Exemple', s['signature'])

    def test_empty_profile_gives_neutral_wording(self):
        from agent import pieces510
        pieces510.save_profile(self.desk, {k: '' for k in t510.PROFILE})
        self.assertEqual(cabinet560.writer_intro(self.desk), 'l’avocat du cabinet')
        self.assertFalse(cabinet560.identity(self.desk)['configured'])
        self.assertEqual(routines520.settings(self.desk)['signature'], '')

    def test_about_page_and_sidebar_carry_the_attribution(self):
        about = self.request('/a-propos')['body']
        self.assertIn('AxiorHub Pilote — créé par Timo RAINIO', about)
        self.assertIn('article 7 b', about)
        self.assertIn('AGPL', about)
        home = self.request('/')['body']
        self.assertIn('AxiorHub Pilote — créé par Timo RAINIO', home)
        self.assertIn('Version 5.6.17', home)
        with patch.dict(os.environ, {'AXIORHUB_SOURCE_URL': 'https://git.example.test/axiorhub'}):
            self.assertIn('href="https://git.example.test/axiorhub"', self.request('/a-propos')['body'])


class PublicRepository(unittest.TestCase):
    def test_no_personal_data_secret_or_client_matter_in_the_repository(self):
        self.assertEqual(_scanner().scan(ROOT), [])

    def test_scanner_catches_real_data(self):
        scan = _scanner()
        planted = ('Me Dupont, 12 rue Réelle, tél. 06 71 ' + '23 45 67, jean.dupont@' + 'gmail.com, serveur 51.91.' + '12.34, '
                   '/var/www/html/next' + 'cloud2025/data/Je' + 'an/files/, sk-ant-' + 'a' * 30 + ', ' + 'Ti' + 'mo')
        found = ' | '.join(scan.scan_text('agent/x.py', planted))
        for label in ('numéro de téléphone', 'adresse électronique', 'adresse IP', 'chemin de serveur', "clé d'API", "nom de l'auteur"):
            self.assertIn(label, found)
        self.assertEqual(scan.scan_text('agent/x.py', 'cabinet@example.test 06 00 00 00 01 127.0.0.1 version 5.6.0.1'), [])
        self.assertEqual(scan.scan_text('NOTICE', 'créé par ' + 'Ti' + 'mo RAI' + 'NIO'), [])
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / '.privacy-denylist').write_text('# clients\nSCI DES TILLEULS FICTIFS\n', encoding='utf-8')
            (root / 'a.py').write_text("DOSSIER = 'sci des tilleuls fictifs'\n", encoding='utf-8')
            with zipfile.ZipFile(root / 'modele.docx', 'w') as z:
                z.writestr('docProps/core.xml', '<cp:coreProperties><dc:creator>jean.dupont@' + 'gmail.com</dc:creator></cp:coreProperties>')
            (root / '.env').write_text('X=1\n')
            found = '\n'.join(scan.scan(root))
        self.assertIn('a.py : terme de la liste personnelle', found)
        self.assertIn('modele.docx : adresse électronique', found)
        self.assertIn('.env : fichier .env', found)

    def test_document_templates_are_fictitious(self):
        for name in ('MODELE_BCP.docx', 'MODELE_CONCLUSIONS.docx', 'MODELE_BCP_BALISES.docx'):
            with zipfile.ZipFile(ROOT / 'templates' / name) as z:
                core = z.read('docProps/core.xml').decode() if 'docProps/core.xml' in z.namelist() else ''
                text = z.read('word/document.xml').decode()
            self.assertNotRegex(core, r'(?i)<dc:creator>(?!AxiorHub)[^<]+')
            self.assertNotRegex(text, r'(?i)\b(?:n[ée]e? le \d|RG\s*n?°?\s*\d{2}/\d{4,})')

    def test_attribution_and_licence_files(self):
        notice = (ROOT / 'NOTICE').read_text(encoding='utf-8')
        self.assertIn('« AxiorHub Pilote — créé par Timo RAINIO »', notice)
        self.assertIn('7 b)', notice)
        self.assertIn('AGPL-3.0-or-later', notice)
        self.assertEqual(about560.ATTRIBUTION, 'AxiorHub Pilote — créé par Timo RAINIO')
        self.assertIn('Timo RAINIO', (ROOT / 'AUTHORS.md').read_text(encoding='utf-8'))
        readme = (ROOT / 'README.md').read_text(encoding='utf-8')
        for doc in ('docs/INSTALLATION-VPS.md', 'docs/INSTALLATION-SERVEUR.md', 'docs/COMPTES-ET-ROLES.md', 'docs/CONFIDENTIALITE.md',
                    'docs/ARCHITECTURE.md', 'DOCKER-INSTALLATION.md', 'CHANGELOG.md'):
            self.assertIn(doc, readme)
            self.assertTrue((ROOT / doc).is_file(), doc)
        ignored = (ROOT / '.gitignore').read_text().split()
        for entry in ('/.env', '/data/', '/deploy/vps/.env', '/deploy/vps/data/', '*.sqlite3', '.privacy-denylist'):
            self.assertIn(entry, ignored)
        self.assertEqual(list(ROOT.glob('MISE-A-JOUR-*.md')) + list(ROOT.glob('VALIDATION-*.md')) + list(ROOT.glob('GUIDE-*.md')), [])


class VPS(unittest.TestCase):
    base = ROOT / 'deploy' / 'vps'

    def test_services_listen_only_locally(self):
        compose = (self.base / 'docker-compose.yml').read_text(encoding='utf-8')
        ports = re.findall(r'^\s+- "([^"]+)"$', compose, re.M)
        published = [p for p in ports if re.match(r'^[\d.${}:A-Z_-]+:\d+$', p)]
        self.assertTrue(published)
        self.assertTrue(all(p.startswith('127.0.0.1:') for p in published), published)
        for service in ('axiorhub:', 'axiorhub-worker:', 'axiorhub-watcher:', 'nextcloud:', 'nextcloud-cron:', 'roundcube:', 'onlyoffice:', 'ollama:'):
            self.assertIn('\n  ' + service, compose)
        self.assertIn('profiles: ["ollama"]', compose)

    def test_environment_template_holds_no_secret(self):
        env = (self.base / 'env.example').read_text(encoding='utf-8')
        for key in ('NEXTCLOUD_ADMIN_PASSWORD', 'MYSQL_PASSWORD', 'MYSQL_ROOT_PASSWORD', 'ONLYOFFICE_JWT_SECRET', 'AXIORHUB_INTERNAL_API_TOKEN'):
            self.assertIn(key + '=a-generer', env)
        self.assertIn('AXIORHUB_ALLOW_SIGNUP=false', env)
        self.assertIn('AXIORHUB_ALLOW_SIGNUP=false', (ROOT / '.env.example').read_text(encoding='utf-8'))

    def test_apache_virtual_hosts_are_ready_for_certbot(self):
        expected = {'axiorhub.conf': '8626', 'nextcloud.conf': '8080', 'roundcube.conf': '8081', 'office.conf': '8082'}
        for name, port in expected.items():
            conf = (self.base / 'apache' / name).read_text(encoding='utf-8')
            self.assertIn('<VirtualHost *:80>', conf)
            self.assertIn('ServerName __DOMAIN__', conf)
            self.assertIn('ProxyPass /.well-known/acme-challenge !', conf)
            if name=='axiorhub.conf':
                self.assertIn('http://127.0.0.1:__PORT__/',conf)
                self.assertIn('http://127.0.0.1:9126/',conf.replace('__PORT__','9126'))
                self.assertIn('s|__PORT__|$AXIORHUB_PORT|g',(self.base/'install-vps.sh').read_text())
            else:
                self.assertIn('http://127.0.0.1:%s/' % port, conf)
            self.assertIn('X-Forwarded-Proto "https"', conf)
        self.assertIn('ws://127.0.0.1:8082/', (self.base / 'apache' / 'office.conf').read_text(encoding='utf-8'))
        self.assertIn('caldav', (self.base / 'apache' / 'nextcloud.conf').read_text(encoding='utf-8'))

    def test_install_script_generates_secrets_and_asks_before_lets_encrypt(self):
        script = (self.base / 'install-vps.sh').read_text(encoding='utf-8')
        self.assertTrue(script.startswith('#!/usr/bin/env bash\n'))
        self.assertIn('set -euo pipefail', script)
        self.assertIn('openssl rand', script)
        self.assertIn('chmod 600 "$ENV_FILE"', script)
        self.assertIn('a2enmod -q proxy proxy_http proxy_wstunnel headers rewrite ssl', script)
        self.assertIn('apachectl configtest', script)
        consent = script.index('Acceptez-vous ces conditions')
        certbot = script.index('certbot --apache -d "$d" --redirect --hsts')
        self.assertLess(consent, certbot)
        self.assertIn('nextcloud-post-install.sh', script)
        post = (self.base / 'nextcloud-post-install.sh').read_text(encoding='utf-8')
        for app in ('calendar', 'tasks', 'deck', 'onlyoffice'):
            self.assertIn(app, post)
        self.assertIn('jwt_secret', post)
        self.assertIn('maintenance:mode --off', (self.base / 'backup.sh').read_text(encoding='utf-8'))
        if os.name != 'nt' and Path('/bin/bash').exists():
            for name in ('install-vps.sh', 'nextcloud-post-install.sh', 'backup.sh'):
                subprocess.run(['bash', '-n', str(self.base / name)], check=True)


class Paths(unittest.TestCase):
    def test_roundcube_location_is_detected_not_hard_coded(self):
        sys.path.insert(0, str(ROOT))
        import localpaths
        with tempfile.TemporaryDirectory() as tmp:
            web = Path(tmp)
            (web / 'roundcube190' / 'config').mkdir(parents=True)
            (web / 'roundcube190' / 'config' / 'config.inc.php').write_text('<?php')
            (web / 'roundcube-vide').mkdir()
            with patch.dict(os.environ, {'AXIORHUB_ROUNDCUBE_ROOT': ''}):
                self.assertEqual(localpaths.roundcube_root(web), web / 'roundcube190')
                self.assertEqual(localpaths.roundcube_root(web / 'absent'), web / 'absent' / 'roundcube')
            with patch.dict(os.environ, {'AXIORHUB_ROUNDCUBE_ROOT': '/srv/webmail'}):
                self.assertEqual(localpaths.roundcube_root(web), Path('/srv/webmail'))

    def test_version(self):
        import upgrade
        from agent import __version__
        self.assertEqual(__version__, '5.6.17')
        self.assertEqual(upgrade.VERSION, '5.6.17')
        self.assertIn('5.5.0', upgrade.SUPPORTED_PREVIOUS)


if __name__ == '__main__':
    unittest.main()
