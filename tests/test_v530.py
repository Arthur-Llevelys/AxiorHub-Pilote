"""5.3.0 : poste de pilotage — conversation avec l'agent (dossier deviné ou corrigé, pièces jointes), « À relire » en panneau latéral,
activité de l'agent, journée Nextcloud (agenda, Tâches, Deck), routines en onglets, menu réduit ; rangement des documents dans
PROCEDURE / CORRESPONDANCES / PROJETS / LIVRABLES."""
import io
import json
from pathlib import Path
import time
import unittest
from unittest.mock import patch
import zipfile

from agent import cockpit530 as ck, deck530, docrequest520 as dr
from agent.common import Stop, private_json
from agent.desk import Desk
from agent.state import State
import test_v510 as t510
import test_v520_docs as td
import test_v520_routines as tr

ALPHA, BETA = td.ALPHA, td.BETA


class FakeDeck:
    def __init__(self):
        self.user, self.web = 'axiorhub', 'https://cloud.example.test/index.php/apps/deck/#/board/'
        self.boards_list, self.stack_rows, self.cards, self.shared, self.n = [], [], {}, [], 100

    def boards(self):
        return self.boards_list

    def create_board(self, title):
        self.boards_list.append({'id': 7, 'title': title})
        return {'id': 7}

    def stacks(self, board):
        return self.stack_rows

    def create_stack(self, board, title, order):
        self.n += 1
        self.stack_rows.append({'id': self.n, 'title': title})
        return {'id': self.n}

    def create_card(self, board, stack, title, description):
        self.n += 1
        self.cards[self.n] = {'stack': stack, 'title': title}
        return {'id': self.n}

    def move_card(self, board, stack, card, new_stack):
        assert self.cards[card]['stack'] == stack
        self.cards[card]['stack'] = new_stack

    def share(self, board, user):
        self.shared.append(user)

    def stack_name(self, sid):
        return next(s['title'] for s in self.stack_rows if s['id'] == sid)


class FakeTasks:
    def __init__(self):
        self.puts = []

    def put_todo(self, calendar, uid, title, description='', start=None, due=None, status='NEEDS-ACTION', priority=5, percent=0, etag=''):
        self.puts.append({'uid': uid, 'title': title, 'status': status, 'etag': etag})
        return {'uid': uid}


class Base(t510.Base):
    def setUp(self):
        super().setUp()
        private_json(Path(self.f.c['matters_file']), [ALPHA, BETA])
        self.f.c['mail'].update({'inbox': 'INBOX', 'sent': 'Sent', 'drafts': 'Drafts'})
        self.docs.files['/Dossiers/DEMO/CORRESPONDANCES/Courrier ancien.pdf'] = t510.make_pdf(1, 'Ancien')
        self.model = td.DocModel()
        self.drafts = {'folder': 'Drafts', 'uidvalidity': '9', 'items': []}
        for p in (patch('agent.docrequest520._model', return_value=self.model), patch('agent.document_projects._dav', return_value=self.docs),
                  patch('agent.mailbox.Mailbox', tr.FakeBox), patch('agent.drafts440.list_drafts', side_effect=lambda *a, **k: self.drafts)):
            p.start()
            self.addCleanup(p.stop)
        from agent.workplan import ensure_schema as wp
        wp(self.desk)

    def run_jobs(self, kind):
        for r in self.desk.db.execute("SELECT id,args FROM jobs WHERE kind=? AND status='pending'", (kind,)).fetchall():
            dr.perform(self.desk, kind, json.loads(r['args']))
            self.desk.db.execute("UPDATE jobs SET status='done' WHERE id=?", (r['id'],))
        self.desk.db.commit()


