"""5.6.88 : Google Drive, avec le contrat de ``DAV``.

Drive ne ressemble pas à WebDAV sur trois points, traités ici et signalés au cabinet (docs/CLOUD-5.6.88.md) :

- **Pas de chemins** : ils sont retrouvés dossier par dossier, avec un cache court des identifiants de dossiers.
- **Deux fichiers peuvent porter le même nom dans un dossier**, et aucune création n'est exclusive côté serveur. L'exclusivité est donc
  obtenue par détection : verrou local, lecture du nom, création, relecture ; en cas de doublon, le plus ancien gagne selon une règle
  déterministe et le perdant retire sa propre copie, puis répond ``http_412``. Un document n'est jamais remplacé. Un doublon venu d'ailleurs
  (site Drive) reste signalé dans ``refused`` et bloque les écritures sur ce nom.
- **Précondition de version** : l'API v3 n'expose pas d'ETag. La version est lue en v3, l'ETag en v2, puis le contenu est écrit par l'API v2
  avec ``If-Match`` ; absence d'ETag : refus. La mise à la corbeille suit la même voie (5.6.89 : elle passait par un PATCH v3 sans condition).
  Ce passage par la v2 n'a pas été éprouvé sur un vrai compte : le contrôle d'aptitude le rejoue, écriture et corbeille, et la garantie n'est annoncée qu'après lui.

**Portée** : ``drive.file`` (non sensible) par défaut. Elle suffit si AxiorHub écrit tout ce qu'il doit lire, mais elle ne montre pas un fichier
déposé à la main dans Drive par un collaborateur. La portée complète ``drive`` (restreinte chez Google) est un choix explicite du cabinet.

Les documents natifs Google (Docs, Sheets, Slides) n'ont pas d'octets : ils apparaissent avec une extension bureautique, exportés à la
lecture et jamais remplacés.
"""
import json
from pathlib import PurePosixPath
import secrets
import time
from urllib.parse import quote, urlencode

from .adaptateurs5652 import GoogleWorkspace
from .common import Stop
from . import cloud5688 as core

