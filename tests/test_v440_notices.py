"""Procedural notices: detection, extraction, calendar, information draft."""
from datetime import date, datetime, timezone
import json
import unittest

from agent.common import Stop
from agent.desk import Desk
from agent import notices440 as n
import test_agent as fixtures

NAME = 'XXX - YYY - 20261002 - Avis de renvoi.pdf'
TEXT = ('TRIBUNAL DE COMMERCE DE LYON\nRG 2026/001234\n\nAVIS DE RENVOI\n\n'
        "L'affaire est renvoyée à l'audience du 12 décembre 2026 à 9h30 pour dépôt des conclusions de Maître BBB. "
        'Le dossier sera ensuite plaidé le 15 mars 2027.')


class FakeDAV:
    def __init__(self):
        self.puts = []
        self.existing = []

    def stat(self, path):
        return {'path': path, 'etag': '"e1"', 'size': 10}

    def download(self, item):
        return b'raw'

    def events(self, urls, start, end, tz):
        return list(self.existing)

    def put_event(self, calendar, proposal_id, title, start, end, description, replace=False):
        self.puts.append((title, start, description))
        return 'axiorhub-' + proposal_id + '@mail-agent.local'


class Box:
    drafts = []

    def __init__(self, cfg):
        pass

    def find_own_draft(self, mid):
        return any(d['Message-ID'] == mid for d in Box.drafts)

    def verify_draft(self, d):
        if not self.find_own_draft(d['Message-ID']):
            raise Stop('brouillon_non_retrouve')
        return {'folder': 'Drafts', 'uid': '1'}

    def append_draft(self, d):
        Box.drafts.append(d)

    def close(self):
        pass


