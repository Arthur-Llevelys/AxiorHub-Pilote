"""5.6.42 : mode ExApp (application externe Nextcloud gérée par AppAPI) — moteur livré dans un conteneur, installé depuis la boutique.

Contrat AppAPI (Nextcloud 32 à 35, démon HaRP) :
  - ``GET /heartbeat`` sans authentification ; ``POST /init`` (progression 0 à 100 envoyée à Nextcloud) ; ``PUT /enabled?enabled=1|0``
    (entrée du menu supérieur et script de la page enregistrés ou retirés) ;
  - chaque autre requête porte ``EX-APP-ID``, ``EX-APP-VERSION`` et ``AUTHORIZATION-APP-API`` = base64(utilisateur:secret) ; le secret est
    comparé à temps constant ; l'utilisateur est l'utilisateur Nextcloud connecté ;
  - les appels vers Nextcloud (OCS, WebDAV, CalDAV) portent les mêmes en-têtes, au nom d'un utilisateur choisi ;
  - sous HaRP, l'application écoute la socket ``/tmp/exapp.sock`` (tunnel FRP ouvert par ``start.sh``) ; sinon ``APP_HOST:APP_PORT``.

La passerelle reprend la règle de la distribution autonome (``standalone_auth``) : en-têtes d'identité du navigateur supprimés, rôle établi
par les groupes Nextcloud (``admin`` → administrateur, ``axiorhub-avocats`` et ``axiorhub-collaborateurs`` → avocat, ``axiorhub-assistants``
→ assistant ; sans groupe : aucun accès), mêmes droits par rôle (``standalone_auth.allowed``), application interne authentifiée par son
jeton privé. Les pages sont affichées dans la page Nextcloud du menu supérieur (même origine) : ``frame-ancestors 'self'``.

Données dans le volume persistant de l'ExApp (``APP_PERSISTENT_STORAGE``) ; configuration créée sans secret par ``docker/bootstrap.py`` ;
fichiers et agendas Nextcloud lus au nom d'un utilisateur de service (``AXIORHUB_SERVICE_USER``), sans mot de passe d'application.
"""
import base64
import hmac
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
from urllib.parse import parse_qs, urlsplit
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
TOP_MENU = 'axiorhub'
SCRIPT = 'js/axiorhub-exapp'
ROLE_GROUPS = (('administrateur', ('AXIORHUB_GROUP_ADMIN', 'admin')),
               ('avocat', ('AXIORHUB_GROUP_AVOCATS', 'axiorhub-avocats')),
               ('avocat', ('AXIORHUB_GROUP_COLLABORATEURS', 'axiorhub-collaborateurs')),
               ('assistant', ('AXIORHUB_GROUP_ASSISTANTS', 'axiorhub-assistants')))
ROLE_TTL = 300
JS = '''/* AxiorHub Pilote ExApp : page du menu supérieur de Nextcloud — interface AxiorHub dans un cadre de même origine. */
(function () {
  var src = (document.currentScript && document.currentScript.src) || '';
  var base = src ? src.replace(/\\/js\\/axiorhub-exapp\\.js(\\?.*)?$/, '') : '/exapps/axiorhub';
  function mount() {
    var host = document.getElementById('app-content') || document.getElementById('content') || document.body;
    if (!host || host.querySelector('iframe.axiorhub-exapp')) { return; }
    var frame = document.createElement('iframe');
    frame.className = 'axiorhub-exapp';
    frame.title = 'AxiorHub Pilote';
    frame.src = base + '/aujourdhui';
    frame.setAttribute('allow', 'microphone');
    frame.style.border = '0';
    frame.style.width = '100%';
    frame.style.height = 'calc(100vh - 50px)';
    frame.style.display = 'block';
    host.appendChild(frame);
  }
  if (document.readyState === 'loading') { document.addEventListener('DOMContentLoaded', mount); } else { mount(); }
})();
'''


