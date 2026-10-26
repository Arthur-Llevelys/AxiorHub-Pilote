"""5.6.48 : page de connexion de la version serveur (au lieu de la fenêtre « Basic » du navigateur).

- **Connexion** par une page aux couleurs d'AxiorHub (français, anglais, allemand, espagnol), session enregistrée sur le serveur (cookie
  ``axh_session``, HttpOnly, Secure, SameSite=Lax ; seule l'empreinte du jeton est conservée), « Se souvenir de moi » (30 jours),
  tentatives limitées par identifiant et par adresse.
- **Compte administrateur** : celui de l'installation, repris tel quel (``ui-auth.json`` et changement fait dans Paramètres › Comptes) ;
  ses données restent celles du cabinet (propriétaire « cabinet »). Sans compte (nouvelle installation), la première visite propose de
  le créer avec le code privé écrit sur le serveur (``premier-compte.code`` dans le dossier d'état).
- **Autres comptes** (avocats, assistants) créés par l'administrateur dans Paramètres › Comptes, avec un mot de passe provisoire à
  changer à la première connexion ; droits par rôle identiques à ceux de la distribution autonome (``standalone_auth.allowed``).
- **Mot de passe oublié** : lien valable 30 minutes envoyé par courriel (serveur SMTP configuré, adresse de récupération connue) ; la
  réponse est la même que le compte existe ou non. Sinon : ``sudo python3 /opt/axiorhub-mail-agent/current/reinitialiser-acces.py``.
- **Déconnexion** : bouton « Se déconnecter » en pied du menu (formulaire POST, même origine).

Les accès d'intégration restent inchangés : jeton Bearer (Open WebUI), session de l'application Nextcloud, fenêtre du poste ; l'en-tête
« Basic » de l'administrateur reste accepté pour les scripts du cabinet.
"""
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
from html import escape as e
import json
from pathlib import Path
import re
import secrets
import sqlite3
import time
from urllib.parse import parse_qs, quote, urlsplit

from .common import Stop, private_json

DB_NAME = 'comptes5648.sqlite3'
CODE_NAME = 'premier-compte.code'
COOKIE = 'axh_session'
ADMIN_ID = 0
SESSION_HOURS = 12
REMEMBER_DAYS = 30
RESET_MINUTES = 30
LIMITS = {'ident': (5, 900), 'ip': (20, 900), 'reset': (3, 3600)}
MIN_ADMIN = 16
MIN_USER = 12
ROLES = {'administrateur': 'Administrateur', 'avocat': 'Avocat', 'assistant': 'Assistant(e)'}
IDENT = re.compile(r'[A-Za-z0-9._@+-]{3,120}')
SCHEMA = '''
CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, ident TEXT UNIQUE NOT NULL, email TEXT NOT NULL DEFAULT '', name TEXT NOT NULL DEFAULT '',
  role TEXT NOT NULL DEFAULT 'avocat', salt TEXT NOT NULL, hash TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1,
  must_change INTEGER NOT NULL DEFAULT 1, created TEXT NOT NULL, last_login TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS sessions(token_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL, stamp TEXT NOT NULL, created TEXT NOT NULL,
  expires TEXT NOT NULL, remember INTEGER NOT NULL DEFAULT 0, client TEXT NOT NULL DEFAULT '');
CREATE INDEX IF NOT EXISTS sessions_user ON sessions(user_id);
CREATE TABLE IF NOT EXISTS resets(token_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL, stamp TEXT NOT NULL, expires TEXT NOT NULL,
  used INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS attempts(id INTEGER PRIMARY KEY, scope TEXT NOT NULL, at REAL NOT NULL);
CREATE INDEX IF NOT EXISTS attempts_scope ON attempts(scope, at);
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS log(id INTEGER PRIMARY KEY, at TEXT NOT NULL, actor TEXT NOT NULL, event TEXT NOT NULL, target TEXT NOT NULL DEFAULT '');
'''


def _now():
    return datetime.now(timezone.utc)


def _hash(password, salt):
    return hashlib.scrypt(str(password).encode('utf-8'), salt=bytes.fromhex(salt), n=16384, r=8, p=1).hex()


def _token_hash(token):
    return hashlib.sha256(str(token).encode()).hexdigest()


# ------------------------------------------------------------------------------------------------ mode et stockage
def enabled(auth):
    """Version serveur seulement : le poste (fenêtre locale), l'ExApp (Nextcloud) et la distribution autonome ont leur propre accès."""
    if not isinstance(auth, dict) or auth.get('poste') or auth.get('exapp'):
        return False
    from .comptes5642 import standalone
    return not standalone()


def connect(state_dir):
    path = Path(state_dir) / DB_NAME
    db = sqlite3.connect(str(path), timeout=10)
    db.row_factory = sqlite3.Row
    db.executescript(SCHEMA)
    try:
        path.chmod(0o600)
    except OSError:
        pass
    return db


def _setting(db, key, default=''):
    row = db.execute('SELECT value FROM settings WHERE key=?', (key,)).fetchone()
    return row['value'] if row else default


def _log(db, actor, event, target=''):
    db.execute('INSERT INTO log(at,actor,event,target) VALUES(?,?,?,?)', (_now().isoformat(timespec='seconds'), str(actor)[:120], event, str(target)[:200]))
    db.commit()


def admin_stamp(auth):
    """Empreinte du mot de passe administrateur : un changement de mot de passe ferme toutes ses sessions."""
    return hashlib.sha256(('admin|' + str(auth.get('hash') or '')).encode()).hexdigest()[:24]


def _user_stamp(row):
    return hashlib.sha256(('user|%s|%s' % (row['id'], row['hash'])).encode()).hexdigest()[:24]


def admin_user(auth, db=None):
    email = _setting(db, 'admin_email') if db is not None else ''
    return {'id': ADMIN_ID, 'ident': str(auth.get('username') or 'admin'), 'email': email, 'name': str(auth.get('username') or 'admin'), 'role': 'administrateur',
            'owner': 'cabinet', 'must_change': False}