FOLDER = 'application/vnd.google-apps.folder'
SHORTCUT = 'application/vnd.google-apps.shortcut'
NATIVE = {'application/vnd.google-apps.document': ('.docx', 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'),
          'application/vnd.google-apps.spreadsheet': ('.xlsx', 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'),
          'application/vnd.google-apps.presentation': ('.pptx', 'application/vnd.openxmlformats-officedocument.presentationml.presentation')}
FIELDS = 'id,name,mimeType,version,size,createdTime,modifiedTime,webViewLink,parents'
SIMPLE_MAX = 5 * 1024 * 1024
CHUNK = 8 * 1024 * 1024            # multiple de 256 Kio exigé par l'envoi repris
FOLDER_TTL = 120
RATE_REASONS = ('rateLimitExceeded', 'userRateLimitExceeded', 'dailyLimitExceeded')


def _q(value):
    return str(value).replace('\\', '\\\\').replace("'", "\\'")


class GoogleDriveAdapter(GoogleWorkspace):
    provider = 'cloud_google_drive'

    @property
    def authorize_extra(self):
        return {'access_type': 'offline', 'prompt': 'consent'}

    def scopes(self, capabilities):
        wide = str(self.ctx.params.get('acces') or '') == 'complet'
        return 'https://www.googleapis.com/auth/drive' if wide else 'https://www.googleapis.com/auth/drive.file'

    def identity(self):
        about = self.request('GET', '', url=core.ENDPOINTS['google_api'] + '/drive/v3/about?fields=user(emailAddress,displayName)')
        user = about.get('user') or {}
        return {'compte': user.get('emailAddress') or user.get('displayName') or ''}

    def revoke(self):
        token = self.ctx.optional_secret('refresh') or self.bearer()
        url = core.ENDPOINTS['google_oauth']
        self.ctx.http(url).request('POST', url, urlencode({'token': token}).encode(), {'Content-Type': 'application/x-www-form-urlencoded'}, 100_000)
        return 'revoquee'


class GoogleDriveStore(core.CloudStore):
    provider = 'cloud_google_drive'
    label = 'Google Drive'
    adapter = GoogleDriveAdapter

    def __init__(self, cfg):
        super().__init__(cfg)
        self.root_id = str(self.cloud.get('root_id') or '')         # dossier AxiorHub, retenu par ``prepare`` : avec drive.file, il ne se retrouve pas autrement
        self.start = str(self.cloud.get('dossier_id') or 'root')
        self._folders = {}

    # ----- réseau
    def _limited(self, status, body):
        if status != 403:
            return False
        try:
            errors = (json.loads(body).get('error') or {}).get('errors') or []
        except (ValueError, AttributeError, TypeError):
            return False
        return any(e.get('reason') in RATE_REASONS for e in errors if isinstance(e, dict))

    @property
    def api(self):
        return core.ENDPOINTS['google_api']

    def _call(self, method, path, query=None, body=None, headers=None, limit=2_000_000, safe=None, ok=(200, 201, 204)):
        q = {'supportsAllDrives': 'true', **(query or {})}
        url = self.api + path + '?' + urlencode(q)
        hdr = dict(headers or {})
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            hdr['Content-Type'] = 'application/json; charset=UTF-8'
        status, answer, raw = self._send(method, url, data, hdr, limit, safe=safe)
        if status not in ok:
            raise self._fail(status, method, url)
        return answer, raw

    def _files(self, q):
        out, token = [], ''
        while True:
            query = {'q': q, 'fields': 'nextPageToken,files(%s)' % FIELDS, 'pageSize': 1000, 'includeItemsFromAllDrives': 'true', 'corpora': 'allDrives' if self.cloud.get('dossier_id') else 'user'}
            if token:
                query['pageToken'] = token
            answer, raw = self._call('GET', '/drive/v3/files', query)
            page = self._json(raw)
            out += page.get('files') or []
            token = page.get('nextPageToken') or ''
            if not token:
                return out

    def _named(self, parent, name):
        rows = self._files("'%s' in parents and name='%s' and trashed=false" % (_q(parent), _q(name)))
        return sorted((r for r in rows if r.get('name') == name), key=lambda r: (r.get('createdTime') or '', r.get('id') or ''))

    # ----- chemins
    def _base_id(self):
        if self.root_id:
            return self.root_id
        cached = self._folders.get('')
        if cached and cached[1] > time.time():
            return cached[0]
        current = self.start
        for part in [p for p in self.base.split('/') if p]:
            rows = [r for r in self._named(current, part) if r.get('mimeType') == FOLDER]
            if not rows:
                raise Stop('http_404')
            current = rows[0]['id']
        self._folders[''] = (current, time.time() + FOLDER_TTL)
        return current

    def _folder_id(self, path):
        """Identifiant du dossier logique ``path``, en remontant au plus proche dossier connu ; ``http_404`` s'il manque."""
        parts = [p for p in path.split('/') if p]
        current, done = self._base_id(), ''
        for part in parts:
            done += '/' + part
            cached = self._folders.get(done)
            if cached and cached[1] > time.time():
                current = cached[0]
                continue
            rows = [r for r in self._named(current, part) if r.get('mimeType') == FOLDER]
            if not rows:
                raise Stop('http_404')
            current = rows[0]['id']
            self._folders[done] = (current, time.time() + FOLDER_TTL)
        return current

    def _normal(self, row, path=''):
        directory = row.get('mimeType') == FOLDER
        native = NATIVE.get(row.get('mimeType'))
        return {'id': row.get('id'), 'name': row.get('name', '') + (native[0] if native else ''), 'directory': directory, 'native': bool(native),
                'mime': row.get('mimeType', ''), 'etag': '' if directory else '"%s"' % row.get('version', ''), 'modified': core.http_date(row.get('modifiedTime')),
                'created': core.http_date(row.get('createdTime')), 'size': int(row.get('size') or 0), 'web': row.get('webViewLink', ''), 'path': path}

    def _list(self, path):
        rows = self._files("'%s' in parents and trashed=false" % _q(self._folder_id(path)))
        by_name, out = {}, []
        for row in sorted(rows, key=lambda r: (r.get('createdTime') or '', r.get('id') or '')):
            if row.get('mimeType') == SHORTCUT or (str(row.get('mimeType', '')).startswith('application/vnd.google-apps.') and row.get('mimeType') not in NATIVE and row.get('mimeType') != FOLDER):
                self.refused = self.refused + ['format Google non lisible : ' + str(row.get('name', ''))[-100:]]
                continue
            entry = self._normal(row)
            if entry['name'] in by_name:            # Drive accepte deux fois le même nom : le plus ancien est lu, le doublon est signalé, jamais ignoré en silence
                self.refused = self.refused + ['nom en double : ' + entry['name'][-100:]]
                continue
            by_name[entry['name']] = True
            out.append(entry)
        return out

    def _meta(self, path):
        pure = PurePosixPath(path)
        if clean_parent(path) == path:
            raise Stop('http_404')
        parent = self._folder_id(str(pure.parent))
        rows = self._named(parent, pure.name)
        if not rows:
            for mime, (suffix, _) in NATIVE.items():
                if pure.name.endswith(suffix):
                    rows = [r for r in self._named(parent, pure.name[:-len(suffix)]) if r.get('mimeType') == mime]
                    if rows:
                        break
        if not rows:
            raise Stop('http_404')
        meta = self._normal(rows[0], path)
        meta['ambiguous'] = len(rows) > 1
        return meta

    def _read(self, meta, limit):
        target = '/drive/v3/files/%s' % quote(meta['id'], safe='')
        if meta.get('native'):
            mime = NATIVE[meta['mime']][1]
            return self._call('GET', target + '/export', {'mimeType': mime}, limit=limit, headers={'Accept': '*/*'})[1]
        return self._call('GET', target, {'alt': 'media'}, limit=limit, headers={'Accept': '*/*'})[1]

    # ----- écriture
    def _multipart(self, metadata, data, content_type):
        boundary = 'axh' + secrets.token_hex(12)
        head = ('--%s\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n%s\r\n--%s\r\nContent-Type: %s\r\n\r\n' % (
            boundary, json.dumps(metadata), boundary, content_type)).encode()
        return head + data + ('\r\n--%s--\r\n' % boundary).encode(), 'multipart/related; boundary=' + boundary

    def _upload(self, metadata, data, content_type):
        query = {'fields': FIELDS}
        if len(data) <= SIMPLE_MAX:
            body, ctype = self._multipart(metadata, data, content_type)
            url = self.api + '/upload/drive/v3/files?' + urlencode({**query, 'uploadType': 'multipart', 'supportsAllDrives': 'true'})
            status, _, raw = self._send('POST', url, body, {'Content-Type': ctype}, safe=False)
            if status not in (200, 201):
                raise self._fail(status, 'POST', url)
            return self._json(raw)
        return self._resumable(metadata, data, content_type, query)

    def _resumable(self, metadata, data, content_type, query):
        """Envoi par blocs : session, blocs de 8 Mio, reprise à l'octet indiqué par le serveur après une coupure."""
        url = self.api + '/upload/drive/v3/files?' + urlencode({**query, 'uploadType': 'resumable', 'supportsAllDrives': 'true'})
        status, answer, _ = self._send('POST', url, json.dumps(metadata).encode(), {'Content-Type': 'application/json; charset=UTF-8', 'X-Upload-Content-Type': content_type,
                                                                                    'X-Upload-Content-Length': str(len(data))}, safe=False)
        if status not in (200, 201) or not answer.get('location'):
            raise self._fail(status, 'POST', url)
        session = answer['location']
        if not session.startswith(self.api + '/'):
            raise Stop('hote_transfert_refuse')
        total, offset, faults = len(data), 0, 0
        while offset < total:
            end = min(offset + CHUNK, total)
            try:
                status, answer, raw = self._send('PUT', session, data[offset:end], {'Content-Type': 'application/octet-stream', 'Content-Range': 'bytes %d-%d/%d' % (offset, end - 1, total)}, safe=True)
            except Stop as ex:
                if str(ex) not in core.TRANSIENT or faults >= 3:
                    raise
                faults += 1
                status, answer, raw = self._send('PUT', session, b'', {'Content-Type': 'application/octet-stream', 'Content-Range': 'bytes */%d' % total}, safe=True)
            if status in (200, 201):
                return self._json(raw)
            if status != 308:
                raise self._fail(status, 'PUT', session.split('?', 1)[0])
            offset = int(str(answer.get('range') or 'bytes=0--1').rsplit('-', 1)[1]) + 1
        raise Stop('envoi_incomplet')

    def _trash_id(self, ident):
        """Sans condition : réservé à la copie qu'AxiorHub vient de créer et qui a perdu (``_exclusive``). Un document du cabinet passe par ``_trash_tagged``."""
        self._call('PATCH', '/drive/v3/files/%s' % quote(ident, safe=''), body={'trashed': True}, safe=False)

    def _exclusive(self, parent, name, create):
        """Création exclusive par détection (voir l'en-tête du module) : verrou, lecture, création, relecture, retrait de sa propre copie si elle perd."""
        with self._name_lock(parent + '|' + name):
            if self._named(parent, name):
                raise Stop('http_412')
            made = create()
            rows = self._named(parent, name)
            if rows and rows[0].get('id') != made.get('id'):
                self._trash_id(made['id'])
                raise Stop('http_412')
            return made

    def _create(self, path, data, content_type):
        pure = PurePosixPath(path)
        parent = self._folder_id(str(pure.parent))
        self._exclusive(parent, pure.name, lambda: self._upload({'name': pure.name, 'parents': [parent]}, data, content_type))

    def server_tag(self, meta):
        answer, raw = self._call('GET', '/drive/v2/files/%s' % quote(meta['id'], safe=''), {'fields': 'etag,version'})
        info = self._json(raw)
        tag, version = str(info.get('etag') or answer.get('etag') or ''), str(info.get('version') or '')
        if not tag:
            raise Stop('precondition_version_indisponible')      # jamais d'écriture sans condition
        if '"%s"' % version != meta.get('etag'):
            raise Stop('version_nextcloud_modifiee')
        return tag

    def _put_tagged(self, meta, data, tag):
        if meta.get('native'):
            raise Stop('format_google_natif_lecture_seule')
        if meta.get('ambiguous'):
            raise Stop('nom_en_double_chez_le_fournisseur')
        url = '%s/upload/drive/v2/files/%s?%s' % (self.api, quote(meta['id'], safe=''), urlencode({'uploadType': 'media', 'supportsAllDrives': 'true'}))
        status, _, _ = self._send('PUT', url, data, {'Content-Type': 'application/octet-stream', 'If-Match': tag}, safe=False)
        if status == 412:
            raise Stop('version_nextcloud_modifiee')
        if status not in (200, 201):
            raise self._fail(status, 'PUT', url)

    def _trash(self, meta):
        if meta.get('ambiguous'):
            raise Stop('nom_en_double_chez_le_fournisseur')
        self._trash_tagged(meta, self.server_tag(meta))

    def _trash_tagged(self, meta, tag):
        """5.6.89 : corbeille conditionnelle, par la même voie que ``_put_tagged`` : l'API v3 n'accepte pas ``If-Match``, l'API v2 l'annonce. Le succès n'est retenu
        qu'après relecture de l'état du fichier. Cette garantie reste à démontrer sur le compte du cabinet : le contrôle d'aptitude la rejoue (voir ``cloud5688.aptitude``)."""
        url = '%s/drive/v2/files/%s?%s' % (self.api, quote(meta['id'], safe=''), urlencode({'supportsAllDrives': 'true'}))
        status, _, _ = self._send('PATCH', url, json.dumps({'labels': {'trashed': True}}).encode(), {'Content-Type': 'application/json', 'If-Match': tag}, safe=False)
        if status == 412:
            raise Stop('version_nextcloud_modifiee')
        if status not in (200, 201):
            raise self._fail(status, 'PATCH', url)
        _, raw = self._call('GET', '/drive/v3/files/%s' % quote(meta['id'], safe=''), {'fields': 'id,trashed'})
        if self._json(raw).get('trashed') is not True:
            raise Stop('corbeille_non_confirmee')

    def _mkdir(self, path):
        pure = PurePosixPath(path)
        parent = self._folder_id(str(pure.parent))

        def create():
            answer, raw = self._call('POST', '/drive/v3/files', {'fields': FIELDS}, {'name': pure.name, 'mimeType': FOLDER, 'parents': [parent]}, safe=False)
            return self._json(raw)
        self._exclusive(parent, pure.name, create)

    def prepare(self):
        """Dossier AxiorHub, puis racines. Avec ``drive.file``, seul un dossier créé par l'application reste visible : il est donc créé ici."""
        current = self.start
        for part in [p for p in self.base.split('/') if p]:
            rows = [r for r in self._named(current, part) if r.get('mimeType') == FOLDER]
            if rows:
                current = rows[0]['id']
                continue

            def create(parent=current, name=part):
                return self._json(self._call('POST', '/drive/v3/files', {'fields': FIELDS}, {'name': name, 'mimeType': FOLDER, 'parents': [parent]}, safe=False)[1])
            current = self._exclusive(current, part, create)['id']
        self.root_id = current
        self._folders = {}
        for root in self.cfg['roots']:
            built = ''
            for part in [p for p in root.split('/') if p]:
                built += '/' + part
                try:
                    self._folder_id(built)
                except Stop as ex:
                    if str(ex) != 'http_404':
                        raise
                    self._mkdir(built)
        return {'root_id': current}

    # ----- cloisonnement
    def account_identity(self):
        about = self._json(self._call('GET', '/drive/v3/about', {'fields': 'user(emailAddress)'})[1])
        return str((about.get('user') or {}).get('emailAddress') or '').lower()

    def permission_support(self):
        return {'possible': True, 'raison': '', 'limites': (
            'Un accès hérité d’un dossier parent ne se retire pas sur le sous-dossier : Google impose de le retirer sur le dossier d’origine, ou de passer le '
            'sous-dossier en accès limité dans Drive, ce qu’AxiorHub ne fait pas seul, car cela retirerait aussi les accès hérités des comptes autorisés.')}

    def _item(self, path):
        return {'id': self._folder_id(path)} if self._is_folder(path) else self._meta(path)

    def _is_folder(self, path):
        try:
            self._folder_id(path)
            return True
        except Stop as ex:
            if str(ex) != 'http_404':
                raise
            return False

    def _permissions(self, ident):
        out, token = [], ''
        fields = 'nextPageToken,permissions(id,type,role,emailAddress,domain,displayName,permissionDetails(inherited,inheritedFrom))'
        while True:
            query = {'fields': fields, 'pageSize': 100}
            if token:
                query['pageToken'] = token
            page = self._json(self._call('GET', '/drive/v3/files/%s/permissions' % quote(ident, safe=''), query)[1])
            out += page.get('permissions') or []
            token = page.get('nextPageToken') or ''
            if not token:
                return out

    def list_permissions(self, path):
        ident = self._item(path)['id']
        rows = self._permissions(ident)
        parents = self._json(self._call('GET', '/drive/v3/files/%s' % quote(ident, safe=''), {'fields': 'parents'})[1]).get('parents') or []
        above = {p.get('id') for parent in parents for p in self._parent_permissions(parent)}
        out = []
        for p in rows:
            details = p.get('permissionDetails') or []
            kind = {'user': 'compte', 'group': 'groupe', 'domain': 'domaine', 'anyone': 'lien'}.get(p.get('type'), 'autre')
            out.append({'id': p.get('id'), 'item': ident, 'type': kind, 'identite': str(p.get('emailAddress') or p.get('domain') or p.get('displayName') or '').lower(),
                        'nom': p.get('displayName', ''), 'role': p.get('role', ''), 'proprietaire': p.get('role') in ('owner', 'organizer'),
                        'herite': any(d.get('inherited') for d in details) or (not details and p.get('id') in above)})
        return out

    def _parent_permissions(self, parent):
        """Accès du dossier parent : Drive ne détaille pas toujours l'héritage ; un accès présent au-dessus est traité comme hérité (aucun retrait)."""
        try:
            return self._permissions(parent)
        except Stop as ex:
            if str(ex) in ('http_403', 'http_404'):
                return []
            raise

    def remove_permission(self, path, entry):
        self._call('DELETE', '/drive/v3/files/%s/permissions/%s' % (quote(entry['item'], safe=''), quote(entry['id'], safe='')), safe=False)


def clean_parent(path):
    return str(PurePosixPath(path).parent)
