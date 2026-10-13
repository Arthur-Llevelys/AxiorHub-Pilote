"""5.6.36 : routes /api440/m5636/… (points à vérifier, demandes écartées, dossiers de la surveillance, missions)."""
from .common import Stop
from .web5634 import _post


# Droits en écriture des routes m5636 (mode autonome : même règle que le serveur, testée).
ADMIN_ONLY = ('nextcloud/app', 'nextcloud/cle/revoquer', 'microsoft/', 'compte/')   # 5.6.42 : compte de l'interface
ASSISTANT_ROUTES = ('contacts/', 'pieces/analyser', 'pieces/ranger-pj', 'corporate/operation', 'corporate/societe', 'corporate/souscription',
                    'outils/memo/rediger', 'travail/ecarter', 'office/reformuler')


def allowed(role, route):
    if role == 'administrateur':
        return True
    if route.startswith(ADMIN_ONLY):
        return False
    if role == 'avocat':
        return True
    return role == 'assistant' and route.startswith(ASSISTANT_ROUTES)


def api(env, desk, auth, prefix, name, args, method):
    from .web440 import json_out
    from .web567 import actor
    owner, role = actor(env)
    route = name[len('m5636/'):].split('?', 1)[0]
    if method == 'GET':
        if route == 'points':
            from . import sante5636
            return json_out({'points': sante5636.checks(desk)})
        if route == 'signalement':
            from . import signalement5636
            return json_out(signalement5636.report(desk))
        if route.startswith('office/'):   # 5.6.36 : complément Word (le manifeste porte l'adresse de ce serveur)
            from . import office5636
            out = office5636.api_get(desk, route, args, env, prefix)
            if out is not None:
                return json_out(out)
        from . import fonctions5636
        out = fonctions5636.get(desk, route, args, owner, role)
        if out is not None:
            return json_out(out)
        raise Stop('route_inconnue')
    if method != 'POST':
        raise Stop('methode_refusee')
    data = _post(env, auth, 12_000_000 if route.startswith(('documents/', 'corporate/import', 'grilles/import', 'office/')) else 400_000)
    if route == 'compte/mot-de-passe':   # 5.6.42 : identifiant et mot de passe de l'interface (administrateur)
        if role != 'administrateur':
            raise Stop('role_insuffisant')
        from . import comptes5642
        return json_out(comptes5642.change(desk, auth, data, owner))
    from . import sante5636
    if route == 'erreurs/ecarter':
        return json_out(sante5636.dismiss_errors(desk, owner))
    if route == 'travail/ecarter':
        return json_out(sante5636.dismiss_job(desk, data.get('job'), owner))
    if route in ('dossier/reessayer', 'dossier/ignorer', 'dossier/retablir'):
        if role not in ('administrateur', 'avocat'):
            raise Stop('role_insuffisant')
        from .watch430 import retry_folder
        return json_out(retry_folder(desk, data.get('matter'), ignore=route == 'dossier/ignorer', restore=route == 'dossier/retablir'))
    if route in ('mission/annuler', 'mission/retirer'):
        from . import missions567
        return json_out(missions567.control(desk, {'id': data.get('id'), 'action': 'cancel' if route == 'mission/annuler' else 'hide'}, owner,
                                           role == 'administrateur'))
    if route.startswith('office/'):
        from . import office5636
        out = office5636.api_post(desk, route, data, owner)
        if out is not None:
            return json_out(out)
    if route.startswith('microsoft/'):   # 5.6.36 : agenda Microsoft 365 (connexion OAuth : adresse de retour calculée depuis la requête)
        if role not in ('administrateur', 'avocat'):
            raise Stop('role_insuffisant')
        from . import microsoft5636
        out = microsoft5636.api_post(desk, route, data, owner, env, prefix)
        if out is not None:
            return json_out(out)
    from . import fonctions5636
    out = fonctions5636.post(desk, route, data, owner, role, prefix)
    if out is not None:
        return json_out(out)
    raise Stop('route_inconnue')