def _user_dict(row):
    return {'id': row['id'], 'ident': row['ident'], 'email': row['email'] or (row['ident'] if '@' in row['ident'] else ''), 'name': row['name'] or row['ident'],
            'role': row['role'] if row['role'] in ROLES else 'assistant', 'owner': row['ident'], 'must_change': bool(row['must_change'])}


def first_run(auth):
    return not (auth.get('hash') and auth.get('salt'))


def bootstrap_code(state_dir):
    """Code privé de création du premier compte, écrit une fois dans le dossier d'état (lisible par root et par le service)."""
    path = Path(state_dir) / CODE_NAME
    if not path.exists():
        try:
            with path.open('x', encoding='utf-8') as stream:
                stream.write(secrets.token_urlsafe(18) + '\n')
            path.chmod(0o600)
        except FileExistsError:
            pass
        except OSError:
            return ''
    try:
        return path.read_text(encoding='utf-8').strip()
    except OSError:
        return ''


# ------------------------------------------------------------------------------------------------ tentatives
def _limited(db, scope, kind):
    count, window = LIMITS[kind]
    db.execute('DELETE FROM attempts WHERE at<?', (time.time() - 86400,))
    return db.execute('SELECT COUNT(*) FROM attempts WHERE scope=? AND at>=?', (scope, time.time() - window)).fetchone()[0] >= count


def _attempt(db, *scopes):
    for scope in scopes:
        db.execute('INSERT INTO attempts(scope,at) VALUES(?,?)', (scope, time.time()))
    db.commit()


# ------------------------------------------------------------------------------------------------ connexion et sessions
def check_password(db, auth, ident, password, client='inconnu'):
    """Utilisateur reconnu, sinon Stop. Même calcul que le compte existe ou non (la durée ne révèle pas les identifiants)."""
    ident = str(ident or '').strip()[:120]
    key = ident.lower()
    scopes = ('ident:' + key, 'ip:' + str(client)[:64])
    if _limited(db, scopes[0], 'ident') or _limited(db, scopes[1], 'ip'):
        _log(db, ident or '?', 'connexion_bloquee', client)
        raise Stop('trop_de_tentatives')
    admin_email = _setting(db, 'admin_email').lower()
    admin = admin_user(auth, db)
    if not first_run(auth) and key in (admin['ident'].lower(),) + ((admin_email,) if admin_email else ()):
        ok = hmac.compare_digest(_hash(password, auth['salt']), str(auth['hash']))
        user = admin
    else:
        row = db.execute('SELECT * FROM users WHERE lower(ident)=? OR (email<>\'\' AND lower(email)=?)', (key, key)).fetchone()
        computed = _hash(password, row['salt'] if row else '00' * 16)
        ok = bool(row) and bool(row['active']) and hmac.compare_digest(computed, row['hash'])
        user = _user_dict(row) if row else None
    if not ok:
        _attempt(db, *scopes)
        _log(db, ident or '?', 'connexion_refusee', client)
        raise Stop('identifiants_incorrects')
    db.execute('DELETE FROM attempts WHERE scope=?', (scopes[0],))
    if user['id'] != ADMIN_ID:
        db.execute('UPDATE users SET last_login=? WHERE id=?', (_now().isoformat(timespec='seconds'), user['id']))
    db.commit()
    _log(db, user['ident'], 'connexion', client)
    return user


def open_session(db, auth, user, remember=False, client=''):
    token = secrets.token_urlsafe(32)
    now = _now()
    life = timedelta(days=REMEMBER_DAYS) if remember else timedelta(hours=SESSION_HOURS)
    if user['id'] == ADMIN_ID:
        stamp = admin_stamp(auth)
    else:
        stamp = _user_stamp(db.execute('SELECT * FROM users WHERE id=?', (user['id'],)).fetchone())
    db.execute('DELETE FROM sessions WHERE expires<?', (now.isoformat(),))
    db.execute('INSERT INTO sessions VALUES(?,?,?,?,?,?,?)', (_token_hash(token), user['id'], stamp, now.isoformat(), (now + life).isoformat(),
                                                            1 if remember else 0, str(client)[:64]))
    db.commit()
    return token, int(life.total_seconds()) if remember else None


def session_user(db, auth, token):
    """Utilisateur de la session (cookie), ou None : session inconnue, expirée, compte désactivé ou mot de passe changé depuis."""
    if not token or len(token) > 200:
        return None
    row = db.execute('SELECT * FROM sessions WHERE token_hash=? AND expires>?', (_token_hash(token), _now().isoformat())).fetchone()
    if not row:
        return None
    if row['user_id'] == ADMIN_ID:
        if first_run(auth) or not hmac.compare_digest(row['stamp'], admin_stamp(auth)):
            return None
        user = admin_user(auth, db)
    else:
        found = db.execute('SELECT * FROM users WHERE id=? AND active=1', (row['user_id'],)).fetchone()
        if not found or not hmac.compare_digest(row['stamp'], _user_stamp(found)):
            return None
        user = _user_dict(found)
    user['session'] = row['token_hash']
    return user


def close_session(db, token):
    db.execute('DELETE FROM sessions WHERE token_hash=?', (_token_hash(token),))
    db.commit()


def revoke(db, user_id, keep=''):
    db.execute('DELETE FROM sessions WHERE user_id=? AND token_hash<>?', (int(user_id), keep or ''))
    db.commit()


def cookie_value(env):
    for part in str(env.get('HTTP_COOKIE') or '').split(';'):
        name, _, value = part.strip().partition('=')
        if name == COOKIE:
            return value
    return ''


def cookie_header(prefix, token, max_age=None, secure=True):
    path = (prefix or '') + '/'
    if token is None:
        return 'Set-Cookie', '%s=; Path=%s; Max-Age=0; HttpOnly; SameSite=Lax%s' % (COOKIE, path, '; Secure' if secure else '')
    return 'Set-Cookie', '%s=%s; Path=%s;%s HttpOnly; SameSite=Lax%s' % (COOKIE, token, path, (' Max-Age=%d;' % max_age) if max_age else '',
                                                                   '; Secure' if secure else '')


