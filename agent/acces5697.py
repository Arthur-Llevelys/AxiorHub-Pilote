"""5.6.97 (lot proteger, B01 · B07 · B02) : garde d'accès unique aux dossiers et aux messages.

Tout affichage, compte, export, recherche ou envoi au modèle passe par ce module **avant** filtres, LIMIT, totaux et tri. Le registre des
droits reste celui de la 5.6.50 (``membres5650`` : rôles, droits par compte, dossiers réservés) : ce module n'en crée pas un second, il en
fait la porte d'entrée commune des autres lots.

- ``peut_lire_dossier(desk, acteur, dossier_id)`` : vrai si l'acteur peut voir ce dossier (``''`` : élément sans dossier, visible).
- ``dossiers_autorises(desk, acteur)`` : périmètre de l'acteur (``in``, ``filtrer``, ``sql`` pour joindre les droits avant LIMIT).
- ``peut_lire_message(desk, acteur, ref)`` : vrai si l'acteur peut voir ce message ; le dossier est lu sur le serveur (B01).
- ``exiger(desk, acteur, dossier_id=None, ref=None)`` : refus neutre ``Stop('introuvable')`` (404), sans dire si l'objet existe.

Acteur : ``(compte, rôle)``, un dict ``{'actor_id'|'owner', 'role'}`` (forme d'``operations5693.acteur``), l'environnement WSGI de la
requête authentifiée (règle de ``web567.actor`` : sans en-tête d'identité, installation à authentification serveur), ou ``AGENT`` (travaux du cabinet en arrière-plan, non cloisonnés, comme en 5.6.50). Un acteur absent, un rôle inconnu
ou un registre illisible refusent tout : jamais « aucune restriction » par défaut.
"""
from collections import namedtuple

from .common import Stop

NEUTRE = 'introuvable'                    # même réponse pour « réservé » et « absent » : l'existence n'est pas divulguée
Acteur = namedtuple('Acteur', 'compte role')
AGENT = Acteur('agent_cabinet', 'agent')  # travaux du cabinet, sans personne derrière (non cloisonnés, comme en 5.6.50)


def acteur_de(acteur):
    """Normalise un acteur ; absent ou illisible : ``Stop('acteur_requis')`` (jamais un administrateur par défaut)."""
    if isinstance(acteur, Acteur):
        compte, role = acteur
    elif isinstance(acteur, dict) and ('REQUEST_METHOD' in acteur or 'HTTP_X_AXIORHUB_USER' in acteur or 'wsgi.input' in acteur):
        from .web567 import actor
        compte, role = actor(acteur)
    elif isinstance(acteur, dict):
        compte, role = acteur.get('actor_id') or acteur.get('owner') or acteur.get('compte') or '', acteur.get('role') or ''
    elif isinstance(acteur, (tuple, list)) and len(acteur) == 2:
        compte, role = acteur
    else:
        raise Stop('acteur_requis')
    compte, role = str(compte or '').strip(), str(role or '').strip()
    if not compte or not role:
        raise Stop('acteur_requis')
    return Acteur(compte, role)


def _etat(desk):
    return desk.c.get('state_dir') if hasattr(desk, 'c') else desk


def masques(desk, acteur):
    """Dossiers que l'acteur ne peut pas voir (ensemble vide : aucun). Rôle inconnu : ``Stop('role_insuffisant')``."""
    from . import membres5650
    a = acteur_de(acteur)
    if a == AGENT:
        return frozenset()
    if a.role not in membres5650.ROLES:
        raise Stop('role_insuffisant')
    # registre relu à chaque appel (une révocation vaut immédiatement) ; les dossiers masqués de la requête en cours ne font qu'ajouter
    return frozenset(membres5650.hidden_for(_etat(desk), a.compte, a.role)) | frozenset(membres5650.viewer_hidden() or ())


def acteur_requete():
    """Acteur de la requête en cours, tel que ``membres5650.gate`` l'a établi ; hors requête (travaux du cabinet) : ``AGENT``."""
    from . import membres5650
    compte = membres5650.current_author()
    if not compte:
        return AGENT
    return Acteur(compte, membres5650.viewer_role())


def masques_requete(desk):
    """Dossiers masqués pour l'acteur de la requête en cours (vide hors requête), pour les projections appelées sans acteur explicite."""
    from . import membres5650
    a = acteur_requete()
    if a == AGENT:
        return frozenset(membres5650.viewer_hidden() or ())
    if a.compte == 'jeton_service':          # jeton de service (5.6.88) : pas d'identité ; les masques posés par le contrôle central valent
        return frozenset(membres5650.viewer_hidden() or ())
    return masques(desk, a)


