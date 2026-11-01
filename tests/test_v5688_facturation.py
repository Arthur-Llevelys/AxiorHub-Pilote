"""5.6.88 : lot facturation — temps par personne, contribution au chiffre d'affaires, relances des factures internes, accusés de paiement, solde du
dossier, relevé automatique de la facturation électronique. Données fictives, faux services : aucune plateforme réelle n'est exercée ici."""
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import inspect
import json
import sqlite3
import unittest
from unittest.mock import patch

import test_v5653 as t53
import test_v5652 as base
from agent import accuses5688, contribution5688 as contrib, dossier5679, efacture5670 as ef, efacture5688 as auto, facturation5653 as fact, membres5650 as membres
from agent import metier500 as m5, relances5636 as rel, time500, web500, web5670
from agent.common import Stop

ACTOR, MID = t53.ACTOR, t53.MID
MARTIN = 'martin@example.test'
BERNARD = 'bernard@example.test'
ASSISTANT = 'assistant@example.test'
DRAFT = 'agent.webmail5628.save'


def env(owner, role):
    return {'HTTP_X_AXIORHUB_USER': owner, 'HTTP_X_AXIORHUB_ROLE': role}


class Scene(base.Base):
    """Factures réellement émises par le registre (TVA 20 %)."""
    n = 0
    setUp0 = base.Base.setUp
    invoice = t53.Billing.invoice
    approve_issue = t53.Billing.approve_issue

    def setUp(self):
        self.setUp0()
        fact.save_entity(self.desk, 'admin@example.test', 'administrateur', t53.ENTITY)
        fact.save_client(self.desk, ACTOR, 'avocat', MID, t53.CLIENT)

    def issued(self, amount='1200'):
        Scene.n += 1
        return self.approve_issue(self.invoice(amount), 'cle-essai-%08d' % Scene.n)

    def time_invoice(self, *rows):
        """rows : (personne, minutes, taux) ; facture au temps passé émise."""
        for who, minutes, rate in rows:
            time500.add_manual(self.desk, MID, '2026-09-10', minutes, 'Analyse des pièces', rate, who)
        d = fact.prepare(self.desk, ACTOR, 'avocat', 'facture', MID, {'sous_type': 'temps', 'date_prestation': '2026-09-10'})
        Scene.n += 1
        return self.approve_issue(d, 'cle-essai-%08d' % Scene.n)

    def pay(self, d, amount, day='2026-10-10', ref=''):
        return fact.record_payment(self.desk, ACTOR, 'avocat', d['id'], amount, day, 'virement', ref)

    def due_on(self, d, day):
        snap = json.loads(self.desk.db.execute('SELECT snapshot_json FROM b53_documents WHERE id=?', (d['id'],)).fetchone()[0])
        snap['dates']['echeance'] = day
        self.desk.db.execute('UPDATE b53_documents SET snapshot_json=? WHERE id=?', (json.dumps(snap, ensure_ascii=False), d['id']))
        self.desk.db.commit()

    def rows(self):
        rel.schema(self.desk)
        return self.desk.db.execute('SELECT * FROM relances5636 ORDER BY id').fetchall()


# ---------------------------------------------------------------------------------------------------- 1. temps par personne
class Migration(unittest.TestCase):
    class Desk:
        def __init__(self):
            self.db = sqlite3.connect(':memory:')
            self.db.row_factory = sqlite3.Row
            self.db.executescript(m5.SCHEMA)                  # base antérieure : la table n'a pas de colonne auteur

    def test_anciennes_lignes_restent_non_attribuees_et_migration_rejouable(self):
        d = self.Desk()
        d.db.execute("INSERT INTO time500_entries VALUES('e1','DOS-001','2026-01-02',60,20000,20000,'Ancien','saisie_manuelle','','x','x')")
        d.db.commit()
        m5.ensure_schema(d)
        m5.ensure_schema(d)
        names = [r[1] for r in d.db.execute('PRAGMA table_info(time500_entries)')]
        self.assertEqual(names.count('author'), 1)
        row = d.db.execute('SELECT * FROM time500_entries').fetchone()
        self.assertEqual((row['author'], row['label']), ('', 'Ancien'))
        self.assertEqual(time500.person_label({}, row['author']), 'Non attribué')


