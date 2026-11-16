"""5.6.103 (lot 0) : outils de test en parallèle (suite unitaire et parcours navigateur)."""
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def charger(nom, fichier):
    spec = importlib.util.spec_from_file_location(nom, ROOT / 'scripts' / fichier)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class SuiteParallele(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tp = charger('tests_paralleles56103', 'tests-paralleles.py')

    def test_O01_un_module_entier_sans_objet_est_compte_ignore_comme_discover(self):
        if __import__('os').name != 'nt':
            self.skipTest('test_installer ne se déclare sans objet que sous Windows')
        with tempfile.TemporaryDirectory() as d:
            sortie = Path(d) / 'r.json'
            self.tp._executer('test_installer', str(sortie))
            r = json.loads(sortie.read_text(encoding='utf-8'))
        self.assertEqual((r['tests'], r['ignores'], r['erreurs'], r['echecs']), (1, 1, [], []))

    def test_O02_un_module_introuvable_est_une_erreur_pas_un_succes(self):
        with tempfile.TemporaryDirectory() as d:
            sortie = Path(d) / 'r.json'
            self.tp._executer('test_module_qui_n_existe_pas_56103', str(sortie))
            r = json.loads(sortie.read_text(encoding='utf-8'))
        self.assertTrue(r['erreurs'])

    def test_O03_les_taches_longues_partent_en_premier(self):
        taches = self.tp._taches(['test_a', 'test_b', 'test_c'], {'test_a': 5, 'test_b': 50, 'test_c': 20}, 1e9, Path(tempfile.gettempdir()), 60)
        self.assertEqual(taches, ['test_b', 'test_c', 'test_a'])

    def test_O04_les_durees_ne_sont_jamais_publiees(self):
        ignores = (ROOT / '.gitignore').read_text(encoding='utf-8')
        self.assertIn('.durees-tests.json', ignores)
        self.assertIn('.durees-parcours.json', ignores)
        liste = json.loads((ROOT / 'RELEASE-FILES.json').read_text(encoding='utf-8'))
        self.assertIn('scripts/tests-paralleles.py', liste)
        self.assertFalse([n for n in liste if 'durees-' in n])


class ParcoursParallele(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        src = (ROOT / 'scripts' / 'parcours-navigateur.py').read_text(encoding='utf-8')
        cls.src = src

    def _fonctions(self):
        """Fonctions pures du banc, sans importer Playwright ni les parcours."""
        ns = {'json': json, 'Path': Path, 'ROOT': ROOT / 'nexistepas'}
        debut = self.src.index('def _tranche(ident):')
        fin = self.src.index('def lancer_parallele(')
        exec(compile(self.src[debut:fin], 'banc', 'exec'), ns)
        return ns

    def test_O05_tranches_du_tour_du_menu(self):
        ns = self._fonctions()
        self.assertEqual(ns['_tranche']('P02g@2/4'), ('P02g', (2, 4)))
        self.assertEqual(ns['_tranche']('P96c'), ('P96c', (1, 1)))
        with self.assertRaises(ValueError):
            ns['_tranche']('P02g@5/4')

    def test_O06_repartition_equilibree_et_complete(self):
        ns = self._fonctions()
        durees = {'P02g': 2700, 'P101f': 370, 'P101v': 230, 'P02a': 60, 'P02b': 60}
        groupes = ns['repartir'](['P02a', 'P02b', 'P02g', 'P101f', 'P101v'], 4, durees)
        taches = sorted(t for g in groupes for t in g)
        self.assertEqual(taches, sorted(['P02a', 'P02b', 'P101f', 'P101v'] + ['P02g@%d/4' % i for i in range(1, 5)]))
        charges = [sum(durees.get(t, durees.get(t.split('@')[0], 0) / (4 if '@' in t else 1)) for t in g) for g in groupes]
        self.assertLess(max(charges), 2700 / 2)          # le tour du menu n'impose plus sa durée à tout le banc
        self.assertEqual(ns['repartir'](['P02g', 'P02a'], 1, durees), [['P02g', 'P02a']])   # un seul navigateur : pas de tranche

    def test_O07_un_groupe_va_au_bout_apres_un_echec(self):
        self.assertIn("'--continuer'", self.src)
        self.assertIn('if not continuer:\n                        break', self.src)


if __name__ == '__main__':
    unittest.main()
