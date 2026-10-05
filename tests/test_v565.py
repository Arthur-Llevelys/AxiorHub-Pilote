"""5.6.5 : la file n'est plus bloquée par les travaux automatiques, la surveillance n'inonde plus la file avec d'anciens dossiers,
rôles des correspondants complétés, courriel d'origine et emplacement des documents dans le volet de relecture."""
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from agent import cockpit530 as ck, docrequest520 as dr, watch430
from agent.common import Stop, load_matters, private_json
from agent.desk import AUTOMATIC_LIMIT, AUTOMATIC_PER_KIND, ROLES, THIRD_PARTY_ROLES
import test_v520_docs as td
import test_v530 as t530

ROOT = Path(__file__).resolve().parents[1]


class Queue(t530.Base):
    def test_automatic_flood_never_blocks_the_lawyer(self):
        self.desk.db.execute('DELETE FROM jobs')
        self.desk.db.commit()
        for i in range(AUTOMATIC_PER_KIND):
            self.desk.enqueue('index', {'matter': 'M%d' % i}, priority=50)
        with self.assertRaises(Stop):                                     # 9e indexation automatique : reportée
            self.desk.enqueue('index', {'matter': 'M99'}, priority=50)
        kinds = ('monitor_matter', 'extract_facts460', 'refresh_brief', 'health', 'sync_legal_memory', 'classify_portfolio', 'reconcile_inbox')
        n = AUTOMATIC_PER_KIND
        for kind in kinds:
            for i in range(AUTOMATIC_PER_KIND):
                if n >= AUTOMATIC_LIMIT:
                    break
                try:
                    self.desk.enqueue(kind, {'n': i}, priority=55)
                    n += 1
                except Stop:
                    pass
        self.assertEqual(self.desk.db.execute("SELECT COUNT(*) FROM jobs WHERE status='pending' AND priority>=30").fetchone()[0], AUTOMATIC_LIMIT)
        with self.assertRaises(Stop):
            self.desk.enqueue('monitor_matter', {'matter': 'autre'}, priority=55)
        # la demande de l'avocat entre toujours, file automatique pleine ou non
        self.assertTrue(self.desk.enqueue('prepare_reply', {'key': 'a' * 64}))
        self.assertTrue(self.desk.enqueue('docrequest520', {'request': 'x'}, priority=0))
        # la suite d'un travail terminé (lot suivant d'indexation…) n'est jamais refusée : elle remplace le travail fini
        self.assertTrue(self.desk._then('index', {'matter': 'M99'}))
        src = (ROOT / 'agent' / 'desk.py').read_text(encoding='utf-8')
        follow = src[src.index('self.active_job_id=None;trace480.end()'):src.index("def worker(c,worker_id='1'):")]
        self.assertNotIn('self.enqueue(', follow)

    def test_index_all_with_saturated_queue_stops_instead_of_looping(self):
        self.desk.db.execute('DELETE FROM jobs')
        for i in range(100):
            self.desk.db.execute("INSERT INTO jobs(kind,args,status,created,priority) VALUES('chat',?,'pending',?,0)", ('{"n":%d}' % i, self.desk.now()))
        self.desk.db.commit()
        with patch('agent.portfolio.portfolio_rows', return_value=[{'matter': m['id']} for m in load_matters(self.f.c)]):
            with self.assertRaises(Stop):
                self.desk.perform('index_all', {})


class Surveillance(t530.Base):
    def items(self, days_ago):
        stamp = format_datetime(datetime.now(timezone.utc) - timedelta(days=days_ago), usegmt=True)
        return {'/Dossiers/DEMO/a.pdf': {'etag': '"%d"' % days_ago, 'modified': stamp, 'error': ''}}

    def check(self, items):
        class Fake:
            refused = []
            def inventory_step(self, path, state=None):
                return {'root': path, 'pending': [], 'files': items, 'visited': 1}, True
        with patch('agent.watch430.heartbeat'), patch('agent.watch430.progress'), patch('agent.notices440.observe_inventory'), \
                patch('agent.echeances450.observe_inventory'):
            return watch430.documents_check(self.desk, Fake())

    def queued(self):
        return self.desk.db.execute("SELECT COUNT(*) FROM jobs WHERE kind IN ('index','monitor_matter','extract_facts460') AND status='pending'").fetchone()[0]

    def test_first_inventory_of_old_matters_queues_nothing(self):
        self.desk.db.execute('DELETE FROM jobs')
        self.desk.db.commit()
        out = self.check(self.items(800))
        self.assertEqual(out['changed'], 0)
        self.assertEqual(self.queued(), 0)                                # avant : 3 travaux par dossier, dossiers de 2019 compris
        self.check(self.items(700))                                       # réorganisation d'un dossier ancien : toujours rien
        self.assertEqual(self.queued(), 0)

    def test_recent_change_queues_analysis_and_a_full_queue_is_not_a_failure(self):
        self.desk.db.execute('DELETE FROM jobs')
        self.desk.db.commit()
        self.check(self.items(800))
        out = self.check(self.items(2))
        self.assertGreaterEqual(out['changed'], 1)
        self.assertGreater(self.queued(), 0)
        with patch.object(self.desk, 'enqueue', side_effect=Stop('file_attente_pleine')):
            out = self.check(self.items(1))                               # pas d'exception : la surveillance continue
        self.assertIn('changed', out)