class Temps(Scene):
    def test_saisie_validation_filtres_et_totaux(self):
        time500.add_manual(self.desk, MID, '2026-10-01', 60, 'Conclusions', '200', MARTIN)
        time500.add_manual(self.desk, MID, '2026-10-05', 30, 'Audience', '200', BERNARD)
        time500.add_manual(self.desk, MID, '2026-10-06', 15, 'Divers', '200')
        self.desk.db.execute("INSERT INTO time500_proposals VALUES('p1',?,?,1,45,'x','a_valider','x','','')", (MID, '2026-10-07'))
        time500.validate(self.desk, 'p1', 45, 'Validé', '200', BERNARD)
        self.assertEqual({e['label'] for e in time500.entries(self.desk, author=BERNARD)}, {'Audience', 'Validé'})
        self.assertEqual([e['label'] for e in time500.entries(self.desk, author='')], ['Divers'])
        self.assertEqual([e['label'] for e in time500.entries(self.desk, since='2026-10-02', until='2026-10-05')], ['Audience'])
        totals = time500.by_person(time500.entries(self.desk))
        self.assertEqual([t['author'] for t in totals], sorted([MARTIN, BERNARD]) + [''])          # « Non attribué » en dernier
        with self.assertRaisesRegex(Stop, 'date_invalide'):
            time500.entries(self.desk, since='2026-02-31')

    def test_attribuer_un_temps_ancien_et_nettoyage(self):
        out = time500.add_manual(self.desk, MID, '2026-10-01', 60, 'Ancien', '200')
        self.assertEqual(time500.set_author(self.desk, out['entry_id'], ' mar\x07tin@example.test ', ACTOR)['author'], MARTIN)
        with self.assertRaisesRegex(Stop, 'temps_absent'):
            time500.set_author(self.desk, 'absent', MARTIN)

    def test_reprise_vers_la_facture_interne_seulement(self):
        time500.add_manual(self.desk, MID, '2026-10-01', 60, 'Conclusions', '200', MARTIN)
        time500.add_manual(self.desk, MID, '2026-10-02', 30, 'Audience', '200', BERNARD)
        only = fact.prepare(self.desk, ACTOR, 'avocat', 'facture', MID, {'sous_type': 'temps', 'personne': MARTIN})
        self.assertEqual(len(only['snapshot']['lignes']), 1)
        self.assertEqual(only['snapshot']['lignes'][0]['sources'][0]['auteur'], MARTIN)
        text = fact.preview(self.desk, ACTOR, 'avocat', only['id'])['texte']
        self.assertNotIn('martin@', text)                                  # la personne ne s'imprime pas sur la facture du client

    def _seed(self):
        time500.add_manual(self.desk, MID, '2026-10-01', 60, 'Temps de MARTIN', '200', MARTIN)
        time500.add_manual(self.desk, MID, '2026-10-02', 30, 'Temps assistant', '200', ASSISTANT)

    def test_sans_le_droit_seuls_ses_temps_sont_visibles(self):
        self._seed()
        viewer = (ASSISTANT, 'assistant')
        self.assertEqual([x['label'] for x in time500.entries(self.desk, viewer=viewer)], ['Temps assistant'])
        self.assertEqual(time500.entries(self.desk, author=MARTIN, viewer=viewer), [])     # le filtre ne contourne pas le droit
        summary = web500.handle(self.desk, 'm500/time/summary', {}, 'GET', {'matter': MID}, viewer)
        self.assertEqual([x['label'] for x in summary['entries']], ['Temps assistant'])
        self.assertNotIn('Temps de MARTIN', web500.handle(self.desk, 'm500/time/export', {}, 'GET', {'matter': MID}, viewer)['download']['text'])
        html = web500._matter_detail(self.desk, MID, '/p', viewer)
        self.assertNotIn('Temps de MARTIN', html)
        self.assertIn('Temps assistant', html)

    def test_droit_facturation_aligne_sur_le_registre(self):
        self._seed()
        self.assertEqual(len(time500.entries(self.desk, viewer=(ACTOR, 'avocat'))), 2)
        self.assertEqual(len(time500.entries(self.desk, viewer=('admin@example.test', 'administrateur'))), 2)
        membres.set_rights(self.desk.c['state_dir'], 'admin@example.test', ACTOR, 'avocat', {'facturation': False})
        self.assertEqual(time500.entries(self.desk, viewer=(ACTOR, 'avocat')), [])
        membres.set_rights(self.desk.c['state_dir'], 'admin@example.test', ASSISTANT, 'assistant', {'facturation': True})
        self.assertEqual(len(time500.entries(self.desk, viewer=(ASSISTANT, 'assistant'))), 2)

    def test_registre_des_droits_illisible_vaut_refus(self):
        self._seed()
        with patch('agent.membres5650.effective_rights', side_effect=Stop('controle_acces_indisponible')):
            self.assertEqual(len(time500.entries(self.desk, viewer=(ASSISTANT, 'assistant'))), 1)

    def test_page_filtre_et_nom_echappe(self):
        time500.add_manual(self.desk, MID, '2026-10-01', 60, 'x', '200', '<b>MARTIN</b>')
        page = web500.honoraires_page(self.desk, {'csrf': 'x'}, '/p', {'matter': MID}, lambda title, html, *a: html, ('admin@example.test', 'administrateur'))
        self.assertIn('Temps par personne', page)
        self.assertNotIn('<b>MARTIN</b>', page)
        self.assertIn('name="author"', page)

    def test_ecriture_du_temps_sous_le_droit_facturation(self):
        self.assertEqual(membres.category('POST', '/api440/m500/time/author'), 'facturation')
        out = web500.handle(self.desk, 'm500/time/add', {'matter': MID, 'day': '2026-10-01', 'minutes': 30, 'label': 'x', 'rate': '200'}, viewer=(ACTOR, 'avocat'))
        self.assertEqual(self.desk.db.execute('SELECT author FROM time500_entries WHERE id=?', (out['entry_id'],)).fetchone()[0], ACTOR)   # sans choix : soi-même


# ---------------------------------------------------------------------------------------------------- 2. contribution
class Partage(unittest.TestCase):
    def test_somme_exacte_et_centime_au_plus_grand_reste(self):
        out = contrib.split(100, {'b': Decimal(1), 'a': Decimal(1), 'c': Decimal(1)})
        self.assertEqual(sum(v[0] for v in out.values()), 100)
        self.assertEqual(out['a'], (34, 1))                               # égalité : ordre de l'identifiant
        self.assertEqual((out['b'], out['c']), ((33, 0), (33, 0)))
        self.assertEqual(contrib.split(100, {'b': Decimal(1), 'a': Decimal(1), 'c': Decimal(1)}), out)

    def test_non_attribue_recoit_le_centime_en_dernier_et_negatif(self):
        out = contrib.split(1, {'': Decimal(1), 'z': Decimal(1)})
        self.assertEqual((out['z'], out['']), ((1, 1), (0, 0)))
        neg = contrib.split(-100, {'a': Decimal(1), 'b': Decimal(2)})
        self.assertEqual(sum(v[0] for v in neg.values()), -100)
        self.assertEqual(neg['b'], (-67, -1))

    def test_sans_poids_tout_reste_non_attribue(self):
        self.assertEqual(contrib.split(500, {}), {'': (500, 0)})

    def test_aucun_flottant_dans_le_module(self):
        source = inspect.getsource(contrib)
        self.assertNotIn('float(', source)
        self.assertNotIn('round(', source.replace('_round(', ''))


