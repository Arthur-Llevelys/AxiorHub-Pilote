"""5.6.25 : connexion de la fenêtre locale du poste sans mot de passe à saisir.

Au lancement, le poste écrit l'empreinte d'un jeton aléatoire à usage unique (valable deux minutes) à côté de ``ui-auth.json`` et
ouvre ``/poste-session?jeton=…``. L'interface échange ce jeton contre un cookie de session (``HttpOnly``, ``SameSite=Strict``,
limité au préfixe) : le jeton est réclamé atomiquement puis effacé, qu'il soit valide ou non. Seules les empreintes SHA-256 du
jeton et des sessions sont écrites sur le disque. Ce mécanisme n'existe que si ``ui-auth.json`` porte ``"poste": true`` (poste
de travail) ; un serveur n'est jamais concerné. Le contrôle de l'hôte et le jeton CSRF des écritures restent inchangés.

5.6.110 : la session durait douze heures fixes, même pendant le travail ; passé ce délai, l'interface demandait des identifiants
(« Se connecter à http://127.0.0.1:… »). Elle est maintenant **prolongée par l'usage** : sept jours sans activité au plus
(``IDLE_SECONDS``), trente jours au plus depuis l'ouverture (``MAX_SECONDS``), une écriture toutes les dix minutes au plus. Une session
expirée ne déclenche plus la demande d'identifiants du navigateur : la page dit de rouvrir AxiorHub Pilote (``page_expiree``).
"""
import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import threading
import time
from urllib.parse import parse_qs

LAUNCH = 'poste-lancement.json'
SESSIONS = 'poste-sessions.json'
COOKIE = 'axh_poste'
LAUNCH_SECONDS = 120
SESSION_SECONDS = 12 * 3600      # durée d'origine (sessions ouvertes avant la 5.6.110)
IDLE_SECONDS = 7 * 86400         # 5.6.110 : sans activité
MAX_SECONDS = 30 * 86400         # 5.6.110 : depuis l'ouverture de la session
RENEW_SECONDS = 600              # 5.6.110 : une prolongation écrite toutes les dix minutes au plus
_VERROU = threading.Lock()


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
        if not isinstance(data, dict):
            return {}
        out = {}
        for k, v in data.items():
            if isinstance(v, (list, tuple)) and len(v) == 2:
                out[k] = (float(v[0]), float(v[1]))
            else:                      # session d'avant la 5.6.110 : une seule date ; elle se prolonge comme les autres
                out[k] = (float(v), float(v) - SESSION_SECONDS + MAX_SECONDS)
        return out
    except (OSError, ValueError, TypeError):
        return {}


def exchange(auth_path, env, prefix):
    """Réponse à ``GET /poste-session`` : cookie de session et redirection, ou refus (403)."""
    deny = {'status': '403 Forbidden', 'kind': 'text/html; charset=utf-8',
            'body': '<p>Lien de connexion locale expiré ou déjà utilisé : relancez AxiorHub Pilote depuis le menu Démarrer.</p>'}
    if os.environ.get('AXIORHUB_DEMO_HOTE') == '1':   # 5.6.107 (lot M) : démo hébergée (lot H) — ni poste, ni menu Démarrer
        deny['body'] = '<p>Lien de la démonstration expiré ou déjà utilisé : recommencez la démo depuis la page d’accueil.</p>'
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
    with _VERROU:
        sessions = {k: list(v) for k, v in _sessions(auth_path).items() if v[0] > now}
        sessions[_sha(sid)] = [now + IDLE_SECONDS, now + MAX_SECONDS]
        _write_private(Path(auth_path).parent / SESSIONS, dict(list(sessions.items())[-20:]))
    if not suite.startswith('/') or suite.startswith('//') or '\\' in suite or any(ord(c) < 32 for c in suite):
        suite = '/'
    # Le frontal approuvé doit normaliser wsgi.url_scheme ; ne pas faire confiance
    # ici à un X-Forwarded-Proto fourni directement par un client.
    secure = env.get('wsgi.url_scheme') == 'https'
    return {'status': '303 See Other', 'kind': 'text/plain; charset=utf-8', 'body': '',
            'headers': [('Location', (prefix or '') + suite),
                        ('Set-Cookie', '%s=%s; Path=%s; HttpOnly; SameSite=Strict%s' % (COOKIE, sid, prefix or '/', '; Secure' if secure else ''))]}


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
    cle, now = _sha(value), time.time()
    expires, limite = _sessions(auth_path).get(cle, (0, 0))
    if expires <= now:
        return False
    if expires - now < IDLE_SECONDS - RENEW_SECONDS and expires < limite:   # prolongée par l'usage, sans écrire à chaque requête
        try:
            with _VERROU:
                sessions = {k: list(v) for k, v in _sessions(auth_path).items() if v[0] > now}
                if cle in sessions:
                    sessions[cle][0] = min(now + IDLE_SECONDS, sessions[cle][1])
                    _write_private(Path(auth_path).parent / SESSIONS, sessions)
        except OSError:
            pass                       # prolongation non écrite : la session reste valable jusqu'à sa date actuelle
    return True


def cookie_presente(env):
    """Vrai si la requête porte un cookie de session du poste, valable ou non (session expirée : pas de demande d'identifiants)."""
    raw = env.get('HTTP_COOKIE', '')
    return len(raw) <= 8192 and any(part.strip().startswith(COOKIE + '=') for part in raw.split(';'))


def page_expiree():
    """Page rendue à la fenêtre du poste quand sa session a expiré (aucune donnée, aucun lien de connexion)."""
    return ('<!doctype html><html lang="fr"><meta charset="utf-8"><title>Session expirée · AxiorHub Pilote</title>'
            '<body style="font-family:system-ui,sans-serif;max-width:36rem;margin:15vh auto;padding:0 1rem;line-height:1.5">'
            '<h1 style="font-size:1.4rem">Votre session sur ce poste a expiré</h1>'
            '<p>Fermez cette fenêtre, puis rouvrez AxiorHub Pilote depuis le menu Démarrer (Windows) ou le menu des applications '
            '(Ubuntu). Aucun mot de passe n’est à saisir, et rien n’est perdu : vos dossiers et vos réglages sont intacts.</p></body></html>')