# ------------------------------------------------------------------------------------------------ comptes
def _write_admin(state_dir, ident, password):
    """Mot de passe administrateur : même fichier privé que Paramètres › Comptes (lu par-dessus ui-auth.json)."""
    from .comptes5642 import OVERRIDE
    salt = secrets.token_hex(16)
    record = {'username': ident, 'salt': salt, 'hash': _hash(password, salt), 'date': _now().isoformat(timespec='seconds')}
    private_json(Path(state_dir) / OVERRIDE, record)
    return record


def create_first_admin(db, state_dir, auth, data):
    if not first_run(auth):
        raise Stop('compte_administrateur_existe')
    expected = bootstrap_code(state_dir)
    if not expected or not hmac.compare_digest(str(data.get('code') or '').strip(), expected):
        raise Stop('code_premier_compte_incorrect')
    ident = str(data.get('ident') or '').strip()
    if not IDENT.fullmatch(ident):
        raise Stop('identifiant_invalide')
    password, again = str(data.get('password') or ''), str(data.get('confirm') or '')
    if len(password) < MIN_ADMIN:
        raise Stop('mot_de_passe_trop_court')
    if password != again:
        raise Stop('mots_de_passe_differents')
    email = str(data.get('email') or '').strip()
    if email and not re.fullmatch(r'[^@\s]+@[^@\s]+\.[^@\s]+', email):
        raise Stop('adresse_recuperation_invalide')
    record = _write_admin(state_dir, ident, password)
    if email:
        db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)', ('admin_email', email))
        db.commit()
    try:
        (Path(state_dir) / CODE_NAME).unlink()
    except OSError:
        pass
    _log(db, ident, 'premier_compte_cree')
    return {**auth, **record}


def set_admin_email(db, email):
    email = str(email or '').strip()
    if email and not re.fullmatch(r'[^@\s]+@[^@\s]+\.[^@\s]+', email):
        raise Stop('adresse_recuperation_invalide')
    db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)', ('admin_email', email))
    db.commit()
    return email


def list_users(db):
    return [dict(r) for r in db.execute('SELECT id,ident,email,name,role,active,must_change,created,last_login FROM users ORDER BY ident')]


def add_user(db, actor, data):
    ident = str(data.get('ident') or '').strip()
    if not IDENT.fullmatch(ident):
        raise Stop('identifiant_invalide')
    role = str(data.get('role') or 'avocat')
    if role not in ROLES:
        raise Stop('role_invalide')
    password = str(data.get('password') or '')
    if len(password) < MIN_USER:
        raise Stop('mot_de_passe_trop_court_12')
    email = str(data.get('email') or (ident if '@' in ident else '')).strip()
    if email and not re.fullmatch(r'[^@\s]+@[^@\s]+\.[^@\s]+', email):
        raise Stop('adresse_recuperation_invalide')
    salt = secrets.token_hex(16)
    try:
        db.execute('INSERT INTO users(ident,email,name,role,salt,hash,active,must_change,created) VALUES(?,?,?,?,?,?,1,1,?)',
                   (ident, email, str(data.get('name') or '').strip()[:80], role, salt, _hash(password, salt), _now().isoformat(timespec='seconds')))
        db.commit()
    except sqlite3.IntegrityError:
        raise Stop('compte_existe_deja') from None
    _log(db, actor, 'compte_cree', ident)
    return {'message': 'Compte « %s » créé (%s). Mot de passe provisoire à changer à la première connexion.' % (ident, ROLES[role])}


def update_user(db, actor, data):
    try:
        uid = int(data.get('id'))
    except (TypeError, ValueError):
        raise Stop('compte_absent') from None
    row = db.execute('SELECT * FROM users WHERE id=?', (uid,)).fetchone()
    if not row:
        raise Stop('compte_absent')
    action = str(data.get('action') or '')
    if action == 'desactiver':
        db.execute('UPDATE users SET active=0 WHERE id=?', (uid,))
        revoke(db, uid)
        message = 'Compte « %s » désactivé ; ses sessions sont fermées.' % row['ident']
    elif action == 'activer':
        db.execute('UPDATE users SET active=1 WHERE id=?', (uid,))
        message = 'Compte « %s » réactivé.' % row['ident']
    elif action == 'role':
        role = str(data.get('role') or '')
        if role not in ROLES:
            raise Stop('role_invalide')
        db.execute('UPDATE users SET role=? WHERE id=?', (role, uid))
        message = 'Rôle de « %s » : %s.' % (row['ident'], ROLES[role])
    elif action == 'provisoire':
        password = str(data.get('password') or '')
        if len(password) < MIN_USER:
            raise Stop('mot_de_passe_trop_court_12')
        salt = secrets.token_hex(16)
        db.execute('UPDATE users SET salt=?,hash=?,must_change=1 WHERE id=?', (salt, _hash(password, salt), uid))
        revoke(db, uid)
        message = 'Mot de passe provisoire de « %s » enregistré ; ses sessions sont fermées.' % row['ident']
    else:
        raise Stop('action_compte_invalide')
    db.commit()
    _log(db, actor, 'compte_' + action, row['ident'])
    return {'message': message}


def change_own(db, user, password, again):
    if len(password) < MIN_USER:
        raise Stop('mot_de_passe_trop_court_12')
    if password != again:
        raise Stop('mots_de_passe_differents')
    salt = secrets.token_hex(16)
    db.execute('UPDATE users SET salt=?,hash=?,must_change=0 WHERE id=?', (salt, _hash(password, salt), user['id']))
    db.commit()
    revoke(db, user['id'])
    _log(db, user['ident'], 'mot_de_passe_change')


