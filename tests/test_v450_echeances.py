"""Échéances 4.5.0 : détection, agenda sans doublon, corrections, contrôle croisé, rappels."""
from datetime import date, datetime
import json
import unittest

from agent.common import Stop
from agent.desk import Desk
from agent import echeances450 as ech
import test_agent as fixtures

NAME = 'DEMO - 20261002 - Signification du jugement.pdf'
PATH = '/Dossiers/DEMO/' + NAME
TEXT = ("ACTE DE COMMISSAIRE DE JUSTICE\nLe jugement du 5 mars 2026 a été signifié le 12 mars 2026 à la société X.\n"
        "Le présent acte est établi pour servir ce que de droit.")
TODAY = date(2026, 10, 2)


class FakeDAV:
    def __init__(self):
        self.store = {}
        self.puts = 0

    def stat(self, path):
        return {'path': path, 'etag': '"e1"', 'size': 10}

    def download(self, item):
        return b'raw'

    def put_event(self, calendar, proposal_id, title, start, end, description, replace=False):
        uid = 'axiorhub-' + proposal_id + '@mail-agent.local'
        if replace and uid not in self.store:
            raise Stop('http_412')
        if not replace and uid in self.store:
            raise Stop('http_412')
        self.puts += 1
        self.store[uid] = {'uid': uid, 'start': start.isoformat(), 'summary': title, 'description': description}
        return uid

    def events(self, urls, start, end, tz):
        return list(self.store.values())