class Contribution(Scene):
    MONTH = '2026-10'

    def statement(self, month=None, person=None):
        return contrib.statement(self.desk, month or self.MONTH, person)

    def test_prorata_des_temps_factures_sur_le_ht_encaisse(self):
        d = self.time_invoice((MARTIN, 60, '200'), (BERNARD, 60, '100'))       # 200 € + 100 € HT, soit 360 € TTC
        self.pay(d, '180')                                                     # moitié : 150 € HT
        s = self.statement()
        self.assertEqual({r['personne']: r['base_cents'] for r in s['personnes']}, {MARTIN: 10000, BERNARD: 5000})
        self.assertEqual(s['total_base_cents'], 15000)
        self.assertEqual(s['lignes'][0]['origine'], 'temps_factures')
        for line in s['lignes']:
            self.assertIsInstance(line['base_cents'], int)
            self.assertTrue(all(isinstance(r['part_cents'], int) and isinstance(r['quote'], str) for r in line['repartition']))

    def test_reglements_fractionnes_retombent_sur_le_ht_exact(self):
        d = self.issued('100.01')                                              # TTC 120,01 : arrondis à chaque tranche
        time500.add_manual(self.desk, MID, '2026-09-01', 60, 'x', '200', MARTIN)
        for i, part in enumerate(('33.33', '33.33', '33.35', '20')):
            self.pay(d, part, '2026-10-%02d' % (10 + i), 'VIR-%d' % i)
        self.assertEqual(self.statement()['total_base_cents'], 10001)

    def test_trop_percu_ignore_et_avoir_apres_reglement_negatif(self):
        d = self.issued('1000')
        self.pay(d, '1300')                                                    # TTC 1 200 € : 100 € de trop
        self.assertEqual(self.statement()['total_base_cents'], 100000)
        av = fact.credit_note(self.desk, ACTOR, 'avocat', d['id'], 'Erreur de quantité', [{'nature': 'honoraires', 'libelle': 'Correction', 'quantite': '1', 'prix_unitaire_ht': '200'}])
        av = fact.approve(self.desk, ACTOR, 'avocat', av['id'], av['version'])
        fact.issue(self.desk, ACTOR, 'avocat', av['id'], 'cle-avoir-88000001')
        doc = self.desk.db.execute('SELECT * FROM b53_documents WHERE id=?', (d['id'],)).fetchone()
        lines = contrib.invoice_lines(self.desk, doc)
        self.assertEqual([x['base_cents'] for x in lines], [100000, -20000])
        self.assertEqual(lines[1]['nature'], 'Avoir')

    def test_frais_et_debours_exclus_de_la_base(self):
        snap = {'lignes': [{'nature': 'honoraires', 'montant_ht_cents': 100000}, {'nature': 'debours', 'montant_ht_cents': 5000}, {'nature': 'frais', 'montant_ht_cents': 1000}]}
        self.assertEqual(contrib.honoraires_ht(snap), 100000)

    def _provision_scene(self):
        """Solde après provision imputée : une seule base pour les mêmes honoraires."""
        fact.ensure_schema(self.desk)
        snap = {'lignes': [{'nature': 'honoraires', 'montant_ht_cents': 100000, 'sources': []}],
                'totaux': {'ht_cents': 100000, 'tva_cents': 20000, 'ttc_cents': 120000, 'deja_regle_cents': 60000, 'net_a_payer_cents': 60000}}
        self.desk.db.execute("INSERT INTO b53_documents(id,kind,subtype,matter,state,number,series,issue_date,snapshot_json,created_by,created,updated) "
                             "VALUES('fin-solde','facture','solde',?,'emise','F-2026-9000','F','2026-10-01',?,'x','x','x')", (MID, json.dumps(snap)))
        self.desk.db.execute("INSERT INTO b53_payments(id,document,amount_cents,day,method,source,actor,created) VALUES('pay-s','fin-solde',60000,'2026-10-20','virement','saisie','x','x')")
        self.desk.db.commit()

    def test_provision_imputee_non_comptee_deux_fois(self):
        self._provision_scene()
        self.assertEqual(self.statement()['total_base_cents'], 50000)          # 600 € TTC encaissés sur le solde = 500 € HT

    def test_facture_sans_temps_detaille_puis_sans_aucun_temps(self):
        d = self.issued('1000')
        self.pay(d, '600')
        first = self.statement()['lignes'][0]
        self.assertEqual((first['origine'], first['repartition'][0]['personne']), ('aucun', ''))
        time500.add_manual(self.desk, MID, '2000-01-01', 60, 'x', '200', MARTIN)
        self.assertEqual(self.statement()['lignes'][0]['origine'], 'temps_dossier')

    def test_ajustement_a_la_main_facture_puis_reglement(self):
        d = self.time_invoice((MARTIN, 60, '200'))
        self.pay(d, '120', ref='VIR-1')
        self.pay(d, '120', '2026-10-11', 'VIR-2')
        pays = self.desk.db.execute('SELECT id FROM b53_payments ORDER BY day').fetchall()
        contrib.set_parts(self.desk, ACTOR, 'avocat', {'cible': 'facture:' + d['id'], 'quote_0': '25', 'personne_0': MARTIN, 'quote_1': '75', 'personne_1': BERNARD})
        contrib.set_parts(self.desk, ACTOR, 'avocat', {'cible': 'reglement:' + pays[1]['id'], 'quote_0': '100', 'personne_0': BERNARD})
        s = self.statement()
        self.assertEqual({r['personne']: r['base_cents'] for r in s['personnes']}, {MARTIN: 2500, BERNARD: 17500})
        self.assertEqual([x['origine'] for x in s['lignes']], ['facture', 'reglement'])
        self.assertEqual(s['lignes'][1]['ajustement']['par'], ACTOR)
        contrib.clear_parts(self.desk, ACTOR, 'avocat', {'cible': 'reglement:' + pays[1]['id']})
        self.assertEqual(self.statement()['lignes'][1]['origine'], 'facture')

    def test_repartition_refusee_si_le_total_n_est_pas_100(self):
        d = self.issued('1000')
        with self.assertRaisesRegex(Stop, 'repartition_differente_de_100'):
            contrib.set_parts(self.desk, ACTOR, 'avocat', {'cible': 'facture:' + d['id'], 'quote_0': '33.33', 'personne_0': MARTIN, 'quote_1': '66.66', 'personne_1': BERNARD})
        with self.assertRaisesRegex(Stop, 'cible_repartition_absente'):
            contrib.set_parts(self.desk, ACTOR, 'avocat', {'cible': 'facture:absente', 'quote_0': '100', 'personne_0': MARTIN})

    def test_cle_seuil_et_historique(self):
        d = self.time_invoice((MARTIN, 300, '200'))                            # 1 000 € HT
        self.pay(d, '1200')
        contrib.save_key(self.desk, ACTOR, 'avocat', {'personne': MARTIN, 'debut': '2026-01', 'taux': '10'})
        contrib.save_key(self.desk, ACTOR, 'avocat', {'personne': MARTIN, 'debut': '2026-10', 'taux': '33,5', 'seuil': '200'})
        contrib.save_key(self.desk, ACTOR, 'avocat', {'personne': MARTIN, 'debut': '2026-11', 'taux': '99'})
        row = self.statement()['personnes'][0]
        self.assertEqual((row['cle']['taux'], row['assiette_cents'], row['retrocession_cents']), ('33.5', 80000, 26800))
        self.assertEqual(contrib.retrocession(15000, {'taux': '10', 'seuil_cents': 20000}), (0, 0))      # sous le seuil
        self.assertEqual(contrib.retrocession(-1000, {'taux': '10', 'seuil_cents': 20000}), (-1000, -100))   # régularisation visible
        with self.assertRaisesRegex(Stop, 'pourcentage_invalide'):
            contrib.save_key(self.desk, ACTOR, 'avocat', {'personne': MARTIN, 'debut': '2026-10', 'taux': '101'})
        with self.assertRaisesRegex(Stop, 'personne_requise'):
            contrib.save_key(self.desk, ACTOR, 'avocat', {'personne': '', 'debut': '2026-10', 'taux': '10'})

    def test_releve_par_personne_et_mois_invalide(self):
        d = self.time_invoice((MARTIN, 60, '200'), (BERNARD, 60, '100'))
        self.pay(d, '360')
        one = self.statement(person=BERNARD)
        self.assertEqual([r['personne'] for r in one['personnes']], [BERNARD])
        self.assertEqual(one['total_base_cents'], 10000)
        self.assertEqual(self.statement(month='2026-09')['lignes'], [])
        with self.assertRaisesRegex(Stop, 'mois_invalide'):
            self.statement(month='2026-13')

    def test_export_csv_indicatif_et_sans_formule(self):
        d = self.time_invoice(('=CMD()', 60, '200'))
        self.pay(d, '240')
        out = contrib.export_csv(self.desk, ACTOR, 'avocat', self.MONTH)['download']
        self.assertEqual(out['filename'], 'contribution-2026-10.csv')
        self.assertIn('pas une écriture comptable', out['text'].splitlines()[0])
        self.assertIn('200,00', out['text'])
        self.assertIn("'=CMD()", out['text'])
        self.assertNotIn(';=CMD()', out['text'])

    def test_page_dit_indicatif_et_rend_l_arrondi_visible(self):
        d = self.time_invoice((MARTIN, 60, '100'), (BERNARD, 60, '100'), (ASSISTANT, 60, '100'))
        self.pay(d, '100')                                                     # base 83,33 € : 2 centimes à répartir entre trois parts égales
        html = contrib.page(self.desk, {'csrf': 'x'}, '/p', env(ACTOR, 'avocat'), {'mois': self.MONTH})
        self.assertIn('pas une écriture comptable', html)
        self.assertIn('Écarts d’arrondi', html)
        for label in ('Afficher', 'Exporter le relevé (CSV)', 'Enregistrer la clé'):
            self.assertIn(label, html)

    def test_reservee_aux_droits_d_honoraires(self):
        d = self.time_invoice((MARTIN, 60, '200'))
        self.pay(d, '240')
        html = contrib.page(self.desk, {'csrf': 'x'}, '/p', env(ASSISTANT, 'assistant'), {'mois': self.MONTH})
        self.assertIn('réservée', html)
        self.assertNotIn('200,00', html)
        self.assertNotIn(MARTIN, html)
        with self.assertRaisesRegex(Stop, 'role_insuffisant'):
            contrib.api_get(self.desk, ASSISTANT, 'assistant', 'contribution/releve', {'mois': self.MONTH})
        with self.assertRaisesRegex(Stop, 'role_insuffisant'):
            contrib.export_csv(self.desk, ASSISTANT, 'assistant', self.MONTH)
        with self.assertRaisesRegex(Stop, 'role_insuffisant'):
            contrib.save_key(self.desk, ASSISTANT, 'assistant', {'personne': MARTIN, 'debut': '2026-10', 'taux': '10'})
        self.assertEqual(membres.category('POST', '/api440/m5653/contribution/cle'), 'facturation')
        membres.set_rights(self.desk.c['state_dir'], 'admin@example.test', ACTOR, 'avocat', {'facturation': False})
        with self.assertRaisesRegex(Stop, 'role_insuffisant'):
            contrib.api_get(self.desk, ACTOR, 'avocat', 'contribution/releve', {'mois': self.MONTH})

    def test_dossier_reserve_exclu_et_signale(self):
        d = self.time_invoice((MARTIN, 60, '200'))
        self.pay(d, '240')
        membres.set_viewer(frozenset({MID}))
        self.addCleanup(membres.set_viewer, None)
        s = self.statement()
        self.assertEqual((s['lignes'], s['reserves']), ([], 1))
        with self.assertRaisesRegex(Stop, 'dossier_reserve'):
            contrib.set_parts(self.desk, ACTOR, 'avocat', {'cible': 'facture:' + d['id'], 'quote_0': '100', 'personne_0': MARTIN})


