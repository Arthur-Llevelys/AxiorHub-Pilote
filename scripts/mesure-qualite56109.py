#!/usr/bin/env python3
"""5.6.109 (lot Q, G22) : mesure de la chaîne de qualité locale AVANT tout choix d'option (OCR, détecteur, extraction alternative).

À lancer par le cabinet sur le poste cible, avec SON jeu de recette (jamais sur des données réelles transmises ailleurs). Aucun réseau
(les connexions sortantes sont bloquées pendant la mesure), aucun modèle, aucun téléchargement, aucune écriture dans les dossiers mesurés.

Jeu de recette (dossier fourni par le cabinet) :
  cas.jsonl    une ligne par situation : {"id", "corpus": "local" | "fonds", "question", "date" (facultative, AAAA-MM-JJ),
               "attendu": [{"document": nom de fichier ou identifiant officiel, "passage": texte exact attendu}],
               "reserve_finale": true/false (cas réservés à l'évaluation finale), "corrections_humaines": nombre (facultatif, relevé
               par l'avocat après relecture), "minutes_correction": nombre (facultatif)}
  fichiers/    fichiers du corpus local (copiés dans un dossier temporaire : les originaux ne sont jamais ouverts en écriture)
  lot/         lot du fonds juridique local (manifeste.json + documents.jsonl), facultatif

Mesures, pour la chaîne actuelle (« base ») et, si demandée, UNE option à la fois (``--option``) : mémoire (pic des allocations Python,
et mémoire maximale du processus quand le système la donne), latence par question (médiane, 95e centile), rappel à k (documents attendus
parmi les k premiers), citations exactes (passage attendu relu dans un extrait rendu), corrections humaines et minutes déclarées par le
cabinet. Sans ``--jeu`` : petit jeu FICTIF de démonstration, qui vérifie seulement que le script fonctionne — aucun gain n'est mesurable
ainsi ; le rapport le dit.

Exemples :
  python scripts/mesure-qualite56109.py --sortie rapport.json
  python scripts/mesure-qualite56109.py --jeu D:/recette-cabinet --k 5 --sortie rapport.json
  python scripts/mesure-qualite56109.py --jeu D:/recette-cabinet --option paddleocr --sortie rapport.json
"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import socket
import sqlite3
import statistics
import sys
import tempfile
import time
import tracemalloc

RACINE = Path(__file__).resolve().parents[1]
if str(RACINE) not in sys.path:
    sys.path.insert(0, str(RACINE))


def _refuse_reseau(*a, **k):
    raise OSError('mesure56109 : aucun appel réseau pendant la mesure')


class Bureau:
    """Bureau minimal : base SQLite du banc (dossier temporaire), réglages en mémoire, journal ignoré. Juste ce que lisent les services."""

    def __init__(self, state_dir):
        self.c = {'state_dir': str(state_dir), 'documents': {}, 'rag': {}, 'ollama': {}, 'matters_file': str(Path(state_dir) / 'matters.json')}
        Path(self.c['matters_file']).write_text('[]', encoding='utf-8')
        self.db = sqlite3.connect(str(Path(state_dir) / 'desk.sqlite3'))
        self.db.row_factory = sqlite3.Row
        self._reglages = {}

    def settings(self, key, default=None):
        return self._reglages.get(key, default)

    def setting(self, key, value):
        self._reglages[key] = value

    def audit(self, action, data):
        return None


def _demo(dossier):
    """Jeu fictif (DURAND, ZORVAL, Valbrune) : trois fichiers, un lot de deux versions d'article, quatre cas."""
    f = dossier / 'fichiers'
    f.mkdir(parents=True)
    (f / 'contrat-zorval.txt').write_text('Contrat de prestation entre DURAND et ZORVAL.\n' + 'Clause générale sans objet. ' * 800 +
                                          '\nArticle 12 — Préavis : chaque partie peut résilier moyennant un préavis de trois mois.\n', encoding='utf-8')
    (f / 'note-valbrune.md').write_text('# Note Valbrune\nLe bail commercial de Valbrune expire le 30 juin.\n', encoding='utf-8')
    (f / 'honoraires.csv').write_text('poste;montant\nconsultation;1200\n', encoding='utf-8')
    lot = dossier / 'lot'
    lot.mkdir()
    textes = [('ART-1', 'v1', '2020-01-01', '2025-01-01', 'Le délai de préavis est de un mois.'),
              ('ART-1', 'v2', '2025-01-01', '', 'Le délai de préavis est de deux mois.')]
    lignes = [json.dumps({'official_id': o, 'version_id': v, 'kind': 'article', 'jurisdiction': 'FR', 'language': 'fr', 'valid_from': a,
                          'valid_to_exclusive': b, 'intervalle_verifie': True, 'text': t, 'sha256': hashlib.sha256(t.encode()).hexdigest(),
                          'title': 'Article fictif %s' % o}, ensure_ascii=False) for o, v, a, b, t in textes]
    (lot / 'documents.jsonl').write_text('\n'.join(lignes) + '\n', encoding='utf-8')
    (lot / 'manifeste.json').write_text(json.dumps({'snapshot_id': 'demo-1', 'source': 'source-fictive', 'pays': 'FR', 'rights_ref': 'licence-fictive',
                                                     'licence': 'fictive', 'licence_verifiee': True, 'coverage': 'selection_limitee',
                                                     'expected_count': 2, 'complet': True, 'corpus_au': '2026-10-01'}), encoding='utf-8')
    cas = [{'id': 'c1', 'corpus': 'local', 'question': 'préavis résiliation', 'attendu': [{'document': 'contrat-zorval.txt', 'passage': 'préavis de trois mois'}]},
           {'id': 'c2', 'corpus': 'local', 'question': 'bail Valbrune expiration', 'attendu': [{'document': 'note-valbrune.md', 'passage': 'expire le 30 juin'}]},
           {'id': 'c3', 'corpus': 'fonds', 'question': 'délai de préavis', 'date': '2024-06-01', 'attendu': [{'document': 'ART-1', 'passage': 'délai de préavis est de un mois'}]},
           {'id': 'c4', 'corpus': 'fonds', 'question': 'délai de préavis', 'date': '2025-06-01', 'attendu': [{'document': 'ART-1', 'passage': 'délai de préavis est de deux mois'}],
            'reserve_finale': True}]
    (dossier / 'cas.jsonl').write_text('\n'.join(json.dumps(c, ensure_ascii=False) for c in cas) + '\n', encoding='utf-8')


