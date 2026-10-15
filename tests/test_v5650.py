"""5.6.50 : messagerie interne du cabinet, droits par compte, dossiers réservés, fichiers sur un serveur WebDAV autre que Nextcloud.

Données d'essai fictives (domaine example.test, comptes et dossiers fictifs) ; le serveur WebDAV est un faux serveur local.
"""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import threading
import unittest
from unittest.mock import patch
from urllib.parse import quote, unquote, urlsplit

from agent import membres5650 as mb, messagerie5650 as mx
from agent.common import Stop, load_matters, private_json
from agent.desk import Desk
import test_agent as fixtures
import test_v5648 as t48
import test_v520_docs as td

PREFIX = '/agent-courriel'


# ---------------------------------------------------------------------------------------------------- droits (unitaires)
class Rights(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.EngineTests(methodName='test_observation_has_no_mail_write')
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.state = self.f.c['state_dir']

    def test_routes_are_classified_by_right(self):
        cases = {('POST', '/api440/send/confirm', ''): 'envoyer', ('POST', '/api440/m5628/envoyer', ''): 'envoyer',
                 ('POST', '/action', 'deposit_draft'): 'deposer', ('POST', '/api440/fact/validate', ''): 'valider',
                 ('POST', '/action', 'approve_project'): 'valider', ('POST', '/api440/m5634/agenda/creer', ''): 'agenda',
                 ('POST', '/action', 'create_agenda_event'): 'agenda', ('POST', '/api440/m500/time/add', ''): 'facturation',
                 ('GET', '/parametres', ''): 'reglages', ('GET', '/parametres/agendas', ''): None, ('GET', '/courriels', ''): None,
                 ('POST', '/api440/m5636/comptes/ajouter', ''): 'comptes', ('POST', '/api440/m5650/droits', ''): 'comptes',
                 ('GET', '/api440/send/status', ''): None}
        for (method, path, action), expected in cases.items():
            self.assertEqual(mb.category(method, path, action), expected, (method, path, action))

    def test_role_defaults_then_account_overrides(self):
        allowed = lambda owner, role, method, path, action='': mb.allowed(self.state, owner, role, method, path, action)
        self.assertTrue(allowed('avocate@example.test', 'avocat', 'POST', '/api440/send/confirm'))
        self.assertFalse(allowed('assistant@example.test', 'assistant', 'POST', '/api440/send/confirm'))
        mb.set_rights(self.state, 'cabinet', 'avocate@example.test', 'avocat', {'envoyer': False, 'agenda': True})
        mb.set_rights(self.state, 'cabinet', 'assistant@example.test', 'assistant', {'envoyer': True, 'reglages': True})
        self.assertFalse(allowed('avocate@example.test', 'avocat', 'POST', '/api440/send/confirm'))
        self.assertTrue(allowed('avocate@example.test', 'avocat', 'POST', '/api440/m5634/agenda/creer'))   # inchangé
        self.assertTrue(allowed('assistant@example.test', 'assistant', 'POST', '/api440/send/confirm'))
        self.assertTrue(allowed('assistant@example.test', 'assistant', 'GET', '/parametres'))
        for route in ('/api440/m5636/comptes/ajouter', '/api440/m5650/droits'):          # jamais d'élévation vers la gestion des comptes
            self.assertFalse(allowed('assistant@example.test', 'assistant', 'POST', route), route)
        self.assertFalse(allowed('assistant@example.test', 'assistant', 'POST', '/api440/route/inconnue'))
        self.assertTrue(allowed('cabinet', 'administrateur', 'POST', '/api440/send/confirm'))
        stored = json.loads((Path(self.state) / mb.FILE).read_text(encoding='utf-8'))
        self.assertEqual(stored['droits']['avocate@example.test'], {'envoyer': False})     # seul l'écart au rôle est gardé
        mb.set_rights(self.state, 'cabinet', 'avocate@example.test', 'avocat', dict(mb.ROLE_DEFAULTS['avocat']))
        self.assertNotIn('avocate@example.test', mb.load(self.state)['droits'])

    def test_administrators_and_self_cannot_be_restricted(self):
        with self.assertRaises(Stop):
            mb.set_rights(self.state, 'cabinet', 'associe@example.test', 'administrateur', {'envoyer': False})
        with self.assertRaises(Stop):
            mb.set_rights(self.state, 'avocate@example.test', 'avocate@example.test', 'avocat', {'reglages': True})

    def test_reserved_matter_is_hidden_from_others_during_a_request(self):
        mb.set_reserve(self.state, 'DOS-001', ['avocate@example.test'])
        self.assertEqual(mb.hidden_for(self.state, 'autre@example.test', 'avocat'), {'DOS-001'})
        self.assertEqual(mb.hidden_for(self.state, 'avocate@example.test', 'avocat'), set())
        self.assertEqual(mb.hidden_for(self.state, 'associe@example.test', 'administrateur'), set())
        mb.set_viewer(mb.hidden_for(self.state, 'autre@example.test', 'avocat'))
        try:
            self.assertNotIn('DOS-001', [m['id'] for m in load_matters(self.f.c)])
            self.assertIn('DOS-001', [m['id'] for m in load_matters(self.f.c, unfiltered=True)])
        finally:
            mb.clear()
        self.assertIn('DOS-001', [m['id'] for m in load_matters(self.f.c)])                 # hors requête : travaux de l'agent
        mb.set_reserve(self.state, 'DOS-001', [])
        self.assertEqual(mb.load(self.state)['reserves'], {})

    def test_agenda_events_of_a_reserved_matter_are_hidden(self):
        from datetime import datetime, timedelta, timezone
        from agent import agenda5634
        d = Desk(self.f.c)
        start = (datetime.now(timezone.utc) + timedelta(days=2)).replace(microsecond=0).isoformat()
        agenda5634.create(d, {'title': 'Audience fictive', 'start': start, 'matter': 'DOS-001', 'kind': 'audience'})
        self.assertEqual(len(agenda5634.listing(d)), 1)
        mb.set_reserve(self.state, 'DOS-001', ['avocate@example.test'])
        mb.set_viewer(mb.hidden_for(self.state, 'autre@example.test', 'avocat'))
        try:
            self.assertEqual(agenda5634.listing(d), [])
        finally:
            mb.clear()
        self.assertEqual(len(agenda5634.listing(d)), 1)

    def test_cabinet_wide_search_skips_reserved_matters(self):
        from agent.index import DocumentIndex
        index = DocumentIndex(self.state)
        matter = next(m for m in load_matters(self.f.c) if m['id'] == 'DOS-001')
        index.sync(self.f.dav, matter, {})
        found, _ = index.global_sources(['etat'], limit=10)
        self.assertTrue(any(x['matter'] == 'DOS-001' for x in found))
        mb.set_reserve(self.state, 'DOS-001', ['avocate@example.test'])
        mb.set_viewer(mb.hidden_for(self.state, 'autre@example.test', 'avocat'))
        try:
            found, _ = index.global_sources(['etat'], limit=10)
            self.assertFalse(any(x['matter'] == 'DOS-001' for x in found))
        finally:
            mb.clear()


# ---------------------------------------------------------------------------------------------------- messagerie (unitaires)
class Messaging(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.EngineTests(methodName='test_observation_has_no_mail_write')
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.d = Desk(self.f.c)
        mx.ensure_schema(self.d)
        self.members = {o: {'owner': o, 'name': n, 'role': r} for o, n, r in (
            ('cabinet', 'admin', 'administrateur'), ('avocate@example.test', 'Me Avocate', 'avocat'),
            ('assistant@example.test', 'Assistant Fictif', 'assistant'), ('stagiaire@example.test', 'Stagiaire Fictif', 'avocat'))}

    def post(self, me, tid, text, hidden=frozenset()):
        return mx.post(self.d, me, self.members[me]['name'], self.members[me]['role'], tid, text, self.members, hidden)

    def test_direct_conversation_is_private_and_counts_unread(self):
        tid = mx.open_direct(self.d, 'avocate@example.test', 'assistant@example.test', self.members)
        self.assertEqual(mx.open_direct(self.d, 'assistant@example.test', 'avocate@example.test', self.members), tid)   # une seule conversation
        self.post('avocate@example.test', tid, 'Pouvez-vous préparer le bordereau ?')
        self.assertEqual(mx.unread_total(self.d, 'assistant@example.test'), 1)
        self.assertEqual(mx.unread_total(self.d, 'avocate@example.test'), 0)
        listed = mx.threads(self.d, 'assistant@example.test', self.members)
        self.assertEqual((listed[0]['titre'], listed[0]['non_lus']), ('Me Avocate', 1))
        got = mx.messages(self.d, 'assistant@example.test', tid)
        self.assertEqual((got[0]['nom'], got[0]['role'], got[0]['texte']), ('Me Avocate', 'avocat', 'Pouvez-vous préparer le bordereau ?'))
        self.assertEqual(mx.unread_total(self.d, 'assistant@example.test'), 0)                 # lu à l'ouverture
        for outsider in ('cabinet', 'stagiaire@example.test'):                                  # l'administrateur ne lit pas les conversations
            with self.assertRaisesRegex(Stop, 'conversation_absente'):
                mx.messages(self.d, outsider, tid)
        with self.assertRaisesRegex(Stop, 'destinataire_inconnu'):
            mx.open_direct(self.d, 'avocate@example.test', 'inconnu@example.test', self.members)

    def test_group_membership_rules(self):
        tid = mx.create_group(self.d, 'avocate@example.test', 'Pôle contentieux', ['assistant@example.test'], self.members)
        self.post('assistant@example.test', tid, 'Bonjour à tous')
        with self.assertRaisesRegex(Stop, 'conversation_absente'):
            mx.messages(self.d, 'stagiaire@example.test', tid)
        mx.change_group(self.d, 'assistant@example.test', tid, add=['stagiaire@example.test'], members=self.members)
        self.assertEqual(len(mx.messages(self.d, 'stagiaire@example.test', tid)), 1)
        with self.assertRaisesRegex(Stop, 'retrait_reserve_au_createur'):
            mx.change_group(self.d, 'assistant@example.test', tid, remove=['stagiaire@example.test'])
        mx.change_group(self.d, 'stagiaire@example.test', tid, remove=['stagiaire@example.test'])   # chacun peut partir
        with self.assertRaisesRegex(Stop, 'conversation_absente'):
            mx.messages(self.d, 'stagiaire@example.test', tid)
        info = mx.thread_info(self.d, 'avocate@example.test', tid, self.members)
        self.assertEqual([m['owner'] for m in info['membres']], ['avocate@example.test', 'assistant@example.test'])
        with self.assertRaisesRegex(Stop, 'titre_groupe_requis'):
            mx.create_group(self.d, 'avocate@example.test', ' ', ['assistant@example.test'], self.members)

    def test_matter_thread_followers_mentions_and_agent_notices(self):
        tid = mx.matter_thread(self.d, 'DOS-001', 'avocate@example.test')
        self.post('avocate@example.test', tid, 'Audience déplacée. @assistant merci de prévenir le client.')
        self.assertEqual(mx.unread_total(self.d, 'assistant@example.test'), 1)                 # mentionné : suit le fil
        self.assertEqual(mx.unread_total(self.d, 'stagiaire@example.test'), 0)                 # ne suit pas le fil
        self.assertEqual(len(mx.messages(self.d, 'stagiaire@example.test', tid)), 1)           # mais peut le lire
        self.assertIsNotNone(mx.notice(self.d, 'DOS-001', 'Document produit, à relire : « Note ».', '/dossier?id=DOS-001&vue=travaux', 'Travaux'))
        self.assertEqual(mx.unread_total(self.d, 'avocate@example.test'), 1)
        last = mx.messages(self.d, 'avocate@example.test', tid)[-1]
        self.assertEqual((last['genre'], last['nom'], last['lien']), ('avis', 'Agent AxiorHub', '/dossier?id=DOS-001&vue=travaux'))
        mx.follow(self.d, 'avocate@example.test', tid, False)
        self.assertEqual(mx.threads(self.d, 'avocate@example.test', self.members), [])
        self.assertIsNone(mx.notice(self.d, 'DOS-INCONNU', 'x'))                               # jamais d'erreur visible
        with self.assertRaisesRegex(Stop, 'conversation_absente'):                             # dossier réservé à d'autres
            mx.messages(self.d, 'stagiaire@example.test', tid, frozenset({'DOS-001'}))

    def test_message_rules(self):
        tid = mx.open_direct(self.d, 'avocate@example.test', 'assistant@example.test', self.members)
        for text, code in (('   ', 'message_vide'), ('x' * (mx.MAX_TEXT + 1), 'message_trop_long')):
            with self.assertRaisesRegex(Stop, code):
                self.post('avocate@example.test', tid, text)
        with self.assertRaisesRegex(Stop, 'lien_interne_invalide'):
            mx.post(self.d, 'avocate@example.test', 'Me', 'avocat', tid, 'Voir', self.members, link='https://autre.example.test/')
        out = self.post('avocate@example.test', tid, '<script>alert(1)</script>')
        self.assertEqual(mx.messages(self.d, 'assistant@example.test', tid)[0]['texte'], '<script>alert(1)</script>')   # texte brut, rendu par textContent
        with self.assertRaisesRegex(Stop, 'message_absent'):
            mx.remove(self.d, 'assistant@example.test', out['id'])
        mx.remove(self.d, 'avocate@example.test', out['id'])
        got = mx.messages(self.d, 'assistant@example.test', tid)[0]
        self.assertEqual((got['retire'], got['texte']), (True, ''))


# ---------------------------------------------------------------------------------------------------- version serveur (application réelle)
class ServerAccounts(t48.Base):
    def add(self, admin, ident, role):
        r = self.api('m5636/comptes/ajouter', {'ident': ident, 'name': ident.split('@')[0].title(), 'role': role, 'password': 'provisoire-fictif-1'}, admin)
        self.assertTrue(r['status'].startswith('200'), r['body'])
        r = self.login(ident, 'provisoire-fictif-1')
        cookie = self.token(r)
        self.request('/connexion/changer', 'POST', {'password': 'personnel-fictif-22', 'confirm': 'personnel-fictif-22'}, cookie=cookie)
        return self.token(self.login(ident, 'personnel-fictif-22'))

    def test_messaging_between_accounts(self):
        admin = self.admin_cookie()
        lawyer = self.add(admin, 'avocate@example.test', 'avocat')
        page = self.request('/messagerie', cookie=lawyer)
        self.assertEqual(page['status'], '200 OK', page['body'][:300])
        self.assertIn('Messagerie du cabinet', page['body'])
        self.assertIn('v5650.js', page['body'])
        self.assertIn('href="%s/messagerie"' % PREFIX, page['body'])                         # entrée du menu
        opened = json.loads(self.api('m5650/direct', {'avec': 'cabinet'}, lawyer)['body'])
        tid = opened['id']
        self.assertEqual(opened['goto'], '?fil=%d' % tid)
        sent = self.api('m5650/ecrire', {'id': tid, 'texte': 'Bonjour, le dossier DEMO avance.'}, lawyer)
        self.assertTrue(sent['status'].startswith('200'), sent['body'])
        unread = json.loads(self.call('/api440/m5650/nonlus', auth=False, extra={'HTTP_COOKIE': 'axh_session=' + admin})['body'])
        self.assertEqual(unread['non_lus'], 1)
        read = json.loads(self.call('/api440/m5650/fil', auth=False, query='id=%d' % tid, extra={'HTTP_COOKIE': 'axh_session=' + admin})['body'])
        self.assertEqual(read['messages'][0]['nom'], 'Avocate')
        self.assertEqual(read['messages'][0]['role'], 'avocat')
        self.assertEqual(read['fil']['titre'], 'Avocate')
        page = self.request('/messagerie', query='fil=%d' % tid, cookie=admin)
        self.assertIn('m50-compose', page['body'])

    def test_rights_page_and_restriction_by_the_administrator(self):
        admin = self.admin_cookie()
        lawyer = self.add(admin, 'avocate@example.test', 'avocat')
        self.assertEqual(self.request('/parametres/droits', cookie=lawyer)['status'], '403 Forbidden')
        page = self.request('/parametres/droits', cookie=admin)
        self.assertEqual(page['status'], '200 OK')
        self.assertIn('Droits par compte', page['body'])
        self.assertIn('avocate@example.test', page['body'])
        refused = self.api('m5650/droits', {'owner': 'avocate@example.test', 'role': 'avocat', 'envoyer': True}, lawyer)
        self.assertTrue(refused['status'].startswith('403'))
        values = {**mb.ROLE_DEFAULTS['avocat'], 'agenda': False}
        ok = self.api('m5650/droits', {'owner': 'avocate@example.test', 'role': 'avocat', **values}, admin)
        self.assertTrue(ok['status'].startswith('200'), ok['body'])
        blocked = self.api('m5634/agenda/creer', {'matter': 'DOS-001', 'title': 'Audience'}, lawyer)
        self.assertTrue(blocked['status'].startswith('403'), blocked['body'])
        self.assertIn('role_insuffisant', blocked['body'].decode())

    def test_reserved_matter_in_the_real_application(self):
        admin = self.admin_cookie()
        lawyer = self.add(admin, 'avocate@example.test', 'avocat')
        other = self.add(admin, 'autre@example.test', 'avocat')
        self.assertTrue(self.request('/dossier', query='id=DOS-001', cookie=other)['status'].startswith('200'))
        card = self.request('/dossier', query='id=DOS-001', cookie=admin)['body']
        self.assertIn('Accès au dossier', card)
        self.assertIn('Discussion du dossier', card)
        r = self.api('m5650/reserve', {'matter': 'DOS-001', 'compte:avocate@example.test': True, 'compte:autre@example.test': False}, admin)
        self.assertTrue(r['status'].startswith('200'), r['body'])
        self.assertEqual(self.request('/dossier', query='id=DOS-001', cookie=other)['status'], '403 Forbidden')
        self.assertNotIn('DOS-001', self.request('/dossiers', cookie=other)['body'])
        api = self.api('m5650/dossier', {'matter': 'DOS-001'}, other)
        self.assertTrue(api['status'].startswith('403'))
        self.assertIn('dossier_reserve', api['body'].decode())
        self.assertTrue(self.request('/dossier', query='id=DOS-001', cookie=lawyer)['status'].startswith('200'))
        self.assertTrue(self.request('/dossier', query='id=DOS-001', cookie=admin)['status'].startswith('200'))
        self.assertTrue(self.call('/dossier', query='id=DOS-001')['status'].startswith('200'))   # en-tête Basic des scripts : administrateur


class AgentNotices(td.Docs):
    """Un document produit par l'agent dépose un avis dans le fil du dossier (reprend aussi les essais 5.2.0 des documents)."""

    def test_document_produced_posts_a_notice(self):
        from agent import docrequest520 as dr
        rid = dr.submit(self.desk, 'Prépare un courrier au confrère pour un renvoi', '20240101001', 'courrier')['request']
        self.run_job(rid)
        row = self.desk.db.execute("SELECT m.* FROM messages5650 m JOIN fils5650 f ON f.id=m.thread WHERE f.matter='20240101001'").fetchone()
        self.assertEqual((row['kind'], row['author']), ('avis', 'agent'))
        self.assertIn('Document produit, à relire', row['text'])
        self.assertEqual(row['link'], '/dossier?id=20240101001&vue=travaux')


# ---------------------------------------------------------------------------------------------------- WebDAV autre que Nextcloud
class FakeWebDAV(BaseHTTPRequestHandler):
    files = {}
    folders = set()
    root = '/dav/fichiers'

    def log_message(self, *args):
        pass

    def _rel(self):
        path = unquote(urlsplit(self.path).path)
        assert path.startswith(self.root), path
        return path[len(self.root):].rstrip('/') or '/'

    def _send(self, code, body=b'', headers=()):
        self.send_response(code)
        for k, v in headers:
            self.send_header(k, v)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _entry(self, rel):
        href = quote(self.root + rel + ('/' if rel in self.folders and rel != '/' else ''))
        if rel in self.folders:
            prop = '<d:resourcetype><d:collection/></d:resourcetype>'
        else:
            prop = '<d:resourcetype/><d:getetag>"%d"</d:getetag><d:getcontentlength>%d</d:getcontentlength>' % (hash(self.files[rel]) & 0xffff, len(self.files[rel]))
        return ('<d:response><d:href>%s</d:href><d:propstat><d:prop>%s<d:getlastmodified>Wed, 14 Oct 2026 10:00:00 GMT</d:getlastmodified></d:prop>'
                '<d:status>HTTP/1.1 200 OK</d:status></d:propstat></d:response>') % (href, prop)

    def do_PROPFIND(self):
        self.rfile.read(int(self.headers.get('Content-Length') or 0))
        rel = self._rel()
        if rel not in self.folders and rel not in self.files:
            return self._send(404)
        entries = [rel]
        if rel in self.folders and self.headers.get('Depth') == '1':
            entries += sorted(p for p in list(self.folders) + list(self.files) if p != rel and p.rsplit('/', 1)[0] == (rel if rel != '/' else ''))
        body = ('<?xml version="1.0"?><d:multistatus xmlns:d="DAV:">%s</d:multistatus>' % ''.join(self._entry(p) for p in entries)).encode()
        self._send(207, body, [('Content-Type', 'application/xml')])

    def do_GET(self):
        rel = self._rel()
        if rel not in self.files:
            return self._send(404)
        self._send(200, self.files[rel])

    def do_PUT(self):
        rel = self._rel()
        data = self.rfile.read(int(self.headers.get('Content-Length') or 0))
        if self.headers.get('If-None-Match') == '*' and rel in self.files:
            return self._send(412)
        self.files[rel] = data
        self._send(201)

    def do_MKCOL(self):
        self.folders.add(self._rel())
        self._send(201)

    def do_MOVE(self):
        rel = self._rel()
        dest = unquote(urlsplit(self.headers['Destination']).path)[len(self.root):]
        if rel not in self.files:
            return self._send(404)
        self.files[dest] = self.files.pop(rel)
        self._send(201)

    def do_DELETE(self):
        self.files.pop(self._rel(), None)
        self._send(204)


class GenericWebDAV(unittest.TestCase):
    def setUp(self):
        FakeWebDAV.files = {'/Dossiers/DEMO/Contrat.txt': b'Contrat fictif'}
        FakeWebDAV.folders = {'/', '/Dossiers', '/Dossiers/DEMO'}
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), FakeWebDAV)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.f = fixtures.EngineTests(methodName='test_observation_has_no_mail_write')
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        secret = Path(self.f.c['state_dir']) / 'webdav.pw'
        secret.write_text('mot-de-passe-fictif')
        secret.chmod(0o600)
        self.cfg = {'url': 'https://nextcloud.example.com', 'username': 'cabinet', 'password_file': str(secret), 'roots': ['/Dossiers'],
                    'webdav_url': 'http://127.0.0.1:%d/dav/fichiers/' % self.server.server_address[1]}

    def test_files_on_another_webdav_server(self):
        from agent.dav import DAV
        dav = DAV(self.cfg)
        self.assertTrue(dav.generic)
        listed = dav.list_folder('/Dossiers/DEMO')
        self.assertEqual([x['path'] for x in listed], ['/Dossiers/DEMO/Contrat.txt'])
        meta = dav.stat('/Dossiers/DEMO/Contrat.txt')
        self.assertEqual(dav.download(meta), b'Contrat fictif')
        dav.put_file('/Dossiers/DEMO/Note.docx', b'PK-fictif')
        self.assertEqual(FakeWebDAV.files['/Dossiers/DEMO/Note.docx'], b'PK-fictif')
        with self.assertRaises(Stop):
            dav.put_file('/Dossiers/DEMO/Note.docx', b'autre')                                   # jamais d'écrasement
        with self.assertRaisesRegex(Stop, 'fichier_hors_racines'):
            dav.list_folder('/Autre')
        dav.trash_file('/Dossiers/DEMO/Note.docx', dav.stat('/Dossiers/DEMO/Note.docx')['etag'])
        self.assertNotIn('/Dossiers/DEMO/Note.docx', FakeWebDAV.files)
        moved = [p for p in FakeWebDAV.files if p.startswith('/Dossiers/DEMO/Corbeille AxiorHub/')]
        self.assertEqual(len(moved), 1)                                                            # déplacé, jamais supprimé
        self.assertTrue(moved[0].endswith('Note.docx'))
        with self.assertRaisesRegex(Stop, 'ouverture_en_ligne_nextcloud_seulement'):
            dav.file_web_url('/Dossiers/DEMO/Contrat.txt')
        with self.assertRaisesRegex(Stop, 'agendas_dans_parametres_agendas'):
            dav.calendars()

    def test_address_validation(self):
        from agent.config567 import _validate
        from agent.dav import DAV
        self.assertEqual(_validate('url', '', 'nextcloud.webdav_url'), '')
        with self.assertRaisesRegex(Stop, 'https_obligatoire'):
            _validate('url', 'http://dav.example.test/fichiers', 'nextcloud.webdav_url')
        self.assertEqual(_validate('url', 'https://dav.example.test/fichiers/', 'nextcloud.webdav_url'), 'https://dav.example.test/fichiers')
        with self.assertRaisesRegex(Stop, 'adresse_webdav_invalide'):
            DAV({**self.cfg, 'webdav_url': 'https://dav.example.test/fichiers?x=1'})
        with self.assertRaisesRegex(Stop, 'https_obligatoire'):
            DAV({**self.cfg, 'webdav_url': 'http://dav.example.test/fichiers'})               # en clair : seulement en local
        self.assertFalse(DAV({k: v for k, v in self.cfg.items() if k != 'webdav_url'}).generic)  # Nextcloud inchangé


if __name__ == '__main__':
    unittest.main()