# ------------------------------------------------------------------------------------------------ mot de passe oublié
def request_reset(db, auth, cfg, ident, client, base_url):
    """Lien envoyé si le compte existe, a une adresse et que le SMTP est configuré ; réponse identique dans tous les cas."""
    key = str(ident or '').strip().lower()[:200]
    if not key or _limited(db, 'reset:' + key, 'reset') or _limited(db, 'ip:' + str(client)[:64], 'ip'):
        return False
    _attempt(db, 'reset:' + key)
    admin = admin_user(auth, db)
    if not first_run(auth) and key in (admin['ident'].lower(), (admin['email'] or '').lower()):
        uid, address, stamp = ADMIN_ID, admin['email'], admin_stamp(auth)
    else:
        row = db.execute("SELECT * FROM users WHERE active=1 AND (lower(ident)=? OR (email<>'' AND lower(email)=?))", (key, key)).fetchone()
        if not row:
            return False
        uid, address, stamp = row['id'], _user_dict(row)['email'], _user_stamp(row)
    if not address:
        _log(db, key, 'oubli_sans_adresse')
        return False
    token = secrets.token_urlsafe(32)
    db.execute('INSERT INTO resets VALUES(?,?,?,?,0)', (_token_hash(token), uid, stamp, (_now() + timedelta(minutes=RESET_MINUTES)).isoformat()))
    db.commit()
    link = base_url.rstrip('/') + '/connexion/nouveau?jeton=' + quote(token)
    try:
        sent = send_mail(cfg, address, 'AxiorHub Pilote — nouveau mot de passe',
                         'Bonjour,\n\nUne demande de nouveau mot de passe a été faite pour votre accès AxiorHub Pilote.\n\nOuvrez ce lien dans les %d '
                         'minutes pour choisir un nouveau mot de passe :\n%s\n\nSi vous n’êtes pas à l’origine de cette demande, ignorez ce message : '
                         'votre mot de passe actuel reste valable.\n' % (RESET_MINUTES, link))
    except Exception:   # noqa: BLE001 — l'échec d'envoi n'est jamais révélé à la page publique
        sent = False
    _log(db, key, 'oubli_lien_envoye' if sent else 'oubli_envoi_impossible')
    return sent


def send_mail(cfg, to, subject, body):
    """Envoi par le serveur SMTP configuré dans Paramètres (aucun envoi si l'envoi n'est pas configuré)."""
    import smtplib
    from email.message import EmailMessage
    from .common import read_secret
    smtp = (cfg or {}).get('smtp') or {}
    if not smtp.get('enabled') or not smtp.get('host'):
        return False
    message = EmailMessage()
    message['Subject'] = subject
    message['From'] = smtp.get('from_address') or smtp.get('username') or 'no-reply@example.com'
    message['To'] = to
    message.set_content(body)
    security = smtp.get('security', 'starttls')
    client = smtplib.SMTP_SSL if security == 'ssl' else smtplib.SMTP
    with client(smtp['host'], int(smtp.get('port') or (465 if security == 'ssl' else 587)), timeout=20) as server:
        if security == 'starttls':
            server.starttls()
        if smtp.get('username'):
            server.login(smtp['username'], read_secret(smtp['password_file']) if smtp.get('password_file') else '')
        server.send_message(message)
    return True


def reset_with_token(db, state_dir, auth, token, password, again):
    row = db.execute('SELECT * FROM resets WHERE token_hash=? AND used=0', (_token_hash(token),)).fetchone() if token else None
    if not row or datetime.fromisoformat(row['expires']) <= _now():
        raise Stop('lien_expire')
    minimum = MIN_ADMIN if row['user_id'] == ADMIN_ID else MIN_USER
    if len(password) < minimum:
        raise Stop('mot_de_passe_trop_court' if minimum == MIN_ADMIN else 'mot_de_passe_trop_court_12')
    if password != again:
        raise Stop('mots_de_passe_differents')
    if row['user_id'] == ADMIN_ID:
        if row['stamp'] != admin_stamp(auth):
            raise Stop('lien_expire')
        _write_admin(state_dir, str(auth.get('username') or 'admin'), password)
    else:
        user = db.execute('SELECT * FROM users WHERE id=? AND active=1', (row['user_id'],)).fetchone()
        if not user or row['stamp'] != _user_stamp(user):
            raise Stop('lien_expire')
        salt = secrets.token_hex(16)
        db.execute('UPDATE users SET salt=?,hash=?,must_change=0 WHERE id=?', (salt, _hash(password, salt), row['user_id']))
    db.execute('UPDATE resets SET used=1 WHERE user_id=?', (row['user_id'],))
    db.commit()
    revoke(db, row['user_id'])
    _log(db, str(row['user_id']), 'mot_de_passe_reinitialise')


# ------------------------------------------------------------------------------------------------ pages publiques
MESSAGES = {
    'identifiants_incorrects': 'Identifiant ou mot de passe incorrect.',
    'trop_de_tentatives': 'Trop de tentatives : réessayez dans 15 minutes, ou utilisez « Mot de passe oublié ».',
    'code_premier_compte_incorrect': 'Code d’installation incorrect : relisez-le sur le serveur.',
    'identifiant_invalide': 'Identifiant invalide : 3 à 120 caractères (lettres, chiffres, point, tiret, @).',
    'mot_de_passe_trop_court': 'Mot de passe trop court : 16 caractères au moins.',
    'mot_de_passe_trop_court_12': 'Mot de passe trop court : 12 caractères au moins.',
    'mots_de_passe_differents': 'Les deux saisies du mot de passe sont différentes.',
    'adresse_recuperation_invalide': 'Adresse électronique de récupération invalide.',
    'lien_expire': 'Ce lien n’est plus valable : demandez-en un nouveau.',
    'compte_administrateur_existe': 'Le compte administrateur existe déjà : connectez-vous.',
    'origine_refusee': 'Formulaire refusé : rouvrez la page de connexion depuis l’adresse d’AxiorHub.',
}


