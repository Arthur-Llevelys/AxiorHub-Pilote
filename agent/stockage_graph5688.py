"""5.6.88 : OneDrive et SharePoint par Microsoft Graph, avec le contrat de ``DAV``.

- Chemins : adressage natif ``/root:/chemin:`` sous le dossier AxiorHub.
- Version : ``eTag``, passé en ``If-Match`` ; refus du serveur (412) rendu en ``version_nextcloud_modifiee``.
- Création exclusive : ``@microsoft.graph.conflictBehavior=fail`` ; conflit du serveur (409) rendu en ``http_412``.
- Lecture : Graph redirige vers une adresse à durée limitée, or ``HTTP`` ne suit aucune redirection. L'adresse vient de la métadonnée
  ``@microsoft.graph.downloadUrl``, son hôte est contrôlé (``TRANSFER_HOSTS``) et elle s'appelle sans jeton.
- Envoi par blocs : session Graph au-delà de 4 Mio, blocs multiples de 320 Kio, adresse contrôlée, jamais de jeton vers elle. Avant le dernier
  bloc d'un remplacement, la version est relue : le ``If-Match`` de la session n'a été vérifié qu'à son ouverture.
- Portées : ``Files.ReadWrite`` pour OneDrive ; ``Files.ReadWrite.All`` et ``Sites.Read.All`` si un site SharePoint est choisi.
- Révocation : Graph ne sait pas révoquer un seul jeton délégué. La déconnexion efface les jetons du coffre, et l'interface indique où retirer
  le consentement (mon compte Microsoft ou centre d'administration Entra).
"""
import json
from pathlib import PurePosixPath
from urllib.parse import quote

from .adaptateurs5652 import MicrosoftGraph
from .common import Stop
from . import cloud5688 as core

SIMPLE_MAX = 4 * 1024 * 1024
FRAGMENT = 32 * 327_680            # 10 Mio, multiple de 320 Kio exigé par Graph
SELECT = 'id,name,eTag,size,createdDateTime,lastModifiedDateTime,webUrl,file,folder,package,remoteItem,@microsoft.graph.downloadUrl'
CONFLICT = '@microsoft.graph.conflictBehavior'


class MicrosoftAdapter(MicrosoftGraph):
    provider = 'cloud_microsoft'
    base_key = 'microsoft_graph'

    def scopes(self, capabilities):
        if str(self.ctx.params.get('site') or ''):
            return 'offline_access User.Read Files.ReadWrite.All Sites.Read.All'
        return 'offline_access User.Read Files.ReadWrite'

    def identity(self):
        me = self.request('GET', '', url=core.ENDPOINTS['graph'] + '/me?$select=userPrincipalName,displayName')
        return {'compte': me.get('userPrincipalName') or me.get('displayName') or ''}

    def revoke(self):
        return 'locale_seulement'         # Graph ne révoque pas un jeton isolé ; /me/revokeSignInSessions fermerait toutes les sessions de la personne


