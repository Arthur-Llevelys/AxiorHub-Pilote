"""5.6.88, lot cloud : stockages Google Drive, Microsoft Graph et Dropbox, connexion, copie, nommage, cloisonnement.

Un vrai serveur HTTP tourne sur la boucle locale et imite les trois fournisseurs ; aucun service réel n'est appelé. Ces tests prouvent le comportement d'AxiorHub
face à l'idée que ces imitations donnent des fournisseurs, pas que les fournisseurs se comportent ainsi : c'est le rôle du contrôle d'aptitude, sur un vrai compte.
"""
import base64
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import re
import tempfile
import threading
import unittest
from unittest.mock import patch
import uuid
from urllib.parse import parse_qs, unquote, urlsplit

from agent import cloud5688 as core, cloud_cloison5688 as cloison, cloud_gestion5688 as gestion, cloud_migration5688 as migration, cloud_nommage5688 as nommage
from agent import connexions5652 as cx, adaptateurs5652 as ad, membres5650, office440, office_gen5684, stockage_drive5688 as gdrive, stockage_dropbox5688 as dropbox, stockage_graph5688 as graph
from agent.common import Stop, load_config, private_json
from agent.dav import DAV, LocalFolder
from agent.desk import Desk
import test_agent as fixtures

ROOTS = ['/Dossiers']
BIG = 1_100_000
ADMIN = 'admin@example.test'


