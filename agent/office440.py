"""Edit Nextcloud documents inside AxiorHub with an ONLYOFFICE-compatible Document Server.

Works with ONLYOFFICE Docs and forks that keep its public API (Euro-Office).

Design notes
  * The browser loads ``api.js`` from the Document Server and opens the editor
    in an iframe on the AxiorHub page.
  * The Document Server downloads the file from AxiorHub (``/office/file/<token>``)
    and posts its changes back to AxiorHub (``/office/callback/<token>``). These two
    routes cannot use the lawyer's browser password; they are protected by
    short-lived HMAC tokens and, when configured, by the shared JWT secret.
  * Saving never blindly overwrites: the file is replaced only if its ETag is
    still the one seen when the editor was opened. Otherwise a separate copy is
    created next to it. Nextcloud keeps previous versions of replaced files.
"""
import base64
import hashlib
import hmac
import json
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import ssl
import threading
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit
from xml.etree import ElementTree as ET

from .common import HTTP, Stop, clean_path, load_matters, under

EDITABLE = {
    'docx': 'word', 'odt': 'word', 'rtf': 'word', 'txt': 'word', 'doc': 'word', 'dotx': 'word',
    'xlsx': 'cell', 'ods': 'cell', 'csv': 'cell', 'xls': 'cell',
    'pptx': 'slide', 'odp': 'slide', 'ppt': 'slide',
}
VIEW_ONLY = {'pdf': 'pdf'}
ZIP_FORMATS = {'docx', 'odt', 'dotx', 'xlsx', 'ods', 'pptx', 'odp'}
MAX_EDITED_BYTES = 40_000_000
TOKEN_TTL = 24 * 3600
_LOCKS = {}
_LOCKS_GUARD = threading.Lock()


# ---------------------------------------------------------------- settings
def _state_dir(desk):
    return Path(desk.c['state_dir'])


def _secret_path(desk):
    return _state_dir(desk) / 'office440.jwt'


def _key_path(desk):
    return _state_dir(desk) / 'office440.key'


def valid_server_url(value):
    value = str(value or '').strip()
    if not value:
        return ''
    p = urlsplit(value)
    if p.scheme != 'https' or not p.netloc or p.username or p.password or p.query or p.fragment:
        raise Stop('url_editeur_invalide')
    return p.scheme + '://' + p.netloc + p.path.rstrip('/').removesuffix('/welcome')


def settings(desk):
    stored = desk.settings('office440:config', {}) or {}
    fallback = desk.c.get('office', {}) if isinstance(desk.c.get('office', {}), dict) else {}
    server = stored.get('server_url') or fallback.get('server_url') or fallback.get('url') or ''
    try:
        server = valid_server_url(server)
    except Stop:
        server = ''
    secret_file = fallback.get('jwt_secret_file', '')
    has_secret = _secret_path(desk).is_file() or bool(secret_file and Path(secret_file).is_file())
    return {'server_url': server, 'callback_base': stored.get('callback_base', ''),
            'jwt': has_secret, 'enabled': bool(server), 'engine': stored.get('engine', 'onlyoffice'),
            'default_edit': stored.get('default_edit', True)}


def jwt_secret(desk):
    path = _secret_path(desk)
    if path.is_file():
        return path.read_text(encoding='utf-8').strip()
    fallback = desk.c.get('office', {}).get('jwt_secret_file', '') if isinstance(desk.c.get('office'), dict) else ''
    if fallback and Path(fallback).is_file():
        return Path(fallback).read_text(encoding='utf-8').strip()
    return ''


def save_settings(desk, server_url, secret='', callback_base='', engine='onlyoffice'):
    server = valid_server_url(server_url)
    callback = str(callback_base or '').strip().rstrip('/')
    if callback:
        p = urlsplit(callback)
        if p.scheme not in ('https', 'http') or not p.netloc or p.username or p.password or p.query or p.fragment:
            raise Stop('url_retour_invalide')
    if engine not in ('onlyoffice', 'euro-office'):
        raise Stop('moteur_editeur_inconnu')
    secret = str(secret or '').strip()
    if secret:
        if len(secret) < 12 or len(secret) > 512 or any(ord(c) < 33 for c in secret):
            raise Stop('secret_jwt_invalide')
        path = _secret_path(desk)
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(path) + '.tmp', os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            f.write(secret + '\n')
        os.replace(str(path) + '.tmp', path)
    old = desk.settings('office440:config', {}) or {}
    desk.setting('office440:config', {**old, 'server_url': server, 'callback_base': callback, 'engine': engine})
    desk.audit('editeur_documents_440_configure', {'server_configured': bool(server), 'jwt_updated': bool(secret),
                                                   'engine': engine})
    return {'saved': True, 'server_url': server}


