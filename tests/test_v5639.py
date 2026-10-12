"""5.6.39 : contre-tests de l'audit 5.6.37 (P01 à P11) et bibliothèque de modèles Corporate.

Données d'essai fictives (domaines example.test) ; le stockage Nextcloud, le modèle d'IA et l'API Invoice Ninja sont simulés.
"""
from datetime import date
import json
from pathlib import Path
import sqlite3
import unittest
import unittest.mock

from agent.common import Stop
from agent.erreurs5634 import Qualified
import test_v5634 as t34
import test_v5637 as t37

ROOT = Path(__file__).resolve().parents[1]


class AuditFigures(unittest.TestCase):
    """F01 (P0) : chaque chiffre du rapport de gestion est rattaché à son poste, sa devise, son unité et son exercice dans le même segment."""

    B = {'devise': 'EUR', 'unite': 'unites', 'exercice': '2025'}
    P01 = 'Chiffre d’affaires 2025 : 812 450 EUR. Résultat net 2025 : 20 000 EUR.'

    def check(self, x, text, exercice='2025'):
        from agent.comptes5639 import check
        return check({**self.B, **x}, text, exercice)[0]

    def test_p01_amount_of_another_line_in_the_same_excerpt_is_not_justified(self):
        self.assertEqual(self.check({'libelle': 'Chiffre d’affaires', 'montant': '20 000 EUR', 'extrait': self.P01}, self.P01),
                         'montant_absent_de_l_extrait')
        self.assertIsNone(self.check({'libelle': 'Chiffre d’affaires', 'montant': '812 450 EUR', 'extrait': self.P01}, self.P01))

    def test_p02_currency_is_read_next_to_the_amount_not_elsewhere_in_the_document(self):
        text = 'Frais divers : 100 EUR.\nRésultat net 2025 : 20 000 USD.'
        self.assertEqual(self.check({'libelle': 'Résultat net', 'montant': '20 000 EUR', 'extrait': 'Résultat net 2025 : 20 000 USD.'}, text),
                         'devise_differente_des_comptes')

    def test_p03_financial_year_comes_from_the_source_not_from_the_model(self):
        text = 'Comptes annuels\nChiffre d’affaires 2024 : 790 000 EUR.'
        self.assertEqual(self.check({'libelle': 'Chiffre d’affaires', 'montant': '790 000 EUR', 'extrait': 'Chiffre d’affaires 2024 : 790 000 EUR'},
                                    text), 'exercice_different')

    def test_c01_consistent_figure_stays_justified_with_its_evidence(self):
        from agent.comptes5639 import check
        text = 'Comptes annuels de l’exercice clos le 31/12/2025 (en euros)\nChiffre d’affaires net : 812 450 €\nRésultat net : 20 000 €'
        cause, detail = check({**self.B, 'libelle': 'Chiffre d’affaires', 'montant': '812 450 €', 'extrait': 'Chiffre d’affaires net : 812 450 €'},
                              text, '2025')
        self.assertIsNone(cause)
        self.assertTrue(detail.get('preuve_exercice'))
        self.assertTrue(detail.get('preuve_devise'))

    def test_comparative_table_wide_excerpt_and_units(self):
        table = 'Compte de résultat (en euros)\nRubrique 2025 2024\nChiffre d’affaires  812 450  790 120\nRésultat net  20 000  18 000'
        line = 'Chiffre d’affaires  812 450  790 120'
        self.assertIsNone(self.check({'libelle': 'Chiffre d’affaires', 'montant': '812 450', 'extrait': line}, table))
        self.assertEqual(self.check({'libelle': 'Chiffre d’affaires', 'montant': '790 120', 'extrait': line}, table), 'exercice_different')
        wide = 'Chiffre d’affaires 2025 : 812 450 EUR ; Résultat net 2025 : 20 000 EUR'
        self.assertIsNone(self.check({'libelle': 'Résultat net', 'montant': '20 000 EUR', 'extrait': wide}, wide))
        self.assertEqual(self.check({'libelle': 'Résultat net', 'montant': '812 450 EUR', 'extrait': wide}, wide), 'montant_absent_de_l_extrait')

    def test_management_report_marks_the_three_wrong_associations(self):
        from agent import corporate5636 as co
        text = ('Comptes annuels de l’exercice 2025 (en euros)\nChiffre d’affaires 2025 : 812 450 EUR. Résultat net 2025 : 20 000 EUR.\n'
                'Frais divers : 100 EUR.\nRésultat exceptionnel 2025 : 3 000 USD.\nChiffre d’affaires 2024 : 790 000 EUR.')
        answer = {'activite': 'a', 'evenements': 'b', 'perspectives': 'c', 'chiffres': [
            {**self.B, 'libelle': 'Chiffre d’affaires', 'montant': '20 000 EUR', 'extrait': self.P01},
            {**self.B, 'libelle': 'Résultat exceptionnel', 'montant': '3 000 EUR', 'extrait': 'Résultat exceptionnel 2025 : 3 000 USD.'},
            {**self.B, 'libelle': 'Chiffre d’affaires', 'montant': '790 000 EUR', 'extrait': 'Chiffre d’affaires 2024 : 790 000 EUR.'},
            {**self.B, 'libelle': 'Chiffre d’affaires', 'montant': '812 450 EUR', 'extrait': 'Chiffre d’affaires 2025 : 812 450 EUR.'}]}

        class Model:
            def complete(self, *a, **k):
                return json.dumps(answer)

        class Desk:
            c = {'documents': {}}
        with unittest.mock.patch('agent.documents.extract', return_value=text):
            out = co.accounts_analysis(Desk(), {'matter': ''}, b'', 'comptes.pdf', model=Model(), exercice='2025')
        self.assertEqual([x['statut'] for x in out['controle_chiffres']], ['a_verifier', 'a_verifier', 'a_verifier', 'justifie'])
        self.assertTrue(all(x['cause'] for x in out['controle_chiffres'][:3]))
        self.assertEqual(out['chiffres_cles'].count('[À VÉRIFIER'), 3)
        self.assertEqual(out['methode_controle'], co.METHODE_COMPTES)


