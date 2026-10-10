"""4.6.0 : cockpit à cinq cartes, fiche de dossier, chronologie, mémoire de dossier validée."""
from datetime import date, datetime, timezone
import json
import re
import unittest
from unittest.mock import patch

from agent.common import Stop
from agent.desk import Desk
from agent import cockpit460, echeances450 as ech, facts460, fiche460, legal_memory as lm
from agent.index import DocumentIndex
from test_v440_web import WebWorkshopTests as Base0
from test_v450_echeances import FakeDAV
import test_agent as fixtures

TODAY = date(2026, 10, 2)
NOW = datetime(2026, 10, 2, 8, 0, tzinfo=timezone.utc)
PATH = '/Dossiers/DEMO/Assignation.pdf'
TEXT = ("TRIBUNAL DE COMMERCE DE TOULON\nRG 2026/001234\n\nASSIGNATION DÉLIVRÉE LE 4 septembre 2026\n"
        "La SAS FIMEX, représentée par son président, demande la condamnation de M. Jean BERTIN au paiement de la somme de 12 345,67 euros "
        "au titre du solde de la facture. Le jugement du 5 mars 2026 a été signifié le 12 mars 2026. "
        "Il est rappelé que le contrat a été conclu le 3 janvier 2025 entre les parties pour un prix de 8 000 €. " * 2)