# ------------------------------------------------------------------------------------------------ réglages et en-têtes AppAPI
def settings(env=None):
    env = os.environ if env is None else env
    app_id = str(env.get('APP_ID') or 'axiorhub')
    return {'app_id': app_id, 'version': str(env.get('APP_VERSION') or ''), 'secret': str(env.get('APP_SECRET') or ''),
            'host': str(env.get('APP_HOST') or '127.0.0.1'), 'port': int(env.get('APP_PORT') or 23000),
            'nextcloud_url': str(env.get('NEXTCLOUD_URL') or '').rstrip('/'), 'storage': str(env.get('APP_PERSISTENT_STORAGE') or '/data'),
            'harp': bool(env.get('HP_SHARED_KEY')), 'prefix': str(env.get('AXIORHUB_EXAPP_PREFIX') or '/exapps/' + app_id).rstrip('/'),
            'service_user': str(env.get('AXIORHUB_SERVICE_USER') or 'axiorhub'),
            'groups': [(role, str(env.get(name) or default)) for role, (name, default) in ROLE_GROUPS]}


def app_api_headers(s, user=''):
    """En-têtes d'un appel vers Nextcloud au nom de ``user`` (vide : contexte système)."""
    token = base64.b64encode(('%s:%s' % (user or '', s['secret'])).encode('utf-8')).decode('ascii')
    return {'EX-APP-ID': s['app_id'], 'EX-APP-VERSION': s['version'] or '0', 'AUTHORIZATION-APP-API': token, 'OCS-APIRequest': 'true'}


def check(s, env):
    """(requête AppAPI valide, utilisateur Nextcloud). Identifiant de l'application et secret exigés ; jamais de comparaison stricte
    d'``AA-VERSION`` (réécrit par HaRP)."""
    if not s['secret'] or not hmac.compare_digest(str(env.get('HTTP_EX_APP_ID') or ''), s['app_id']):
        return False, ''
    raw = str(env.get('HTTP_AUTHORIZATION_APP_API') or '')
    try:
        user, sep, secret = base64.b64decode(raw, validate=True).decode('utf-8').rpartition(':')
    except (ValueError, UnicodeError):
        return False, ''
    if not sep or not hmac.compare_digest(secret, s['secret']):
        return False, ''
    return True, user


def nextcloud_http(url, cfg, timeout=45):
    """5.6.42 : client HTTP d'un service Nextcloud — en mode ExApp (``auth: app_api``), en-têtes AppAPI au nom de l'utilisateur de
    service, sans mot de passe d'application ; sinon identifiant et mot de passe d'application comme avant."""
    from .common import HTTP, read_secret
    if (cfg or {}).get('auth') == 'app_api':
        http = HTTP(url, timeout=timeout)
        http.headers.update(app_api_headers(settings(), str(cfg.get('username') or '')))
        return http
    return HTTP(url, cfg['username'], read_secret(cfg['password_file']), timeout=timeout)


# ------------------------------------------------------------------------------------------------ OCS (Nextcloud)
class OCS:
    """Appels OCS vers Nextcloud avec les en-têtes AppAPI (format JSON)."""

    def __init__(self, s, timeout=20):
        self.s, self.timeout = s, timeout

    def call(self, method, path, user='', data=None):
        url = self.s['nextcloud_url'] + path + ('&' if '?' in path else '?') + 'format=json'
        body = json.dumps(data).encode('utf-8') if data is not None else None
        headers = {**app_api_headers(self.s, user), 'Accept': 'application/json'}
        if body is not None:
            headers['Content-Type'] = 'application/json'
        req = urllib.request.Request(url, data=body, method=method, headers=headers)
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(req, timeout=self.timeout) as r:
            raw = r.read(2_000_000)
        try:
            out = json.loads(raw or b'{}')
        except ValueError:
            return {}
        return (out.get('ocs') or {}).get('data', out) if isinstance(out, dict) else {}

    def groups(self, user):
        data = self.call('GET', '/ocs/v1.php/cloud/user', user)
        return [str(g) for g in (data.get('groups') or [])] if isinstance(data, dict) else []

    def status(self, progress, error=''):
        return self.call('PUT', '/ocs/v1.php/apps/app_api/ex-app/status', '', {'progress': int(progress), 'error': error})

    def ui(self, enabled):
        if enabled:
            self.call('POST', '/ocs/v1.php/apps/app_api/api/v1/ui/top-menu', '',
                      {'name': TOP_MENU, 'displayName': 'AxiorHub Pilote', 'icon': 'img/app.svg', 'adminRequired': '0'})
            self.call('POST', '/ocs/v1.php/apps/app_api/api/v1/ui/script', '', {'type': 'top_menu', 'name': TOP_MENU, 'path': SCRIPT})
        else:
            self.call('DELETE', '/ocs/v1.php/apps/app_api/api/v1/ui/script', '', {'type': 'top_menu', 'name': TOP_MENU, 'path': SCRIPT})
            self.call('DELETE', '/ocs/v1.php/apps/app_api/api/v1/ui/top-menu', '', {'name': TOP_MENU})