def _page(prefix, title, body, lead=''):
    p = e(prefix, quote=True)
    try:
        from .i18n5631 import current
        lang = current() if current() in ('fr', 'en', 'de', 'es') else 'fr'
    except Exception:   # noqa: BLE001
        lang = 'fr'
    return ('<!doctype html><html lang="%s"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            '<meta name="color-scheme" content="light dark"><meta name="robots" content="noindex"><title>%s · AxiorHub Pilote</title>'
            '<link rel="stylesheet" href="%s/static/connexion5648.css"><link rel="icon" href="%s/static/axiorhub-icon-192.png"></head>'
            '<body class="cx48"><main class="cx48-shell">'
            '<section class="cx48-brand" aria-hidden="true"><div class="cx48-logo"><img src="%s/static/axiorhub-icon-192.png" alt="" width="56" height="56">'
            '<span>AxiorHub Pilote</span></div><p class="cx48-tag">L’agent IA du cabinet d’avocats</p>'
            '<ul class="cx48-points"><li>Vos dossiers, courriels et agendas réunis</li><li>Des projets préparés, relus par vous</li>'
            '<li>Hébergé par le cabinet, sur son serveur</li></ul></section>'
            '<section class="cx48-card"><h1>%s</h1>%s%s</section></main>'
            '<footer class="cx48-foot"><span>%s</span> · <span>logiciel libre (AGPL-3.0)</span> · '
            '<a href="%s/connexion?lang=fr" hreflang="fr">Français</a> · <a href="%s/connexion?lang=en" hreflang="en">English</a> · '
            '<a href="%s/connexion?lang=de" hreflang="de">Deutsch</a> · <a href="%s/connexion?lang=es" hreflang="es">Español</a></footer></body></html>') % (
        lang, e(title), p, p, p, e(title), ('<p class="cx48-lead">%s</p>' % lead) if lead else '', body, e(_attribution()), p, p, p, p)


def _attribution():
    from .about560 import ATTRIBUTION
    return ATTRIBUTION


def _notice(code=None, text='', kind='error'):
    message = text or MESSAGES.get(code or '', '')
    return ('<p class="cx48-msg cx48-%s" role="%s">%s</p>' % (kind, 'alert' if kind == 'error' else 'status', e(message))) if message else ''


def _form_data(env, limit=20000):
    try:
        length = int(env.get('CONTENT_LENGTH') or 0)
    except ValueError:
        length = 0
    if not 0 < length <= limit:
        return {}
    raw = env['wsgi.input'].read(length).decode('utf-8', 'replace')
    return {k: v[0] for k, v in parse_qs(raw, max_num_fields=30).items()}


def _same_origin(env, auth):
    origin = str(env.get('HTTP_ORIGIN') or '').rstrip('/')
    return not origin or origin == str(auth.get('origin') or '').rstrip('/')


def _next_path(prefix, value):
    """Retour après connexion : chemin interne d'AxiorHub seulement (jamais une autre adresse)."""
    value = str(value or '')
    if value in ('', prefix, prefix + '/'):                    # 5.6.56 (audit 5.6.55, N01) : la racine mène à Aujourd'hui
        return prefix + '/aujourdhui'
    if value.startswith(prefix + '/') and not value.startswith('//') and '\\' not in value and len(value) < 600 and not urlsplit(value).netloc:
        return value
    return prefix + '/aujourdhui'


def _client(env):
    # 5.6.70 (audit) : la PREMIÈRE adresse de X-Forwarded-For est écrite par le client (falsifiable) ; on prend celle qu'a vue le proxy
    from .garde5670 import client_ip
    return client_ip(env)


def _redirect(location, headers=()):
    return {'status': '303 See Other', 'kind': 'text/html; charset=utf-8', 'body': '', 'headers': [('Location', location), ('Cache-Control', 'no-store')] + list(headers)}


def _html(body, status='200 OK', headers=()):
    return {'status': status, 'kind': 'text/html; charset=utf-8', 'body': body, 'headers': [('Cache-Control', 'no-store')] + list(headers),
            'csp': "default-src 'none'; style-src 'self'; img-src 'self'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'"}


CSS = (Path(__file__).parent / 'static' / 'connexion5648.css')


def handle(env, auth, cfg, prefix, path):
    """Routes publiques de connexion (préfixe retiré). Renvoie la réponse, ou None si le chemin n'est pas une route de connexion."""
    method = env.get('REQUEST_METHOD', 'GET')
    if path == '/static/connexion5648.css' and method in ('GET', 'HEAD'):
        return {'status': '200 OK', 'kind': 'text/css; charset=utf-8', 'body': CSS.read_text(encoding='utf-8') if method == 'GET' else '',
                'csp': "default-src 'none'", 'headers': [('Cache-Control', 'public, max-age=3600')]}
    if not (path == '/deconnexion' or path == '/connexion' or path.startswith('/connexion/')):
        return None
    state = cfg['state_dir']
    secure = str(auth.get('origin') or '').startswith('https://')
    query = parse_qs(env.get('QUERY_STRING') or '')
    lang_headers = []
    if query.get('lang', [''])[0] in ('fr', 'en', 'de', 'es'):
        from .i18n5631 import cookie_header as _lang_cookie, set_lang
        lang_headers = [_lang_cookie(query['lang'][0], prefix + '/')]
        set_lang(query['lang'][0])
    db = connect(state)
    try:
        if method == 'POST' and not _same_origin(env, auth):
            return _html(_page(prefix, 'Connexion', _notice('origine_refusee') + '<p><a href="%s/connexion">Connexion</a></p>' % e(prefix)), '403 Forbidden')
        if path == '/deconnexion':
            if method != 'POST':
                return _redirect(prefix + '/connexion')
            token = cookie_value(env)
            if token:
                close_session(db, token)
            return _redirect(prefix + '/connexion?sortie=1', [cookie_header(prefix, None, secure=secure)])
        if first_run(auth) and path != '/connexion/premier-compte':
            return _redirect(prefix + '/connexion/premier-compte', lang_headers)
        if path == '/connexion':
            return _login(db, env, auth, prefix, query, secure, lang_headers)
        if path == '/connexion/premier-compte':
            return _first(db, env, auth, state, prefix)
        if path == '/connexion/oubli':
            return _forgot(db, env, auth, cfg, prefix)
        if path == '/connexion/nouveau':
            return _new_password(db, env, auth, state, prefix, query)
        if path == '/connexion/changer':
            return _change(db, env, auth, prefix)
        if path == '/connexion/appareil':   # 5.6.74 : associer un téléphone (invitation à usage unique du QR code)
            from .appareils5674 import SCHEMA as _ap74, pairing as _pairing
            db.executescript(_ap74)
            return _pairing(db, env, auth, prefix, secure, _form_data(env) if method == 'POST' else None, query)
        return _redirect(prefix + '/connexion')
    finally:
        db.close()


