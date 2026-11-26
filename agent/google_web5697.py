"""5.6.97 (lot gmail) : retour OAuth Google commun, ``/api440/m5689/oauth/google/callback`` (GET, session authentifiée).

L'URI déclarée chez Google est celle de la configuration (``google5697.redirect_uri``) ; elle se termine par ``/oauth/google/callback``.
Le retour vérifie l'état (usage unique, 10 minutes), la personne et la session AVANT l'échange du code, puis redirige vers la page de
l'usage (Gmail ou agenda) sans code ni état dans l'URL finale, avec ``Referrer-Policy: no-referrer``. Un refus ou une erreur ne crée
aucune connexion active ; le motif est dit en clair sur la page d'arrivée.

5.6.110 (lot G) : sur un poste, le retour de Google arrive sans la session de la fenêtre (cookie ``SameSite=Strict`` sous Windows,
navigateur du système sous Ubuntu). ``retour_poste`` (appelé par ``web.py`` avant l'authentification, seulement sur un poste, depuis
l'adresse de bouclage, pour les deux chemins de retour Google) dépose le code sans rien échanger et affiche une page sans donnée ;
``oauth/google/terminer`` (GET authentifié) termine ensuite depuis la fenêtre AxiorHub, avec tous les contrôles ; ``oauth/google/attente``
(lecture seule) permet à la fenêtre d'Ubuntu d'attendre ce dépôt. ``encart_poste`` : adresse de retour du poste, bouton « Copier », marche
à suivre chez Google.
"""
from html import escape as e
from urllib.parse import parse_qs, urlencode
import uuid

from .common import Stop
from . import google5697 as gg

ROUTES = ('oauth/google/callback', 'oauth/google/terminer', 'oauth/google/attente')
CHEMINS_POSTE = ('/api440/m5689/oauth/google/callback', '/api440/m568/google/callback')
BOUCLAGE = ('127.0.0.1', '::1', '::ffff:127.0.0.1')


def allowed(role, route):
    return role in ('administrateur', 'avocat', 'assistant') and route.split('?', 1)[0] in ROUTES


def _vers(prefix, retour, resultat):
    chemin, _, ancre = retour.partition('#')
    cible = prefix + chemin + ('&' if '?' in chemin else '?') + urlencode({'google': resultat}) + ('#' + ancre if ancre else '')
    return {'status': '303 See Other', 'kind': 'text/plain; charset=utf-8', 'body': 'Retour à AxiorHub.',
            'headers': [('Location', cible), ('Referrer-Policy', 'no-referrer'), ('Cache-Control', 'no-store')]}


def api(env, desk, auth, prefix, name, args, method):
    from .web567 import actor
    owner, _role = actor(env)
    route = name[len('m5689/'):].split('?', 1)[0]
    if method != 'GET' or route not in ROUTES:
        from .web440 import json_out
        return json_out({'error': 'route_inconnue'}, '404 Not Found')
    if route == 'oauth/google/attente':
        from .web440 import json_out
        return json_out(gg.attente_depot(desk, owner, gg.session_de(env, auth)))
    retour = '/parametres?rubrique=connexions'
    try:
        if route == 'oauth/google/terminer':
            out = gg.terminer_depose(desk, owner, gg.session_de(env, auth))
        else:
            out = gg.terminer(desk, owner, gg.session_de(env, auth), args)
        retour, resultat = out['retour'] or retour, 'connecte'
    except Stop as error:
        resultat = str(error).split(':')[0]
        try:
            desk.audit('google5697_retour_refuse', {'motif': resultat[:60]})
        except Exception:   # noqa: BLE001 — journal indisponible : le refus reste dit à la personne
            pass
    return _vers(prefix, retour, resultat)


# ------------------------------------------------------------------------------------------------ 5.6.110 (lot G) : poste
PAGE = ('<!doctype html><html lang="fr"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">'
        '<meta name="referrer" content="no-referrer"><title>AxiorHub Pilote</title>'
        '<body style="font-family:system-ui,sans-serif;margin:3rem auto;max-width:40rem;padding:0 1rem;line-height:1.5">'
        '<h1>%s</h1>%s</body></html>')


