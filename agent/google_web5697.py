"""5.6.97 (lot gmail) : retour OAuth Google commun, ``/api440/m5689/oauth/google/callback`` (GET, session authentifiée).

L'URI déclarée chez Google est celle de la configuration (``google5697.redirect_uri``) ; elle se termine par ``/oauth/google/callback``.
Le retour vérifie l'état (usage unique, 10 minutes), la personne et la session AVANT l'échange du code, puis redirige vers la page de
l'usage (Gmail ou agenda) sans code ni état dans l'URL finale, avec ``Referrer-Policy: no-referrer``. Un refus ou une erreur ne crée
aucune connexion active ; le motif est dit en clair sur la page d'arrivée.
"""
from urllib.parse import urlencode

from .common import Stop
from . import google5697 as gg

ROUTES = ('oauth/google/callback',)


def allowed(role, route):
    return role in ('administrateur', 'avocat', 'assistant') and route.split('?', 1)[0] in ROUTES


def api(env, desk, auth, prefix, name, args, method):
    from .web567 import actor
    owner, _role = actor(env)
    if method != 'GET' or name[len('m5689/'):].split('?', 1)[0] not in ROUTES:
        from .web440 import json_out
        return json_out({'error': 'route_inconnue'}, '404 Not Found')
    retour = '/parametres?rubrique=connexions'
    try:
        out = gg.terminer(desk, owner, gg.session_de(env, auth), args)
        retour, resultat = out['retour'] or retour, 'connecte'
    except Stop as error:
        resultat = str(error).split(':')[0]
        try:
            desk.audit('google5697_retour_refuse', {'motif': resultat[:60]})
        except Exception:   # noqa: BLE001 — journal indisponible : le refus reste dit à la personne
            pass
    chemin, _, ancre = retour.partition('#')
    cible = prefix + chemin + ('&' if '?' in chemin else '?') + urlencode({'google': resultat}) + ('#' + ancre if ancre else '')
    return {'status': '303 See Other', 'kind': 'text/plain; charset=utf-8', 'body': 'Retour à AxiorHub.',
            'headers': [('Location', cible), ('Referrer-Policy', 'no-referrer'), ('Cache-Control', 'no-store')]}