def role_for(groups, s):
    """Rôle AxiorHub d'un utilisateur Nextcloud : le plus élevé de ses groupes ; aucun groupe prévu : aucun accès."""
    have = set(groups or [])
    for role, group in s['groups']:
        if group in have:
            return role
    return ''


# ------------------------------------------------------------------------------------------------ passerelle WSGI
def _json(start, status, value, extra=()):
    raw = json.dumps(value, ensure_ascii=False).encode('utf-8')
    start(status, [('Content-Type', 'application/json; charset=utf-8'), ('Content-Length', str(len(raw))), ('Cache-Control', 'no-store'),
                   *extra])
    return [raw]


def _page(start, status, title, text):
    from html import escape as e
    raw = ('<!doctype html><html lang="fr"><head><meta charset="utf-8"><title>%s</title></head><body><main><h1>%s</h1><p>%s</p></main>'
           '</body></html>' % (e(title), e(title), e(text))).encode('utf-8')
    start(status, [('Content-Type', 'text/html; charset=utf-8'), ('Content-Length', str(len(raw))), ('Cache-Control', 'no-store'),
                   ('Content-Security-Policy', "default-src 'none'; frame-ancestors 'self'")])
    return [raw]


def _same_origin_framing(headers):
    """Pages AxiorHub affichées dans la page Nextcloud du menu supérieur (même origine) : cadre limité à 'self'."""
    out = []
    for name, value in headers:
        low = name.lower()
        if low == 'x-frame-options':
            value = 'SAMEORIGIN'
        elif low == 'content-security-policy' and "frame-ancestors 'none'" in value:
            value = value.replace("frame-ancestors 'none'", "frame-ancestors 'self'")
        out.append((name, value))
    return out


