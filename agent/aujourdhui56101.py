"""5.6.101 (lot P, P01-P04, V101-08) : Aujourd'hui allégé.

- P01 : le premier envoi garde l'en-tête, le bandeau de sécurité (lot S, ``securite56101.bandeau_html``), « Actuellement », le bandeau des
  routines, les trois premières lignes du rang et « Ma journée ». Le reste est remplacé par une **attente** (``attente``) que
  ``v56101-aujourdhui.js`` remplit par un fragment ``GET /api440/m56101/aujourdhui/fragment?nom=…`` :
  * au dépli quand l'attente est dans un ``<details>`` fermé (cartes de la file historique, formulaires des routines, comptes rendus du
    briefing, style du cabinet) ;
  * juste après l'affichage quand elle est visible (cartes de « Travail préparé »), sans bloquer le premier envoi.
- Les fragments appellent les mêmes fonctions que la page complète, avec le compte, le rôle et les dossiers réservés de la requête
  (R04 : droits filtrés avant toute ligne et tout compteur) ; ils n'écrivent rien (R05) : aucune préparation de schéma n'est faite ici.
  Réponse ``Cache-Control: private, no-store`` ; objet absent ou interdit : même 404 neutre ; rôle qui ne peut jamais l'action : 403.
- P03 : chaque attente porte un lien vers la page complète correspondante (lisible sans JavaScript).
- P04 : un fragment ouvert est gardé pour l'onglet (``sessionStorage``, par compte, dix minutes) et remis sans nouvelle requête au retour
  par le bouton Précédent (le navigateur ne garde pas la page : réponses ``no-store``).
Les deux présentations (interrupteur ``interface56100``) passent par les mêmes attentes ; l'interrupteur n'intervient pas dans les droits.
"""
from html import escape as e
import json
from urllib.parse import urlencode

from .common import Stop

PREFIXE = 'm56101/aujourdhui/'
FRAGMENTS = ('travail', 'file', 'style', 'briefing', 'routine')
# page complète de chaque bloc différé (P03) ; « routine » et « briefing » : la gestion complète des routines et leurs comptes rendus
COMPLETES = {'travail': '/livrables', 'file': '/aujourdhui?vue=file', 'style': '/mon-style', 'briefing': '/aujourdhui?vue=routines',
             'routine': '/aujourdhui?vue=routines'}
ROLES = ('administrateur', 'avocat', 'assistant')
ID_MAX = 80


def T(cle, **valeurs):
    from .i18n5631 import t
    return t('p101.' + cle, **valeurs)


def attente(nom, prefix, params=None):
    """Bloc différé : chargé par ``v56101-aujourdhui.js`` (au dépli ou juste après l'affichage) ; lien vers la page complète sans script."""
    donnees = (' data-p101-params="%s"' % e(urlencode(params), quote=True)) if params else ''
    return ('<div class="p101-fragment" data-p101-fragment="%s"%s><p class="p101-attente" role="status">%s <a href="%s">%s</a></p></div>') % (
        e(nom, quote=True), donnees, e(T('attente')), e(prefix + COMPLETES[nom], quote=True), e(T('complet')))


def script_html(prefix):
    """Messages du script (texte brut, lus par ``textContent``) et script différé ; une seule fois par page."""
    messages = {k: T(k) for k in ('chargement', 'panne', 'vide')}
    return ('<script type="application/json" id="p101-messages">%s</script><script defer src="%s"></script>') % (
        json.dumps(messages, ensure_ascii=False).replace('<', '\\u003c'), e(prefix + '/static/v56101-aujourdhui.js', quote=True))


def bandeau_securite(desk, prefix, owner, role):
    """Contrat lot S → lot P : bandeau des alertes critiques du serveur, juste sous l'en-tête (chaîne vide si rien, si le rôle n'est pas
    administrateur ou si la veille n'existe pas ici). Import paresseux protégé : une panne de la veille ne cache jamais Aujourd'hui."""
    try:
        from .securite56101 import bandeau_html
        return bandeau_html(desk, prefix, owner, role) or ''
    except Exception:   # noqa: BLE001
        return ''


# ------------------------------------------------------------------------------------------------ fragments (lecture seule)
def _travail(desk, prefix, owner, role, args):
    from .accueil5689 import travail_html
    from .artefacts56109 import travaux_prets_html   # 5.6.109 (lot V, F08) : point de montage — trois livrables structurés récents, mêmes documents et même lecteur que Produire (lecture seule, aucun compteur)
    return travail_html(desk, prefix) + travaux_prets_html(desk, prefix)