# ---------------------------------------------------------------------------------------------------- 3. relances des factures internes
class Relances(Scene):
    def setUp(self):
        super().setUp()
        self.d = self.issued('1000')                                           # 1 200 € TTC
        self.due_on(self.d, '2026-09-01')

    def prepare(self, day):
        return rel.prepare(self.desk, today=date.fromisoformat(day), refresh=False)['created']

    def test_premier_palier_a_j15_sans_invoice_ninja(self):
        self.assertFalse(rel.configured(self.desk))
        self.assertEqual(self.prepare('2026-09-15'), 0)                        # J+14
        self.assertEqual(self.prepare('2026-09-16'), 1)                        # J+15
        row = self.rows()[0]
        self.assertEqual((row['invoice'], row['level'], row['state'], row['matter'], row['recipient'], row['client_id']),
                         ('b53:' + self.d['id'], 1, 'a_valider', MID, 'compta@example.test', ''))
        self.assertIn('1 200,00', row['body'].replace(' ', ' ').replace('\xa0', ' '))
        self.assertEqual(self.prepare('2026-09-17'), 0)                        # projet ouvert : pas de doublon

    def test_planification_quotidienne_avec_factures_internes(self):
        stamp = datetime(2026, 10, 12, 9, 0, tzinfo=timezone.utc).timestamp()
        self.assertEqual(len(rel.schedule(self.desk, stamp)), 1)
        self.assertEqual(rel.schedule(self.desk, stamp + 3600), [])
        rel.save_settings(self.desk, {'enabled': True, 'formulaire5688': '1', 'palier_1': '15'})
        self.assertFalse(rel.settings(self.desk)['factures_internes'])
        self.assertEqual(self.prepare('2026-10-12'), 0)                        # source interne désactivée

    def test_job_ne_touche_pas_invoice_ninja_s_il_n_est_pas_relie(self):
        with patch('agent.workstation.refresh_unpaid_invoices', side_effect=AssertionError('aucun appel')):
            self.assertEqual(rel.job(self.desk, {})['created'], 1)

    def test_panne_invoice_ninja_ne_prive_pas_les_factures_internes(self):
        with patch('agent.workstation.refresh_unpaid_invoices', side_effect=Stop('invoice_ninja_injoignable')):
            with self.assertRaises(Stop):
                rel.prepare(self.desk, today=date(2026, 9, 20), refresh=True)
        self.assertEqual(len(self.rows()), 1)

    def test_invoice_ninja_reste_une_source(self):
        from agent.workstation import ensure_schema
        ensure_schema(self.desk)
        cols = [r[1] for r in self.desk.db.execute('PRAGMA table_info(invoice_ninja_cache_v310)')]
        values = {'id': 'in1', 'number': 'IN-1', 'client_id': 'cli1', 'status': 'x', 'amount': 100, 'balance': 100, 'due_date': '2026-09-01', 'currency': 'EUR', 'invoice_url': '', 'updated': '2026-09-01'}
        self.desk.db.execute('INSERT INTO invoice_ninja_cache_v310(%s) VALUES(%s)' % (','.join(values), ','.join('?' * len(values))), list(values.values()))
        self.desk.db.commit()
        self.assertIn('balance', cols)
        self.assertEqual(self.prepare('2026-09-20'), 2)
        self.assertEqual({r['invoice'][:4] for r in self.rows()}, {'in1', 'b53:'})

    def test_paliers_reglables_et_controles(self):
        rel.save_settings(self.desk, {'enabled': True, 'formulaire5688': '1', 'factures_internes': True, 'accuse_paiement': True, 'palier_1': '5', 'palier_2': '40', 'palier_3': ''})
        self.assertEqual(rel.settings(self.desk)['paliers'], [5, 40])
        self.assertEqual(self.prepare('2026-09-06'), 1)
        for bad in ({'palier_1': '30', 'palier_2': '15'}, {'palier_1': '0'}, {'palier_1': ''}, {'palier_1': 'x'}, {'palier_1': '400'}):
            with self.assertRaisesRegex(Stop, 'reglage_hors_plage_paliers'):
                rel.save_settings(self.desk, {'enabled': True, 'formulaire5688': '1', **bad})

    def test_reglages_anciens_conserves_sans_le_formulaire(self):
        rel.save_settings(self.desk, {'enabled': True, 'hour': 9})
        s = rel.settings(self.desk)
        self.assertEqual((s['paliers'], s['factures_internes'], s['accuse_paiement']), ([15, 30, 60], True, True))

    def test_facture_reglee_jamais_relancee(self):
        self.pay(self.d, '1200')
        self.assertEqual(self.prepare('2026-10-30'), 0)

    def test_acompte_le_solde_est_relu_a_la_preparation(self):
        self.pay(self.d, '200')
        self.assertEqual(self.prepare('2026-09-20'), 1)
        self.assertIn('1 000,00', self.rows()[0]['body'].replace(' ', ' ').replace('\xa0', ' '))

    def test_facture_exclue_ou_en_litige(self):
        self.assertEqual(self.prepare('2026-09-20'), 1)
        rel.api_post(self.desk, 'relances/deposer', {'id': str(self.rows()[0]['id']), 'litige': '1'}, ACTOR)
        self.assertEqual(self.desk.db.execute('SELECT reason FROM relances_exclues5636').fetchone()[0], 'litige')
        self.assertEqual(self.prepare('2026-12-30'), 0)

    def test_statut_en_litige_de_la_plateforme(self):
        ef.ensure_schema(self.desk)
        self.desk.db.execute("INSERT INTO ef5670_suivi VALUES('superpdp','r1',?,'fr:207','En litige','2026-09-02T08:00:00')", (self.d['id'],))
        self.desk.db.commit()
        self.assertEqual(self.prepare('2026-09-20'), 0)

    def test_facture_sous_avoir_meme_en_preparation(self):
        fact.credit_note(self.desk, ACTOR, 'avocat', self.d['id'], 'Erreur', [{'nature': 'honoraires', 'libelle': 'Correction', 'quantite': '1', 'prix_unitaire_ht': '100'}])
        self.assertEqual(self.prepare('2026-09-20'), 0)

    def test_facture_rejetee_ou_rectifiee(self):
        self.desk.db.execute("UPDATE b53_documents SET state='rejetee' WHERE id=?", (self.d['id'],))
        self.desk.db.commit()
        self.assertEqual(self.prepare('2026-09-20'), 0)

    def test_meme_palier_jamais_deux_fois(self):
        self.assertEqual(self.prepare('2026-09-20'), 1)
        rel.dismiss(self.desk, self.rows()[0]['id'])
        self.assertEqual(self.prepare('2026-09-21'), 0)
        self.assertEqual(self.prepare('2026-09-29'), 0)                        # J+28 : palier 1 déjà proposé, palier 2 non atteint
        now = datetime.now(timezone.utc).isoformat()
        with self.assertRaises(sqlite3.IntegrityError):                        # garantie en base, indépendante du calcul
            self.desk.db.execute("INSERT INTO relances5636(invoice,number,client_id,level,subject,body,balance,due_date,state,created,updated) VALUES(?,?,'',1,'s','b','1','d','ecartee',?,?)",
                                 ('b53:' + self.d['id'], 'n', now, now))

    def _dismiss_all(self):
        self.desk.db.execute("UPDATE relances5636 SET updated='2026-01-01T00:00:00+00:00'")
        self.desk.db.commit()

    def test_palier_ecarte_le_deuxieme_ne_pretend_pas_a_un_rappel(self):
        self.assertEqual(self.prepare('2026-09-20'), 1)
        rel.dismiss(self.desk, self.rows()[0]['id'])
        self._dismiss_all()
        self.assertEqual(self.prepare('2026-10-05'), 1)
        row = self.rows()[-1]
        self.assertEqual(row['level'], 2)
        self.assertNotIn('précédent rappel', row['body'])

    def test_palier_envoye_le_suivant_le_rappelle(self):
        self.assertEqual(self.prepare('2026-09-20'), 1)
        first = self.rows()[0]
        with patch(DRAFT, return_value={'uid': '7'}):
            rel.deposit(self.desk, first['id'], {})
        self.assertEqual(self.prepare('2026-10-05'), 0)                        # déposée, envoi non confirmé : le palier suivant attend
        rel.confirm_sent(self.desk, first['id'])
        self.desk.db.execute("UPDATE relances5636 SET sent_at='2026-09-21T08:00:00+00:00'")
        self.desk.db.commit()
        self.assertEqual(self.prepare('2026-10-05'), 1)
        self.assertIn('précédent rappel', self.rows()[-1]['body'])

    def test_jamais_d_envoi_le_depot_passe_par_brouillons_apres_controle(self):
        self.assertEqual(self.prepare('2026-09-20'), 1)
        row = self.rows()[0]
        with patch(DRAFT, return_value={'uid': '7'}) as save:
            self.assertEqual(save.call_count, 0)                               # préparer ne dépose rien
            out = rel.deposit(self.desk, row['id'], {})
        self.assertEqual(save.call_args[0][1]['mode'], 'nouveau')
        self.assertIn('rien n’est envoyé', out['message'])
        self.assertEqual(self.rows()[0]['state'], 'deposee')

    def test_reglement_entre_preparation_et_depot(self):
        from agent.erreurs5634 import Qualified
        self.assertEqual(self.prepare('2026-09-20'), 1)
        row = self.rows()[0]
        self.pay(self.d, '200')
        with patch(DRAFT, side_effect=AssertionError('aucun dépôt')):
            with self.assertRaises(Qualified):
                rel.deposit(self.desk, row['id'], {})
        row = self.rows()[0]
        self.assertEqual(row['state'], 'a_relire')
        self.assertIn('1 000,00', row['body'].replace(' ', ' ').replace('\xa0', ' '))
        self.pay(self.d, '1000')
        with patch(DRAFT, side_effect=AssertionError('aucun dépôt')):
            with self.assertRaises(Qualified):
                rel.deposit(self.desk, row['id'], {'relu': '1'})
        self.assertEqual(self.rows()[0]['state'], 'perimee')

    def test_ton_adapte_au_client_d_un_avocat(self):
        for level in (1, 2, 3):
            for reminded in (True, False):
                subject, body = rel._text_cabinet(level, 'F-2026-0001', '1 200,00 €', '2026-09-01', 'Cabinet fictif', reminded)
                text = (subject + ' ' + body).lower()
                for word in ('mise en demeure', 'huissier', 'commissaire de justice', 'poursuite', 'contraint', 'pénalité', 'sous huit jours', 'tribunal'):
                    self.assertNotIn(word, text)
        self.assertIn('échéancier', rel._text_cabinet(1, 'F', '1 €', '2026-09-01', 'x', False)[1])
        self.assertIn('bâtonnier', rel._text_cabinet(3, 'F', '1 €', '2026-09-01', 'x', True)[1])

    def test_le_message_ne_dit_rien_du_dossier(self):
        self.assertEqual(self.prepare('2026-09-20'), 1)
        row = self.rows()[0]
        self.assertNotIn(MID, row['subject'] + row['body'])
        self.assertNotIn('bail', (row['subject'] + row['body']).lower())

    def test_page_sans_invoice_ninja_avec_paliers(self):
        self.assertEqual(self.prepare('2026-09-20'), 1)
        html = rel.page_html(self.desk, '/p', {})
        for text in ('Rappel courtois', 'palier_1', 'Facture en litige', 'Invoice Ninja n’est pas relié (facultatif)', 'accuse_paiement'):
            self.assertIn(text, html)
        membres.set_viewer(frozenset({MID}))
        self.addCleanup(membres.set_viewer, None)
        self.assertNotIn(self.d['number'] + ' —', rel.page_html(self.desk, '/p', {}))    # dossier réservé

    def test_bouton_preparer_sans_invoice_ninja(self):
        self.assertIn('created', rel.api_post(self.desk, 'relances/preparer', {}, ACTOR))


