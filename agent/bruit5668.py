"""5.6.68 : moins de bruit dans « À décider ».

Constat du 21 octobre 2026 : 33 cartes « Décision attendue par l'agent » pour le dossier « 09 - Scanner ». Chaque pièce scannée y créait
une mission automatique « Analyse les sources… » ; le nom du fichier désignant un autre dossier (le vrai client), la mission attendait
qu'on choisisse son dossier. Désormais :
- un dossier de dépôt (« Scanner », « Numérisation », « A TRAITER », « A classer »…, avec ou sans numéro devant) ne déclenche plus de
  mission automatique par nouveau document ; ses pièces restent consultables et indexées comme avant ;
- les missions automatiques en attente d'une décision sont regroupées par dossier en une seule carte, avec « Écarter » (toutes) et
  « Ne plus proposer pour ce dossier » (le dossier est exclu de la proactivité, ses missions automatiques en attente sont écartées).
"""
import re

from .common import Stop, fold

DEPOT_NAMES = frozenset(('scanner', 'scanners', 'scan', 'scans', 'numerisation', 'numerisations', 'documents numerises', 'a traiter', 'a classer',
                         'a ranger', 'arrivee', 'arrivees', 'depot', 'depots', 'courrier arrive', 'courriers arrives', 'courrier entrant',
                         'inbox', 'boite de depot'))


def _base(text):
    name = str(text or '').rstrip('/').rsplit('/', 1)[-1]
    name = re.sub(r'^\s*\d{1,3}\s*[-–_.)]\s*', '', name)          # « 09 - Scanner » → « Scanner »
    return re.sub(r'[\s_]+', ' ', fold(name)).strip()


def depot_folder(matter):
    """Vrai pour un dossier de dépôt (scanner, documents à classer…), jamais pour un dossier client."""
    m = matter or {}
    return any(_base(v) in DEPOT_NAMES for v in (m.get('path'), m.get('client_name'), m.get('name')) if v)


def exclude_matter(desk, matter, actor='cabinet'):
    """« Ne plus proposer pour ce dossier » : exclu de la proactivité de chaque profil ; missions automatiques en attente écartées."""
    from . import settings568
    from .common import load_matters
    from .file5657 import SAFETY
    matter = str(matter or '').strip()
    if matter not in {str(m['id']) for m in load_matters(desk.c)}:
        raise Stop('dossier_absent')
    owners = list(dict.fromkeys(settings568.owners(desk) + ['cabinet']))
    for owner in owners:
        p = settings568.profile(desk, owner)
        if matter not in p['excluded_matters']:
            try:
                settings568.save(desk, {'excluded_matters': p['excluded_matters'] + [matter]}, owner)
            except Stop:
                continue   # profil incomplet (identité à revoir) : l'exclusion vaut pour les autres profils
    try:
        cur = desk.db.execute("UPDATE missions_v567 SET state='cancelled',updated=? WHERE matter=? AND state='decision' AND instr(instruction,?)>0",
                              (desk.now(), matter, SAFETY))
        cancelled = cur.rowcount
        desk.db.commit()
    except Exception:   # noqa: BLE001 — table absente avant la première mission
        cancelled = 0
    desk.audit('proactivite5668_dossier_exclu', {'matter': matter, 'missions_ecartees': cancelled, 'par': str(actor)[:80]})
    return {'ok': True, 'ecartees': cancelled,
            'message': 'Dossier exclu des missions automatiques ; %d mission(s) en attente écartée(s). Réversible dans Paramètres › Proactivité.' % cancelled}
