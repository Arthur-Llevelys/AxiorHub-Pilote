"""5.6.88 : Dropbox, avec le contrat de ``DAV``.

- Chemins natifs, sous le dossier AxiorHub ; la casse n'est pas distinguée par Dropbox.
- Version : ``rev``. Le remplacement passe par ``mode=update`` avec ``strict_conflict`` ; un conflit répond 409, rendu en
  ``version_nextcloud_modifiee``. La lecture demande ``rev:…`` : le fichier lu est celui attendu, sans second contrôle.
- Création exclusive : ``mode=add`` sans renommage automatique ; conflit rendu en ``http_412``.
- Envoi par blocs : session Dropbox au-delà de 8 Mio.
- ``Dropbox-API-Arg`` est un en-tête HTTP : le JSON y est écrit en ASCII, sans quoi un nom accentué serait refusé.
- Portées : lecture, écriture, identité du compte ; les portées de partage ne sont demandées que si le cabinet active le cloisonnement.
- Cloisonnement : seul un dossier partagé a des membres, sur un compte d'équipe. Une application limitée à un dossier d'application n'y a pas accès.
"""
import json
from urllib.parse import quote

from .adaptateurs5652 import Adapter, OAuthCodeMixin
from .common import Stop
from . import cloud5688 as core

SIMPLE_MAX = 8 * 1024 * 1024
CHUNK = 8 * 1024 * 1024
BASE_SCOPES = 'account_info.read files.metadata.read files.content.read files.content.write'
SHARING_SCOPES = ' sharing.read sharing.write'


class DropboxAdapter(OAuthCodeMixin, Adapter):
    provider = 'cloud_dropbox'
    authorize_url = 'https://www.dropbox.com/oauth2/authorize'
    token_url = 'https://api.dropboxapi.com/oauth2/token'
    authorize_extra = {'token_access_type': 'offline'}

    @property
    def base(self):
        return core.ENDPOINTS['dropbox_api']

    def scopes(self, capabilities):
        return BASE_SCOPES + (SHARING_SCOPES if str(self.ctx.params.get('cloisonnement') or '').lower() == 'oui' else '')

    def identity(self):
        url = self.base + '/2/users/get_current_account'
        raw = self.ctx.http(url).request('POST', url, b'null', {'Authorization': 'Bearer ' + self.bearer(), 'Content-Type': 'application/json'}, 500_000)
        try:
            info = json.loads(raw)
        except ValueError:
            raise Stop('reponse_fournisseur_invalide') from None
        return {'compte': info.get('email') or '', 'type': (info.get('account_type') or {}).get('.tag', '')}

    def revoke(self):
        url = self.base + '/2/auth/token/revoke'
        self.ctx.http(url).request('POST', url, b'null', {'Authorization': 'Bearer ' + self.bearer(), 'Content-Type': 'application/json'}, 100_000)
        return 'revoquee'


def _arg(value):
    return json.dumps(value, ensure_ascii=True).replace('\x7f', '\\u007f')