def _login(db, env, auth, prefix, query, secure, lang_headers):
    error, ident = '', ''
    nxt = _next_path(prefix, query.get('suite', [''])[0])
    if env.get('REQUEST_METHOD') == 'POST':
        form = _form_data(env)
        ident = str(form.get('ident') or '')[:120]
        nxt = _next_path(prefix, form.get('suite'))
        try:
            user = check_password(db, auth, ident, str(form.get('password') or ''), _client(env))
        except Stop as ex:
            error = str(ex)
            if error == 'identifiants_incorrects':   # 5.6.70 : 10 échecs en 10 minutes bloquent l'adresse (garde des accès)
                try:   # même base que la garde du service web (acces5670.sqlite3, à côté de la base des comptes)
                    from .garde5670 import open_garde
                    _g70 = open_garde(str(Path(db.execute('PRAGMA database_list').fetchone()[2]).parent))
                    try:
                        _g70.record(_client(env), 'echec_connexion', ident[:80])
                    finally:
                        _g70.db.close()
                except Exception:   # noqa: BLE001
                    pass
        else:
            token, age = open_session(db, auth, user, form.get('remember') == '1', _client(env))
            target = prefix + '/connexion/changer' if user['must_change'] else nxt
            return _redirect(target, [cookie_header(prefix, token, age, secure)])
    status = '429 Too Many Requests' if error == 'trop_de_tentatives' else ('401 Unauthorized' if error else '200 OK')
    info = _notice(text='Vous êtes déconnecté(e).', kind='ok') if query.get('sortie') else (
        _notice(text='Mot de passe enregistré : connectez-vous.', kind='ok') if query.get('nouveau') else '')
    body = (_notice(error) + info +
            '<form method="post" action="%s/connexion" class="cx48-form" autocomplete="on">'
            '<input type="hidden" name="suite" value="%s">'
            '<label for="cx48-ident">Identifiant ou adresse électronique</label>'
            '<input id="cx48-ident" name="ident" value="%s" autocomplete="username" required autofocus maxlength="120" autocapitalize="none" spellcheck="false">'
            '<label for="cx48-pass">Mot de passe</label>'
            '<input id="cx48-pass" name="password" type="password" autocomplete="current-password" required>'
            '<label class="cx48-check"><input type="checkbox" name="remember" value="1"> Se souvenir de moi sur cet appareil (30 jours)</label>'
            '<button type="submit">Se connecter</button></form>'
            '<p class="cx48-links"><a href="%s/connexion/oubli">Mot de passe oublié ?</a></p>') % (
        e(prefix, quote=True), e(nxt, quote=True), e(ident, quote=True), e(prefix, quote=True))
    return _html(_page(prefix, 'Connexion', body, 'Connectez-vous pour ouvrir l’espace de travail du cabinet.'), status, lang_headers)


def _first(db, env, auth, state, prefix):
    if not first_run(auth):
        return _redirect(prefix + '/connexion')
    bootstrap_code(state)
    error = ''
    form = {}
    if env.get('REQUEST_METHOD') == 'POST':
        form = _form_data(env)
        if _limited(db, 'ip:' + _client(env), 'ip'):
            error = 'trop_de_tentatives'
        else:
            try:
                create_first_admin(db, state, auth, form)
            except Stop as ex:
                error = str(ex)
                _attempt(db, 'ip:' + _client(env))
            else:
                return _redirect(prefix + '/connexion?nouveau=1')
    body = (_notice(error) +
            '<div class="cx48-steps"><p>Ce compte sera l’administrateur du cabinet. Pour prouver que vous installez ce serveur, '
            'saisissez le code privé écrit sur le serveur :</p><pre>sudo cat %s</pre></div>'
            '<form method="post" action="%s/connexion/premier-compte" class="cx48-form" autocomplete="off">'
            '<label for="cx48-code">Code d’installation</label><input id="cx48-code" name="code" required autocomplete="one-time-code" spellcheck="false">'
            '<label for="cx48-ident">Identifiant</label><input id="cx48-ident" name="ident" value="%s" required maxlength="120" autocomplete="username">'
            '<label for="cx48-mail">Adresse électronique de récupération (facultative)</label>'
            '<input id="cx48-mail" name="email" type="email" value="%s" autocomplete="email" maxlength="200">'
            '<label for="cx48-pass">Mot de passe (16 caractères au moins)</label>'
            '<input id="cx48-pass" name="password" type="password" minlength="16" required autocomplete="new-password">'
            '<label for="cx48-conf">Répétez le mot de passe</label>'
            '<input id="cx48-conf" name="confirm" type="password" minlength="16" required autocomplete="new-password">'
            '<button type="submit">Créer le compte administrateur</button></form>') % (
        e(str(Path(state) / CODE_NAME)), e(prefix, quote=True), e(str(form.get('ident') or 'admin'), quote=True), e(str(form.get('email') or ''), quote=True))
    return _html(_page(prefix, 'Premier lancement', body, 'Bienvenue : créez le compte administrateur du cabinet.'), '400 Bad Request' if error else '200 OK')