# ---------------------------------------------------------------------------------------------------- 4. accusé de paiement
class Accuses(Scene):
    def setUp(self):
        super().setUp()
        self.d = self.issued('1000')

    def test_proposition_unique_jamais_deposee_seule(self):
        with patch(DRAFT, side_effect=AssertionError('jamais de dépôt automatique')):
            self.pay(self.d, '200', ref='VIR-1')
            self.pay(self.d, '200', ref='VIR-1')                               # même référence : une seule proposition
        rows = accuses5688.pending(self.desk, self.d['id'])
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]['recipient'], rows[0]['state']), ('compta@example.test', 'a_valider'))
        text = rows[0]['body'].replace(' ', ' ').replace('\xa0', ' ')
        self.assertIn('200,00', text)
        self.assertIn('Il reste 1 000,00', text)
        self.assertNotIn(MID, rows[0]['subject'] + rows[0]['body'])

    def test_reglement_complet(self):
        self.pay(self.d, '1200')
        self.assertIn('intégralement réglée', accuses5688.pending(self.desk)[0]['body'])

    def test_depot_sur_decision_et_solde_relu(self):
        self.pay(self.d, '200', ref='VIR-1')
        row = accuses5688.pending(self.desk)[0]
        self.pay(self.d, '1000', ref='VIR-2')
        with patch(DRAFT, return_value={'uid': '9'}) as save:
            out = accuses5688.deposit(self.desk, ACTOR, 'avocat', row['id'], {})
        self.assertIn('intégralement réglée', save.call_args[0][1]['html'])    # texte non corrigé : actualisé
        self.assertIn('rien n’est envoyé', out['message'])
        self.assertEqual(len(accuses5688.pending(self.desk)), 1)               # reste celui du second règlement

    def test_texte_corrige_conserve(self):
        self.pay(self.d, '200')
        row = accuses5688.pending(self.desk)[0]
        with patch(DRAFT, return_value={'uid': '9'}) as save:
            accuses5688.deposit(self.desk, ACTOR, 'avocat', row['id'], {'body': 'Merci de votre règlement.'})
        self.assertIn('Merci de votre règlement.', save.call_args[0][1]['html'])

    def test_desactivable(self):
        rel.save_settings(self.desk, {'enabled': True, 'formulaire5688': '1', 'factures_internes': True, 'palier_1': '15'})
        self.assertFalse(rel.settings(self.desk)['accuse_paiement'])
        self.pay(self.d, '200')
        self.assertEqual(accuses5688.pending(self.desk), [])

    def test_une_panne_n_empeche_pas_le_reglement(self):
        with patch('agent.accuses5688.propose', side_effect=RuntimeError('panne')):
            out = self.pay(self.d, '200')
        self.assertEqual(out['payment_state'], 'partielle')

    def test_droit_deposer_exige_et_ecarter(self):
        self.pay(self.d, '200')
        row = accuses5688.pending(self.desk)[0]
        membres.set_rights(self.desk.c['state_dir'], 'admin@example.test', ACTOR, 'avocat', {'deposer': False})
        with patch(DRAFT, side_effect=AssertionError('aucun dépôt')):
            with self.assertRaisesRegex(Stop, 'role_insuffisant'):
                accuses5688.deposit(self.desk, ACTOR, 'avocat', row['id'], {})
        accuses5688.dismiss(self.desk, ACTOR, 'avocat', row['id'])
        self.assertEqual(accuses5688.pending(self.desk), [])
        with self.assertRaisesRegex(Stop, 'accuse_absent'):
            accuses5688.dismiss(self.desk, ACTOR, 'avocat', row['id'])

    def test_reglement_disparu_accuse_perime(self):
        self.pay(self.d, '200')
        row = accuses5688.pending(self.desk)[0]
        self.desk.db.execute('UPDATE b53_payments SET verified=0')
        self.desk.db.commit()
        with patch(DRAFT, side_effect=AssertionError('aucun dépôt')):
            with self.assertRaisesRegex(Stop, 'accuse_perime'):
                accuses5688.deposit(self.desk, ACTOR, 'avocat', row['id'], {})

    def test_section_de_la_page_echappee(self):
        self.pay(self.d, '200')
        self.desk.db.execute("UPDATE accuses5688 SET body='<script>x</script>'")
        self.desk.db.commit()
        html = accuses5688.section_html(self.desk, self.d['id'])
        self.assertIn('Déposer dans Brouillons', html)
        self.assertNotIn('<script>', html)


