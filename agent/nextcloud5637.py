"""5.6.37 : l'interface AxiorHub affichée dans Nextcloud (application « AxiorHub Pilote » 0.4.0).

Pourquoi : l'avocat veut retrouver SON interface AxiorHub (Aujourd'hui, Dossiers, Courriels, Agenda, Produire, Corporate…) dans
Nextcloud, sans saisir de nouveau son mot de passe et sans ouvrir AxiorHub à d'autres sites.

Fonctionnement :
- l'application Nextcloud demande, pour l'utilisateur relié, ``POST /integration/v1/session`` (requête signée avec SA clé personnelle,
  contrat 1.3) ; le moteur remet une adresse de connexion à usage unique valable 60 secondes ; seule l'empreinte SHA-256 du jeton est
  conservée ;
- le navigateur ouvre ``<préfixe>/nextcloud-session?jeton=…&suite=/aujourdhui`` dans le cadre ; le jeton est réclamé atomiquement
  (un seul échange), puis remplacé par un cookie de session ``axh_nc`` (HttpOnly ; sur HTTPS : Secure, SameSite=None, Partitioned) de
  12 heures, limité au préfixe ; seule son empreinte est conservée ;
- la session tombe avec la clé : clé révoquée dans AxiorHub, compte délié dans Nextcloud ou liaison désactivée = session refusée ;
- l'affichage dans un cadre n'est permis qu'au site Nextcloud déclaré par CETTE clé (``frame-ancestors 'self' <origine>``), et
  seulement pour les réponses à une session Nextcloud ; une connexion par mot de passe garde ``frame-ancestors 'none'`` ;
- le jeton CSRF et le contrôle de l'origine des écritures sont inchangés (le cadre a l'origine d'AxiorHub).

Distribution autonome (Docker, comptes du cabinet) : la couche des comptes est devant le moteur ; l'adresse remise est celle de la page
demandée et le cadre affiche la page de connexion AxiorHub.
"""
from datetime import datetime, timezone
from html import escape
import hashlib
import re
import secrets
import time
from urllib.parse import parse_qs, urlencode, urlsplit

from .common import Stop

COOKIE = 'axh_nc'
TOKEN_SECONDS = 60
SESSION_SECONDS = 12 * 3600
ROLES = ('administrateur', 'avocat')
ORIGIN = re.compile(r'https?://[a-z0-9.-]{1,190}(:\d{1,5})?|https?://\[[0-9a-f:]{2,45}\](:\d{1,5})?')
DEFAULT_SUITE = '/aujourdhui'


def schema(desk):
    desk.db.executescript('''
    CREATE TABLE IF NOT EXISTS integration_sessions5637(sha TEXT PRIMARY KEY, kind TEXT NOT NULL, key_id TEXT NOT NULL, origin TEXT NOT NULL DEFAULT '',
      expires REAL NOT NULL, created TEXT NOT NULL);
    CREATE INDEX IF NOT EXISTS integration_sessions5637_key ON integration_sessions5637(key_id);''')


def _sha(value):
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def clean_origin(value):
    """Origine Nextcloud déclarée par l'application : schéma, hôte, port ; rien d'autre (ni chemin, ni joker)."""
    value = str(value or '').strip().lower().rstrip('/')
    if not value:
        return ''
    if len(value) > 240 or not ORIGIN.fullmatch(value):
        raise Stop('origine_nextcloud_invalide')
    return value


def clean_suite(value):
    """Page d'arrivée : chemin interne d'AxiorHub uniquement (jamais une autre adresse, jamais l'intégration elle-même)."""
    value = str(value or '') or DEFAULT_SUITE
    if (not value.startswith('/') or value.startswith('//') or '\\' in value or len(value) > 500 or any(ord(c) < 32 for c in value)
            or value.startswith(('/integration/', '/nextcloud-session'))):
        return DEFAULT_SUITE
    return value


def standalone():
    from .missions567 import auth_root
    return (auth_root() / 'users.sqlite3').is_file()