class _Corp(t37._Linked):
    def setUp(self):
        super().setUp()
        import test_v510 as t510
        from agent import corporate5636 as co
        self.co, self.t510 = co, t510
        self.docs = t510.FakeDocs({})
        self.cid = co.company_save(self.desk, {'denomination': 'EXEMPLE SAS', 'forme': 'SAS', 'capital': '10000', 'nominal': '1',
                                               'siege': '10 rue du Test, 69002 Lyon', 'rcs': 'Lyon', 'siren': '123456789', 'matter': t34.MID})['id']
        co.subscription(self.desk, {'company': self.cid, 'name': 'Paul MARTIN', 'quantity': '6000'})
        co.subscription(self.desk, {'company': self.cid, 'name': 'Anne DUPONT', 'quantity': '4000'})

    def state(self, op):
        return self.desk.db.execute('SELECT state FROM operations5636 WHERE id=?', (op,)).fetchone()['state']

    def siege(self):
        return self.co.operation_submit(self.desk, {'kind': 'siege', 'company': self.cid, 'nouveau_siege': '5 place Fictive, 69003 Lyon',
                                                    'organe': 'dirigeant'})


class AuditCorporateFlows(_Corp):
    def test_p04_notification_failure_after_commit_keeps_operation_and_job_linked(self):
        with unittest.mock.patch('agent.live430.emit', side_effect=sqlite3.OperationalError('database is locked')):
            out = self.siege()                                                                          # aucune erreur présentée
        other = sqlite3.connect(str(Path(self.desk.c['state_dir']) / 'desk.sqlite3'))                  # une autre connexion observe la base
        try:
            job = other.execute('SELECT job FROM operations5636 WHERE id=?', (out['id'],)).fetchone()[0]
            pending = other.execute("SELECT id FROM jobs WHERE kind='corporate5636' AND json_extract(args,'$.id')=?", (out['id'],)).fetchall()
        finally:
            other.close()
        self.assertEqual((job, [r[0] for r in pending]), (out['job'], [out['job']]))
        self.assertIn('notification_file_en_panne', json.dumps([list(r) for r in self.desk.db.execute('SELECT * FROM audit')], ensure_ascii=False))

    def test_p04_incident_before_commit_leaves_neither_project_nor_job(self):
        real = self.desk.db.execute
        calls = {'n': 0}

        def failing(sql, *a):
            if sql.startswith('UPDATE operations5636 SET job='):
                calls['n'] += 1
                raise sqlite3.OperationalError('disk I/O error')
            return real(sql, *a)
        with unittest.mock.patch.object(self.desk.db, 'execute', side_effect=failing):
            with self.assertRaises(sqlite3.OperationalError):
                self.siege()                                                                            # incident entre insertion et rattachement
        self.assertEqual(calls['n'], 1)
        self.assertEqual(self.desk.db.execute('SELECT COUNT(*) FROM operations5636').fetchone()[0], 0)
        self.assertEqual(self.desk.db.execute("SELECT COUNT(*) FROM jobs WHERE kind='corporate5636'").fetchone()[0], 0)
        again = self.siege()                                                                            # nouvel essai : une seule opération
        self.assertEqual(self.desk.db.execute('SELECT job FROM operations5636 WHERE id=?', (again['id'],)).fetchone()['job'], again['job'])

    def test_p05_pending_operation_cannot_be_realised(self):
        out = self.siege()
        with self.assertRaises(Stop) as ex:
            self.co.realize(self.desk, {'id': out['id'], 'date': '2026-10-12', 'justificatif': 'PV fictif signé'})
        self.assertEqual(str(ex.exception), 'actes_en_preparation')
        self.assertEqual(self.co.company(self.desk, self.cid)['siege'], '10 rue du Test, 69002 Lyon')
        self.assertEqual(self.co.registers(self.desk, self.cid)[1], [])
        page = self.request('/corporate', query='societe=%s&vue=actes' % self.cid)['body']
        self.assertIn('Les actes sont en préparation', page)
        self.assertIn('actes établis hors d’AxiorHub', page.replace("'", '’'))

    def test_p06_failed_preparation_cannot_be_realised_by_the_normal_path(self):
        out = self.siege()
        with unittest.mock.patch('agent.dav.DAV', return_value=self.docs), \
                unittest.mock.patch.object(self.co, 'render', side_effect=Stop('modele_indisponible')):
            with self.assertRaises(Stop):
                self.co.job(self.desk, {'id': out['id']})                                               # le vrai gestionnaire du travail
        self.assertEqual(self.state(out['id']), 'echec')
        with self.assertRaises(Stop) as ex:
            self.co.realize(self.desk, {'id': out['id'], 'date': '2026-10-12', 'justificatif': 'PV fictif signé'})
        self.assertEqual(str(ex.exception), 'preparation_en_echec')
        self.assertEqual(self.co.company(self.desk, self.cid)['siege'], '10 rue du Test, 69002 Lyon')
        with self.assertRaises(Stop) as ex:                                                             # parcours externe : confirmation exigée
            self.co.realize(self.desk, {'id': out['id'], 'date': '2026-10-12', 'justificatif': 'PV fictif signé'}, externe=True)
        self.assertEqual(str(ex.exception), 'confirmation_actes_externes_requise')
        self.co.realize(self.desk, {'id': out['id'], 'date': '2026-10-12', 'justificatif': 'PV établi par le notaire (fictif)',
                                    'actes_externes': '1'}, externe=True)
        self.assertEqual(self.co.company(self.desk, self.cid)['siege'], '5 place Fictive, 69003 Lyon')
        data = json.loads(self.desk.db.execute('SELECT data FROM operations5636 WHERE id=?', (out['id'],)).fetchone()['data'])
        self.assertEqual(data['mode_realisation'], 'actes_externes')
        with self.assertRaises(Stop):                                                                   # une seule réalisation
            self.co.realize(self.desk, {'id': out['id'], 'date': '2026-10-12', 'justificatif': 'deuxième', 'actes_externes': '1'}, externe=True)

    def test_p05_abandoned_operation_cannot_be_realised_by_api(self):
        out = self.siege()
        self.co.abandon(self.desk, {'id': out['id']})
        for externe in (False, True):
            with self.assertRaises(Stop):
                self.co.realize(self.desk, {'id': out['id'], 'date': '2026-10-12', 'justificatif': 'PV fictif', 'actes_externes': '1'}, externe=externe)
        self.assertEqual(self.state(out['id']), 'abandonnee')

    def test_p07_decision_date_differs_from_the_planned_meeting(self):
        out = self.co.operation_submit(self.desk, {'kind': 'agoa', 'company': self.cid, 'exercice_clos': '2025-12-31', 'date_ag': '2026-11-20',
                                                   'resultat': '20000', 'dividendes': '5000'})
        self.co.generate(self.desk, out['id'], dav=self.docs)
        with self.assertRaises(Qualified) as ex:
            self.co.realize(self.desk, {'id': out['id'], 'date': '2026-10-12', 'justificatif': 'PV d’AG signé (fictif)'})
        self.assertEqual(str(ex.exception), 'date_decision_differente')
        self.assertIn('2026-11-20', ex.exception.detail['precision'])
        self.assertEqual((self.co.registers(self.desk, self.cid)[1], self.state(out['id'])), ([], 'actes_prets'))   # rien d'écrit
        self.co.realize(self.desk, {'id': out['id'], 'date': '2026-10-12', 'justificatif': 'PV d’AG signé (fictif)', 'confirmer_date': '1'})
        decision = self.co.registers(self.desk, self.cid)[1][-1]
        self.assertEqual(decision['day'], '2026-10-12')
        data = json.loads(self.desk.db.execute('SELECT data FROM operations5636 WHERE id=?', (out['id'],)).fetchone()['data'])
        self.assertEqual((data['date_decision'], data['date_ag'], data['date_ag_prevue']), ('2026-10-12', '2026-10-12', '2026-11-20'))

    def test_p07_same_date_needs_no_confirmation_and_invalid_date_is_refused(self):
        out = self.co.operation_submit(self.desk, {'kind': 'agoa', 'company': self.cid, 'exercice_clos': '2025-12-31', 'date_ag': '2026-11-20',
                                                   'resultat': '20000', 'dividendes': '5000'})
        self.co.generate(self.desk, out['id'], dav=self.docs)
        with self.assertRaises(Stop):
            self.co.realize(self.desk, {'id': out['id'], 'date': '2026-13-45', 'justificatif': 'PV fictif'})
        self.co.realize(self.desk, {'id': out['id'], 'date': '2026-11-20', 'justificatif': 'PV d’AG signé (fictif)'})
        self.assertEqual(self.co.registers(self.desk, self.cid)[1][-1]['day'], '2026-11-20')

    def test_p10_abandon_during_the_first_deposit_stops_the_next_files(self):
        out = self.siege()
        co, desk, op = self.co, self.desk, out['id']

        class Docs(self.t510.FakeDocs):
            def put_file(self, path, data, kind='application/octet-stream'):
                result = super().put_file(path, data, kind)
                if len(self.puts) == 1:
                    co.abandon(desk, {'id': op})                                                       # clic « Abandonner » pendant le dépôt
                return result
        docs = Docs({})
        result = co.generate(desk, op, dav=docs)
        self.assertEqual(self.state(op), 'abandonnee')
        self.assertEqual(len(docs.puts), 1)
        self.assertEqual(result.get('arret'), 'abandon_pendant_la_preparation')
        stored = json.loads(desk.db.execute('SELECT result FROM operations5636 WHERE id=?', (op,)).fetchone()['result'])
        self.assertEqual(len(stored['files']), 1)                                                      # le fichier déposé reste tracé
        with unittest.mock.patch.object(co, 'generate', side_effect=Stop('incident_fictif')):
            with self.assertRaises(Stop):
                co.job(desk, {'id': op})
        self.assertEqual(self.state(op), 'abandonnee')                                                 # jamais transformé en échec
        self.assertEqual(co.generate(desk, op, dav=docs).get('interrompu'), 'abandonnee')
        self.assertEqual(len(docs.puts), 1)


