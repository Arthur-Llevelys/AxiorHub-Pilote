#!/usr/bin/env python3
"""5.6.95 (R04) : mesure de la recherche dans le dossier, sans puis avec un reclasseur, sur les cas fictifs de ``tests/data/recherche5695``.

Poste de développement seulement, jamais le serveur du cabinet. Aucun réseau. Aucun reclasseur n'est livré (option retirée par le cabinet en
5.6.95 : aucun gain de rappel mesuré, licence du modèle essayé incertaine) ; pour mesurer un futur modèle à licence sûre, fournir un fichier
Python local qui définit ``reclasser(question, textes) -> scores`` (``--reclasseur FICHIER``), exécuté dans l'environnement qui lance le script.

Exemples :
  python scripts/mesure-recherche.py --sortie rapport.json                              # recherche actuelle seule
  python scripts/mesure-recherche.py --reclasseur mon_modele.py --mode fusion remplacement --sortie rapport.json

Mesures, pour chaque configuration : rappel à 5 et à 10, MRR, part des questions dont les pièces citées (les 3 premières, comme pour un
brouillon) sont les bonnes, respect des décisions de l'avocat, extrait attendu présent, temps par question (médiane, 95e centile) et mémoire
du processus. Règle proposée : un reclasseur n'est « recommandé » que si le rappel à 5 gagne au moins 10 points, que le 95e centile reste
sous 2 secondes et que les décisions de l'avocat sont respectées à 100 %.
"""
import argparse
from hashlib import sha256
import importlib.util
import json
from pathlib import Path
import platform
import sqlite3
import statistics
import sys
import tempfile
import time
import uuid

RACINE = Path(__file__).resolve().parents[1]
if str(RACINE) not in sys.path:
    sys.path.insert(0, str(RACINE))
CAS = RACINE / 'tests' / 'data' / 'recherche5695' / 'cas.json'
SEUIL_GAIN = 0.10
SEUIL_P95_MS = 2000.0


# ------------------------------------------------------------------------------------------------ banc
class Bureau:
    """Faux bureau : base SQLite en mémoire, réglages en mémoire, journal ignoré. Juste ce que lit ``recherche5695``."""

    def __init__(self, state_dir):
        self.c = {'state_dir': str(state_dir), 'rag': {}, 'ollama': {}, 'documents': {}}
        self.db = sqlite3.connect(':memory:')
        self.db.row_factory = sqlite3.Row
        self._reglages = {}
        self.journal = []

    def settings(self, key, default=None):
        return self._reglages.get(key, default)

    def setting(self, key, value):
        self._reglages[key] = value

    def audit(self, kind, data):
        self.journal.append((kind, data))

    def now(self):
        return '2026-01-01T00:00:00+00:00'


def charger(state_dir, cas=None):
    """(bureau, index) remplis avec les cas fictifs : documents indexés, décisions de l'avocat enregistrées dans leurs tables réelles."""
    from agent.index import DocumentIndex
    from agent import assertions5689, legal_memory
    cas = cas or json.loads(CAS.read_text(encoding='utf-8'))
    desk = Bureau(state_dir)
    desk.db.executescript(assertions5689.SCHEMA)
    legal_memory.ensure_schema(desk)
    index = DocumentIndex(state_dir)
    for d in cas['documents']:
        index.put_source(d['dossier'], d['chemin'], d['texte'], sha256(d['texte'].encode('utf-8')).hexdigest(), d['date'], d['nature'],
                         {'confidentiality': 'matter', 'source': 'banc5695'})
        index.db.execute('INSERT OR REPLACE INTO docs VALUES (?,?,?,?,?,?)', (d['dossier'], d['chemin'], 'e', d['date'], d['texte'], ''))
    index.db.commit()
    dec = cas['decisions']
    for chemin in dec['pieces_ecartees']:
        mid = chemin.split('/')[2]
        desk.db.execute("INSERT INTO source_versions5689(version_id,cabinet_id,matter,source_id,path,sha256,etat,importe_le,retiree_le) VALUES(?,?,?,?,?,?,?,?,?)",
                        (uuid.uuid4().hex, 'banc', mid, uuid.uuid4().hex, chemin, uuid.uuid4().hex, 'retiree', desk.now(), desk.now()))
    for r in dec['memoire']:
        desk.db.execute('INSERT INTO legal_memory_records VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                        (r['id'], r['dossier'], r['type'], r['titre'], r['contenu'], 'avocat', '', 1.0, r['statut'], '[]', '{}', 'banc', 1, '',
                         desk.now(), desk.now(), desk.now() if r['statut'] in ('validated', 'pinned') else '', ''))
    for r in dec['assertions']:
        desk.db.execute('INSERT INTO case_assertions5689(id,cabinet_id,matter,kind,statement,asserted_by,status,created,updated) VALUES(?,?,?,?,?,?,?,?,?)',
                        (r['id'], 'banc', r['dossier'], r['kind'], r['texte'], r['selon'], r['statut'], desk.now(), desk.now()))
    desk.db.commit()
    return desk, index, cas