class Gateway:
    """Passerelle AppAPI devant l'application web d'AxiorHub (``agent.web.App``)."""

    def __init__(self, app, s, token, ocs=None, ready=None):
        self.app, self.s, self.token = app, s, token
        self.ocs = ocs or OCS(s)
        self.ready = ready
        self.roles, self.lock = {}, threading.Lock()

    # -------------------------------------------------------------- rôles
    def role(self, user):
        now = time.time()
        with self.lock:
            hit = self.roles.get(user)
            if hit and hit[1] > now:
                return hit[0]
        try:
            role = role_for(self.ocs.groups(user), self.s)
        except Exception:   # noqa: BLE001 — groupes illisibles : aucun accès plutôt qu'un rôle supposé
            role = ''
        with self.lock:
            if len(self.roles) > 500:
                self.roles.clear()
            self.roles[user] = (role, now + (ROLE_TTL if role else 30))
        return role

    # -------------------------------------------------------------- cycle de vie
    def init(self):
        try:
            if self.ready is not None:
                self.ready()
            self.ocs.status(100)
        except Exception as exc:   # noqa: BLE001
            try:
                self.ocs.status(0, 'initialisation_impossible: %s' % type(exc).__name__)
            except Exception:   # noqa: BLE001
                pass

    def enable(self, enabled):
        try:
            self.ocs.ui(enabled)
            return ''
        except Exception as exc:   # noqa: BLE001
            return 'menu_nextcloud_non_enregistre: %s' % type(exc).__name__

    # -------------------------------------------------------------- requêtes
    def __call__(self, env, start):
        path = env.get('PATH_INFO') or '/'
        method = env.get('REQUEST_METHOD', 'GET')
        if path == '/heartbeat':
            return _json(start, '200 OK', {'status': 'ok'})
        if path == '/js/axiorhub-exapp.js' and method in ('GET', 'HEAD'):
            raw = JS.encode('utf-8')
            start('200 OK', [('Content-Type', 'text/javascript; charset=utf-8'), ('Content-Length', str(len(raw))), ('Cache-Control', 'no-cache')])
            return [raw]
        if path == '/img/app.svg' and method in ('GET', 'HEAD'):
            icon = ROOT / 'nextcloud-app' / 'axiorhub_pilote' / 'img' / 'app.svg'
            raw = icon.read_bytes() if icon.is_file() else b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32"/>'
            start('200 OK', [('Content-Type', 'image/svg+xml'), ('Content-Length', str(len(raw))), ('Cache-Control', 'max-age=86400')])
            return [raw]
        ok, user = check(self.s, env)
        if not ok:
            return _json(start, '403 Forbidden', {'error': 'appapi_refuse'})
        if path == '/init' and method == 'POST':
            threading.Thread(target=self.init, name='axiorhub-exapp-init', daemon=True).start()
            return _json(start, '200 OK', {})
        if path == '/enabled' and method == 'PUT':
            enabled = (parse_qs(env.get('QUERY_STRING', '')).get('enabled') or ['0'])[0] in ('1', 'true')
            return _json(start, '200 OK', {'error': self.enable(enabled)})
        if not user:
            return _json(start, '401 Unauthorized', {'error': 'utilisateur_nextcloud_requis'})
        role = self.role(user)
        if not role:
            return _page(start, '403 Forbidden', 'Accès à AxiorHub Pilote non autorisé',
                         'Votre compte Nextcloud n’appartient à aucun groupe AxiorHub. Demandez à l’administrateur de Nextcloud de vous '
                         'ajouter au groupe « axiorhub-avocats », « axiorhub-collaborateurs » ou « axiorhub-assistants ».')
        from .standalone_auth import allowed, normalize
        for key in [k for k in env if k.startswith('HTTP_X_AXIORHUB_')]:   # identité : jamais acceptée du navigateur
            del env[key]
        env.pop('HTTP_COOKIE', None)   # les cookies de session Nextcloud ne sont pas transmis à l'application
        inner = path[len(self.s['prefix']):] if path.startswith(self.s['prefix'] + '/') else path
        inner = normalize(inner)
        if inner is None:
            return _json(start, '400 Bad Request', {'error': 'adresse_invalide'})
        action = ''
        if method == 'POST' and inner == '/action':
            from io import BytesIO
            length = int(env.get('CONTENT_LENGTH', '0') or 0)
            raw = env['wsgi.input'].read(length) if 0 < length <= 30_000_000 else b''
            env['wsgi.input'] = BytesIO(raw)
            try:
                action = parse_qs(raw.decode('utf-8', 'replace'), max_num_fields=400).get('action', [''])[0]
            except ValueError:
                action = ''
        from .membres5650 import allowed as allowed5650   # 5.6.50 : droits accordés ou retirés à ce compte
        state5650 = self.app._state_dir5642() if hasattr(self.app, '_state_dir5642') else ''
        if not allowed5650(state5650, user, role, method, inner, action):
            if inner.startswith(('/api', '/live/')):
                return _json(start, '403 Forbidden', {'error': 'role_insuffisant'})
            return _page(start, '403 Forbidden', 'Accès refusé', 'Votre rôle dans AxiorHub ne permet pas cette action.')
        public = urlsplit(self.s['nextcloud_url'])
        env['PATH_INFO'] = self.s['prefix'] + inner
        env['HTTP_AUTHORIZATION'] = 'Bearer ' + self.token
        env['HTTP_X_AXIORHUB_USER'] = user
        env['HTTP_X_AXIORHUB_ROLE'] = role
        env['HTTP_HOST'] = public.netloc
        env['HTTP_X_FORWARDED_PROTO'] = public.scheme or 'https'
        env.pop('HTTP_AUTHORIZATION_APP_API', None)

        def framed(status, headers, exc_info=None):
            return start(status, _same_origin_framing(headers), exc_info) if exc_info else start(status, _same_origin_framing(headers))
        return self.app(env, framed)