class AuditInitiativeChildren(t37._Linked):
    """F05 (P08) : écarter une initiative annule ses rédactions enfants ; une rédaction vérifie son autorisation au démarrage et au dépôt."""

    def setUp(self):
        super().setUp()
        self.row = t37.AuditAutomations.plan(self)

    def test_p08_pending_child_is_cancelled_and_never_starts(self):
        from agent import initiatives5636 as i
        from agent.docrequest520 import run
        i.validate(self.desk, self.row['id'], {'id': self.row['id'], 'matter': t34.MID, 'step_constitution': True})
        i.execute(self.desk, self.row['id'], version=1)
        step = {s['id']: s for s in i._row(self.desk, self.row['id'])['steps']}['constitution']
        self.assertEqual(step['status'], 'en_file')
        job = self.desk.db.execute('SELECT job_id FROM docreq520 WHERE id=?', (step['request'],)).fetchone()['job_id']
        i.dismiss(self.desk, self.row['id'])
        self.assertEqual(self.desk.db.execute('SELECT status FROM docreq520 WHERE id=?', (step['request'],)).fetchone()['status'], 'annule')
        self.assertIn(self.desk.db.execute('SELECT status FROM jobs WHERE id=?', (job,)).fetchone()['status'], ('cancelled', 'canceled'))
        self.assertEqual({s['id']: s for s in i._row(self.desk, self.row['id'])['steps']}['constitution']['status'], 'annulee')
        with self.assertRaises(Stop) as ex:
            run(self.desk, {'request': step['request']})
        self.assertEqual(str(ex.exception), 'demande_annulee')

    def test_p08_revalidated_plan_does_not_revive_the_old_child(self):
        from agent import initiatives5636 as i
        from agent.docrequest520 import run
        i.validate(self.desk, self.row['id'], {'id': self.row['id'], 'matter': t34.MID, 'step_constitution': True})
        i.execute(self.desk, self.row['id'], version=1)
        step = {s['id']: s for s in i._row(self.desk, self.row['id'])['steps']}['constitution']
        self.assertTrue(i.child_authorized(self.desk, step['request']))
        self.desk.db.execute('UPDATE initiatives5636 SET version=version+1 WHERE id=?', (self.row['id'],))   # nouvelle validation
        self.desk.db.commit()
        self.assertFalse(i.child_authorized(self.desk, step['request']))
        with self.assertRaises(Stop):
            run(self.desk, {'request': step['request']})
        self.assertEqual(self.desk.db.execute('SELECT status FROM docreq520 WHERE id=?', (step['request'],)).fetchone()['status'], 'annule')
        self.assertTrue(i.child_authorized(self.desk, 'demande-sans-initiative'))


