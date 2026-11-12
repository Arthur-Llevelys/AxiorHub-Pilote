"""5.6.97 (lot proteger, B02 et J07) : choix des sources juridiques AVANT tout appel réseau.

Défaut corrigé : ``registre5634.search()`` connaissait la sélection mais appelait ``research_enabled_mcp()`` sans la transmettre ; la boucle des
connecteurs (``extensions364.legal_connector_search``) interrogeait alors tous les connecteurs actifs et le tri venait après l'appel. Une
sélection vide redevenait « tous ».

Règles :
- ``None`` (non précisé) : les sources autorisées par défaut, c'est-à-dire actives pour cette installation et cette capacité ;
- liste vide : aucune source, donc aucun appel ;
- identifiant inconnu : erreur de validation (``fournisseur_juridique_invalide``), avant tout appel ;
- source demandée mais inactive (désactivée, non testée, révoquée) : écartée, aucun appel ;
- zones demandées : seules les sources qui couvrent l'une d'elles ;
- mode local strict de la politique des données (ou politique illisible) : aucune source externe (MCP, API REST). Seule une source dont le
  transport est local (index intégré, ``transport`` ou ``protocol`` = ``local``/``integre``) reste possible.

L'ensemble retenu (``Selection.ids``) est transmis jusqu'à chaque transport : le registre, la recherche MCP, la boucle des connecteurs et
la recherche REST le revérifient juste avant de se connecter. Aucune lecture réseau ici : catalogue et état viennent de la configuration.
"""
from collections import namedtuple

from .common import Stop

Selection = namedtuple('Selection', 'ids ecartes local_strict demande')
LOCAUX = frozenset(('local', 'integre', 'index_local'))
MOTIFS = {'fournisseur_juridique_invalide': 'Source juridique inconnue : la recherche n’a pas été lancée.',
          'source_inactive': 'Source désactivée, non testée ou révoquée : elle n’a pas été interrogée.',
          'hors_zone': 'Source sans couverture du territoire demandé : elle n’a pas été interrogée.',
          'mode_local_strict': 'Mode local strict : aucune source juridique externe n’est interrogée.',
          'aucune_source_choisie': 'Aucune source choisie : aucune recherche n’a été lancée.'}


def normaliser(demande):
    """``None`` reste ``None`` ; une chaîne « a, b » ou une liste devient un tuple d'identifiants en minuscules, sans doublon. Un élément qui
    n'est pas une chaîne est une erreur de validation (jamais ignoré en silence, jamais « tous »)."""
    if demande is None:
        return None
    if isinstance(demande, str):
        demande = [x for x in demande.split(',') if x.strip()]
    if not isinstance(demande, (list, tuple, set, frozenset)):
        raise Stop('fournisseur_juridique_invalide')
    out = []
    for pid in demande:
        if not isinstance(pid, str) or not pid.strip():
            raise Stop('fournisseur_juridique_invalide')
        out.append(pid.strip().lower())
    return tuple(dict.fromkeys(out))


def local_strict(desk):
    """Vrai en mode local strict ; une politique illisible vaut local strict (aucun appel externe par défaut)."""
    from . import data_policy5689
    try:
        return data_policy5689.charger(desk.c.get('state_dir'))['mode'] == 'local_strict'
    except Exception:   # noqa: BLE001 — PolitiqueIllisible ou base inaccessible : bloqué, jamais ouvert
        return True


def est_locale(entree):
    return str(entree.get('transport') or entree.get('protocol') or '').lower() in LOCAUX


def _anciens_connecteurs(desk):
    """Connecteurs actifs installés avant le catalogue (sans champ ``provider``), reconnus à leur nom comme le fait la boucle des connecteurs :
    actifs, testés, envoi externe admis. Lecture de la configuration seulement."""
    from .extensions364 import _legal_provider
    out = []
    for item in (desk.c.get('lawve_extensions') or {}).values():
        if (isinstance(item, dict) and item.get('kind') == 'connector' and item.get('enabled') and item.get('external_data_allowed')
                and (item.get('last_test') or {}).get('status') == 'ok'):
            pid = _legal_provider(item)
            if pid and pid not in out:
                out.append(pid)
    return out


def toutes_locales(ids):
    """Vrai si l'ensemble n'est fait que de sources locales (adaptateurs intégrés) ; un ensemble vide n'est pas « local »."""
    from . import registre5634
    catalogue = registre5634.catalog()
    return bool(ids) and all(pid in catalogue and est_locale(catalogue[pid]) for pid in ids)


def item_local(item, pid):
    """Connecteur réellement local : son transport ET la fiche du catalogue le disent (un connecteur distant d'une fiche « intégrée » ne l'est pas)."""
    from . import registre5634
    entree = registre5634.catalog().get(pid)
    return bool(entree) and est_locale(entree) and str(item.get('transport') or '').lower() in LOCAUX


def selectionner(desk, demande=None, zones=None, capacite='search'):
    """Sources à interroger pour cette demande. Lève ``Stop('fournisseur_juridique_invalide')`` pour un identifiant inconnu."""
    from . import registre5634
    catalogue = registre5634.catalog()
    ids = normaliser(demande)
    if ids is not None:
        inconnus = [pid for pid in ids if pid not in catalogue]
        if inconnus:
            raise Stop('fournisseur_juridique_invalide')
    actives = [r['id'] for r in registre5634.available(desk, capacite)]
    actives += [pid for pid in _anciens_connecteurs(desk) if pid in catalogue and pid not in actives]
    zones = {str(z).strip().upper() for z in (zones or ()) if str(z).strip()}
    strict = local_strict(desk)
    gardes, ecartes = [], {}
    for pid in (tuple(actives) if ids is None else ids):
        entree = catalogue[pid]
        if pid not in actives:
            ecartes[pid] = 'source_inactive'
        elif zones and not zones & {str(t).upper() for t in entree.get('territories') or ()}:
            ecartes[pid] = 'hors_zone'
        elif strict and not est_locale(entree):
            ecartes[pid] = 'mode_local_strict'
        else:
            gardes.append(pid)
    return Selection(tuple(dict.fromkeys(gardes)), ecartes, strict, ids)


def autorises(desk, providers):
    """Ensemble autorisé transmis à un transport : ``None`` (appel d'un ancien parcours) = sélection par défaut recalculée ici."""
    if providers is None:
        return frozenset(selectionner(desk).ids)
    return frozenset(normaliser(providers) or ())


def rapports_ecartes(selection):
    """Lignes de rapport lisibles pour les sources écartées (aucun appel n'est parti vers elles)."""
    return [{'provider': pid, 'status': 'skipped', 'error': motif, 'message': MOTIFS.get(motif, motif)} for pid, motif in selection.ecartes.items()]
