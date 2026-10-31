"""5.6.83 : ``/api440/m5683/*`` : cessions de parts et actions, formulaires officiels, règles datées, liaisons d'outils.

Les transitions se décident au serveur : le rôle et les contrôles sont revérifiés ici, jamais déduits de l'écran."""
from .common import Stop

ASSISTANT_ROUTES = ('ouvrir', 'fiche', 'donnee', 'excel', 'preparer', 'justificatif', 'formalite', 'formulaire/telecharger', 'formulaire/generer')
LAWYER_ROUTES = ('realiser', 'rectifier', 'signe', 'abandonner', 'regle', 'formulaire/confirmer', 'liaisons/actualiser')


def allowed(role, route):
    if role in ('administrateur', 'avocat'):
        return route in ASSISTANT_ROUTES + LAWYER_ROUTES
    return role == 'assistant' and route in ASSISTANT_ROUTES


def api(env, desk, auth, prefix, name, args, method):
    from .web440 import json_out
    from .web567 import actor
    from .web5634 import _post
    from . import formulaires5683, operations5683, regles5683
    owner, role = actor(env)
    route = name[len('m5683/'):].split('?', 1)[0]
    if method == 'GET':
        if route == 'regles':
            return json_out({'regles': regles5683.catalogue(desk)})
        if route == 'liaisons':
            from . import liaisons5683
            return json_out({'capacites': liaisons5683.overview(desk)})
        raise Stop('operation_absente')
    if method != 'POST':
        raise Stop('methode_refusee')
    if not allowed(role, route):
        raise Stop('role_insuffisant')
    data = _post(env, auth, 200_000)
    if route == 'regle':
        return json_out(regles5683.validate(desk, str(data.get('regle') or ''), owner, data.get('note')))
    if route == 'liaisons/actualiser':
        from . import liaisons5683
        return json_out({'capacites': liaisons5683.refresh(desk, str(data.get('extension') or '')), 'message': 'Liaisons actualisées.'})
    if route == 'formulaire/telecharger':
        out = formulaires5683.fetch(desk, str(data.get('formulaire') or ''))
        return json_out({**out, 'message': 'Formulaire téléchargé : %s.' % formulaires5683.STATES[out['etat']]})
    if route == 'formulaire/confirmer':
        return json_out(formulaires5683.confirm_version(desk, str(data.get('formulaire') or ''), owner))
    if route == 'formulaire/generer':
        return json_out(formulaires5683.generate(desk, data, owner))
    return json_out(operations5683.api_post(desk, route, data, owner, role))


def page(desk, auth, prefix, env, shell):
    from .web567 import actor
    from . import formulaires5683
    _, role = actor(env)
    return shell('Formulaires officiels', formulaires5683.page_html(desk, prefix, {}, role), prefix, auth['csrf'], '/formulaires')
