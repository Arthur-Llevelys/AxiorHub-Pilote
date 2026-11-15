"""5.6.101, lot 0 : tests indépendants de la date du jour (T01, T03, V101-01).

T01 — ``tests/dates_relatives.py`` calcule les dates dans le fuseau du cabinet (jour, décalage, année à venir, date écrite en toutes
lettres) ; ``date.today()`` et ``datetime.now()`` sans fuseau sont interdits dans les tests : un relevé des usages anciens (avant la
5.6.101) sert de plafond, tout usage nouveau échoue.

T03 — ``scripts/tests-horloge.py`` simule l'horloge en Python (aucun téléchargement) : instant simulé (décalage en jours, heure de
Paris, vendredi soir), remplacement effectif des horloges dans un processus enfant (``date.today``, ``datetime.now``, ``time.time``,
``time.strftime``, ``isinstance`` conservé, adaptateur sqlite3), code de sortie. Travail GitHub « horloge » non bloquant, cinq horloges.
"""
from datetime import date, datetime, timedelta, timezone
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import textwrap
import tokenize
import unittest
from zoneinfo import ZoneInfo

import dates_relatives as dr

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / 'scripts' / 'tests-horloge.py'
PARIS = ZoneInfo('Europe/Paris')


def charger_script():
    spec = importlib.util.spec_from_file_location('tests_horloge56101', SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)   # n'installe rien : seul main() remplace les horloges
    return mod


def appels_date_machine(texte):
    """Appels ``date.today()``, ``datetime.today()``, ``datetime.now()`` et ``datetime.utcnow()`` dans le code (chaînes et commentaires
    exclus : une docstring qui les interdit n'est pas un usage)."""
    toks = [t.string for t in tokenize.generate_tokens(io.StringIO(texte).readline) if t.type in (tokenize.NAME, tokenize.OP)]
    appels = (('date', 'today'), ('datetime', 'today'), ('datetime', 'now'), ('datetime', 'utcnow'))
    return sum(1 for i in range(len(toks) - 4)
               if (toks[i], toks[i + 2]) in appels and (toks[i + 1], toks[i + 3], toks[i + 4]) == ('.', '(', ')'))


class T01DatesRelatives(unittest.TestCase):
    def test_T01_jour_du_cabinet_et_decalages(self):
        self.assertEqual(dr.aujourdhui_cabinet(), datetime.now(PARIS).date())
        self.assertEqual(dr.jour(10) - dr.jour(0), timedelta(days=10))
        self.assertEqual(dr.jour(-3), dr.aujourdhui_cabinet() - timedelta(days=3))
        self.assertGreater(date(dr.annee(2), 1, 1), dr.aujourdhui_cabinet())
        self.assertEqual(dr.maintenant_cabinet().utcoffset(), datetime.now(PARIS).utcoffset())

    def test_T01_date_en_toutes_lettres(self):
        self.assertEqual(dr.texte_date(date(2028, 11, 12)), '12 novembre 2028')
        self.assertEqual(dr.texte_date(datetime(2029, 8, 1, 9, 30)), '1 août 2029')
        with self.assertRaises(TypeError):
            dr.texte_date('2028-11-12')

    def test_T01_pas_de_nouvel_usage_de_la_date_de_la_machine(self):
        # Plafond : usages présents avant la 5.6.101 (relatifs au jour, jamais comparés à une date figée). Un fichier nouveau : zéro.
        plafond = {
            'test_v5697_gestion.py': 40, 'test_v5697_gestion_recettes.py': 12, 'test_v5688_agenda.py': 5, 'test_v5696_accueil.py': 3,
            'test_v5676.py': 3, 'test_v5636.py': 3, 'test_v5697_agenda.py': 2, 'test_v5659.py': 2, 'test_v5697_sources.py': 1,
            'test_v5697_gmail.py': 1, 'test_v5696_echeances.py': 1, 'test_v5696_discussion.py': 1, 'test_v5696_courriels.py': 1,
            'test_v5667.py': 1, 'test_v5637.py': 1, 'test_v5635.py': 1, 'test_v5634.py': 1, 'test_v5628.py': 1,
            'test_v56100_integration.py': 1, 'test_v450_web.py': 1, 'test_d5651.py': 1, 'test_correctifs_56100.py': 1,
        }
        trop = {}
        for f in sorted((ROOT / 'tests').glob('test_*.py')):
            n = appels_date_machine(f.read_text(encoding='utf-8'))
            if n > plafond.get(f.name, 0):
                trop[f.name] = n
        self.assertEqual(trop, {}, 'utiliser tests/dates_relatives.py (jour du cabinet), jamais la date de la machine')

    def test_T01_le_releve_ignore_chaines_et_commentaires(self):
        self.assertEqual(appels_date_machine('x = date.today()\ny = datetime.now( )\nz = mobile.date.today()\n'), 3)
        self.assertEqual(appels_date_machine("'''jamais date.today()'''\n# datetime.now()\nz = datetime.now(PARIS)\n"), 0)