# ---------------------------------------------------------------------------------------------------- 5. solde du dossier
class Solde(Scene):
    def header(self):
        return dossier5679.balance_html(self.desk, '/p', MID).replace(' ', ' ').replace('\xa0', ' ')

    def test_aucune_facture_puis_solde_puis_a_jour(self):
        self.assertIn('aucune facture émise', self.header())
        d = self.issued('1000')
        self.assertIn('Solde dû : 1 200,00', self.header())
        self.pay(d, '200')
        self.assertIn('Solde dû : 1 000,00', self.header())                    # relu à chaque affichage
        self.pay(d, '1000')
        self.assertIn('rien à régler', self.header())
        self.pay(d, '50')
        self.assertIn('Trop-perçu : 50,00', self.header())

    def test_meme_solde_que_le_bloc_honoraires(self):
        d = self.issued('1000')
        self.pay(d, '300')
        self.assertEqual(fact.matter_balance(self.desk, MID)['solde_cents'], fact.matter_summary(self.desk, MID)['solde_connu_cents'])

    def test_lecture_impossible_n_est_pas_un_solde_nul(self):
        with patch('agent.facturation5653.matter_balance', side_effect=RuntimeError('panne')):
            self.assertIn('lecture impossible', self.header())

    def test_visible_dans_l_en_tete_de_tous_les_onglets(self):
        self.assertIn('balance_html', inspect.getsource(dossier5679.header_html))


