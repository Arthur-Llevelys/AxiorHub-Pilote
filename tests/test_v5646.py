"""5.6.46 — écritures longues relevées sur le serveur (14 octobre 2026) : aucune lecture réseau ni calcul long dans une transaction
d'écriture (rapprochement des courriels, agenda). Données fictives uniquement."""
from datetime import datetime, timedelta, timezone
import time
import unittest
from unittest.mock import patch

import test_v530 as fixtures
from agent import portfolio, workplan


class _Mail:
    def __init__(self, flags):
        self.flags = flags


class RapprochementSansTransactionOuverte(fixtures.Base):
    KEYS = ('a' * 64, 'b' * 64, 'c' * 64)

    def setUp(self):
        super().setUp()
        portfolio.ensure_schema(self.desk)
        for key in self.KEYS:
            self.desk.db.execute('INSERT OR REPLACE INTO work_items VALUES(?,?,?,?,?,?,?,?,?,?)',
                                 (key, 'needs_action', 'review', '', 'DOS-1', 'Objet fictif', 'client@example.test',
                                  datetime.now(timezone.utc).isoformat(), datetime.now(timezone.utc).isoformat(), ''))
        self.desk.db.commit()

    def test_lectures_imap_hors_transaction_et_decision_de_l_avocat_preservee(self):
        seen = []
        desk = self.desk

        class Box:
            def fetch(self, folder, uid, headers_only=False):
                seen.append(desk.db.in_transaction)              # aucune écriture ouverte pendant une lecture réseau
                if uid == RapprochementSansTransactionOuverte.KEYS[2]:
                    other = __import__('sqlite3').connect(desk.c['state_dir'] + '/desk.sqlite3', timeout=5)
                    other.execute("UPDATE work_items SET state='archived' WHERE mail_key=?", (RapprochementSansTransactionOuverte.KEYS[1],))
                    other.commit(); other.close()                  # l'avocat archive la carte pendant le rapprochement
                return _Mail({'\\Answered'})

            def close(self):
                pass
        with patch('agent.desk.report_for', lambda c, key: {'source_mailbox': 'INBOX', 'source_uid': key}), \
                patch('agent.integration.sync_work_items', lambda d: None):
            result = portfolio.reconcile_work_items(desk, Box())
        self.assertEqual(seen, [False, False, False])
        self.assertEqual(result['handled'], 3)
        states = dict(desk.db.execute('SELECT mail_key,state FROM work_items'))
        self.assertEqual(states[self.KEYS[0]], 'handled')
        self.assertEqual(states[self.KEYS[1]], 'archived')            # changement de l'avocat jamais écrasé
        self.assertEqual(states[self.KEYS[2]], 'handled')


class AgendaTransactionCourte(fixtures.Base):
    def test_rattachement_calcule_avant_l_ecriture_et_rapide(self):
        matters = [{'id': 'DOS-%04d' % i, 'path': '/Dossiers/CLIENT FICTIF %04d - DOS-%04d' % (i, i), 'client_name': 'Client fictif %d' % i,
                    'references': ['RG %04d' % i], 'aliases': []} for i in range(300)]
        start = datetime.now(timezone.utc).replace(microsecond=0)
        events = [{'uid': 'u%d' % i, 'recurrence_id': '', 'source_url': 'https://cal.example.test/c1/', 'href': '/e%d.ics' % i, 'etag': '',
                   'summary': 'Audience DOS-%04d' % (i % 300), 'description': 'Rendez-vous fictif', 'location': '',
                   'start': (start + timedelta(hours=i)).isoformat(), 'end': (start + timedelta(hours=i, minutes=30)).isoformat(), 'busy': True}
                  for i in range(400)]
        desk = self.desk
        seen = []
        real = workplan._matter_candidates

        def watched(*args, **kwargs):
            seen.append(desk.db.in_transaction)
            return real(*args, **kwargs)

        class Client:
            def events(self, urls, a, b, tz):
                return events

            def calendar_url(self, url):
                return url
        with patch('agent.workplan.load_matters', lambda c: matters), patch('agent.workplan._calendar_urls', lambda d: ['https://cal.example.test/c1/']), \
                patch('agent.workplan._matter_candidates', watched):
            began = time.monotonic()
            out = workplan.calendar_events(desk, start.isoformat(), (start + timedelta(days=30)).isoformat(), dav=Client())
            elapsed = time.monotonic() - began
        self.assertFalse(any(seen))
        self.assertEqual(out['counts']['linked'], out['counts']['all'])
        self.assertLess(elapsed, 20)

    def test_motifs_prepares_meme_resultat(self):
        matters = [{'id': 'DOS-0001', 'path': '/Dossiers/ALPHA FICTIF - DOS-0001', 'client_name': 'Alpha Fictif', 'references': ['RG 2026/0001'], 'aliases': []},
                   {'id': 'DOS-0002', 'path': '/Dossiers/BETA FICTIF - DOS-0002', 'client_name': 'Beta Fictif', 'references': [], 'aliases': ['Bêta']}]
        prepared = workplan.prepare_matters(matters)
        for text in ('Audience DOS-0001', 'Rendez-vous Alpha Fictif', 'Appel Bêta et Alpha Fictif', 'Sans dossier'):
            self.assertEqual(workplan._matter_candidates(matters, text), workplan._matter_candidates(matters, text, prepared), text)


if __name__ == '__main__':
    unittest.main(verbosity=2)