class T03InstantSimule(unittest.TestCase):
    def setUp(self):
        self.h = charger_script()
        # Mercredi 10 mars 2027, 14:15 à Paris (heure d'hiver).
        self.reel = datetime(2027, 3, 10, 14, 15, tzinfo=PARIS).timestamp()

    def test_T03_decalage_en_jours_garde_l_heure(self):
        c = self.h.instant_simule(365, reel=self.reel)
        self.assertEqual((c.date(), c.hour, c.minute), (date(2028, 3, 9), 14, 15))
        # +730 jours traverse deux passages à l'heure d'été : l'heure murale de Paris reste 14:15.
        c = self.h.instant_simule(730, reel=self.reel)
        self.assertEqual((c.date(), c.hour, c.minute), (date(2029, 3, 9), 14, 15))

    def test_T03_heure_de_paris_et_bascule_utc(self):
        c = self.h.instant_simule(0, (23, 30), reel=self.reel)
        self.assertEqual((c.date(), c.hour, c.minute), (date(2027, 3, 10), 23, 30))
        self.assertEqual(c.astimezone(timezone.utc).strftime('%H:%M'), '22:30')
        c = self.h.instant_simule(0, (0, 30), reel=self.reel)
        self.assertEqual(c.astimezone(timezone.utc).date(), date(2027, 3, 9))   # Paris a déjà changé de jour, pas l'UTC

    def test_T03_vendredi_soir(self):
        c = self.h.instant_simule(0, None, True, reel=self.reel)
        self.assertEqual((c.date(), c.weekday(), c.hour, c.minute), (date(2027, 3, 12), 4, 21, 0))
        vendredi = datetime(2027, 3, 12, 8, 0, tzinfo=PARIS).timestamp()
        self.assertEqual(self.h.instant_simule(0, None, True, reel=vendredi).date(), date(2027, 3, 12))   # déjà vendredi : ce jour
        c = self.h.instant_simule(0, (18, 45), True, reel=self.reel)
        self.assertEqual((c.weekday(), c.hour, c.minute), (4, 18, 45))

    def test_T03_liste_des_modules_annexe_b(self):
        for m in ('test_document_agents568', 'test_v440_notices', 'test_v5695_lecture', 'test_v5689_qualif'):
            self.assertIn(m, self.h.MODULES_DATES)
        for m in self.h.MODULES_DATES:
            self.assertTrue((ROOT / 'tests' / (m + '.py')).is_file(), m)


