"""5.6.25 : connexion de la fenêtre locale du poste sans mot de passe à saisir.

Au lancement, le poste écrit l'empreinte d'un jeton aléatoire à usage unique (valable deux minutes) à côté de ``ui-auth.json`` et
ouvre ``/poste-session?jeton=…``. L'interface échange ce jeton contre un cookie de session (``HttpOnly``, ``SameSite=Strict``,
limité au préfixe) : le jeton est réclamé atomiquement puis effacé, qu'il soit valide ou non. Seules les empreintes SHA-256 du
jeton et des sessions sont écrites sur le disque. Ce mécanisme n'existe que si ``ui-auth.json`` porte ``"poste": true`` (poste
de travail) ; un serveur n'est jamais concerné. Le contrôle de l'hôte et le jeton CSRF des écritures restent inchangés.
"""
import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import time
from urllib.parse import parse_qs

LAUNCH = 'poste-lancement.json'
SESSIONS = 'poste-sessions.json'
COOKIE = 'axh_poste'
LAUNCH_SECONDS = 120
SESSION_SECONDS = 12 * 3600


def _sha(value):
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def _write_private(path, data):
    tmp = path.with_name(path.name + '.' + secrets.token_hex(4) + '.tmp')
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as handle:
        json.dump(data, handle)
    os.replace(tmp, path)


def new_launch_token(auth_path, ttl=LAUNCH_SECONDS):
    """Jeton de lancement à usage unique ; seule son empreinte est écrite."""
    token = secrets.token_urlsafe(32)
    _write_private(Path(auth_path).parent / LAUNCH, {'sha256': _sha(token), 'expires': time.time() + ttl})
    return token


def _sessions(auth_path):
    try:
        data = json.loads((Path(auth_path).parent / SESSIONS).read_text(encoding='utf-8'))
        return {k: float(v) for k, v in data.items()} if isinstance(data, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def exchange(auth_path, env, prefix):
    """Réponse à ``GET /poste-session`` : cookie de session et redirection, ou refus (403)."""
    deny = {'status': '403 Forbidden', 'kind': 'text/html; charset=utf-8',
            'body': '<p>Lien de connexion locale expiré ou déjà utilisé : relancez AxiorHub Pilote depuis le menu Démarrer.</p>'}
    query = parse_qs(env.get('QUERY_STRING', ''))
    token = (query.get('jeton') or [''])[0]
    suite = (query.get('suite') or ['/'])[0]
    path = Path(auth_path).parent / LAUNCH
    claimed = path.with_name(path.name + '.' + secrets.token_hex(6) + '.reclame')
    try:
        os.replace(path, claimed)   # réclamation atomique : un seul échange possible
    except OSError:
        return deny
    try:
        data = json.loads(claimed.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        data = {}
    finally:
        try:
            claimed.unlink()
        except OSError:
            pass
    if not token or len(token) > 200 or float(data.get('expires', 0)) < time.time() or not hmac.compare_digest(_sha(token), str(data.get('sha256', ''))):
        return deny
    sid = secrets.token_urlsafe(32)
    now = time.time()
    sessions = {k: v for k, v in _sessions(auth_path).items() if v > now}
    sessions[_sha(sid)] = now + SESSION_SECONDS
    _write_private(Path(auth_path).parent / SESSIONS, dict(list(sessions.items())[-20:]))
    if not suite.startswith('/') or suite.startswith('//') or '\\' in suite or any(ord(c) < 32 for c in suite):
        suite = '/'
    return {'status': '303 See Other', 'kind': 'text/plain; charset=utf-8', 'body': '',
            'headers': [('Location', (prefix or '') + suite),
                        ('Set-Cookie', '%s=%s; Path=%s; HttpOnly; SameSite=Strict' % (COOKIE, sid, prefix or '/'))]}


def cookie_ok(auth_path, env):
    """Vrai si la requête porte un cookie de session du poste encore valide."""
    raw = env.get('HTTP_COOKIE', '')
    if COOKIE + '=' not in raw or len(raw) > 8192:
        return False
    value = ''
    for part in raw.split(';'):
        name, _, val = part.strip().partition('=')
        if name == COOKIE:
            value = val
    if not value or len(value) > 200:
        return False
    expires = _sessions(auth_path).get(_sha(value), 0)
    return expires > time.time()
