"""5.6.88 : routes ``/api440/m5688/*`` : palette, fiche depuis les registres, veille des sociétés, page Aujourd'hui personnelle, note commune, mémoire.

Le compte et le rôle viennent de la requête authentifiée (``web567.actor``), jamais du corps ; les dossiers réservés sont recalculés pour ce compte."""
from .common import Stop

ADMIN = ('memoire/reglage', 'societe/reglage')


def allowed(role, route):
    """Tous les rôles ; réglage de la mémoire : administrateur (les services contrôlent ensuite le droit précis)."""
    if role not in ('administrateur', 'avocat', 'assistant'):
        return False
    return role == 'administrateur' if route.split('?', 1)[0] in ADMIN else True


def _erreur(error):
    from .erreurs5634 import explain
    from .notes5688 import NoteConflit
    from .web import REASONS
    from .web440 import json_out
    code = str(error).split(':')[0]
    body = {'error': code, 'message': REASONS.get(code) or explain(code, getattr(error, 'detail', None))}
    if isinstance(error, NoteConflit):
        return json_out({**body, 'actuelle': error.actuelle}, '409 Conflict')
    status = ('403 Forbidden' if code in ('role_insuffisant', 'dossier_reserve') else '409 Conflict' if code == 'version_en_conflit' else
              '503 Service Unavailable' if code == 'source_registre_indisponible' else '404 Not Found' if code.endswith(('_absent', '_absente')) else '400 Bad Request')
    return json_out(body, status)


def api(env, desk, auth, prefix, name, args, method):
    try:
        return _route(env, desk, auth, prefix, name, args, method)
    except Stop as error:
        return _erreur(error)


def _route(env, desk, auth, prefix, name, args, method):
    from . import crm5688, memoire5688, notes5688, palette5688, tableau5688
    from .membres5650 import directory, hidden_for
    from .web440 import check_post, json_out
    from .web567 import actor
    owner, role = actor(env)
    route = name[len('m5688/'):].split('?', 1)[0]
    hidden = hidden_for(desk.c['state_dir'], owner, role)
    if method == 'GET':
        if route == 'palette':
            return json_out(palette5688.search(desk, args.get('q', ''), owner, role))
        if route == 'mentions':
            return json_out({'personnes': notes5688.candidats(desk, directory(desk, auth), owner, str(args.get('matter') or ''), args.get('q', ''), hidden)})
        if route == 'note/version':
            try:
                numero = int(args.get('version'))
            except (TypeError, ValueError):
                raise Stop('version_absente') from None
            row = notes5688.version(desk, str(args.get('matter') or ''), numero, hidden)
            return json_out({'version': row['version'], 'texte': row['texte']})
        raise Stop('route_inconnue')
    if method != 'POST':
        raise Stop('methode_refusee')
    data = check_post(env, auth)
    if route == 'fiche/confirmer':
        choisis = {k[len('champ:'):] for k, v in data.items() if str(k).startswith('champ:') and v is True}
        remplacer = {k[len('remplacer:'):] for k, v in data.items() if str(k).startswith('remplacer:') and v is True}
        return json_out(crm5688.confirmer(desk, owner, str(data.get('contact') or ''), data.get('siren'), choisis, remplacer, data.get('jeton')))
    if route == 'veille/suivre':
        return json_out(crm5688.suivre(desk, owner, data))
    if route == 'veille/retirer':
        return json_out(crm5688.retirer(desk, data.get('id')))
    if route == 'veille/vu':
        return json_out(crm5688.vu(desk, owner, data.get('annonce')))
    if route == 'tableau/enregistrer':
        return json_out(tableau5688.enregistrer(desk, owner, data))
    if route == 'tableau/reinitialiser':
        return json_out(tableau5688.reinitialiser(desk, owner))
    if route == 'note/enregistrer':
        members = directory(desk, auth)
        name_ = (members.get(owner) or {}).get('name') or (env.get('axiorhub.user') or {}).get('name') or owner
        return json_out(notes5688.enregistrer(desk, owner, name_, str(data.get('matter') or ''), data.get('texte'), data.get('base'), members, hidden))
    if route == 'memoire/decider':
        return json_out(memoire5688.decider(desk, owner, role, data.get('id'), str(data.get('action') or ''), hidden))
    if route == 'memoire/reglage':
        if role != 'administrateur':
            raise Stop('role_insuffisant')
        return json_out(memoire5688.regler(desk, data, owner))
    if route == 'societe/reglage':
        if role != 'administrateur':
            raise Stop('role_insuffisant')
        from . import societe5688
        return json_out(societe5688.enregistrer(desk, data, owner))
    if route == 'societe/registre':
        from . import inpi5688, membres5650, registres5688
        membres5650.set_viewer(hidden)          # dossiers réservés de ce compte, recalculés ici ; ``gate`` a déjà refusé en amont
        matter = str(data.get('matter') or '')
        parti = inpi5688.demander(desk, registres5688.norm_siren(data.get('siren')), matter, repos=False)
        return json_out({'message': 'Récupération lancée : les documents publics arriveront dans « Registre ».' if parti else 'Aucune récupération lancée.'})
    raise Stop('route_inconnue')
