"""5.6.68 : routes ``/api440/m5668/...``.

- ``budget/liberer`` : libérer les réservations incertaines d'un fournisseur d'IA externe (administrateur) ;
- ``proactivite/exclure`` : ne plus proposer de mission automatique pour un dossier (avocat, administrateur).
"""
from .common import Stop

WORK = ('proactivite/exclure',)
ADMIN = ('budget/liberer',)


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
    route = name[len('m5668/'):].split('?', 1)[0]
    if not allowed(role, route):
        raise Stop('role_insuffisant')
    if method != 'POST':
        raise Stop('methode_refusee')
    data = _post(env, auth, 2_000)
    if route == 'budget/liberer':
        from .budget5668 import release
        return json_out(release(desk, str(data.get('provider') or ''), owner))
    from .bruit5668 import exclude_matter
    return json_out(exclude_matter(desk, str(data.get('matter') or ''), owner))