class AuditReminders(t37._Linked):
    """F06 (P09) : texte corrigé conservé après un paiement partiel ; relecture confirmée avant tout dépôt."""

    class Api:
        base = 'https://factures.example.test'

        def __init__(self, balance):
            self.balance = balance

        def request(self, method, url, headers=None, limit=0):
            return json.dumps({'data': {'balance': self.balance, 'status_id': '2', 'due_date': '2026-09-01'}}).encode()

    def setUp(self):
        super().setUp()
        import test_v5636 as t36
        from agent import relances5636 as r
        self.r = r
        t36.Relances.seed(self, '2026-09-01', 1200.0)
        r.prepare(self.desk, today=date(2026, 10, 12), refresh=False)
        self.rid = self.desk.db.execute('SELECT id FROM relances5636').fetchone()['id']

    def row(self):
        return self.desk.db.execute('SELECT * FROM relances5636 WHERE id=?', (self.rid,)).fetchone()

    def test_p09_custom_text_survives_and_review_is_required(self):
        drafts = []
        custom = {'subject': 'Facture F-2026-042 — solde restant', 'body': 'Madame, Monsieur,\n\nIl reste 800,00 € à régler.\n\nCordialement'}
        with unittest.mock.patch('agent.webmail5628.save', side_effect=lambda desk, d, owner='cabinet': drafts.append(d) or {'uid': '9'}):
            with self.assertRaises(Qualified) as ex:
                self.r.deposit(self.desk, self.rid, dict(custom), http=self.Api(800))
            self.assertEqual(str(ex.exception), 'relance_solde_modifie')
            row = self.row()
            self.assertEqual((row['subject'], row['body'], row['state']), (custom['subject'], custom['body'], 'a_relire'))
            self.assertTrue(row['previous_balance'])                                                  # ancien solde conservé
            with self.assertRaises(Qualified) as ex:                                                 # rechargement, second dépôt sans relecture
                self.r.deposit(self.desk, self.rid, {}, http=self.Api(800))
            self.assertEqual(str(ex.exception), 'relance_a_relire')
            self.assertEqual(drafts, [])
            page = self.request('/relances')['body'] if self._has_page() else ''
            if page:
                self.assertIn('J’ai relu', page)
            out = self.r.deposit(self.desk, self.rid, {'relu': '1'}, http=self.Api(800))
        self.assertEqual(len(drafts), 1)
        self.assertIn('800,00 €', drafts[0]['html'])
        self.assertNotIn('1 200', drafts[0]['html'])
        self.assertEqual(drafts[0]['subject'], custom['subject'])
        self.assertIn('relecture confirmée', out['message'])

    def _has_page(self):
        try:
            return self.request('/relances').get('status', '200').startswith('200')
        except Exception:   # noqa: BLE001
            return False

    def test_p09_generated_text_is_rebuilt_with_the_new_balance(self):
        with unittest.mock.patch('agent.webmail5628.save', return_value={'uid': '9'}):
            with self.assertRaises(Qualified):
                self.r.deposit(self.desk, self.rid, {}, http=self.Api(500))
        row = self.row()
        self.assertIn('500,00 €', row['body'])
        self.assertNotIn('1 200,00 €', row['body'])
        self.assertEqual(row['state'], 'a_relire')


class AuditNextcloudApp(unittest.TestCase):
    """F08 (P11) : dans l'application Nextcloud, une réponse de session arrivée après un nouveau choix est ignorée."""

    def test_p11_stale_session_answers_are_ignored(self):
        src = (ROOT / 'nextcloud-app' / 'axiorhub_pilote' / 'src' / 'App.vue')
        if not src.exists():
            src = ROOT / 'nextcloud-app' / 'src' / 'App.vue'
        if not src.exists():
            self.skipTest('application Nextcloud absente de cette distribution')
        text = src.read_text(encoding='utf-8')
        self.assertIn('if (seq === sessionSeq)', text)
        self.assertIn('invalidateSession()', text)
        self.assertIn("selected === s.path ? 'page'", text)