# ---------------------------------------------------------------------------------------------------- imitation des fournisseurs
class Fake:
    """État commun des imitations ; ``failures`` : réponses forcées, ``before`` : action glissée juste avant une écriture (concurrence)."""

    def __init__(self):
        self.calls, self.failures, self.before = [], [], {}
        self.lock = threading.RLock()
        self.drive, self.next = {}, 1
        self.graph = {}
        self.box = {}
        self.sessions = {}
        self.corrupt = False
        self.tokens = {'token': 'renouvele-fictif', 'expires_in': 3600}
        self.perms = {}
        self.drive_type = 'business'
        self.dropbox_type = 'business'
        self.foreign_upload = ''

    def stamp(self):
        self.next += 1
        return '2026-10-%02dT10:%02d:%02dZ' % (1 + self.next // 3600 % 27, self.next // 60 % 60, self.next % 60)

    # ----- Drive
    def d_add(self, name, parent='root', mime='text/plain', data=b'', ident=None):
        ident = ident or 'g%d' % self.next
        self.next += 1
        self.drive[ident] = {'id': ident, 'name': name, 'mimeType': mime, 'parents': [parent], 'version': '10', 'data': data, 'createdTime': self.stamp(), 'modifiedTime': self.stamp(),
                             'trashed': False, 'webViewLink': 'https://drive.example.test/' + ident, 'size': str(len(data)), 'etag': 'e-' + uuid.uuid4().hex}
        return ident

    # ----- Graph
    def g_add(self, path, data=None):
        ident = 'm%d' % self.next
        self.next += 1
        self.graph[path] = {'id': ident, 'path': path, 'name': path.rsplit('/', 1)[-1], 'eTag': '"{%s},1"' % uuid.uuid4().hex, 'data': data, 'size': len(data or b''), 'createdDateTime': self.stamp(),
                            'lastModifiedDateTime': self.stamp(), 'webUrl': 'https://valbrune.sharepoint.com' + path}
        return self.graph[path]

    # ----- Dropbox
    def b_add(self, path, data=None):
        self.box[path] = {'id': 'id:%d' % self.next, 'path': path, 'name': path.rsplit('/', 1)[-1], 'rev': uuid.uuid4().hex[:12], 'data': data, 'server_modified': self.stamp(), 'client_modified': self.stamp()}
        self.next += 1
        return self.box[path]


def handler_for(fake):
    class H(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'

        def log_message(self, *a):
            pass

        def reply(self, status=200, body=None, headers=None):
            raw = body if isinstance(body, bytes) else (json.dumps(body).encode() if body is not None else b'')
            self.send_response(status)
            for k, v in (headers or {}).items():
                self.send_header(k, v)
            self.send_header('Content-Length', str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def handle_any(self):
            size = int(self.headers.get('Content-Length') or 0)
            body = self.rfile.read(size) if size else b''
            parts = urlsplit(self.path)
            path, query = unquote(parts.path), {k: v[0] for k, v in parse_qs(parts.query, keep_blank_values=True).items()}
            with fake.lock:
                fake.calls.append({'method': self.command, 'path': path, 'query': query, 'auth': self.headers.get('Authorization', ''), 'body': body, 'if_match': self.headers.get('If-Match', ''),
                                   'content_type': self.headers.get('Content-Type', ''), 'arg': self.headers.get('Dropbox-API-Arg', '')})
                for i, (needle, method, status, headers, payload) in enumerate(fake.failures):
                    if needle in path and method in ('', self.command):
                        fake.failures.pop(i)
                        return self.reply(status, payload, headers)
                for key in list(fake.before):
                    if key in path and self.command != 'GET':
                        fake.before.pop(key)()
                if path.startswith('/oauth/'):
                    fake.calls[-1]['form'] = parse_qs(body.decode())
                    return self.reply(200, {'access_token': fake.tokens['token'], 'refresh_token': 'renouvellement-suivant', 'expires_in': fake.tokens['expires_in']})
                if path.startswith('/google'):
                    return self.google(path[len('/google'):], query, body)
                if path.startswith('/graph'):
                    return self.graph(path[len('/graph'):], query, body)
                if path.startswith('/transfer'):
                    return self.transfer(path[len('/transfer'):], body)
                if path.startswith('/dbapi') or path.startswith('/dbcontent'):
                    return self.box(path.split('/', 3)[3], body)
                return self.reply(404, {})

        do_GET = do_POST = do_PUT = do_PATCH = do_DELETE = handle_any

        # ----------------------------------------------------------------------- Drive
        def google(self, path, q, body):
            if path == '/drive/v3/about':
                return self.reply(200, {'user': {'emailAddress': 'cabinet@example.test', 'displayName': 'Cabinet'}})
            if path == '/drive/v3/files' and self.command == 'GET':
                parent = re.search(r"'((?:[^'\\]|\\.)*)' in parents", q['q']).group(1)
                name = re.search(r"name='((?:[^'\\]|\\.)*)'", q['q'])
                rows = [f for f in fake.drive.values() if parent in f['parents'] and not f['trashed'] and (not name or f['name'].lower() == name.group(1).replace("\\'", "'").lower())]
                return self.reply(200, {'files': [{k: v for k, v in f.items() if k not in ('data', 'etag')} for f in rows]})
            if path == '/drive/v3/files' and self.command == 'POST':
                meta = json.loads(body)
                return self.reply(200, self.pub(fake.d_add(meta['name'], meta['parents'][0], meta['mimeType'])))
            if path == '/upload/drive/v3/files' and q.get('uploadType') == 'multipart':
                boundary = self.headers['Content-Type'].split('boundary=')[1].encode()
                chunks = body.split(b'--' + boundary)
                meta = json.loads(chunks[1].split(b'\r\n\r\n', 1)[1])
                data = chunks[2].split(b'\r\n\r\n', 1)[1][:-2]
                return self.reply(200, self.pub(fake.d_add(meta['name'], meta['parents'][0], 'text/plain', data)))
            if path == '/upload/drive/v3/files' and q.get('uploadType') == 'resumable':
                sid = uuid.uuid4().hex
                fake.sessions[sid] = {'meta': json.loads(body), 'data': b''}
                host = self.headers['Host']
                return self.reply(200, {}, {'Location': 'http://%s/google/upload/session/%s' % (host, sid)})
            if path.startswith('/upload/session/'):
                sess = fake.sessions[path.rsplit('/', 1)[1]]
                m = re.fullmatch(r'bytes (\d+)-(\d+)/(\d+)', self.headers['Content-Range'])
                sess['data'] += body
                if int(m.group(2)) + 1 < int(m.group(3)):
                    return self.reply(308, b'', {'Range': 'bytes=0-%s' % m.group(2)})
                return self.reply(200, self.pub(fake.d_add(sess['meta']['name'], sess['meta']['parents'][0], 'text/plain', sess['data'])))
            m = re.fullmatch(r'/drive/v2/files/([^/]+)', path)
            if m:
                f = fake.drive[m.group(1)]
                if self.command == 'PATCH':   # 5.6.89 : corbeille conditionnelle ; ``ignorer_*`` imite un service qui ne tiendrait pas la promesse
                    if self.headers.get('If-Match') != f['etag'] and not getattr(fake, 'ignorer_condition', False):
                        return self.reply(412, {'error': {'code': 412}})
                    if not getattr(fake, 'ignorer_corbeille', False):
                        f.update(etag_avant_corbeille=f['etag'], trashed=json.loads(body)['labels']['trashed'], etag='e-' + uuid.uuid4().hex)
                return self.reply(200, {'etag': f['etag'], 'version': f['version']})
            m = re.fullmatch(r'/upload/drive/v2/files/([^/]+)', path)
            if m:
                f = fake.drive[m.group(1)]
                if self.headers.get('If-Match') != f['etag']:
                    return self.reply(412, {'error': {'code': 412}})
                f.update(data=body, version=str(int(f['version']) + 1), etag='e-' + uuid.uuid4().hex, size=str(len(body)))
                return self.reply(200, {'id': f['id']})
            m = re.fullmatch(r'/drive/v3/files/([^/]+)/permissions(?:/([^/]+))?', path)
            if m:
                rows = fake.perms.setdefault(m.group(1), [])
                if self.command == 'DELETE':
                    rows[:] = [p for p in rows if p['id'] != m.group(2)]
                    return self.reply(204)
                return self.reply(200, {'permissions': rows})
            m = re.fullmatch(r'/drive/v3/files/([^/]+)/export', path)
            if m:
                return self.reply(200, b'PK export ' + fake.drive[m.group(1)]['name'].encode())
            m = re.fullmatch(r'/drive/v3/files/([^/]+)', path)
            if m:
                f = fake.drive.get(m.group(1))
                if not f:
                    return self.reply(404, {'error': {'code': 404}})
                if self.command == 'PATCH':
                    f.update(json.loads(body))
                    return self.reply(200, self.pub(f['id']))
                return self.reply(200, f['data']) if q.get('alt') == 'media' else self.reply(200, self.pub(f['id']))
            return self.reply(404, {})

        @staticmethod
        def pub(ident):
            return {k: v for k, v in fake.drive[ident].items() if k not in ('data', 'etag')}

        # ----------------------------------------------------------------------- Graph
        def item(self, row):
            out = {k: row[k] for k in ('id', 'name', 'eTag', 'size', 'createdDateTime', 'lastModifiedDateTime', 'webUrl')}
            if row['data'] is None:
                out['folder'] = {}
            else:
                out['file'] = {}
                out['@microsoft.graph.downloadUrl'] = 'http://%s/transfer/down/%s' % (self.headers['Host'], row['id'])
            return out

        def by_id(self, ident):
            return next((r for r in fake.graph.values() if r['id'] == ident), None)

        def graph(self, path, q, body):
            if path == '/me':
                return self.reply(200, {'userPrincipalName': 'cabinet@example.test'})
            if path == '/me/drive':
                return self.reply(200, {'driveType': fake.drive_type, 'id': 'd1'})
            m = re.fullmatch(r'/me/drive/root:(.*?):(/children|/content|/createUploadSession)?', path)
            if m:
                target, tail = m.group(1), m.group(2)
                row = fake.graph.get(target)
                if tail == '/children' and self.command == 'GET':
                    if row is None:
                        return self.reply(404, {'error': {'code': 'itemNotFound'}})
                    return self.reply(200, {'value': [self.item(r) for p, r in fake.graph.items() if p.rsplit('/', 1)[0] == target]})
                if tail == '/children':
                    meta = json.loads(body)
                    child = target + '/' + meta['name']
                    if child in fake.graph:
                        return self.reply(409, {'error': {'code': 'nameAlreadyExists'}})
                    return self.reply(201, self.item(fake.g_add(child)))
                if tail == '/content':
                    if row is not None and q.get('@microsoft.graph.conflictBehavior') == 'fail':
                        return self.reply(409, {'error': {'code': 'nameAlreadyExists'}})
                    return self.reply(201, self.item(fake.g_add(target, body)))
                if tail == '/createUploadSession':
                    if row is not None:
                        return self.reply(409, {'error': {'code': 'nameAlreadyExists'}})
                    sid = uuid.uuid4().hex
                    fake.sessions[sid] = {'path': target, 'data': b''}
                    return self.reply(200, {'uploadUrl': fake.foreign_upload or 'http://%s/transfer/up/%s' % (self.headers['Host'], sid)})
                if row is None:
                    return self.reply(404, {'error': {'code': 'itemNotFound'}})
                return self.reply(200, self.item(row))
            m = re.fullmatch(r'/me/drive/root/children', path)
            if m:
                meta = json.loads(body)
                child = '/' + meta['name']
                if child in fake.graph:
                    return self.reply(409, {'error': {'code': 'nameAlreadyExists'}})
                return self.reply(201, self.item(fake.g_add(child)))
            m = re.fullmatch(r'/me/drive/items/([^/]+)(/content|/createUploadSession|/permissions(?:/([^/]+))?)?', path)
            if m:
                row, tail = self.by_id(m.group(1)), m.group(2)
                if tail and tail.startswith('/permissions'):
                    rows = fake.perms.setdefault(m.group(1), [])
                    if self.command == 'DELETE':
                        rows[:] = [p for p in rows if p['id'] != m.group(3)]
                        return self.reply(204)
                    return self.reply(200, {'value': rows})
                if row is None:
                    return self.reply(404, {})
                match = self.headers.get('If-Match')
                if match and match != row['eTag']:
                    return self.reply(412, {'error': {'code': 'resourceModified'}})
                if tail == '/content':
                    row.update(data=body, size=len(body), eTag='"{%s},2"' % uuid.uuid4().hex)
                    return self.reply(200, self.item(row))
                if tail == '/createUploadSession':
                    sid = uuid.uuid4().hex
                    fake.sessions[sid] = {'row': row, 'data': b''}
                    return self.reply(200, {'uploadUrl': 'http://%s/transfer/up/%s' % (self.headers['Host'], sid)})
                if self.command == 'DELETE':
                    del fake.graph[row['path']]
                    return self.reply(204)
                return self.reply(200, self.item(row))
            return self.reply(404, {})

        def transfer(self, path, body):
            m = re.fullmatch(r'/down/([^/]+)', path)
            if m:
                data = self.by_id(m.group(1))['data']
                return self.reply(200, (data + b'!') if fake.corrupt else data)
            sid = path.rsplit('/', 1)[1]
            sess = fake.sessions[sid]
            if self.command == 'DELETE':
                return self.reply(204)
            first, last, total = map(int, re.fullmatch(r'bytes (\d+)-(\d+)/(\d+)', self.headers['Content-Range']).groups())
            sess['data'] += body
            if last + 1 < total:
                return self.reply(202, {'nextExpectedRanges': ['%d-' % (last + 1)]})
            if 'row' in sess:
                sess['row'].update(data=sess['data'], size=len(sess['data']), eTag='"{%s},3"' % uuid.uuid4().hex)
                return self.reply(200, self.item(sess['row']))
            if sess['path'] in fake.graph:
                return self.reply(409, {'error': {'code': 'nameAlreadyExists'}})
            return self.reply(201, self.item(fake.g_add(sess['path'], sess['data'])))

        # ----------------------------------------------------------------------- Dropbox
        def entry(self, row):
            if row['data'] is None:
                return {'.tag': 'folder', 'id': row['id'], 'name': row['name'], 'path_display': row['path'], **row.get('folder', {})}
            return {'.tag': 'file', 'id': row['id'], 'name': row['name'], 'rev': row['rev'], 'size': len(row['data']), 'server_modified': row['server_modified'], 'client_modified': row['client_modified'], 'path_display': row['path']}

        def conflict(self, summary, extra=None):
            return self.reply(409, {'error_summary': summary, 'error': extra or {}})

        def commit(self, commit, data):
            target, mode = commit['path'], commit['mode']
            row = fake.box.get(target)
            if mode == 'add':
                if row is not None:
                    return self.conflict('path/conflict/file/..')
                return self.reply(200, self.entry(fake.b_add(target, data)))
            if row is None or row['rev'] != mode['update']:
                return self.conflict('path/conflict/file/..')
            row.update(data=data, rev=uuid.uuid4().hex[:12])
            return self.reply(200, self.entry(row))

        def box(self, endpoint, body):
            arg = json.loads(self.headers.get('Dropbox-API-Arg') or (body or b'null').decode() if not self.headers.get('Dropbox-API-Arg') else self.headers['Dropbox-API-Arg'])
            if endpoint == 'users/get_current_account':
                return self.reply(200, {'email': 'cabinet@example.test', 'account_type': {'.tag': fake.dropbox_type}})
            if endpoint == 'auth/token/revoke':
                return self.reply(200, b'null')
            if endpoint == 'files/list_folder':
                if arg['path'] not in fake.box:
                    return self.conflict('path/not_found/..')
                return self.reply(200, {'entries': [self.entry(r) for p, r in fake.box.items() if p.rsplit('/', 1)[0] == arg['path']], 'has_more': False})
            if endpoint == 'files/get_metadata':
                row = fake.box.get(arg['path'])
                return self.reply(200, self.entry(row)) if row else self.conflict('path/not_found/..')
            if endpoint == 'files/download':
                row = next((r for r in fake.box.values() if 'rev:' + r.get('rev', '') == arg['path']), None)
                return self.reply(200, row['data'], {'Dropbox-API-Result': '{}'}) if row else self.conflict('path/not_found/..')
            if endpoint == 'files/upload':
                return self.commit(arg, body)
            if endpoint == 'files/upload_session/start':
                sid = uuid.uuid4().hex
                fake.sessions[sid] = {'data': body}
                return self.reply(200, {'session_id': sid})
            if endpoint == 'files/upload_session/append_v2':
                sess = fake.sessions[arg['cursor']['session_id']]
                if arg['cursor']['offset'] != len(sess['data']):
                    return self.conflict('incorrect_offset/..', {'lookup_failed': {'.tag': 'incorrect_offset', 'correct_offset': len(sess['data'])}})
                sess['data'] += body
                return self.reply(200, b'null')
            if endpoint == 'files/upload_session/finish':
                sess = fake.sessions[arg['cursor']['session_id']]
                return self.commit(arg['commit'], sess['data'] + body)
            if endpoint == 'files/create_folder_v2':
                if arg['path'] in fake.box:
                    return self.conflict('path/conflict/folder/..')
                return self.reply(200, {'metadata': self.entry(fake.b_add(arg['path']))})
            if endpoint == 'files/delete_v2':
                row = fake.box.get(arg['path'])
                if not row or row.get('rev') != arg.get('parent_rev'):
                    return self.conflict('path_lookup/not_found/..')
                del fake.box[arg['path']]
                return self.reply(200, {'metadata': {}})
            if endpoint in ('sharing/list_folder_members',):
                return self.reply(200, fake.perms.get(arg['shared_folder_id'], {'users': []}))
            if endpoint == 'sharing/list_shared_links':
                return self.reply(200, {'links': []})
            if endpoint == 'sharing/remove_folder_member':
                users = fake.perms[arg['shared_folder_id']]['users']
                users[:] = [u for u in users if u['user']['account_id'] != arg['member']['dropbox_id']]
                return self.reply(200, {'.tag': 'complete'})
            return self.reply(404, {})
    return H


class Base(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.EngineTests('test_observation_has_no_mail_write')
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.local = Path(self.tmp.name)
        (self.local / 'Dossiers/DEMO').mkdir(parents=True)
        self.f.c['nextcloud'] = {**self.f.c['nextcloud'], 'local_path': str(self.local)}
        self.desk = Desk(self.f.c)
        self.addCleanup(self.desk.db.close)
        self.fake = Fake()
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), handler_for(self.fake))
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        base = 'http://127.0.0.1:%d' % self.server.server_port
        self.base_url = base
        for target, values in ((core.ENDPOINTS, {'google_api': base + '/google', 'google_oauth': base + '/oauth/revoke', 'graph': base + '/graph',
                                                 'dropbox_api': base + '/dbapi', 'dropbox_content': base + '/dbcontent'}),):
            p = patch.dict(target, values)
            p.start()
            self.addCleanup(p.stop)
        self.waits = []
        for p in (patch.object(core, 'TRANSFER_HOSTS', ('.0.0.1',)), patch.object(core, 'SLEEP', self.waits.append),
                  patch.object(ad.GoogleWorkspace, 'token_url', base + '/oauth/google'), patch.object(dropbox.DropboxAdapter, 'token_url', base + '/oauth/dropbox'),
                  patch.dict(ad.ENV_URLS, {'microsoft_login': {'test': base + '/oauth/ms', 'production': base + '/oauth/ms'}})):
            p.start()
            self.addCleanup(p.stop)

    def connect(self, provider, **params):
        data = {'provider': provider, 'environment': 'production', 'client_id': 'client-fictif', 'client_secret': 'secret-fictif-123456', **params}
        view = cx.save(self.desk, ADMIN, 'administrateur', data)
        cx._store_tokens(self.desk, view['id'], {'access_token': 'jeton-acces-fictif', 'refresh_token': 'renouvellement-fictif', 'expires_in': 3600}, cx._row(self.desk, view['id'])['token_version'])
        return view

    def store(self, provider, **extra):
        view = cx._row(self.desk, cx.save(self.desk, ADMIN, 'administrateur', {'provider': provider, 'environment': 'production'})['id']) if gestion.connection(self.desk, provider) else None
        cid = view['id'] if view else self.connect(provider)['id']
        return DAV({'roots': ROOTS, core.KEY: {'provider': provider, 'connection': cid, 'state_dir': str(self.desk.c['state_dir']), **extra}})


# ---------------------------------------------------------------------------------------------------- sélection centrale
class Selection(Base):
    def test_sans_choix_rien_ne_change(self):
        self.assertIsInstance(DAV({'roots': ROOTS, 'local_path': str(self.local)}), LocalFolder)
        c = {'state_dir': str(self.desk.c['state_dir']), 'nextcloud': {'roots': ROOTS}}
        core.apply_selection(c)
        self.assertNotIn(core.KEY, c['nextcloud'])

    def test_une_cle_dans_config_json_est_sans_effet(self):
        c = {'state_dir': str(self.desk.c['state_dir']), 'nextcloud': {'roots': ROOTS, core.KEY: {'provider': 'cloud_dropbox', 'connection': 'x'}}}
        self.assertNotIn(core.KEY, core.apply_selection(c)['nextcloud'])

    def test_choix_enregistre_gagne_les_deux_comptes(self):
        core.write_selection(self.desk.c['state_dir'], {'provider': 'cloud_dropbox', 'connection': 'cx-1'})
        c = {'state_dir': str(self.desk.c['state_dir']), 'nextcloud': {'roots': ROOTS}, 'nextcloud_documents': {'roots': ROOTS}}
        core.apply_selection(c)
        self.assertIsInstance(DAV(c['nextcloud']), dropbox.DropboxStore)
        self.assertIsInstance(DAV(c['nextcloud_documents']), dropbox.DropboxStore)

    def test_load_config_applique_le_choix(self):
        cfg = {k: v for k, v in self.f.c.items() if not k.startswith('_')}
        path = self.local / 'config.json'
        private_json(path, cfg)
        self.assertNotIn(core.KEY, load_config(path)['nextcloud'])
        core.write_selection(self.desk.c['state_dir'], {'provider': 'cloud_microsoft', 'connection': 'cx-1'})
        self.assertIsInstance(DAV(load_config(path)['nextcloud']), graph.MicrosoftStore)

    def test_choix_illisible_arrete_au_lieu_de_revenir_a_nextcloud(self):
        core.selection_path(self.desk.c['state_dir']).write_text('{illisible', encoding='utf-8')
        c = core.apply_selection({'state_dir': str(self.desk.c['state_dir']), 'nextcloud': {'roots': ROOTS, 'url': 'https://cloud.example.test', 'username': 'x'}})
        with self.assertRaises(Stop) as ctx:
            DAV(c['nextcloud'])
        self.assertEqual(str(ctx.exception), 'stockage_cloud_illisible')


# ---------------------------------------------------------------------------------------------------- contrat commun
class Contract:
    provider = ''

    def seed(self):
        raise NotImplementedError

    def setUp(self):
        super().setUp()
        self.seed()
        self.s = self.store(self.provider)

    def test_liste_et_lit_comme_webdav(self):
        items = self.s.list_folder('/Dossiers/DEMO')
        names = {i['path']: i for i in items}
        self.assertIn('/Dossiers/DEMO/piece.txt', names)
        self.assertFalse(names['/Dossiers/DEMO/piece.txt']['directory'])
        self.assertTrue(names['/Dossiers/DEMO/piece.txt']['etag'])
        self.assertTrue(names['/Dossiers/DEMO/piece.txt']['modified'].endswith('GMT'))
        self.assertEqual(names['/Dossiers/DEMO/piece.txt']['size'], 7)
        self.assertEqual(self.s.download(names['/Dossiers/DEMO/piece.txt']), b'contenu')
        self.assertEqual(self.s.read_archive('/Dossiers/DEMO/piece.txt', 100), b'contenu')

    def test_exclus_et_hors_racines_comme_ailleurs(self):
        paths = [i['path'] for i in self.s.list_folder('/Dossiers/DEMO')]
        self.assertFalse([p for p in paths if '/.cache' in p or 'secrets' in p.lower()])
        for bad, code in (('/Autre/x.txt', 'fichier_hors_racines'), ('/Dossiers/secrets/x.txt', 'repertoire_exclu'), ('/Dossiers/DEMO/../x.txt', 'chemin_refuse')):
            with self.assertRaises(Stop) as ctx:
                self.s.put_file(bad, b'x')
            self.assertEqual(str(ctx.exception), code)

    def test_absents_rendent_les_codes_attendus(self):
        for call, code in ((lambda: self.s.list_folder('/Dossiers/ABSENT'), 'http_404'), (lambda: self.s.stat('/Dossiers/DEMO/absent.txt'), 'fichier_nextcloud_introuvable'),
                           (lambda: self.s.read_archive('/Dossiers/DEMO/absent.txt', 100), 'http_404'), (lambda: self.s.put_file('/Dossiers/ABSENT/x.txt', b'x'), 'http_409')):
            with self.assertRaises(Stop) as ctx:
                call()
            self.assertEqual(str(ctx.exception), code)
        with self.assertRaises(Stop) as ctx:
            self.s.stat('/Dossiers/DEMO')
        self.assertEqual(str(ctx.exception), 'chemin_est_un_dossier')

    def test_creation_exclusive_rend_http_412(self):
        self.assertEqual(self.s.put_file('/Dossiers/DEMO/neuf.txt', b'un'), '/Dossiers/DEMO/neuf.txt')
        with self.assertRaises(Stop) as ctx:
            self.s.put_file('/Dossiers/DEMO/neuf.txt', b'deux')
        self.assertEqual(str(ctx.exception), 'http_412')
        self.assertEqual(self.s.read_archive('/Dossiers/DEMO/neuf.txt', 100), b'un')

    def test_taille_bornee(self):
        s = DAV({**self.s.cfg, 'max_generated_file_bytes': 3, 'max_file_bytes': 3})
        with self.assertRaises(Stop) as ctx:
            s.put_file('/Dossiers/DEMO/gros.txt', b'1234')
        self.assertEqual(str(ctx.exception), 'fichier_genere_trop_volumineux')
        with self.assertRaises(Stop) as ctx:
            s.download(self.s.stat('/Dossiers/DEMO/piece.txt'))
        self.assertEqual(str(ctx.exception), 'piece_trop_volumineuse')
        with self.assertRaises(Stop) as ctx:
            self.s.put_archive('/Dossiers/DEMO/a.txt', b'1234', 3)
        self.assertEqual(str(ctx.exception), 'archive_trop_volumineuse')
        with self.assertRaises(Stop) as ctx:
            self.s.read_archive('/Dossiers/DEMO/piece.txt', 3)
        self.assertEqual(str(ctx.exception), 'reponse_trop_volumineuse')

    def test_version_a_jour_remplace_version_perimee_refusee(self):
        info = self.s.stat('/Dossiers/DEMO/piece.txt')
        self.s.replace_file('/Dossiers/DEMO/piece.txt', b'nouveau', info['etag'])
        self.assertEqual(self.s.read_archive('/Dossiers/DEMO/piece.txt', 100), b'nouveau')
        with self.assertRaises(Stop) as ctx:
            self.s.replace_file('/Dossiers/DEMO/piece.txt', b'ecrase', info['etag'])
        self.assertEqual(str(ctx.exception), 'version_nextcloud_modifiee')
        self.assertEqual(self.s.read_archive('/Dossiers/DEMO/piece.txt', 100), b'nouveau')

    def test_le_serveur_refuse_une_marque_perimee(self):
        """Le contrôle local est passé : seule la condition envoyée au fournisseur protège. Sans elle, ce test écraserait le fichier."""
        meta = self.s._file('/Dossiers/DEMO/piece.txt')
        tag = self.s.server_tag(meta)
        self.s.replace_file('/Dossiers/DEMO/piece.txt', b'version 2', meta['etag'])
        with self.assertRaises(Stop) as ctx:
            self.s._put_tagged(meta, b'ecrase', tag)
        self.assertEqual(str(ctx.exception), 'version_nextcloud_modifiee')
        self.assertEqual(self.s.read_archive('/Dossiers/DEMO/piece.txt', 100), b'version 2')

    def test_lecture_refuse_une_version_perimee(self):
        info = self.s.stat('/Dossiers/DEMO/piece.txt')
        self.s.replace_file('/Dossiers/DEMO/piece.txt', b'nouveau', info['etag'])
        with self.assertRaises(Stop) as ctx:
            self.s.download(info)
        self.assertEqual(str(ctx.exception), 'http_412')

    def test_corbeille_seulement_sur_la_version_attendue(self):
        info = self.s.stat('/Dossiers/DEMO/piece.txt')
        with self.assertRaises(Stop):
            self.s.trash_file('/Dossiers/DEMO/piece.txt', 'version-fausse')
        self.assertTrue(self.s.stat('/Dossiers/DEMO/piece.txt'))
        self.s.trash_file('/Dossiers/DEMO/piece.txt', info['etag'])
        with self.assertRaises(Stop):
            self.s.stat('/Dossiers/DEMO/piece.txt')

    def test_dossiers(self):
        self.s.ensure_folder('/Dossiers/DEMO/a/b', '/Dossiers/DEMO')
        self.s.ensure_folder('/Dossiers/DEMO/a/b', '/Dossiers/DEMO')
        self.assertEqual(self.s.list_folder('/Dossiers/DEMO/a/b'), [])
        self.assertEqual(self.s.create_folder('/Dossiers/DEMO/c'), '/Dossiers/DEMO/c')
        with self.assertRaises(Stop) as ctx:
            self.s.create_folder('/Dossiers/DEMO/c')
        self.assertEqual(str(ctx.exception), 'http_412')
        with self.assertRaises(Stop) as ctx:
            self.s.ensure_folder('/Dossiers/AILLEURS', '/Dossiers/DEMO')
        self.assertEqual(str(ctx.exception), 'destination_hors_dossier')
        with self.assertRaises(Stop) as ctx:
            self.s.create_folder('/Dossiers')
        self.assertEqual(str(ctx.exception), 'creation_racine_refusee')

    def test_inventaires_herites(self):
        self.s.ensure_folder('/Dossiers/DEMO/sous', '/Dossiers/DEMO')
        self.s.put_file('/Dossiers/DEMO/sous/b.txt', b'b')
        self.assertEqual({'/Dossiers/DEMO/piece.txt', '/Dossiers/DEMO/sous/b.txt'}, {i['path'] for i in self.s.inventory('/Dossiers/DEMO')})
        page, _, complete = self.s.inventory_page('/Dossiers/DEMO')
        self.assertTrue(complete)
        self.assertEqual(len(page), 2)

    def test_envoi_par_blocs_gros_fichier(self):
        data = bytes(range(256)) * (BIG // 256)
        s = DAV({**self.s.cfg, 'max_generated_file_bytes': 40_000_000, 'max_file_bytes': 40_000_000})
        with patch.object(gdrive, 'SIMPLE_MAX', 100_000), patch.object(gdrive, 'CHUNK', 256 * 1024), patch.object(graph, 'SIMPLE_MAX', 100_000), patch.object(graph, 'FRAGMENT', 327_680), \
                patch.object(dropbox, 'SIMPLE_MAX', 100_000), patch.object(dropbox, 'CHUNK', 300_000):
            s.put_file('/Dossiers/DEMO/gros.bin', data)
        self.assertEqual(s.read_archive('/Dossiers/DEMO/gros.bin', 40_000_000), data)
        self.assertGreater(len([c for c in self.fake.calls if self.chunk_marker in c['path']]), 1)

    def test_limitation_de_debit_reprise_avec_retry_after(self):
        self.fake.failures.append(('', 'GET' if self.provider != 'cloud_dropbox' else 'POST', 429, {'Retry-After': '7'}, {}))
        self.assertEqual(self.s.read_archive('/Dossiers/DEMO/piece.txt', 100), b'contenu')
        self.assertEqual(self.waits, [7.0])

    def test_limitation_trop_longue_renoncement_clair(self):
        for _ in range(8):
            self.fake.failures.append(('', '', 429, {'Retry-After': '3600'}, {}))
        with self.assertRaises(Stop) as ctx:
            self.s.list_folder('/Dossiers/DEMO')
        self.assertEqual(str(ctx.exception), 'stockage_limite_debit')
        self.assertEqual(self.waits, [])

    def test_reprise_bornee(self):
        for _ in range(30):
            self.fake.failures.append(('', '', 429, {'Retry-After': '1'}, {}))
        with self.assertRaises(Stop) as ctx:
            self.s.list_folder('/Dossiers/DEMO')
        self.assertEqual(str(ctx.exception), 'stockage_limite_debit')
        self.assertLess(len(self.fake.calls), core.MAX_TRIES + 3)

    def test_jeton_refuse_renouvele_une_seule_fois(self):
        self.fake.failures.append(('', '', 401, {}, {}))
        self.assertTrue(self.s.list_folder('/Dossiers/DEMO'))
        self.assertTrue(any(c['path'].startswith('/oauth/') for c in self.fake.calls))
        self.assertEqual(self.s.tokens.get(), 'renouvele-fictif')

    def test_deconnexion_arrete_les_acces(self):
        row = gestion.connection(self.desk, self.provider)
        cx.disconnect(self.desk, ADMIN, 'administrateur', row['id'])
        with self.assertRaises(Stop) as ctx:
            self.store(self.provider).list_folder('/Dossiers/DEMO')
        self.assertEqual(str(ctx.exception), 'connexion_a_autoriser')

    def test_controle_aptitude_passe(self):
        out = core.aptitude(self.s, '/Dossiers')
        self.assertTrue(out['ok'], out)
        self.assertFalse([i for i in self.s.list_folder('/Dossiers') if 'Contrôle' in i['path']])

    def test_editeur_enregistre_et_copie_en_cas_de_conflit(self):
        """L'éditeur n'a besoin que de stat, download, replace_file et put_file : identifiant de fichier non numérique accepté, conflit rendu en copie, jamais d'écrasement."""
        office440.ensure_schema(self.desk)
        data = b'PK nouvelle version'
        self.s.put_file('/Dossiers/DEMO/acte.docx', b'PK version 1')
        info = self.s.stat('/Dossiers/DEMO/acte.docx')
        self.assertTrue(info['fileid'])
        generation = office_gen5684.open_generation(self.desk, '/Dossiers/DEMO/acte.docx', info)
        self.assertEqual(office_gen5684.document_for(self.desk, '/Dossiers/DEMO/acte.docx', info), generation['document_id'])
        now = self.desk.now()
        self.desk.db.execute('INSERT INTO office_sessions440(doc_key,path,base_etag,matter,opened,updated) VALUES(?,?,?,?,?,?)', ('cle1', '/Dossiers/DEMO/acte.docx', info['etag'], '', now, now))
        self.desk.db.commit()
        row = self.desk.db.execute("SELECT * FROM office_sessions440 WHERE doc_key='cle1'").fetchone()
        out = office440.save_edited(self.desk, row, data, 2, self.s)
        self.assertEqual(out['outcome'], 'replaced')
        self.assertEqual(self.s.read_archive('/Dossiers/DEMO/acte.docx', 100), data)
        current = self.s.stat('/Dossiers/DEMO/acte.docx')
        self.s.replace_file('/Dossiers/DEMO/acte.docx', b'PK autre personne', current['etag'])
        row = self.desk.db.execute("SELECT * FROM office_sessions440 WHERE doc_key='cle1'").fetchone()
        out = office440.save_edited(self.desk, row, b'PK ma version', 2, self.s)
        self.assertEqual(out['outcome'], 'copy')
        self.assertEqual(self.s.read_archive('/Dossiers/DEMO/acte.docx', 100), b'PK autre personne')
        self.assertEqual(self.s.read_archive(out['path'], 100), b'PK ma version')

    def test_editeur_ouvre_et_sert_le_fichier(self):
        office440.save_settings(self.desk, 'https://documents.example.test')
        self.s.put_file('/Dossiers/DEMO/acte.docx', b'PK contenu')
        auth = {'origin': 'https://axiorhub.example.test', 'prefix': '/agent-courriel'}
        out = office440.open_document(self.desk, auth, '/Dossiers/DEMO/acte.docx', client=self.s)
        self.assertTrue(out['editable'])
        token = out['config']['document']['url'].rsplit('/', 1)[1]
        data, name = office440.serve_file(self.desk, token, self.s)
        self.assertEqual((data, name), (b'PK contenu', 'acte.docx'))

    def test_ouvrir_chez_le_fournisseur_sans_creer_de_lien(self):
        before = len([c for c in self.fake.calls if c['method'] != 'GET'])
        url = self.s.file_web_url('/Dossiers/DEMO/piece.txt')
        self.assertTrue(url.startswith('https://'))
        self.assertEqual(len([c for c in self.fake.calls if c['method'] != 'GET']), before + (self.provider == 'cloud_dropbox'))     # Dropbox : lectures en POST

    def test_index_des_documents(self):
        from agent.index import DocumentIndex
        self.s.put_file('/Dossiers/DEMO/assignation.txt', b'Assignation de la societe KERVILAC devant le tribunal de Valbrune')
        (self.local / 'index').mkdir()
        index = DocumentIndex(str(self.local / 'index'))
        self.addCleanup(index.db.close)
        matter = {'id': 'DOS-001', 'path': '/Dossiers/DEMO'}
        with patch.object(DocumentIndex, 'ensure_embeddings', lambda *a, **k: None):
            index.sync_page(self.s, matter, {'max_document_chars': 100000})
            rows = dict(index.db.execute('SELECT path, text FROM docs').fetchall())
            self.assertIn('KERVILAC', rows['/Dossiers/DEMO/assignation.txt'])
            before = index.db.execute("SELECT etag FROM docs WHERE path='/Dossiers/DEMO/assignation.txt'").fetchone()[0]
            info = self.s.stat('/Dossiers/DEMO/assignation.txt')
            self.s.replace_file('/Dossiers/DEMO/assignation.txt', b'Assignation modifiee ORLANDEC', info['etag'])
            index.sync_page(self.s, matter, {'max_document_chars': 100000})
            after = index.db.execute("SELECT etag, text FROM docs WHERE path='/Dossiers/DEMO/assignation.txt'").fetchone()
        self.assertNotEqual(before, after[0])
        self.assertIn('ORLANDEC', after[1])

    def test_pseudonymisation_sur_le_texte_lu(self):
        """La pseudonymisation porte sur le texte, après lecture : elle ne dépend pas du stockage. Le document, lui, reste en clair chez le fournisseur."""
        from agent.dav import document_sources
        from agent.pseudo540 import preview
        self.s.put_file('/Dossiers/DEMO/note.txt', b'Monsieur Henri DURAND demeure a Valbrune.')
        sources, coverage = document_sources(self.s, {'path': '/Dossiers/DEMO'}, ['note'], {'max_document_chars': 100000})
        self.assertEqual(coverage['failed'], [])
        text = next(s['excerpt'] for s in sources if s['path'].endswith('note.txt'))
        self.assertIn('DURAND', text)
        out = preview(self.f.c, text)
        self.assertNotIn('DURAND', out['sent'])
        self.assertTrue(out['replacements'])
        self.assertTrue(out['restored_ok'])


class DriveContract(Contract, Base):
    provider = 'cloud_google_drive'
    chunk_marker = '/upload/session/'

    def seed(self):
        f = self.fake
        self.base_id = f.d_add('AxiorHub', 'root', gdrive.FOLDER)
        self.roots = f.d_add('Dossiers', self.base_id, gdrive.FOLDER)
        self.demo = f.d_add('DEMO', self.roots, gdrive.FOLDER)
        self.piece = f.d_add('piece.txt', self.demo, data=b'contenu')
        f.d_add('.cache', self.demo, data=b'x')
        f.d_add('Secrets', self.demo, gdrive.FOLDER)

    def test_double_creation_le_plus_ancien_gagne_et_la_copie_perdante_part_a_la_corbeille(self):
        """Drive accepte deux fois le même nom : sans relecture après création, deux fichiers coexisteraient et chacun se croirait unique."""
        self.fake.before['/upload/drive/v3/files'] = lambda: self.fake.d_add('course.txt', self.demo, data=b'arrive avant')
        with self.assertRaises(Stop) as ctx:
            self.s.put_file('/Dossiers/DEMO/course.txt', b'arrive apres')
        self.assertEqual(str(ctx.exception), 'http_412')
        alive = [f for f in self.fake.drive.values() if f['name'] == 'course.txt' and not f['trashed']]
        self.assertEqual([f['data'] for f in alive], [b'arrive avant'])

    def test_doublon_venu_d_ailleurs_signale_et_ecriture_refusee(self):
        self.fake.d_add('piece.txt', self.demo, data=b'autre')
        items = [i for i in self.s.list_folder('/Dossiers/DEMO') if i['path'].endswith('piece.txt')]
        self.assertEqual(len(items), 1)
        self.assertTrue(any('nom en double' in r for r in self.s.refused))
        info = self.s.stat('/Dossiers/DEMO/piece.txt')
        with self.assertRaises(Stop) as ctx:
            self.s.replace_file('/Dossiers/DEMO/piece.txt', b'x', info['etag'])
        self.assertEqual(str(ctx.exception), 'nom_en_double_chez_le_fournisseur')

    def test_sans_etag_v2_aucune_ecriture(self):
        self.fake.failures.append(('/drive/v2/files', 'GET', 200, {}, {'version': '10'}))
        info = self.s.stat('/Dossiers/DEMO/piece.txt')
        with self.assertRaises(Stop) as ctx:
            self.s.replace_file('/Dossiers/DEMO/piece.txt', b'x', info['etag'])
        self.assertEqual(str(ctx.exception), 'precondition_version_indisponible')
        self.assertFalse([c for c in self.fake.calls if c['method'] == 'PUT'])

    def test_document_natif_exporte_et_jamais_remplace(self):
        self.fake.d_add('Mémoire', self.demo, 'application/vnd.google-apps.document')
        item = next(i for i in self.s.list_folder('/Dossiers/DEMO') if i['path'].endswith('Mémoire.docx'))
        self.assertTrue(self.s.download(item).startswith(b'PK'))
        with self.assertRaises(Stop) as ctx:
            self.s.replace_file(item['path'], b'x', item['etag'])
        self.assertEqual(str(ctx.exception), 'format_google_natif_lecture_seule')

    def test_raccourci_signale_pas_copie(self):
        self.fake.d_add('Lien', self.demo, gdrive.SHORTCUT)
        self.assertFalse([i for i in self.s.list_folder('/Dossiers/DEMO') if 'Lien' in i['path']])
        self.assertTrue(any('Lien' in r for r in self.s.refused))

    def test_limitation_403_propre_a_google(self):
        self.fake.failures.append(('/drive/v3/files', 'GET', 403, {}, {'error': {'errors': [{'reason': 'userRateLimitExceeded'}]}}))
        self.assertTrue(self.s.list_folder('/Dossiers/DEMO'))
        self.assertEqual(len(self.waits), 1)
        self.fake.failures.append(('/drive/v3/files', 'GET', 403, {}, {'error': {'errors': [{'reason': 'insufficientPermissions'}]}}))
        with self.assertRaises(Stop) as ctx:
            DAV(self.s.cfg).list_folder('/Dossiers/DEMO')
        self.assertEqual(str(ctx.exception), 'http_403')

    def test_nom_avec_apostrophe(self):
        self.s.put_file("/Dossiers/DEMO/L'acte.txt", b'x')
        self.assertEqual(self.s.read_archive("/Dossiers/DEMO/L'acte.txt", 10), b'x')

    def test_prepare_retient_le_dossier_cree(self):
        self.fake.drive.clear()
        store = self.store(self.provider)
        extra = store.prepare()
        again = self.store(self.provider, **extra)
        self.assertEqual(again.list_folder('/Dossiers'), [])
        self.assertEqual(len([f for f in self.fake.drive.values() if f['name'] == 'AxiorHub']), 1)
        again.put_file('/Dossiers/essai.txt', b'x')
        created = next(f for f in self.fake.drive.values() if f['name'] == 'essai.txt')
        self.assertEqual(self.fake.drive[created['parents'][0]]['name'], 'Dossiers')


class GraphContract(Contract, Base):
    provider = 'cloud_microsoft'
    chunk_marker = '/transfer/up/'

    def seed(self):
        f = self.fake
        for path in ('/AxiorHub', '/AxiorHub/Dossiers', '/AxiorHub/Dossiers/DEMO', '/AxiorHub/Dossiers/DEMO/Secrets'):
            f.g_add(path)
        self.piece = f.g_add('/AxiorHub/Dossiers/DEMO/piece.txt', b'contenu')
        f.g_add('/AxiorHub/Dossiers/DEMO/.cache', b'x')

    def test_lecture_sans_jeton_vers_l_adresse_de_transfert(self):
        self.s.read_archive('/Dossiers/DEMO/piece.txt', 100)
        transfer = [c for c in self.fake.calls if c['path'].startswith('/transfer')]
        self.assertTrue(transfer)
        self.assertFalse([c for c in transfer if c['auth']])
        self.assertTrue([c for c in self.fake.calls if c['path'].startswith('/graph') and c['auth']])

    def test_envoi_sans_jeton_vers_la_session(self):
        with patch.object(graph, 'SIMPLE_MAX', 100_000), patch.object(graph, 'FRAGMENT', 327_680):
            self.s.put_file('/Dossiers/DEMO/gros.bin', b'x' * 700_000)
        up = [c for c in self.fake.calls if c['path'].startswith('/transfer/up')]
        self.assertGreater(len(up), 1)
        self.assertFalse([c for c in up if c['auth']])

    def test_adresse_de_transfert_etrangere_refusee(self):
        self.fake.foreign_upload = 'https://depot.ailleurs.example.test/up/1'
        with patch.object(graph, 'SIMPLE_MAX', 100_000):
            with self.assertRaises(Stop) as ctx:
                self.s.put_file('/Dossiers/DEMO/gros.bin', b'x' * 700_000)
        self.assertEqual(str(ctx.exception), 'hote_transfert_refuse')
        self.assertFalse(core.host_allowed('http://valbrune.sharepoint.com/x'))
        with patch.object(core, 'TRANSFER_HOSTS', ('.valbrune.example.test',)):
            self.assertTrue(core.host_allowed('https://depot.valbrune.example.test/x'))
            self.assertFalse(core.host_allowed('http://depot.valbrune.example.test/x'))
            self.assertFalse(core.host_allowed('https://depot.valbrune.example.test.ailleurs.example/x'))
            self.assertFalse(core.host_allowed('https://compte:secret@depot.valbrune.example.test/x'))

    def test_conflit_sur_le_dernier_bloc(self):
        self.fake.before['/transfer/up'] = lambda: self.fake.g_add('/AxiorHub/Dossiers/DEMO/gros.bin', b'arrive avant')
        with patch.object(graph, 'SIMPLE_MAX', 100_000), patch.object(graph, 'FRAGMENT', 10_000_000):
            with self.assertRaises(Stop) as ctx:
                self.s.put_file('/Dossiers/DEMO/gros.bin', b'x' * 200_000)
        self.assertEqual(str(ctx.exception), 'http_412')
        self.assertEqual(self.fake.graph['/AxiorHub/Dossiers/DEMO/gros.bin']['data'], b'arrive avant')

    def test_nom_refuse_avant_envoi(self):
        with self.assertRaises(Stop) as ctx:
            self.s.put_file('/Dossiers/DEMO/a:b.txt', b'x')
        self.assertEqual(str(ctx.exception), 'nom_fichier_refuse_par_le_stockage')

    def test_gros_remplacement_arrete_si_la_version_change_avant_le_dernier_bloc(self):
        s = DAV({**self.s.cfg, 'max_generated_file_bytes': 40_000_000})
        info = s.stat('/Dossiers/DEMO/piece.txt')

        def change(*a):
            self.fake.graph['/AxiorHub/Dossiers/DEMO/piece.txt']['eTag'] = '"{autre},9"'
        self.fake.before['/createUploadSession'] = lambda: None
        self.fake.before['/transfer/up'] = change
        with patch.object(graph, 'SIMPLE_MAX', 100_000), patch.object(graph, 'FRAGMENT', 327_680):
            with self.assertRaises(Stop) as ctx:
                s.replace_file('/Dossiers/DEMO/piece.txt', b'x' * 700_000, info['etag'])
        self.assertEqual(str(ctx.exception), 'version_nextcloud_modifiee')
        self.assertEqual(self.fake.graph['/AxiorHub/Dossiers/DEMO/piece.txt']['data'], b'contenu')


class DropboxContract(Contract, Base):
    provider = 'cloud_dropbox'
    chunk_marker = 'upload_session/append_v2'

    def seed(self):
        f = self.fake
        for path in ('/AxiorHub', '/AxiorHub/Dossiers', '/AxiorHub/Dossiers/DEMO', '/AxiorHub/Dossiers/DEMO/Secrets'):
            f.b_add(path)
        self.piece = f.b_add('/AxiorHub/Dossiers/DEMO/piece.txt', b'contenu')
        f.b_add('/AxiorHub/Dossiers/DEMO/.cache', b'x')

    def test_nom_accentue_passe_dans_l_entete(self):
        self.s.put_file('/Dossiers/DEMO/Résumé Lucie.txt', b'x')
        arg = next(c['arg'] for c in self.fake.calls if c['path'].endswith('files/upload'))
        self.assertTrue(arg.isascii())
        self.assertIn('/AxiorHub/Dossiers/DEMO/Résumé Lucie.txt', self.fake.box)

    def test_reprise_de_bloc_a_la_position_donnee(self):
        data = b'y' * 1_000_000
        with patch.object(dropbox, 'SIMPLE_MAX', 100_000), patch.object(dropbox, 'CHUNK', 300_000):
            real = self.fake.before

            def lost():
                sid = next(iter(self.fake.sessions))
                self.fake.sessions[sid]['data'] += data[300_000:600_000]     # bloc reçu, réponse perdue
            real['upload_session/append_v2'] = lost
            self.s.put_file('/Dossiers/DEMO/gros.bin', data)
        self.assertEqual(self.fake.box['/AxiorHub/Dossiers/DEMO/gros.bin']['data'], data)


# ---------------------------------------------------------------------------------------------------- connexion
class OAuth(Base):
    def test_lancement_pkce_portee_minimale_et_rien_de_code_en_dur(self):
        view = self.connect('cloud_google_drive')
        out = cx.oauth_start(self.desk, ADMIN, 'administrateur', view['id'], 'https://axiorhub.example.test/agent-courriel/connexions-retour')
        q = parse_qs(urlsplit(out['url']).query)
        self.assertEqual(q['scope'], ['https://www.googleapis.com/auth/drive.file'])
        self.assertEqual(q['code_challenge_method'], ['S256'])
        self.assertEqual(q['client_id'], ['client-fictif'])
        self.assertEqual(q['access_type'], ['offline'])
        self.assertTrue(q['state'][0])

    def test_portee_complete_seulement_sur_choix_explicite(self):
        view = self.connect('cloud_google_drive', acces='complet')
        out = cx.oauth_start(self.desk, ADMIN, 'administrateur', view['id'], 'https://axiorhub.example.test/agent-courriel/connexions-retour')
        self.assertEqual(parse_qs(urlsplit(out['url']).query)['scope'], ['https://www.googleapis.com/auth/drive'])

    def test_dropbox_hors_ligne_partages_seulement_si_demandes(self):
        view = self.connect('cloud_dropbox')
        q = parse_qs(urlsplit(cx.oauth_start(self.desk, ADMIN, 'administrateur', view['id'], 'https://a.example.test/connexions-retour')['url']).query)
        self.assertEqual(q['token_access_type'], ['offline'])
        self.assertNotIn('sharing', q['scope'][0])
        view = self.connect('cloud_dropbox', cloisonnement='oui')
        q = parse_qs(urlsplit(cx.oauth_start(self.desk, ADMIN, 'administrateur', view['id'], 'https://a.example.test/connexions-retour')['url']).query)
        self.assertIn('sharing.write', q['scope'][0])

    def test_microsoft_portees_selon_le_stockage(self):
        view = self.connect('cloud_microsoft', tenant='valbrune.example.test')
        out = cx.oauth_start(self.desk, ADMIN, 'administrateur', view['id'], 'https://a.example.test/connexions-retour')
        scope = parse_qs(urlsplit(out['url']).query)['scope'][0]
        self.assertIn('Files.ReadWrite', scope)
        self.assertNotIn('.All', scope)
        view = self.connect('cloud_microsoft', tenant='valbrune.example.test', site='valbrune.sharepoint.com:/sites/cabinet')
        scope = parse_qs(urlsplit(cx.oauth_start(self.desk, ADMIN, 'administrateur', view['id'], 'https://a.example.test/connexions-retour')['url']).query)['scope'][0]
        self.assertIn('Files.ReadWrite.All', scope)

    def test_jetons_au_coffre_jamais_en_clair(self):
        view = self.connect('cloud_dropbox')
        out = cx.oauth_start(self.desk, ADMIN, 'administrateur', view['id'], 'https://a.example.test/connexions-retour')
        query = parse_qs(urlsplit(out['url']).query)
        result = cx.oauth_finish(self.desk, ADMIN, {'state': query['state'][0], 'code': 'code-fictif'})
        self.assertTrue(result['ok'], result)
        verifier = next(c for c in self.fake.calls if c['path'].startswith('/oauth/'))['form']['code_verifier'][0]
        self.assertEqual(base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('='), query['code_challenge'][0])
        with self.assertRaises(Stop):
            cx.oauth_finish(self.desk, ADMIN, {'state': query['state'][0], 'code': 'code-fictif'})      # un état ne sert qu'une fois
        vault = Path(self.desk.c['state_dir']) / 'vault' / 'connexions5652' / view['id']
        for name in ('access.secret', 'refresh.secret'):
            self.assertTrue((vault / name).read_text(encoding='utf-8').startswith('AXIORHUB-VAULT-V1:'))
        self.assertNotIn('renouvele-fictif', json.dumps(cx.public_view(self.desk, cx._row(self.desk, view['id']))))

    def test_renouvellement_a_l_expiration(self):
        view = self.connect('cloud_dropbox')
        self.desk.db.execute("UPDATE connexions5652 SET token_expires='2000-01-01' WHERE id=?", (view['id'],))
        self.desk.db.commit()
        self.fake.b_add('/AxiorHub')
        self.fake.b_add('/AxiorHub/Dossiers')
        DAV({'roots': ROOTS, core.KEY: {'provider': 'cloud_dropbox', 'connection': view['id'], 'state_dir': str(self.desk.c['state_dir'])}}).list_folder('/Dossiers')
        call = next(c for c in self.fake.calls if c['path'].startswith('/oauth/'))
        self.assertEqual(call['form']['grant_type'], ['refresh_token'])
        self.assertEqual(call['form']['refresh_token'], ['renouvellement-fictif'])

    def test_revocation_chez_le_fournisseur_et_effacement(self):
        for provider, marker in (('cloud_google_drive', '/oauth/revoke'), ('cloud_dropbox', 'auth/token/revoke')):
            view = self.connect(provider)
            out = cx.disconnect(self.desk, ADMIN, 'administrateur', view['id'])
            self.assertEqual(out['revocation'], 'revoquee')
            self.assertTrue([c for c in self.fake.calls if marker in c['path']])
            self.assertFalse((Path(self.desk.c['state_dir']) / 'vault' / 'connexions5652' / view['id'] / 'access.secret').exists())

    def test_microsoft_dit_que_la_revocation_reste_locale(self):
        view = self.connect('cloud_microsoft')
        self.assertEqual(cx.disconnect(self.desk, ADMIN, 'administrateur', view['id'])['revocation'], 'locale_seulement')

    def test_revocation_en_echec_efface_quand_meme(self):
        view = self.connect('cloud_dropbox')
        self.fake.failures.append(('auth/token/revoke', '', 500, {}, {}))
        out = cx.disconnect(self.desk, ADMIN, 'administrateur', view['id'])
        self.assertEqual(out['revocation'], 'echec')
        self.assertFalse((Path(self.desk.c['state_dir']) / 'vault' / 'connexions5652' / view['id'] / 'access.secret').exists())

    def test_test_de_lecture_identifie_le_compte(self):
        for provider in core.PROVIDERS:
            view = self.connect(provider)
            out = cx.test(self.desk, ADMIN, 'administrateur', view['id'])
            self.assertTrue(out['ok'], out)
            self.assertEqual(out['identite']['compte'], 'cabinet@example.test')

    def test_stockages_hors_des_outils_de_l_agent(self):
        self.assertFalse([p for p in cx.PROVIDERS if p.startswith('cloud_')])
        view = self.connect('cloud_dropbox')
        cx.test(self.desk, ADMIN, 'administrateur', view['id'])
        self.assertFalse([t for t in cx.available_tools(self.desk, ADMIN) if t['fournisseur'].startswith('cloud_')])

    def test_connexion_personnelle_jamais_utilisee_comme_stockage(self):
        self.assertIsNone(gestion.connection(self.desk, 'cloud_dropbox'))
        cx.save(self.desk, 'avocat@example.test', 'avocat', {'provider': 'cloud_dropbox', 'portee': 'personnel', 'client_id': 'perso'})
        self.assertIsNone(gestion.connection(self.desk, 'cloud_dropbox'))


# ---------------------------------------------------------------------------------------------------- bascule
class Activation(Base):
    def ready(self, provider='cloud_dropbox'):
        view = self.connect(provider)
        cx.test(self.desk, ADMIN, 'administrateur', view['id'])
        gestion.prepare(self.desk, 'administrateur', provider)
        return view

    def test_preparation_et_controle_puis_choix(self):
        self.ready()
        self.assertEqual(gestion.check(self.desk, 'administrateur', 'cloud_dropbox')['ok'], True)
        gestion.activate(self.desk, ADMIN, 'administrateur', 'cloud_dropbox', without_copy=True)
        self.assertEqual(gestion.active(self.desk), 'cloud_dropbox')
        self.assertEqual(core.read_selection(self.desk.c['state_dir'])['choisi_par'], ADMIN)
        self.assertFalse([k for k in json.loads(core.selection_path(self.desk.c['state_dir']).read_text(encoding='utf-8')) if 'secret' in k or 'token' in k])

    def test_sans_controle_reussi_pas_de_bascule(self):
        self.ready()
        with self.assertRaises(Stop) as ctx:
            gestion.activate(self.desk, ADMIN, 'administrateur', 'cloud_dropbox', without_copy=True)
        self.assertEqual(str(ctx.exception), 'controle_aptitude_requis')

    def test_controle_echoue_bloque_la_bascule(self):
        self.ready()
        self.fake.failures.append(('files/upload', '', 409, {}, {'error_summary': 'path/conflict/file/..'}))
        out = gestion.check(self.desk, 'administrateur', 'cloud_dropbox')
        self.assertFalse(out['ok'])
        with self.assertRaises(Stop):
            gestion.activate(self.desk, ADMIN, 'administrateur', 'cloud_dropbox', without_copy=True)

    def test_compte_change_controle_a_refaire(self):
        view = self.ready()
        gestion.check(self.desk, 'administrateur', 'cloud_dropbox')
        self.assertTrue(gestion.aptitude_valid(self.desk, 'cloud_dropbox'))
        cx.save(self.desk, ADMIN, 'administrateur', {'id': view['id'], 'provider': 'cloud_dropbox', 'client_id': 'autre-application'})
        self.assertFalse(gestion.aptitude_valid(self.desk, 'cloud_dropbox'))

    def test_sans_copie_terminee_accord_explicite_exige(self):
        self.ready()
        gestion.check(self.desk, 'administrateur', 'cloud_dropbox')
        with self.assertRaises(Stop) as ctx:
            gestion.activate(self.desk, ADMIN, 'administrateur', 'cloud_dropbox')
        self.assertEqual(str(ctx.exception), 'copie_terminee_requise')

    def test_seul_un_administrateur_bascule(self):
        self.ready()
        gestion.check(self.desk, 'administrateur', 'cloud_dropbox')
        with self.assertRaises(Stop) as ctx:
            gestion.activate(self.desk, 'avocat@example.test', 'avocat', 'cloud_dropbox', without_copy=True)
        self.assertEqual(str(ctx.exception), 'role_insuffisant')
        self.assertEqual(gestion.active(self.desk), 'nextcloud')

    def test_choix_sans_copie_coche_dans_la_page(self):
        from agent import web_cloud5688 as web
        self.ready()
        gestion.check(self.desk, 'administrateur', 'cloud_dropbox')
        self.assertEqual(web._route(self.desk, ADMIN, 'administrateur', 'choisir', {'provider': 'cloud_dropbox', 'sans_copie': True})['message'][:15], 'Stockage choisi')

    def test_retour_ne_deplace_rien(self):
        self.ready()
        gestion.check(self.desk, 'administrateur', 'cloud_dropbox')
        gestion.activate(self.desk, ADMIN, 'administrateur', 'cloud_dropbox', without_copy=True)
        gestion.deactivate(self.desk, ADMIN, 'administrateur')
        self.assertEqual(gestion.active(self.desk), 'nextcloud')
        self.assertFalse(core.selection_path(self.desk.c['state_dir']).exists())


# ---------------------------------------------------------------------------------------------------- copie
class Migration(Base):
    def setUp(self):
        super().setUp()
        (self.local / 'Dossiers/DEMO/sous').mkdir()
        (self.local / 'Dossiers/DEMO/a.txt').write_bytes(b'alpha')
        (self.local / 'Dossiers/DEMO/sous/b.txt').write_bytes(b'beta')
        self.connect('cloud_microsoft')
        gestion.prepare(self.desk, 'administrateur', 'cloud_microsoft')

    def run_all(self, ident):
        for _ in range(30):
            out = migration.step(self.desk, ident)
            if out['state'] != 'en_cours':
                return out
        self.fail('copie sans fin')

    def begin(self):
        with patch.object(self.desk, 'enqueue', lambda *a, **k: None):
            return migration.start(self.desk, ADMIN, 'administrateur', 'nextcloud', 'cloud_microsoft')['id']

    def snapshot(self):
        return {p.relative_to(self.local).as_posix(): p.read_bytes() for p in self.local.rglob('*') if p.is_file()}

    def test_copie_controlee_par_empreinte_source_intacte_sans_bascule(self):
        before = self.snapshot()
        ident = self.begin()
        self.assertEqual(self.run_all(ident)['state'], 'terminee')
        self.assertEqual(self.fake.graph['/AxiorHub/Dossiers/DEMO/a.txt']['data'], b'alpha')
        self.assertEqual(self.fake.graph['/AxiorHub/Dossiers/DEMO/sous/b.txt']['data'], b'beta')
        self.assertEqual(migration.report(self.desk, ident)['bilan'], {'copie': 2})
        rows = self.desk.db.execute("SELECT sha256 FROM cloud_migration_items5688 WHERE path='/Dossiers/DEMO/a.txt'").fetchall()
        self.assertEqual(rows[0][0], hashlib.sha256(b'alpha').hexdigest())
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(gestion.active(self.desk), 'nextcloud')
        self.assertTrue(migration.finished_towards(self.desk, 'cloud_microsoft'))

    def test_la_source_ne_peut_pas_etre_modifiee(self):
        guard = migration.SourceOnly(DAV({'roots': ROOTS, 'local_path': str(self.local)}))
        for name in ('put_file', 'replace_file', 'trash_file', 'put_archive', 'create_folder', 'ensure_folder', 'nouvelle_methode'):
            with self.assertRaises(Stop) as ctx:
                getattr(guard, name)
            self.assertEqual(str(ctx.exception), 'source_en_lecture_seule')

    def test_la_copie_ne_recoit_que_la_source_gardee(self):
        ident = self.begin()
        refused = []

        def probe(desk, row, item, source, target, limit):
            with self.assertRaises(Stop) as ctx:
                source.trash_file(item['path'], item['etag'])
            refused.append(str(ctx.exception))
        with patch.object(migration, '_copy', probe):
            migration.step(self.desk, ident)
        self.assertEqual(refused, ['source_en_lecture_seule'] * 2)
        self.assertTrue((self.local / 'Dossiers/DEMO/a.txt').exists())

    def test_empreinte_differente_signalee_jamais_tenue_pour_bonne(self):
        self.fake.corrupt = True
        ident = self.begin()
        self.run_all(ident)
        self.assertEqual(migration.report(self.desk, ident)['bilan'], {'empreinte_differente': 2})
        self.assertFalse(migration.finished_towards(self.desk, 'cloud_microsoft'))

    def test_fichier_present_jamais_remplace(self):
        self.fake.g_add('/AxiorHub/Dossiers/DEMO')
        self.fake.g_add('/AxiorHub/Dossiers/DEMO/a.txt', b'autre contenu')
        self.fake.g_add('/AxiorHub/Dossiers/DEMO/sous')
        self.fake.g_add('/AxiorHub/Dossiers/DEMO/sous/b.txt', b'beta')
        ident = self.begin()
        self.run_all(ident)
        self.assertEqual(migration.report(self.desk, ident)['bilan'], {'conflit': 1, 'identique': 1})
        self.assertEqual(self.fake.graph['/AxiorHub/Dossiers/DEMO/a.txt']['data'], b'autre contenu')

    def test_reprise_apres_panne_sans_refaire_ce_qui_est_fait(self):
        ident = self.begin()
        real = migration._copy
        seen = []

        def flaky(desk, row, item, source, target, limit):
            seen.append(item['path'])
            if len(seen) == 2:
                raise Stop('stockage_limite_debit')
            return real(desk, row, item, source, target, limit)
        with patch.object(migration, '_copy', flaky):
            out = migration.step(self.desk, ident)
        self.assertEqual(out['state'], 'en_pause')
        self.assertEqual(migration.report(self.desk, ident)['erreur'], 'stockage_limite_debit')
        with patch.object(self.desk, 'enqueue', lambda *a, **k: None):
            migration.resume(self.desk, ADMIN, 'administrateur', ident)
        copied = []
        with patch.object(migration, '_copy', lambda desk, row, item, source, target, limit: (copied.append(item['path']), real(desk, row, item, source, target, limit))[1]):
            self.assertEqual(self.run_all(ident)['state'], 'terminee')
        self.assertEqual(copied, [seen[1]])
        self.assertEqual(migration.report(self.desk, ident)['bilan'], {'copie': 2})

    def test_gros_fichier_liste_a_part(self):
        self.desk.setting('cloud5688:migration_max', 4)
        ident = self.begin()
        self.run_all(ident)
        self.assertEqual(migration.report(self.desk, ident)['bilan'], {'trop_volumineux': 1, 'copie': 1})

    def test_fichier_vide_signale(self):
        (self.local / 'Dossiers/DEMO/vide.txt').write_bytes(b'')
        ident = self.begin()
        self.run_all(ident)
        self.assertEqual(migration.report(self.desk, ident)['bilan'].get('fichier_vide'), 1)

    def test_fichier_modifie_a_la_source_pendant_la_copie(self):
        ident = self.begin()
        migration.step(self.desk, ident, budget=0)
        (self.local / 'Dossiers/DEMO/a.txt').write_bytes(b'alpha modifie')
        self.run_all(ident)
        self.assertEqual(migration.report(self.desk, ident)['bilan'].get('source_modifiee'), 1)
        self.assertFalse(migration.finished_towards(self.desk, 'cloud_microsoft'))

    def test_deux_executions_simultanees_impossibles(self):
        ident = self.begin()
        self.desk.db.execute('UPDATE cloud_migrations5688 SET lease=? WHERE id=?', (9999999999.0, ident))
        self.desk.db.commit()
        self.assertEqual(migration.step(self.desk, ident)['state'], 'occupee')

    def test_retention_des_copies_terminees(self):
        with patch.object(migration, 'KEEP', 1), patch.object(self.desk, 'enqueue', lambda *a, **k: None):
            for _ in range(3):
                self.run_all(migration.start(self.desk, ADMIN, 'administrateur', 'nextcloud', 'cloud_microsoft')['id'])
            self.run_all(migration.start(self.desk, ADMIN, 'administrateur', 'nextcloud', 'cloud_microsoft')['id'])
        self.assertLessEqual(self.desk.db.execute('SELECT COUNT(*) FROM cloud_migrations5688').fetchone()[0], 2)
        self.assertTrue(migration.finished_towards(self.desk, 'cloud_microsoft'))

    def test_seconde_copie_ne_refait_rien_et_ne_remplace_rien(self):
        first = self.begin()
        self.run_all(first)
        stamp = self.fake.graph['/AxiorHub/Dossiers/DEMO/a.txt']['eTag']
        second = self.begin()
        self.run_all(second)
        self.assertEqual(migration.report(self.desk, second)['bilan'], {'identique': 2})
        self.assertEqual(self.fake.graph['/AxiorHub/Dossiers/DEMO/a.txt']['eTag'], stamp)

    def test_memes_stockages_refuses_et_administrateur_requis(self):
        with self.assertRaises(Stop):
            migration.start(self.desk, ADMIN, 'administrateur', 'nextcloud', 'nextcloud')
        with self.assertRaises(Stop) as ctx:
            migration.start(self.desk, 'avocat@example.test', 'avocat', 'nextcloud', 'cloud_microsoft')
        self.assertEqual(str(ctx.exception), 'role_insuffisant')

    def test_travail_de_fond_enchaine_les_lots(self):
        ident = self.begin()
        (self.local / 'Dossiers/DEMO/c.txt').write_bytes(b'gamma')
        queued = []
        with patch.object(self.desk, 'enqueue', lambda kind, args, **k: queued.append((kind, args))), patch.object(migration, 'BUDGET', 1), patch.object(migration.step, '__defaults__', (1, None)):
            self.assertEqual(migration.job(self.desk, {'migration': ident})['state'], 'en_cours')
            self.assertEqual(queued, [('migration5688', {'migration': ident})])
            for _ in range(10):
                if migration.job(self.desk, {'migration': ident})['state'] == 'terminee':
                    break
        self.assertEqual(len(queued), 2)                # trois fichiers, un par lot : le dernier lot termine, plus de remise en file
        self.assertEqual(migration.report(self.desk, ident)['etat'], 'terminee')


# ---------------------------------------------------------------------------------------------------- nommage
class Naming(Base):
    def test_sans_regle_le_nom_reste(self):
        self.assertEqual(nommage.apply(self.desk, 'courrier', 'Courrier_DOS-001.docx', {}), 'Courrier_DOS-001.docx')
        self.assertEqual(nommage.folder_for(self.desk, 'courrier', '/Dossiers/DEMO', '/Dossiers/DEMO/x'), '/Dossiers/DEMO/x')

    def test_regle_appliquee_extension_gardee(self):
        nommage.save(self.desk, ADMIN, 'administrateur', 'courrier', {'modele': '{date} - {nature} - {dossier} - {titre}', 'sous_dossier': 'Courriers/{annee}'})
        context = nommage.context_of({'id': 'DOS-001', 'client_name': 'DURAND'}, date='2026-11-01', titre='Relance : ZORVAL / KERVILAC')
        self.assertEqual(nommage.apply(self.desk, 'courrier', 'x.docx', context), '2026-11-01 - Courrier - DOS-001 - Relance _ ZORVAL _ KERVILAC.docx')
        self.assertEqual(nommage.folder_for(self.desk, 'courrier', '/Dossiers/DEMO', '/Dossiers/DEMO/x', context), '/Dossiers/DEMO/Courriers/2026')

    def test_champ_vide_ne_laisse_pas_de_separateur(self):
        nommage.save(self.desk, ADMIN, 'administrateur', 'note', {'modele': '{date} - {rg} - {titre}'})
        self.assertEqual(nommage.apply(self.desk, 'note', 'a.docx', {'date': '2026-11-01', 'titre': 'Note'}), '2026-11-01 - Note.docx')

    def test_regles_dangereuses_refusees(self):
        for rule in ({'modele': '../{titre}'}, {'modele': 'a/b'}, {'modele': '{inconnu}'}, {'sous_dossier': '../autre'}, {'sous_dossier': '.cache'}, {'sous_dossier': 'a\\b'}, {'sous_dossier': 'a//b'}):
            with self.assertRaises(Stop):
                nommage.check_rule(rule)

    def test_valeur_du_dossier_ne_sort_pas_du_dossier(self):
        nommage.save(self.desk, ADMIN, 'administrateur', 'note', {'sous_dossier': '{titre}'})
        self.assertEqual(nommage.folder_for(self.desk, 'note', '/Dossiers/DEMO', '/Dossiers/DEMO', {'titre': '../../Autre'}), '/Dossiers/DEMO/_.._Autre')

    def test_jamais_d_ecrasement_dans_le_stockage_choisi(self):
        self.fake.b_add('/AxiorHub')
        self.fake.b_add('/AxiorHub/Dossiers')
        self.fake.b_add('/AxiorHub/Dossiers/DEMO')
        self.fake.b_add('/AxiorHub/Dossiers/DEMO/acte.docx', b'premier')
        target = nommage.put_unique(self.store('cloud_dropbox'), '/Dossiers/DEMO', 'acte.docx', b'second')
        self.assertEqual(target, '/Dossiers/DEMO/acte (2).docx')
        self.assertEqual(self.fake.box['/AxiorHub/Dossiers/DEMO/acte.docx']['data'], b'premier')

    def test_seul_un_administrateur_regle(self):
        with self.assertRaises(Stop) as ctx:
            nommage.save(self.desk, 'avocat@example.test', 'avocat', 'note', {'modele': '{titre}'})
        self.assertEqual(str(ctx.exception), 'role_insuffisant')

    def test_note_rangee_selon_la_regle(self):
        from agent import notes5666
        nommage.save(self.desk, ADMIN, 'administrateur', 'note', {'modele': '{dossier} - {titre}', 'sous_dossier': 'Notes'})
        out = notes5666.deposit(self.desk, DAV({'roots': ROOTS, 'local_path': str(self.local)}), {'id': 'DOS-001', 'path': '/Dossiers/DEMO'}, 'Point audience', ['ligne'])
        self.assertEqual(out['chemin'], '/Dossiers/DEMO/Notes/DOS-001 - Point audience.docx')

    def test_piece_jointe_rangee_selon_la_regle(self):
        from email.message import EmailMessage
        from types import SimpleNamespace
        from agent import ged5636
        msg = EmailMessage()
        msg['From'] = 'client@example.test'
        msg.set_content('corps')
        msg.add_attachment(b'%PDF-1.4 fictif', maintype='application', subtype='pdf', filename='contrat.pdf')

        class Box:
            def fetch(self, folder, uid):
                return SimpleNamespace(msg=msg, timestamp=None)
        client = DAV({'roots': ROOTS, 'local_path': str(self.local)})
        (self.local / 'Dossiers/DEMO/Pieces').mkdir()
        (self.local / 'Dossiers/DEMO/Pieces/DOS-001 - contrat.pdf').write_bytes(b'deja la')
        nommage.save(self.desk, ADMIN, 'administrateur', 'piece_jointe', {'modele': '{dossier} - {titre}', 'sous_dossier': 'Pieces'})
        out = ged5636.file_attachments(self.desk, '', 'DOS-001', dav=client, mailbox=Box(), uid='12', mail_folder='INBOX')
        self.assertEqual(out['filed'], ['/Dossiers/DEMO/Pieces/DOS-001 - contrat (2).pdf'])
        self.assertEqual((self.local / 'Dossiers/DEMO/Pieces/DOS-001 - contrat.pdf').read_bytes(), b'deja la')

    def test_note_sans_regle_inchangee(self):
        from agent import notes5666
        out = notes5666.deposit(self.desk, DAV({'roots': ROOTS, 'local_path': str(self.local)}), {'id': 'DOS-001', 'path': '/Dossiers/DEMO'}, 'Point audience', ['ligne'])
        self.assertRegex(out['chemin'], r'^/Dossiers/DEMO/\d{4}-\d\d-\d\d - Note interne - Point audience\.docx$')


# ---------------------------------------------------------------------------------------------------- cloisonnement
AVOCATE, ASSISTANT = 'avocate@example.test', 'assistant@example.test'


def graph_perm(ident, email, roles=('write',), inherited=False, link=None):
    row = {'id': ident, 'roles': list(roles)}
    if email:
        row['grantedToV2'] = {'user': {'email': email, 'displayName': email}}
    if inherited:
        row['inheritedFrom'] = {'id': 'parent', 'path': '/AxiorHub/Dossiers'}
    if link:
        row['link'] = {'scope': link, 'type': 'view'}
    return row


class Partition(Base):
    def setUp(self):
        super().setUp()
        view = self.connect('cloud_microsoft')
        self.folder = None
        for path in ('/AxiorHub', '/AxiorHub/Dossiers', '/AxiorHub/Dossiers/DEMO'):
            self.folder = self.fake.g_add(path)
        self.parent = self.fake.graph['/AxiorHub/Dossiers']
        self.doc = self.fake.g_add('/AxiorHub/Dossiers/DEMO/piece.txt', b'x')
        core.write_selection(self.desk.c['state_dir'], {'provider': 'cloud_microsoft', 'connection': view['id']})
        self.desk.db.execute(membres5650.SCHEMA)
        for owner, role in ((AVOCATE, 'avocat'), (ASSISTANT, 'assistant'), (ADMIN, 'administrateur')):
            self.desk.db.execute('INSERT INTO membres5650(owner,name,role,first_seen,last_seen) VALUES(?,?,?,?,?)', (owner, owner, role, 'x', 'x'))
        self.desk.db.commit()
        membres5650.set_reserve(self.desk.c['state_dir'], 'DOS-001', [AVOCATE])
        for owner, identity in ((AVOCATE, 'avocate@valbrune.example.test'), (ASSISTANT, 'assistant@valbrune.example.test')):
            cloison.set_identity(self.desk, ADMIN, 'administrateur', 'cloud_microsoft', owner, identity)
        self.fake.perms[self.folder['id']] = [
            graph_perm('p-owner', 'proprietaire@valbrune.example.test', ('owner',)), graph_perm('p-ok', 'avocate@valbrune.example.test'), graph_perm('p-non', 'assistant@valbrune.example.test'),
            graph_perm('p-herite', 'assistant@valbrune.example.test', inherited=True), graph_perm('p-ext', 'expert@ailleurs.example.test'), graph_perm('p-lien', '', link='anonymous')]
        self.fake.perms[self.doc['id']] = [graph_perm('p-doc', 'assistant@valbrune.example.test')]

    def actions(self, plan):
        return {e['perm']['id']: e['action'] for e in plan['entrees']}

    def test_apercu_classe_sans_rien_modifier(self):
        out = cloison.preview(self.desk, ADMIN, 'administrateur', 'DOS-001')
        self.assertEqual(self.actions(out['plan']), {'p-owner': 'conserver', 'p-ok': 'conserver', 'p-non': 'retirer', 'p-herite': 'herite', 'p-ext': 'a_verifier', 'p-lien': 'retirer', 'p-doc': 'retirer'})
        self.assertFalse([c for c in self.fake.calls if c['method'] != 'GET'])

    def test_explication_de_l_acces_herite(self):
        out = cloison.preview(self.desk, ADMIN, 'administrateur', 'DOS-001')
        why = next(e['raison'] for e in out['plan']['entrees'] if e['action'] == 'herite')
        self.assertIn('dossier parent', why)
        self.assertIn('ne le retire pas', why)

    def test_confirmation_retire_seulement_ce_qui_est_prevu_et_laisse_une_trace(self):
        out = cloison.preview(self.desk, ADMIN, 'administrateur', 'DOS-001')
        done = cloison.confirm(self.desk, ADMIN, 'administrateur', out['id'], out['hash'])
        self.assertEqual(done['state'], 'applique', done)
        self.assertEqual({p['id'] for p in self.fake.perms[self.folder['id']]}, {'p-owner', 'p-ok', 'p-herite', 'p-ext'})
        self.assertEqual(self.fake.perms[self.doc['id']], [])
        deletes = [c['path'] for c in self.fake.calls if c['method'] == 'DELETE']
        self.assertEqual(len(deletes), 3)
        self.assertFalse([d for d in deletes if self.parent['id'] in d])
        trace = self.desk.db.execute("SELECT * FROM audit WHERE action='cloud5688_cloison_appliquee'").fetchall()
        self.assertEqual(len(trace), 1)
        self.assertIn('assistant@valbrune.example.test', trace[0][3])

    def test_jamais_d_elargissement(self):
        for cls in (gdrive.GoogleDriveStore, graph.MicrosoftStore, dropbox.DropboxStore):
            for name in dir(cls):
                self.assertFalse(re.search(r'(add|grant|create|invite|share)_?.*perm|share_link|invite', name), name)
        out = cloison.preview(self.desk, ADMIN, 'administrateur', 'DOS-001')
        self.fake.perms[self.folder['id']] = [p for p in self.fake.perms[self.folder['id']] if p['id'] != 'p-ok']       # compte autorisé sans accès : rien n'est ajouté
        with self.assertRaises(Stop):
            cloison.confirm(self.desk, ADMIN, 'administrateur', out['id'], out['hash'])
        out = cloison.preview(self.desk, ADMIN, 'administrateur', 'DOS-001')
        cloison.confirm(self.desk, ADMIN, 'administrateur', out['id'], out['hash'])
        self.assertFalse([c for c in self.fake.calls if c['method'] == 'POST' and ('permissions' in c['path'] or 'invite' in c['path'] or 'createLink' in c['path'])])
        self.assertNotIn('p-ok', {p['id'] for p in self.fake.perms[self.folder['id']]})

    def test_lever_la_reservation_ne_rend_rien(self):
        out = cloison.preview(self.desk, ADMIN, 'administrateur', 'DOS-001')
        cloison.confirm(self.desk, ADMIN, 'administrateur', out['id'], out['hash'])
        membres5650.set_reserve(self.desk.c['state_dir'], 'DOS-001', [])
        with self.assertRaises(Stop) as ctx:
            cloison.preview(self.desk, ADMIN, 'administrateur', 'DOS-001')
        self.assertEqual(str(ctx.exception), 'dossier_non_reserve')

    def test_aucun_retrait_sur_un_dossier_parent_commun(self):
        registry = json.loads(Path(self.desk.c['matters_file']).read_text(encoding='utf-8'))
        registry.append({'id': 'DOS-002', 'client_name': 'BERNARD', 'path': '/Dossiers/DEMO/SOUS-DOSSIER', 'references': ['DOS-002'], 'aliases': [], 'correspondents': []})
        private_json(self.desk.c['matters_file'], registry)
        out = cloison.preview(self.desk, ADMIN, 'administrateur', 'DOS-001')
        self.assertEqual(self.actions(out['plan'])['p-non'], 'parent_commun')
        self.assertEqual(self.actions(out['plan'])['p-lien'], 'parent_commun')
        self.assertEqual(self.actions(out['plan'])['p-doc'], 'retirer')

    def test_le_compte_relie_n_est_jamais_retire(self):
        cloison.set_identity(self.desk, ADMIN, 'administrateur', 'cloud_microsoft', ASSISTANT, 'cabinet@example.test')
        self.fake.perms[self.folder['id']].append(graph_perm('p-soi', 'cabinet@example.test'))
        out = cloison.preview(self.desk, ADMIN, 'administrateur', 'DOS-001')
        self.assertEqual(self.actions(out['plan'])['p-soi'], 'conserver')

    def test_seul_un_administrateur_voit_ou_confirme(self):
        out = cloison.preview(self.desk, ADMIN, 'administrateur', 'DOS-001')
        for call in (lambda: cloison.preview(self.desk, AVOCATE, 'avocat', 'DOS-001'), lambda: cloison.confirm(self.desk, AVOCATE, 'avocat', out['id'], out['hash']),
                     lambda: cloison.set_identity(self.desk, AVOCATE, 'avocat', 'cloud_microsoft', AVOCATE, 'a@example.test')):
            with self.assertRaises(Stop) as ctx:
                call()
            self.assertEqual(str(ctx.exception), 'role_insuffisant')
        self.assertFalse([c for c in self.fake.calls if c['method'] == 'DELETE'])

    def test_apercu_perime_rien_n_est_retire(self):
        out = cloison.preview(self.desk, ADMIN, 'administrateur', 'DOS-001')
        self.fake.perms[self.folder['id']].append(graph_perm('p-nouveau', 'assistant@valbrune.example.test'))
        with self.assertRaises(Stop) as ctx:
            cloison.confirm(self.desk, ADMIN, 'administrateur', out['id'], out['hash'])
        self.assertEqual(str(ctx.exception), 'apercu_perime')
        self.assertFalse([c for c in self.fake.calls if c['method'] == 'DELETE'])

    def test_empreinte_d_apercu_obligatoire(self):
        out = cloison.preview(self.desk, ADMIN, 'administrateur', 'DOS-001')
        with self.assertRaises(Stop) as ctx:
            cloison.confirm(self.desk, ADMIN, 'administrateur', out['id'], 'autre')
        self.assertEqual(str(ctx.exception), 'apercu_perime')
        self.assertFalse([c for c in self.fake.calls if c['method'] == 'DELETE'])

    def test_apercu_trop_ancien(self):
        out = cloison.preview(self.desk, ADMIN, 'administrateur', 'DOS-001')
        self.desk.db.execute("UPDATE cloud_cloisons5688 SET created='2020-01-01T00:00:00+00:00' WHERE id=?", (out['id'],))
        self.desk.db.commit()
        with self.assertRaises(Stop) as ctx:
            cloison.confirm(self.desk, ADMIN, 'administrateur', out['id'], out['hash'])
        self.assertEqual(str(ctx.exception), 'apercu_perime')

    def test_un_aperçu_ne_sert_qu_une_fois(self):
        out = cloison.preview(self.desk, ADMIN, 'administrateur', 'DOS-001')
        cloison.confirm(self.desk, ADMIN, 'administrateur', out['id'], out['hash'])
        with self.assertRaises(Stop) as ctx:
            cloison.confirm(self.desk, ADMIN, 'administrateur', out['id'], out['hash'])
        self.assertEqual(str(ctx.exception), 'apercu_deja_traite')

    def test_retrait_refuse_visible_et_partiel(self):
        out = cloison.preview(self.desk, ADMIN, 'administrateur', 'DOS-001')
        self.fake.failures.append(('/permissions/p-non', 'DELETE', 403, {}, {}))
        done = cloison.confirm(self.desk, ADMIN, 'administrateur', out['id'], out['hash'])
        self.assertEqual(done['state'], 'partiel')
        self.assertEqual(len(done['echecs']), 1)
        self.assertEqual(len(done['retires']), 2)
        self.assertEqual(done['encore_presents'][0]['identite'], 'assistant@valbrune.example.test')

    def test_correspondance_controlee(self):
        with self.assertRaises(Stop) as ctx:
            cloison.set_identity(self.desk, ADMIN, 'administrateur', 'cloud_microsoft', ADMIN, 'avocate@valbrune.example.test')
        self.assertEqual(str(ctx.exception), 'identite_cloud_deja_utilisee')
        with self.assertRaises(Stop):
            cloison.set_identity(self.desk, ADMIN, 'administrateur', 'cloud_microsoft', ADMIN, 'pas une adresse')

    def test_membre_sans_correspondance_rien_de_retire_et_c_est_dit(self):
        cloison.set_identity(self.desk, ADMIN, 'administrateur', 'cloud_microsoft', ASSISTANT, '')
        membres5650.set_reserve(self.desk.c['state_dir'], 'DOS-001', [AVOCATE, 'absent@example.test'])
        out = cloison.preview(self.desk, ADMIN, 'administrateur', 'DOS-001')
        self.assertEqual(self.actions(out['plan'])['p-non'], 'a_verifier')
        self.assertEqual(out['plan']['sans_correspondance'], ['absent@example.test'])

    def test_compte_personnel_l_interface_le_dit(self):
        self.fake.drive_type = 'personal'
        self.assertFalse(cloison.support(self.desk)['possible'])
        self.assertIn('personnel', cloison.support(self.desk)['raison'])
        with self.assertRaises(Stop) as ctx:
            cloison.preview(self.desk, ADMIN, 'administrateur', 'DOS-001')
        self.assertEqual(str(ctx.exception), 'cloisonnement_non_disponible')

    def test_pas_de_propagation_vers_nextcloud(self):
        core.clear_selection(self.desk.c['state_dir'])
        with self.assertRaises(Stop) as ctx:
            cloison.preview(self.desk, ADMIN, 'administrateur', 'DOS-001')
        self.assertEqual(str(ctx.exception), 'cloisonnement_stockage_cloud_seulement')
        self.assertIn('Nextcloud', cloison.support(self.desk)['raison'])

    def test_parcours_borne_et_signale(self):
        with patch.object(cloison, 'MAX_ITEMS', 1):
            out = cloison.preview(self.desk, ADMIN, 'administrateur', 'DOS-001')
        self.assertTrue(out['plan']['parcours_partiel'])


class PartitionDrive(Base):
    def setUp(self):
        super().setUp()
        self.view = self.connect('cloud_google_drive')
        f = self.fake
        self.root = f.d_add('AxiorHub', 'root', gdrive.FOLDER)
        self.top = f.d_add('Dossiers', self.root, gdrive.FOLDER)
        self.demo = f.d_add('DEMO', self.top, gdrive.FOLDER)
        core.write_selection(self.desk.c['state_dir'], {'provider': 'cloud_google_drive', 'connection': self.view['id'], 'root_id': self.root})
        membres5650.set_reserve(self.desk.c['state_dir'], 'DOS-001', [AVOCATE])
        self.desk.db.execute(membres5650.SCHEMA)
        self.desk.db.execute('INSERT INTO membres5650(owner,name,role,first_seen,last_seen) VALUES(?,?,?,?,?)', (ASSISTANT, 'A', 'assistant', 'x', 'x'))
        self.desk.db.commit()
        cloison.set_identity(self.desk, ADMIN, 'administrateur', 'cloud_google_drive', ASSISTANT, 'assistant@valbrune.example.test')

    def test_acces_venant_du_parent_detecte_meme_sans_detail(self):
        """Drive ne détaille pas toujours l'héritage : sans comparaison avec le parent, ce retrait partirait, échouerait, ou laisserait croire le dossier fermé."""
        same = {'id': 'perm-1', 'type': 'user', 'role': 'writer', 'emailAddress': 'assistant@valbrune.example.test'}
        self.fake.perms[self.demo] = [dict(same), {'id': 'perm-2', 'type': 'anyone', 'role': 'reader'}]
        self.fake.perms[self.top] = [dict(same)]
        out = cloison.preview(self.desk, ADMIN, 'administrateur', 'DOS-001')
        actions = {e['perm']['id']: e['action'] for e in out['plan']['entrees']}
        self.assertEqual(actions, {'perm-1': 'herite', 'perm-2': 'retirer'})
        cloison.confirm(self.desk, ADMIN, 'administrateur', out['id'], out['hash'])
        self.assertEqual([p['id'] for p in self.fake.perms[self.top]], ['perm-1'])
        self.assertEqual([p['id'] for p in self.fake.perms[self.demo]], ['perm-1'])

    def test_detail_d_heritage_lu(self):
        self.fake.perms[self.demo] = [{'id': 'perm-3', 'type': 'user', 'role': 'reader', 'emailAddress': 'assistant@valbrune.example.test', 'permissionDetails': [{'inherited': True, 'inheritedFrom': 'x'}]}]
        out = cloison.preview(self.desk, ADMIN, 'administrateur', 'DOS-001')
        self.assertEqual(out['plan']['entrees'][0]['action'], 'herite')


class PartitionDropbox(Base):
    def setUp(self):
        super().setUp()
        self.view = self.connect('cloud_dropbox')
        for path in ('/AxiorHub', '/AxiorHub/Dossiers'):
            self.fake.b_add(path)
        self.demo = self.fake.b_add('/AxiorHub/Dossiers/DEMO')
        self.demo['folder'] = {'shared_folder_id': 'sf1'}
        core.write_selection(self.desk.c['state_dir'], {'provider': 'cloud_dropbox', 'connection': self.view['id']})
        membres5650.set_reserve(self.desk.c['state_dir'], 'DOS-001', [AVOCATE])
        self.desk.db.execute(membres5650.SCHEMA)
        self.desk.db.execute('INSERT INTO membres5650(owner,name,role,first_seen,last_seen) VALUES(?,?,?,?,?)', (ASSISTANT, 'A', 'assistant', 'x', 'x'))
        self.desk.db.commit()
        cloison.set_identity(self.desk, ADMIN, 'administrateur', 'cloud_dropbox', ASSISTANT, 'assistant@valbrune.example.test')
        member = lambda ident, email, access='editor', inherited=False: {'user': {'account_id': ident, 'email': email}, 'access_type': {'.tag': access}, 'is_inherited': inherited}
        self.fake.perms['sf1'] = {'users': [member('dbid:1', 'cabinet@example.test', 'owner'), member('dbid:2', 'assistant@valbrune.example.test'), member('dbid:3', 'assistant@valbrune.example.test', inherited=True)]}

    def test_retrait_sans_copie_laissee_au_membre(self):
        out = cloison.preview(self.desk, ADMIN, 'administrateur', 'DOS-001')
        self.assertEqual({e['perm']['id']: e['action'] for e in out['plan']['entrees']}, {'dbid:1': 'conserver', 'dbid:2': 'retirer', 'dbid:3': 'herite'})
        cloison.confirm(self.desk, ADMIN, 'administrateur', out['id'], out['hash'])
        call = next(c for c in self.fake.calls if c['path'].endswith('sharing/remove_folder_member'))
        self.assertIs(json.loads(call['body'])['leave_a_copy'], False)

    def test_compte_personnel_non_disponible(self):
        self.fake.dropbox_type = 'basic'
        support = cloison.support(self.desk)
        self.assertFalse(support['possible'])
        self.assertIn('personnel', support['raison'])


# ---------------------------------------------------------------------------------------------------- interface
class Interface(Base):
    def test_page_sans_secret_avec_echappement(self):
        from agent import web_cloud5688 as web
        self.connect('cloud_dropbox', label='<script>x</script>', base='AxiorHub')
        env = {'wsgi.url_scheme': 'https', 'HTTP_HOST': 'axiorhub.example.test', 'SERVER_NAME': 'axiorhub.example.test', 'SERVER_PORT': '443'}
        html = web.section_html(self.desk, {'origin': 'https://axiorhub.example.test'}, '/agent-courriel', env)
        for text in ('Stockage en service', 'Google Drive', 'SharePoint ou OneDrive', 'Dropbox', 'Nommage et rangement', 'Cloisonnement chez le fournisseur', 'connexions-retour'):
            self.assertIn(text, html)
        self.assertNotIn('secret-fictif', html)
        self.assertNotIn('jeton-acces-fictif', html)
        self.assertNotIn('<script', html)

    def test_rubrique_declaree(self):
        from agent import parametres5621 as p21
        self.assertIn('stockage', dict((r[0], r[1]) for r in p21.RUBRIQUES))
        self.assertIn('stockage', p21.BUILDERS)
        self.assertEqual(sum(len(keys) for _, keys in p21.FAMILIES), len(p21.RUBRIQUES))

    def test_routes_reservees_aux_administrateurs_meme_avec_le_droit_reglages(self):
        from agent import standalone_auth
        membres5650.set_rights(self.desk.c['state_dir'], ADMIN, 'avocat@example.test', 'avocat', {'reglages': True})
        path = '/api440/m5688cloud/cloison/confirmer'
        self.assertFalse(membres5650.allowed(self.desk.c['state_dir'], 'avocat@example.test', 'avocat', 'POST', path))
        self.assertFalse(standalone_auth.allowed('assistant', 'POST', path))
        self.assertFalse(standalone_auth.allowed('avocat', 'POST', path))
        self.assertTrue(membres5650.allowed(self.desk.c['state_dir'], ADMIN, 'administrateur', 'POST', path))

    def test_api_refuse_un_compte_non_administrateur(self):
        from agent import web_cloud5688 as web
        with patch('agent.web567.actor', return_value=('avocat@example.test', 'avocat')):
            out = web.api({}, self.desk, {}, '', 'm5688cloud/choisir', {}, 'POST')
        self.assertIn('role_insuffisant', str(out))

    def test_page_du_dossier_signale_que_la_reservation_ne_ferme_pas_le_cloud(self):
        self.desk.c['nextcloud']['stockage_cloud'] = {'provider': 'cloud_dropbox', 'connection': 'x'}
        html = membres5650.access_card(self.desk, {}, '/agent-courriel', 'DOS-001', 'administrateur')
        self.assertIn('rubrique=stockage#cloisonnement', html)
        del self.desk.c['nextcloud']['stockage_cloud']
        self.assertNotIn('rubrique=stockage', membres5650.access_card(self.desk, {}, '/agent-courriel', 'DOS-001', 'administrateur'))


if __name__ == '__main__':
    unittest.main()
