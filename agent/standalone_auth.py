"""Connexion, comptes et rôles du cabinet pour la distribution autonome (Docker / VPS) — AxiorHub 5.6.0.

Un serveur = un cabinet. Les comptes sont ceux des membres du cabinet ; il n'existe aucun partage de données entre deux installations.

Rôles :
- administrateur : tout, y compris l'assistant d'installation, les paramètres, l'IA, les mises à jour et les comptes ;
- avocat : tout le travail (instructions à l'agent, relecture et validation, documents, style), sans les réglages techniques ;
- assistant : consultation, instructions à l'agent, agenda et tâches, préparation de bordereaux ; ne valide ni n'écarte une production,
  ne dépose rien dans la messagerie et ne modifie aucun réglage.

Le premier compte créé est administrateur ; les inscriptions publiques sont ensuite fermées (AXIORHUB_ALLOW_SIGNUP=true les rouvre, mais
un compte ainsi créé reste inactif jusqu'à son activation par un administrateur).
"""
from datetime import datetime, timezone, timedelta
import base64
import hashlib
import hmac
from html import escape
from io import BytesIO
import json
import os
from pathlib import Path
import secrets
import smtplib
import sqlite3
from email.message import EmailMessage
from urllib.parse import parse_qs

ROLES = {'administrateur': 'Administrateur (associé)', 'avocat': 'Avocat', 'assistant': 'Assistant(e)'}
ADMIN_PATHS = ('/parametres', '/ia-externe', '/routage-hybride', '/administration', '/mcp', '/atelier/reglages', '/extensions', '/regles')
ADMIN_API = ('m540/', 'm520/queue/', 'm530/nextcloud', 'm550/settings')
ADMIN_ACTIONS = {'save_ai_provider', 'test_ai_provider', 'save_ai_route', 'save_hybrid_policy400', 'automation_setting', 'save_update_policy420',
                 'save_live430', 'save_local_model', 'save_external_url', 'set_lawve_extension', 'test_lawve_extension', 'save_routines520',
                 'set_agenda_personal_edit', 'save_reminders520'}
# Ce qu'un(e) assistant(e) peut faire en écriture ; tout autre envoi de formulaire lui est refusé.
ASSISTANT_API = ('m530/ask', 'm530/task', 'm530/routine', 'm510/scan', 'm510/table', 'm510/check', 'm510/plan', 'm510/case', 'm520/check/')
ASSISTANT_ACTIONS = {'create_agenda_event', 'edit_agenda_event', 'update_work_task', 'edit_work_task', 'schedule_work_task', 'confirm_task',
                     'create_local_task', 'sync_caldav_tasks', 'assistant_ask', 'edit_personal_event', 'edit_personal_task'}
ASSISTANT_PATHS = ('/assistant/attachment',)


def _b64(raw): return base64.urlsafe_b64encode(raw).decode().rstrip('=')
def _unb64(raw): return base64.urlsafe_b64decode(raw + '=' * ((-len(raw)) % 4))
def _now(): return datetime.now(timezone.utc)


def allowed(role, method, path, action=''):
    """Contrôle des droits d'un rôle sur une requête (sans état, testable)."""
    if role == 'administrateur':
        return True
    api = path[len('/api440/'):] if path.startswith('/api440/') else ''
    if any(path == p or path.startswith(p + '/') or path.startswith(p + '?') for p in ADMIN_PATHS) or path in ('/installation', '/comptes'):
        return False
    if method != 'POST':
        return True
    if api.startswith(ADMIN_API) or action in ADMIN_ACTIONS:
        return False
    if role == 'avocat':
        return True
    if role == 'assistant':
        if api:
            return api.startswith(ASSISTANT_API)
        if path == '/action':
            return action in ASSISTANT_ACTIONS
        return path in ASSISTANT_PATHS
    return False


