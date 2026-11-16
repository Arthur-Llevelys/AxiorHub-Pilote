#!/usr/bin/env python3
"""Suite de tests répartie sur plusieurs processus (5.6.103).

Même découverte que « python -m unittest discover -s tests -t tests », mais chaque module
(ou chaque classe d'un module long) tourne dans son propre processus Python, plusieurs à la fois.

  python scripts/tests-paralleles.py                 # toute la suite, un processus par cœur moins un
  python scripts/tests-paralleles.py -j 8            # huit processus
  python scripts/tests-paralleles.py test_v5662 test_v410
  python scripts/tests-paralleles.py --sortie resultats.json

Chaque processus a son propre dossier temporaire. Un module qui dépasse --delai est arrêté
et compté en erreur, au lieu de bloquer toute la suite. Les durées mesurées sont gardées dans
.durees-tests.json (non versionné) : les tâches longues partent en premier, et les modules
de plus de --decoupe secondes sont découpés par classe de tests au passage suivant.

Le résumé final reprend la forme de unittest (« Ran N tests in S s », « OK » ou « FAILED (...) »),
et le code de sortie vaut 1 en cas d'échec ou d'erreur.
"""
import argparse
import concurrent.futures
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import unittest
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
TESTS = RACINE / 'tests'
DUREES = RACINE / '.durees-tests.json'


# --- côté processus de test ---------------------------------------------------------------

def _chemins():
    """Mêmes chemins d'import que « python -m unittest discover -s tests -t tests » lancé depuis la racine."""
    ici = str(Path(__file__).resolve().parent)
    sys.path[:] = [p for p in sys.path if os.path.abspath(p or '.') != ici]
    sys.path.insert(0, str(RACINE))
    sys.path.insert(0, str(TESTS))

def _executer(nom, sortie):
    """Exécute un module ou une classe (« test_vX » ou « test_vX.Classe ») et écrit un JSON."""
    _chemins()
    tampon = io.StringIO()
    resultat = {'nom': nom, 'tests': 0, 'echecs': [], 'erreurs': [], 'ignores': 0,
                'echecs_attendus': 0, 'succes_inattendus': 0}
    debut = time.monotonic()
    ancien_out, ancien_err = sys.stdout, sys.stderr
    try:
        sys.stdout = sys.stderr = tampon
        suite = unittest.defaultTestLoader.loadTestsFromName(nom)
        r = unittest.TextTestRunner(stream=tampon, verbosity=1).run(suite)
        resultat['tests'] = r.testsRun
        resultat['echecs'] = [(t.id(), tb) for t, tb in r.failures]
        resultat['erreurs'] = [(t.id(), tb) for t, tb in r.errors]
        resultat['ignores'] = len(r.skipped)
        resultat['echecs_attendus'] = len(r.expectedFailures)
        resultat['succes_inattendus'] = len(r.unexpectedSuccesses)
    except unittest.SkipTest:                   # module entier sans objet ici (ex. installateur Linux sous Windows) : compté comme discover
        resultat['tests'] = 1
        resultat['ignores'] = 1
    except BaseException:                       # import impossible, sortie brutale…
        resultat['erreurs'].append((nom, traceback.format_exc()))
    finally:
        sys.stdout, sys.stderr = ancien_out, ancien_err
    resultat['duree'] = round(time.monotonic() - debut, 2)
    resultat['journal'] = tampon.getvalue()[-20000:]
    Path(sortie).write_text(json.dumps(resultat, ensure_ascii=False), encoding='utf-8')


def _lister_classes(module, sortie):
    """Écrit la liste des classes de tests d'un module (pour découper les modules longs)."""
    _chemins()
    classes = []
    try:
        suite = unittest.defaultTestLoader.loadTestsFromName(module)
        def parcourir(s):
            for t in s:
                if isinstance(t, unittest.TestSuite):
                    parcourir(t)
                else:
                    c = '%s.%s' % (module, type(t).__name__)
                    if type(t).__module__ == module and c not in classes:
                        classes.append(c)
                    elif type(t).__module__ != module:
                        raise ValueError('test importé d’un autre module')
        parcourir(suite)
    except Exception:
        classes = []                             # on garde le module entier
    Path(sortie).write_text(json.dumps(classes), encoding='utf-8')