class Base(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.EngineTests('test_observation_has_no_mail_write')
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.desk = Desk(self.f.c)
        self.dav = FakeDAV()
        self.emitted = []

    def one(self, rule='appel_jugement_contentieux', start='2026-09-10', **kw):
        return ech.create_deadline(self.desk, 'DOS-001', rule, start, **kw)


class Detection(unittest.TestCase):
    def events(self, text):
        return ech.extract_start_events(text, TODAY)

    def test_signification_date_is_the_one_after_the_cue(self):
        found = self.events(TEXT)
        self.assertEqual([(e['event'], e['date']) for e in found], [('signification_jugement', date(2026, 3, 12))])
        self.assertEqual(found[0]['rule_id'], 'appel_jugement_contentieux')

    def test_numeric_date_and_refere(self):
        found = self.events("L'ordonnance de référé a été signifiée le 03/09/2026 à Monsieur Y.")
        self.assertEqual([(e['event'], e['date']) for e in found], [('signification_ordonnance_refere', date(2026, 9, 3))])

    def test_declaration_d_appel_and_signification(self):
        found = self.events("Déclaration d'appel en date du 4 septembre 2025 formée par la société X. "
                            "Signification de la déclaration d'appel le 20 septembre 2025.")
        kinds = {(e['event'], e['date']) for e in found}
        self.assertIn(('declaration_appel', date(2025, 9, 4)), kinds)
        self.assertIn(('signification_declaration_appel', date(2025, 9, 20)), kinds)

    def test_bref_delai_needs_its_marker(self):
        self.assertEqual([e for e in self.events("Avis de fixation du 2 septembre 2026 pour l'audience.") if e['event'] == 'avis_fixation_bref_delai'], [])
        found = self.events("Avis de fixation à bref délai (article 905) du 2 septembre 2026.")
        self.assertEqual(found[0]['event'], 'avis_fixation_bref_delai')

    def test_never_guesses_year_nor_future_date(self):
        self.assertEqual(self.events("Le jugement a été signifié le 12 mars."), [])
        self.assertEqual(self.events("Le jugement sera signifié le 12 mars 2027."), [])
        self.assertEqual(self.events("Aucune date. Jugement rendu."), [])

    def test_candidate_names(self):
        self.assertTrue(ech.looks_like_candidate('/a/Signification du jugement.pdf'))
        self.assertTrue(ech.looks_like_candidate('/a/Déclaration d’appel.docx'))
        self.assertFalse(ech.looks_like_candidate('/a/Facture.pdf'))
        self.assertFalse(ech.looks_like_candidate('/a/Jugement.exe'))


class Lifecycle(Base):
    def test_analyze_creates_pending_deadline_and_calendar_once(self):
        args = {'matter': 'DOS-001', 'path': PATH, 'etag': '"e1"'}
        result = ech.analyze(self.desk, args, dav=self.dav, extractor=lambda raw, name: TEXT, today=TODAY)
        self.assertEqual(result['echeances_creees'], 1)
        rows = ech.listing(self.desk)
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row['status'], 'a_confirmer')
        self.assertEqual(row['start_date'], '2026-03-12')
        self.assertEqual(row['due'], '2026-04-13')          # 12 avril 2026 dimanche -> lundi 13
        self.assertEqual(row['source_path'], PATH)
        self.assertIn('signifié le 12 mars 2026', row['source_excerpt'])
        self.assertTrue(any('art. 642' in s for s in row['calc']['steps']))
        self.assertEqual(len(self.dav.store), 1)
        self.assertIn('À CONFIRMER', list(self.dav.store.values())[0]['summary'])
        again = ech.analyze(self.desk, args, dav=self.dav, extractor=lambda raw, name: TEXT, today=TODAY)
        self.assertEqual(again, result)
        # nouvelle version de la même pièce : pas de doublon de ligne ni d'événement
        args2 = dict(args, etag='"e2"')
        r2 = ech.analyze(self.desk, args2, dav=self.dav, extractor=lambda raw, name: TEXT, today=TODAY)
        self.assertEqual(r2['echeances_creees'], 0)
        self.assertEqual(len(ech.listing(self.desk)), 1)
        self.assertEqual(len(self.dav.store), 1)

    def test_no_event_no_deadline(self):
        args = {'matter': 'DOS-001', 'path': PATH, 'etag': '"z"'}
        result = ech.analyze(self.desk, args, dav=self.dav, extractor=lambda raw, name: 'Rien de daté.', today=TODAY)
        self.assertEqual(result['status'], 'blocked')
        self.assertEqual(ech.listing(self.desk), [])
        self.assertEqual(self.dav.store, {})

    def test_confirm_updates_same_event(self):
        row = self.one()
        ech.sync_calendar(self.desk, self.dav, row['id'])
        out = ech.confirm(self.desk, self.dav, row['id'])
        self.assertEqual(out['status'], 'confirmee')
        self.assertEqual(len(self.dav.store), 1)
        self.assertNotIn('À CONFIRMER', list(self.dav.store.values())[0]['summary'])

    def test_confirm_with_other_rule_recomputes(self):
        row = self.one(start='2026-09-10')
        out = ech.confirm(self.desk, self.dav, row['id'], rule_id='appel_jugement_gracieux')
        self.assertEqual(out['due'], '2026-09-25')
        with self.assertRaises(Stop):
            ech.confirm(self.desk, self.dav, row['id'], rule_id='conclusions_appelant')

    def test_manual_correction_needs_reason_is_journaled_and_never_duplicates(self):
        row = self.one()
        ech.sync_calendar(self.desk, self.dav, row['id'])
        with self.assertRaises(Stop):
            ech.correct(self.desk, self.dav, row['id'], '', new_due='2026-10-20')
        out = ech.correct(self.desk, self.dav, row['id'], 'Signification régulière reçue plus tôt', new_due='2026-10-20')
        self.assertEqual(out['status'], 'manuel')
        self.assertEqual(out['due'], '2026-10-20')
        self.assertEqual(len(self.dav.store), 1)
        self.assertTrue(list(self.dav.store.values())[0]['start'].startswith('2026-10-20'))
        log = ech.journal(self.desk, row['id'])
        self.assertEqual([j['action'] for j in log][:2], ['correction', 'creation'])
        self.assertIn('Signification régulière', log[0]['reason'])
        self.assertEqual(json.loads(log[0]['before'])['due'], row['due'])
        # une confirmation ultérieure ne reprend pas la date calculée
        again = ech.confirm(self.desk, self.dav, row['id'])
        self.assertEqual(again['due'], '2026-10-20')
        self.assertEqual(again['status'], 'manuel')

    def test_correct_start_date_recomputes(self):
        row = self.one(start='2026-09-10')
        out = ech.correct(self.desk, self.dav, row['id'], 'Date de signification erronée', start_date='2026-09-30')
        self.assertEqual(out['due'], '2026-10-30')
        self.assertEqual(out['status'], 'confirmee')

    def test_missing_regime_makes_deadline_incomplete_then_completed(self):
        row = ech.create_deadline(self.desk, 'DOS-001', 'conclusions_intime', '2026-02-11')
        self.assertEqual(row['status'], 'a_completer')
        self.assertEqual(row['due'], '')
        self.assertIn('déclaration d’appel', row['error'])
        self.assertEqual(self.dav.store, {})
        out = ech.correct(self.desk, self.dav, row['id'], 'Déclaration d’appel du 5 janvier 2026', regime_date='2026-01-05')
        self.assertEqual(out['due'], '2026-05-11')
        self.assertEqual(len(self.dav.store), 1)

    def test_regime_taken_from_declaration_in_same_matter(self):
        ech.create_deadline(self.desk, 'DOS-001', 'conclusions_appelant', '2025-10-08')
        row = ech.create_deadline(self.desk, 'DOS-001', 'conclusions_intime', '2025-12-10')
        self.assertEqual(row['due'], '2026-03-10')

    def test_old_regime_is_flagged_not_covered(self):
        row = ech.create_deadline(self.desk, 'DOS-001', 'conclusions_appelant', '2024-05-02')
        self.assertEqual(row['status'], 'a_completer')
        self.assertIn('non couvert', row['error'])

    def test_close_and_cancel(self):
        row = self.one()
        ech.sync_calendar(self.desk, self.dav, row['id'])
        with self.assertRaises(Stop):
            ech.close(self.desk, self.dav, row['id'], 'annulee', '')
        ech.close(self.desk, self.dav, row['id'], 'annulee', 'Appel finalement formé')
        self.assertIn('ANNULÉE', list(self.dav.store.values())[0]['summary'])
        self.assertEqual(ech.listing(self.desk), [])
        self.assertEqual(len(ech.listing(self.desk, include_closed=True)), 1)

    def test_calendar_event_deleted_by_user_is_recreated_not_duplicated(self):
        row = self.one()
        ech.sync_calendar(self.desk, self.dav, row['id'])
        self.dav.store.clear()
        self.assertEqual(ech.sync_calendar(self.desk, self.dav, row['id']), 'cree')
        self.assertEqual(len(self.dav.store), 1)

    def test_create_manual(self):
        out = ech.create_manual(self.desk, self.dav, 'DOS-001', 'pourvoi_cassation', '2026-08-30')
        self.assertEqual(out['status'], 'confirmee')
        self.assertEqual(out['due'], '2026-10-30')
        self.assertEqual(out['origin'], 'manuel')
        self.assertEqual(len(self.dav.store), 1)

    def test_unknown_matter_and_rule_refused(self):
        with self.assertRaises(Stop):
            ech.create_deadline(self.desk, 'NOPE', 'appel_jugement_contentieux', '2026-09-10')
        with self.assertRaises(Exception):
            ech.create_deadline(self.desk, 'DOS-001', 'regle_inconnue', '2026-09-10')