def retour_poste(desk, env, prefix, chemin):
    """Retour de Google sur l'adresse de bouclage du poste, sans session (``web.py`` vérifie avant : poste, GET, requête non authentifiée).
    Dépose le code (aucun échange, aucune activation) et rend une page sans donnée ni paramètre OAuth, qui renvoie vers la fenêtre AxiorHub."""
    entetes = [('Referrer-Policy', 'no-referrer'), ('Cache-Control', 'no-store')]
    csp = "default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
    relais = any(env.get(h) for h in ('HTTP_X_FORWARDED_FOR', 'HTTP_X_REAL_IP', 'HTTP_FORWARDED'))
    if relais or str(env.get('REMOTE_ADDR') or '') not in BOUCLAGE or chemin not in CHEMINS_POSTE or gg.poste_local(desk.c) is None:
        return {'status': '403 Forbidden', 'kind': 'text/html; charset=utf-8', 'csp': csp, 'headers': entetes,
                'body': PAGE % ('Connexion non aboutie', '<p>%s</p>' % e(gg.MESSAGES['google_retour_poste_seulement']))}
    args = {k: v[0] for k, v in parse_qs(str(env.get('QUERY_STRING') or ''), max_num_fields=20).items() if v}
    calendrier = chemin == CHEMINS_POSTE[1]
    try:
        if calendrier:
            from . import calendar568
            calendar568.oauth_deposit(desk, args)
            suite = prefix + '/api440/m568/google/terminer'
        else:
            gg.deposer_retour(desk, args)
            suite = prefix + '/api440/m5689/oauth/google/terminer'
    except Stop:
        try:
            desk.audit('google5697_retour_poste_refuse', {'chemin': 'agenda568' if calendrier else 'google5697'})
        except Exception:   # noqa: BLE001
            pass
        corps = ('<p>Accès refusé chez Google : rien n’est connecté.</p>' if args.get('error') and calendrier else
                 '<p>Google a répondu, mais cette réponse ne correspond à aucune demande en cours (déjà utilisée, expirée ou inconnue). '
                 'Rien n’a été enregistré.</p>') + '<p>Revenez à la fenêtre AxiorHub et recommencez la connexion.</p>'
        return {'status': '400 Bad Request', 'kind': 'text/html; charset=utf-8', 'csp': csp, 'headers': entetes,
                'body': PAGE % ('Connexion non aboutie', corps)}
    corps = ('<p>Google a répondu. La connexion se termine dans la fenêtre AxiorHub, avec votre session : rien n’est encore activé.</p>'
             '<p><a href="%s" style="display:inline-block;padding:.6rem 1rem;border-radius:.4rem;background:#1f4e79;color:#fff;text-decoration:none">'
             'Terminer la connexion</a></p>'
             '<p>Si cette page s’est ouverte dans votre navigateur habituel et non dans la fenêtre AxiorHub : fermez-la et revenez à la fenêtre '
             'AxiorHub ; la connexion s’y termine seule en quelques secondes.</p>'
             '<p>Sans suite dans les dix minutes, la demande expire : recommencez depuis AxiorHub.</p>') % e(suite, quote=True)
    return {'status': '200 OK', 'kind': 'text/html; charset=utf-8', 'csp': csp, 'headers': entetes, 'body': PAGE % ('Réponse de Google reçue', corps)}


def encart_poste(adresse, prefix, attente, terminer, declencheur='form[data-cx-oauth] button[type=submit]'):
    """Bloc affiché sur un poste dans les cartes Google : adresse de retour calculée (bouton « Copier »), marche à suivre chez Google en trois
    lignes, et zone d'attente du retour (``v56110-google-poste.js``). ``attente`` et ``terminer`` : routes de l'interface, jamais d'ailleurs ;
    ``declencheur`` : sélecteur du bouton qui part chez Google (l'attente ne commence qu'après lui)."""
    ident = 'g110-adresse-' + uuid.uuid4().hex[:8]
    etapes = ('Chez Google (console Google Cloud, rubrique Clients) : créez un client OAuth de type « Application de bureau » ; il n’y a rien à déclarer.',
              'Ou, avec un client de type « Application Web » : ajoutez exactement l’adresse ci-dessus aux « URI de redirection autorisés ».',
              'Collez ensuite l’identifiant et le secret du client dans AxiorHub.')
    return ('<div class="g110-poste" data-g110-attente="%s" data-g110-terminer="%s" data-g110-declencheur="%s">'
            '<p>Adresse de retour de ce poste, calculée par AxiorHub :</p>'
            '<p><code id="%s">%s</code> <button type="button" class="ax-btn ghost" data-g110-copier="%s">Copier</button></p>'
            '<ol>%s</ol>'
            '<p class="ax-muted">La page Google peut s’ouvrir dans votre navigateur habituel. Après votre accord chez Google, revenez à cette fenêtre : '
            'la connexion s’y termine.</p><p class="g110-statut" role="status" aria-live="polite"></p></div>'
            '<script defer src="%s"></script>') % (
        e(prefix + attente, quote=True), e(prefix + terminer, quote=True), e(declencheur, quote=True), ident, e(adresse), ident,
        ''.join('<li>%s</li>' % e(x) for x in etapes), e(prefix + '/static/v56110-google-poste.js', quote=True))
