"""5.6.95 (lot routage, G03, G04) : routes ``/api440/m5689/routage/*``.

Lecture (administrateur) : ``routage/mesures?periode=30`` (tableau en JSON) et ``routage/export?periode=30`` (CSV). Écriture
(administrateur, registre ``standalone_auth.ADMIN_API``, revérifié ici) : ``routage/mode`` {valeur: conseil|actif, revision} avec contrôle CSRF
(``check_post``) et révision lue par le formulaire (409 si elle est périmée ; la même valeur renvoyée deux fois est sans effet). Aucune
route ne lance un calcul de fond ni un appel au modèle ; aucune ne renvoie un texte ou un nom de dossier."""
from .common import Stop

LECTURE = ('routage/mesures', 'routage/export')
ECRITURE = ('routage/mode',)
MESSAGES = {'routage_mode_invalide': 'Choisissez « conseil » ou « actif ».',
            'routage_mesures_illisibles': 'Les mesures du routage n’ont pas pu être lues : rien n’est exporté plutôt qu’un faux zéro.',
            'revision_perimee': 'Le réglage a changé depuis l’ouverture de la page : rechargez-la, puis recommencez.'}


def allowed(role, route):
    return route in ECRITURE + LECTURE and role == 'administrateur'


def _erreur(error):
    from .erreurs5634 import explain
    from .web import REASONS
    from .web440 import json_out
    code = str(error).split(':')[0]
    statut = ('403 Forbidden' if code == 'role_insuffisant' else '409 Conflict' if code == 'revision_perimee' else
              '404 Not Found' if code == 'route_inconnue' else '400 Bad Request')
    return json_out({'error': code, 'message': MESSAGES.get(code) or REASONS.get(code) or explain(code, getattr(error, 'detail', None))}, statut)


def api(env, desk, auth, prefix, name, args, method):
    try:
        return _route(env, desk, auth, name, method, args or {})
    except Stop as error:
        return _erreur(error)


def _route(env, desk, auth, name, method, args):
    from . import routage5695
    from .web440 import check_post, json_out
    from .web567 import actor
    owner, role = actor(env)
    route = name[len('m5689/'):].split('?', 1)[0]
    if method == 'GET':
        if route not in LECTURE:
            raise Stop('route_inconnue')
        if role != 'administrateur':
            raise Stop('role_insuffisant')
        jours = routage5695._periode(args.get('periode'))
        if route == 'routage/export':
            nom, texte = routage5695.export_csv(desk, jours)
            return {'status': '200 OK', 'kind': 'text/csv; charset=utf-8', 'body': texte,
                    'headers': [('Content-Disposition', 'attachment; filename="%s"' % nom), ('Cache-Control', 'no-store')]}
        data = routage5695.tableau(desk, jours)
        if data is None:
            raise Stop('routage_mesures_illisibles')
        return json_out(data)
    if method != 'POST':
        raise Stop('methode_refusee')
    if route not in ECRITURE:
        raise Stop('route_inconnue')
    data = check_post(env, auth)
    if role != 'administrateur':
        raise Stop('role_insuffisant')
    return json_out(routage5695.enregistrer_mode(desk, str(data.get('valeur') or ''), data.get('revision'), owner))
