"""5.6.56 : corrections de l'audit 5.6.55 au-delà des contre-tests de l'audit (tests/test_contre5655*.py). Données fictives."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import test_v5652 as base
import test_v5653 as t53
from agent import facturation5653 as fact, facturx5653 as fx, crm5636 as crm, intelligence, sens5643, veille5632 as veille
from agent import membres5650 as mb
from agent.common import Stop, load_matters

ACTOR, MID = t53.ACTOR, t53.MID
HERE = Path(__file__).resolve().parent


class Transactions(base.Base):
    def test_executescript_ne_valide_pas_une_transaction_ouverte(self):
        self.desk.db.execute("CREATE TABLE IF NOT EXISTS essai5656(x TEXT)")
        self.desk.db.commit()
        self.desk.db.execute('BEGIN IMMEDIATE')
        self.desk.db.execute("INSERT INTO essai5656 VALUES('a')")
        self.desk.db.executescript('CREATE TABLE IF NOT EXISTS essai5656b(y TEXT);\nCREATE INDEX IF NOT EXISTS essai5656b_y ON essai5656b(y);')
        self.assertTrue(self.desk.db.in_transaction)
        self.desk.db.rollback()
        self.assertEqual(self.desk.db.execute('SELECT COUNT(*) FROM essai5656').fetchone()[0], 0)


    def test_begin_valide_les_ecritures_implicites_mais_pas_une_transaction_explicite(self):
        import sqlite3
        self.desk.db.execute("CREATE TABLE IF NOT EXISTS essai5656c(x TEXT)")
        self.desk.db.commit()
        self.desk.db.execute("INSERT INTO essai5656c VALUES('implicite')")        # transaction implicite en attente
        self.desk.db.execute('BEGIN IMMEDIATE')                                    # compatibilité : écritures validées d'abord
        self.desk.db.rollback()
        self.assertEqual(self.desk.db.execute('SELECT COUNT(*) FROM essai5656c').fetchone()[0], 1)
        self.desk.db.execute('BEGIN IMMEDIATE')
        with self.assertRaises(sqlite3.OperationalError):
            self.desk.db.execute('BEGIN IMMEDIATE')                                # jamais de validation d'une transaction explicite
        self.desk.db.rollback()


class Billing56(base.Base):
    invoice = t53.Billing.invoice

    def setUp(self):
        base.Base.setUp(self)
        mb.set_viewer(None)
        self.addCleanup(mb.set_viewer, None)
        fact.save_entity(self.desk, 'admin@example.test', 'administrateur', t53.ENTITY)
        fact.save_client(self.desk, ACTOR, 'avocat', MID, t53.CLIENT)
        p = patch.object(fx, 'office_pdf', return_value=None)
        p.start()
        self.addCleanup(p.stop)

    def emit(self, d, key):
        d = fact.approve(self.desk, ACTOR, 'avocat', d['id'], d['version'])
        return fact.issue(self.desk, ACTOR, 'avocat', d['id'], key)

    def test_iban_et_dates(self):
        self.assertTrue(fact.iban_ok('FR76 0000 0000 0000 0000 0000 000'))
        self.assertFalse(fact.iban_ok('FR7611111111111111111111111'))
        self.assertFalse(fact.iban_ok('FR760000000000000000000000'))          # longueur française fausse
        self.assertEqual(fact.day_value('2028-02-29'), '2028-02-29')
        for bad in ('2027-02-29', '2026-04-31', '2026-13-01', '2026-10-17T10:00'):
            with self.assertRaisesRegex(Stop, 'date_invalide'):
                fact.day_value(bad)
        self.assertEqual(fact.day_value('', required=False), '')

    def test_reglement_double_clic_sans_reference(self):
        inv = self.emit(self.invoice(), 'emission-5656-0001')
        for _ in range(2):
            fact.record_payment(self.desk, ACTOR, 'avocat', inv['id'], '100', '2026-10-10', 'virement', '', 'requete-5656-0001')
        fact.record_payment(self.desk, ACTOR, 'avocat', inv['id'], '100', '2026-10-10', 'virement')     # second règlement réel
        self.assertEqual(len(fact.get(self.desk, inv['id'])['payments']), 2)

    def test_original_emis_conserve_puis_conflit_et_copie(self):
        inv = self.emit(self.invoice(), 'emission-5656-0002')
        self.assertEqual(inv['files_state'], 'publies')
        stored = self.desk.db.execute('SELECT COUNT(*) FROM b56_issued_files WHERE document=?', (inv['id'],)).fetchone()[0]
        self.assertGreaterEqual(stored, 2)                                    # Word, CII, UBL conservés dans AxiorHub
        fact.save_entity(self.desk, 'admin@example.test', 'administrateur', {'nom': 'Cabinet Renommé Fictif'})
        target = self.root / inv['files']['docx']['chemin'].lstrip('/')
        target.write_bytes(b'modifie apres emission')
        d = fact.publish(self.desk, inv['id'])
        self.assertEqual(d['files_state'], 'conflit')
        self.assertEqual(target.read_bytes(), b'modifie apres emission')      # jamais écrasé
        d = fact.restore_copy(self.desk, ACTOR, 'avocat', inv['id'])
        self.assertEqual(d['files_state'], 'publies')
        copy = self.root / d['files']['docx']['chemin'].lstrip('/')
        self.assertIn('(original émis)', copy.name)
        import io, zipfile
        with zipfile.ZipFile(io.BytesIO(copy.read_bytes())) as z:
            xml = z.read('word/document.xml').decode()
        self.assertIn('Cabinet Fictif Avocat', xml)
        self.assertNotIn('Cabinet Renommé Fictif', xml)

    def test_fichier_absent_redepose_a_l_identique(self):
        inv = self.emit(self.invoice(), 'emission-5656-0003')
        target = self.root / inv['files']['cii']['chemin'].lstrip('/')
        original = target.read_bytes()
        target.unlink()
        d = fact.publish(self.desk, inv['id'])
        self.assertEqual(d['files_state'], 'publies')
        self.assertEqual(target.read_bytes(), original)

    def test_changement_de_client_recalcule_les_brouillons(self):
        d = self.invoice()
        fact.save_client(self.desk, ACTOR, 'avocat', MID, {'kind': 'consommateur', 'siren': '', 'siret': ''})
        out = fact.recalculate_drafts(self.desk, ACTOR, 'avocat', MID)
        self.assertEqual([x['id'] for x in out], [d['id']])
        self.assertEqual(fact.get(self.desk, d['id'])['snapshot']['fiscal']['perimetre'], 'b2c')
        self.assertFalse([b for b in fact.check(self.desk, ACTOR, 'avocat', d['id'])['bloquants'] if 'modifié depuis le calcul' in b])

    def test_regime_modifie_message_avec_montants(self):
        d = self.invoice()
        fact.save_entity(self.desk, 'admin@example.test', 'administrateur', {'regime_cabinet': 'franchise', 'justification_franchise': 'Hypothèse fictive'})
        msg = [b for b in fact.check(self.desk, ACTOR, 'avocat', d['id'])['bloquants'] if 'Enregistrer et recalculer' in b]
        self.assertTrue(msg and 'après recalcul 0,00' in msg[0].replace(' ', ' ').replace('\xa0', ' '), msg)

    def test_avoir_trop_eleve_bloque_a_l_emission(self):
        inv = self.emit(self.invoice('1200'), 'emission-5656-0004')
        lines = [{'libelle': 'Rectification fictive', 'quantite': '1', 'prix_unitaire_ht': '800'}]
        a = fact.credit_note(self.desk, ACTOR, 'avocat', inv['id'], 'Première', lines)
        b = fact.credit_note(self.desk, ACTOR, 'avocat', inv['id'], 'Seconde, préparée avant la première émission', lines)
        self.emit(a, 'avoir-5656-0001')
        report = fact.check(self.desk, ACTOR, 'avocat', b['id'])
        self.assertTrue(any('encore rectifiable' in x for x in report['bloquants']))
        with self.assertRaises(Stop):
            fact.approve(self.desk, ACTOR, 'avocat', b['id'], b['version'])


class Identities56(base.Base):
    def test_nom_et_adresse_analyses_et_classification(self):
        cfg = {'own_addresses': ['Cabinet Fictif <cabinet@example.test>'], 'from_address': '', 'username': 'technique'}
        self.assertIn('cabinet@example.test', sens5643.identities(cfg))
        self.assertEqual(sens5643.classify('client@example.test', 'cabinet@example.test', '', cfg), 'received')
        self.assertEqual(sens5643.classify('cabinet@example.test', 'client@example.test', '', cfg), 'sent')
        self.assertEqual(sens5643.classify('cabinet@example.test', 'cabinet@example.test', '', cfg), 'internal')
        self.assertEqual(sens5643.classify('confrere@example.test', 'cabinet@example.test', '', cfg), 'received')   # même domaine : extérieur
        html = sens5643.identities_html(cfg, {'test_de': 'cabinet@example.test', 'test_a': 'client@example.test'})
        self.assertIn('Vos adresses reconnues', html)
        self.assertIn('envoyé par le cabinet', html)

    def test_propositions_existantes_vers_le_cabinet_ecartees(self):
        self.desk.db.execute("INSERT INTO proposals VALUES ('k5656',?,?,'[]','pending','2026-10-01')", (MID, self.desk.c['mail']['from_address']))
        self.desk.db.commit()
        self.assertEqual(self.desk.quarantine_own5656(), 1)
        self.assertEqual(self.desk.db.execute("SELECT status FROM proposals WHERE key='k5656'").fetchone()[0], 'ecartee_cabinet')


class Drafting56(unittest.TestCase):
    def test_intentions(self):
        self.assertEqual(intelligence.manual_intent('prudent', 'Rédiger une synthèse factuelle, sans question de clarification.'), 'reponse_fondee_dossier')
        self.assertEqual(intelligence.manual_intent('prudent', 'Demander des précisions au client sur la date'), 'demande_precision')
        self.assertEqual(intelligence.manual_intent('clarification', ''), 'demande_precision')
        self.assertEqual(intelligence.manual_intent('prudent', 'Relancer le confrère'), 'relance')
        self.assertEqual(intelligence.manual_intent('slots', ''), 'appointment')


class Watch56(base.Base):
    def test_veille_desactivee_explicitement_jamais_reactivee(self):
        self.desk.setting('proactive568:profile:cabinet', {'legal_fields': ['Baux commerciaux'], 'news_enabled': False})
        self.assertFalse(veille.profile(self.desk)['enabled'])
        with patch.object(self.desk, 'enqueue', side_effect=AssertionError('aucune collecte')):
            self.assertEqual(veille.schedule(self.desk), [])

    def test_actualiser_repare_un_passage_orphelin(self):
        veille.save_profile(self.desk, 'cabinet', {'enabled': True, 'themes': 'Baux commerciaux', 'hour': '08:00'})
        veille.schema(self.desk)
        self.desk.db.execute("INSERT INTO veille_runs5632(owner,slot,state,created) VALUES('cabinet','manual-orphelin','pending','2026-10-17')")
        self.desk.db.commit()
        out = veille.refresh(self.desk)
        self.assertFalse(out['joined'])
        self.assertTrue(out.get('repare'))
        self.assertTrue(self.desk.db.execute("SELECT 1 FROM jobs WHERE kind='veille5632_collect'").fetchone())
        self.assertTrue(veille.refresh(self.desk)['joined'])                 # le second clic rejoint le travail réel


class Contacts56(base.Base):
    def test_filtre_sans_resultat_explique(self):
        crm.save(self.desk, {'name': 'Contact Fictif', 'email': 'fictif@example.test', 'category': 'client'})
        html = crm.page_html(self.desk, '/agent-courriel', {'categorie': 'commissaire'})
        self.assertIn('0 résultat pour ce filtre (1 contact(s) au total)', html)
        self.assertIn('Réinitialiser les filtres', html)

    def test_import_reclasse_un_ancien_autre_non_corrige(self):
        m = dict(next(m for m in load_matters(self.desk.c) if m['id'] == MID))
        m['correspondents'] = [{'email': 'confrere@example.test', 'role': 'confrere_adverse'}]
        c = crm.save(self.desk, {'name': 'confrere', 'email': 'confrere@example.test', 'category': 'autre'}, source='import')
        with patch.object(crm, 'load_matters', return_value=[m]), patch('agent.dossiers5632.adverse_parties', return_value=[]):
            crm.import_existing(self.desk)
        self.assertEqual(crm.get(self.desk, c['id'])['category'], 'confrere')

    def test_lien_refuse_rien_n_est_ecrit(self):
        with self.assertRaisesRegex(Stop, 'dossier_absent'):
            crm.save(self.desk, {'name': 'Fictif', 'email': 'f@example.test', 'matter': 'DOSSIER-INCONNU', 'role': 'client'})
        self.assertEqual(crm.search(self.desk), [])


class Navigation(unittest.TestCase):
    """Test de l'audit 5.6.55 (v500.js réel dans une VM Node) : un objet créé est ouvert même sans data-reload."""

    def test_goto_suivi(self):
        node = shutil.which('node')
        if not node:
            self.skipTest('Node.js absent')
        out = tempfile.TemporaryDirectory()
        self.addCleanup(out.cleanup)
        env = {**os.environ, 'AXH_NAV_OUT': out.name}
        r = subprocess.run([node, str(HERE / 'navigation5655.cjs'), str(HERE.parent)], capture_output=True, text=True, timeout=60, env=env)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)


if __name__ == '__main__':
    unittest.main()
