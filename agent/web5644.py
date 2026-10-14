"""5.6.44 : routes /api440/m5644/… — corrections de l'avocat dans la page du dossier (supprimer, modifier, ajouter, annuler)."""
from .common import Stop
from .web5634 import _post


def allowed(role, route):
    return role in ('administrateur', 'avocat', 'assistant') and route.startswith('dossier/')


def api(env, desk, auth, prefix, name, args, method):
    from .web440 import json_out
    from .web567 import actor
    from . import dossier5644
    owner, role = actor(env)
    route = name[len('m5644/'):].split('?', 1)[0]
    if method != 'POST':
        raise Stop('methode_refusee')
    data = _post(env, auth, 20_000)
    if route == 'dossier/corriger':
        return json_out(dossier5644.correct(desk, owner, data))
    if route == 'dossier/annuler-correction':
        return json_out(dossier5644.undo(desk, owner, data))
    raise Stop('route_inconnue')