class DropboxStore(core.CloudStore):
    provider = 'cloud_dropbox'
    label = 'Dropbox'
    adapter = DropboxAdapter
    versioned_read = True

    def _base_headers(self):
        return {'Dropbox-API-Path-Root': self.cloud['path_root']} if self.cloud.get('path_root') else {}

    def _rpc(self, endpoint, arg, safe=False):
        """Appel JSON ; renvoie (statut, réponse, résumé d'erreur) : 409 est un cas normal chez Dropbox, l'appelant le traduit."""
        url = core.ENDPOINTS['dropbox_api'] + '/2/' + endpoint
        status, _, raw = self._send('POST', url, _arg(arg).encode(), {'Content-Type': 'application/json', **self._base_headers()}, safe=safe)
        if status == 409:
            try:
                return status, {}, str(json.loads(raw).get('error_summary') or '')
            except (ValueError, AttributeError):
                return status, {}, ''
        if status != 200:
            raise self._fail(status, 'POST', url)
        return status, self._json(raw), ''

    def _content(self, endpoint, arg, data=None, limit=2_000_000, safe=False):
        url = core.ENDPOINTS['dropbox_content'] + '/2/' + endpoint
        hdr = {'Dropbox-API-Arg': _arg(arg), **self._base_headers()}
        if data is not None:
            hdr['Content-Type'] = 'application/octet-stream'
        status, answer, raw = self._send('POST', url, data, hdr, limit, safe=safe)
        if status == 409:
            return status, answer, raw        # le corps dit la nature du conflit et, pour un bloc, où reprendre
        if status != 200:
            raise self._fail(status, 'POST', url)
        return status, answer, raw

    def _normal(self, row, path=''):
        directory = row.get('.tag') == 'folder'
        return {'id': row.get('id'), 'name': row.get('name', ''), 'directory': directory, 'etag': '' if directory else row.get('rev', ''),
                'modified': core.http_date(row.get('server_modified')), 'created': core.http_date(row.get('client_modified')),
                'size': 0 if directory else int(row.get('size') or 0), 'web': 'https://www.dropbox.com/home' + quote(row.get('path_display') or '', safe='/'),
                'path': path, 'downloadable': row.get('is_downloadable', True), 'sharing': row.get('sharing_info') or {}, 'shared_id': row.get('shared_folder_id', '')}

    def _list(self, path):
        status, page, err = self._rpc('files/list_folder', {'path': self._remote(path), 'limit': 1000, 'include_deleted': False}, safe=True)
        out = []
        while True:
            if status == 409:
                if 'not_found' in err:
                    raise Stop('http_404')
                if 'not_folder' in err:
                    raise Stop('chemin_est_un_fichier')
                raise Stop('http_409')
            out += [self._normal(r) for r in page.get('entries') or [] if r.get('.tag') in ('file', 'folder')]
            if not page.get('has_more'):
                return out
            status, page, err = self._rpc('files/list_folder/continue', {'cursor': page.get('cursor')}, safe=True)

    def _meta(self, path):
        status, row, err = self._rpc('files/get_metadata', {'path': self._remote(path), 'include_deleted': False}, safe=True)
        if status == 409:
            raise Stop('http_404' if 'not_found' in err else 'http_409')
        return self._normal(row, path)

    def _read(self, meta, limit):
        if meta.get('downloadable') is False:
            raise Stop('fichier_non_telechargeable')       # Paper et autres documents sans octets
        status, _, raw = self._content('files/download', {'path': 'rev:' + meta['etag']}, None, limit, safe=True)
        if status == 409:
            raise Stop('http_412')
        return raw

    def _upload(self, remote, data, mode):
        commit = {'path': remote, 'mode': mode, 'autorename': False, 'mute': True, 'strict_conflict': True}
        if len(data) <= SIMPLE_MAX:
            status, _, raw = self._content('files/upload', commit, data)
            return status == 409, raw
        status, _, raw = self._content('files/upload_session/start', {'close': False}, data[:CHUNK], safe=False)
        session = self._json(raw).get('session_id', '')
        offset = CHUNK
        while len(data) - offset > CHUNK:
            cursor = {'session_id': session, 'offset': offset}
            status, _, raw = self._content('files/upload_session/append_v2', {'cursor': cursor, 'close': False}, data[offset:offset + CHUNK], safe=True)
            if status == 409:          # bloc déjà reçu (réponse perdue) : Dropbox indique où reprendre
                offset = self._resume_at(raw, offset)
                continue
            offset += CHUNK
        status, _, raw = self._content('files/upload_session/finish', {'cursor': {'session_id': session, 'offset': offset}, 'commit': commit}, data[offset:])
        return status == 409, raw

    def _resume_at(self, raw, offset):
        """Position donnée par Dropbox dans ``incorrect_offset`` ; tout autre refus arrête l'envoi. La position doit avancer."""
        error = self._json(raw).get('error') or {}
        lookup = error.get('lookup_failed') if isinstance(error.get('lookup_failed'), dict) else error
        position = lookup.get('correct_offset') if isinstance(lookup, dict) else None
        if not isinstance(position, int) or position <= offset:
            raise Stop('envoi_incomplet')
        return position

    def _create(self, path, data, content_type):
        conflict, raw = self._upload(self._remote(path), data, 'add')
        if conflict:
            raise Stop('http_412')

    def server_tag(self, meta):
        return meta['etag']

    def _put_tagged(self, meta, data, tag):
        conflict, raw = self._upload(self._remote(meta['path']), data, {'.tag': 'update', 'update': tag})
        if conflict:
            raise Stop('version_nextcloud_modifiee')

    def _trash(self, meta):
        status, _, err = self._rpc('files/delete_v2', {'path': self._remote(meta['path']), 'parent_rev': meta['etag']})
        if status == 409:                 # version différente ou fichier déjà parti : les deux se lisent comme une version changée
            raise Stop('version_nextcloud_modifiee')

    def _mkdir_remote(self, remote):
        status, _, err = self._rpc('files/create_folder_v2', {'path': remote, 'autorename': False})
        if status == 409:
            raise Stop('http_412' if 'conflict' in err else 'http_409')

    def _mkdir(self, path):
        self._mkdir_remote(self._remote(path))

    def _account(self):
        url = core.ENDPOINTS['dropbox_api'] + '/2/users/get_current_account'
        status, _, raw = self._send('POST', url, b'null', {'Content-Type': 'application/json'}, safe=True)
        if status != 200:
            raise self._fail(status, 'POST', url)
        return self._json(raw)

    def prepare(self):
        extra = {}
        if str(self.cloud.get('equipe') or '').lower() == 'oui':
            root = (self._account().get('root_info') or {}).get('root_namespace_id')
            if not root:
                raise Stop('espace_equipe_introuvable')
            extra['path_root'] = _arg({'.tag': 'root', 'root': str(root)})
            self.cloud['path_root'] = extra['path_root']
        for root in self.cfg['roots']:
            built = ''
            for part in [p for p in self.base.split('/') if p] + [p for p in root.split('/') if p]:
                built += '/' + part
                try:
                    self._mkdir_remote(built)
                except Stop as ex:
                    if str(ex) != 'http_412':
                        raise
        return extra

    # ----- cloisonnement
    def account_identity(self):
        return str(self._account().get('email') or '').lower()

    def permission_support(self):
        kind = (self._account().get('account_type') or {}).get('.tag', '')
        if kind != 'business':
            return {'possible': False, 'raison': 'Compte Dropbox personnel (%s) : un dossier ne peut pas être fermé à un membre du dossier partagé qui le contient. '
                                                 'Le retrait se fait à la main, dans Dropbox, à partir de « Partager ».' % (kind or 'type inconnu')}
        return {'possible': True, 'raison': '', 'limites': (
            'Dropbox ne porte les membres que sur un dossier partagé. Un membre hérité du dossier partagé parent ne se retire qu’en passant le dossier en accès restreint '
            'dans Dropbox. Les membres posés sur un fichier isolé ne sont pas examinés.')}

    def list_permissions(self, path):
        meta = self._meta(path)
        if not meta.get('directory'):
            return []
        shared = meta.get('shared_id') or (meta.get('sharing') or {}).get('shared_folder_id') or ''
        parent = (meta.get('sharing') or {}).get('parent_shared_folder_id') or ''
        target = shared or parent
        out = []
        if target:
            status, page, err = self._rpc('sharing/list_folder_members', {'shared_folder_id': target, 'limit': 200}, safe=True)
            while True:
                if status == 409:
                    raise Stop('http_409')
                for row in page.get('users') or []:
                    user = row.get('user') or {}
                    access = (row.get('access_type') or {}).get('.tag', '')
                    out.append({'id': user.get('account_id', ''), 'item': target, 'type': 'compte', 'identite': str(user.get('email') or '').lower(), 'nom': user.get('display_name', ''),
                                'role': access, 'proprietaire': access == 'owner', 'herite': bool(row.get('is_inherited')) or not shared})
                for row in page.get('groups') or []:
                    group = row.get('group') or {}
                    out.append({'id': group.get('group_id', ''), 'item': target, 'type': 'groupe', 'identite': str(group.get('group_name') or '').lower(), 'nom': group.get('group_name', ''),
                                'role': (row.get('access_type') or {}).get('.tag', ''), 'proprietaire': False, 'herite': bool(row.get('is_inherited')) or not shared})
                for row in page.get('invitees') or []:
                    out.append({'id': '', 'item': target, 'type': 'compte', 'identite': str((row.get('invitee') or {}).get('email') or '').lower(), 'nom': '',
                                'role': (row.get('access_type') or {}).get('.tag', ''), 'proprietaire': False, 'herite': bool(row.get('is_inherited')) or not shared, 'invitation': True})
                if not page.get('cursor'):
                    break
                status, page, err = self._rpc('sharing/list_folder_members/continue', {'cursor': page['cursor']}, safe=True)
        status, links, err = self._rpc('sharing/list_shared_links', {'path': self._remote(path), 'direct_only': True}, safe=True)
        if status == 200:
            for link in links.get('links') or []:
                out.append({'id': link.get('url', ''), 'item': '', 'type': 'lien', 'identite': 'lien de partage', 'nom': '', 'role': '', 'proprietaire': False, 'herite': False})
        return out

    def remove_permission(self, path, entry):
        if entry.get('type') == 'lien':
            status, _, err = self._rpc('sharing/revoke_shared_link', {'url': entry['id']})
            if status == 409:
                raise Stop('retrait_refuse_par_le_fournisseur')
            return
        if entry.get('invitation') or not entry.get('id'):
            raise Stop('retrait_a_faire_chez_le_fournisseur')
        member = {'.tag': 'dropbox_id', 'dropbox_id': entry['id']}
        status, _, err = self._rpc('sharing/remove_folder_member', {'shared_folder_id': entry['item'], 'member': member, 'leave_a_copy': False})   # jamais de copie laissée à un compte retiré
        if status == 409:
            raise Stop('retrait_refuse_par_le_fournisseur')
