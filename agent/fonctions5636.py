"""5.6.36 : point d'entrée des fonctions nouvelles (contacts, initiatives, GED, outils IA, corporate, Microsoft 365, Invoice Ninja, Word).

Chaque fonction a son module ; ce répartiteur renvoie None pour une route qu'il ne connaît pas.
"""
from .common import Stop


def _lawyer(role):
    if role not in ('administrateur', 'avocat'):
        raise Stop('role_insuffisant')


def get(desk, route, args, owner, role):
    if route.startswith('contacts'):
        from . import crm5636
        return crm5636.api_get(desk, route, args)
    if route.startswith('pieces'):
        from . import ged5636
        return ged5636.api_get(desk, route, args)
    if route.startswith('initiatives'):
        from . import initiatives5636
        return initiatives5636.api_get(desk, route, args)
    if route.startswith('relances'):
        from . import relances5636
        return relances5636.api_get(desk, route, args)
    if route.startswith('outils/'):
        from . import outils5636
        return outils5636.api_get(desk, route, args)
    if route.startswith('corporate/bibliotheque'):   # 5.6.39 : bibliothèque de modèles
        from . import bibliotheque5639
        return bibliotheque5639.api_get(desk, route, args, owner)
    if route.startswith('corporate/'):
        from . import corporate5636
        return corporate5636.api_get(desk, route, args)
    if route == 'nextcloud/dossiers':
        _lawyer(role)
        from . import nextcloud5636
        return nextcloud5636.folders(desk, args.get('path', '/'), '.docx' if args.get('fichiers') == 'docx' else '')
    return None


def post(desk, route, data, owner, role, prefix):
    if route.startswith('contacts/'):
        if role not in ('administrateur', 'avocat', 'assistant'):
            raise Stop('role_insuffisant')
        from . import crm5636
        return crm5636.api_post(desk, route, data, owner)
    if route.startswith('corporate/bibliotheque/'):
        # 5.6.39 : l'assistant prépare des projets ; seuls l'avocat et l'administrateur modifient la bibliothèque du cabinet
        if role not in ('administrateur', 'avocat', 'assistant') or (role == 'assistant' and route != 'corporate/bibliotheque/generer'):
            raise Stop('role_insuffisant')
        from . import bibliotheque5639
        return bibliotheque5639.api_post(desk, route, data, owner)
    if route.startswith('corporate/'):
        if role not in ('administrateur', 'avocat', 'assistant') or (role == 'assistant' and route not in (
                'corporate/operation', 'corporate/societe', 'corporate/souscription')):
            raise Stop('role_insuffisant')
        from . import corporate5636
        return corporate5636.api_post(desk, route, data, owner)
    if route.startswith('outils/'):
        if role not in ('administrateur', 'avocat', 'assistant') or (role == 'assistant' and route != 'outils/memo/rediger'):
            raise Stop('role_insuffisant')
        from . import outils5636
        return outils5636.api_post(desk, route, data, owner, role)
    if route.startswith('relances/'):
        _lawyer(role)
        from . import relances5636
        return relances5636.api_post(desk, route, data, owner)
    if route.startswith('initiatives/'):
        _lawyer(role)
        from . import initiatives5636
        return initiatives5636.api_post(desk, route, data, owner)
    if route.startswith('pieces/'):
        if role not in ('administrateur', 'avocat', 'assistant') or (route == 'pieces/pj-auto' and role == 'assistant'):
            raise Stop('role_insuffisant')
        from . import ged5636
        return ged5636.api_post(desk, route, data, owner)
    if route == 'nextcloud/app':   # autoriser la liaison des comptes Nextcloud (administrateur)
        if role != 'administrateur':
            raise Stop('role_insuffisant')
        from . import integration5636
        return integration5636.set_enabled(desk, str(data.get('enabled') or '') in ('1', 'true', 'on', 'True'), owner)
    if route == 'nextcloud/cle/revoquer':
        if role != 'administrateur':
            raise Stop('role_insuffisant')
        from . import integration5636
        return integration5636.revoke(desk, str(data.get('key') or ''), owner)
    return None