def clear_secret(desk):
    path = _secret_path(desk)
    if path.exists():
        path.unlink()
    desk.audit('editeur_documents_440_secret_retire', {})
    return {'cleared': True}


# --------------------------------------------------------------------- tokens
def _b64(data):
    return base64.urlsafe_b64encode(data).rstrip(b'=').decode()


def _unb64(text):
    return base64.urlsafe_b64decode(text + '=' * (-len(text) % 4))


def _install_key(desk):
    path = _key_path(desk)
    if not path.is_file():
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(path) + '.tmp', os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, 'wb') as f:
            f.write(secrets.token_bytes(48))
        os.replace(str(path) + '.tmp', path)
    return path.read_bytes()


def sign(desk, payload, ttl=TOKEN_TTL):
    body = dict(payload, exp=int(time.time()) + int(ttl))
    raw = _b64(json.dumps(body, separators=(',', ':'), sort_keys=True).encode())
    mac = _b64(hmac.new(_install_key(desk), raw.encode(), hashlib.sha256).digest())
    return raw + '.' + mac


def verify(desk, token, kind):
    try:
        raw, mac = str(token).split('.', 1)
        good = _b64(hmac.new(_install_key(desk), raw.encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(mac, good):
            raise Stop('jeton_invalide')
        body = json.loads(_unb64(raw))
    except (ValueError, UnicodeError, TypeError):
        raise Stop('jeton_invalide') from None
    if body.get('t') != kind or int(body.get('exp', 0)) < time.time():
        raise Stop('jeton_expire_ou_incorrect')
    return body


def jwt_encode(payload, secret):
    head = _b64(json.dumps({'alg': 'HS256', 'typ': 'JWT'}, separators=(',', ':')).encode())
    body = _b64(json.dumps(payload, separators=(',', ':'), ensure_ascii=False).encode())
    sig = _b64(hmac.new(secret.encode(), (head + '.' + body).encode(), hashlib.sha256).digest())
    return head + '.' + body + '.' + sig


def jwt_decode(token, secret):
    try:
        head_s, body_s, sig = str(token).split('.')
        head = json.loads(_unb64(head_s))
        if head.get('alg') != 'HS256':
            raise Stop('jwt_algorithme_refuse')
        good = _b64(hmac.new(secret.encode(), (head_s + '.' + body_s).encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(sig, good):
            raise Stop('jwt_signature_invalide')
        body = json.loads(_unb64(body_s))
    except (ValueError, UnicodeError, TypeError):
        raise Stop('jwt_invalide') from None
    if not isinstance(body, dict):
        raise Stop('jwt_invalide')
    if 'exp' in body and int(body['exp']) < time.time():
        raise Stop('jwt_expire')
    return body


# ------------------------------------------------------------------ sessions
def ensure_schema(desk):
    desk.db.executescript('''
    CREATE TABLE IF NOT EXISTS office_sessions440(
      doc_key TEXT PRIMARY KEY, path TEXT NOT NULL, base_etag TEXT NOT NULL, last_sha TEXT NOT NULL DEFAULT '',
      matter TEXT NOT NULL DEFAULT '', opened TEXT NOT NULL, updated TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS office_saves440(
      id INTEGER PRIMARY KEY AUTOINCREMENT, doc_key TEXT NOT NULL, path TEXT NOT NULL, outcome TEXT NOT NULL,
      saved_path TEXT NOT NULL, sha256 TEXT NOT NULL, bytes INTEGER NOT NULL, status INTEGER NOT NULL, at TEXT NOT NULL);
    ''')
    desk.db.commit()


def dav_client(desk):
    from .dav import DAV
    from .connector_readiness5689 import brider_ecriture   # 5.6.89 : écritures refusées si la capacité est révoquée ; les lectures continuent
    return brider_ecriture(desk, DAV(desk.c.get('nextcloud_documents') or desk.c['nextcloud']))


def matter_of(desk, path):
    """5.6.84 : le dossier d'un document se déduit de son emplacement, côté serveur ; une valeur envoyée par le navigateur n'est pas une preuve de droit."""
    rows = [m for m in load_matters(desk.c) if m.get('path') and under(path, m['path'])]
    return max(rows, key=lambda m: len(m['path']))['id'] if rows else ''


def extension(path):
    return PurePosixPath(path).suffix.lower().lstrip('.')


def can_open(path):
    return extension(path) in EDITABLE or extension(path) in VIEW_ONLY


def public_base(desk, auth):
    cfg = settings(desk)
    if cfg['callback_base']:
        return cfg['callback_base']
    return auth['origin'].rstrip('/') + auth.get('prefix', '/agent-courriel')


def open_document(desk, auth, path, user_name='Cabinet', mode='edit', client=None, user_id='cabinet', matter='', role='avocat'):
    """Build the editor configuration for one Nextcloud file."""
    ensure_schema(desk)
    cfg = settings(desk)
    if not cfg['enabled']:
        raise Stop('editeur_documents_non_configure')
    path = clean_path(path)
    ext = extension(path)
    if ext not in EDITABLE and ext not in VIEW_ONLY:
        raise Stop('format_non_modifiable')
    client = client or dav_client(desk)
    info = client.stat(path)
    if info['size'] > desk.c.get('nextcloud', {}).get('max_file_bytes', 40_000_000) * 3:
        raise Stop('piece_trop_volumineuse')
    from . import office_gen5684     # 5.6.84 : une seule clé pour tous les participants pendant toute la génération
    generation = office_gen5684.open_generation(desk, path, info, matter_of(desk, path) or matter)
    key = generation['doc_key']
    now = desk.now()
    desk.db.execute('''INSERT INTO office_sessions440(doc_key,path,base_etag,matter,opened,updated)
      VALUES(?,?,?,?,?,?) ON CONFLICT(doc_key) DO UPDATE SET updated=excluded.updated''',
                    (key, path, generation['current_etag'] or info['etag'], '', now, now))
    desk.db.commit()
    base = public_base(desk, auth)
    file_token = sign(desk, {'t': 'file', 'k': key, 'p': path})
    callback_token = sign(desk, {'t': 'callback', 'k': key, 'p': path})
    editable = ext in EDITABLE and mode == 'edit'
    doc_type = EDITABLE.get(ext) or 'pdf'
    config = {
        'documentType': doc_type,
        'document': {'fileType': ext, 'key': key, 'title': PurePosixPath(path).name,
                     'url': base + '/office/file/' + file_token,
                     'permissions': {'edit': editable, 'download': True, 'print': True, 'comment': True,
                                     'review': True, 'copy': True}},
        'editorConfig': {'mode': 'edit' if editable else 'view', 'lang': 'fr',
                         'callbackUrl': base + '/office/callback/' + callback_token,
                         'user': {'id': str(user_id)[:128], 'name': str(user_name)[:128]},
                         'customization': {'autosave': True, 'forcesave': True, 'compactHeader': True,
                                           'feedback': False, 'help': False, 'hideRightMenu': False,
                                           'uiTheme': 'default-light'}},
        'type': 'desktop', 'width': '100%', 'height': '100%',
    }
    plugin = None
    from . import office_bridge5684 as bridge
    if ext == 'docx' and bridge.flags(desk)['observation']:   # 5.6.84 : plugin servi par AxiorHub ; jeton court dans la configuration signée, jamais dans une adresse
        plugin = bridge.open_session(desk, {'generation_id': generation['generation_id'], 'document_id': generation['document_id'], 'editable': editable}, user_id, role)
        browser = auth['origin'].rstrip('/') + auth.get('prefix', '/agent-courriel')
        config['editorConfig']['plugins'] = {'autostart': [bridge.GUID], 'pluginsData': [browser + '/office/plugin/config.json'],
                                             'options': {bridge.GUID: {k: plugin[k] for k in ('token', 'session_id', 'document_id')}}}
    secret = jwt_secret(desk)
    if secret:
        config['token'] = jwt_encode(config, secret)
    desk.audit('document_ouvert_dans_editeur_440', {'path': path, 'mode': config['editorConfig']['mode']})
    return {'config': config, 'server_url': cfg['server_url'], 'path': path, 'etag': info['etag'],
            'name': PurePosixPath(path).name, 'editable': editable, 'document_id': generation['document_id'], 'generation_id': generation['generation_id']}


# ------------------------------------------------------ routes used by the server
def serve_file(desk, token, client=None):
    ensure_schema(desk)
    body = verify(desk, token, 'file')
    row = desk.db.execute('SELECT * FROM office_sessions440 WHERE doc_key=?', (body['k'],)).fetchone()
    if not row or row['path'] != body['p']:
        raise Stop('session_editeur_inconnue')
    client = client or dav_client(desk)
    info = client.stat(row['path'])
    data = client.download(info)
    return data, PurePosixPath(row['path']).name


def _lock(key):
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(key, threading.Lock())


def _fetch_edited(desk, url, client=None):
    cfg = settings(desk)
    server = urlsplit(cfg['server_url'])
    target = urlsplit(url)
    if target.scheme != 'https' or target.netloc != server.netloc:
        raise Stop('url_editeur_hors_serveur')
    http = client or HTTP(server.scheme + '://' + server.netloc)
    return http.request('GET', url, limit=MAX_EDITED_BYTES)


def _is_valid_content(ext, data):
    if not data:
        return False
    if ext in ZIP_FORMATS:
        return data[:2] == b'PK'
    return True


def save_edited(desk, row, data, status, client=None, force_copy=False, copy_path=''):
    """Écriture avec précondition ETag, puis relecture, y compris pour une copie de conflit (5.6.84). Une copie existante est reprise plutôt que multipliée ;
    ``force_copy`` : une génération ancienne ne remplace jamais l'original."""
    key, path = row['doc_key'], row['path']
    ext = extension(path)
    if not _is_valid_content(ext, data):
        raise Stop('contenu_editeur_invalide')
    sha = hashlib.sha256(data).hexdigest()
    with _lock(key):
        row = desk.db.execute('SELECT * FROM office_sessions440 WHERE doc_key=?', (key,)).fetchone()
        if row['last_sha'] == sha:
            return {'outcome': 'unchanged', 'path': path, 'sha256': sha, 'etag': row['base_etag']}
        client = client or dav_client(desk)
        outcome, saved_path = 'copy', path
        try:
            current = client.stat(path)
        except Stop:
            current = None
        if current is not None and current['etag'] == row['base_etag'] and not force_copy and not copy_path:
            try:
                client.replace_file(path, data, current['etag'])
                outcome = 'replaced'
            except Stop as ex:
                if str(ex) != 'version_nextcloud_modifiee':
                    raise
        if outcome == 'copy' and copy_path:
            client.replace_file(copy_path, data, client.stat(copy_path)['etag'])
            saved_path = copy_path
        elif outcome == 'copy':
            stamp = time.strftime('%Y%m%d-%H%M')
            p = PurePosixPath(path)
            for n in range(0, 20):
                suffix = stamp + ('' if n == 0 else '-' + str(n))
                saved_path = str(p.with_name(p.stem + ' (modifié ' + suffix + ')' + p.suffix))
                try:
                    client.put_file(saved_path, data)
                    break
                except Stop as ex:
                    if str(ex) != 'http_412' or n == 19:
                        raise
        fresh = client.stat(saved_path)
        if hashlib.sha256(client.download(fresh)).hexdigest() != sha:
            raise Stop('relecture_nextcloud_non_conforme')
        etag = fresh['etag'] if outcome == 'replaced' else row['base_etag']
        desk.db.execute('UPDATE office_sessions440 SET base_etag=?,last_sha=?,updated=? WHERE doc_key=?',
                        (etag, sha, desk.now(), key))
        desk.db.execute('INSERT INTO office_saves440(doc_key,path,outcome,saved_path,sha256,bytes,status,at) '
                        'VALUES(?,?,?,?,?,?,?,?)', (key, path, outcome, saved_path, sha, len(data), int(status), desk.now()))
        desk.db.commit()
        desk.audit('document_enregistre_depuis_editeur_440', {'path': path, 'saved_path': saved_path,
                                                              'outcome': outcome, 'sha256': sha, 'bytes': len(data)})
        return {'outcome': outcome, 'path': saved_path, 'sha256': sha, 'etag': fresh['etag']}


def maintenance(desk, client=None):
    """5.6.84 : tâche de santé : reprise des sauvegardes interrompues par un redémarrage, puis rétention. Rien d'actif si l'éditeur n'est pas configuré."""
    from . import office_gen5684
    office_gen5684.schema(desk)
    if desk.db.execute("SELECT 1 FROM office_saves5684 WHERE state IN ('pending','fetching','writing') LIMIT 1").fetchone():
        def write(gen, content):
            row = desk.db.execute('SELECT * FROM office_sessions440 WHERE doc_key=?', (gen['doc_key'],)).fetchone()
            if not row:
                raise Stop('session_editeur_inconnue')
            return save_edited(desk, row, content, 6, client, gen['state'] in ('closed', 'expired'), gen['conflict_path'])
        office_gen5684.resume(desk, write, client or dav_client(desk))
    return office_gen5684.purge(desk)


def handle_callback(desk, token, authorization, body_bytes, client=None, fetcher=None):
    """Return the JSON object the Document Server expects. ``error`` 0 means saved/acknowledged."""
    ensure_schema(desk)
    claims = verify(desk, token, 'callback')
    try:
        data = json.loads(body_bytes.decode('utf-8'))
        if not isinstance(data, dict):
            raise ValueError
    except (ValueError, UnicodeError):
        raise Stop('callback_json_invalide') from None
    secret = jwt_secret(desk)
    if secret:
        raw = data.get('token') or (authorization or '').removeprefix('Bearer ').strip()
        if not raw:
            raise Stop('jwt_absent')
        verified = jwt_decode(raw, secret)
        data = verified.get('payload') if isinstance(verified.get('payload'), dict) else verified
    if str(data.get('key', '')) != claims['k']:
        raise Stop('cle_document_incoherente')
    row = desk.db.execute('SELECT * FROM office_sessions440 WHERE doc_key=?', (claims['k'],)).fetchone()
    if not row or row['path'] != claims['p']:
        raise Stop('session_editeur_inconnue')
    status = int(data.get('status', 0))
    from . import office_gen5684
    generation = office_gen5684.by_key(desk, claims['k'])
    if generation is None:      # session ouverte avant la mise à jour : elle finit son cycle sous sa clé
        info = (client or dav_client(desk)).stat(row['path'])
        generation = office_gen5684.open_generation(desk, row['path'], {**info, 'etag': row['base_etag']})
    if status == 1:
        office_gen5684.touch(desk, generation['generation_id'], data.get('users') if isinstance(data.get('users'), list) else None)
        return {'error': 0}
    if status == 4:
        office_gen5684.close(desk, generation['generation_id'])
        return {'error': 0}
    if status in (3, 7):    # acquittement du protocole ; l'échec reste durable et visible (5.6.84), jamais une preuve de sauvegarde
        office_gen5684.record_failure(desk, generation, status)
        desk.audit('editeur_documents_440_erreur_enregistrement', {'path': row['path'], 'status': status})
        return {'error': 0}
    if status in (2, 6):
        url = str(data.get('url') or '')
        if not url:
            raise Stop('url_editeur_absente')
        if status == 2:
            office_gen5684.close(desk, generation['generation_id'], 'closing')
        office_gen5684.save(desk, generation, status, str(data.get('lastsave') or ''), url, lambda: (fetcher or _fetch_edited)(desk, url),
                            lambda content, force, copy: save_edited(desk, row, content, status, client, force, copy))
        if status == 2:
            office_gen5684.close(desk, generation['generation_id'])
        return {'error': 0}
    return {'error': 0}


# --------------------------------------------------------------- diagnostics
def _probe(url, method='GET', body=None, headers=None, timeout=12):
    request = urllib.request.Request(url, data=body, method=method, headers=headers or {})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), urllib.request.HTTPSHandler(
        context=ssl.create_default_context()))
    try:
        with opener.open(request, timeout=timeout) as response:
            return response.status, dict(response.headers), response.read(200_000)
    except urllib.error.HTTPError as error:
        return error.code, dict(error.headers), error.read(20_000)
    except (urllib.error.URLError, OSError) as error:
        return 0, {}, str(getattr(error, 'reason', error)).encode()[:300]


def diagnostic(desk, auth, probe=_probe):
    cfg = settings(desk)
    checks = []

    def add(name, ok, detail, fix=''):
        checks.append({'name': name, 'ok': bool(ok), 'detail': detail, 'fix': fix})

    if not cfg['enabled']:
        add('Serveur de documents', False, 'Aucune adresse enregistrée.',
            'Renseignez l’adresse HTTPS du serveur (par exemple https://office.example.fr).')
        return checks
    server = cfg['server_url']
    status, headers, body = probe(server + '/healthcheck')
    add('Serveur joignable (healthcheck)', status == 200 and body.strip().lower() == b'true',
        'HTTP %s' % status if status else 'Connexion impossible : %s' % body.decode('utf-8', 'replace'),
        'Vérifiez que le service « documentserver » tourne et que le certificat HTTPS est valide.')
    status, headers, body = probe(server + '/web-apps/apps/api/documents/api.js')
    add('Bibliothèque api.js', status == 200 and b'DocsAPI' in body,
        'HTTP %s' % status, 'L’interface ne peut pas charger l’éditeur sans api.js. Contrôlez le proxy inverse.')
    status, headers, body = probe(server + '/hosting/discovery')
    formats = 0
    if status == 200:
        try:
            formats = len(ET.fromstring(body).findall('.//action'))
        except ET.ParseError:
            formats = 0
    add('Découverte des formats (discovery)', status == 200 and formats > 0,
        '%d action(s) déclarée(s)' % formats if status == 200 else 'HTTP %s' % status,
        'Le serveur répond mais ne déclare aucun éditeur : contrôlez son installation.')
    origin = auth['origin']
    frame_headers = {}
    for candidate in ('/web-apps/apps/documenteditor/main/index.html', '/'):
        status, frame_headers, _ = probe(server + candidate)
        if status in (200, 301, 302):
            break
    csp = ' '.join(str(v) for k, v in frame_headers.items() if k.lower() == 'content-security-policy')
    xfo = ' '.join(str(v) for k, v in frame_headers.items() if k.lower() == 'x-frame-options')
    ancestors = re.search(r'frame-ancestors([^;]*)', csp)
    blocked = False
    detail = 'Aucune restriction d’intégration détectée.'
    if xfo and xfo.strip().upper() in ('DENY', 'SAMEORIGIN'):
        blocked, detail = True, 'En-tête X-Frame-Options: ' + xfo
    if ancestors:
        values = ancestors[1].split()
        if origin not in values and '*' not in values:
            blocked, detail = True, 'frame-ancestors' + ancestors[1]
    add('Intégration dans cette page (iframe)', not blocked, detail,
        'Autorisez ' + origin + ' dans frame-ancestors du serveur de documents (en-tête Content-Security-Policy '
        'du proxy ou du fichier nginx de l’éditeur), puis rechargez le service.')
    secret = jwt_secret(desk)
    command = {'c': 'version'}
    headers_out = {'Content-Type': 'application/json'}
    payload = dict(command)
    if secret:
        token = jwt_encode(command, secret)
        payload['token'] = token
        headers_out['Authorization'] = 'Bearer ' + token
    status, _, body = probe(server + '/command', 'POST', json.dumps(payload).encode(), headers_out)
    try:
        answer = json.loads(body.decode()) if status == 200 else {}
    except ValueError:
        answer = {}
    if answer.get('error') == 0:
        add('Secret JWT et API de commande', True, 'Version du serveur : ' + str(answer.get('version', 'inconnue')))
    elif answer.get('error') == 6:
        add('Secret JWT et API de commande', False, 'Le serveur refuse la signature.',
            'Le secret enregistré ici doit être identique à « JWT_SECRET » (ou services.CoAuthoring.token.*.secret) du serveur de documents.')
    else:
        add('Secret JWT et API de commande', status == 200, 'Réponse inattendue (HTTP %s).' % status,
            'Si le serveur n’utilise pas de JWT, retirez le secret ici ; sinon enregistrez le même secret.')
    base = public_base(desk, auth)
    add('Adresse de retour pour le serveur de documents', True,
        'Le serveur doit pouvoir atteindre : ' + base + '/office/file/… et ' + base + '/office/callback/…',
        'Si le serveur est sur le même hôte, testez : curl -I ' + base + '/office/file/test (réponse 403 attendue, pas 000/502).')
    return checks
