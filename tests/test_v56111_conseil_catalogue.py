"""5.6.111 (lot K, F06) : essais du dossier technique (``test_correctifs_conseil``) intégrés au produit — l'outil d'export des trames
(``scripts/preparer-catalogue-conseil.py``) et le plan de sous-agents d'exemple, validés contre les services natifs du logiciel.

Chemin corrigé : le script est celui du produit (``scripts/``), plus l'exemple du dossier ; le plan d'exemple du dossier
(``exemples/plan-sous-agents-conseil.json``) est repris tel quel ci-dessous, le dossier technique n'étant pas livré avec le produit.
Aucune base, aucun réseau : l'export lit les seules trames intégrées."""
import importlib.util
import json
from pathlib import Path
import unittest

from agent import artefacts56109, droits56108
from agent.common import Stop

ROOT = Path(__file__).resolve().parents[1]
sp = importlib.util.spec_from_file_location('export_conseil56111', ROOT / 'scripts' / 'preparer-catalogue-conseil.py')
export = importlib.util.module_from_spec(sp)
sp.loader.exec_module(export)

PLAN_EXEMPLE = {'budget_tokens': 16000, 'plan': [
    {'id': 'faits', 'role': 'faits', 'depends_on': [], 'max_tokens': 2500},
    {'id': 'recherche', 'role': 'recherche', 'depends_on': ['faits'], 'max_tokens': 3500},
    {'id': 'redaction', 'role': 'redaction', 'depends_on': ['faits', 'recherche'], 'max_tokens': 5000},
    {'id': 'controle', 'role': 'controle', 'depends_on': ['redaction'], 'max_tokens': 2500},
    {'id': 'assemblage', 'role': 'assemblage', 'depends_on': ['controle'], 'max_tokens': 2500}]}


class ExemplesConseil(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.items = export.construire()['modeles']

    def test_les_six_systemes_sont_distincts(self):
        self.assertEqual({m['systeme_juridique'] for m in self.items}, {'FR', 'ES', 'DE', 'UK-EAW', 'CA-QC', 'CA-ON'})
        self.assertEqual(len({m['id'] for m in self.items}), len(self.items))

    def test_les_questionnaires_sont_lies_a_la_bonne_trame(self):
        for m in self.items:
            with self.subTest(id=m['id']):
                q = m['questionnaire']
                self.assertEqual(q['modele_id'], m['id'])
                self.assertEqual(q['systeme_juridique'], m['systeme_juridique'])
                self.assertEqual(q['modele_version'], m['version_source'])
                self.assertNotIn('reponses', q)

    def test_aucune_validation_juridique_fabriquee(self):
        for m in self.items:
            self.assertEqual(m['etat_initial'], 'draft')
            self.assertEqual(m['valide_par'], '')
            self.assertEqual(m['valide_le'], '')

    def test_uk_et_canada_generiques_ne_sont_pas_un_droit_precis(self):
        for droit in ('UK', 'GB', 'CA'):
            with self.assertRaises(Stop):
                droits56108.systeme_requis(droit)

    def test_plan_accepte_par_le_moteur_existant(self):
        data = json.loads(json.dumps(PLAN_EXEMPLE))
        r = artefacts56109.verifier_plan(data['plan'], data['budget_tokens'])
        self.assertEqual(r['steps'], 5)
        self.assertEqual(r['max_tokens'], 16000)

    def test_plan_sans_controle_apres_redaction_refuse(self):
        plan = [{'id': 'r', 'role': 'redaction', 'depends_on': [], 'max_tokens': 100},
                {'id': 'a', 'role': 'assemblage', 'depends_on': ['r'], 'max_tokens': 100}]
        with self.assertRaises(Stop):
            artefacts56109.verifier_plan(plan, 200)

    def test_script_ecrit_seulement_le_fichier_demande(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            sortie = Path(d) / 'catalogue.json'
            self.assertEqual(export.main(['--sortie', str(sortie)]), 0)
            data = json.loads(sortie.read_text(encoding='utf-8'))
            self.assertEqual(data['format'], 'axiorhub-revision-conseil-1')
            self.assertEqual(len(data['modeles']), len(self.items))
            self.assertEqual(sorted(p.name for p in Path(d).iterdir()), ['catalogue.json'])


if __name__ == '__main__':
    unittest.main()
