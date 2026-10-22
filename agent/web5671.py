"""5.6.71 : routes ``/api440/m5671/...`` (écritures POST seulement).

- ``reponse/deposer`` : dépôt dans Brouillons d'une réponse préparée en attente de décision — texte relu et éventuellement modifié par
  l'avocat (``corps``), ou tel quel « à compléter » (``a_completer=oui``) si le contrôle de sûreté l'autorise (droit « déposer ») ;
- ``reponse/ecarter`` : écarter une réponse préparée (droit « déposer ») ;
- ``reglage/sans-dossier`` : réponses « sans dossier » automatiques activées ou non (administrateur).
"""
from .common import Stop

WORK = ('reponse/deposer', 'reponse/ecarter')
ADMIN = ('reglage/sans-dossier',)


def allowed(role, route):
    route = route.split('?', 1)[0]
    if route in WORK:
        return role in ('administrateur', 'avocat', 'assistant')
    return role == 'administrateur' and route in ADMIN


def api(env, desk, auth, prefix, name, args, method):
    from .web440 import json_out
    from .web567 import actor
    owner, role = actor(env)
    route = name[len('m5671/'):].split('?', 1)[0]
    if route not in WORK + ADMIN:
        raise Stop('route_inconnue')
    if not allowed(role, route):
        raise Stop('role_insuffisant')
    if method != 'POST':
        raise Stop('methode_refusee')
    from .web5634 import _post
    data = _post(env, auth, 60_000)
    from . import reponses5671 as rep
    key = str(data.get('key') or '')
    if route == 'reponse/deposer':
        corps = data.get('corps')
        return json_out(rep.deposer(desk, owner, key, body=None if corps is None else str(corps),
                                    a_completer=str(data.get('a_completer') or '') == 'oui'))
    if route == 'reponse/ecarter':
        return json_out(rep.ecarter(desk, owner, key))
    on = str(data.get('actif') or '') in ('1', 'oui', 'true', 'on') or data.get('actif') is True
    desk.setting(rep.SETTING, on)
    desk.audit('reponses5671_reglage', {'actif': on, 'par': str(owner)[:80]})
    return json_out({'ok': True, 'message': 'Réponses « sans dossier » automatiques %s.' % ('activées' if on else 'désactivées')})


def settings_html(desk):
    """Paramètres › Messagerie : réglage des réponses « sans dossier » automatiques."""
    from html import escape as e
    from . import reponses5671 as rep
    on = bool(desk.settings(rep.SETTING, True))
    return ('<section class="ax-card" id="reponses5671"><h2>Réponses « sans dossier »</h2>'
            '<p class="ax-muted">Pour un courriel qui appelle une réponse mais dont le dossier n’est pas confirmé (expéditeur inconnu, prospect, '
            'correspondant à confirmer), l’agent prépare une réponse à partir du seul courriel et de ses pièces jointes, marquée « [À COMPLÉTER] » '
            'et déposée dans Brouillons si le contrôle de sûreté l’autorise. Jamais envoyée ; jamais un dossier deviné. État : <strong>%s</strong>.</p>'
            '<form class="r5671-form" data-c5671="m5671/reglage/sans-dossier" data-reload><input type="hidden" name="actif" value="%s">'
            '<button class="ax-btn%s" type="submit">%s</button></form></section>') % (
        'activées' if on else 'désactivées', '0' if on else '1', ' ghost' if on else '', e('Désactiver' if on else 'Activer'))