def _maxrss():
    if sys.platform == 'win32':                     # pic de la mémoire de travail du processus (API Windows, sans dépendance)
        try:
            import ctypes
            from ctypes import wintypes

            class Compteurs(ctypes.Structure):
                _fields_ = [('cb', wintypes.DWORD), ('PageFaultCount', wintypes.DWORD), ('PeakWorkingSetSize', ctypes.c_size_t),
                            ('WorkingSetSize', ctypes.c_size_t), ('QuotaPeakPagedPoolUsage', ctypes.c_size_t), ('QuotaPagedPoolUsage', ctypes.c_size_t),
                            ('QuotaPeakNonPagedPoolUsage', ctypes.c_size_t), ('QuotaNonPagedPoolUsage', ctypes.c_size_t),
                            ('PagefileUsage', ctypes.c_size_t), ('PeakPagefileUsage', ctypes.c_size_t)]
            c = Compteurs()
            c.cb = ctypes.sizeof(c)
            k32 = ctypes.WinDLL('kernel32')
            k32.GetCurrentProcess.restype = wintypes.HANDLE
            psapi = ctypes.WinDLL('psapi')
            psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(Compteurs), wintypes.DWORD]
            if psapi.GetProcessMemoryInfo(k32.GetCurrentProcess(), ctypes.byref(c), c.cb):
                return {'valeur': int(c.PeakWorkingSetSize), 'unite': 'octets'}
        except (OSError, AttributeError, ValueError):
            pass
    try:
        import resource
        valeur = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return {'valeur': valeur, 'unite': 'octets' if sys.platform == 'darwin' else 'kio'}
    except (ImportError, AttributeError, OSError):
        return {'valeur': None, 'unite': 'non disponible sur ce système (pic des allocations Python seulement)'}