class MicrosoftStore(core.CloudStore):
    provider = 'cloud_microsoft'
    label = 'SharePoint ou OneDrive'
    adapter = MicrosoftAdapter
    strict_names = True

    def __init__(self, cfg):
        super().__init__(cfg)
        drive = str(self.cloud.get('drive_id') or '')
        self.route = '/drives/' + quote(drive, safe='') if drive else '/me/drive'

    @property
    def api(self):
        return core.ENDPOINTS['graph']

    def _url(self, tail):
        return self.api + self.route + tail

    def _item_url(self, remote, tail=''):
        return self._url('/root:%s:%s' % (quote(remote, safe='/'), tail))

    def _call(self, method, url, body=None, headers=None, limit=2_000_000, safe=None, ok=(200, 201, 204)):
        hdr = dict(headers or {})
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            hdr['Content-Type'] = 'application/json'
        status, answer, raw = self._send(method, url, data, hdr, limit, safe=safe)
        if status not in ok:
            raise self._fail(status, method, url)
        return raw

    def _normal(self, row, path=''):
        directory = 'folder' in row
        return {'id': row.get('id'), 'name': row.get('name', ''), 'directory': directory, 'etag': '' if directory else row.get('eTag', ''),
                'modified': core.http_date(row.get('lastModifiedDateTime')), 'created': core.http_date(row.get('createdDateTime')),
                'size': 0 if directory else int(row.get('size') or 0), 'web': row.get('webUrl', ''), 'download': row.get('@microsoft.graph.downloadUrl', ''), 'path': path}

    def _list(self, path):
        url, out = self._item_url(self._remote(path), '/children?$top=200&$select=' + SELECT), []
        while url:
            if not url.startswith(self.api + '/'):
                raise Stop('hote_transfert_refuse')
            page = self._json(self._call('GET', url))
            for row in page.get('value') or []:
                if 'folder' in row or 'file' in row:      # bloc-notes OneNote et éléments partagés d'un autre lecteur : pas des fichiers du cabinet
                    out.append(self._normal(row))
                else:
                    self.refused = self.refused + ['élément non lisible : ' + str(row.get('name', ''))[-100:]]
            url = page.get('@odata.nextLink') or ''
        return out

    def _meta(self, path):
        return self._normal(self._json(self._call('GET', self._item_url(self._remote(path), '?$select=' + SELECT))), path)

    def _read(self, meta, limit):
        link = meta.get('download')
        if not link:                               # la métadonnée ne l'a pas donnée : autre lecture de l'élément, jamais une redirection suivie à l'aveugle
            status, answer, _ = self._send('GET', self._url('/items/%s/content' % quote(meta['id'], safe='')), None, {}, 100_000)
            link = answer.get('location', '') if status in (301, 302, 303, 307) else ''
        if not core.host_allowed(link):
            raise Stop('hote_transfert_refuse')
        status, _, raw = self._send('GET', link, None, {}, limit, auth=False)
        if status != 200:
            raise self._fail(status, 'GET', link)
        return raw

    def _session(self, url, data, headers=None, body=None, before_last=None):
        """Envoi par blocs. L'adresse de session est préauthentifiée : le jeton n'y va jamais, et son hôte est contrôlé. 409 à la fin : nom pris entre-temps."""
        raw = self._call('POST', url, body or {}, headers, safe=False)
        link = self._json(raw).get('uploadUrl', '')
        if not core.host_allowed(link):
            raise Stop('hote_transfert_refuse')
        total, offset = len(data), 0
        while offset < total:
            end = min(offset + FRAGMENT, total)
            if end == total and before_last:
                try:
                    before_last()
                except Stop:
                    self._send('DELETE', link, None, {}, auth=False, safe=False)
                    raise
            status, _, answer = self._send('PUT', link, data[offset:end], {'Content-Type': 'application/octet-stream', 'Content-Range': 'bytes %d-%d/%d' % (offset, end - 1, total)}, auth=False, safe=True)
            if status in (200, 201):
                return self._json(answer)
            if status == 409:
                raise Stop('http_412')
            if status != 202:
                raise self._fail(status, 'PUT', link.split('?', 1)[0])
            offset = end
        raise Stop('envoi_incomplet')

    def _create(self, path, data, content_type):
        remote = self._remote(path)
        if len(data) > SIMPLE_MAX:
            self._session(self._item_url(remote, '/createUploadSession'), data, body={'item': {CONFLICT: 'fail', 'name': PurePosixPath(remote).name}})
            return
        url = self._item_url(remote, '/content?' + CONFLICT + '=fail')
        status, _, _ = self._send('PUT', url, data, {'Content-Type': content_type}, safe=False)
        if status == 409:
            raise Stop('http_412')
        if status not in (200, 201):
            raise self._fail(status, 'PUT', url)

    def server_tag(self, meta):
        return meta['etag']

    def _put_tagged(self, meta, data, tag):
        target = '/items/%s' % quote(meta['id'], safe='')
        try:
            if len(data) > SIMPLE_MAX:
                def unchanged():
                    if self._normal(self._json(self._call('GET', self._url(target + '?$select=id,eTag')))).get('etag') != tag:
                        raise Stop('version_nextcloud_modifiee')
                self._session(self._url(target + '/createUploadSession'), data, {'If-Match': tag}, {'item': {CONFLICT: 'replace'}}, unchanged)
                return
            url = self._url(target + '/content')
            status, _, _ = self._send('PUT', url, data, {'Content-Type': 'application/octet-stream', 'If-Match': tag}, safe=False)
            if status == 412:
                raise Stop('version_nextcloud_modifiee')
            if status not in (200, 201):
                raise self._fail(status, 'PUT', url)
        except Stop as ex:
            if str(ex) in ('http_412', 'http_409'):
                raise Stop('version_nextcloud_modifiee') from None
            raise

    def _trash(self, meta):
        url = self._url('/items/%s' % quote(meta['id'], safe=''))
        status, _, _ = self._send('DELETE', url, None, {'If-Match': meta['etag']}, safe=False)
        if status == 412:
            raise Stop('version_nextcloud_modifiee')
        if status not in (200, 204):
            raise self._fail(status, 'DELETE', url)

    def _mkdir_remote(self, remote):
        parent = str(PurePosixPath(remote).parent)
        url = self._url('/root/children') if parent == '/' else self._item_url(parent, '/children')
        status, _, _ = self._send('POST', url, json.dumps({'name': PurePosixPath(remote).name, 'folder': {}, CONFLICT: 'fail'}).encode(), {'Content-Type': 'application/json'}, safe=False)
        if status == 409:
            raise Stop('http_412')
        if status not in (200, 201):
            raise self._fail(status, 'POST', url)

    def _mkdir(self, path):
        self._mkdir_remote(self._remote(path))

    def _resolve_site(self, site, library):
        site = str(site).strip().replace('https://', '')
        if '/' in site and ':/' not in site:
            host, _, tail = site.partition('/')
            site = host + ':/' + tail
        info = self._json(self._call('GET', self.api + '/sites/' + quote(site, safe=':/')))
        drives = self._json(self._call('GET', self.api + '/sites/%s/drives?$select=id,name,driveType' % quote(str(info.get('id')), safe=''))).get('value') or []
        rows = [d for d in drives if not library or d.get('name') == library]
        if not rows:
            raise Stop('bibliotheque_sharepoint_introuvable')
        return rows[0]['id']

    def prepare(self):
        extra = {}
        site = str(self.cloud.get('site') or '')
        if site:
            extra['drive_id'] = self._resolve_site(site, str(self.cloud.get('bibliotheque') or ''))
            self.route = '/drives/' + quote(extra['drive_id'], safe='')
        built = ''
        parts = [p for p in self.base.split('/') if p]
        for root in self.cfg['roots']:
            built = ''
            for part in parts + [p for p in root.split('/') if p]:
                built += '/' + part
                try:
                    self._mkdir_remote(built)
                except Stop as ex:
                    if str(ex) != 'http_412':
                        raise
        return extra

    # ----- cloisonnement
    def account_identity(self):
        me = self._json(self._call('GET', self.api + '/me?$select=userPrincipalName,mail'))
        return str(me.get('userPrincipalName') or me.get('mail') or '').lower()

    def permission_support(self):
        drive = self._json(self._call('GET', self.api + self.route + '?$select=driveType'))
        if drive.get('driveType') == 'personal':
            return {'possible': False, 'raison': 'Compte OneDrive personnel : il n’y a ni annuaire de membres ni bibliothèque partagée à cloisonner. '
                                                 'Le retrait se fait à la main, dans OneDrive, à partir de « Gérer l’accès ».'}
        return {'possible': True, 'raison': '', 'limites': (
            'Microsoft Graph ne retire que les accès posés directement. Un accès hérité du site ou de la bibliothèque ne peut être retiré qu’en rompant l’héritage '
            'dans SharePoint, ce qu’AxiorHub ne fait pas.')}

    @staticmethod
    def _identity(row):
        granted = row.get('grantedToV2') or row.get('grantedTo') or {}
        for key in ('user', 'siteUser'):
            person = granted.get(key) or {}
            login = str(person.get('email') or person.get('loginName') or person.get('userPrincipalName') or '').lower()
            if login:
                return login.rsplit('|', 1)[-1], str(person.get('displayName') or ''), 'compte'
        group = granted.get('group') or granted.get('siteGroup') or {}
        if group:
            return str(group.get('email') or group.get('displayName') or '').lower(), str(group.get('displayName') or ''), 'groupe'
        return str(((row.get('invitation') or {}).get('email')) or '').lower(), '', 'compte'

    def list_permissions(self, path):
        meta = self._meta(path)
        out, url = [], self._url('/items/%s/permissions?$top=200' % quote(meta['id'], safe=''))
        while url:
            page = self._json(self._call('GET', url))
            for row in page.get('value') or []:
                link = row.get('link') or {}
                identity, name, kind = self._identity(row)
                if link:
                    kind, identity = 'lien', 'lien de partage (%s)' % str(link.get('scope') or 'portée inconnue')
                roles = row.get('roles') or []
                out.append({'id': row.get('id'), 'item': meta['id'], 'type': kind, 'identite': identity, 'nom': name, 'role': ', '.join(roles),
                            'proprietaire': 'owner' in roles, 'herite': bool(row.get('inheritedFrom'))})
            url = page.get('@odata.nextLink') or ''
            if url and not url.startswith(self.api + '/'):
                raise Stop('hote_transfert_refuse')
        return out

    def remove_permission(self, path, entry):
        url = self._url('/items/%s/permissions/%s' % (quote(entry['item'], safe=''), quote(entry['id'], safe='')))
        status, _, _ = self._send('DELETE', url, None, {}, safe=False)
        if status not in (200, 204):
            raise self._fail(status, 'DELETE', url)