class CrossCheckAndReminders(Base):
    def kinds(self, report):
        return sorted(a['kind'] for a in report['anomalies'])

    def test_agenda_absent_divergent_and_ok(self):
        row = self.one(start='2026-09-10')                      # échéance 2026-10-12 (10 oct samedi)
        self.assertEqual(row['due'], '2026-10-12')
        report = ech.cross_check(self.desk, self.dav, TODAY)
        self.assertIn('agenda_absent', self.kinds(report))
        ech.sync_calendar(self.desk, self.dav, row['id'])
        self.assertNotIn('agenda_absent', self.kinds(ech.cross_check(self.desk, self.dav, TODAY)))
        uid = row['id']
        self.dav.store['axiorhub-%s@mail-agent.local' % uid]['start'] = '2026-10-19T09:00:00+02:00'
        self.assertIn('agenda_divergent', self.kinds(ech.cross_check(self.desk, self.dav, TODAY)))

    def test_missing_date_and_contradiction(self):
        ech.create_deadline(self.desk, 'DOS-001', 'conclusions_intime', '2026-02-11')
        a = self.one(start='2026-09-10')
        b = ech.create_deadline(self.desk, 'DOS-001', 'appel_jugement_contentieux', '2026-09-20')
        kinds = self.kinds(ech.cross_check(self.desk, None, TODAY))
        self.assertIn('date_manquante', kinds)
        self.assertEqual(kinds.count('deux_dates'), 2)
        self.assertNotEqual(a['due'], b['due'])

    def test_without_prepared_act_then_with(self):
        row = self.one(start='2026-09-10')                      # 12 octobre : dans 22 jours le 20 sept. -> pas d'alerte
        self.assertNotIn('sans_acte', self.kinds(ech.cross_check(self.desk, None, date(2026, 9, 20))))
        close = date(2026, 10, 8)
        self.assertIn('sans_acte', self.kinds(ech.cross_check(self.desk, None, close)))
        ech.mark_act_prepared(self.desk, row['id'])
        self.assertNotIn('sans_acte', self.kinds(ech.cross_check(self.desk, None, close)))

    def test_overdue_reported(self):
        self.one(start='2026-08-10')
        self.assertIn('depassee', self.kinds(ech.cross_check(self.desk, None, TODAY)))

    def test_reminder_cascade_without_repetition(self):
        row = self.one(start='2026-09-10')                      # due 2026-10-12
        messages = lambda: [dict(r)['message'] for r in self.desk.db.execute('SELECT message FROM live_events_v430 ORDER BY id')]
        self.assertEqual(ech.run_reminders(self.desk, date(2026, 9, 12)), 1)      # J-30
        self.assertEqual(ech.run_reminders(self.desk, date(2026, 9, 13)), 0)
        self.assertEqual(ech.run_reminders(self.desk, date(2026, 9, 28)), 1)      # J-14
        self.assertEqual(ech.run_reminders(self.desk, date(2026, 10, 5)), 2)      # J-7 + alerte sans acte
        self.assertEqual(ech.run_reminders(self.desk, date(2026, 10, 10)), 1)     # J-2
        self.assertEqual(ech.run_reminders(self.desk, date(2026, 10, 10)), 0)
        self.assertEqual(ech.run_reminders(self.desk, date(2026, 10, 12)), 1)     # jour J
        self.assertEqual(ech.run_reminders(self.desk, date(2026, 10, 13)), 1)     # dépassée
        text = ' | '.join(messages())
        for needle in ('J-30', 'J-14', 'J-7', 'J-2', 'jour J', 'ALERTE', 'DÉPASSÉE'):
            self.assertIn(needle, text)
        self.assertEqual(ech.run_reminders(self.desk, date(2026, 10, 14)), 0)
        self.assertIsNotNone(row)

    def test_late_discovery_emits_only_closest_level(self):
        self.one(start='2026-09-10')
        self.assertEqual(ech.run_reminders(self.desk, date(2026, 10, 6)), 2)      # J-7 (pas J-30 ni J-14) + alerte sans acte
        self.assertEqual(ech.run_reminders(self.desk, date(2026, 10, 7)), 0)

    def test_closed_deadline_has_no_reminder(self):
        row = self.one(start='2026-09-10')
        ech.close(self.desk, self.dav, row['id'], 'terminee')
        self.assertEqual(ech.run_reminders(self.desk, date(2026, 10, 11)), 0)

    def test_daily_check_can_be_disabled(self):
        self.one(start='2026-09-10')
        self.desk.setting('automation:deadlines450_enabled', False)
        self.assertIn('abstention', ech.daily_check(self.desk, self.dav, TODAY))
        self.desk.setting('automation:deadlines450_enabled', True)
        self.assertEqual(ech.daily_check(self.desk, self.dav, TODAY)['remote_write'], False)


class Inventory(Base):
    def test_inventory_queues_once_and_ignores_unrelated(self):
        items = {PATH: {'etag': '"e1"', 'size': 10, 'modified': ''}, '/Dossiers/DEMO/Facture.pdf': {'etag': '"x"'}}
        self.assertEqual(ech.observe_inventory(self.desk, 'DOS-001', items), 1)
        self.assertEqual(ech.observe_inventory(self.desk, 'DOS-001', items), 0)
        pending = self.desk.db.execute("SELECT COUNT(*) FROM jobs WHERE kind='analyze_deadline450'").fetchone()[0]
        self.assertEqual(pending, 1)
        self.desk.setting('automation:deadlines450_enabled', False)
        self.assertEqual(ech.observe_inventory(self.desk, 'DOS-001', {PATH: {'etag': '"e9"'}}), 0)


if __name__ == '__main__':
    unittest.main()
