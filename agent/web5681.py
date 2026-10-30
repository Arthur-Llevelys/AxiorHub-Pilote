"""5.6.81 : ``POST /api440/m5681/modeles`` (liste des modèles d'un fournisseur) et ``POST /api440/m5681/profil-mixte``. Réglages du cabinet :
administrateur seulement (registre ``standalone_auth.ADMIN_API``, revérifié ici)."""
from .common import Stop


def api(env, desk, auth, prefix, name, args, method):
    from .web440 import json_out
    from .web567 import actor
    from .web5634 import _post
    from . import modeles5681
    _, role = actor(env)
    route = name[len('m5681/'):].split('?', 1)[0]
    if route not in ('modeles', 'profil-mixte'):
        raise Stop('operation_absente')
    if method != 'POST':
        raise Stop('methode_refusee')
    if role != 'administrateur':
        raise Stop('role_insuffisant')
    data = _post(env, auth, 10_000)
    if route == 'modeles':
        return json_out(modeles5681.list_models(desk, data.get('provider'), data.get('actualiser') is True))
    consent = data.get('consent') is True or str(data.get('consent') or '').lower() in ('on', 'true', '1', 'oui')
    return json_out(modeles5681.apply_mixte(desk, data.get('api_key', ''), consent))
