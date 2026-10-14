"""5.6.43 : routes /api440/m5643/… — conservation des courriels et des pièces (cahier v9, § 12-13).

L'acteur et le compte viennent de la session ; le navigateur ne fournit jamais un chemin de destination (résolu sur le serveur depuis
l'identifiant du dossier). Les autorisations générales sont réservées à l'avocat et à l'administrateur (portée cabinet : administrateur).
"""
from urllib.parse import quote

from .common import Stop
from .web5634 import _post

ASSISTANT_ROUTES = ('correspondances/preparer', 'correspondances/confirmer', 'correspondances/message-regle', 'correspondances/modifier',
                    'correspondances/annoter', 'correspondances/confier')


def allowed(role, route):
    if role == 'administrateur':
        return True
    if role == 'avocat':
        return route.startswith('correspondances/')
    return role == 'assistant' and route.startswith(ASSISTANT_ROUTES)


def api(env, desk, auth, prefix, name, args, method):
    from .web440 import json_out
    from .web567 import actor
    from . import correspondances5643 as c
    owner, role = actor(env)
    route = name[len('m5643/'):].split('?', 1)[0]
    if method == 'GET':
        if route == 'correspondances/message':
            return json_out(c.message_state(desk, args.get('uid', ''), args.get('folder', '')))
        if route == 'correspondances/politique':
            return json_out({'account': c.get_policy(desk, 'account'), 'cabinet': c.get_policy(desk, 'cabinet'), 'role': role})
        if route == 'correspondances/etat':
            return json_out(c.status(desk, args.get('lien', '')))
        if route == 'correspondances/liste':
            return json_out({'items': c.listing(desk, str(args.get('dossier') or ''), str(args.get('q') or ''))})
        if route == 'correspondances/detail':
            return json_out(c.detail(desk, args.get('lien', '')))
        if route == 'correspondances/original':
            data = c.original(desk, args.get('lien', ''))
            st = c.status(desk, args.get('lien', ''))
            name = c.safe_name(st['title'] or 'courriel', 60) + '.eml'
            return {'status': '200 OK', 'kind': 'message/rfc822', 'body': data,
                    'headers': [('Content-Disposition', "attachment; filename*=UTF-8''" + quote(name)), ('X-Content-Type-Options', 'nosniff')]}
        raise Stop('route_inconnue')
    if method != 'POST':
        raise Stop('methode_refusee')
    data = _post(env, auth, 400_000)
    if route == 'correspondances/preparer':
        return json_out(c.prepare(desk, owner, data))
    if route == 'correspondances/confirmer':
        return json_out(c.confirm(desk, owner, data), '202 Accepted')
    if route == 'correspondances/politique':
        return json_out(c.set_policy(desk, owner, role, data))
    if route == 'correspondances/confier':
        return json_out(c.entrust(desk, owner, data), '202 Accepted')
    if route == 'correspondances/message-regle':
        return json_out(c.set_message_rule(desk, owner, data))
    if route == 'correspondances/modifier':
        return json_out(c.update(desk, owner, data))
    if route == 'correspondances/annoter':
        return json_out(c.annotate(desk, owner, data))
    if route == 'correspondances/arreter':
        link = c.status(desk, data.get('lien', ''))
        jobs = [j for j in (desk.db.execute('SELECT job_id,attachments_job_id FROM mail_archive_links WHERE id=?', (link['link_id'],)).fetchone() or ()) if j]
        out = []
        for j in jobs:
            try:
                out.append(desk.cancel_job(j))
            except Stop:
                pass
        return json_out({'link_id': link['link_id'], 'jobs': out, 'message': 'Arrêt demandé ; les fichiers déjà vérifiés restent enregistrés.'})
    raise Stop('route_inconnue')