class Roles(t530.Base):
    def test_new_correspondent_categories(self):
        for key in ('juridiction', 'expert', 'administration', 'commissaire_justice', 'autre_partie', 'personnel'):
            self.assertIn(key, ROLES)
            self.assertIn(key, THIRD_PARTY_ROLES)                          # rien du dossier ne part sans validation
        self.assertNotIn('client', THIRD_PARTY_ROLES)
        matter = {**td.ALPHA, 'correspondents': [{'email': 'greffe@example.test', 'role': 'juridiction'},
                                                  {'email': 'expert@example.test', 'role': 'expert'}]}
        private_json(Path(self.f.c['matters_file']), [matter, td.BETA])
        self.assertEqual({p['role'] for p in load_matters(self.f.c)[0]['correspondents']}, {'juridiction', 'expert'})
        self.desk.propose(load_matters(self.f.c)[0], 'contentieux@greffe.example.test', {'type': 'score_automatique', 'source': 'Convocation',
                          'date': self.desk.now(), 'indice': '90 %', 'limite': 'Rôle à confirmer.'})
        page = self.request('/associations')['body']
        for label in ('Juridiction / greffe', 'Expert judiciaire ou amiable', 'Administration / organisme public',
                      'Commissaire de justice (huissier)', 'Contact personnel (hors dossier)'):
            self.assertIn(label, page)
        src = (ROOT / 'agent' / 'engine.py').read_text(encoding='utf-8')
        self.assertIn("if role in THIRD_PARTY_ROLES and", src)
        self.assertIn("finish('review', 'contact_personnel_hors_dossier')", src)


class Drawer(t530.Base):
    def test_draft_shows_and_opens_the_original_mail(self):
        self.drafts['items'] = [{'uid': '12', 'subject': 'Re: Audience', 'to': 'confrere@example.test', 'date': self.desk.now(), 'agent': True,
                                 'agent_key': ''}]
        item = ck.review_items(self.desk)[0]['id']
        draft = {'uid': '12', 'body': 'Cher Confrère', 'to': 'confrere@example.test',
                 'source': {'uid': '7', 'mailbox': 'INBOX', 'sender': 'confrere@example.test', 'subject': 'Audience du 5 octobre',
                            'date': '2026-10-05T07:00:00+00:00', 'text': 'Mon cher Confrère, je vous confirme la date.', 'key': 'a' * 64}}
        with patch('agent.drafts440.get_draft', return_value=draft), \
                patch('agent.workstation.external_links', return_value={'roundcube': 'https://webmail.example.test/'}):
            html = ck.item_html(self.desk, '/p', item)
        self.assertIn('Courriel d’origine : confrere@example.test', html)
        self.assertIn('je vous confirme la date', html)
        self.assertIn('_mbox=INBOX&amp;_uid=7&amp;_action=show', html)
        self.assertIn('_uid=12&amp;_action=edit', html)
        self.assertIn('/p/courriels?uid=12', html)
        js = (ROOT / 'agent' / 'static' / 'v440.js').read_text(encoding='utf-8')
        self.assertIn("new URLSearchParams(location.search).get('uid')", js)

    def test_document_location_and_direct_edit(self):
        rid = dr.submit(self.desk, 'Prépare une note de synthèse', '20240101001', 'note')['request']
        dr.perform(self.desk, 'docrequest520', {'request': rid})
        item = next(x for x in ck.review_items(self.desk) if x['type'] == 'Note')
        html = ck.item_html(self.desk, '/p', item['id'])
        self.assertIn('<strong>Emplacement :</strong> DEMO', html)
        self.assertIn('Modifier le document', html)
        self.assertIn('Éditeur AxiorHub', html)
        text, _ = ck._follow_up(self.desk, '/p', 'docreq:' + rid)
        self.assertIn('prêt : il vous attend dans « À relire ». Enregistré dans DEMO', text)
        self.assertIn('data-open-path="/Dossiers/DEMO/', text)


if __name__ == '__main__':
    unittest.main()