class NoticeTests(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.EngineTests('test_observation_has_no_mail_write')
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.desk = Desk(self.f.c)
        self.dav = FakeDAV()
        Box.drafts = []

    def run_job(self, **kw):
        args = {'matter': 'DOS-001', 'path': '/Dossiers/DEMO/' + NAME, 'etag': '"e1"'}
        return n.analyze(self.desk, args, dav=self.dav, mailbox_factory=Box, extractor=lambda raw, name: TEXT)

    def test_name_detection(self):
        self.assertEqual(n.classify_name(NAME), 'renvoi')
        self.assertEqual(n.classify_name('Calendrier de procédure.docx'), 'calendrier')
        self.assertEqual(n.classify_name('Convocation à audience.pdf'), 'convocation')
        self.assertTrue(n.looks_like_notice('/a/Avis de renvoi.pdf'))
        self.assertFalse(n.looks_like_notice('/a/Facture.pdf'))
        self.assertFalse(n.looks_like_notice('/a/Avis de renvoi.exe'))

    def test_filename_info(self):
        info = n.filename_info(NAME)
        self.assertEqual(info['date'], date(2026, 10, 2))
        self.assertEqual(info['label'], 'XXX / YYY')
        self.assertEqual(info['title'], 'Avis de renvoi')

    def test_extraction(self):
        r = n.extract_notice(TEXT, date(2026, 10, 2), kind_hint='renvoi')
        dates = {e['date']: e for e in r['events']}
        self.assertIn(date(2026, 12, 12), dates)
        e = dates[date(2026, 12, 12)]
        self.assertEqual(e['time'], '09:30')
        self.assertIn('conclusions', e['roles'])
        self.assertEqual(r['act_by'], 'BBB')
        self.assertEqual(r['rg'], '2026/001234')
        self.assertEqual(n.describe(e, r['act_by']), 'Renvoi – dépôt des conclusions de Me BBB')
        self.assertEqual(dates[date(2026, 3, 15) if False else date(2027, 3, 15)]['role'], 'plaidoirie')

    def test_never_invents_dates(self):
        r = n.extract_notice('Avis de renvoi. Aucune date ici.', date(2026, 10, 2), kind_hint='renvoi')
        self.assertEqual(r['events'], [])
        past = n.extract_notice('Audience du 3 mars 2020.', date(2026, 10, 2), kind_hint='renvoi')
        self.assertEqual(past['events'], [])

    def test_postulant_gets_confrere_draft_and_calendar(self):
        n.save_matter_profile(self.desk, 'DOS-001', 'postulant', 'dominus@example.test', 'Me Dominus')
        result = self.run_job()
        self.assertEqual(result['recipient_kind'], 'confrere')
        self.assertEqual(result['brouillon_imap'], 'verifie')
        self.assertEqual(len(Box.drafts), 1)
        d = Box.drafts[0]
        self.assertEqual(d['To'], 'dominus@example.test')
        self.assertIn('12 décembre 2026', d.get_content())
        self.assertIn('samedi 12 décembre 2026', d.get_content())
        self.assertTrue(any('Renvoi' in p[0] and p[1].day == 12 and p[1].hour == 9 for p in self.dav.puts))
        self.assertTrue(d['Message-ID'].startswith('<axiorhub-'))

    def test_idempotent(self):
        self.run_job()
        self.run_job()
        self.assertEqual(len(Box.drafts), 1)
        self.assertEqual(len(self.dav.puts), 2)

    def test_no_profile_makes_internal_note(self):
        result = self.run_job()
        self.assertEqual(result['recipient_kind'], 'interne')
        self.assertEqual(Box.drafts[0]['To'], 'cabinet@example.test')
        self.assertTrue(Box.drafts[0]['Subject'].startswith('[À traiter]'))

    def test_existing_calendar_event_not_duplicated(self):
        self.dav.existing = [{'start': '2026-12-12T09:30:00+01:00', 'summary': 'Audience Client DEMO'}]
        self.f.matters[0]['aliases']
        r = self.run_job()
        created = [c for c in r['calendar'] if c['calendar'] == 'cree']
        self.assertEqual(len(created), 1)

    def test_blocked_without_dates(self):
        n.analyze  # noqa
        args = {'matter': 'DOS-001', 'path': '/Dossiers/DEMO/' + NAME, 'etag': 'z'}
        r = n.analyze(self.desk, args, dav=self.dav, mailbox_factory=Box, extractor=lambda raw, name: 'rien')
        self.assertEqual(r['status'], 'blocked')
        self.assertEqual(Box.drafts, [])
        self.assertEqual(self.dav.puts, [])

    def test_invalid_role_and_partner(self):
        with self.assertRaises(Stop):
            n.save_matter_profile(self.desk, 'DOS-001', 'roi')
        with self.assertRaises(Stop):
            n.save_matter_profile(self.desk, 'DOS-001', 'postulant', 'pas une adresse')
        with self.assertRaises(Stop):
            n.save_matter_profile(self.desk, 'INCONNU', 'postulant')

    def test_inventory_queues_recent_notices_only_once(self):
        items = {'/Dossiers/DEMO/' + NAME: {'etag': '"a"', 'modified': datetime.now(timezone.utc).strftime('%a, %d %b %Y %H:%M:%S GMT')},
                 '/Dossiers/DEMO/Facture.pdf': {'etag': '"b"', 'modified': ''},
                 '/Dossiers/DEMO/Ancien - Avis de renvoi.pdf': {'etag': '"c"', 'modified': 'Mon, 01 Jan 2024 10:00:00 GMT'}}
        self.assertEqual(n.observe_inventory(self.desk, 'DOS-001', items), 1)
        self.assertEqual(n.observe_inventory(self.desk, 'DOS-001', items), 0)
        kinds = [r[0] for r in self.desk.db.execute("SELECT kind FROM jobs")]
        self.assertEqual(kinds.count('analyze_notice440'), 1)

    def test_job_is_registered(self):
        from agent.desk import JOBS
        from agent.production420 import JOB_META
        self.assertIn('analyze_notice440', JOBS)
        self.assertIn('analyze_notice440', JOB_META)


if __name__ == '__main__':
    unittest.main()
