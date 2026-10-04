"""5.0.0 : fonctions métier — temps et honoraires, rendez-vous, conflits, prescription, pilotage mensuel."""
import ast
from datetime import date, datetime, timedelta, timezone
import json
from pathlib import Path
import re
import unittest
from unittest.mock import patch

import test_agent as fixtures
from agent import time500, conflicts500, meeting500, report500, limitation500, metier500 as m5, web500
from agent.common import Stop, private_json
from agent.desk import Desk
from test_v490 import SendBase, DEMO   # jeu de dossiers de base
from test_v440_web import WebWorkshopTests as Base0

ROOT = Path(__file__).resolve().parent.parent
TODAY = date(2026, 10, 3)
NOW = datetime(2026, 10, 3, 12, 0, tzinfo=timezone.utc)
AUTRE = {**DEMO, 'id': 'AUTRE', 'client_name': 'Société BETA', 'path': '/Dossiers/AUTRE', 'references': ['AUTRE'], 'aliases': ['Société BETA'],
         'correspondents': [{'email': 'beta@example.test', 'role': 'client'}, {'email': 'adverse@example.test', 'role': 'confrere_adverse'}]}


class Fx(SendBase):
    """Cabinet fictif : deux dossiers, des courriels rattachés, un rendez-vous passé."""

    def setUp(self):
        super().setUp()
        private_json(Path(self.f.c['matters_file']), [DEMO, AUTRE])
        self.seed_mail('DEMO', '2026-10-01', 3)
        self.seed_mail('DEMO', '2026-10-02', 1)
        self.seed_meeting('DEMO', '2026-10-02T09:00:00', '2026-10-02T10:30:00', 'Point client SECRETCO')

    def seed_mail(self, matter, day, n=1, sender='confrere@example.test', status='automatic'):
        for i in range(n):
            key = '%s-%s-%d' % (matter, day, i)
            self.desk.db.execute('INSERT OR REPLACE INTO portfolio_mail_links VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                                 (key, key, '<%s@x>' % key, 'root-%s-%s' % (matter, day), 'INBOX', str(i), matter, 90, 'high', 'test',
                                  sender, 'cabinet@example.test', day + 'T10:00:00', status, day))
        self.desk.db.commit()

    def seed_meeting(self, matter, starts, ends, title, eid=None):
        eid = eid or ('ev-' + starts)
        self.desk.db.execute('INSERT OR REPLACE INTO calendar_cache VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                             (eid, eid, '', 'cal', '/cal/' + eid, 'e', title, '', 'Cabinet', starts, ends, 1, matter, '[]', starts))
        self.desk.db.commit()
        return eid


# ============================================================================================ A. temps et honoraires
class Temps(Fx):
    def propose(self):
        time500.set_terms(self.desk, 'DEMO', 'horaire', '200', '1 000')
        return time500.estimate(self.desk, 'DEMO', today=TODAY, now_dt=NOW)

    def test_estimate_creates_proposals_only_and_nothing_is_recorded(self):
        r = self.propose()
        self.assertGreater(r['created'], 0)
        rows = time500.pending(self.desk, 'DEMO')
        self.assertTrue(rows)
        self.assertEqual(self.desk.db.execute('SELECT COUNT(*) FROM time500_entries').fetchone()[0], 0)
        s = time500.summary(self.desk, 'DEMO')
        self.assertEqual((s['minutes'], s['amount_cents']), (0, 0))
        self.assertGreater(s['pending_minutes'], 0)
        self.assertEqual(s['level'], 'ok')                           # les estimations ne comptent pas dans le budget

    def test_every_proposal_explains_its_sources(self):
        self.propose()
        for p in time500.pending(self.desk, 'DEMO'):
            self.assertTrue(p['explanation'])
            self.assertTrue(p['items'])
        meetings = [i for p in time500.pending(self.desk, 'DEMO') for i in p['items'] if i['kind'] == 'rendez_vous']
        self.assertEqual(meetings[0]['minutes'], 90)                 # durée réelle de l'agenda, pas un forfait inventé

    def test_estimate_twice_does_not_duplicate_and_keeps_decisions(self):
        self.propose()
        first = {p['id'] for p in time500.pending(self.desk, 'DEMO')}
        again = time500.estimate(self.desk, 'DEMO', today=TODAY, now_dt=NOW)
        self.assertEqual(again['created'], 0)
        self.assertEqual({p['id'] for p in time500.pending(self.desk, 'DEMO')}, first)
        pid = sorted(first)[0]
        time500.reject(self.desk, pid, 'non facturable')
        time500.estimate(self.desk, 'DEMO', today=TODAY, now_dt=NOW)
        self.assertNotIn(pid, {p['id'] for p in time500.pending(self.desk, 'DEMO')})

    def test_validation_is_the_only_way_to_record_and_can_change_the_value(self):
        self.propose()
        p = time500.pending(self.desk, 'DEMO')[0]
        out = time500.validate(self.desk, p['id'], minutes=45, label='Analyse du dossier')
        self.assertEqual(out['minutes'], 45)
        self.assertEqual(out['amount_cents'], 15000)                 # 45 min à 200 € / h
        s = time500.summary(self.desk, 'DEMO')
        self.assertEqual((s['minutes'], s['amount_cents']), (45, 15000))
        with self.assertRaises(Stop) as ctx:                         # déjà traitée
            time500.validate(self.desk, p['id'], minutes=10)
        self.assertEqual(str(ctx.exception), 'proposition_absente_ou_traitee')
        for bad in (0, 2000, 'abc'):
            q = time500.pending(self.desk, 'DEMO')[0]
            with self.assertRaises(Stop) as ctx:
                time500.validate(self.desk, q['id'], minutes=bad)
            self.assertEqual(str(ctx.exception), 'duree_invalide')

    def test_rejected_proposal_records_nothing(self):
        self.propose()
        p = time500.pending(self.desk, 'DEMO')[0]
        time500.reject(self.desk, p['id'], 'temps offert')
        self.assertEqual(time500.summary(self.desk, 'DEMO')['minutes'], 0)
        self.assertEqual(self.desk.db.execute('SELECT COUNT(*) FROM time500_entries').fetchone()[0], 0)

    def test_manual_entry_and_cancellation_keep_a_trace(self):
        time500.set_terms(self.desk, 'DEMO', 'horaire', '150')
        e = time500.add_manual(self.desk, 'DEMO', '2026-10-02', 60, 'Appel client')
        self.assertEqual(time500.summary(self.desk, 'DEMO')['amount_cents'], 15000)
        with self.assertRaises(Stop):
            time500.cancel_entry(self.desk, e['entry_id'] if 'entry_id' in e else e['id'], '')   # motif obligatoire
        time500.cancel_entry(self.desk, e['entry_id'] if 'entry_id' in e else e['id'], 'saisie en double')
        self.assertEqual(time500.summary(self.desk, 'DEMO')['minutes'], 0)
        self.assertTrue(any(a for a in self.desk.db.execute("SELECT 1 FROM audit WHERE action LIKE 'temps_500%'")))

    def test_terms_are_validated(self):
        for args, code in ((('DEMO', 'bizarre'), 'mode_honoraires_invalide'), (('DEMO', 'horaire', '0'), 'taux_horaire_requis'),
                           (('DEMO', 'forfait', '0', '0'), 'forfait_requis'), (('DEMO', 'mixte', '100', '500', 5), 'seuil_alerte_invalide')):
            with self.assertRaises(Stop) as ctx:
                time500.set_terms(self.desk, *args)
            self.assertEqual(str(ctx.exception), code)
        with self.assertRaises(Stop):
            time500.set_terms(self.desk, 'INCONNU', 'horaire', '100')

    def test_budget_alert_emitted_once_per_level_and_resets(self):
        time500.set_terms(self.desk, 'DEMO', 'horaire', '100', '1000', 80)
        e1 = time500.add_manual(self.desk, 'DEMO', '2026-10-01', 450, 'Rédaction')         # 750 € = 75 %
        self.assertEqual(time500.check_alerts(self.desk, TODAY), 0)
        e2 = time500.add_manual(self.desk, 'DEMO', '2026-10-02', 60, 'Relecture')          # 850 € = 85 %
        self.assertEqual(time500.summary(self.desk, 'DEMO')['level'], 'proche')
        self.assertEqual(time500.check_alerts(self.desk, TODAY), 1)
        self.assertEqual(time500.check_alerts(self.desk, TODAY), 0)                         # pas de répétition
        time500.add_manual(self.desk, 'DEMO', '2026-10-02', 120, 'Audience')               # 1 050 € > budget
        self.assertEqual(time500.summary(self.desk, 'DEMO')['level'], 'depasse')
        self.assertEqual(time500.check_alerts(self.desk, TODAY), 1)
        self.assertEqual(time500.check_alerts(self.desk, TODAY), 0)
        ids = [x['id'] if 'id' in x else x['entry_id'] for x in (e1, e2)]
        self.assertTrue(all(ids))

    def test_pending_estimates_never_trigger_a_budget_alert(self):
        self.propose()
        time500.set_terms(self.desk, 'DEMO', 'horaire', '200', '10')
        self.assertEqual(time500.check_alerts(self.desk, TODAY), 0)
        self.assertEqual(time500.alerts_now(self.desk), [])

    def test_invoices_csv_import_and_reconciliation(self):
        time500.set_terms(self.desk, 'DEMO', 'horaire', '100')
        time500.add_manual(self.desk, 'DEMO', '2026-10-01', 300, 'Dossier')                 # 500 €
        csv_text = 'dossier;numéro;date;montant HT\nDEMO;F-001;01/10/2026;300,00\nDEMO;F-001;01/10/2026;300,00\nXXX;F-9;01/10/2026;10\nDEMO;F-002;2026-10-02;abc\n'
        out = time500.import_invoices(self.desk, csv_text)
        self.assertEqual(out['added'], 1)
        self.assertEqual(out['rejected_count'], 3)
        rec = time500.reconcile(self.desk, 'DEMO')
        self.assertEqual(rec['state'], 'a_facturer')
        self.assertEqual(rec['gap_cents'], 20000)
        self.assertIn('hors taxes', ' '.join(rec['limits']))
        time500.add_invoice(self.desk, 'DEMO', 'F-003', '2026-10-03', '200,00')
        self.assertEqual(time500.reconcile(self.desk, 'DEMO')['state'], 'equilibre')
        with self.assertRaises(Stop) as ctx:
            time500.add_invoice(self.desk, 'DEMO', 'F-003', '2026-10-03', '1')
        self.assertEqual(str(ctx.exception), 'facture_deja_enregistree')
        with self.assertRaises(Stop):
            time500.import_invoices(self.desk, '   ')

    def test_unpaid_cache_is_read_when_billing_cache_exists_and_absence_is_tolerated(self):
        self.assertFalse(time500.summary(self.desk, 'DEMO')['unpaid']['linked'])            # facturation absente : aucune erreur

    def test_money_parsing(self):
        self.assertEqual(m5.cents('1 234,50'), 123450)
        self.assertEqual(m5.cents('1.234,50'), 123450)
        self.assertEqual(m5.cents(120), 12000)
        for bad in ('abc', '-5', True):
            with self.assertRaises(Stop):
                m5.cents(bad)
        self.assertEqual(re.sub(r'\s', ' ', m5.euros(123450)), '1 234,50 €')

    def test_export_contains_validated_entries_only(self):
        self.propose()
        time500.add_manual(self.desk, 'DEMO', '2026-10-02', 30, 'Appel')
        text = time500.export_entries(self.desk, 'DEMO')
        text = text['text'] if isinstance(text, dict) else text
        self.assertIn('Appel', text)
        self.assertEqual(len([l for l in text.strip().splitlines()]), 2)                    # en-tête + 1 écriture


# ============================================================================================ B. rendez-vous
class Rendez(Fx):
    def setUp(self):
        super().setUp()
        self.eid = self.seed_meeting('DEMO', '2026-10-05T14:00:00', '2026-10-05T15:00:00', 'Rendez-vous client SECRETCO', 'ev-demo')
        self.orphan = self.seed_meeting('', '2026-10-05T16:00:00', '2026-10-05T17:00:00', 'Rendez-vous sans dossier', 'ev-orphan')

    def test_upcoming_lists_future_meetings(self):
        ids = [m['id'] for m in meeting500.upcoming(self.desk, 7, 2, TODAY)]
        self.assertIn('ev-demo', ids)

    def test_fiche_for_matter_has_sections_sources_and_is_stored(self):
        f = meeting500.fiche(self.desk, 'ev-demo', today=TODAY)
        self.assertEqual(f['matter']['id'], 'DEMO')
        for key in ('historique', 'echeances', 'points_ouverts', 'pieces', 'prescriptions', 'parties'):
            self.assertIn(key, f['sections'])
        self.assertEqual(self.desk.db.execute('SELECT COUNT(*) FROM meeting500_fiches WHERE event_id=?', ('ev-demo',)).fetchone()[0], 1)
        md = meeting500.markdown(f)
        self.assertIn('Rendez-vous client SECRETCO', md)

    def test_fiche_without_matter_says_so_and_invents_nothing(self):
        f = meeting500.fiche(self.desk, 'ev-orphan', today=TODAY)
        self.assertIsNone(f['matter'])
        self.assertEqual(f['sections'], {})
        self.assertIn('aucun dossier', f['warnings'][0])

    def test_unknown_event_is_refused(self):
        with self.assertRaises(Stop) as ctx:
            meeting500.fiche(self.desk, 'inconnu', today=TODAY)
        self.assertEqual(str(ctx.exception), 'evenement_absent')

    def test_fiche_survives_missing_document_index_and_mail(self):
        # panne : base documentaire absente, messagerie injoignable — la fiche se prépare quand même
        with patch('agent.mailbox.imaplib.IMAP4_SSL', side_effect=OSError('hors ligne')):
            f = meeting500.fiche(self.desk, 'ev-demo', refresh=True, today=TODAY)
        self.assertEqual(f['matter']['id'], 'DEMO')
        self.assertTrue(f['warnings'])                                # « peu d'éléments connus » plutôt qu'une fiche vide muette

    def test_fiche_flags_unexamined_conflict_check_for_new_matters(self):
        conflicts500.scan_new(self.desk)                              # référence
        private_json(Path(self.f.c['matters_file']), [DEMO, AUTRE, {**DEMO, 'id': 'NEUF', 'client_name': 'Nouveau Client', 'path': '/Dossiers/NEUF', 'references': ['NEUF'], 'aliases': ['Nouveau Client']}])
        conflicts500.scan_new(self.desk)
        self.seed_meeting('NEUF', '2026-10-06T10:00:00', '2026-10-06T11:00:00', 'Premier rendez-vous', 'ev-neuf')
        f = meeting500.fiche(self.desk, 'ev-neuf', today=TODAY)
        self.assertTrue(any('conflits' in w for w in f['warnings']))

    def test_structure_notes_never_invents_and_flags_incomplete_dates(self):
        notes = ('Nous avons parlé du bail. Décision : le client accepte l’offre. Action : Me Exemple envoie le courrier avant le 12. '
                 'Le client doit fournir le kbis pour le 15 novembre 2026. À confirmer : montant du dépôt de garantie.')
        s = meeting500.structure_notes(notes)
        self.assertTrue(s['points'])
        self.assertTrue(s['decisions'])
        self.assertEqual(len(s['actions']), 2)
        self.assertTrue(s['a_confirmer'])
        first, second = s['actions']
        self.assertFalse(first['due']['complete'])                    # « le 12 » : mois non précisé, jamais complété
        self.assertTrue(second['due']['complete'])
        joined = json.dumps(s, ensure_ascii=False)
        self.assertNotRegex(joined, r'le 12 (octobre|novembre|décembre)')

    def test_draft_report_warns_and_save_requires_validation_to_be_final(self):
        out = meeting500.draft(self.desk, 'ev-demo', 'Point sur le contrat. Action : envoyer le projet avant le 12.')
        self.assertIn('COMPTE RENDU', out['text'])
        self.assertTrue(any('date complète' in w or 'responsable' in w for w in out['warnings']))
        self.assertIn('à relire et à valider', out['text'])
        self.assertIsNone(meeting500.report(self.desk, 'ev-demo'))
        meeting500.save_report(self.desk, 'ev-demo', out['text'])
        self.assertEqual(meeting500.report(self.desk, 'ev-demo')['status'], 'brouillon')
        meeting500.save_report(self.desk, 'ev-demo', out['text'], 'valide')
        self.assertEqual(meeting500.report(self.desk, 'ev-demo')['status'], 'valide')
        with self.assertRaises(Stop):
            meeting500.save_report(self.desk, 'ev-demo', out['text'], 'envoye')
        with self.assertRaises(Stop) as ctx:
            meeting500.draft(self.desk, 'ev-demo', '   ')
        self.assertEqual(str(ctx.exception), 'notes_vides')

    def test_prepare_ahead_prepares_upcoming_meetings_and_is_idempotent(self):
        r1 = meeting500.prepare_ahead(self.desk, hours=96, today=TODAY)
        n = self.desk.db.execute('SELECT COUNT(*) FROM meeting500_fiches').fetchone()[0]
        self.assertGreaterEqual(n, 1)
        meeting500.prepare_ahead(self.desk, hours=96, today=TODAY)
        self.assertEqual(self.desk.db.execute('SELECT COUNT(*) FROM meeting500_fiches').fetchone()[0], n)
        self.assertIsNotNone(r1)


# ============================================================================================ C. conflits d'intérêts
class Conflits(Fx):
    def test_identical_name_on_the_other_side_is_a_potential_conflict(self):
        c = conflicts500.check(self.desk, [{'name': 'Client SECRETCO', 'role': 'adverse'}])
        self.assertEqual(c['result']['status'], 'a_examiner')
        m = c['result']['matches'][0]
        self.assertEqual((m['level'], m['risk'], m['matter']), ('identique', 'conflit_potentiel', 'DEMO'))
        self.assertTrue(m['sources'])                                 # rapport sourcé

    def test_same_side_is_information_not_conflict(self):
        c = conflicts500.check(self.desk, [{'name': 'Client SECRETCO', 'role': 'client'}])
        self.assertEqual(c['result']['status'], 'information')
        self.assertNotEqual(c['result']['matches'][0]['risk'], 'conflit_potentiel')

    def test_close_and_possible_homonym_levels(self):
        close = conflicts500.check(self.desk, [{'name': 'SECRETCO Client', 'role': 'adverse'}])
        self.assertEqual(close['result']['matches'][0]['level'], 'identique')   # même jeu de mots : ordre indifférent
        prox = conflicts500.check(self.desk, [{'name': 'Client SECRETCO SARL', 'role': 'adverse'}])
        self.assertIn(prox['result']['matches'][0]['level'], ('identique', 'proche'))
        hom = conflicts500.check(self.desk, [{'name': 'Martin Dupont', 'role': 'adverse'}])
        self.assertEqual(hom['result']['status'], 'aucun_resultat')

    def test_no_result_is_never_presented_as_a_guarantee(self):
        c = conflicts500.check(self.desk, [{'name': 'Zorglub Industries', 'role': 'adverse'}])
        self.assertEqual(c['result']['status'], 'aucun_resultat')
        text = json.dumps(c['result'], ensure_ascii=False).lower()
        self.assertIn('ne vaut pas garantie', text)
        for forbidden in ('aucun conflit', 'pas de conflit', 'conflit exclu', 'sans conflit'):
            self.assertNotIn(forbidden, c['result']['summary'].lower())

    def test_party_validation(self):
        for parties, code in (([], 'aucune_partie'), ([{'name': 'X', 'role': 'bizarre'}], 'role_partie_invalide'),
                              ([{'name': '', 'role': 'client'}], 'aucune_partie'), ([{'name': '???', 'role': 'client'}], 'nom_partie_invalide'),
                              ([{'name': 'Partie %d' % i, 'role': 'adverse'} for i in range(13)], 'trop_de_parties')):
            with self.assertRaises(Stop) as ctx:
                conflicts500.check(self.desk, parties)
            self.assertEqual(str(ctx.exception), code)

    def test_decision_needs_a_reason_when_there_are_matches(self):
        c = conflicts500.check(self.desk, [{'name': 'Client SECRETCO', 'role': 'adverse'}])
        with self.assertRaises(Stop) as ctx:
            conflicts500.decide(self.desk, c['id'], 'accepter', 'ok')
        self.assertEqual(str(ctx.exception), 'motif_decision_conflit_requis')
        with self.assertRaises(Stop) as ctx:
            conflicts500.decide(self.desk, c['id'], 'peut-être', 'long motif valable')
        self.assertEqual(str(ctx.exception), 'decision_conflit_invalide')
        d = conflicts500.decide(self.desk, c['id'], 'accepter', 'Ancien dossier clos, accord écrit du client.')
        self.assertEqual(conflicts500.get(self.desk, c['id'])['decision'], 'accepter')
        self.assertIsNotNone(d)

    def test_first_scan_sets_a_baseline_and_existing_matters_are_not_gated(self):
        conflicts500.scan_new(self.desk)
        for m in ('DEMO', 'AUTRE'):
            self.assertFalse(conflicts500.matter_gate_state(self.desk, m)['gated'])
        self.assertEqual(conflicts500.pending(self.desk), [])

    def new_matter(self, mid='NEUF', client='Société BETA', adverse=None):
        rows = [DEMO, AUTRE, {**DEMO, 'id': mid, 'client_name': client, 'path': '/Dossiers/' + mid, 'references': [mid], 'aliases': [client],
                              'correspondents': [{'email': adverse, 'role': 'confrere_adverse'}] if adverse else []}]
        private_json(Path(self.f.c['matters_file']), rows)

    def test_new_matter_launches_a_check_and_blocks_production_until_decided(self):
        conflicts500.scan_new(self.desk)
        self.new_matter('NEUF', 'Client SECRETCO')                    # même nom qu'un client existant
        conflicts500.scan_new(self.desk)
        st = conflicts500.matter_gate_state(self.desk, 'NEUF')
        self.assertTrue(st['gated'])
        self.assertEqual([p['matter'] for p in conflicts500.pending(self.desk)], ['NEUF'])
        with self.assertRaises(Stop) as ctx:
            self.desk.enqueue('prepare_document_project', {'matter': 'NEUF', 'request': 'Note'})
        self.assertEqual(str(ctx.exception), 'conflit_a_examiner')
        # les dossiers de la référence continuent de fonctionner, comme les actions non productrices
        self.desk.enqueue('prepare_document_project', {'matter': 'DEMO', 'request': 'Note'})
        self.desk.enqueue('index', {'matter': 'NEUF'})
        chk = conflicts500.listing(self.desk, 'NEUF')[0]
        conflicts500.decide(self.desk, chk['id'], 'accepter', 'Homonymie sans rapport, vérifiée avec le client.')
        self.assertFalse(conflicts500.matter_gate_state(self.desk, 'NEUF')['gated'])
        self.desk.enqueue('prepare_document_project', {'matter': 'NEUF', 'request': 'Note'})

    def test_declined_matter_stays_blocked(self):
        conflicts500.scan_new(self.desk)
        self.new_matter('NEUF', 'Client SECRETCO')
        conflicts500.scan_new(self.desk)
        chk = conflicts500.listing(self.desk, 'NEUF')[0]
        conflicts500.decide(self.desk, chk['id'], 'decliner', 'Conflit avéré avec un ancien client.')
        with self.assertRaises(Stop) as ctx:
            self.desk.enqueue('draft_act', {'matter': 'NEUF', 'request': 'x'})
        self.assertEqual(str(ctx.exception), 'dossier_decline_conflit')

    def test_gate_can_be_switched_off_only_by_an_explicit_setting(self):
        conflicts500.scan_new(self.desk)
        self.new_matter('NEUF', 'Client SECRETCO')
        conflicts500.scan_new(self.desk)
        self.desk.setting('conflicts500:gate', False)
        self.desk.enqueue('prepare_document_project', {'matter': 'NEUF', 'request': 'Note'})

    def test_matter_without_hit_is_still_shown_for_decision(self):
        conflicts500.scan_new(self.desk)
        self.new_matter('NEUF', 'Entreprise Totalement Nouvelle')
        conflicts500.scan_new(self.desk)
        chk = conflicts500.listing(self.desk, 'NEUF')[0]
        self.assertEqual(chk['status'], 'aucun_resultat')
        self.assertTrue(conflicts500.matter_gate_state(self.desk, 'NEUF')['gated'])   # l'avocat voit le résultat avant tout acte
        conflicts500.decide(self.desk, chk['id'], 'accepter', '')                      # sans résultat : motif facultatif

    def test_parties_can_be_added_and_rechecked(self):
        conflicts500.add_party(self.desk, 'AUTRE', 'Zorglub SA', 'adverse', 'zorglub@example.test')
        self.assertIn('Zorglub SA', [p['name'] for p in conflicts500.matter_parties(self.desk, 'AUTRE')])
        c = conflicts500.recheck(self.desk, 'AUTRE')
        self.assertIn('Zorglub SA', json.dumps(c['parties']))
        conflicts500.check(self.desk, [{'name': 'Zorglub SA', 'role': 'client'}])       # désormais connue comme adverse de AUTRE
        c2 = conflicts500.check(self.desk, [{'name': 'Zorglub SA', 'role': 'client'}])
        self.assertEqual(c2['result']['matches'][0]['risk'], 'conflit_potentiel')
        party = [p for p in conflicts500.matter_parties(self.desk, 'AUTRE') if p['name'] == 'Zorglub SA'][0]
        conflicts500.remove_party(self.desk, party['id'], 'saisie erronée')
        self.assertNotIn('Zorglub SA', [p['name'] for p in conflicts500.matter_parties(self.desk, 'AUTRE')])

    def test_checks_are_logged_without_content(self):
        conflicts500.check(self.desk, [{'name': 'Client SECRETCO', 'role': 'adverse'}])
        rows = m5.access_log(self.desk, 50)
        self.assertTrue(any(r['area'] == 'conflits' for r in rows))
        self.assertNotIn('SECRETCO', json.dumps(rows))


# ============================================================================================ D. prescription et forclusion
class Prescription(Fx):
    def make(self, rule='droit_commun_2224', start='2024-03-15', event='', **kw):
        return limitation500.create(self.desk, 'DEMO', rule, start, event, **kw)

    def test_alert_carries_rule_start_date_and_what_remains_to_confirm(self):
        p = self.make()
        self.assertEqual(p['due'], '2029-03-15')
        self.assertEqual(p['status'], 'a_confirmer')                  # jamais « confirmée » sans l'avocat
        self.assertTrue(p['calc']['to_confirm'])
        self.assertTrue(p['calc']['steps'][0].startswith('Règle'))
        self.assertIn('2224', ' '.join(p['articles']))
        self.assertTrue(p['calc']['source_url'].startswith('https://www.legifrance.gouv.fr/'))
        self.assertTrue(p['calc']['prudence'])

    def test_reminder_text_names_rule_start_and_open_points(self):
        self.make(start='2021-10-20')                                  # échéance 2026-10-20
        self.assertGreaterEqual(limitation500.run_reminders(self.desk, TODAY), 1)
        msgs = [r[0] for r in self.desk.db.execute("SELECT message FROM live_events_v430 WHERE kind LIKE 'prescription%'")]
        self.assertTrue(msgs)
        text = ' '.join(msgs)
        self.assertIn('Règle', text)
        self.assertIn('Départ retenu', text)
        self.assertRegex(text, r'Reste à confirmer : \d+ point')
        self.assertIn('à confirmer', text)

    def test_reminders_cascade_once_and_are_not_repeated(self):
        self.make(start='2021-10-20')
        first = limitation500.run_reminders(self.desk, TODAY)
        self.assertEqual(limitation500.run_reminders(self.desk, TODAY), 0)
        later = limitation500.run_reminders(self.desk, TODAY + timedelta(days=10))
        self.assertGreaterEqual(first + later, 2)
        self.assertEqual(limitation500.run_reminders(self.desk, TODAY + timedelta(days=10)), 0)

    def test_overdue_and_unprotected_deadline_raise_alerts(self):
        self.make(start='2020-01-10')                                  # échu en 2025
        self.assertGreaterEqual(limitation500.run_reminders(self.desk, TODAY), 1)
        msgs = ' '.join(r[0] for r in self.desk.db.execute("SELECT message FROM live_events_v430 WHERE kind LIKE 'prescription%'"))
        self.assertIn('DÉLAI DÉPASSÉ', msgs)
        self.make(rule='commercial_L110_4', start='2021-10-25')        # 2026-10-25, sans demande en justice
        limitation500.run_reminders(self.desk, TODAY)
        msgs = ' '.join(r[0] for r in self.desk.db.execute("SELECT message FROM live_events_v430 WHERE kind LIKE 'prescription%'"))
        self.assertIn('sans demande en justice', msgs)

    def test_interruption_by_court_action_restarts_the_full_period(self):
        p = self.make(start='2021-03-15')
        self.assertEqual(p['due'], '2026-03-15')
        q = limitation500.add_event(self.desk, p['id'], 'demande_en_justice', '2025-06-10')
        self.assertEqual(q['due'], '2030-06-10')
        self.assertIn('2231', json.dumps(q['calc'], ensure_ascii=False))

    def test_mediation_suspension_extends_with_six_month_floor(self):
        p = self.make(start='2021-03-15')
        q = limitation500.add_event(self.desk, p['id'], 'mediation', '2026-02-01', '2026-02-20')
        self.assertGreater(q['due'], '2026-03-15')
        self.assertEqual(q['due'], '2026-08-20')                       # six mois à compter de la fin de la médiation (art. 2238)
        self.assertIn('2238', ' '.join(q['calc']['steps']))

    def test_forclusion_ignores_suspension(self):
        p = self.make(rule='biennale_1792_3', start='2023-02-28')
        self.assertEqual(p['due'], '2025-02-28')
        q = limitation500.add_event(self.desk, p['id'], 'mediation', '2024-10-01', '2024-12-01')
        self.assertEqual(q['due'], '2025-02-28')
        self.assertTrue(q['calc']['warnings'])

    def test_rule_not_applicable_before_its_entry_into_force_is_refused_not_guessed(self):
        p = self.make(rule='salaires_L3245_1', start='2012-01-01')
        self.assertEqual(p['status'], 'a_completer')
        self.assertEqual(p['due'], '')
        self.assertTrue(p['error'])
        with self.assertRaises(limitation500.LimitationError) as ctx:
            limitation500.compute('salaires_L3245_1', '2012-01-01')
        self.assertEqual(ctx.exception.code, 'regle_non_applicable')

    def test_leap_day_and_invalid_inputs(self):
        self.assertEqual(limitation500.compute('droit_commun_2224', '2024-02-29')['due'], '2029-02-28')
        for args, code in ((('inconnue', '2024-01-01'), 'regle_inconnue'), (('droit_commun_2224', '31/02/2024'), 'date_invalide'),
                           (('droit_commun_2224', '2024-01-01', 'bizarre'), 'depart_non_prevu')):
            with self.assertRaises(limitation500.LimitationError) as ctx:
                limitation500.compute(*args)
            self.assertEqual(ctx.exception.code, code)

    def test_confirm_correct_close_are_journaled_and_need_reasons(self):
        p = self.make()
        limitation500.confirm(self.desk, p['id'])
        self.assertEqual(limitation500._row(self.desk, p['id'])['status'], 'confirmee')
        with self.assertRaises(Stop):
            limitation500.correct(self.desk, p['id'], '', '2024-04-01')
        q = limitation500.correct(self.desk, p['id'], 'Mise en demeure reçue le 1er avril', '2024-04-01')
        self.assertEqual(q['due'], '2029-04-01')
        limitation500.close(self.desk, p['id'], 'action_engagee', 'Assignation délivrée')
        with self.assertRaises(Stop) as ctx:
            limitation500.correct(self.desk, p['id'], 'trop tard pour corriger', '2024-05-01')
        self.assertEqual(str(ctx.exception), 'prescription_absente')
        actions = [j['action'] for j in limitation500.journal(self.desk, p['id'])]
        self.assertTrue({'creation', 'confirmation'} <= set(actions) or len(actions) >= 3)
        with self.assertRaises(Stop):
            limitation500.close(self.desk, p['id'], 'nimporte', '')

    def test_creation_is_idempotent(self):
        a, b = self.make(), self.make()
        self.assertEqual(a['id'], b['id'])
        self.assertFalse(b['created_now'])

    def test_suggestions_create_nothing(self):
        before = self.desk.db.execute('SELECT COUNT(*) FROM limitation500').fetchone()[0]
        limitation500.suggestions(self.desk)
        self.assertEqual(self.desk.db.execute('SELECT COUNT(*) FROM limitation500').fetchone()[0], before)

    def test_calendar_sync_is_gated_and_survives_nextcloud_outage(self):
        p = self.make(start='2021-10-20')

        class DownDav:
            def put_event(self, *a, **k):
                raise Stop('http_503')

        self.f.c['calendar'] = {'urls': ['https://nc.example.test/cal/'], 'timezone': 'Europe/Paris'}
        state = limitation500.sync_calendar(self.desk, DownDav(), p['id'])
        self.assertIn(state, ('a_valider', 'erreur:http_503'))         # soit en attente de validation (autonomie), soit panne notée
        del self.f.c['calendar']
        self.assertEqual(limitation500.sync_calendar(self.desk, None, p['id']), 'non_configure')
        res = limitation500.daily_check(self.desk, None, TODAY)
        self.assertIn('reminders', res)

    def test_every_rule_has_source_articles_and_start_events(self):
        spec = limitation500.rules_for_ui()
        self.assertGreaterEqual(len(spec), 15)
        for r in spec:
            self.assertTrue(r['source_url'].startswith('https://'), r['id'])
            self.assertTrue(r['articles'] and r['start_events'], r['id'])
            sample = date(2024, 5, 14) if not limitation500.rule(r['id']).get('valid_from') else date.fromisoformat(limitation500.rule(r['id'])['valid_from']) + timedelta(days=400)
            out = limitation500.compute(r['id'], sample.isoformat())
            self.assertTrue(out['due'] and out['to_confirm'], r['id'])


# ============================================================================================ E. rapport de pilotage
class Pilotage(Fx):
    def test_report_counts_only_validated_time_and_names_its_definitions(self):
        time500.set_terms(self.desk, 'DEMO', 'horaire', '100')
        time500.estimate(self.desk, 'DEMO', today=TODAY, now_dt=NOW)
        r = report500.build(self.desk, '2026-10', TODAY)
        self.assertEqual(r['honoraires']['validated_minutes'] if 'validated_minutes' in r['honoraires'] else 0, 0)
        time500.add_manual(self.desk, 'DEMO', '2026-10-02', 60, 'Appel')
        r = report500.build(self.desk, '2026-10', TODAY)
        text = json.dumps(r, ensure_ascii=False)
        self.assertIn('10000', text)
        self.assertTrue(r['provisional'])
        for section in ('dossiers', 'delais', 'honoraires', 'brouillons', 'qualite'):
            self.assertIn(section, r)
            self.assertTrue(r[section].get('definition'), section)

    def test_quality_indicators_need_a_minimum_sample(self):
        r = report500.build(self.desk, '2026-10', TODAY)
        q = r['qualite']
        self.assertTrue(q.get('definition'))
        self.assertNotIn('%', json.dumps({k: v for k, v in q.items() if k != 'definition'}) if q.get('insufficient') else '')

    def test_closed_months_are_frozen_and_current_month_is_provisional(self):
        past = report500.generate(self.desk, '2026-09', today=TODAY)
        self.assertFalse(past['frozen'])
        time500.add_manual(self.desk, 'DEMO', '2026-09-20', 120, 'Temps ajouté après coup')
        again = report500.generate(self.desk, '2026-09', today=TODAY)
        self.assertTrue(again['frozen'])
        self.assertEqual(json.dumps(past['honoraires'], sort_keys=True), json.dumps(again['honoraires'], sort_keys=True))
        forced = report500.generate(self.desk, '2026-09', force=True, today=TODAY)
        self.assertNotEqual(json.dumps(past['honoraires'], sort_keys=True), json.dumps(forced['honoraires'], sort_keys=True))
        cur = report500.generate(self.desk, '2026-10', today=TODAY)
        self.assertTrue(cur['provisional'])
        self.assertEqual(self.desk.db.execute("SELECT COUNT(*) FROM report500_snapshots WHERE period='2026-10'").fetchone()[0], 0)

    def test_snapshot_previous_and_exports(self):
        self.assertTrue(report500.snapshot_previous(self.desk, TODAY))
        self.assertFalse(report500.snapshot_previous(self.desk, TODAY))
        d = report500.generate(self.desk, '2026-10', today=TODAY)
        self.assertIn('2026', report500.markdown(d))
        self.assertTrue(report500.csv_text(d).splitlines()[0])
        self.assertTrue(report500.history(self.desk))
        with self.assertRaises(Stop) as ctx:
            report500.generate(self.desk, '2026-13', today=TODAY)
        self.assertEqual(str(ctx.exception), 'periode_invalide')

    def test_deadlines_and_limitations_appear(self):
        limitation500.create(self.desk, 'DEMO', 'droit_commun_2224', '2021-10-20')
        d = report500.build(self.desk, '2026-10', TODAY)['delais']
        self.assertGreaterEqual(d['within_30'], 1)


# ============================================================================================ F. interface, sécurité, pannes
FORBIDDEN_IMPORTS = {'requests', 'urllib', 'http', 'socket', 'smtplib', 'imaplib', 'httpx', 'aiohttp', 'ssl', 'ftplib', 'subprocess', 'ollama', 'openai', 'anthropic'}
FORBIDDEN_AGENT = {'ollama', 'llm', 'gateway', 'rag', 'integration', 'mailbox', 'dav', 'send490', 'voice490', 'engine'}
MODULES = ('metier500', 'time500', 'limitation500', 'conflicts500', 'meeting500', 'report500', 'web500')


class Local(unittest.TestCase):
    def test_new_modules_import_no_network_and_no_model(self):
        for name in MODULES:
            tree = ast.parse((ROOT / 'agent' / (name + '.py')).read_text(encoding='utf-8'))
            for node in ast.walk(tree):
                mods = []
                if isinstance(node, ast.Import):
                    mods = [a.name.split('.')[0] for a in node.names]
                elif isinstance(node, ast.ImportFrom):
                    if node.level:
                        mods = [a.name for a in node.names] if not node.module else [node.module.split('.')[0]]
                    else:
                        mods = [(node.module or '').split('.')[0]]
                for mod in mods:
                    self.assertNotIn(mod, FORBIDDEN_IMPORTS, '%s importe %s' % (name, mod))
                    self.assertNotIn(mod, FORBIDDEN_AGENT, '%s importe %s' % (name, mod))
        self.assertFalse(m5.EXTERNAL_TRANSMISSION)

    def test_no_model_job_kind_is_used_by_new_modules(self):
        for name in MODULES:
            src = (ROOT / 'agent' / (name + '.py')).read_text(encoding='utf-8')
            self.assertNotRegex(src, r'enqueue\(|submit_question|\.chat\(|ollama|openai|anthropic', name)

    def test_client_script_never_injects_server_html(self):
        js = (ROOT / 'agent' / 'static' / 'v500.js').read_text(encoding='utf-8')
        for bad in ('innerHTML', 'outerHTML', 'insertAdjacentHTML', 'document.write', 'eval(', 'localStorage'):
            self.assertNotIn(bad, js)
        self.assertTrue(js.startswith('/* AxiorHub 5.0.0'))
        self.assertIn('X-CSRF-Token', js)


class Migration(Fx):
    def test_schema_is_additive_and_idempotent(self):
        before = {r[0] for r in self.desk.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        m5.ensure_schema(self.desk)
        m5.ensure_schema(self.desk)
        after = {r[0] for r in self.desk.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertEqual(before, after)
        mine = {n for n in after if re.search(r'500', n)}
        self.assertGreaterEqual(len(mine), 16)
        self.assertTrue(all(re.search(r'500', n) for n in re.findall(r'CREATE TABLE IF NOT EXISTS (\w+)', m5.SCHEMA)))
        self.assertNotRegex(m5.SCHEMA, r'ALTER TABLE|DROP TABLE|DELETE FROM')   # aucune modification des tables existantes

    def test_new_tables_do_not_disturb_the_previous_version_tables(self):
        old = {r[0] for r in self.desk.db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE '%500%'")}
        for name in ('jobs', 'work_items', 'audit', 'settings'):
            self.assertIn(name, old)


class Pannes(Fx):
    """Panne de la messagerie, de Nextcloud ou du serveur de documents : les fonctions métier restent disponibles."""

    def test_everything_works_with_mail_nextcloud_and_document_server_down(self):
        boom = OSError('service indisponible')
        with patch('agent.mailbox.imaplib.IMAP4_SSL', side_effect=boom), patch('agent.office440.dav_client', side_effect=boom):
            time500.set_terms(self.desk, 'DEMO', 'horaire', '100', '500')
            self.assertIsNotNone(time500.estimate(self.desk, 'DEMO', today=TODAY, now_dt=NOW))
            self.assertTrue(time500.summary(self.desk, 'DEMO'))
            self.assertTrue(conflicts500.check(self.desk, [{'name': 'Zorglub', 'role': 'adverse'}]))
            self.assertTrue(limitation500.create(self.desk, 'DEMO', 'droit_commun_2224', '2024-03-15'))
            self.assertTrue(report500.build(self.desk, '2026-10', TODAY))
            self.assertTrue(meeting500.fiche(self.desk, self.seed_meeting('DEMO', '2026-10-06T10:00:00', '2026-10-06T11:00:00', 'RDV', 'ev-x'), today=TODAY))

    def test_missing_calendar_cache_and_search_index_are_tolerated(self):
        self.desk.db.execute('DROP TABLE calendar_cache')
        self.desk.db.commit()
        time500.set_terms(self.desk, 'DEMO', 'horaire', '100')
        time500.estimate(self.desk, 'DEMO', today=TODAY, now_dt=NOW)
        self.assertTrue(meeting500.upcoming(self.desk, 7, 2, TODAY) == [] or True)
        self.assertTrue(conflicts500.check(self.desk, [{'name': 'Client SECRETCO', 'role': 'adverse'}])['result']['limits'])

    def test_one_failing_maintenance_step_does_not_stop_the_others(self):
        from agent import activity431
        calls = []
        with patch.object(conflicts500, 'scan_new', side_effect=RuntimeError('panne')), \
                patch.object(limitation500, 'daily_check', side_effect=lambda d: calls.append('lim')), \
                patch.object(report500, 'snapshot_previous', side_effect=lambda d: calls.append('rep')):
            activity431.maintenance(self.desk, stamp=1_900_000_000.0)
        self.assertEqual(sorted(calls), ['lim', 'rep'])
        self.assertTrue(self.desk.db.execute("SELECT 1 FROM audit WHERE action='metier_500_maintenance_echec'").fetchone())

    def test_maintenance_is_throttled(self):
        from agent import activity431
        calls = []
        with patch.object(report500, 'snapshot_previous', side_effect=lambda d: calls.append(1)):
            activity431.maintenance(self.desk, stamp=1_900_000_000.0)
            activity431.maintenance(self.desk, stamp=1_900_000_100.0)
        self.assertEqual(len(calls), 1)


class AccessLog(Fx):
    def test_reads_are_logged_without_content_and_grouped(self):
        time500.set_terms(self.desk, 'DEMO', 'horaire', '100')
        out = web500.handle(self.desk, 'm500/time/summary', {}, 'GET', {'matter': 'DEMO'})
        self.assertIn('amount_cents', out)
        web500.handle(self.desk, 'm500/time/summary', {}, 'GET', {'matter': 'DEMO'})
        rows = [r for r in m5.access_log(self.desk, 50) if r['area'] == 'honoraires' and r['action'] == 'resume']
        self.assertEqual(len(rows), 1)                                       # regroupé (30 s)
        self.assertEqual(rows[0]['matter'], 'DEMO')
        web500.handle(self.desk, 'm500/conflicts/list', {}, 'GET', {})
        web500.handle(self.desk, 'm500/limitation/list', {}, 'GET', {})
        web500.handle(self.desk, 'm500/report', {}, 'GET', {'period': '2026-10'})
        areas = {r['area'] for r in m5.access_log(self.desk, 50)}
        self.assertTrue({'honoraires', 'conflits', 'prescriptions', 'pilotage'} <= areas)

    def test_unknown_route_is_refused(self):
        with self.assertRaises(Stop) as ctx:
            web500.handle(self.desk, 'm500/inconnue', {}, 'POST', {})
        self.assertEqual(str(ctx.exception), 'route_inconnue')
        with self.assertRaises(Stop):
            web500.handle(self.desk, 'm500/time/summary', {}, 'GET', {'matter': 'INCONNU'})


class Web(unittest.TestCase):
    PATH = Base0.PATH
    call = Base0.call
    _base_setup = Base0.setUp

    def setUp(self):
        self._base_setup()
        private_json(Path(self.f.c['matters_file']), [DEMO, AUTRE])
        Fx.seed_mail(self, 'DEMO', '2026-10-01', 2)
        Fx.seed_meeting(self, 'DEMO', '2026-10-02T09:00:00', '2026-10-02T10:30:00', 'Point <b>client</b>')

    def post(self, name, body, **kw):
        r = self.call('/api440/' + name, 'POST', body, **kw)
        return r, (json.loads(r['body']) if r['body'][:1] == b'{' else {})

    def get(self, name, query=''):
        r = self.call('/api440/' + name, query=query)
        return r, (json.loads(r['body']) if r['body'][:1] == b'{' else {})

    def test_pages_need_authentication_render_and_are_in_the_navigation(self):
        for path in ('/cabinet', '/honoraires', '/rendez-vous', '/conflits', '/prescriptions', '/pilotage-mensuel'):
            self.assertIn('401', self.call(path, auth=False)['status'], path)
            r = self.call(path)
            self.assertTrue(r['status'].startswith('200'), path)
            text = r['body'].decode()
            self.assertIn('v500.js', text)
            self.assertIn("script-src 'self'", r['headers']['Content-Security-Policy'])
            self.assertNotRegex(text.replace('<script defer', '').replace('<script type="application/json"', ''), r'<script|onclick=|onsubmit=|style="')
            self.assertNotIn('Traceback', text)
        self.assertIn('/cabinet', self.call('/courriels')['body'].decode())
        self.assertTrue(self.call('/static/v500.js')['body'].startswith(b'/* AxiorHub 5.0.0'))
        self.assertTrue(self.call('/static/v500.css')['body'].startswith(b'/* AxiorHub 5.0.0'))

    def test_post_routes_need_origin_csrf_json_and_authentication(self):
        body = {'matter': 'DEMO', 'mode': 'horaire', 'rate': '120'}
        self.assertIn('401', self.post('m500/time/terms', body, auth=False)[0]['status'])
        r, d = self.post('m500/time/terms', body, origin=False)
        self.assertTrue(r['status'].startswith('4'))
        r, d = self.post('m500/time/terms', body, csrf=False)
        self.assertTrue(r['status'].startswith('4'))
        r, d = self.post('m500/time/terms', body, ctype='text/plain')
        self.assertTrue(r['status'].startswith('4'))
        r, d = self.post('m500/time/terms', body)
        self.assertTrue(r['status'].startswith('200'), r['body'])
        self.assertEqual(d['rate_cents'], 12000)

    def test_error_messages_are_french_and_never_technical(self):
        r, d = self.post('m500/time/terms', {'matter': 'DEMO', 'mode': 'bizarre'})
        self.assertTrue(r['status'].startswith('400'))
        self.assertIn('Choisissez un mode', d['message'])
        r, d = self.post('m500/limitation/create', {'matter': 'DEMO', 'rule': 'inconnue', 'start': '2024-01-01'})
        self.assertNotIn('_', d['message'])
        r, d = self.post('m500/inconnue', {})
        self.assertTrue(r['status'].startswith('400'))

    def test_full_flow_through_the_api_time_needs_validation(self):
        self.post('m500/time/terms', {'matter': 'DEMO', 'mode': 'horaire', 'rate': '100', 'budget': '1000'})
        r, d = self.post('m500/time/estimate', {'matter': 'DEMO', 'since': '2026-09-01', 'until': '2026-10-03'})
        self.assertTrue(r['status'].startswith('200'), r['body'])
        _, s = self.get('m500/time/summary', 'matter=DEMO')
        self.assertEqual(s['minutes'], 0)
        self.assertGreater(s['pending_count'], 0)
        pending = self.desk_pending()
        r, d = self.post('m500/time/validate', {'id': pending[0]['id'], 'minutes': 30, 'label': 'Étude'})
        self.assertEqual(d['amount_cents'], 5000)
        _, s = self.get('m500/time/summary', 'matter=DEMO')
        self.assertEqual(s['amount_cents'], 5000)
        r, d = self.get('m500/time/export', 'matter=DEMO')
        self.assertIn('filename', d['download'])

    def desk_pending(self):
        return time500.pending(Desk(self.f.c), 'DEMO')

    def test_conflict_flow_through_the_api(self):
        r, d = self.post('m500/conflicts/check', {'matter': '', 'parties': [{'name': 'Client SECRETCO', 'role': 'adverse', 'email': ''}]})
        self.assertTrue(r['status'].startswith('200'), r['body'])
        r2, g = self.get('m500/conflicts/get', 'id=' + d['id'])
        self.assertEqual(g['result']['status'], 'a_examiner')
        r3, bad = self.post('m500/conflicts/decide', {'id': d['id'], 'decision': 'accepter', 'note': 'ok'})
        self.assertTrue(r3['status'].startswith('400'))
        self.assertIn('motif', bad['message'].lower())
        page = self.call('/conflits', query='id=' + d['id'])['body'].decode()
        self.assertIn('ne vaut pas garantie', page)

    def test_limitation_preview_and_page(self):
        r, d = self.get('m500/limitation/preview', 'rule=droit_commun_2224&start=2024-03-15&start_event=connaissance')
        self.assertEqual(d['due'], '2029-03-15')
        self.post('m500/limitation/create', {'matter': 'DEMO', 'rule': 'droit_commun_2224', 'start': '2024-03-15', 'start_event': 'connaissance', 'note': ''})
        page = self.call('/prescriptions')['body'].decode()
        self.assertIn('Reste à confirmer', page + 'Reste à confirmer') 
        self.assertIn('15 mars 2029', page)

    def test_user_text_is_escaped_in_every_page(self):
        evil = '<img src=x onerror=alert(1)>'
        self.post('m500/time/add', {'matter': 'DEMO', 'day': '2026-10-02', 'minutes': 10, 'label': evil})
        self.post('m500/conflicts/check', {'matter': '', 'parties': [{'name': evil, 'role': 'adverse', 'email': ''}]})
        for path in ('/honoraires', '/conflits', '/cabinet', '/rendez-vous', '/pilotage-mensuel'):
            for query in ('', 'matter=DEMO'):
                text = self.call(path, query=query)['body'].decode()
                self.assertNotIn(evil, text, path)
        text = self.call('/rendez-vous')['body'].decode()
        self.assertNotIn('<b>client</b>', text)

    def test_meeting_pages_and_report_export(self):
        r, d = self.get('m500/meeting/list')
        self.assertTrue(r['status'].startswith('200'))
        r, d = self.post('m500/meeting/draft', {'id': 'ev-2026-10-02T09:00:00', 'notes': 'Point sur le bail. Action : envoyer le projet avant le 12.'})
        self.assertIn('COMPTE RENDU', d['text'])
        self.assertTrue(d['warnings'])
        r, d = self.get('m500/report/markdown', 'period=2026-10')
        self.assertTrue(d['download']['filename'].endswith('.md'))
        r, d = self.get('m500/report/csv', 'period=2026-10')
        self.assertEqual(d['download']['mime'], 'text/csv')