class Perimetre:
    """Dossiers visibles d'un acteur, défini par exclusion des dossiers réservés à d'autres (un dossier nouveau est visible)."""

    def __init__(self, acteur, caches):
        self.acteur, self.masques = acteur, frozenset(caches)

    def __contains__(self, dossier_id):
        return str(dossier_id or '') not in self.masques

    def filtrer(self, elements, cle=lambda x: x.get('matter', '')):
        return [x for x in elements if cle(x) in self]

    def sql(self, colonne):
        """Fragment ``WHERE`` à joindre AVANT ORDER BY / LIMIT : ``(texte, paramètres)`` ; la colonne vient du code, jamais de la requête."""
        if not self.masques:
            return '1=1', ()
        caches = sorted(self.masques)
        return "COALESCE(%s,'') NOT IN (%s)" % (colonne, ','.join('?' * len(caches))), tuple(caches)

    def sql_json(self, colonne):
        """Même fragment pour une colonne qui contient une liste JSON de dossiers (candidats d'un rapprochement) : aucun ne doit être masqué."""
        if not self.masques:
            return '1=1', ()
        caches = sorted(self.masques)
        # liste illisible : refusée (jamais lue comme « aucun dossier ») ; vide : aucun candidat
        return ("(COALESCE(%s,'')='' OR json_valid(%s)) AND NOT EXISTS (SELECT 1 FROM json_each(CASE WHEN json_valid(%s) THEN %s ELSE '[]' END) j "
                "WHERE j.value IN (%s))" % (colonne, colonne, colonne, colonne, ','.join('?' * len(caches))), tuple(caches))


def dossiers_autorises(desk, acteur):
    a = acteur_de(acteur)
    return Perimetre(a, masques(desk, a))


def peut_lire_dossier(desk, acteur, dossier_id):
    try:
        return str(dossier_id or '') not in masques(desk, acteur)
    except Stop:
        return False


def dossiers_du_message(desk, ref):
    """Dossiers rattachés au message ``ref`` d'après le registre du serveur (voir ``messages5697.resoudre``)."""
    from .messages5697 import resoudre
    return resoudre(desk, ref).dossiers


def visible(dossiers, caches):
    """Règle d'un message : aucun de ses dossiers n'est masqué ; un rattachement illisible ne passe que pour un acteur sans masque."""
    from .messages5697 import ILLISIBLE
    dossiers = set(dossiers or ())
    return not (dossiers & set(caches)) and not (caches and ILLISIBLE in dossiers)


def filtrer_elements(desk, acteur, elements, caches=None):
    """Éléments d'une file ou d'un compteur visibles par l'acteur, à appliquer AVANT tri, borne et totaux : le dossier de l'élément, et pour
    un élément qui désigne un message (``mail_key`` ou objet « …:<clé> »), les dossiers que le serveur rattache à ce message."""
    import re
    from .messages5697 import dossiers_par_cle
    caches = frozenset(masques(desk, acteur) if caches is None else caches)
    if not caches:
        return list(elements)
    gardes = [x for x in elements if str(x.get('matter') or '') not in caches]
    cle_de = {}
    for x in gardes:
        trouve = re.search(r'(?:^|:)([0-9a-f]{64})$', str(x.get('mail_key') or x.get('object') or ''))
        if trouve:
            cle_de[id(x)] = trouve.group(1)
    associations = dossiers_par_cle(desk, list(cle_de.values())) if cle_de else {}
    return [x for x in gardes if id(x) not in cle_de or visible(associations.get(cle_de[id(x)], ()), caches)]


def peut_lire_message(desk, acteur, ref):
    """Vrai si l'acteur peut voir CE message : tous les dossiers que le serveur lui associe doivent être visibles (associations
    contradictoires : droits exigés sur chacune). Message inconnu ou registre illisible : faux."""
    try:
        caches = masques(desk, acteur)
        dossiers = dossiers_du_message(desk, ref)
    except Stop:
        return False
    return visible(dossiers, caches)


def exiger(desk, acteur, dossier_id=None, ref=None):
    """Refus neutre (``Stop('introuvable')``, rendu 404) si l'acteur ne peut voir ni le dossier ni le message désignés. Rend l'acteur normalisé."""
    a = acteur_de(acteur)
    if dossier_id is not None and not peut_lire_dossier(desk, a, dossier_id):
        raise Stop(NEUTRE)
    if ref is not None and not peut_lire_message(desk, a, ref):
        raise Stop(NEUTRE)
    return a