class Library(unittest.TestCase):
    """Bibliothèque de modèles : catalogue, clauses, numérotation, variables (sans base de données)."""

    def tpl(self, ident):
        from agent import bibliotheque5639 as b
        return dict(b.BUILTIN[ident], origine='axiorhub', personnalise=False, word='')

    def test_catalogue_covers_the_requested_forms_documents_and_laws(self):
        from agent import bibliotheque5639 as b
        ids = set(b.BUILTIN)
        for forme in ('sas', 'sasu', 'selas', 'sarl', 'eurl', 'selarl', 'sci', 'scm', 'scp'):
            self.assertIn('statuts_' + forme, ids)
        for ident in ('pacte_associes', 'pacte_actionnaires', 'lettre_intention', 'protocole_conditions', 'compromis_fonds', 'cession_titres',
                      'cession_fonds', 'contrat_prestation', 'accord_confidentialite', 'cgv_b2b', 'compte_courant'):
            self.assertIn(ident, ids)
        for droit in ('DE', 'ES', 'EN'):
            cats = {t['categorie'] for t in b.BUILTIN.values() if t['droit'] == droit}
            self.assertEqual(cats, {'statuts', 'pacte', 'operation', 'contrat'}, droit)
        for ident, t in b.BUILTIN.items():
            t = self.tpl(ident)
            for flags in ({}, {'clause_' + k: True for k, _, _, _ in t['clauses']}, {'clause_' + k: False for k, _, _, _ in t['clauses']}):
                text = b.plain_text(b.render(t, b.values_for(t, {}), b.flags_for(t, flags)))
                for leftover in ('[[', ']]', '{{', '}}', '\n## '):
                    self.assertNotIn(leftover, text, ident)
            self.assertTrue(set(t['resume']) >= {'fr', 'en', 'de', 'es'}, ident)

    def test_sas_clauses_numbering_and_variables(self):
        from agent import bibliotheque5639 as b
        t = self.tpl('statuts_sas')
        base = b.plain_text(b.render(t, b.values_for(t, {'denomination': 'EXEMPLE SAS'}), b.flags_for(t, {})))
        self.assertIn('Agrément des cessions', base)
        self.assertIn('Droit de préemption', base)
        self.assertNotIn('sortie conjointe', base)
        self.assertIn('[À COMPLÉTER : Adresse du siège social]', base)
        full = b.render(t, b.values_for(t, {'denomination': 'EXEMPLE SAS'}), b.flags_for(t, {'clause_sortie_conjointe': True, 'clause_sortie_forcee': True,
                                                                                           'clause_inalienabilite': '1', 'clause_agrement': False}))
        text = b.plain_text(full)
        self.assertIn('Droit de sortie conjointe', text)
        self.assertIn('Obligation de sortie forcée', text)
        self.assertIn('L. 227-13', text)
        self.assertNotIn('Agrément des cessions', text)
        numbers = [int(x.split()[1]) for x, s in full if s == 'article']
        self.assertEqual(numbers, list(range(1, len(numbers) + 1)))                                   # numérotation continue après les clauses
        self.assertIn('EXEMPLE SAS', text)
        self.assertIn('société par actions simplifiée', text)

    def test_foreign_templates_use_their_own_language_and_placeholders(self):
        from agent import bibliotheque5639 as b
        de = b.plain_text(b.render(self.tpl('de_satzung_gmbh'), {}, {'vinkulierung': True}))
        self.assertIn('§ 1 Firma, Sitz', de)
        self.assertIn('[ZU ERGÄNZEN: Firma der Gesellschaft]', de)
        es = b.plain_text(b.render(self.tpl('es_estatutos_sl'), {}, {}))
        self.assertIn('Artículo 1. Denominación', es)
        self.assertIn('administrador único', es)
        en = b.plain_text(b.render(self.tpl('en_articles_ltd'), {}, {'drag_along': True}))
        self.assertIn('1. Preliminary', en)
        self.assertIn('Drag-along', en)

    def test_conditions_nested_inline_and_negated(self):
        from agent import bibliotheque5639 as b
        lines = ['A', '[[si x]]', 'B', '[[si y]]', 'C', '[[sinon]]', 'D', '[[fin]]', '[[fin]]', '[[si !x]]', 'E', '[[fin]]',
                 'F [[si y]]oui[[sinon]]non[[fin]] G']
        self.assertEqual(b.apply_conditions(lines, {'x': True, 'y': False}), ['A', 'B', 'D', 'F non G'])
        self.assertEqual(b.apply_conditions(lines, {'x': False, 'y': True}), ['A', 'E', 'F oui G'])

    def test_request_matching_is_conservative(self):
        from agent import bibliotheque5639 as b

        class Desk:
            pass
        with unittest.mock.patch.object(b, 'catalog', return_value=[self.tpl(i) for i in b.BUILTIN]):
            pick = lambda text, lang='fr': (b.match(Desk(), text, lang) or {}).get('id')   # noqa: E731
            self.assertEqual(pick('Rédige les statuts d’une SAS au capital de 1 000 euros'), 'statuts_sas')
            self.assertEqual(pick('Prépare les statuts de la SCI familiale'), 'statuts_sci')
            self.assertEqual(pick('Projet de pacte d’actionnaires pour la levée de fonds'), 'pacte_actionnaires')
            self.assertEqual(pick('Lettre d’intention pour le rachat de la société cible'), 'lettre_intention')
            self.assertEqual(pick('Compromis de vente du fonds de commerce de boulangerie'), 'compromis_fonds')
            self.assertEqual(pick('Bitte eine Satzung für eine GmbH entwerfen', 'de'), 'de_satzung_gmbh')
            self.assertIsNone(pick('Courrier à la SAS Exemple pour réclamer la facture'))
            self.assertIsNone(pick('Note sur la loi applicable au litige'))