# ---------------------------------------------------------------------------------------------------- droit fin « voir les honoraires »
class VoirHonoraires(Scene):
    def setUp(self):
        super().setUp()
        self.d = self.time_invoice((MARTIN, 60, '200'))
        self.pay(self.d, '100')
        self.state = self.desk.c['state_dir']
        self.addCleanup(membres.clear)

    def close(self):
        membres.set_rights(self.state, 'admin@example.test', ACTOR, 'avocat', {'voir_honoraires': False})

    def request(self):
        """Le contrôle central réel de la requête, comme devant la page Dossier."""
        self.assertIsNone(membres.gate(env(ACTOR, 'avocat'), self.state, '/dossier', {'id': MID}))

    def test_le_solde_du_dossier_le_respecte(self):
        from agent import web5653
        self.request()
        self.assertIn('Solde dû', dossier5679.balance_html(self.desk, '/p', MID))
        self.assertIn('Honoraires et facturation', web5653.matter_block_html(self.desk, '/p', MID, ACTOR))
        self.close()
        self.request()
        self.assertTrue(membres.allowed(self.state, ACTOR, 'avocat', 'GET', '/dossier'))     # la page Dossier reste ouverte
        self.assertEqual(dossier5679.balance_html(self.desk, '/p', MID), '')
        self.assertEqual(web5653.matter_block_html(self.desk, '/p', MID, ACTOR), '')
        with self.assertRaisesRegex(Stop, 'role_insuffisant'):
            fact.matter_balance(self.desk, MID)

    def test_page_et_releves_fermes_par_la_passerelle(self):
        routes = (('GET', '/contribution'), ('GET', '/api440/m5653/contribution/releve'), ('GET', '/api440/m5653/contribution/export'), ('POST', '/api440/m5653/contribution/cle'),
                  ('POST', '/api440/m5653/accuse/deposer'), ('GET', '/api440/m500/time/entries'), ('POST', '/api440/m5670/efacture/releve'), ('GET', '/relances'))
        for method, path in routes:
            self.assertTrue(membres.allowed(self.state, ACTOR, 'avocat', method, path), path)
        self.close()
        for method, path in routes:
            self.assertFalse(membres.allowed(self.state, ACTOR, 'avocat', method, path), path)

    def test_page_et_releves_fermes_par_le_service(self):
        self.close()
        html = contrib.page(self.desk, {'csrf': 'x'}, '/p', env(ACTOR, 'avocat'), {'mois': '2026-10'})
        self.assertIn('réservée', html)
        self.assertNotIn('83,33', html)
        for call in (lambda: contrib.api_get(self.desk, ACTOR, 'avocat', 'contribution/releve', {'mois': '2026-10'}),
                     lambda: contrib.export_csv(self.desk, ACTOR, 'avocat', '2026-10'),
                     lambda: contrib.save_key(self.desk, ACTOR, 'avocat', {'personne': MARTIN, 'debut': '2026-10', 'taux': '10'})):
            with self.assertRaisesRegex(Stop, 'role_insuffisant'):
                call()

    def test_temps_des_autres_fermes_meme_avec_le_droit_facturation(self):
        self.close()
        self.assertEqual(time500.entries(self.desk, viewer=(ACTOR, 'avocat')), [])

    def test_aucune_nouvelle_route_de_transmission(self):
        from agent import web5653
        self.assertEqual(membres.INVOICE_SEND_API, frozenset({'m5653/document/transmettre'}))
        self.assertEqual([r for r in web5653.WRITE if 'transmettre' in r], ['document/transmettre'])
        self.assertEqual(membres.fine_rights('POST', '/api440/m5670/efacture/releve'), ['voir_honoraires'])   # réglage d'un relevé en lecture