def memoire_processus():
    """(mémoire actuelle, pic) du processus en Mo ; None si la mesure n'est pas disponible."""
    try:
        if platform.system() == 'Windows':
            import ctypes
            from ctypes import wintypes

            class Compteurs(ctypes.Structure):
                _fields_ = [('cb', wintypes.DWORD), ('PageFaultCount', wintypes.DWORD), ('PeakWorkingSetSize', ctypes.c_size_t),
                            ('WorkingSetSize', ctypes.c_size_t), ('QuotaPeakPagedPoolUsage', ctypes.c_size_t), ('QuotaPagedPoolUsage', ctypes.c_size_t),
                            ('QuotaPeakNonPagedPoolUsage', ctypes.c_size_t), ('QuotaNonPagedPoolUsage', ctypes.c_size_t),
                            ('PagefileUsage', ctypes.c_size_t), ('PeakPagefileUsage', ctypes.c_size_t)]
            c = Compteurs()
            c.cb = ctypes.sizeof(c)
            psapi = ctypes.WinDLL('psapi')
            psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(Compteurs), wintypes.DWORD]
            k32 = ctypes.WinDLL('kernel32')
            k32.GetCurrentProcess.restype = wintypes.HANDLE
            if psapi.GetProcessMemoryInfo(k32.GetCurrentProcess(), ctypes.byref(c), c.cb):
                return round(c.WorkingSetSize / 1e6, 1), round(c.PeakWorkingSetSize / 1e6, 1)
            return None
        valeurs = {}
        for ligne in Path('/proc/self/status').read_text().splitlines():
            if ligne.startswith(('VmRSS', 'VmHWM')):
                valeurs[ligne.split(':')[0]] = int(ligne.split()[1]) / 1000
        return round(valeurs['VmRSS'], 1), round(valeurs['VmHWM'], 1)
    except Exception:   # noqa: BLE001 — mesure indisponible : dite comme telle
        return None


def _centile(valeurs, p):
    valeurs = sorted(valeurs)
    if not valeurs:
        return None
    k = (len(valeurs) - 1) * p
    bas, haut = int(k), min(int(k) + 1, len(valeurs) - 1)
    return valeurs[bas] + (valeurs[haut] - valeurs[bas]) * (k - bas)


