"""5.6.25 : API de la boîte de réception (Courriels) et de l'ajout d'événements (Agenda et tâches).

Lecture de la boîte et réponses : avocat et administrateur. Ajout d'événement : avocat, administrateur et assistant(e).
Les écritures passent par le contrôle d'origine et le jeton CSRF communs (``check_post``)."""
from .common import Stop
from .web567 import actor


def api(env, desk, auth, prefix, name, args, method):
    from .web440 import check_post, json_out
    from . import agenda5625, boite5625
    owner, role = actor(env)
    route = name[len('m5625/'):].split('?', 1)[0]
    mail_roles = ('administrateur', 'avocat')
    if method == 'GET':
        if route.startswith('boite/') and role not in mail_roles:
            raise Stop('role_insuffisant')
        if route == 'boite/liste':
            return json_out(boite5625.listing(desk, args.get('page') or 1, args.get('q') or ''))
        if route == 'boite/courriel':
            return json_out(boite5625.message(desk, args.get('uid')))
        if route == 'boite/piece':
            return json_out(boite5625.attachment_text(desk, args.get('uid'), args.get('index')))
        if route == 'agenda/cibles':
            return json_out({'targets': agenda5625.targets(desk, owner)})
        raise Stop('route_inconnue')
    if method != 'POST':
        raise Stop('methode_refusee')
    data = check_post(env, auth)
    if route == 'agenda/creer':
        if role not in ('administrateur', 'avocat', 'assistant'):
            raise Stop('role_insuffisant')
        return json_out(agenda5625.create(desk, owner, data))
    if role not in mail_roles:
        raise Stop('role_insuffisant')
    if route == 'boite/repondre':
        uid = data.get('uid')
        if not uid and data.get('key'):   # relance depuis un brouillon : courriel d'origine retrouvé par son rapport
            from .desk import report_for
            report = report_for(desk.c, str(data['key']))
            uid = report.get('source_uid')
            data.setdefault('matter', report.get('matter', ''))
        return json_out(boite5625.request_reply(desk, uid, str(data.get('matter') or ''), str(data.get('instruction') or ''),
                                                data.get('pieces_jointes', True) not in (False, 'non', '0'), owner))
    if route == 'boite/supprimer':
        return json_out(boite5625.discard_reply(desk, data.get('uid'), str(data.get('uidvalidity') or '')))
    raise Stop('route_inconnue')
