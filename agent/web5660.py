"""5.6.60 : routes ``/api440/m5660/...`` des actions du diagnostic de la file (« L'agent en un coup d'œil »).

- ``file/arreter`` : demande l'arrêt d'un travail (même effet que « Arrêter ce travail » de l'activité de l'assistant) ;
- ``file/relancer`` : un travail qui attend le verrou est repris dès que possible (son délai d'attente est levé).

Réservées à l'avocat et à l'administrateur.
"""
from .common import Stop

WRITE = ('file/arreter', 'file/relancer')
CATEGORIES = {}


def allowed(role, route):
    return role in ('administrateur', 'avocat') and route.split('?', 1)[0] in WRITE


def api(env, desk, auth, prefix, name, args, method):
    from .web440 import json_out
    from .web567 import actor
    from .web5634 import _post
    owner, role = actor(env)
    route = name[len('m5660/'):].split('?', 1)[0]
    if not allowed(role, route):
        raise Stop('role_insuffisant')
    if method != 'POST':
        raise Stop('methode_refusee')
    data = _post(env, auth, 10_000)
    try:
        job = int(data.get('job') or 0)
    except (TypeError, ValueError):
        raise Stop('operation_invalide') from None
    if route == 'file/arreter':
        out = desk.cancel_job(job)
        out['message'] = 'Arrêt demandé.' if out.get('status') == 'cancel_requested' else 'Travail annulé.'
        return json_out(out)
    from .blocage5660 import retry_now
    return json_out(retry_now(desk, job))