# --- côté chef d'orchestre ----------------------------------------------------------------

def _modules(motif):
    return sorted(p.stem for p in TESTS.glob(motif) if p.is_file())


def _charger_durees():
    try:
        return json.loads(DUREES.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}


def _arreter(proc):
    if os.name == 'nt':
        subprocess.run(['taskkill', '/T', '/F', '/PID', str(proc.pid)],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    else:
        proc.kill()


def _lancer(args_internes, dossier, delai):
    """Lance ce script en mode interne dans un processus dédié ; renvoie (code, sortie texte)."""
    env = dict(os.environ)
    for v in ('TMP', 'TEMP', 'TMPDIR'):
        env[v] = str(dossier)
    env.setdefault('PYTHONUTF8', '1')
    env['PYTHONDONTWRITEBYTECODE'] = '1'
    proc = subprocess.Popen([sys.executable, str(Path(__file__).resolve())] + args_internes,
                            cwd=str(RACINE), env=env, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)
    try:
        sortie, _ = proc.communicate(timeout=delai)
        return proc.returncode, sortie.decode('utf-8', 'replace')
    except subprocess.TimeoutExpired:
        _arreter(proc)
        try:
            sortie, _ = proc.communicate(timeout=30)
        except subprocess.TimeoutExpired:
            sortie = b''
        return None, sortie.decode('utf-8', 'replace')


def _taches(modules, durees, decoupe, base, delai):
    """Remplace les modules longs par leurs classes, puis trie du plus long au plus court."""
    taches = []
    for m in modules:
        if durees.get(m, 0) > decoupe:
            d = Path(tempfile.mkdtemp(prefix='liste-', dir=base))
            f = d / 'classes.json'
            _lancer(['--interne-lister', m, str(f)], d, delai)
            try:
                classes = json.loads(f.read_text(encoding='utf-8'))
            except (OSError, ValueError):
                classes = []
            if len(classes) > 1:
                taches.extend(classes)
                continue
        taches.append(m)
    def estimation(t):
        if t in durees:
            return durees[t]
        m = t.split('.')[0]
        n = sum(1 for x in taches if x.split('.')[0] == m)
        return durees.get(m, 30.0) / max(n, 1)
    return sorted(taches, key=estimation, reverse=True)


def main(argv=None):
    p = argparse.ArgumentParser(description='Suite de tests répartie sur plusieurs processus.')
    p.add_argument('modules', nargs='*', help='modules ou classes (défaut : tous les tests/test_*.py)')
    p.add_argument('-j', '--processus', type=int, default=max(1, min((os.cpu_count() or 2) - 1, 12)),
                   help='processus simultanés (défaut : cœurs moins un, 12 au plus)')
    p.add_argument('--motif', default='test_*.py', help='motif de découverte (défaut : test_*.py)')
    p.add_argument('--delai', type=int, default=1800, help='secondes au plus par tâche (défaut : 1800)')
    p.add_argument('--decoupe', type=float, default=120.0,
                   help='au-delà de cette durée mesurée (s), un module est découpé par classe')
    p.add_argument('--sortie', help='écrit le détail des résultats dans ce fichier JSON')
    p.add_argument('-v', '--verbeux', action='store_true', help='une ligne par tâche terminée')
    p.add_argument('--interne-executer', nargs=2, help=argparse.SUPPRESS)
    p.add_argument('--interne-lister', nargs=2, help=argparse.SUPPRESS)
    a = p.parse_args(argv)

    if a.interne_executer:
        _executer(*a.interne_executer)
        return 0
    if a.interne_lister:
        _lister_classes(*a.interne_lister)
        return 0

    debut = time.monotonic()
    durees = _charger_durees()
    base = Path(tempfile.mkdtemp(prefix='axh-tests-'))
    modules = a.modules or _modules(a.motif)
    taches = _taches(modules, durees, a.decoupe, base, a.delai) if not a.modules else list(modules)
    total = len(taches)
    print('%d tâche(s) sur %d processus.' % (total, a.processus), flush=True)

    verrou = threading.Lock()
    resultats = []

    def une(nom):
        d = Path(tempfile.mkdtemp(prefix='t-', dir=base))
        f = d / 'resultat.json'
        code, sortie = _lancer(['--interne-executer', nom, str(f)], d, a.delai)
        try:
            r = json.loads(f.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            motif = 'délai de %d s dépassé' % a.delai if code is None else 'processus terminé (code %s) sans résultat' % code
            r = {'nom': nom, 'tests': 0, 'echecs': [], 'ignores': 0, 'echecs_attendus': 0,
                 'succes_inattendus': 0, 'duree': a.delai if code is None else 0,
                 'erreurs': [(nom, motif + '\n' + sortie[-5000:])], 'journal': sortie[-20000:]}
        shutil.rmtree(d, ignore_errors=True)
        with verrou:
            resultats.append(r)
            ok = not r['echecs'] and not r['erreurs']
            if a.verbeux or not ok:
                print('[%3d/%d] %-60s %4d tests %7.1f s %s' % (
                    len(resultats), total, nom, r['tests'], r['duree'],
                    'OK' if ok else 'ÉCHEC'), flush=True)
            elif len(resultats) % 25 == 0:
                print('[%3d/%d] …' % (len(resultats), total), flush=True)
        return r

    with concurrent.futures.ThreadPoolExecutor(max_workers=a.processus) as pool:
        list(pool.map(une, taches))
    shutil.rmtree(base, ignore_errors=True)

    # durées : par tâche, et cumul par module pour décider du découpage au passage suivant
    par_module = {}
    for r in resultats:
        durees[r['nom']] = r['duree']
        m = r['nom'].split('.')[0]
        par_module[m] = par_module.get(m, 0) + r['duree']
    for m, d in par_module.items():
        durees[m] = round(d, 2)
    try:
        DUREES.write_text(json.dumps(durees, indent=1, sort_keys=True), encoding='utf-8')
    except OSError:
        pass

    tests = sum(r['tests'] for r in resultats)
    echecs = [e for r in resultats for e in r['echecs']]
    erreurs = [e for r in resultats for e in r['erreurs']]
    ignores = sum(r['ignores'] for r in resultats)
    for titre, liste in (('ERROR', erreurs), ('FAIL', echecs)):
        for ident, tb in liste:
            print('=' * 70)
            print('%s: %s' % (titre, ident))
            print('-' * 70)
            print(tb)
    duree = time.monotonic() - debut
    print('-' * 70)
    print('Ran %d tests in %.3fs' % (tests, duree))
    print()
    details = []
    if echecs:
        details.append('failures=%d' % len(echecs))
    if erreurs:
        details.append('errors=%d' % len(erreurs))
    if ignores:
        details.append('skipped=%d' % ignores)
    if echecs or erreurs:
        print('FAILED (%s)' % ', '.join(details))
    else:
        print('OK' + (' (%s)' % ', '.join(details) if details else ''))
    lents = sorted(resultats, key=lambda r: r['duree'], reverse=True)[:5]
    print('Tâches les plus longues : ' + ', '.join('%s %.0f s' % (r['nom'], r['duree']) for r in lents))
    if a.sortie:
        Path(a.sortie).write_text(json.dumps({'tests': tests, 'duree': round(duree, 1),
                                              'echecs': len(echecs), 'erreurs': len(erreurs),
                                              'ignores': ignores, 'taches': resultats},
                                             ensure_ascii=False, indent=1), encoding='utf-8')
    return 1 if (echecs or erreurs) else 0


if __name__ == '__main__':
    sys.exit(main())