def _forgot(db, env, auth, cfg, prefix):
    if env.get('REQUEST_METHOD') == 'POST':
        form = _form_data(env)
        request_reset(db, auth, cfg, form.get('ident'), _client(env), str(auth.get('origin') or '').rstrip('/') + prefix)
        body = (_notice(text='Si ce compte existe et qu’une adresse de récupération est enregistrée, un lien valable 30 minutes vient d’être '
                             'envoyé. Pensez à regarder dans les indésirables.', kind='ok') +
                '<p class="cx48-small">Pas de courriel ? L’administrateur du cabinet peut vous donner un mot de passe provisoire ; sur le serveur : '
                '<code>sudo python3 /opt/axiorhub-mail-agent/current/reinitialiser-acces.py</code></p>'
                '<p class="cx48-links"><a href="%s/connexion">Retour à la connexion</a></p>') % e(prefix, quote=True)
        return _html(_page(prefix, 'Mot de passe oublié', body))
    body = ('<form method="post" action="%s/connexion/oubli" class="cx48-form">'
            '<label for="cx48-ident">Identifiant ou adresse électronique</label>'
            '<input id="cx48-ident" name="ident" required maxlength="200" autocomplete="username" autofocus>'
            '<button type="submit">Recevoir un lien</button></form><p class="cx48-links"><a href="%s/connexion">Retour à la connexion</a></p>') % (
        e(prefix, quote=True), e(prefix, quote=True))
    return _html(_page(prefix, 'Mot de passe oublié', body, 'Un lien pour choisir un nouveau mot de passe sera envoyé à votre adresse de récupération.'))


def _new_password(db, env, auth, state, prefix, query):
    token = (query.get('jeton', [''])[0])[:200]
    error = ''
    if env.get('REQUEST_METHOD') == 'POST':
        form = _form_data(env)
        token = str(form.get('jeton') or '')[:200]
        try:
            reset_with_token(db, state, auth, token, str(form.get('password') or ''), str(form.get('confirm') or ''))
        except Stop as ex:
            error = str(ex)
        else:
            return _redirect(prefix + '/connexion?nouveau=1')
    row = db.execute('SELECT * FROM resets WHERE token_hash=? AND used=0', (_token_hash(token),)).fetchone() if token else None
    if not row or datetime.fromisoformat(row['expires']) <= _now():
        return _html(_page(prefix, 'Nouveau mot de passe', _notice('lien_expire') + '<p class="cx48-links"><a href="%s/connexion/oubli">Demander un nouveau lien</a></p>'
                           % e(prefix, quote=True)), '400 Bad Request')
    minimum = MIN_ADMIN if row['user_id'] == ADMIN_ID else MIN_USER
    body = (_notice(error) + '<form method="post" action="%s/connexion/nouveau" class="cx48-form">'
            '<input type="hidden" name="jeton" value="%s">'
            '<label for="cx48-pass">Nouveau mot de passe (%d caractères au moins)</label>'
            '<input id="cx48-pass" name="password" type="password" minlength="%d" required autocomplete="new-password">'
            '<label for="cx48-conf">Répétez le mot de passe</label>'
            '<input id="cx48-conf" name="confirm" type="password" minlength="%d" required autocomplete="new-password">'
            '<button type="submit">Enregistrer</button></form>') % (e(prefix, quote=True), e(token, quote=True), minimum, minimum, minimum)
    return _html(_page(prefix, 'Nouveau mot de passe', body))


def _change(db, env, auth, prefix):
    user = session_user(db, auth, cookie_value(env))
    if not user:
        return _redirect(prefix + '/connexion')
    if user['id'] == ADMIN_ID:
        return _redirect(prefix + '/parametres?rubrique=comptes')
    error = ''
    if env.get('REQUEST_METHOD') == 'POST':
        form = _form_data(env)
        try:
            change_own(db, user, str(form.get('password') or ''), str(form.get('confirm') or ''))
        except Stop as ex:
            error = str(ex)
        else:
            return _redirect(prefix + '/connexion?nouveau=1', [cookie_header(prefix, None, secure=str(auth.get('origin') or '').startswith('https://'))])
    body = (_notice(error) + '<form method="post" action="%s/connexion/changer" class="cx48-form">'
            '<label for="cx48-pass">Nouveau mot de passe (12 caractères au moins)</label>'
            '<input id="cx48-pass" name="password" type="password" minlength="12" required autocomplete="new-password">'
            '<label for="cx48-conf">Répétez le mot de passe</label>'
            '<input id="cx48-conf" name="confirm" type="password" minlength="12" required autocomplete="new-password">'
            '<button type="submit">Enregistrer mon mot de passe</button></form>') % e(prefix, quote=True)
    return _html(_page(prefix, 'Votre mot de passe', body, 'Première connexion : choisissez votre mot de passe personnel.'))


# ------------------------------------------------------------------------------------------------ menu : compte et déconnexion
def user_block(prefix, user):
    """Pied du menu : nom, rôle, « Se déconnecter » (formulaire POST, même origine)."""
    return ('<div class="cx48-user"><span class="cx48-user-name">%s</span><small>%s</small>'
            '<form method="post" action="%s/deconnexion"><button type="submit" class="cx48-logout">Se déconnecter</button></form></div>') % (
        e(user.get('name') or user.get('ident') or ''), e(ROLES.get(user.get('role'), '')), e(prefix, quote=True))


# ------------------------------------------------------------------------------------------------ réinitialisation sur le serveur
def cli_reset(argv=None):
    """``sudo python3 reinitialiser-acces.py`` : nouveau mot de passe pour un compte (administrateur compris), sessions fermées."""
    import getpass
    import os
    config = Path('/etc/axiorhub-mail-agent/config.json')
    state = Path(json.loads(config.read_text(encoding='utf-8'))['state_dir'])
    auth = json.loads(Path('/etc/axiorhub-mail-agent/ui-auth.json').read_text(encoding='utf-8'))
    from .comptes5642 import overlay
    overlay(auth, str(state))
    db = connect(state)
    names = [str(auth.get('username') or 'admin')] + [u['ident'] for u in list_users(db)]
    print('Comptes : ' + ', '.join(names))
    ident = (argv[0] if argv else input('Identifiant à réinitialiser : ')).strip()
    password = getpass.getpass('Nouveau mot de passe : ')
    if password != getpass.getpass('Répétez le mot de passe : '):
        raise SystemExit('Mots de passe différents.')
    if ident.lower() == names[0].lower():
        if len(password) < MIN_ADMIN:
            raise SystemExit('16 caractères au moins pour l’administrateur.')
        _write_admin(state, names[0], password)
        revoke(db, ADMIN_ID)
    else:
        row = db.execute('SELECT * FROM users WHERE lower(ident)=?', (ident.lower(),)).fetchone()
        if not row:
            raise SystemExit('Compte inconnu.')
        if len(password) < MIN_USER:
            raise SystemExit('12 caractères au moins.')
        salt = secrets.token_hex(16)
        db.execute('UPDATE users SET salt=?,hash=?,must_change=1,active=1 WHERE id=?', (salt, _hash(password, salt), row['id']))
        db.commit()
        revoke(db, row['id'])
    _log(db, 'serveur', 'reinitialisation_en_ligne_de_commande', ident)
    stat = state.stat()
    for name in (DB_NAME, 'compte-interface5642.json'):
        target = state / name
        if target.exists() and hasattr(os, 'chown'):
            try:
                os.chown(target, stat.st_uid, stat.st_gid)
            except OSError:
                pass
    print('Mot de passe de « %s » réinitialisé ; ses sessions sont fermées.' % ident)


