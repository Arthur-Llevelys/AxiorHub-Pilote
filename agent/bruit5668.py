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


def depot_label(label):
    """5.6.99 : nom affiché (« 09 - Scanner ») d'un répertoire de dépôt, pour les listes construites à partir des seuls libellés."""
    return _base(label) in DEPOT_NAMES


def clients(rows):
    """5.6.99 : dossiers clients seulement (répertoires de dépôt retirés), pour les listes et les choix de dossier."""
    return [m for m in rows if not depot_folder(m)]


def depots(desk):
    """5.6.99 : identifiants des répertoires de dépôt présents au registre (registre complet, sans filtre de la personne)."""
    from .common import load_matters
    try:
        rows = load_matters(desk.c, unfiltered=True)
    except Stop:
        return frozenset()
    return frozenset(str(m['id']) for m in rows if depot_folder(m))


AUTOMATIQUES = ('mission', 'docreq', 'plan')        # travaux automatiques d'un répertoire de dépôt : retirés de « À décider »
GARDES = ('decision:document:', 'decision:mails:')  # classement d'une pièce arrivée, courriels à rattacher : conservés


def automatique_de_depot(item, ids):
    """5.6.99 : élément de la file produit automatiquement pour un répertoire de dépôt (analyse, note, demande en échec)."""
    if str(item.get('matter') or '') not in ids:
        return False
    objet = str(item.get('object') or '')
    return not objet.startswith(GARDES) and item.get('class') != 'courriel'


def ecarter_depots(desk, actor='agent'):
    """5.6.99 (choix du cabinet) : pour chaque répertoire de dépôt, une fois, même effet que « Ne plus proposer pour ce dossier »
    (proactivité exclue, missions automatiques en attente écartées, préparations suspendues) ; à chaque passage, ses demandes de document
    en échec ou sans dossier sont annulées. Aucun fichier n'est touché ; les notes déjà produites restent dans le stockage. Répétable."""
    ids = depots(desk)
    if not ids:
        return {'depots': 0, 'exclus': 0, 'demandes_annulees': 0}
    faits = set(desk.settings('depots5699:exclus', []) or [])
    exclus = 0
    for mid in sorted(ids - faits):
        try:
            exclude_matter(desk, mid, actor)
        except Stop:
            continue
        faits.add(mid)
        exclus += 1
    if exclus:
        desk.setting('depots5699:exclus', sorted(faits))
    annulees = 0
    try:
        marques = ','.join('?' for _ in ids)
        cur = desk.db.execute("UPDATE docreq520 SET status='annule', updated=? WHERE status IN ('dossier_a_choisir','echec') AND matter IN (%s)" % marques,
                              [desk.now()] + sorted(ids))
        annulees = cur.rowcount
        desk.db.commit()
    except Exception:   # noqa: BLE001 — table absente avant la première demande
        pass
    if exclus or annulees:
        desk.audit('depots5699_ecartes', {'depots': sorted(ids), 'exclus': exclus, 'demandes_annulees': annulees})
    return {'depots': len(ids), 'exclus': exclus, 'demandes_annulees': annulees}


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
    paused = 0
    try:   # 5.6.69 : préparations automatiques de ce dossier (pièces nouvelles) mises en pause
        from . import plans568
        for row in desk.db.execute("SELECT DISTINCT p.id,p.owner FROM plans_v568 p JOIN events_v568 e ON e.plan_id=p.id WHERE p.matter=? "
                                   "AND e.source='document' AND p.state IN ('active','blocked')", (matter,)).fetchall():
            try:
                plans568.control(desk, {'id': row[0], 'action': 'pause'}, row[1])
                paused += 1
            except Stop:
                continue
    except Exception:   # noqa: BLE001 — tables absentes avant la première préparation
        pass
    desk.audit('proactivite5668_dossier_exclu', {'matter': matter, 'missions_ecartees': cancelled, 'plans_suspendus': paused, 'par': str(actor)[:80]})
    return {'ok': True, 'ecartees': cancelled,
            'message': 'Dossier exclu des missions automatiques ; %d mission(s) en attente écartée(s). Réversible dans Paramètres › Proactivité.' % cancelled}
