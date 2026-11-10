"""5.6.96 (lot affichage, U07) : route ``/api440/m5689/affichage/session``.

- ``affichage/session`` (GET, tous les rôles) : après un retour par l'historique (page restaurée par le navigateur), la page redemande au
  serveur si la session est toujours ouverte et à quel compte elle appartient. La réponse ne porte que le repère court du compte
  (``affichage5696.empreinte_compte``), jamais l'identifiant ; aucune écriture, aucun appel à l'IA. Si la session est fermée, la requête est
  refusée par l'authentification commune avant d'arriver ici ; si le compte a changé, la page se recharge (``v5689-navigation.js``).
"""
from .common import Stop

LECTURE = ('affichage/session',)


def allowed(role, route):
    return role in ('administrateur', 'avocat', 'assistant') and route.split('?', 1)[0] in LECTURE


def api(env, desk, auth, prefix, name, args, method):
    from .affichage5696 import empreinte_compte
    from .web440 import json_out
    from .web567 import actor
    owner, role = actor(env)
    route = name[len('m5689/'):].split('?', 1)[0]
    try:
        if not allowed(role, route):
            raise Stop('role_insuffisant' if route in LECTURE else 'route_inconnue')
        if method != 'GET':
            raise Stop('route_inconnue')
    except Stop as error:
        code = str(error)
        return json_out({'error': code, 'message': 'Action réservée.' if code == 'role_insuffisant' else 'Route inconnue.'},
                        '403 Forbidden' if code == 'role_insuffisant' else '404 Not Found')
    out = json_out({'compte': empreinte_compte(owner)})
    out['headers'] = [('Cache-Control', 'no-store')]
    return out