def mesurer(desk, index, cas, reclasseur=None, limite=10, seuil_abstention=None, mode=None):
    """Mesure toutes les questions ; ``reclasseur`` : None (recherche actuelle) ou fonction (question, textes) -> scores ; ``mode`` :
    'fusion' ou 'remplacement' (voir ``recherche5695.MODE``)."""
    from agent import recherche5695
    ecartees = set(cas['decisions']['pieces_ecartees'])
    refusees = {r['id'] for r in cas['decisions']['memoire'] if r['statut'] in ('disputed', 'archived')}
    refusees |= {r['id'] for r in cas['decisions']['assertions'] if r['statut'] in ('rejected', 'superseded')}
    details, temps = [], []
    for q in cas['questions']:
        debut = time.perf_counter()
        trouves, etat = recherche5695.chercher(desk, q['dossier'], q['question'], limite, index=index, reclasseur=reclasseur, mode=mode)
        temps.append((time.perf_counter() - debut) * 1000)
        documents = list(dict.fromkeys(p['document'] for p in trouves if not p['valide_avocat']))
        attendus = q['attendus']
        rang = next((i + 1 for i, d in enumerate(documents) if d in attendus), None)
        ligne = {'id': q['id'], 'dossier': q['dossier'], 'piege': q['piege'], 'rang': rang, 'premiers': documents[:3],
                 'reclassement': etat.get('reclassement'), 'temps_ms': round(temps[-1], 1)}
        if attendus:
            ligne['rappel5'] = len(set(attendus) & set(documents[:5])) / len(attendus)
            ligne['rappel10'] = len(set(attendus) & set(documents[:10])) / len(attendus)
            ligne['rr'] = 1 / rang if rang else 0.0
            ligne['pieces_justes'] = set(attendus) <= set(documents[:3]) and not (set(q['interdits']) & set(documents[:3]))
            if q['passage']:
                ligne['extrait_juste'] = any(q['passage'] in p['extrait'] for p in trouves if p['document'] in attendus)
        # décisions de l'avocat : pièce écartée jamais rendue, élément refusé jamais rendu, élément validé attendu en tête
        cles = {p['source_id'] for p in trouves if p['valide_avocat']}
        respect = not (ecartees & set(documents)) and not any(any(k.endswith(r[:12]) or r in k for r in refusees) for k in cles)
        if q['memoire_attendue']:
            respect = respect and bool(trouves) and trouves[0]['valide_avocat'] and q['memoire_attendue'] in trouves[0]['source_id']
        ligne['decisions_respectees'] = respect
        if seuil_abstention is not None:   # signal « rien dans le dossier » : score brut du modèle sous le seuil
            scores = [p['score'] for p in trouves if p['score'] is not None and p['provenance'] == 'reclasse']
            ligne['meilleur_score'] = max(scores) if scores else None
            ligne['abstention'] = not scores or max(scores) < seuil_abstention
        details.append(ligne)
    avec = [d for d in details if 'rappel5' in d]
    moyenne = lambda cle, rows: round(sum(float(r[cle]) for r in rows) / len(rows), 4) if rows else None
    pieges = {}
    for d in details:
        if d['piege']:
            pieges.setdefault(d['piege'], []).append(d)
    synthese = {
        'questions': len(details), 'questions_avec_reponse': len(avec),
        'rappel_a_5': moyenne('rappel5', avec), 'rappel_a_10': moyenne('rappel10', avec), 'mrr': moyenne('rr', avec),
        'pieces_justes': moyenne('pieces_justes', avec),
        'extrait_juste': moyenne('extrait_juste', [d for d in avec if 'extrait_juste' in d]),
        'decisions_respectees': moyenne('decisions_respectees', details),
        'temps_median_ms': round(statistics.median(temps), 1), 'temps_p95_ms': round(_centile(temps, 0.95), 1),
        'par_piege': {k: {'n': len(v), 'decisions_respectees': moyenne('decisions_respectees', v),
                          **({'rappel_a_5': moyenne('rappel5', [x for x in v if 'rappel5' in x]), 'mrr': moyenne('rr', [x for x in v if 'rr' in x])}
                             if any('rappel5' in x for x in v) else {}),
                          **({'abstention': moyenne('abstention', v)} if any('abstention' in x for x in v) else {})} for k, v in sorted(pieges.items())},
    }
    if seuil_abstention is not None:   # une abstention ne vaut que si les questions qui ont une réponse ne sont pas, elles aussi, écartées
        synthese['abstention_sans_reponse'] = moyenne('abstention', [d for d in details if d['piege'] == 'sans_reponse'])
        synthese['abstention_a_tort'] = moyenne('abstention', avec)
        synthese['seuil_abstention'] = seuil_abstention
    return {'synthese': synthese, 'details': details}


# ------------------------------------------------------------------------------------------------ reclasseur local (facultatif)
def reclasseur_local(fichier):
    """Fonction ``reclasser(question, textes) -> scores`` d'un fichier Python local (aucun téléchargement par ce script)."""
    spec = importlib.util.spec_from_file_location('reclasseur_mesure5695', str(fichier))
    module = importlib.util.module_from_spec(spec)
    avant = memoire_processus()
    debut = time.perf_counter()
    spec.loader.exec_module(module)
    if not callable(getattr(module, 'reclasser', None)):
        raise SystemExit('Le fichier doit définir reclasser(question, textes) -> scores : %s' % fichier)
    return module.reclasser, {'chargement_ms': round((time.perf_counter() - debut) * 1000, 1), 'memoire_avant_mo': avant,
                              'memoire_apres_chargement_mo': memoire_processus()}