class Base(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.EngineTests('test_observation_has_no_mail_write')
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.desk = Desk(self.f.c)
        lm.ensure_schema(self.desk)
        self.dav = FakeDAV()

    def work_item(self, key, state, subject, sender='client@example.test', received='2026-09-25T09:00:00+00:00', matter='DOS-001'):
        self.desk.db.execute('INSERT INTO work_items VALUES(?,?,?,?,?,?,?,?,?,?)',
                             (key, state, '', '', matter, subject, sender, received, received, ''))
        self.desk.db.commit()

    def index_doc(self, path=PATH, text=TEXT, matter='DOS-001'):
        idx = DocumentIndex(self.desk.c['state_dir'])
        idx.db.execute('INSERT OR REPLACE INTO docs VALUES(?,?,?,?,?,?)', (matter, path, 'e1', '2026-09-20T10:00:00+00:00', text, ''))
        idx.db.commit()


class Facts(Base):
    def test_extraction_cites_and_never_invents(self):
        facts, quality = facts460.extract_facts(TEXT, PATH)
        self.assertGreater(quality, 0.8)
        by = {}
        for record, source in facts:
            by.setdefault(record['record_type'], []).append((record, source))
            self.assertIn(record['source_ids'][0], source['id'])
            self.assertTrue(source['excerpt'])
        self.assertEqual(by['case_number'][0][0]['title'], 'RG 2026/001234')
        self.assertIn('TOULON', by['jurisdiction'][0][0]['title'].upper())
        amounts = {r['title'] for r, _ in by['amount']}
        self.assertIn('Montant : 12 345,67 €', amounts)
        self.assertIn('Montant : 8 000 €', amounts)
        events = {r['title']: r['event_date'] for r, _ in by['event']}
        self.assertEqual(events['Jugement du 5/03/2026'], '2026-03-05')
        self.assertEqual(events['Contrat du 3/01/2025'], '2025-01-03')
        self.assertTrue(any('SAS FIMEX' in r['title'] for r, _ in by['party']))
        for record, source in facts:      # chaque valeur figure dans l'extrait cité
            if record['record_type'] == 'case_number':
                self.assertIn('2026/001234', source['excerpt'])

    def test_no_fact_without_context_or_year(self):
        facts, _ = facts460.extract_facts("Le jugement du 5 mars a été rendu. Prévoir 500 euros de timbres.", PATH)
        self.assertEqual([r['record_type'] for r, _ in facts if r['record_type'] in ('amount', 'event')], [])

    def test_poor_scan_gives_low_confidence(self):
        noisy = ("Tr1bun@l d3 c0mm3rc3 ~~ ¦¦ RG 2026/001234 |||| l' as5ign@tion ;;; ¤¤ x y z q w ?? "
                 "som me de 1 200 euros condamn ## ¦ ¤ ~ ^ ` ¬ ¦ ¦ ¦ ¤ ¤ ¤ ~~~ ^^^ ``` ¦¦¦ ¤¤¤ ~~~ ^^^ ###")
        self.assertLess(facts460.text_quality(noisy), 0.7)
        facts, quality = facts460.extract_facts(noisy, PATH)
        self.assertTrue(facts)
        self.assertTrue(all(r['confidence'] == 'low' for r, _ in facts))
        self.assertEqual(facts460.text_quality('trop court'), 0.0)

    def test_proposed_facts_are_not_validated_and_not_reusable(self):
        facts460.propose(self.desk, 'DOS-001', PATH, TEXT)
        rows = lm.memory_records(self.desk, 'DOS-001')
        self.assertTrue(rows)
        self.assertTrue(all(r['status'] == 'suggested' for r in rows))
        self.assertEqual(facts460.reusable_facts(self.desk, 'DOS-001'), [])
        self.assertEqual(lm.validated_fact_sources(self.desk, 'DOS-001', 'RG'), [])

    def test_validate_correct_and_reuse(self):
        facts460.propose(self.desk, 'DOS-001', PATH, TEXT)
        amount = next(r for r in lm.memory_records(self.desk, 'DOS-001') if r['title'] == 'Montant : 12 345,67 €')
        facts460.decide(self.desk, 'DOS-001', amount['id'], 'validate', 'Montant principal de 12 345,67 € (hors intérêts)')
        reuse = lm.validated_fact_sources(self.desk, 'DOS-001', 'montant')
        self.assertEqual(len(reuse), 1)
        self.assertIn('hors intérêts', reuse[0]['excerpt'])
        self.assertIn('validé par l’avocat', reuse[0]['excerpt'])
        # une nouvelle analyse de la même pièce ne défait pas la décision
        facts460.propose(self.desk, 'DOS-001', PATH, TEXT)
        again = next(r for r in lm.memory_records(self.desk, 'DOS-001') if r['id'] == amount['id'])
        self.assertEqual(again['status'], 'validated')
        self.assertIn('hors intérêts', again['content'])

    def test_refused_fact_is_never_reused_nor_reproposed(self):
        facts460.propose(self.desk, 'DOS-001', PATH, TEXT)
        row = next(r for r in lm.memory_records(self.desk, 'DOS-001') if r['title'] == 'Montant : 8 000 €')
        facts460.decide(self.desk, 'DOS-001', row['id'], 'reject')
        count = len(lm.memory_records(self.desk, 'DOS-001'))
        facts460.propose(self.desk, 'DOS-001', PATH, TEXT)
        facts460.propose(self.desk, 'DOS-001', '/Dossiers/DEMO/Autre.pdf', TEXT)       # autre pièce, même fait
        rows = lm.memory_records(self.desk, 'DOS-001')
        refused = [r for r in rows if r['title'] == 'Montant : 8 000 €']
        self.assertEqual([r['status'] for r in refused], ['disputed'])
        self.assertNotIn(row['id'], [x['id'] for x in lm.memory_records(self.desk, 'DOS-001', ['suggested', 'validated', 'pinned'])])
        self.assertEqual([x for x in lm.validated_fact_sources(self.desk, 'DOS-001', '8000') if '8000' in x['excerpt']], [])
        self.assertEqual([x for x in lm.memory_sources(self.desk, 'DOS-001', '8000', 20) if '8000' in x['excerpt']], [])
        self.assertGreaterEqual(len(rows), count)
        from agent.autonomy import _memory_sources
        memory = _memory_sources(self.desk, 'DOS-001')[4]
        self.assertNotIn(row['id'], [m['id'] for m in memory])

    def test_decide_wrong_matter_or_action_refused(self):
        facts460.propose(self.desk, 'DOS-001', PATH, TEXT)
        rid = lm.memory_records(self.desk, 'DOS-001')[0]['id']
        with self.assertRaises(Stop):
            facts460.decide(self.desk, 'AUTRE', rid, 'validate')
        with self.assertRaises(Stop):
            facts460.decide(self.desk, 'DOS-001', rid, 'supprimer')

    def test_scan_job_reads_indexed_documents_and_flags_unreadable(self):
        self.index_doc()
        self.index_doc('/Dossiers/DEMO/Scan.pdf', 'xx ¤¤ ~~ ' * 30)
        res = facts460.scan_matter(self.desk, {'matter': 'DOS-001'})
        self.assertEqual(res['status'], 'prepared')
        self.assertEqual(res['documents'], 2)
        self.assertIn('/Dossiers/DEMO/Scan.pdf', res['documents_peu_lisibles'])
        self.assertIn('peu lisible', res['message'])
        with self.assertRaises(Stop):
            facts460.scan_matter(self.desk, {'matter': 'NOPE'})


class CockpitCards(Base):
    def seed(self, extra_mail=4):
        d = ech.create_deadline(self.desk, 'DOS-001', 'appel_jugement_contentieux', '2026-08-20')              # échue le 21/09
        ech.create_deadline(self.desk, 'DOS-001', 'pourvoi_cassation', '2026-08-04')                           # 5/10
        ech.create_deadline(self.desk, 'DOS-001', 'appel_ordonnance_refere', '2026-09-24')                     # 9/10
        for i in range(extra_mail):
            self.work_item('k%02d' % i + 'a' * 60, 'needs_action', 'Client %d' % i, received='2026-09-%02dT09:00:00+00:00' % (20 + i))
        for i in range(3):
            self.work_item('d%02d' % i + 'b' * 60, 'draft_ready', 'Brouillon %d' % i)
        lm._timeline_put(self.desk, 'DOS-001', 'calendar', '2026-10-05T09:30:00+00:00', 'Audience de plaidoirie', '', 'cal1', 'calendar', 'Agenda Nextcloud')
        self.desk.db.commit()
        return d

    def test_never_more_than_five_cards_and_counter(self):
        self.seed()
        data = cockpit460.build(self.desk, TODAY, NOW)
        self.assertEqual(len(data['cards']), 5)
        self.assertEqual(data['folded_count'], data['total'] - 5)
        self.assertGreaterEqual(data['folded_count'], 5)
        self.assertEqual(len(data['folded']), data['folded_count'])

    def test_ordering_by_urgency(self):
        self.seed()
        cards = cockpit460.build(self.desk, TODAY, NOW)['cards']
        kinds = [c['kind'] for c in cards]
        self.assertEqual(kinds[0], 'delai')
        self.assertIn('dépassée', cards[0]['urgency'])
        scores = [c['score'] for c in cards]
        self.assertEqual(scores, sorted(scores, reverse=True))
        self.assertIn('audience', kinds)
        self.assertLess(kinds.index('delai'), kinds.index('attente'))

    def test_every_card_leads_to_a_work_screen(self):
        self.seed()
        data = cockpit460.build(self.desk, TODAY, NOW)
        for c in data['cards'] + data['folded']:
            self.assertTrue(c['href'].startswith('/'), c)
            self.assertTrue(c['action_label'])
        hrefs = {c['kind']: c['href'] for c in data['cards'] + data['folded']}
        self.assertTrue(hrefs['delai'].startswith('/echeances#e-'))
        self.assertTrue(hrefs['attente'].startswith('/mail?key='))
        self.assertEqual(hrefs['brouillon'], '/courriels')
        self.assertTrue(hrefs['audience'].startswith('/fiche?matter='))

    def test_far_deadlines_and_past_events_are_not_cards(self):
        ech.create_deadline(self.desk, 'DOS-001', 'conclusions_appelant', '2026-09-20', regime_date='2026-09-20')   # trois mois : décembre
        lm._timeline_put(self.desk, 'DOS-001', 'calendar', '2026-09-01T09:30:00+00:00', 'Ancienne audience', '', 'cal0', 'calendar', 'Agenda')
        lm._timeline_put(self.desk, 'DOS-001', 'calendar', '2026-12-01T09:30:00+00:00', 'Audience lointaine', '', 'cal9', 'calendar', 'Agenda')
        self.desk.db.commit()
        titles = [c['title'] for c in cockpit460.build(self.desk, TODAY, NOW)['cards']]
        self.assertEqual(titles, [])

    def test_suggested_facts_card_and_empty_state(self):
        self.assertEqual(cockpit460.build(self.desk, TODAY, NOW)['total'], 0)
        facts460.propose(self.desk, 'DOS-001', PATH, TEXT)
        cards = cockpit460.build(self.desk, TODAY, NOW)['cards']
        self.assertEqual([c['kind'] for c in cards], ['faits'])
        self.assertIn('#faits', cards[0]['href'])


class FicheAndTimeline(Base):
    def setUp(self):
        super().setUp()
        self.index_doc()
        facts460.propose(self.desk, 'DOS-001', PATH, TEXT)
        lm._timeline_put(self.desk, 'DOS-001', 'email_received', '2026-09-28T10:00:00+00:00', 'Re: FIMEX', 'Bonjour', 'mk1', 'email_received', 'INBOX')
        lm._timeline_put(self.desk, 'DOS-001', 'email_sent', '2026-09-29T10:00:00+00:00', 'Réponse FIMEX', 'Voici', 'mk2', 'email_sent', 'Sent')
        lm._timeline_put(self.desk, 'DOS-001', 'document', '2026-09-20T10:00:00+00:00', 'Assignation.pdf', '', 'sd', 'document', PATH)
        ech.create_deadline(self.desk, 'DOS-001', 'appel_jugement_contentieux', '2026-09-10', source_path=PATH, excerpt='signifié le 10 septembre 2026')
        self.work_item('w' * 64, 'draft_ready', 'Projet de réponse')
        self.desk.db.commit()

    def test_every_information_has_a_source_and_unvalidated_is_marked(self):
        f = fiche460.build_fiche(self.desk, 'DOS-001', TODAY)
        self.assertEqual(f['case_number']['value'], 'Numéro de répertoire général : 2026/001234')
        for key in ('case_number', 'jurisdiction'):
            self.assertTrue(f[key]['sources'] and f[key]['sources'][0]['label'])
            self.assertEqual(f[key]['status'], 'suggested')
            self.assertFalse(f[key]['validated'])
            self.assertEqual(f[key]['status_label'], 'À valider')
        client = f['parties'][0]
        self.assertEqual(client['status'], 'configured')
        self.assertTrue(client['sources'][0]['label'])
        for p in f['parties']:
            self.assertTrue(p['sources'])
        self.assertEqual(f['next_deadline']['source'], 'Assignation.pdf')
        self.assertFalse(f['next_deadline']['validated'])
        self.assertTrue(f['last_received']['source'] and f['last_sent']['source'])
        self.assertEqual(f['last_received']['title'], 'Re: FIMEX')
        self.assertEqual(f['last_sent']['title'], 'Réponse FIMEX')
        kinds = {p['kind'] for p in f['pending']}
        self.assertTrue({'echeance', 'brouillon', 'faits'} <= kinds)
        self.assertEqual(f['documents'][0]['path'], PATH)

    def test_validation_changes_the_displayed_status(self):
        rid = next(r['id'] for r in lm.memory_records(self.desk, 'DOS-001') if r['record_type'] == 'case_number')
        facts460.decide(self.desk, 'DOS-001', rid, 'validate')
        f = fiche460.build_fiche(self.desk, 'DOS-001', TODAY)
        self.assertEqual(f['case_number']['status_label'], 'Validé')
        self.assertTrue(f['case_number']['validated'])

    def test_rg_derived_from_notice_is_marked_unvalidated(self):
        self.desk.db.executescript('DELETE FROM legal_memory_records;')
        self.desk.db.commit()
        from agent import notices440
        notices440.ensure_schema(self.desk)
        self.desk.db.execute("INSERT INTO notices440 VALUES('i','DOS-001','/Dossiers/DEMO/Avis de renvoi.pdf','e','done',?,'2026-09-01','2026-09-01')",
                             (json.dumps({'rg': '2026/009999', 'jurisdiction': 'TRIBUNAL DE COMMERCE DE LYON'}),))
        self.desk.db.commit()
        f = fiche460.build_fiche(self.desk, 'DOS-001', TODAY)
        self.assertEqual(f['case_number']['value'], '2026/009999')
        self.assertEqual(f['case_number']['status_label'], 'Non validé')
        self.assertEqual(f['case_number']['sources'][0]['label'], 'Avis de renvoi.pdf')

    def test_timeline_lines_all_have_a_source(self):
        rows = fiche460.chronologie(self.desk, 'DOS-001')
        self.assertTrue(rows)
        for r in rows:
            self.assertTrue(r['source'], r)
        kinds = {r['kind'] for r in rows}
        self.assertTrue({'email_received', 'email_sent', 'document', 'start_event', 'deadline'} <= kinds)
        doc = next(r for r in rows if r['kind'] == 'document')
        self.assertTrue(doc['href'].startswith('/documents/edit?'))
        dl = next(r for r in rows if r['kind'] == 'deadline')
        self.assertTrue(dl['href'].startswith('/echeances#e-'))
        ats = [r['at'] for r in rows if r['at']]
        self.assertEqual(ats, sorted(ats, reverse=True))
        self.assertEqual({r['kind'] for r in fiche460.chronologie(self.desk, 'DOS-001', kinds={'document'})}, {'document'})

    def test_unvalidated_timeline_items_are_marked(self):
        rows = fiche460.chronologie(self.desk, 'DOS-001')
        start = next(r for r in rows if r['kind'] == 'start_event')
        self.assertFalse(start['validated'])
        self.assertEqual(start['status_label'], 'À confirmer')

    def test_unknown_matter(self):
        with self.assertRaises(Stop):
            fiche460.build_fiche(self.desk, 'NOPE')
        with self.assertRaises(Stop):
            fiche460.chronologie(self.desk, 'NOPE')


class Pages(Base0 if False else unittest.TestCase):
    PATH = Base0.PATH
    call = Base0.call
    _base_setup = Base0.setUp

    def setUp(self):
        self._base_setup()
        lm.ensure_schema(self.desk)
        self.cal = FakeDAV()
        p = patch('agent.office440.dav_client', lambda desk: self.cal)
        p.start()
        self.addCleanup(p.stop)

    def post(self, name, body, **kw):
        r = self.call('/api440/' + name, 'POST', body, **kw)
        return r, (json.loads(r['body']) if r['body'][:1] == b'{' else {})

    def seed_morning(self):
        for rule, start in (('appel_jugement_contentieux', '2026-08-20'), ('pourvoi_cassation', '2026-08-04'), ('appel_ordonnance_refere', '2026-09-24')):
            ech.create_deadline(self.desk, 'DOS-001', rule, start)
        for i in range(4):
            self.desk.db.execute('INSERT INTO work_items VALUES(?,?,?,?,?,?,?,?,?,?)', ('k%d' % i + 'a' * 62, 'needs_action', '', '', 'DOS-001', 'Client %d' % i,
                                 'client@example.test', '2026-09-25T09:00:00+00:00', '2026-09-25T09:00:00+00:00', ''))
        for i in range(3):
            self.desk.db.execute('INSERT INTO work_items VALUES(?,?,?,?,?,?,?,?,?,?)', ('d%d' % i + 'b' * 62, 'draft_ready', '', '', 'DOS-001', 'Brouillon %d' % i,
                                 'x@example.test', '2026-09-25T09:00:00+00:00', '2026-09-25T09:00:00+00:00', ''))
        self.desk.db.commit()

    def test_home_has_at_most_five_cards_with_counter_and_keyboard_hooks(self):
        self.seed_morning()
        r = self.call('/aujourdhui', query='vue=cockpit')
        self.assertTrue(r['status'].startswith('200'))
        text = r['body'].decode()
        self.assertLessEqual(text.count('<article class="ck-card'), 5)
        self.assertEqual(text.count('<article class="ck-card'), 5)
        self.assertRegex(text, r'<strong>\d+</strong> autre\(s\) élément\(s\) replié\(s\)')
        self.assertEqual(text.count('data-ck-main'), 5)
        self.assertIn('class="ck-skip"', text)
        self.assertIn('Touches 1 à 5', text)
        self.assertIn('/static/v460.js', text)
        self.assertIn('Vue détaillée', text)
        mains = re.findall(r'class="ck-main" data-ck-main href="([^"]+)"', text)
        self.assertEqual(len(mains), 5)
        self.assertTrue(all(m.startswith('/agent-courriel/') for m in mains))

    def test_home_empty_and_detail_view(self):
        text = self.call('/aujourdhui', query='vue=cockpit')['body'].decode()
        self.assertIn('Rien n’exige votre décision', text)
        self.assertEqual(text.count('<article class="ck-card'), 0)
        detail = self.call('/aujourdhui', query='vue=detail')['body'].decode()
        self.assertIn('Retour au cockpit', detail)
        self.assertIn('À faire aujourd’hui', detail)

    def test_fiche_and_timeline_pages(self):
        self.assertTrue(self.call('/fiche', auth=False)['status'].startswith('401'))
        index = DocumentIndex(self.desk.c['state_dir'])
        index.db.execute('INSERT OR REPLACE INTO docs VALUES(?,?,?,?,?,?)', ('DOS-001', PATH, 'e1', '2026-09-20T10:00:00+00:00', TEXT, ''))
        index.db.commit()
        facts460.propose(self.desk, 'DOS-001', PATH, TEXT)
        page = self.call('/fiche', query='matter=DOS-001')['body'].decode()
        self.assertIn('Juridiction', page)
        self.assertIn('2026/001234', page)
        self.assertIn('À valider', page)
        self.assertIn('Source :', page)
        self.assertIn('fc-validate', page)
        self.assertIn('/static/v460.js', page)
        chrono = self.call('/chronologie', query='matter=DOS-001')['body'].decode()
        self.assertIn('Chronologie', chrono)
        self.assertIn('Retour à la fiche', chrono)
        self.assertEqual(self.call('/fiche', query='matter=NOPE')['status'][:3], '200')
        self.assertIn('Dossier introuvable', self.call('/fiche', query='matter=NOPE')['body'].decode())
        self.assertIn('Choisissez un dossier', self.call('/fiche')['body'].decode())

    def test_validate_and_reject_through_the_api(self):
        facts460.propose(self.desk, 'DOS-001', PATH, TEXT)
        rows = lm.memory_records(self.desk, 'DOS-001')
        a = next(r for r in rows if r['title'].startswith('Montant : 12'))
        b = next(r for r in rows if r['title'] == 'Montant : 8 000 €')
        r, out = self.post('fact/validate', {'matter': 'DOS-001', 'id': a['id'], 'text': 'Principal : 12 345,67 €'})
        self.assertTrue(r['status'].startswith('200'), r['body'])
        r, out = self.post('fact/reject', {'matter': 'DOS-001', 'id': b['id']})
        self.assertEqual(out['status'], 'disputed')
        self.assertEqual({x['id']: x['status'] for x in lm.memory_records(self.desk, 'DOS-001')}[a['id']], 'validated')
        page = self.call('/fiche', query='matter=DOS-001')['body'].decode()
        self.assertIn('Principal : 12 345,67 €', page)
        self.assertIn('1 fait(s) refusé(s)', page)
        self.assertNotIn('Montant : 8000', page)
        # sécurité
        self.assertTrue(self.post('fact/reject', {'matter': 'DOS-001', 'id': a['id']}, csrf=False)[0]['status'].startswith('400'))
        self.assertTrue(self.post('fact/reject', {'matter': 'DOS-001', 'id': a['id']}, auth=False)[0]['status'].startswith('401'))
        r, out = self.post('fact/validate', {'matter': 'AUTRE', 'id': a['id']})
        self.assertTrue(r['status'].startswith('400'))
        self.assertIn('n’existe plus', out['message'])

    def test_scan_and_refresh_enqueue_jobs(self):
        r, out = self.post('fact/scan', {'matter': 'DOS-001'})
        self.assertIn('job_id', out)
        r, out = self.post('fiche/refresh', {'matter': 'DOS-001'})
        self.assertIn('job_id', out)
        kinds = {row[0] for row in self.desk.db.execute('SELECT kind FROM jobs')}
        self.assertTrue({'extract_facts460', 'analyser_dossier5624'} <= kinds)   # 5.6.24 (F25) : analyse complète suivie
        self.assertTrue(self.post('fact/scan', {'matter': 'NOPE'})[0]['status'].startswith('400'))

    def test_html_in_facts_is_escaped(self):
        evil = "RG 2026/000001 <script>alert(1)</script> TRIBUNAL DE COMMERCE DE <img src=x onerror=alert(1)>"
        facts460.propose(self.desk, 'DOS-001', '/Dossiers/DEMO/<b>x</b>.pdf', "TRIBUNAL DE COMMERCE DE LYON\nRG 2026/000001\n" + evil + " " * 50 + evil)
        page = self.call('/fiche', query='matter=DOS-001')['body'].decode() + self.call('/chronologie', query='matter=DOS-001')['body'].decode()
        self.assertNotIn('<script>alert(1)', page)
        self.assertNotIn('<img src=x', page)

    def test_matter_page_links_to_fiche(self):
        text = self.call('/matter', query='id=DOS-001')['body'].decode()
        self.assertIn('/fiche?matter=DOS-001', text)


del Base0

if __name__ == '__main__':
    unittest.main()