def _p95(valeurs):
    if not valeurs:
        return None
    triees = sorted(valeurs)
    return round(triees[min(len(triees) - 1, int(round(0.95 * (len(triees) - 1))))], 2)


def preparer(bureau, jeu, travail):
    """Corpus local (copie de travail des fichiers du jeu) et lot du fonds, indexés par les services du produit."""
    from agent import contexte56109 as C, fonds_juridique56109 as F
    from unittest.mock import patch
    acteur = ('mesure@example.test', 'administrateur')
    ids = []
    if (jeu / 'fichiers').is_dir():
        copie = travail / 'corpus'
        shutil.copytree(jeu / 'fichiers', copie)
        with patch('agent.contexte56109._sur_le_poste', return_value=True), patch('agent.contexte56109._poste', return_value='poste-mesure'):
            e = C.ajouter(bureau, acteur, str(copie), 'Recette', cle='mesure-ajout-0001')
            ids.append(e['id'])
            for _ in range(1000):
                if C.indexer(bureau, acteur, e['id'], pas=200, checkpoint=lambda d: None)['complet']:
                    break
    if (jeu / 'lot').is_dir():
        F.importer(bureau, acteur, str(jeu / 'lot'), 'mesure-import-0001', pas=500)
        for _ in range(1000):
            lot = bureau.db.execute("SELECT id, etat FROM fonds56109_lots ORDER BY cree_le DESC LIMIT 1").fetchone()
            if lot['etat'] not in ('preparation', 'indexation'):
                break
            F.avancer(bureau, acteur, lot['id'], pas=500, checkpoint=lambda d: None)
    return acteur, ids


def executer(bureau, acteur, ids, cas, k):
    from agent import contexte56109 as C, fonds_juridique56109 as F
    from agent.qualite_sources56109 import passage_relu
    from unittest.mock import patch
    latences, rappels, exactes, total_passages, details = [], [], 0, 0, []
    for c in cas:
        debut = time.perf_counter()
        if c.get('corpus') == 'fonds':
            resultats, _ = F.rechercher(bureau, c['question'], date_utile=c.get('date') or '', limite=k)
            noms = [r['official_id'] for r in resultats]
        else:
            with patch('agent.contexte56109._poste', return_value='poste-mesure'):
                resultats, _ = C.chercher(bureau, acteur, ids, c['question'], k)
            noms = [r['nom'] for r in resultats]
        latences.append((time.perf_counter() - debut) * 1000)
        attendus = c.get('attendu') or []
        trouves = sum(1 for a in attendus if a.get('document') in noms[:k])
        rappels.append(trouves / len(attendus) if attendus else 1.0)
        for a in attendus:
            total_passages += 1
            if any(passage_relu(a.get('passage'), r.get('extrait', '')) for r in resultats if a.get('document') in (r.get('nom'), r.get('official_id'))):
                exactes += 1
        details.append({'id': c.get('id'), 'rappel': rappels[-1], 'latence_ms': round(latences[-1], 2), 'reserve_finale': bool(c.get('reserve_finale'))})
    corrections = [c['corrections_humaines'] for c in cas if isinstance(c.get('corrections_humaines'), (int, float))]
    minutes = [c['minutes_correction'] for c in cas if isinstance(c.get('minutes_correction'), (int, float))]
    return {'questions': len(cas), 'latence_ms': {'mediane': round(statistics.median(latences), 2) if latences else None, 'p95': _p95(latences)},
            'rappel_a_k': round(sum(rappels) / len(rappels), 4) if rappels else None, 'k': k,
            'citations_exactes': {'relues': exactes, 'attendues': total_passages},
            'corrections_humaines': sum(corrections) if corrections else 'non renseignées par le cabinet',
            'minutes_correction': sum(minutes) if minutes else 'non renseignées par le cabinet', 'cas': details}


