"""5.6.82 : ``/api440/m5682/*`` (mode de l'IA, catalogue, liaison poste-serveur ; administrateur seulement, registre ``standalone_auth.ADMIN_API``,
revérifié ici) et ``/api440/q5682/recherche`` (recherche rapide, droits de la personne connectée)."""
from .common import Stop

MODE_FIELDS = ('consentement', 'niveau_maximal', 'credits_autorises', 'semantique_locale')


def _flag(value):
    return value is True or str(value or '').lower() in ('on', 'true', '1', 'oui')


def _amount(value):
    return str(value or '').replace(' ', '').replace(',', '.')


def _policy(desk, owner, data):
    from . import politique5682
    value = {'mode': data.get('mode'), 'preference': data.get('preference') or 'auto'}
    for name in MODE_FIELDS:
        value[name] = _flag(data.get(name))
    value['fournisseurs'] = [k[len('fournisseur_'):] for k, v in data.items() if k.startswith('fournisseur_') and _flag(v)]
    value['api_key'] = str(data.get('cle_' + str(data.get('mode'))) or '')
    for name in ('plafond_mission_credits', 'plafond_mission_usd', 'plafond_auto_jour', 'plafond_auto_avance_jour'):
        if data.get(name) not in (None, ''):
            value[name] = _amount(data.get(name))
    return politique5682.save(desk, value, owner)


def api(env, desk, auth, prefix, name, args, method):
    from .web440 import json_out
    from .web567 import actor
    from .web5634 import _post
    owner, role = actor(env)
    if name.startswith('q5682/'):
        from . import recherche5682
        if name.split('?', 1)[0] != 'q5682/recherche' or method != 'GET':
            raise Stop('operation_absente')
        return json_out(recherche5682.search(desk, args.get('q', ''), owner, role))
    route = name[len('m5682/'):].split('?', 1)[0]
    if route not in ('politique', 'catalogue', 'session/export', 'session/import'):
        raise Stop('operation_absente')
    if method != 'POST':
        raise Stop('methode_refusee')
    if role != 'administrateur':
        raise Stop('role_insuffisant')
    data = _post(env, auth, 80_000)
    if route == 'politique':
        return json_out(_policy(desk, owner, data))
    if route == 'catalogue':
        from . import catalogue5682
        rows = catalogue5682.refresh(desk, str(data.get('provider') or ''))
        return json_out({'ok': True, 'message': '%d modèle(s) lu(s).' % len([r for r in rows if r['statut'] == 'actif'])})
    from . import chatgpt5682
    if route == 'session/export':
        return json_out(chatgpt5682.export_session(desk, owner, data.get('phrase')))
    return json_out(chatgpt5682.import_session(desk, owner, data.get('paquet'), data.get('phrase')))