# ------------------------------------------------------------------------------------------------ installation dans le volume
def bootstrap(s, python=None):
    """Configuration sans secret, jeton interne et compte de l'interface dans le volume persistant ; idempotent (une mise à jour de
    l'ExApp conserve tout). Renvoie les chemins et le jeton interne."""
    from .common import private_json
    data = Path(s['storage']) / 'axiorhub'
    data.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, 'AXIORHUB_DATA_DIR': str(data), 'AXIORHUB_PUBLIC_URL': s['nextcloud_url'] or 'https://cloud.example.com',
           'AXIORHUB_NEXTCLOUD_URL': s['nextcloud_url'] or 'https://cloud.example.com', 'AXIORHUB_NEXTCLOUD_USERNAME': s['service_user'],
           'AXIORHUB_NEXTCLOUD_ROOT': os.environ.get('AXIORHUB_NEXTCLOUD_ROOT', '/Dossiers')}
    out = subprocess.run([python or sys.executable, str(ROOT / 'docker' / 'bootstrap.py')], env=env, cwd=str(ROOT), check=True,
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    token = out.stdout.decode('utf-8').strip().splitlines()[-1].strip()
    auth_path = data / 'internal-auth.json'
    auth = json.loads(auth_path.read_text(encoding='utf-8'))
    auth.update(origin=s['nextcloud_url'] or auth.get('origin'), prefix=s['prefix'], exapp=s['app_id'])
    private_json(auth_path, auth)
    config_path = data / 'config.json'
    config = json.loads(config_path.read_text(encoding='utf-8'))
    nc = config.setdefault('nextcloud', {})
    nc.update(url=s['nextcloud_url'] or nc.get('url', ''), username=s['service_user'], auth='app_api')
    config['exapp'] = {'app_id': s['app_id'], 'service_user': s['service_user'], 'prefix': s['prefix']}
    private_json(config_path, config)
    (data / 'logs').mkdir(exist_ok=True)
    return {'data': data, 'config': config_path, 'auth': auth_path, 'token': token}


def supervisor(paths, python=None):
    """Travaux, veille IMAP et passage périodique de l'agent, surveillés et relancés comme sur un poste ; l'interface est servie par la
    passerelle dans le processus principal."""
    sys.path.insert(0, str(ROOT))
    import poste

    class ExAppSupervisor(poste.Supervisor):
        SERVICES = ('worker', 'veille')

        def start(self):
            cfg = ['--config', str(self.p['config'])]
            self._spawn('worker', [str(self.root / 'web.py'), 'worker', *cfg])
            self._spawn('veille', [str(self.root / 'web.py'), 'watch', *cfg])
            self.started = time.strftime('%Y-%m-%dT%H:%M:%S')
            self.timer = threading.Thread(target=self._periodic, name='axiorhub-exapp-periodique', daemon=True)
            self.timer.start()
            self.monitor = threading.Thread(target=self._watch, name='axiorhub-exapp-surveillance', daemon=True)
            self.monitor.start()
            self._write_runtime()
            return self

    p = {'logs': paths['data'] / 'logs', 'config': paths['config'], 'runtime': paths['data'] / 'logs' / 'runtime.json'}
    return ExAppSupervisor(p, python=python or sys.executable, root=ROOT)


def main():
    """Point d'entrée du conteneur : installation dans le volume, services de fond, passerelle sur la socket HaRP (ou le port)."""
    os.umask(0o077)
    s = settings()
    paths = bootstrap(s)
    from .web import App
    gateway = Gateway(App(str(paths['config']), str(paths['auth'])), s, paths['token'])
    sup = supervisor(paths).start()
    from waitress import serve
    options = {'threads': 24, 'send_bytes': 1, 'max_request_body_size': 25_000_000}
    try:
        if s['harp']:
            sock = '/tmp/exapp.sock'
            if os.path.exists(sock):
                os.unlink(sock)
            serve(gateway, unix_socket=sock, unix_socket_perms='666', **options)
        else:
            serve(gateway, host=s['host'], port=s['port'], **options)
    finally:
        sup.stop()


__all__ = ['Gateway', 'OCS', 'app_api_headers', 'bootstrap', 'check', 'main', 'nextcloud_http', 'role_for', 'settings', 'supervisor']