def mesurer(jeu=None, k=5, option=None):
    """Rapport de mesure (dictionnaire). ``jeu`` : dossier du cabinet, ou ``None`` pour la démonstration fictive."""
    from agent import contexte56109 as C, fonds_juridique56109 as F
    from agent.documents import OPTIONS56109, options_locales
    from unittest.mock import patch
    with tempfile.TemporaryDirectory(prefix='mesure56109-') as td, patch.object(socket.socket, 'connect', _refuse_reseau), \
            patch.object(socket, 'create_connection', _refuse_reseau):
        td = Path(td)
        demo = jeu is None
        if demo:
            jeu = td / 'jeu'
            _demo(jeu)
        jeu = Path(jeu)
        state = td / 'etat'
        state.mkdir()
        bureau = Bureau(state)
        F.ensure_schema(bureau)
        C.ensure_schema(bureau)
        cas = [json.loads(x) for x in (jeu / 'cas.jsonl').read_text(encoding='utf-8').splitlines() if x.strip()]
        tracemalloc.start()
        debut = time.perf_counter()
        acteur, ids = preparer(bureau, jeu, td)
        preparation_ms = (time.perf_counter() - debut) * 1000
        base = executer(bureau, acteur, ids, [c for c in cas if not c.get('reserve_finale')], k)
        finale = executer(bureau, acteur, ids, [c for c in cas if c.get('reserve_finale')], k)
        _, pic = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        bureau.db.close()
        variante = None
        if option:
            etat = options_locales({'options56109': {option: {'active': True, 'qualifiee': False}}}).get(option, 'inconnue')
            variante = {'option': option, 'etat': etat, 'mesuree': False,
                        'motif': 'option inconnue' if option not in OPTIONS56109 else
                                 ('non installée sur ce poste : rien n’est téléchargé' if etat == 'non_installee' else
                                  'aucun adaptateur livré dans cette version : comparaison impossible')}
        return {'version_script': '5.6.109', 'jeu': 'démonstration fictive' if demo else str(jeu), 'reseau': 'bloqué pendant la mesure',
                'memoire': {'pic_allocations_python_octets': pic, 'processus_maximum': _maxrss()}, 'preparation_ms': round(preparation_ms, 2),
                'base': base, 'evaluation_finale': finale, 'variante': variante,
                'gain_mesure': False if demo else None,
                'avertissement': ('Démonstration fictive : vérifie seulement que la mesure fonctionne ; aucun gain de qualité n’est mesurable sans '
                                  'le jeu de recette du cabinet (50 situations, une partie réservée à l’évaluation finale).') if demo else
                                 ('Mesure sur le jeu du cabinet. Comparer une seule option à la fois à la base ; les corrections humaines '
                                  'sont celles déclarées dans cas.jsonl.')}


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    p.add_argument('--jeu', help='dossier du jeu de recette du cabinet (cas.jsonl, fichiers/, lot/)')
    p.add_argument('--k', type=int, default=5)
    p.add_argument('--option', help='une option à comparer (iscc_tika, paddleocr, gliner, privacy_filter)')
    p.add_argument('--sortie', help='fichier JSON du rapport')
    a = p.parse_args(argv)
    rapport = mesurer(a.jeu, max(1, min(a.k, 20)), a.option)
    texte = json.dumps(rapport, ensure_ascii=False, indent=2)
    if a.sortie:
        Path(a.sortie).write_text(texte, encoding='utf-8', newline='\n')
    print(texte)
    return 0


if __name__ == '__main__':
    sys.exit(main())
