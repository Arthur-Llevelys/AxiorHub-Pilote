"""5.6.71 : routes ``/api440/m5671/...`` (écritures POST seulement).

- ``reponse/deposer`` : dépôt dans Brouillons d'une réponse préparée en attente de décision — texte relu et éventuellement modifié par
  l'avocat (``corps``), ou tel quel « à compléter » (``a_completer=oui``) si le contrôle de sûreté l'autorise (droit « déposer ») ;
- ``reponse/ecarter`` : écarter une réponse préparée (droit « déposer ») ;
- ``reponse/garder`` (5.6.95, D05) : garder une variante d'un choix (``choix``, ``variante``, ``version`` vue ; 409 si le texte a changé) ;
- ``reglage/sans-dossier`` : réponses « sans dossier » automatiques activées ou non (administrateur).
"""
from .common import Stop

WORK = ('reponse/deposer', 'reponse/ecarter', 'reponse/relancer', 'reponse/garder')
ADMIN = ('reglage/sans-dossier', 'reglage/presentation', 'reglage/notes')


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
    # 5.6.100 (R09) : déposer et écarter exigent la version affichée (formulaire sans version ou version remplacée : 409, aucun effet)
    revision = str(data.get('revision') or '')
    if route == 'reponse/deposer':
        corps = data.get('corps')
        return json_out(rep.deposer(desk, owner, key, body=None if corps is None else str(corps),
                                    a_completer=str(data.get('a_completer') or '') == 'oui', revision=revision))
    if route == 'reponse/ecarter':
        return json_out(rep.ecarter(desk, owner, key, revision=revision))
    if route == 'reponse/garder':
        return json_out(rep.garder_variante(desk, owner, key, data.get('choix'), str(data.get('variante') or ''), str(data.get('version') or ''),
                                            revision=None if data.get('revision') is None else revision))
    if route == 'reponse/relancer':
        return json_out(rep.relancer(desk, owner, key, str(data.get('fournisseur') or ''), str(data.get('instruction') or '')))
    if route == 'reglage/notes':
        from .notes5666 import AUTO_SETTING
        on = str(data.get('actif') or '') in ('1', 'oui', 'true', 'on')
        desk.setting(AUTO_SETTING, on)
        desk.audit('notes5687_reglage', {'actif': on, 'par': str(owner)[:80]})
        return json_out({'ok': True, 'message': 'Rangement automatique des notes %s.' % ('activé' if on else 'désactivé')})
    if route == 'reglage/presentation':
        desk.setting(rep.PRESENTATION, str(data.get('presentation') or '').strip()[:4000])
        desk.audit('reponses5687_presentation', {'par': str(owner)[:80]})
        return json_out({'ok': True, 'message': 'Présentation du cabinet enregistrée.'})
    on = str(data.get('actif') or '') in ('1', 'oui', 'true', 'on') or data.get('actif') is True
    desk.setting(rep.SETTING, on)
    desk.audit('reponses5671_reglage', {'actif': on, 'par': str(owner)[:80]})
    return json_out({'ok': True, 'message': 'Réponses « sans dossier » automatiques %s.' % ('activées' if on else 'désactivées')})


def settings_html(desk):
    """Paramètres › Messagerie : réglage des réponses « sans dossier » automatiques."""
    from html import escape as e
    from . import reponses5671 as rep
    from .notes5666 import AUTO_SETTING
    on = bool(desk.settings(rep.SETTING, True))
    notes = bool(desk.settings(AUTO_SETTING, True))
    return ('<section class="ax-card" id="reponses5671"><h2>Réponses « sans dossier »</h2>'
            '<p class="ax-muted">Pour un courriel qui appelle une réponse mais dont le dossier n’est pas confirmé (expéditeur inconnu, prospect, '
            'correspondant à confirmer), l’agent prépare une réponse complète à partir du seul courriel et de ses pièces jointes (variantes à choisir là où '
            'votre position manque), marquée « [À RELIRE] » et déposée dans Brouillons si le contrôle de sûreté l’autorise. Jamais envoyée ; jamais un dossier deviné. État : <strong>%s</strong>.</p>'
            '<form class="r5671-form" data-c5671="m5671/reglage/sans-dossier" data-reload><input type="hidden" name="actif" value="%s">'
            '<button class="ax-btn%s" type="submit">%s</button></form><h3>Présentation du cabinet pour les nouveaux contacts</h3><p class="ax-muted">Domaines, modalités d’intervention, honoraires indicatifs. '
            'L’IA reprend seulement ce texte ; le reste est marqué « [À COMPLÉTER] ». Il est pseudonymisé comme le reste.</p>'
            '<form class="r5671-form" data-c5671="m5671/reglage/presentation" data-reload><label for="r5687-p">Présentation</label><textarea id="r5687-p" name="presentation" rows="6">%s</textarea>'
            '<button class="ax-btn ghost" type="submit">Enregistrer</button></form><h3>Notes internes</h3><p class="ax-muted">Les notes de l’agent deviennent un document Word du dossier, ou du dossier « A TRAITER » sans dossier identifié, '
            'toutes les 15 minutes. Le brouillon part dans la corbeille de la messagerie. État : <strong>%s</strong>.</p><form class="r5671-form" data-c5671="m5671/reglage/notes" data-reload><input type="hidden" name="actif" value="%s">'
            '<button class="ax-btn ghost" type="submit">%s</button></form></section>') % (
        'activées' if on else 'désactivées', '0' if on else '1', ' ghost' if on else '', e('Désactiver' if on else 'Activer'), e(str(desk.settings(rep.PRESENTATION, '') or '')),
        'actif' if notes else 'désactivé', '0' if notes else '1', 'Désactiver' if notes else 'Activer')
