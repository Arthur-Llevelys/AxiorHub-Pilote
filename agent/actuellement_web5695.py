"""5.6.95 (lot aujourdhui, A02) : routes ``/api440/m5689/actuellement/*`` de l'encadré « Actuellement ».

- ``actuellement/etat`` (GET, tous les rôles) : contenu rafraîchi de l'encadré. Réponse conditionnelle : l'empreinte du contenu est rendue
  dans l'en-tête ``ETag`` ; si le navigateur renvoie la même dans ``If-None-Match``, la réponse est ``304`` sans corps (rien n'a changé).
  Le calcul ne fait aucun appel à l'IA et n'écrit rien.
- ``actuellement/auto`` (POST, tous les rôles, CSRF) : arrêter ou reprendre la mise à jour automatique, choix mémorisé pour la personne
  (son seul effet ; il est idempotent : renvoyer la même valeur ne change rien).
- ``actuellement/plis`` (POST, tous les rôles, CSRF) : bloc du bas d'Aujourd'hui replié ou ouvert, mémorisé pour la personne (A03), idempotent.

Le compte, le rôle et les dossiers réservés viennent de la requête authentifiée, jamais du corps.
"""
from .common import Stop
from . import actuellement5695 as act

LECTURE = ('actuellement/etat',)
ECRITURE = ('actuellement/auto', 'actuellement/plis')


def allowed(role, route):
    route = route.split('?', 1)[0]
    return role in ('administrateur', 'avocat', 'assistant') and route in LECTURE + ECRITURE


def _hidden(desk, owner, role):
    from .membres5650 import hidden_for, viewer_hidden
    return frozenset(viewer_hidden() or ()) | hidden_for(desk.c.get('state_dir'), owner, role)


def _correspond(entete, etag):
    """``If-None-Match`` : liste d'empreintes, faibles (W/) ou fortes, ou « * »."""
    valeurs = [v.strip() for v in str(entete or '').split(',') if v.strip()]
    return '*' in valeurs or any(v.replace('W/', '', 1).strip('"') == etag for v in valeurs)


def api(env, desk, auth, prefix, name, args, method):
    from .web440 import json_out
    try:
        return _route(env, desk, auth, prefix, name, args, method)
    except Stop as error:
        code = str(error).split(':')[0]
        statut = '403 Forbidden' if code in ('role_insuffisant', 'origine_ou_csrf_refuse') else '404 Not Found' if code == 'route_inconnue' else '400 Bad Request'
        return json_out({'error': code, 'message': {'route_inconnue': 'Route inconnue.', 'role_insuffisant': 'Action réservée.'}.get(code, 'Demande refusée.')}, statut)


def _route(env, desk, auth, prefix, name, args, method):
    from .web440 import check_post, json_out
    from .web567 import actor
    owner, role = actor(env)
    route = name[len('m5689/'):].split('?', 1)[0]
    if not allowed(role, route):
        raise Stop('role_insuffisant' if route in LECTURE + ECRITURE else 'route_inconnue')
    if method == 'GET':
        if route not in LECTURE:
            raise Stop('route_inconnue')
        resume = act.calculer(desk, owner, role, _hidden(desk, owner, role), prefix)
        entetes = [('ETag', '"%s"' % resume['etag'])]
        if _correspond(env.get('HTTP_IF_NONE_MATCH'), resume['etag']):
            return {'status': '304 Not Modified', 'kind': 'application/json; charset=utf-8', 'body': '', 'headers': entetes}
        out = json_out({**act.zone_json(resume), 'auto': act.auto_actif(desk, owner), 'calcul_ms': resume['calcul_ms']})
        out['headers'] = entetes
        return out
    if method != 'POST' or route not in ECRITURE:
        raise Stop('route_inconnue')
    data = check_post(env, auth)
    if route == 'actuellement/plis':
        if not isinstance(data.get('ouvert'), bool) or str(data.get('bloc') or '') not in act.PLIABLES:
            raise Stop('valeur_invalide')
        return json_out(act.regler_pli(desk, owner, str(data['bloc']), data['ouvert']))
    valeur = data.get('auto')
    if not isinstance(valeur, bool):
        raise Stop('valeur_invalide')
    return json_out(act.regler_auto(desk, owner, valeur))