class LibraryFlows(_Corp):
    """Bibliothèque de modèles dans Corporate : écran, préparation (Word, dossier, IA), personnalisation, modèle Word, modèles ajoutés."""

    def api(self, route, data=None):
        import test_v5636 as t36
        return t36.api(self, route, data)

    def test_page_lists_templates_and_prefills_the_company(self):
        page = self.request('/corporate', query='vue=bibliotheque')['body']
        self.assertIn('Bibliothèque de modèles', page)
        self.assertIn('Statuts de SAS', page)
        self.assertNotIn('Gesellschaftsvertrag', page)                                               # filtre par défaut : droit de la langue
        page = self.request('/corporate', query='vue=bibliotheque&droit=DE')['body']
        self.assertIn('Gesellschaftsvertrag (Satzung) einer GmbH', page)
        detail = self.request('/corporate', query='societe=%s&vue=bibliotheque&modele=statuts_sas' % self.cid)['body']
        self.assertIn('value="EXEMPLE SAS"', detail)
        self.assertIn('Paul MARTIN — 6000 actions', detail)
        self.assertIn('name="clause_sortie_conjointe"', detail)
        self.assertIn('Bibliothèque de modèles', self.request('/corporate', query='societe=%s' % self.cid)['body'])
        foreign = self.request('/corporate', query='vue=bibliotheque&modele=de_satzung_gmbh')['body']
        self.assertIn('à faire valider par un conseil local', foreign)

    def test_download_dossier_and_owner_checks(self):
        from agent import bibliotheque5639 as b
        out = b.generate(self.desk, {'modele': 'statuts_sas', 'societe': self.cid, 'denomination': 'EXEMPLE SAS', 'clause_sortie_conjointe': True},
                         'avocat-a')
        jeton = out['goto'].rsplit('fichier=', 1)[1]
        got = b.download(self.desk, jeton, 'avocat-a')['download']
        raw = __import__('base64').b64decode(got['base64'])
        import zipfile, io
        xml = zipfile.ZipFile(io.BytesIO(raw)).read('word/document.xml').decode()
        self.assertIn('EXEMPLE SAS', xml)
        self.assertIn('Droit de sortie conjointe', xml)
        self.assertIn('trame de travail à relire', xml)
        with self.assertRaises(Stop):
            b.download(self.desk, jeton, 'avocat-b')                                                 # jeton d'un autre utilisateur
        out = b.generate(self.desk, {'modele': 'cession_titres', 'societe': self.cid, 'destination': 'dossier'}, dav=self.docs)
        self.assertIn('/JURIDIQUE/', out['path'])
        self.assertTrue(out['path'].endswith('.docx'))
        self.assertEqual(len(self.docs.puts), 1)
        with self.assertRaises(Stop):
            b.generate(self.desk, {'modele': 'cession_titres', 'destination': 'dossier'}, dav=self.docs)   # ni société ni dossier

    def test_customised_text_adds_variables_and_clauses_and_can_be_restored(self):
        from agent import bibliotheque5639 as b
        t = b.template(self.desk, 'accord_confidentialite')
        text = '\n'.join(t['texte']) + '\n[[si audit_securite]]\n## Audit de sécurité\nLe destinataire accepte un audit par {{auditeur}}.\n[[fin]]'
        b.customize(self.desk, {'modele': 'accord_confidentialite', 'texte': text})
        t = b.template(self.desk, 'accord_confidentialite')
        self.assertTrue(t['personnalise'])
        self.assertIn('audit_securite', [k for k, _, _, _ in t['clauses']])
        self.assertIn('auditeur', [k for k, _, _ in t['variables']])
        rendered = b.plain_text(b.render(t, b.values_for(t, {'auditeur': 'Cabinet Exemple'}), b.flags_for(t, {'clause_audit_securite': True})))
        self.assertIn('Audit de sécurité', rendered)
        self.assertIn('Cabinet Exemple', rendered)
        with self.assertRaises(Stop):
            b.customize(self.desk, {'modele': 'accord_confidentialite', 'texte': 'A\n[[si x]]\nB'})   # bloc non fermé
        b.restore(self.desk, {'modele': 'accord_confidentialite'})
        self.assertFalse(b.template(self.desk, 'accord_confidentialite')['personnalise'])

    def test_word_round_trip_keeps_conditions_numbering_and_variables(self):
        from agent import bibliotheque5639 as b
        from agent import corporate5636 as co
        import base64, zipfile, io
        raw = base64.b64decode(b.word_template(self.desk, 'accord_confidentialite')['download']['base64'])
        xml = zipfile.ZipFile(io.BytesIO(raw)).read('word/document.xml').decode()
        self.assertIn('[[si aide_axiorhub]]', xml)
        self.assertIn('{{partie_a}}', xml)
        source = co.import_template(self.desk, {'filename': 'Mon NDA.docx', 'data': base64.b64encode(raw).decode()})['source']
        b.set_word(self.desk, {'modele': 'accord_confidentialite', 'path': source})
        self.assertEqual(b.template(self.desk, 'accord_confidentialite')['word'], source)
        out = b.generate(self.desk, {'modele': 'accord_confidentialite', 'partie_a': 'ALPHA SAS', 'clause_reciproque': False,
                                     'clause_clause_penale': True}, 'cabinet')
        doc = b.download(self.desk, out['goto'].rsplit('fichier=', 1)[1])['download']
        xml = zipfile.ZipFile(io.BytesIO(base64.b64decode(doc['base64']))).read('word/document.xml').decode()
        self.assertIn('ALPHA SAS', xml)
        self.assertIn('Article 1 — Objet', xml)
        self.assertIn('Clause pénale', xml)
        self.assertIn('la Partie A est amenée', xml)
        for gone in ('[[', 'TRAME AXIORHUB', 'Non-sollicitation'):
            self.assertNotIn(gone, xml)
        b.set_word(self.desk, {'modele': 'accord_confidentialite', 'path': ''})
        self.assertEqual(b.template(self.desk, 'accord_confidentialite')['word'], '')

    def test_cabinet_templates_can_be_added_and_removed(self):
        from agent import bibliotheque5639 as b
        out = b.add(self.desk, {'titre': 'Lettre de mission corporate', 'categorie': 'contrat', 'droit': 'FR',
                                'texte': '# LETTRE DE MISSION\n## Mission\nLe cabinet assiste {{client}} [[si forfait]]au forfait[[fin]].'})
        t = b.template(self.desk, out['id'])
        self.assertEqual((t['origine'], t['titre']), ('cabinet', 'Lettre de mission corporate'))
        self.assertIn(out['id'], [x['id'] for x in b.catalog(self.desk)])
        self.assertIn('Article 1 — Mission', b.plain_text(b.render(t, {'client': 'ALPHA'}, {})))
        with self.assertRaises(Stop):
            b.add(self.desk, {'titre': 'Vide', 'categorie': 'contrat', 'droit': 'FR'})
        with self.assertRaises(Stop):
            b.remove(self.desk, {'modele': 'statuts_sas'})                                           # modèle d'origine : jamais supprimé
        b.remove(self.desk, {'modele': out['id']})
        with self.assertRaises(Stop):
            b.template(self.desk, out['id'])

    def test_ai_drafting_receives_the_library_template(self):
        from agent import bibliotheque5639 as b
        out = b.generate(self.desk, {'modele': 'pacte_actionnaires', 'societe': self.cid, 'destination': 'ia', 'clause_bad_leaver': True,
                                     'consignes': 'Prévoir une clause de liquidité à 5 ans.'})
        row = self.desk.db.execute('SELECT request, kind FROM docreq520 WHERE id=?', (out['request'],)).fetchone()
        self.assertIn('[trame:', row['request'])
        self.assertIn('clause de liquidité', row['request'])
        trame = b.trame_for_request(self.desk, row['request'])
        self.assertEqual(trame['titre'], 'Pacte d’actionnaires')
        self.assertIn('good leaver', trame['texte'])
        self.assertIn('EXEMPLE SAS', trame['texte'])
        auto = b.trame_for_request(self.desk, 'Rédige les statuts d’une SARL familiale')
        self.assertEqual(auto['titre'], 'Statuts de SARL')
        self.assertIsNone(b.trame_for_request(self.desk, 'Courrier de relance au client'))
        captured = {}

        class Model:
            def complete(self, messages, **k):
                captured['payload'] = messages[-1]['content']
                return json.dumps({'titre': 'Statuts', 'nom_fichier': 'Statuts', 'paragraphes': [{'style': 'texte', 'texte': 'x'}], 'sources': [],
                                   'a_completer': []})
        from agent import docrequest520
        with unittest.mock.patch.object(docrequest520, '_model', return_value=Model()), \
                unittest.mock.patch('agent.composition567.compact_context', side_effect=lambda desk, ctx, *a, **k: ctx):
            docrequest520.draft(self.desk, 'Rédige les statuts d’une SAS', 'contrat', {'dossier': 'DEMO'}, '', '')
        self.assertIn('trame_bibliotheque', captured['payload'])
        self.assertIn('Statuts de SAS', captured['payload'])

    def test_roles_assistant_prepares_but_does_not_edit_the_library(self):
        from agent import fonctions5636
        out = fonctions5636.post(self.desk, 'corporate/bibliotheque/generer', {'modele': 'cgv_b2b'}, 'assistant-1', 'assistant', '')
        self.assertIn('goto', out)
        for route in ('corporate/bibliotheque/personnaliser', 'corporate/bibliotheque/ajouter', 'corporate/bibliotheque/word',
                      'corporate/bibliotheque/retablir', 'corporate/bibliotheque/retirer'):
            with self.assertRaises(Stop):
                fonctions5636.post(self.desk, route, {'modele': 'cgv_b2b', 'texte': 'x'}, 'assistant-1', 'assistant', '')
        listing = fonctions5636.get(self.desk, 'corporate/bibliotheque', {}, 'assistant-1', 'assistant')
        self.assertGreaterEqual(len(listing['modeles']), 47)