def _file(desk, prefix, owner, role, args):
    """Cartes de la file historique « À décider / À relire » (mêmes cartes et mêmes droits que la page complète)."""
    from .file5651 import html as file_html
    from .membres5650 import viewer_hidden
    try:
        return file_html(desk, prefix, None, str(args.get('file') or ''), viewer_hidden() or frozenset(), 8, actor=owner, role=role)
    except Exception as ex:   # noqa: BLE001 — 5.6.61 : la file signale son erreur au lieu de disparaître (même message que la page avant 5.6.101)
        import logging
        logging.getLogger(__name__).exception('file À décider / À relire : %s', ex)
        return ('<section class="c530-card"><h2>À décider / À relire</h2><p class="d51-warn" role="alert">La file n’a pas pu être '
                'affichée (%s). Les éléments sont conservés ; voir le journal de l’interface.</p></section>') % e(type(ex).__name__)


def _style(desk, prefix, owner, role, args):
    from .cockpit530 import style_html
    return style_html(desk, prefix)


def _briefing(desk, prefix, owner, role, args):
    """Comptes rendus des routines (briefing, tri, bilan, documents). Le lien vers « Ma liste du jour » n'est gardé que si la liste existe."""
    from .cockpit530 import LIEN_MATIN, _matin5688, routines_html
    html = routines_html(desk, prefix, owner)
    return html if _matin5688(desk, prefix, owner) else html.replace(LIEN_MATIN, '')


def _routine(desk, prefix, owner, role, args):
    """Formulaire « Modifier » d'une routine que la personne peut modifier, ou « Ajouter une routine » (administrateur, avocat)."""
    from . import routines5652 as rt
    from .web5652 import formulaire_routine
    ident = str(args.get('id') or '')[:ID_MAX]
    if not ident:
        if role not in ('administrateur', 'avocat'):
            raise Stop('role_insuffisant')
        return formulaire_routine(desk, prefix, None)
    for r in rt.listing(desk, owner, role)['routines']:
        if r['id'] == ident and r['editable']:
            return formulaire_routine(desk, prefix, r)
    raise Stop('introuvable')     # absente ou non modifiable par ce compte : même réponse


CONSTRUCTEURS = {'travail': _travail, 'file': _file, 'style': _style, 'briefing': _briefing, 'routine': _routine}


def fragment(desk, prefix, owner, role, nom, args=None):
    """HTML d'un fragment ; ``Stop('introuvable')`` pour un nom inconnu (404 neutre)."""
    if role not in ROLES:
        raise Stop('role_insuffisant')
    construire = CONSTRUCTEURS.get(str(nom or ''))
    if construire is None:
        raise Stop('introuvable')
    return construire(desk, prefix, owner, role, dict(args or {}))


def _sortie(data, status='200 OK'):
    from .web440 import json_out
    return {**json_out(data, status), 'headers': [('Cache-Control', 'private, no-store')]}


def repondre(env, desk, auth, prefix, name, args, method):
    """``GET /api440/m56101/aujourdhui/fragment?nom=…`` ; toute autre méthode ou route : refus sans effet."""
    from .web567 import actor
    owner, role = actor(env)
    route = name.split('?', 1)[0][len(PREFIXE):]
    if route != 'fragment' or method not in ('GET', 'HEAD'):
        return _sortie({'error': 'introuvable', 'message': 'Élément introuvable.'}, '404 Not Found')
    try:
        return _sortie({'html': fragment(desk, prefix, owner, role, args.get('nom'), args)})
    except Stop as ex:
        code = str(ex).split(':')[0]
        if code == 'role_insuffisant':
            return _sortie({'error': code, 'message': 'Action réservée à un autre rôle.'}, '403 Forbidden')
        return _sortie({'error': 'introuvable', 'message': 'Élément introuvable.'}, '404 Not Found')
    except Exception:   # noqa: BLE001 — panne d'une source : dite comme telle, jamais un contenu vide présenté comme complet
        import logging
        logging.getLogger(__name__).exception('fragment d’Aujourd’hui')
        return _sortie({'error': 'indisponible', 'message': 'Contenu momentanément indisponible.'}, '503 Service Unavailable')