class Page(Base):
    def test_cockpit_is_the_home_screen(self):
        body = self.request('/aujourdhui')['body']
        for text in ('id="c530"', 'Instruction à l’agent', 'aria-label="Joindre un document"', 'aria-label="Dicter"', 'Envoyer',
                     'À relire', 'Ce que fait l’agent', 'Ma journée', 'Tâches Nextcloud', 'Deck · Production de l’agent', 'Routines du cabinet',
                     'Briefing du matin', 'Style du cabinet', '/static/v530.js', '/static/v500.js', '<option value="20240101001">DEMO</option>'):
            self.assertIn(text, body)
        self.assertIn('Audiences et rendez-vous', self.request('/aujourdhui', query='vue=essentiel')['body'])     # écran 5.2 conservé
        self.assertEqual(body.count('<h1'), 1)                                                     # un seul titre
        nav = self.request('/aujourdhui')['body']
        self.assertIn('aria-label="Mon style"', nav)
        self.assertIn('<nav aria-label="Rubriques"', nav)

    def test_guess_and_document_request_from_the_composer(self):
        g = ck.guess(self.desk, 'Prépare un courrier au confrère dans le dossier ALPHA')
        self.assertEqual((g['matter'], g['how']), ('20240101001', 'deviné par l’agent'))
        self.assertEqual(ck.guess(self.desk, 'Bonjour')['matter'], '')
        out = ck.ask(self.desk, {'text': 'Prépare un courrier au confrère pour un renvoi dans le dossier ALPHA'})
        self.assertTrue(out['ref'].startswith('docreq:'))
        html = ck.thread_html(self.desk, '/p')
        self.assertIn('Je travaille dans le dossier « DEMO »', html)
        self.assertIn('data-waiting="1"', html)
        self.run_jobs('docrequest520')
        row = dr.listing(self.desk)[0]
        self.assertTrue(row['path'].startswith('/Dossiers/DEMO/CORRESPONDANCES/'), row['path'])   # rangé dans CORRESPONDANCES
        html = ck.thread_html(self.desk, '/p')
        self.assertIn('prêt : il vous attend dans « À relire »', html)
        self.assertIn('data-waiting="0"', html)

    def test_user_override_and_question_to_the_assistant(self):
        out = ck.ask(self.desk, {'text': 'Où en est la procédure ?', 'matter': '2024020201'})
        self.assertTrue(out['ref'].startswith('ask:'))
        self.assertEqual(out['matter'], '2024020201')
        job = self.desk.db.execute("SELECT args FROM jobs WHERE kind='assistant_answer'").fetchone()
        self.assertEqual(json.loads(job['args'])['matter'], '2024020201')
        thread, mid = out['ref'].split(':')[1:]
        self.assertIn('Je cherche', ck.thread_html(self.desk, '/p'))
        self.desk.db.execute("INSERT INTO assistant_messages(thread_id,role,content,status,sources,job_id,created,updated) VALUES(?,?,?,?,?,?,?,?)",
                             (thread, 'assistant', 'Audience fixée au <12 novembre>.', 'done', '[]', None, 'x', 'x'))
        self.desk.db.commit()
        html = ck.thread_html(self.desk, '/p')
        self.assertIn('Audience fixée au &lt;12 novembre&gt;.', html)                          # échappé
        with self.assertRaises(Stop):
            ck.ask(self.desk, {'text': 'ok'})
        with self.assertRaises(Stop):
            ck.ask(self.desk, {'text': 'Une question valable', 'matter': 'INCONNU'})

    def test_attachments_reach_the_drafting_model(self):
        from agent.improvements36 import upload_attachment
        att = upload_attachment(self.desk, b'Conclusions adverses : la demande de renvoi est contestee.', 'adverse.txt', '20240101001')
        ck.ask(self.desk, {'text': 'Prépare un courrier de renvoi dans le dossier ALPHA', 'attachments': [att['attachment_id']]})
        self.run_jobs('docrequest520')
        joined = self.model.payloads[-1]['dossier']['pieces_jointes']
        self.assertEqual(joined[0]['fichier'], 'adverse.txt')
        self.assertIn('contestee', joined[0]['texte'])
        other = upload_attachment(self.desk, b'Autre dossier', 'autre.txt', '2024020201')
        with self.assertRaises(Stop):                                                          # pièce d'un autre dossier refusée
            ck.ask(self.desk, {'text': 'Prépare un courrier dans le dossier ALPHA', 'attachments': [other['attachment_id']]})