# ------------------------------------------------------------------------------------------------ Paramètres › Comptes (version serveur)
def settings_html(state_dir, auth):
    """Adresse de récupération de l'administrateur et comptes du cabinet (création, rôle, désactivation, mot de passe provisoire)."""
    db = connect(state_dir)
    try:
        email = _setting(db, 'admin_email')
        users = list_users(db)
        smtp_note = ''
    finally:
        db.close()
    options = lambda current: ''.join('<option value="%s"%s>%s</option>' % (k, ' selected' if k == current else '', e(v)) for k, v in ROLES.items())
    rows = ''.join(
        '<tr><td><strong>%s</strong><br><small>%s</small></td><td>%s</td><td>%s</td><td>%s</td><td class="cx48-actions">'
        '<form class="m5-form m5-inline" data-api="m5636/comptes/modifier" data-reload><input type="hidden" name="id" value="%d">'
        '<input type="hidden" name="action" value="role"><select name="role" aria-label="Rôle">%s</select>'
        '<button class="ax-btn ghost" type="submit">Changer le rôle</button><p role="status" class="m5-status"></p></form>'
        '<form class="m5-form m5-inline" data-api="m5636/comptes/modifier" data-reload><input type="hidden" name="id" value="%d">'
        '<input type="hidden" name="action" value="%s"><button class="ax-btn %s" type="submit">%s</button><p role="status" class="m5-status"></p></form>'
        '<form class="m5-form m5-inline" data-api="m5636/comptes/modifier" data-reload autocomplete="off"><input type="hidden" name="id" value="%d">'
        '<input type="hidden" name="action" value="provisoire"><input type="password" name="password" minlength="12" required autocomplete="new-password" '
        'aria-label="Mot de passe provisoire" placeholder="Mot de passe provisoire"><button class="ax-btn ghost" type="submit">Donner un mot de passe '
        'provisoire</button><p role="status" class="m5-status"></p></form></td></tr>' % (
            e(u['name'] or u['ident']), e(u['ident']), e(ROLES.get(u['role'], u['role'])), e(u['email'] or '—'),
            ('Actif' if u['active'] else 'Désactivé') + (' · mot de passe à changer' if u['must_change'] else '') +
            ((' · dernière connexion %s' % e(u['last_login'][:16].replace('T', ' '))) if u['last_login'] else ''),
            u['id'], options(u['role']), u['id'], 'desactiver' if u['active'] else 'activer', 'danger' if u['active'] else 'ghost',
            'Désactiver' if u['active'] else 'Réactiver', u['id'])
        for u in users)
    return ('<section class="ax-card" id="recuperation"><h2>Adresse de récupération</h2>'
            '<p class="ax-muted">« Mot de passe oublié » envoie un lien valable 30 minutes à cette adresse (envoi par le serveur SMTP de '
            'Paramètres › Messagerie). Sans adresse ou sans envoi configuré, le mot de passe se réinitialise sur le serveur : '
            '<code>sudo python3 /opt/axiorhub-mail-agent/current/reinitialiser-acces.py</code>.</p>'
            '<form class="m5-form" data-api="m5636/comptes/recuperation" autocomplete="off">'
            '<label>Adresse électronique de l’administrateur<input type="email" name="email" value="%s" maxlength="200" autocomplete="email"></label>'
            '<button class="ax-btn" type="submit">Enregistrer l’adresse</button><p role="status" class="m5-status"></p></form></section>'
            '<section class="ax-card" id="comptes-cabinet"><h2>Comptes du cabinet</h2>'
            '<p class="ax-muted">Chaque avocat ou assistant se connecte avec son propre compte. Le mot de passe provisoire est à changer à la '
            'première connexion. Droits : l’avocat travaille sur tous les dossiers sans les réglages techniques ; l’assistant consulte, '
            'prépare et gère l’agenda, sans valider ni déposer.</p>'
            '%s'
            '<details class="cx48-add"><summary>Ajouter un compte</summary>'
            '<form class="m5-form" data-api="m5636/comptes/ajouter" data-reload autocomplete="off">'
            '<label>Identifiant (de préférence l’adresse électronique)<input name="ident" required maxlength="120" pattern="[A-Za-z0-9._@+\\-]{3,120}"></label>'
            '<label>Nom affiché<input name="name" maxlength="80"></label>'
            '<label>Adresse de récupération (si l’identifiant n’est pas une adresse)<input type="email" name="email" maxlength="200"></label>'
            '<label>Rôle<select name="role">%s</select></label>'
            '<label>Mot de passe provisoire (12 caractères au moins)<input type="password" name="password" minlength="12" required autocomplete="new-password"></label>'
            '<button class="ax-btn" type="submit">Créer le compte</button><p role="status" class="m5-status"></p></form></details></section>') % (
        e(email, quote=True),
        ('<div class="ax-table-wrap"><table class="cx48-table"><thead><tr><th>Compte</th><th>Rôle</th><th>Adresse</th><th>État</th><th>Actions</th>'
         '</tr></thead><tbody>%s</tbody></table></div>' % rows) if rows else '<p>Aucun autre compte pour l’instant.</p>',
        options('avocat'))