def create(desk, ctx, data, auth):
    """``POST /integration/v1/session`` : adresse de connexion à usage unique pour le compte relié."""
    from .integration5634 import Refused
    if not ctx.get('_key'):
        raise Refused('cle_personnelle_requise', '400 Bad Request')
    if ctx.get('_role') not in ROLES:
        raise Refused('role_insuffisant', '403 Forbidden')
    try:
        origin = clean_origin(data.get('origin'))
    except Stop:
        raise Refused('origine_nextcloud_invalide') from None
    suite = clean_suite(data.get('suite'))
    base_origin = str((auth or {}).get('origin') or '').rstrip('/')
    prefix = str((auth or {}).get('prefix', '/agent-courriel'))
    base = base_origin + prefix
    if standalone():   # la couche des comptes du cabinet gère la connexion
        return {'url': base + suite, 'base': base, 'frame_origin': base_origin, 'expires_in': 0, 'standalone': True}
    schema(desk)
    now = time.time()
    token = secrets.token_urlsafe(32)
    desk.db.execute('DELETE FROM integration_sessions5637 WHERE expires<?', (now,))
    desk.db.execute('INSERT INTO integration_sessions5637(sha,kind,key_id,origin,expires,created) VALUES(?,?,?,?,?,?)',
                    (_sha(token), 'jeton', ctx['_key'], origin, now + TOKEN_SECONDS, datetime.now(timezone.utc).isoformat(timespec='seconds')))
    desk.db.commit()
    return {'url': base + '/nextcloud-session?' + urlencode({'jeton': token, 'suite': suite}), 'base': base, 'frame_origin': base_origin,
            'expires_in': TOKEN_SECONDS, 'standalone': False}


def _cookie(sid, auth, max_age):
    prefix = auth.get('prefix', '/agent-courriel') or '/'
    secure = urlsplit(str(auth.get('origin', ''))).scheme == 'https'
    return '%s=%s; Path=%s; Max-Age=%d; HttpOnly; %s' % (COOKIE, sid, prefix, max_age,
                                                       'Secure; SameSite=None; Partitioned' if secure else 'SameSite=Lax')


def denied_page(prefix, reason='expire'):
    """Page sans donnée du cabinet (affichable dans le cadre) : l'application Nextcloud redemande une session et l'affiche."""
    prefix = escape(str(prefix or ''), quote=True)
    text = {'expire': 'Lien de connexion expiré ou déjà utilisé.',
            'bloque': 'Session AxiorHub absente dans ce cadre : votre navigateur bloque peut-être les cookies d’AxiorHub dans Nextcloud.'}[reason]
    return {'status': '403 Forbidden' if reason == 'expire' else '401 Unauthorized', 'kind': 'text/html; charset=utf-8',
            'body': ('<!doctype html><html lang="fr"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
                     '<title>AxiorHub</title><meta name="axiorhub-integre" content="' + reason + '"></head><body style="font-family:sans-serif;padding:24px">'
                     '<h1 style="font-size:1.2rem">AxiorHub</h1><p>' + text + '</p><p>Rechargez la page Nextcloud, ou utilisez « Ouvrir dans un onglet ».</p>'
                     '<p><a href="' + prefix + DEFAULT_SUITE + '" target="_blank" rel="noopener">Ouvrir AxiorHub dans un onglet</a></p>'
                     '<script src="' + prefix + '/nextcloud-session/signal.js"></script></body></html>'),
            'csp': "default-src 'none'; script-src 'self'; style-src 'unsafe-inline'; frame-ancestors *; base-uri 'none'; form-action 'none'", 'frame_any': True}


SIGNAL_JS = ("/* AxiorHub 5.6.37 : prévient l'application Nextcloud (cadre parent) que la session doit être renouvelée. */\n"
             "(function(){try{var m=document.querySelector('meta[name=\"axiorhub-integre\"]');"
             "if(window.parent!==window&&m){window.parent.postMessage({axiorhub:'session',etat:m.content},'*');}}catch(e){}})();\n")


def signal_js():
    return {'status': '200 OK', 'kind': 'text/javascript; charset=utf-8', 'body': SIGNAL_JS, 'csp': "default-src 'none'"}