class T03ProcessusEnfant(unittest.TestCase):
    """L'horloge est remplacée dans un processus enfant (jamais dans celui de la recette)."""

    MODULE = textwrap.dedent('''
        import datetime as D, json, os, sqlite3, time, unittest
        from datetime import date, datetime
        import dates_relatives as dr

        class Releve(unittest.TestCase):
            def test_releve(self):
                c = sqlite3.connect(':memory:')
                c.execute('CREATE TABLE t (a)')
                c.execute('INSERT INTO t VALUES (?), (?)', (datetime(2030, 1, 2, 3, 4), date.today()))
                out = {
                    'today': date.today().isoformat(), 'cabinet': dr.aujourdhui_cabinet().isoformat(),
                    'heure_paris': dr.maintenant_cabinet().strftime('%H:%M'),
                    'ecart_time': abs(time.time() - datetime.now().timestamp()),
                    'ecart_ns': abs(time.time_ns() / 1e9 - time.time()),
                    'strftime': time.strftime('%Y-%m-%d'), 'localtime': time.strftime('%Y-%m-%d', time.localtime()),
                    'isinstance': [isinstance(D.date.min, date), isinstance(D.datetime.max, datetime),
                                   isinstance(datetime.now(), date), not isinstance(date.today(), datetime)],
                    'sqlite': [r[0] for r in c.execute('SELECT a FROM t')],
                }
                # Dates des fichiers sur la même horloge : un fichier créé à l'instant a « maintenant » pour date.
                f = os.environ['AXH_RELEVE_HORLOGE'] + '.tmp'
                open(f, 'w').close()
                with os.scandir(os.path.dirname(f)) as it:
                    entree = [e for e in it if e.path == f][0]
                    neuf = abs(entree.stat().st_mtime - time.time()) < 60   # lu avant utime (Linux : stat paresseux)
                os.utime(f, (time.time() - 7200, time.time() - 7200))
                out['fichier'] = [neuf, abs(os.path.getmtime(f) - (time.time() - 7200)) < 60,
                                  abs(os.stat(f).st_mtime_ns / 1e9 - (time.time() - 7200)) < 60]
                with open(os.environ['AXH_RELEVE_HORLOGE'], 'w', encoding='utf-8') as f:
                    json.dump(out, f)

            def test_echec_volontaire(self):
                if os.environ.get('AXH_ECHEC_HORLOGE') == '1':
                    self.fail('échec volontaire')
        ''')

    def lancer(self, *options, echec=False):
        with tempfile.TemporaryDirectory() as t:
            (Path(t) / 'releve_horloge56101.py').write_text(self.MODULE, encoding='utf-8')
            sortie = Path(t) / 'releve.json'
            env = dict(os.environ, AXH_RELEVE_HORLOGE=str(sortie), AXH_ECHEC_HORLOGE='1' if echec else '0',
                       PYTHONPATH=os.pathsep.join([t] + [p for p in os.environ.get('PYTHONPATH', '').split(os.pathsep) if p]))
            r = subprocess.run([sys.executable, str(SCRIPT), *options, 'releve_horloge56101'], cwd=t, env=env,
                               capture_output=True, text=True, encoding='utf-8', timeout=120)
            return r, (json.loads(sortie.read_text(encoding='utf-8')) if sortie.exists() else None)

    def test_T03_horloge_decalee_dans_l_enfant(self):
        attendu = charger_script().instant_simule(730, (23, 30))
        r, out = self.lancer('--decalage-jours', '730', '--heure-paris', '23:30')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn('Horloge simulée', r.stderr)
        self.assertEqual(out['cabinet'], attendu.date().isoformat())
        self.assertEqual(out['heure_paris'][:2], '23')
        self.assertLess(out['ecart_time'], 5)
        self.assertLess(out['ecart_ns'], 5)
        self.assertEqual(out['strftime'], out['localtime'])
        self.assertEqual(out['isinstance'], [True, True, True, True])
        self.assertEqual(out['sqlite'], ['2030-01-02 03:04:00', out['today']])
        self.assertEqual(out['fichier'], [True, True, True])
        # Le processus de la recette n'est pas touché.
        self.assertEqual(datetime.now(PARIS).date(), dr.aujourdhui_cabinet())
        self.assertNotEqual(datetime.now(PARIS).year, attendu.year)

    def test_T03_echec_rendu_par_le_code_de_sortie(self):
        r, _ = self.lancer('--vendredi-soir', echec=True)
        self.assertEqual(r.returncode, 1)
        self.assertIn('vendredi', r.stderr)
        self.assertIn('1 échecs', r.stderr)


class T03TravailGitHub(unittest.TestCase):
    def setUp(self):
        self.text = (ROOT / '.github' / 'workflows' / 'tests.yml').read_text(encoding='utf-8')
        start = self.text.index('\n  horloge:')
        nxt = re.search(r'\n  [a-z][a-z0-9-]*:\n', self.text[start + 5:])
        self.job = self.text[start: start + 5 + nxt.start()] if nxt else self.text[start:]

    def test_T03_V101_01_cinq_horloges_non_bloquantes(self):
        self.assertIn('continue-on-error: true', self.job)
        self.assertIn('fail-fast: false', self.job)
        self.assertIn('scripts/tests-horloge.py', self.job)
        for option in ("options: ''", '--decalage-jours 365', '--decalage-jours 730', '--heure-paris 23:30', '--vendredi-soir'):
            self.assertIn(option, self.job)
        self.assertIn('runs-on: ubuntu-', self.job)
        self.assertNotIn('faketime', self.text)   # aucun paquet ni téléchargement : horloge simulée en Python

    def test_T03_autres_travaux_presents(self):
        for job in ('tests', 'nextcloud-app-unit', 'migrations-root', 'windows', 'navigateur'):
            self.assertIn('\n  %s:\n' % job, self.text)
        self.assertEqual(self.text.count('continue-on-error'), 1)   # seul le travail « horloge » est non bloquant


if __name__ == '__main__':
    unittest.main()