# ---------------------------------------------------------------------------------------------------- 6. facturation électronique
class Releve(base.Base):
    STAMP = datetime(2026, 10, 12, 9, 0, tzinfo=timezone.utc).timestamp()

    def platform(self):
        view = self.connect('superpdp', caps=['einvoice.receive', 'einvoice.status', 'einvoice.download'])
        from agent import connexions5652 as cx
        cx.test(self.desk, ACTOR, 'avocat', view['id'])
        self.fake.on('GET', '/v1.beta/invoices?', {'data': [{'id': 77, 'direction': 'in', 'en_invoice': {'number': 'FA-1', 'issue_date': '2026-10-15', 'seller': {'name': 'Fournisseur Fictif SAS'}},
                                                             'events': [{'status_code': 'fr:202'}]}]})
        self.fake.on('GET', '/v1.beta/ereportings?', {'data': []})
        return view

    def jobs(self):
        return self.desk.db.execute("SELECT COUNT(*) FROM jobs WHERE kind='efacture5688'").fetchone()[0]

    def test_rien_sans_plateforme(self):
        self.assertIsNone(auto.schedule(self.desk, self.STAMP))
        self.assertEqual(self.jobs(), 0)
        self.assertTrue(auto.job(self.desk)['ignored'])
        self.assertEqual(self.fake.calls, [])

    def test_une_fois_par_intervalle(self):
        self.platform()
        self.assertTrue(auto.schedule(self.desk, self.STAMP))
        self.assertIsNone(auto.schedule(self.desk, self.STAMP + 3600))         # une heure plus tard : trop tôt
        self.assertEqual(self.jobs(), 1)
        self.assertEqual(auto.settings(self.desk)['hours'], 6)

    def test_desactivable_et_intervalle_controle(self):
        self.platform()
        auto.save_settings(self.desk, ACTOR, 'avocat', {'enabled': '', 'hours': '12'})
        self.assertIsNone(auto.schedule(self.desk, self.STAMP))
        for bad in ('0', '25', 'x'):
            with self.assertRaisesRegex(Stop, 'intervalle_releve_invalide'):
                auto.save_settings(self.desk, ACTOR, 'avocat', {'enabled': '1', 'hours': bad})
        with self.assertRaisesRegex(Stop, 'role_insuffisant'):
            auto.save_settings(self.desk, ASSISTANT, 'assistant', {'enabled': '1'})

    def test_connexion_personnelle_jamais_utilisee_en_arriere_plan(self):
        self.platform()
        self.desk.db.execute("UPDATE connexions5652 SET owner=?", (ACTOR,))
        self.desk.db.commit()
        self.assertIsNone(auto.schedule(self.desk, self.STAMP))

    def test_lecture_seule_rien_n_est_transmis(self):
        self.platform()
        before = len(self.fake.calls)
        with patch('agent.facturation5653.submit', side_effect=AssertionError('transmission')), patch('agent.connexions5652.validate', side_effect=AssertionError('validation')):
            out = auto.job(self.desk, {})
        calls = self.fake.calls[before:]
        self.assertTrue(calls)
        self.assertEqual({c[0] for c in calls if '/oauth2/token' not in c[1]}, {'GET'})
        self.assertEqual(out['recues'], 1)
        self.assertEqual(ef.received(self.desk)[0]['numero'], 'FA-1')
        self.assertEqual(self.desk.settings(auto.LAST, {})['recues'], 1)

    def test_seules_des_capacites_de_lecture_sont_appelees(self):
        self.platform()
        seen = []
        from agent import adaptateurs5652 as ad
        real = ad.Adapter.call
        with patch.object(ad.Adapter, 'call', lambda s, cap, params: seen.append(cap) or real(s, cap, params)):
            auto.job(self.desk, {})
        self.assertTrue(set(seen) <= {'einvoice.receive_detail', 'ereporting.list', 'einvoice.status'}, seen)

    def test_transmettre_reste_une_decision_humaine(self):
        for module in (auto, ef):
            source = inspect.getsource(module)
            self.assertNotIn('send_approved', source)
            self.assertNotIn('.submit(', source)
            self.assertNotIn('cx.validate', source)
        self.assertEqual(membres.category('POST', '/api440/m5652/operation/valider'), 'operation')

    def test_une_transmission_preparee_reste_en_attente_de_validation(self):
        view = self.platform()
        self.desk.db.execute("INSERT INTO connexion_ops5652(id,connection,provider,capability,owner,state,idem_key,payload_json,payload_hash,created,updated) "
                             "VALUES('op-1',?,'superpdp','einvoice.send_approved',?,'a_valider','k1','{}','h','x','x')", (view['id'], ACTOR))
        self.desk.db.commit()
        auto.job(self.desk, {})
        row = self.desk.db.execute("SELECT state,validated_by FROM connexion_ops5652 WHERE id='op-1'").fetchone()
        self.assertEqual((row['state'], row['validated_by']), ('a_valider', ''))

    def test_branchement_dans_la_boucle_et_droits(self):
        import agent.operations as ops
        self.assertIn('efacture5688.schedule', inspect.getsource(ops))
        self.assertTrue(web5670.allowed('avocat', 'efacture/releve'))
        self.assertFalse(web5670.allowed('assistant', 'efacture/releve'))
        self.assertEqual(membres.category('POST', '/api440/m5670/efacture/releve'), 'facturation')

    def test_page_montre_l_etat_du_releve(self):
        self.platform()
        html = ef.html(self.desk, '/p', ACTOR, 'avocat')
        self.assertIn('Relevé automatique', html)
        self.assertIn('m5670/efacture/releve', html)


if __name__ == '__main__':
    unittest.main()