class StandaloneAuth:
    def __init__(self, app, state_dir, public_url):
        self.app = app
        self.state = Path(state_dir)
        self.public_url = public_url.rstrip('/')
        self.state.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.db = sqlite3.connect(self.state / 'users.sqlite3', check_same_thread=False, timeout=10)
        self.db.row_factory = sqlite3.Row
        self.db.executescript('''CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY,email TEXT UNIQUE,
          password_hash TEXT,salt TEXT,active INTEGER,created TEXT);
          CREATE TABLE IF NOT EXISTS reset_tokens(token_hash TEXT PRIMARY KEY,user_id INTEGER,expires TEXT,used INTEGER);''')
        cols = {r[1] for r in self.db.execute('PRAGMA table_info(users)')}
        for name, sql in (('role', "ALTER TABLE users ADD COLUMN role TEXT NOT NULL DEFAULT 'avocat'"),
                          ('name', "ALTER TABLE users ADD COLUMN name TEXT NOT NULL DEFAULT ''"),
                          ('last_login', "ALTER TABLE users ADD COLUMN last_login TEXT NOT NULL DEFAULT ''"),
                          ('must_change', 'ALTER TABLE users ADD COLUMN must_change INTEGER NOT NULL DEFAULT 0')):
            if name not in cols:
                self.db.execute(sql)
        # Installations antérieures : le premier compte devient administrateur s'il n'y en a aucun.
        if not self.db.execute("SELECT 1 FROM users WHERE role='administrateur' AND active=1").fetchone():
            first = self.db.execute('SELECT id FROM users WHERE active=1 ORDER BY id LIMIT 1').fetchone()
            if first:
                self.db.execute("UPDATE users SET role='administrateur' WHERE id=?", (first['id'],))
        self.db.execute('CREATE TABLE IF NOT EXISTS account_log(id INTEGER PRIMARY KEY, at TEXT, actor TEXT, event TEXT, target TEXT)')
        self.db.commit()
        key = self.state / 'session.key'
        if not key.exists():
            key.write_bytes(secrets.token_bytes(32))
            os.chmod(key, 0o600)
        self.key = key.read_bytes()

    # ------------------------------------------------------------------ outils
    def _hash(self, password, salt):
        return hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1).hex()

    def _session(self, user_id):
        payload = json.dumps({'uid': user_id, 'exp': int((_now() + timedelta(hours=12)).timestamp())}, separators=(',', ':')).encode()
        return _b64(payload) + '.' + _b64(hmac.new(self.key, payload, hashlib.sha256).digest())

    def _csrf(self, user):
        return hmac.new(self.key, ('csrf-comptes:%d' % user['id']).encode(), hashlib.sha256).hexdigest()[:40]

    def _user(self, env):
        cookies = {}
        for part in env.get('HTTP_COOKIE', '').split(';'):
            if '=' in part:
                k, v = part.strip().split('=', 1)
                cookies[k] = v
        try:
            raw, sig = cookies['axiorhub_session'].split('.', 1)
            payload = _unb64(raw)
            if not hmac.compare_digest(hmac.new(self.key, payload, hashlib.sha256).digest(), _unb64(sig)):
                return None
            data = json.loads(payload)
            uid = int(data['uid'])
            if int(data['exp']) < int(_now().timestamp()):
                return None
            return self.db.execute('SELECT * FROM users WHERE id=? AND active=1', (uid,)).fetchone()
        except (KeyError, ValueError, TypeError, UnicodeError):
            return None

    def _form(self, env, limit=20000):
        length = int(env.get('CONTENT_LENGTH', '0') or 0)
        if not 0 < length <= limit:
            return {}
        return {k: v[0] for k, v in parse_qs(env['wsgi.input'].read(length).decode(), max_num_fields=60).items()}

    def _page(self, title, body, wide=False):
        return ('<!doctype html><html lang="fr"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>'
                + escape(title) + ' · AxiorHub Pilote</title><style>'
                'body{margin:0;background:#f3f5f8;color:#151922;font:16px system-ui,sans-serif;display:grid;place-items:center;min-height:100vh}'
                '.box{width:min(%s,calc(100%% - 32px));background:#fff;border:1px solid #dbe4f0;border-radius:18px;padding:28px;box-shadow:0 18px 50px #0f172a1a;margin:24px 0}'
                '.brand{display:flex;align-items:center;gap:12px;font-size:1.4rem;font-weight:800}.brand img{width:44px;height:44px}'
                'label{display:block;margin:14px 0 6px;font-weight:650}input,select{box-sizing:border-box;width:100%%;padding:11px;border:1px solid #b8c7dc;border-radius:10px;font:inherit;background:#fff}'
                'button,.btn{display:inline-block;margin-top:16px;padding:11px 16px;border:0;border-radius:10px;background:#1d4ed8;color:#fff;font-weight:700;font:inherit;cursor:pointer;text-decoration:none}'
                '.ghost{background:#fff;color:#1d4ed8;border:1px solid #1d4ed8}a{color:#1d4ed8}.links{display:flex;justify-content:space-between;margin-top:18px;font-size:.9rem}'
                '.notice{background:#eff6ff;padding:11px;border-radius:10px}.warn{background:#fff7ed;padding:11px;border-radius:10px}.ok{background:#ecfdf3;padding:11px;border-radius:10px}'
                'table{width:100%%;border-collapse:collapse;font-size:.92rem}th,td{text-align:left;border-bottom:1px solid #e5e9f0;padding:8px 6px;vertical-align:top}'
                'fieldset{border:1px solid #dbe4f0;border-radius:12px;margin:16px 0;padding:12px 16px}legend{font-weight:750;padding:0 6px}'
                '.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:0 16px}.inline{display:flex;gap:8px;flex-wrap:wrap;align-items:end}'
                '.inline select,.inline input{width:auto}.muted{color:#556070;font-size:.9rem}footer{margin-top:20px;font-size:.8rem;color:#556070}</style></head>'
                '<body><main class="box"><div class="brand"><img src="/static/axiorhub-icon.png" alt=""><span>AxiorHub Pilote</span></div><h1>%s</h1>%s'
                '<footer>AxiorHub Pilote — logiciel libre (AGPL-3.0) créé par Timo RAINIO. <a href="/a-propos">À propos</a></footer></main></body></html>') % (
            '980px' if wide else '440px', escape(title), body)

    def _respond(self, start, status, body, headers=None, kind='text/html; charset=utf-8'):
        raw = body.encode()
        start(status, [('Content-Type', kind), ('Content-Length', str(len(raw))), ('Cache-Control', 'no-store'), ('X-Content-Type-Options', 'nosniff'),
                       ('Content-Security-Policy', "default-src 'none'; img-src 'self'; style-src 'unsafe-inline'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'")]
              + (headers or []))
        return [raw]

    def _log(self, actor, event, target=''):
        self.db.execute('INSERT INTO account_log(at,actor,event,target) VALUES(?,?,?,?)', (_now().isoformat(), actor, event, target))
        self.db.commit()

    def _send_reset(self, email, token):
        host = os.environ.get('AXIORHUB_SMTP_HOST', '')
        if not host:
            return False
        message = EmailMessage()
        message['Subject'] = 'Réinitialisation de votre accès AxiorHub'
        message['From'] = os.environ.get('AXIORHUB_SMTP_FROM', 'no-reply@example.com')
        message['To'] = email
        message.set_content('Ouvrez ce lien pendant 30 minutes : ' + self.public_url + '/reset-password?token=' + token)
        with smtplib.SMTP(host, int(os.environ.get('AXIORHUB_SMTP_PORT', '587')), timeout=20) as smtp:
            if os.environ.get('AXIORHUB_SMTP_STARTTLS', 'true').lower() == 'true':
                smtp.starttls()
            if os.environ.get('AXIORHUB_SMTP_USERNAME'):
                smtp.login(os.environ['AXIORHUB_SMTP_USERNAME'], os.environ.get('AXIORHUB_SMTP_PASSWORD', ''))
            smtp.send_message(message)
        return True

    def _same_origin(self, env):
        origin = env.get('HTTP_ORIGIN', '')
        return not origin or origin.rstrip('/') == self.public_url

    def _installed(self):
        try:
            cfg = json.loads(Path(self.app.config_path).read_text(encoding='utf-8'))
            return bool((cfg.get('installation') or {}).get('done'))
        except (OSError, ValueError, AttributeError):
            return True                       # configuration illisible : ne bloque pas l'accès (diagnostic possible)

    # ------------------------------------------------------------------ comptes
    def accounts_page(self, env, start, user):
        msg = ''
        if env.get('REQUEST_METHOD') == 'POST':
            form = self._form(env)
            if not self._same_origin(env) or not hmac.compare_digest(form.get('csrf', ''), self._csrf(user)):
                return self._respond(start, '400 Bad Request', self._page('Comptes', '<p class="warn">Formulaire expiré : rechargez la page.</p>'))
            msg = self._account_action(user, form)
        rows = self.db.execute('SELECT * FROM users ORDER BY active DESC, role, email').fetchall()
        csrf = escape(self._csrf(user), quote=True)
        role_options = lambda current: ''.join('<option value="%s"%s>%s</option>' % (k, ' selected' if k == current else '', escape(v)) for k, v in ROLES.items())
        lines = ''
        for r in rows:
            me = r['id'] == user['id']
            lines += ('<tr><td><strong>%s</strong><br><span class="muted">%s</span></td><td>%s</td><td>%s</td><td>%s</td><td>'
                      '<form method="post" class="inline"><input type="hidden" name="csrf" value="%s"><input type="hidden" name="id" value="%d">'
                      '<select name="role" aria-label="Rôle">%s</select><button name="op" value="role" class="ghost">Changer le rôle</button>'
                      '<button name="op" value="%s" class="ghost">%s</button><button name="op" value="reset" class="ghost">Nouveau mot de passe</button></form></td></tr>') % (
                escape(r['name'] or '—'), escape(r['email']), escape(ROLES.get(r['role'], r['role'])), 'actif' if r['active'] else '<strong>inactif</strong>',
                escape((r['last_login'] or '')[:16].replace('T', ' ') or 'jamais'), csrf, r['id'], role_options(r['role']),
                'disable' if r['active'] else 'enable', 'Désactiver' if r['active'] else 'Activer') if not me else (
                '<tr><td><strong>%s</strong><br><span class="muted">%s</span></td><td>%s</td><td>actif</td><td>%s</td><td class="muted">votre compte</td></tr>') % (
                escape(r['name'] or '—'), escape(r['email']), escape(ROLES.get(r['role'], r['role'])), escape((r['last_login'] or '')[:16].replace('T', ' ')))
        body = (msg + '<p class="muted">Un serveur = un cabinet. Chaque membre a son propre compte ; les données ne sont partagées avec aucune autre installation.</p>'
                '<table><thead><tr><th>Personne</th><th>Rôle</th><th>État</th><th>Dernière connexion</th><th>Actions</th></tr></thead><tbody>%s</tbody></table>'
                '<fieldset><legend>Ajouter un membre du cabinet</legend><form method="post"><input type="hidden" name="csrf" value="%s"><div class="grid">'
                '<label>Nom<input name="name" maxlength="80" required></label><label>Adresse électronique<input name="email" type="email" required></label>'
                '<label>Rôle<select name="role">%s</select></label></div><button name="op" value="create">Créer le compte</button></form>'
                '<p class="muted">Un mot de passe provisoire est affiché une seule fois ; la personne le change à sa première connexion.</p></fieldset>'
                '<ul class="muted"><li><strong>Administrateur</strong> : tout, y compris installation, paramètres, IA et comptes.</li>'
                '<li><strong>Avocat</strong> : tout le travail (instructions, relecture et validation, documents, style), sans les réglages techniques.</li>'
                '<li><strong>Assistant(e)</strong> : consultation, instructions à l’agent, agenda et tâches, bordereaux ; ne valide ni ne dépose rien.</li></ul>'
                '<p><a href="/">← Retour</a> · <a href="/logout">Se déconnecter</a></p>') % (lines, csrf, role_options('avocat'))
        return self._respond(start, '200 OK', self._page('Comptes du cabinet', body, wide=True))

    def _account_action(self, user, form):
        op = form.get('op', '')
        admins = lambda: self.db.execute("SELECT COUNT(*) FROM users WHERE role='administrateur' AND active=1").fetchone()[0]
        if op == 'create':
            email = form.get('email', '').strip().lower()
            name = form.get('name', '').strip()[:80]
            role = form.get('role', 'avocat')
            if '@' not in email or role not in ROLES:
                return '<p class="warn">Adresse ou rôle invalide.</p>'
            temp = secrets.token_urlsafe(12)
            salt = secrets.token_bytes(16).hex()
            try:
                self.db.execute('INSERT INTO users(email,password_hash,salt,active,created,role,name,must_change) VALUES (?,?,?,?,?,?,?,1)',
                                (email, self._hash(temp, salt), salt, 1, _now().isoformat(), role, name))
                self.db.commit()
            except sqlite3.IntegrityError:
                return '<p class="warn">Ce compte existe déjà.</p>'
            self._log(user['email'], 'creation', email + ' (' + role + ')')
            return ('<p class="ok">Compte créé pour %s. Mot de passe provisoire, à transmettre de vive voix ou par un canal sûr (affiché une seule fois) : '
                    '<strong><code>%s</code></strong></p>') % (escape(email), escape(temp))
        try:
            target = self.db.execute('SELECT * FROM users WHERE id=?', (int(form.get('id', '0')),)).fetchone()
        except ValueError:
            target = None
        if not target or target['id'] == user['id']:
            return '<p class="warn">Compte introuvable (ou votre propre compte).</p>'
        last_admin = target['role'] == 'administrateur' and target['active'] and admins() <= 1
        if op == 'role':
            role = form.get('role', '')
            if role not in ROLES:
                return '<p class="warn">Rôle invalide.</p>'
            if last_admin and role != 'administrateur':
                return '<p class="warn">Il doit rester au moins un administrateur.</p>'
            self.db.execute('UPDATE users SET role=? WHERE id=?', (role, target['id']))
            self.db.commit()
            self._log(user['email'], 'role', target['email'] + ' → ' + role)
            return '<p class="ok">Rôle modifié.</p>'
        if op in ('disable', 'enable'):
            if op == 'disable' and last_admin:
                return '<p class="warn">Il doit rester au moins un administrateur actif.</p>'
            self.db.execute('UPDATE users SET active=? WHERE id=?', (1 if op == 'enable' else 0, target['id']))
            self._log(user['email'], op, target['email'])
            self.db.commit()
            return '<p class="ok">Compte %s.</p>' % ('activé' if op == 'enable' else 'désactivé')
        if op == 'reset':
            temp = secrets.token_urlsafe(12)
            salt = secrets.token_bytes(16).hex()
            self.db.execute('UPDATE users SET password_hash=?,salt=?,must_change=1 WHERE id=?', (self._hash(temp, salt), salt, target['id']))
            self._log(user['email'], 'mot_de_passe', target['email'])
            self.db.commit()
            return '<p class="ok">Nouveau mot de passe provisoire pour %s (affiché une seule fois) : <strong><code>%s</code></strong></p>' % (escape(target['email']), escape(temp))
        return '<p class="warn">Action inconnue.</p>'

    def change_password_page(self, env, start, user):
        error = ''
        if env.get('REQUEST_METHOD') == 'POST':
            form = self._form(env)
            if not hmac.compare_digest(form.get('csrf', ''), self._csrf(user)):
                error = 'Formulaire expiré.'
            elif len(form.get('password', '')) < 12 or form.get('password') != form.get('confirm'):
                error = 'Mot de passe de 12 caractères minimum, saisi deux fois à l’identique.'
            else:
                salt = secrets.token_bytes(16).hex()
                self.db.execute('UPDATE users SET password_hash=?,salt=?,must_change=0 WHERE id=?', (self._hash(form['password'], salt), salt, user['id']))
                self.db.commit()
                self._log(user['email'], 'mot_de_passe_change', user['email'])
                return self._respond(start, '303 See Other', '', [('Location', '/')])
        body = (('<p class="warn">%s</p>' % escape(error)) if error else '<p class="notice">Choisissez votre mot de passe personnel.</p>') + (
            '<form method="post"><input type="hidden" name="csrf" value="%s"><label>Nouveau mot de passe<input name="password" type="password" minlength="12" required '
            'autocomplete="new-password"></label><label>Confirmation<input name="confirm" type="password" minlength="12" required autocomplete="new-password"></label>'
            '<button>Enregistrer</button></form>') % escape(self._csrf(user), quote=True)
        return self._respond(start, '200 OK', self._page('Mot de passe', body))

    # ------------------------------------------------------------------ requêtes
    def __call__(self, env, start):
        path = env.get('PATH_INFO', '/')
        if path == '/healthz':
            raw = b'ok'
            start('200 OK', [('Content-Type', 'text/plain'), ('Content-Length', '2')])
            return [raw]
        if path == '/service-worker.js':
            # Remplace un service worker resté d'une autre application qui occupait le même nom de domaine.
            raw = ("self.addEventListener('install',()=>self.skipWaiting());"
                   "self.addEventListener('activate',e=>e.waitUntil(self.registration.unregister().then(()=>clients.claim())));").encode()
            start('200 OK', [('Content-Type', 'application/javascript; charset=utf-8'), ('Content-Length', str(len(raw))),
                             ('Cache-Control', 'no-store, max-age=0'), ('Service-Worker-Allowed', '/')])
            return [raw]
        if path.startswith('/static/') or path == '/a-propos':
            return self._proxy(env, start, None)
        user = self._user(env)
        if path == '/logout':
            return self._respond(start, '303 See Other', '', [('Location', '/login'), ('Set-Cookie', 'axiorhub_session=; Path=/; Max-Age=0; HttpOnly; Secure; SameSite=Lax')])
        if path == '/signup':
            return self._signup(env, start)
        if path == '/forgot-password':
            return self._forgot(env, start)
        if path == '/reset-password':
            return self._reset(env, start)
        if path == '/login' or not user:
            return self._login(env, start, path)
        if user['must_change'] and path != '/mot-de-passe':
            return self._respond(start, '303 See Other', '', [('Location', '/mot-de-passe')])
        if path == '/mot-de-passe':
            return self.change_password_page(env, start, user)
        role = user['role'] or 'avocat'
        if path == '/comptes':
            if role != 'administrateur':
                return self._forbidden(start)
            return self.accounts_page(env, start, user)
        if path.startswith('/installation'):
            if role != 'administrateur':
                return self._forbidden(start)
            from . import setup560
            return setup560.handle(self, env, start, user)
        if not self._installed() and env.get('REQUEST_METHOD', 'GET') == 'GET' and not path.startswith('/api'):
            if role == 'administrateur':
                return self._respond(start, '303 See Other', '', [('Location', '/installation')])
            return self._respond(start, '200 OK', self._page('Installation en cours', '<p class="notice">L’administrateur du cabinet termine l’installation. '
                                                             'Revenez dans quelques minutes.</p><p><a href="/logout">Se déconnecter</a></p>'))
        action = ''
        if env.get('REQUEST_METHOD') == 'POST' and path == '/action':
            length = int(env.get('CONTENT_LENGTH', '0') or 0)
            raw = env['wsgi.input'].read(length) if 0 < length <= 30_000_000 else b''
            env['wsgi.input'] = BytesIO(raw)
            try:
                action = parse_qs(raw.decode('utf-8', 'replace'), max_num_fields=400).get('action', [''])[0]
            except ValueError:
                action = ''
        if not allowed(role, env.get('REQUEST_METHOD', 'GET'), path, action):
            return self._forbidden(start, json_api=path.startswith('/api'))
        return self._proxy(env, start, user)

    def _forbidden(self, start, json_api=False):
        if json_api:
            raw = json.dumps({'error': 'role_insuffisant', 'message': 'Action réservée à un autre rôle du cabinet.'}).encode()
            start('403 Forbidden', [('Content-Type', 'application/json'), ('Content-Length', str(len(raw)))])
            return [raw]
        return self._respond(start, '403 Forbidden', self._page('Accès réservé', '<p class="warn">Cette page ou cette action est réservée à un autre rôle du cabinet '
                                                                 '(administrateur ou avocat).</p><p><a href="/">← Retour</a></p>'))

    def _proxy(self, env, start, user):
        # L'application interne est authentifiée par son jeton privé ; l'identité du membre est transmise pour l'affichage.
        env['HTTP_AUTHORIZATION'] = 'Bearer ' + os.environ.get('AXIORHUB_INTERNAL_API_TOKEN', '')
        env['HTTP_HOST'] = self.public_url.split('://', 1)[-1].split('/', 1)[0]
        env['HTTP_X_FORWARDED_PROTO'] = 'https' if self.public_url.startswith('https://') else 'http'
        if user is None:
            return self.app(env, start)
        env['HTTP_X_AXIORHUB_USER'] = user['email']
        env['HTTP_X_AXIORHUB_ROLE'] = user['role'] or 'avocat'
        captured = {}

        def capture(status, headers, exc_info=None):
            captured['status'], captured['headers'] = status, headers
            return lambda data: None
        body = b''.join(self.app(env, capture))
        headers = captured.get('headers', [])
        kind = dict((k.lower(), v) for k, v in headers).get('content-type', '')
        if kind.startswith('text/html') and b'</aside>' in body:
            bar = ('<div class="ws-user"><span>%s</span><small>%s</small><span class="ws-user-links">%s<a href="/logout">Se déconnecter</a></span></div>' % (
                escape(user['name'] or user['email']), escape(ROLES.get(user['role'], user['role'])),
                '<a href="/comptes">Comptes</a> · ' if user['role'] == 'administrateur' else '')).encode()
            body = body.replace(b'</aside>', bar + b'</aside>', 1)
            headers = [(k, v) for k, v in headers if k.lower() != 'content-length'] + [('Content-Length', str(len(body)))]
        start(captured.get('status', '500 Internal Server Error'), headers)
        return [body]

    # ------------------------------------------------------------------ connexion, inscription, mot de passe oublié
    def _login(self, env, start, path):
        error = ''
        if path == '/login' and env.get('REQUEST_METHOD') == 'POST':
            form = self._form(env)
            row = self.db.execute('SELECT * FROM users WHERE email=? AND active=1', (form.get('email', '').strip().lower(),)).fetchone()
            if row and hmac.compare_digest(row['password_hash'], self._hash(form.get('password', ''), row['salt'])):
                self.db.execute('UPDATE users SET last_login=? WHERE id=?', (_now().isoformat(), row['id']))
                self.db.commit()
                return self._respond(start, '303 See Other', '', [('Location', '/'), ('Set-Cookie', 'axiorhub_session=' + self._session(row['id'])
                                                                                         + '; Path=/; Max-Age=43200; HttpOnly; Secure; SameSite=Lax')])
            error = 'Identifiants incorrects.'
        count = self.db.execute('SELECT COUNT(*) FROM users').fetchone()[0]
        body = (('<p class="notice">' + escape(error) + '</p>') if error else '') + (
            '<form method="post" action="/login"><label>Adresse électronique<input name="email" type="email" autocomplete="username" required></label>'
            '<label>Mot de passe<input name="password" type="password" autocomplete="current-password" required></label><button>Se connecter</button></form>'
            '<div class="links">%s<a href="/forgot-password">Mot de passe oublié</a></div>') % (
            '<a href="/signup">Créer le compte administrateur</a>' if count == 0 else (
                '<a href="/signup">Demander un accès</a>' if os.environ.get('AXIORHUB_ALLOW_SIGNUP', 'false').lower() == 'true' else '<span></span>'))
        return self._respond(start, '200 OK', self._page('Connexion', body))

    def _signup(self, env, start):
        count = self.db.execute('SELECT COUNT(*) FROM users').fetchone()[0]
        open_signup = os.environ.get('AXIORHUB_ALLOW_SIGNUP', 'false').lower() == 'true'
        allowed_now = count == 0 or open_signup
        error = ''
        if env.get('REQUEST_METHOD') == 'POST' and allowed_now:
            form = self._form(env)
            email = form.get('email', '').strip().lower()
            password = form.get('password', '')
            name = form.get('name', '').strip()[:80]
            if '@' not in email or len(password) < 12:
                error = 'Adresse valide et mot de passe de 12 caractères minimum requis.'
            else:
                salt = secrets.token_bytes(16).hex()
                first = count == 0
                try:
                    self.db.execute('INSERT INTO users(email,password_hash,salt,active,created,role,name) VALUES (?,?,?,?,?,?,?)',
                                    (email, self._hash(password, salt), salt, 1 if first else 0, _now().isoformat(),
                                     'administrateur' if first else 'assistant', name))
                    self.db.commit()
                except sqlite3.IntegrityError:
                    error = 'Ce compte existe déjà.'
                else:
                    self._log(email, 'inscription', 'administrateur' if first else 'en attente')
                    if first:
                        return self._respond(start, '303 See Other', '', [('Location', '/login')])
                    return self._respond(start, '200 OK', self._page('Demande enregistrée', '<p class="ok">Votre demande est enregistrée : un administrateur du '
                                                                     'cabinet doit activer votre compte.</p><p><a href="/login">Connexion</a></p>'))
        intro = ('<p class="notice">Premier compte : il sera <strong>administrateur</strong> du cabinet. Les autres membres seront créés ensuite '
                 'depuis la page « Comptes ».</p>') if count == 0 else '<p class="notice">Le compte restera inactif jusqu’à son activation par un administrateur.</p>'
        body = (('<p class="warn">' + escape(error) + '</p>') if error else '') + ((intro + '<form method="post"><label>Nom<input name="name" maxlength="80" required></label>'
                '<label>Adresse électronique<input name="email" type="email" required></label><label>Mot de passe (12 caractères minimum)'
                '<input name="password" type="password" minlength="12" required autocomplete="new-password"></label><button>Créer le compte</button></form>')
               if allowed_now else '<p>Les inscriptions sont fermées : demandez un accès à l’administrateur du cabinet.</p>') + '<div class="links"><a href="/login">Connexion</a></div>'
        return self._respond(start, '200 OK', self._page('Créer un compte', body))

    def _forgot(self, env, start):
        if env.get('REQUEST_METHOD') == 'POST':
            form = self._form(env)
            row = self.db.execute('SELECT * FROM users WHERE email=? AND active=1', (form.get('email', '').strip().lower(),)).fetchone()
            if row:
                token = secrets.token_urlsafe(32)
                self.db.execute('INSERT INTO reset_tokens VALUES (?,?,?,0)', (hashlib.sha256(token.encode()).hexdigest(), row['id'], (_now() + timedelta(minutes=30)).isoformat()))
                self.db.commit()
                try:
                    self._send_reset(row['email'], token)
                except Exception:
                    pass
            return self._respond(start, '200 OK', self._page('Mot de passe oublié', '<p class="notice">Si le compte existe et que le serveur d’envoi est configuré, '
                                                             'un lien a été envoyé. Sinon, demandez un nouveau mot de passe à l’administrateur du cabinet.</p><a href="/login">Retour</a>'))
        return self._respond(start, '200 OK', self._page('Mot de passe oublié', '<form method="post"><label>Adresse électronique<input name="email" type="email" required>'
                                                         '</label><button>Envoyer le lien</button></form><div class="links"><a href="/login">Retour</a></div>'))

    def _reset(self, env, start):
        token = parse_qs(env.get('QUERY_STRING', '')).get('token', [''])[0]
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        row = self.db.execute('SELECT * FROM reset_tokens WHERE token_hash=? AND used=0', (token_hash,)).fetchone()
        valid = row and datetime.fromisoformat(row['expires']) > _now()
        if env.get('REQUEST_METHOD') == 'POST' and valid:
            form = self._form(env)
            password = form.get('password', '')
            if len(password) >= 12:
                salt = secrets.token_bytes(16).hex()
                self.db.execute('UPDATE users SET password_hash=?,salt=?,must_change=0 WHERE id=?', (self._hash(password, salt), salt, row['user_id']))
                self.db.execute('UPDATE reset_tokens SET used=1 WHERE token_hash=?', (token_hash,))
                self.db.commit()
                return self._respond(start, '303 See Other', '', [('Location', '/login')])
        body = '<p>Lien invalide ou expiré.</p>' if not valid else (
            '<form method="post" action="/reset-password?token=' + escape(token, quote=True) + '"><label>Nouveau mot de passe<input name="password" type="password" '
            'minlength="12" required></label><button>Enregistrer</button></form>')
        return self._respond(start, '200 OK', self._page('Nouveau mot de passe', body))