# ------------------------------------------------------------------------------------------------ programme
def une_configuration(spec, repetitions=1):
    """Mesure d'une configuration ({'reclasseur', 'mode'} ou {} pour la recherche actuelle), dans le processus courant."""
    with tempfile.TemporaryDirectory(prefix='recherche5695-') as tmp:
        desk, index, cas = charger(tmp)
        fonction, info = None, {}
        if spec:
            fonction, info = reclasseur_local(spec['reclasseur'])
        resultat = None
        for _ in range(max(1, repetitions)):
            resultat = mesurer(desk, index, cas, fonction, seuil_abstention=spec.get('seuil'), mode=spec.get('mode'))
        resultat['synthese']['memoire_processus_mo'] = memoire_processus()
        index.db.close()
    nom = 'recherche actuelle' if not spec else 'reclasseur %s, %s' % (Path(spec['reclasseur']).name, spec['mode'])
    return {'nom': nom, **spec, **info, **resultat}


def main(argv=None):
    import subprocess
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--reclasseur', help='fichier Python local définissant reclasser(question, textes) -> scores')
    parser.add_argument('--mode', nargs='+', default=['fusion'], choices=('fusion', 'remplacement'))
    parser.add_argument('--seuil-abstention', type=float, default=None, help='score brut sous lequel le reclasseur dit « rien dans le dossier »')
    parser.add_argument('--repetitions', type=int, default=1, help='passages complets par configuration (le premier sert aussi de chauffe)')
    parser.add_argument('--sortie', default='mesure-recherche5695.json')
    parser.add_argument('--configuration', help=argparse.SUPPRESS)   # usage interne : une configuration par processus (mémoire propre)
    args = parser.parse_args(argv)
    if args.configuration:
        resultat = une_configuration(json.loads(args.configuration), args.repetitions)
        Path(args.sortie).write_text(json.dumps(resultat, ensure_ascii=False), encoding='utf-8')
        return resultat
    cas = json.loads(CAS.read_text(encoding='utf-8'))
    rapport = {'date': time.strftime('%Y-%m-%dT%H:%M:%S'), 'machine': {'systeme': platform.platform(), 'processeur': platform.processor(),
               'python': platform.python_version()}, 'cas': CAS.relative_to(RACINE).as_posix(),
               'jeu': {'dossiers': len(cas['dossiers']), 'documents': len(cas['documents']), 'questions': len(cas['questions'])}, 'configurations': []}
    specs = [{}]
    if args.reclasseur:
        specs += [{'reclasseur': str(Path(args.reclasseur).resolve()), 'mode': m, 'seuil': args.seuil_abstention} for m in args.mode]
    with tempfile.TemporaryDirectory(prefix='recherche5695-rapport-') as tmp:
        for i, spec in enumerate(specs):
            sortie = Path(tmp) / ('%d.json' % i)
            commande = [sys.executable, str(Path(__file__).resolve()), '--configuration', json.dumps(spec), '--sortie', str(sortie),
                        '--repetitions', str(args.repetitions)]
            subprocess.run(commande, check=True)
            c = json.loads(sortie.read_text(encoding='utf-8'))
            rapport['configurations'].append(c)
            s = c['synthese']
            print('%-42s rappel@5 %.3f  rappel@10 %.3f  MRR %.3f  pièces justes %.3f  décisions %.3f  médiane %.0f ms  p95 %.0f ms  mémoire %s Mo' % (
                c['nom'], s['rappel_a_5'], s['rappel_a_10'], s['mrr'], s['pieces_justes'], s['decisions_respectees'], s['temps_median_ms'],
                s['temps_p95_ms'], s['memoire_processus_mo']))
    base = rapport['configurations'][0]['synthese']
    for c in rapport['configurations'][1:]:
        s = c['synthese']
        c['gain_rappel_a_5'] = round(s['rappel_a_5'] - base['rappel_a_5'], 4)
        c['gain_mrr'] = round(s['mrr'] - base['mrr'], 4)
        c['recommande'] = c['gain_rappel_a_5'] >= SEUIL_GAIN and s['temps_p95_ms'] < SEUIL_P95_MS and s['decisions_respectees'] == 1.0
    rapport['regle'] = ('recommandé si gain de rappel à 5 >= %d points, 95e centile < %d ms et décisions de l’avocat respectées à 100 %%'
                        % (SEUIL_GAIN * 100, SEUIL_P95_MS))
    Path(args.sortie).write_text(json.dumps(rapport, ensure_ascii=False, indent=1), encoding='utf-8')
    print('Rapport : %s' % args.sortie)
    return rapport


if __name__ == '__main__':
    main()