class Review(Base):
    def make_doc(self):
        rid = dr.submit(self.desk, 'Prépare une note de synthèse', '20240101001', 'note')['request']
        dr.perform(self.desk, 'docrequest520', {'request': rid})
        return dr.listing(self.desk)[0]

    def test_review_list_drawer_and_decisions(self):
        doc = self.make_doc()
        self.desk.db.execute('INSERT INTO work_items(mail_key,state,matter,subject) VALUES(?,?,?,?)', ('k' * 64, 'draft_ready', '2024020201', 'Re: Pièces'))
        self.desk.db.commit()
        self.drafts['items'] = [{'uid': '12', 'subject': 'Re: Pièces', 'to': 'client@example.test', 'date': '2026-10-04T08:00:00+00:00', 'agent': True, 'agent_key': 'k' * 64},
                                {'uid': '13', 'subject': 'Brouillon personnel', 'to': 'x@example.test', 'date': '2026-10-04T08:00:00+00:00', 'agent': False, 'agent_key': ''}]
        items = ck.review_items(self.desk)
        self.assertEqual([x['type'] for x in items].count('Courriel'), 1)
        mail = next(x for x in items if x['type'] == 'Courriel')
        self.assertEqual(mail['matter_label'], 'BRAVO - SCI LES CHENES - 2024020201')
        docitem = next(x for x in items if x['type'] == 'Note')
        html = ck.review_html(self.desk, '/p')
        self.assertIn('À relire <span class="c530-count">2</span>', html)
        drawer = ck.item_html(self.desk, '/p', docitem['id'])
        self.assertIn('Je sollicite le renvoi', drawer)
        self.assertIn('Valider le projet', drawer)
        self.assertIn('Faire modifier par l’IA', drawer)
        with patch('agent.drafts440.get_draft', return_value={'body': 'Madame, <b>test</b>', 'to': 'client@example.test',
                                                               'source': {'sender': 'client@example.test', 'subject': 'Pièces', 'key': 'a' * 64}}):
            drawer = ck.item_html(self.desk, '/p', mail['id'])
        self.assertIn('Madame, &lt;b&gt;test&lt;/b&gt;', drawer)
        self.assertIn('name="key" value="' + 'a' * 64, drawer)
        out = ck.revise(self.desk, {'item': mail['id'], 'instruction': 'Plus court', 'key': 'a' * 64})
        self.assertIn('Nouveau brouillon demandé', out['message'])
        self.assertEqual(json.loads(self.desk.db.execute("SELECT args FROM jobs WHERE kind='prepare_reply'").fetchone()['args'])['instruction'], 'Plus court')
        ck.review(self.desk, {'item': mail['id'], 'decision': 'relu'})
        ck.review(self.desk, {'item': docitem['id'], 'decision': 'valide'})
        self.assertEqual(ck.review_items(self.desk), [])
        with self.assertRaises(Stop):
            ck.review(self.desk, {'item': 'doc:inconnu', 'decision': 'valide'})

    def test_revision_of_any_document_of_the_matter(self):
        doc = self.make_doc()
        ck.review(self.desk, {'item': 'doc:' + __import__('agent.common', fromlist=['digest']).digest(doc['path'])[:24], 'decision': 'valide'})
        self.desk.db.execute('INSERT INTO production_deliverables_v420 VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                             ('d1', 1, 'prepare_document_project', 'job', 's', '20240101001', 'document_project', 'Projet', 'verified', 'deposited', '[]', '', '[]',
                              '/Dossiers/DEMO/PROJETS', json.dumps({'files': [{'path': '/Dossiers/DEMO/Conclusions récapitulatives.docx'}]}), 'ok', '', 0, 0,
                              self.desk.now(), self.desk.now()))
        self.desk.db.commit()
        item = next(x for x in ck.review_items(self.desk) if x['path'].endswith('Conclusions récapitulatives.docx'))
        ck.revise(self.desk, {'item': item['id'], 'instruction': 'Ajouter un paragraphe sur la recevabilité'})
        self.run_jobs('docrequest520')
        self.assertTrue(dr.listing(self.desk)[0]['path'].endswith('Conclusions récapitulatives v2.docx'))
        self.assertIn('Comme le montre la pièce', self.model.payloads[-1]['document_actuel'])
        with self.assertRaises(Stop):
            dr.submit(self.desk, 'Modifier un fichier hors dossier', '20240101001', 'auto', '', None, '/Dossiers/AUTRE/x.docx')