class ErrorPages(t37._Linked):
    """Pages d'erreur lisibles (400, 401, 404, 405, 500, 503) avec boutons, et moins d'occasions de les voir."""

    def raw(self, path, method='GET', host='cabinet.example.test', referer='', auth=True, query=''):
        import base64 as b64
        import io as _io
        env = {'REQUEST_METHOD': method, 'PATH_INFO': path, 'QUERY_STRING': query, 'HTTP_HOST': host, 'HTTP_X_FORWARDED_PROTO': 'https',
               'wsgi.url_scheme': 'http', 'wsgi.input': _io.BytesIO(b''), 'CONTENT_LENGTH': '0'}
        if referer:
            env['HTTP_REFERER'] = referer
        if auth:
            env['HTTP_AUTHORIZATION'] = 'Basic ' + b64.b64encode(('admin:' + self.password).encode()).decode()
        out = {}

        def start(status, headers):
            out.update(status=status, headers=dict(headers))
        out['body'] = b''.join(self.app(env, start)).decode()
        return out

    def assert_graphic(self, body):
        self.assertIn('/static/erreurs5639.css', body)
        self.assertIn('↻ ', body)
        self.assertIn('⌂ Accueil', body)
        self.assertNotIn('<style', body)                                                            # politique de sécurité : pas de style en ligne
        self.assertNotIn('<script', body)

    def test_unknown_page_is_a_graphic_404(self):
        r = self.raw('/agent-courriel/page-qui-n-existe-pas', referer='https://cabinet.example.test/agent-courriel/dossiers')
        self.assertTrue(r['status'].startswith('404'), r['status'])
        self.assert_graphic(r['body'])
        self.assertIn('Page introuvable', r['body'])
        self.assertIn('href="/agent-courriel/dossiers"', r['body'])                                 # « Revenir en arrière » : page d'origine

    def test_refused_action_keeps_its_explanation_and_offers_buttons(self):
        r = self.raw('/agent-courriel/corporate', query='societe=inexistante')
        self.assertTrue(r['status'].startswith('400'), r['status'])
        self.assert_graphic(r['body'])
        self.assertIn('Action non effectuée', r['body'])
        self.assertIn('href="/agent-courriel/corporate?societe=inexistante"', r['body'])            # « Recharger » : même adresse
        evil = self.raw('/agent-courriel/corporate', query='societe=inexistante', referer='https://evil.example/x')
        self.assertNotIn('evil.example', evil['body'])                                            # jamais de retour vers un autre site

    def test_stylesheet_is_public_and_carries_no_data(self):
        r = self.raw('/agent-courriel/static/erreurs5639.css', auth=False)
        self.assertTrue(r['status'].startswith('200'))
        self.assertIn('.axe-card', r['body'])
        self.assertTrue(r['headers']['Content-Type'].startswith('text/css'))

    def test_login_window_closed_gives_a_readable_page(self):
        r = self.raw('/agent-courriel/dossiers', auth=False)
        self.assertTrue(r['status'].startswith('401'))
        self.assertIn('WWW-Authenticate', r['headers'])
        self.assert_graphic(r['body'])
        self.assertIn('Connexion nécessaire', r['body'])
        api = self.raw('/agent-courriel/api440/m5636/corporate/societes', auth=False)
        self.assertEqual(json.loads(api['body'])['error'], 'authentification_requise')               # API : JSON inchangé

    def test_internal_error_retries_once_then_explains(self):
        from agent import web
        try:
            raise RuntimeError('incident fictif')
        except RuntimeError:
            import contextlib, io as _io
            with contextlib.redirect_stderr(_io.StringIO()):
                status, kind, body = web.internal_error_response({'PATH_INFO': '/agent-courriel/dossiers', 'REQUEST_METHOD': 'GET'}, 'text/html', '')
                again = web.internal_error_response({'PATH_INFO': '/agent-courriel/dossiers', 'QUERY_STRING': '_reessai=1', 'REQUEST_METHOD': 'GET'},
                                                    'text/html', '')[2]
        self.assertTrue(status.startswith('500'))
        self.assertIn('Interface indisponible', body)
        self.assertIn('content="5;url=/agent-courriel/dossiers?_reessai=1"', body)
        self.assertNotIn('http-equiv="refresh"', again)                                             # un seul nouvel essai
        self.assertNotIn('incident fictif', body)

    def test_other_name_of_the_same_site_is_redirected_not_refused(self):
        ok = self.raw('/agent-courriel/dossiers', host='Cabinet.Example.Test:443')
        self.assertTrue(ok['status'].startswith('200'), ok['status'])
        foreign = self.raw('/agent-courriel/dossiers', host='evil.example')
        self.assertTrue(foreign['status'].startswith('400'))                                        # autre nom : refus (DNS rebinding)
        self.assertNotIn('Location', foreign['headers'])
        refused = self.raw('/agent-courriel/action', method='POST', host='localhost:8769')
        self.assertTrue(refused['status'].startswith('400'))
        from urllib.parse import urlsplit
        from agent.web import loopback_alias
        poste = urlsplit('http://127.0.0.1:8769')
        self.assertTrue(loopback_alias('localhost:8769', poste))                                    # poste : l'autre nom de la machine
        self.assertFalse(loopback_alias('localhost:8770', poste))
        self.assertFalse(loopback_alias('evil.example:8769', poste))
        self.assertFalse(loopback_alias('localhost', urlsplit('https://cabinet.example.test')))

    def test_apache_shows_a_self_reloading_page_while_the_engine_restarts(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location('interface_installer_5639', ROOT / 'install-interface.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        conf = module.apache_config('courriel.example.com')
        for code in (502, 503, 504):
            self.assertIn('ErrorDocument %d "' % code, conf)
        self.assertIn("http-equiv='refresh' content='5'", conf)
        before = module.apache_config('courriel.example.com', errors=False)
        self.assertNotIn('ErrorDocument', before)
        src = (ROOT / 'install-interface.py').read_text(encoding='utf-8')
        self.assertIn('before=apache_config(host,errors=False)', src)                               # configuration 5.6.38 reconnue, remplacée
        for path in ('deploy/vps/apache/axiorhub.conf', 'deploy/apache-agent.example.conf'):
            self.assertIn('ErrorDocument 503 "', (ROOT / path).read_text(encoding='utf-8'))

if __name__ == '__main__':
    unittest.main()
