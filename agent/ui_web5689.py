"""5.6.89 : routes ``/api440/m5689/ui/*`` (drapeaux, contexte du panneau IA, travail contextuel) et ``/api440/m5689/capacites`` (fiches des fonctions).

Lecture de l'état : tous les rôles. Le rôle et le compte viennent de la requête authentifiée, jamais du corps ; ``check_post`` est passé sur chaque écriture. Une route inconnue est refusée.
``ui/contexte`` est en POST parce qu'il reçoit la description de la page, pas parce qu'il écrit : il ne lance rien. Aucune route d'ici n'envoie, ne signe, ne dépose, ne facture ni ne paie. Le contrôle de la route n'exempte pas
le service de refaire le sien : ``ui_ai5689`` revalide contexte, action et droits à chaque appel, et à l'exécution du travail. Écriture des drapeaux et chargement des situations du banc : administrateur (``ADMIN_API`` et contrôle ici).
"""
import re

from . import capacites5689 as capacites, ui_ai5689 as ia, ui_drapeaux5689 as drapeaux
from .common import Stop

LECTURE = ('ui/drapeaux', 'ui/resultat', 'capacites')
ECRITURE = ('ui/contexte', 'ui/mission', 'ui/mission/repondre')
ADMIN = ('ui/drapeaux', 'capacites/pertinence')
ROUTE_FICHE = re.compile(r'^capacites/[a-z_]{1,40}$')
STATUTS = {'dossier_reserve': '403 Forbidden', 'role_insuffisant': '403 Forbidden', 'action_non_disponible': '403 Forbidden', 'version_en_conflit': '409 Conflict', 'contexte_ambigu': '409 Conflict', 'requete_reutilisee_avec_autres_donnees': '409 Conflict',
           'cle_idempotence_deja_utilisee': '409 Conflict', 'fonction_inconnue': '404 Not Found', 'objet_absent': '404 Not Found', 'dossier_absent': '404 Not Found', 'mission_absente': '404 Not Found', 'livrable_absent': '404 Not Found', 'version_inconnue': '404 Not Found', 'essai_absent': '404 Not Found', 'route_inconnue': '404 Not Found'}
MESSAGES = {'contexte_invalide': 'La demande ne décrit pas une page connue : rechargez la page.', 'fonction_inconnue': 'Cette page n’a pas de fiche d’assistance.', 'objet_absent': 'L’élément sélectionné n’existe plus ou ne vous est pas accessible.',
            'action_non_disponible': 'Cette action n’est pas disponible ici, ou plus pour ce compte.', 'contexte_ambigu': 'Le contexte est ambigu : précisez le dossier avant de lancer ce travail.', 'dossier_reserve': 'Ce dossier est réservé : ce compte n’y a pas accès.',
            'version_en_conflit': 'Une version plus récente existe : rouvrez-la avant de lancer ce travail ; rien n’a été lancé.', 'mission_sans_question': 'Ce travail n’attend pas de réponse.'}


def allowed(role, route):
    route = route.split('?', 1)[0]
    if role not in ('administrateur', 'avocat', 'assistant'):
        return False
    return route in LECTURE or route in ECRITURE or route in ADMIN or bool(ROUTE_FICHE.fullmatch(route))


def _refus(code, json_out):
    from .erreurs5634 import explain
    return json_out({'error': code, 'message': MESSAGES.get(code) or explain(code, None)}, STATUTS.get(code, '400 Bad Request'))


def api(env, desk, auth, prefix, name, args, method):
    from .web440 import check_post, json_out
    from .web567 import actor
    owner, role = actor(env)
    route = name[len('m5689/'):].split('?', 1)[0]
    lecture = method in ('GET', 'HEAD')
    try:
        if route == 'ui/drapeaux' and lecture:
            return json_out(drapeaux.etat(desk))
        if route == 'ui/drapeaux' and method == 'POST':
            data = check_post(env, auth)
            if role != 'administrateur':
                return json_out({'error': 'role_insuffisant', 'message': 'Seul un administrateur modifie ces réglages.'}, '403 Forbidden')
            return json_out(drapeaux.enregistrer(desk, data))
        if route == 'ui/resultat' and lecture:
            return json_out(ia.lire_resultat(desk, str((args or {}).get('id') or ''), owner, role))
        if route == 'capacites' and lecture:
            return json_out(capacites.tableau(desk, owner, role))
        if route == 'capacites/pertinence' and method == 'POST':
            data = check_post(env, auth)
            if role != 'administrateur':
                return _refus('role_insuffisant', json_out)
            from .banc_pertinence5689 import charger
            return json_out(charger(desk, owner, data.get('cle')))
        if route == 'capacites/pertinence' and lecture:
            if role == 'assistant':
                return _refus('role_insuffisant', json_out)
            from .banc_pertinence5689 import bilan
            return json_out(bilan(desk, str((args or {}).get('essai') or '')))
        if ROUTE_FICHE.fullmatch(route) and lecture:
            fid = route.split('/', 1)[1]
            from .fonctions5631 import INDEX, visible
            if fid not in INDEX:
                raise Stop('fonction_inconnue')
            if not visible(role, INDEX[fid][2]):
                raise Stop('role_insuffisant')
            return json_out(capacites.vue(desk, fid, owner, role))
        if route in ECRITURE and method == 'POST':
            data = check_post(env, auth)
            if route == 'ui/contexte':
                return json_out(ia.panneau(desk, data, owner, role))
            if route == 'ui/mission':
                return json_out(ia.creer(desk, data, owner, role, prefix))
            return json_out(ia.repondre(desk, data, owner, role, prefix))
    except Stop as ex:
        return _refus(str(ex).split(':')[0], json_out)
    return json_out({'error': 'route_inconnue', 'message': 'Cette adresse n’existe pas.'}, '404 Not Found')