def exchange(desk, env, auth):
    """``GET <préfixe>/nextcloud-session?jeton=…&suite=…`` : jeton réclamé une seule fois, cookie de session, redirection."""
    prefix = auth.get('prefix', '/agent-courriel')
    query = parse_qs(env.get('QUERY_STRING', ''), max_num_fields=5)
    token = (query.get('jeton') or [''])[0]
    suite = clean_suite((query.get('suite') or [''])[0])
    if not token or len(token) > 200:
        return denied_page(prefix)
    schema(desk)
    now = time.time()
    sha = _sha(token)
    row = desk.db.execute("SELECT key_id,origin,expires FROM integration_sessions5637 WHERE sha=? AND kind='jeton'", (sha,)).fetchone()
    cur = desk.db.execute("DELETE FROM integration_sessions5637 WHERE sha=? AND kind='jeton'", (sha,))   # réclamation : un seul échange
    desk.db.commit()
    if row is None or not cur.rowcount or float(row['expires']) < now or not _key_active(desk, row['key_id'], auth):
        return denied_page(prefix)
    sid = secrets.token_urlsafe(32)
    desk.db.execute('INSERT INTO integration_sessions5637(sha,kind,key_id,origin,expires,created) VALUES(?,?,?,?,?,?)',
                    (_sha(sid), 'session', row['key_id'], row['origin'], now + SESSION_SECONDS, datetime.now(timezone.utc).isoformat(timespec='seconds')))
    desk.db.commit()
    desk.audit('nextcloud5637_session', {'key': row['key_id'][:12]})
    # 5.6.38 : redirection affichable dans le cadre du seul site Nextcloud de la clé (jamais « X-Frame-Options: DENY »)
    return {'status': '303 See Other', 'kind': 'text/plain; charset=utf-8', 'body': '', 'frame_any': True,
            'csp': "default-src 'none'; frame-ancestors 'self'" + ((' ' + row['origin']) if row['origin'] else ''),
            'headers': [('Location', prefix + suite), ('Set-Cookie', _cookie(sid, auth, SESSION_SECONDS))]}


def _key_active(desk, key_id, auth=None):
    """Clé active, liaison autorisée, compte relié encore actif avec un rôle autorisé (audit 5.6.36, F02 : état ACTUEL du compte)."""
    from . import integration5636
    if not desk.settings(integration5636.SETTING, False):
        return False
    key = integration5636.key_secret(desk, key_id)
    if key is None:
        return False
    account = integration5636.current_account(desk, key, auth)
    return bool(account) and account['role'] in ROLES


def cookie_value(env):
    raw = env.get('HTTP_COOKIE', '')
    if COOKIE + '=' not in raw or len(raw) > 8192:
        return ''
    value = ''
    for part in raw.split(';'):
        name, _, val = part.strip().partition('=')
        if name == COOKIE:
            value = val
    return value if 0 < len(value) <= 200 else ''


def session(desk, value, auth=None):
    """Session Nextcloud valide (clé active, liaison autorisée) : {'key_id', 'origin'} ; sinon None."""
    if not value:
        return None
    schema(desk)
    row = desk.db.execute("SELECT key_id,origin,expires FROM integration_sessions5637 WHERE sha=? AND kind='session'", (_sha(value),)).fetchone()
    if row is None or float(row['expires']) < time.time() or not _key_active(desk, row['key_id'], auth):
        return None
    return {'key_id': row['key_id'], 'origin': row['origin']}


def end_for_key(desk, key_id):
    """Clé révoquée ou compte délié : toutes ses sessions et adresses de connexion tombent."""
    try:
        schema(desk)
        desk.db.execute('DELETE FROM integration_sessions5637 WHERE key_id=?', (str(key_id),))
    except Exception:   # noqa: BLE001 — la révocation de la clé suffit (session refusée sans clé active)
        pass


def frame_headers(headers, origin):
    """Réponse à une session Nextcloud : cadre permis au seul site Nextcloud de la clé (et à AxiorHub lui-même)."""
    ancestors = "frame-ancestors 'self'" + (' ' + origin if origin else '')
    out = []
    for name, value in headers:
        if name == 'X-Frame-Options':
            continue
        if name == 'Content-Security-Policy':
            value = re.sub(r"frame-ancestors [^;]*", ancestors, value) if 'frame-ancestors' in value else value + '; ' + ancestors
        out.append((name, value))
    return out
