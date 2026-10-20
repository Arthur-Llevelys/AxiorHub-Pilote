"""5.6.67 : routes ``/api440/m5667/...``.

- ``corrections/decider`` : adopter ou écarter une correction récurrente (avocat, administrateur) ;
- ``modeles/appliquer`` : appliquer la répartition des modèles (administrateur) ;
- ``banc/lancer`` : lancer le banc d'essai d'un modèle local sur les cas du cabinet (administrateur).
"""
from .common import Stop

WORK = ('corrections/decider',)
ADMIN = ('modeles/appliquer', 'banc/lancer')


def allowed(role, route):
    route = route.split('?', 1)[0]
    if route in WORK:
        return role in ('administrateur', 'avocat')
    return role == 'administrateur' and route in ADMIN


def api(env, desk, auth, prefix, name, args, method):
    from .web440 import json_out
    from .web567 import actor
    from .web5634 import _post
    owner, role = actor(env)
    route = name[len('m5667/'):].split('?', 1)[0]
    if not allowed(role, route):
        raise Stop('role_insuffisant')
    if method != 'POST':
        raise Stop('methode_refusee')
    data = _post(env, auth, 4_000)
    if route == 'corrections/decider':
        from .apprentissage5667 import decide
        return json_out(decide(desk, str(data.get('id') or ''), str(data.get('action') or '')))
    if route == 'modeles/appliquer':
        from .modeles5667 import apply
        return json_out(apply(desk, {k: data.get(k) for k in ('fast', 'complex', 'control')}))
    from .modeles5667 import ensure_schema
    ensure_schema(desk)
    model = str(data.get('model') or '').strip()
    if not model:
        raise Stop('modele_requis')
    job = desk.enqueue('banc5667', {'model': model, 'par': str(owner)[:80]}, origin='user')
    return json_out({'ok': True, 'job': job, 'message': 'Banc d’essai de %s lancé en arrière-plan : le résultat s’affichera ici.' % model})