class Activity(Base):
    def test_feed_shows_running_done_and_blocked_with_actions(self):
        from agent import queue521
        jid = self.desk.enqueue('docrequest520', {'request': 'x'})
        self.desk.db.execute("UPDATE jobs SET status='running',started=? WHERE id=?", (queue521.now(), jid))
        for kind, code, prio in (('prepare_reply', 'generation_ia_delai_depasse', 0), ('run', queue521.INTERRUPTED, 0)):
            self.desk.db.execute("INSERT INTO jobs(kind,args,status,created,finished,result,priority,attempts) VALUES(?,?,?,?,?,?,?,1)",
                                 (kind, json.dumps({'matter': '20240101001', 'key': 'k'}), 'error', queue521.now(), queue521.now(), json.dumps({'erreur': code}), prio))
        self.desk.db.commit()
        State(self.f.c['state_dir']).set('m1', '<m1@x>', 't1', 'review', 'correspondant_ou_dossier_a_confirmer')
        f = ck.feed(self.desk)
        self.assertEqual(f['en_cours'][0]['text'], 'Préparation d’un document demandé')
        self.assertTrue(f['en_cours'][0]['running'])
        self.assertEqual(len([x for x in f['bloque'] if x.get('retry')]), 1)                     # l'interruption n'est pas un blocage
        self.assertIn('correspondant ou dossier à confirmer', f['bloque'][0]['text'])
        html = ck.feed_html(self.desk, '/p')
        self.assertIn('data-retry=', html)
        self.assertIn('/p/associations', html)
        retry = next(x for x in f['bloque'] if x.get('retry'))['retry']
        self.assertIn('Relancé', ck.retry(self.desk, {'job': retry})['message'])
        with self.assertRaises(Stop):
            ck.retry(self.desk, {'job': 999999})

    def test_tasks_are_listed_and_checked_in_nextcloud(self):
        now = self.desk.now()
        for tid, uid, title in (('t1', 'axiorhub-' + 'a' * 32 + '@mail-agent.local', 'Relire les conclusions'), ('t2', 'perso-1', 'Appeler le greffe')):
            self.desk.db.execute('INSERT INTO work_tasks_v211 VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                                 (tid, uid, 'https://cloud/t/', '', '*', '20240101001', title, '', '', '', 15, 5, 'todo', '', '', '', '', 'test', now, now, now))
        self.desk.db.commit()
        html = ck.day_html(self.desk, '/p')
        self.assertIn('Relire les conclusions', html)
        self.assertIn('c530-by agent', html)
        self.assertIn('c530-by me', html)
        fake = FakeTasks()
        with patch('agent.workplan._dav', return_value=fake):
            ck.toggle_task(self.desk, {'task': 't1', 'done': True})
        self.assertEqual(fake.puts[-1]['status'], 'COMPLETED')
        with self.assertRaises(Stop):                                                          # tâche personnelle : écriture non autorisée par défaut
            ck.toggle_task(self.desk, {'task': 't2', 'done': True})


class NextcloudBoard(Base):
    def setUp(self):
        super().setUp()
        secret = Path(self.f.c['state_dir']) / 'nc.secret'
        secret.write_text('x')
        self.f.c['nextcloud_workflow'] = {'enabled': True, 'url': 'https://cloud.example.test', 'username': 'axiorhub', 'password_file': str(secret),
                                          'task_calendar_url': 'https://cloud.example.test/remote.php/dav/calendars/axiorhub/axiorhub-taches/'}

    def test_disabled_by_default_then_board_cards_and_tasks_follow_the_work(self):
        self.assertIsNone(deck530.tick(self.desk))
        self.assertIn('désactivés', deck530.sync(self.desk)['message'])
        deck530.save_settings(self.desk, {'deck': True, 'tasks': True, 'board': 'Production de l’agent', 'share_with': 'avocat'})
        rid = dr.submit(self.desk, 'Prépare une note de synthèse', '20240101001', 'note')['request']
        deck, tasks = FakeDeck(), FakeTasks()
        out = deck530.sync(self.desk, deck, tasks)                                             # demande en file : carte « En cours »
        self.assertEqual([s['title'] for s in deck.stack_rows], list(deck530.STACKS))
        self.assertEqual(deck.shared, ['avocat'])
        self.assertEqual([deck.stack_name(c['stack']) for c in deck.cards.values()], ['En cours'])
        dr.perform(self.desk, 'docrequest520', {'request': rid})
        self.desk.db.execute("UPDATE jobs SET status='done' WHERE kind='docrequest520'")
        self.desk.db.commit()
        deck530.sync(self.desk, deck, tasks)                                                    # document prêt : « À relire » + tâche
        names = sorted(deck.stack_name(c['stack']) for c in deck.cards.values())
        self.assertEqual(names, ['Fait', 'À relire'])
        self.assertTrue(tasks.puts[-1]['title'].startswith('Relire : '))
        item = ck.review_items(self.desk)[0]['id']
        ck.review(self.desk, {'item': item, 'decision': 'valide'})
        deck530.sync(self.desk, deck, tasks)
        self.assertEqual(sorted(deck.stack_name(c['stack']) for c in deck.cards.values()), ['Fait', 'Fait'])
        self.assertEqual(tasks.puts[-1]['status'], 'COMPLETED')                                # tâche cochée
        deck530.sync(self.desk, deck, tasks)
        self.assertEqual(deck.shared, ['avocat'])                                                # partage une seule fois
        self.assertEqual(dict(deck530.counts(self.desk))['Fait'], 2)
        self.assertIn('Ouvrir dans Deck', ck.day_html(self.desk, '/p'))

    def test_tick_is_throttled_and_settings_validated(self):
        deck530.save_settings(self.desk, {'deck': 'yes', 'board': 'Production de l’agent'})
        self.assertTrue(deck530.tick(self.desk, now=1000))
        self.assertIsNone(deck530.tick(self.desk, now=1100))
        with self.assertRaises(Stop):
            deck530.save_settings(self.desk, {'deck': 'yes', 'board': 'x'})
        with self.assertRaises(Stop):
            deck530.save_settings(self.desk, {'deck': 'yes', 'board': 'Tableau', 'share_with': 'a/b'})


class Http(unittest.TestCase):
    from test_v440_web import WebWorkshopTests as _W
    PATH = _W.PATH
    call = _W.call
    setUp = _W.setUp

    def test_api_routes(self):
        private_json(Path(self.f.c['matters_file']), [ALPHA, BETA])
        r = self.call('/api440/m530/guess', query='text=' + __import__('urllib.parse', fromlist=['quote']).quote('dossier ALPHA'))
        self.assertEqual(json.loads(r['body'])['matter'], '20240101001')
        r = self.call('/api440/m530/part', query='name=feed')
        self.assertIn('Ce que fait l’agent', json.loads(r['body'])['html'])
        self.assertTrue(self.call('/api440/m530/ask', 'POST', {'text': 'Où en est le dossier ALPHA ?'}, csrf=False)['status'].startswith('4'))
        r = self.call('/api440/m530/ask', 'POST', {'text': 'Où en est le dossier ALPHA ?'})
        self.assertTrue(r['status'].startswith('200'), r)
        r = self.call('/api440/m530/nextcloud', 'POST', {'deck': True, 'tasks': False, 'board': 'Production de l’agent', 'share_with': ''})
        self.assertTrue(json.loads(r['body'])['deck'])
        self.assertTrue(self.call('/api440/m530/part', query='name=inconnu')['status'].startswith('4'))


if __name__ == '__main__':
    unittest.main()
