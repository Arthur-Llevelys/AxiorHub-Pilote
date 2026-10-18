"""5.6.62 : routes ``/api440/m5662/chatgpt/…`` — connexion ChatGPT avec l'abonnement (administrateur seulement)."""
import ipaddress

from .common import Stop

WRITE = ('chatgpt/connecter', 'chatgpt/terminer', 'chatgpt/fournisseur', 'chatgpt/modeles', 'chatgpt/deconnecter', 'chatgpt/bienvenue')


def allowed(role, route):
    return role == 'administrateur' and route.split('?', 1)[0] in WRITE


def local_browser(env):
    """Vrai si le navigateur est sur la machine d'AxiorHub (poste) : le retour d'OpenAI sur 127.0.0.1 arrive alors à AxiorHub lui-même.
    Derrière un serveur web (en-têtes de relais), le navigateur est ailleurs : l'avocat recopie l'adresse de retour."""
    try:
        from .maj5661 import platform
        if platform() in ('windows', 'poste'):
            return True
    except Exception:   # noqa: BLE001
        pass
    if any(env.get(h) for h in ('HTTP_X_FORWARDED_FOR', 'HTTP_X_REAL_IP', 'HTTP_FORWARDED')):
        return False
    try:
        return ipaddress.ip_address(str(env.get('REMOTE_ADDR') or '')).is_loopback
    except ValueError:
        return False


def api(env, desk, auth, prefix, name, args, method):
    from .web440 import json_out
    from .web567 import actor
    from .web5634 import _post
    owner, role = actor(env)
    route = name[len('m5662/'):].split('?', 1)[0]
    if not allowed(role, route):
        raise Stop('role_insuffisant')
    if method != 'POST':
        raise Stop('methode_refusee')
    data = _post(env, auth, 6_000)
    from . import chatgpt5662 as cg
    if route == 'chatgpt/connecter':
        local = local_browser(env)
        port = cg.listen(desk.c) if local else cg.DEFAULT_PORT
        url = cg.start(desk, owner, port)
        return json_out({'ok': True, 'url': url, 'mode': 'poste' if local else 'serveur', 'message':
                         'Page d’OpenAI ouverte dans un nouvel onglet : connectez-vous puis acceptez. ' +
                         ('AxiorHub reçoit la réponse tout seul ; rechargez ensuite cette page.' if local else
                          'Puis copiez l’adresse affichée (http://127.0.0.1:1455/auth/callback?…) dans « Après la connexion » ci-dessous.')})
    if route == 'chatgpt/terminer':
        out = cg.complete(desk, str(data.get('adresse') or ''))
        return json_out({'ok': True, 'message': 'ChatGPT connecté (%s).%s' % (out['email'] or 'compte ChatGPT', '' if out['plan'] else
                                                                             ' L’utilisation de l’abonnement n’a pas été autorisée.')})
    if route == 'chatgpt/fournisseur':
        on = lambda k: str(data.get(k) or '') in ('on', '1', 'true')
        return json_out(cg.save_provider(desk, owner, data.get('model'), on('actif'), on('externe')))
    if route == 'chatgpt/modeles':
        models = cg.refresh_models(desk)
        return json_out({'ok': True, 'message': '%d modèle(s) disponible(s) pour ce compte.' % len(models)})
    if route == 'chatgpt/deconnecter':
        return json_out(cg.disconnect(desk, owner))
    if route == 'chatgpt/bienvenue':
        desk.setting(cg.ACCOUNT_KEY, {**cg.account(desk), 'bienvenue_vue': True})
        return json_out({'ok': True})
    raise Stop('route_inconnue')
