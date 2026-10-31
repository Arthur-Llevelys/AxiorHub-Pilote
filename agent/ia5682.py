"""5.6.82 : Paramètres › Modèles d'IA : mode, préférence, état d'usage, volet avancé, liaison poste-serveur, bandeau de limite.

Vocabulaire de l'abonnement (règles d'OpenAI) : « Utilise votre abonnement ChatGPT », « Gérer l'usage », « Limite d'usage atteinte ».
Jamais « gratuit » ni « illimité », aucune économie en euros déduite des tarifs d'API. Une valeur non accessible s'affiche « Non fournie ».
"""
from html import escape as e

from . import catalogue5682, conso5682, politique5682, routage5682, tache5682
from .sobriete5682 import counters

HINTS = {'local': 'Tout reste sur vos machines. Si le modèle local ne répond pas, le travail attend ou signale l’incident. Rien ne part.',
         'chatgpt': 'Tous les appels à l’IA passent par votre abonnement : Luna pour les tâches simples, Terra pour la rédaction courante, Sol pour les '
                    'analyses complexes. Limite atteinte : le travail est reporté, sans recours à un autre fournisseur. Ollama n’est pas nécessaire.',
         'mistral': 'Tous les appels passent par votre clé Mistral : Mistral Small pour les tâches simples, Mistral Large pour la rédaction et l’analyse. '
                    'Facturé à l’usage, dans la limite de vos plafonds.',
         'anthropic': 'Tous les appels passent par votre clé Claude : Haiku pour les tâches simples, Sonnet pour la rédaction courante, Opus pour les analyses '
                      'complexes. Facturé à l’usage, dans la limite de vos plafonds.',
         'auto': 'AxiorHub choisit parmi les connexions que vous cochez, le local d’abord, puis l’abonnement, puis l’API. Le repli reste dans cette liste.',
         'routes': 'Votre installation garde les fournisseurs choisis fonction par fonction (Mode mixte, Routage).'}


def _fr(number, digits=2):
    return ('%.*f' % (digits, number)).replace('.', ',')


def _usage(desk):
    rows = conso5682.snapshot(desk.c['state_dir'])
    if not rows:
        return '<p class="ax-muted">Aucun appel enregistré ce mois-ci.</p>'
    body = ''
    for r in rows:
        if r['unite'] == 'credits':
            amount = 'repère : %s crédits' % _fr(r['quantite']) if r['quantite'] else 'Non fournie'
        elif r['unite'] == 'usd':
            amount = '%s $ estimés' % _fr(r['quantite']) if r['quantite'] else 'Non fournie'
        else:
            amount = '—'
        body += '<tr><td>%s</td><td>%s</td><td>%d</td><td>%d</td><td>%d</td><td>%d</td><td>%s</td></tr>' % (
            e(r['fournisseur']), e({'abonnement': 'Abonnement', 'api': 'API', 'local': 'Local'}.get(r['canal'], r['canal'])), r['appels'], r['entree'], r['entree_cache'], r['sortie'], e(amount))
    return ('<div class="m82-table"><table class="vf-table"><caption class="sr-only">Consommation du mois</caption><thead><tr><th>Connexion</th><th>Canal</th><th>Appels</th>'
            '<th>Entrée</th><th>En cache</th><th>Sortie</th><th>Consommation</th></tr></thead><tbody>%s</tbody></table></div>'
            '<p class="ax-muted">Les crédits sont un repère, à la vitesse Standard, relevé le 31 octobre 2026 : ils ne disent pas ce qu’il reste dans votre forfait.</p>') % body


def _plan_state(desk):
    from . import chatgpt5662, chatgpt5682
    acc = chatgpt5662.account(desk)
    if acc.get('etat') != 'connecte':
        return '<p class="m82-state">ChatGPT : non connecté.</p>'
    plan = 'Utilise votre abonnement ChatGPT' if acc.get('plan') else 'Connexion sans usage du forfait : reconnectez-vous pour l’autoriser'
    deferred = chatgpt5682.deferred(desk.c['state_dir'])
    extra = (' · <strong>Limite d’usage atteinte</strong>, prochaine vérification à %s' % e(_time(deferred['jusqu_a']))) if deferred else ''
    return ('<p class="m82-state"><strong>%s</strong>%s · limites restantes : Non fournie · crédits : Non fournie · <a href="%s" target="_blank" rel="noopener">Gérer l’usage</a></p>'
            % (e(plan), extra, e(chatgpt5662.USAGE_URL, quote=True)))


def _time(stamp):
    from datetime import datetime
    try:
        return datetime.fromisoformat(stamp).astimezone().strftime('%H:%M')
    except (TypeError, ValueError):
        return str(stamp)[:16]


def _mode_row(value, current, ready, extra=''):
    return ('<label class="m82-mode"><input type="radio" name="mode" value="%s"%s><span><strong>%s</strong><small>%s</small>%s%s</span></label>' % (
        value, ' checked' if value == current else '', e(politique5682.LABELS[value]), e(HINTS[value]),
        '' if ready is True else '<small class="m82-need">%s</small>' % e(ready), extra))


def _catalog(desk, rule):
    from .ai_gateway import provider_registry
    registry = provider_registry(desk.c)
    html = ''
    for pid, item in sorted(registry.items()):
        kind = item.get('type', 'ollama')
        if kind == 'ollama':
            continue
        rows = catalogue5682.entries(desk.c['state_dir'], pid)
        forms = ('<form class="m5-form m82-inline" data-api="m5682/catalogue" data-reload="1"><input type="hidden" name="provider" value="%s">'
                 '<button class="ax-btn ghost" type="submit">Actualiser le catalogue</button><p role="status" class="m5-status"></p></form>') % e(pid, quote=True)
        if not rows:
            html += '<h4>%s</h4><p class="ax-muted">Catalogue à lire : les modèles viennent de votre compte.</p>%s' % (e(pid), forms)
            continue
        trs = ''
        for r in rows:
            caps = r.get('capacites') or {}
            trs += '<tr%s><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>' % (
                ' class="m82-off"' if r.get('statut') != 'actif' else '', e(r.get('nom') or r['slug']),
                e(catalogue5682.NIVEAU_LABELS.get(r.get('niveau') or '', 'Non classé')), e(str(caps.get('contexte') or 'Non fournie')),
                e(', '.join(tache5682.EFFORT_LABELS.get(x, x) for x in caps.get('efforts') or []) or 'non réglable'),
                e({'catalogue_serveur': 'liste du fournisseur', 'documentation': 'documentation', 'refus_fournisseur': 'refus du fournisseur'}.get(caps.get('preuve'), 'inconnue')),
                'retiré' if r.get('statut') != 'actif' else 'actif')
        html += ('<h4>%s</h4><div class="m82-table"><table class="vf-table"><caption class="sr-only">Modèles de %s</caption><thead><tr><th>Modèle</th><th>Niveau</th><th>Contexte</th><th>Efforts</th>'
                 '<th>Preuve</th><th>État</th></tr></thead><tbody>%s</tbody></table></div>%s') % (e(pid), e(pid), trs, forms)
    return html


def _journal(desk):
    rows = routage5682.recent(desk.c['state_dir'], 12)
    if not rows:
        return '<p class="ax-muted">Aucun choix enregistré pour le moment.</p>'
    body = ''.join('<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>' % (
        e(_time(r['at'])), e(tache5682.TASK_LABELS.get(r['type_tache'], r['type_tache'])), e(r['modele']), e(tache5682.EFFORT_LABELS.get(r['effort'], r['effort'])),
        e(r['raison_courte']), e(r['etat'] if not r['code'] else r['etat'] + ' (' + r['code'] + ')')) for r in rows)
    return ('<div class="m82-table"><table class="vf-table"><caption class="sr-only">Derniers choix</caption><thead><tr><th>Heure</th><th>Travail</th><th>Modèle</th><th>Effort</th><th>Raison</th><th>État</th></tr></thead>'
            '<tbody>%s</tbody></table></div>') % body


def card(desk):
    """Bloc en tête de la page : les réglages utiles d'abord, le détail dans le volet avancé."""
    from .ai_gateway import provider_registry
    from . import chatgpt5662
    rule = politique5682.normalize(desk.settings(politique5682.KEY, {}) or {})
    acc = chatgpt5662.account(desk)
    registry = provider_registry(desk.c)
    chatgpt_ready = True if acc.get('etat') == 'connecte' and acc.get('plan') else 'À connecter d’abord (section ChatGPT, plus bas).'
    keyed = lambda kind: any(p.get('type') == kind and p.get('secret_file') for p in registry.values())
    key_field = lambda kind, name: '' if keyed(kind) else '<label class="m5-field m82-key">Clé API %s<input type="password" name="cle_%s" maxlength="4096" autocomplete="new-password"></label>' % (name, kind)
    boxes = ''.join('<label class="r52-day"><input type="checkbox" name="fournisseur_%s"%s> %s</label>' % (
        e(pid, quote=True), ' checked' if pid in rule['fournisseurs'] else '', e(pid)) for pid, item in sorted(registry.items())
        if item.get('enabled', True) and (item.get('type') == 'ollama' or item.get('external_data_allowed')))
    modes = (_mode_row('local', rule['mode'], True) + _mode_row('chatgpt', rule['mode'], chatgpt_ready) +
             _mode_row('mistral', rule['mode'], True, key_field('mistral', 'Mistral')) + _mode_row('anthropic', rule['mode'], True, key_field('anthropic', 'Claude')) +
             _mode_row('auto', rule['mode'], True, '<span class="m82-boxes">%s</span>' % boxes))
    if rule['mode'] == 'routes':
        modes = _mode_row('routes', 'routes', True) + modes
    prefs = ''.join('<label class="m82-pref"><input type="radio" name="preference" value="%s"%s> %s</label>' % (
        k, ' checked' if k == rule['preference'] else '', e(v)) for k, v in politique5682.PREFERENCE_LABELS.items())
    check = lambda name, text, on: '<label class="r52-day"><input type="checkbox" name="%s"%s> %s</label>' % (name, ' checked' if on else '', e(text))
    return ('<section class="ax-card m82" id="m82-politique"><h2>Mode de l’IA</h2>'
            '<p>L’avocat formule sa demande : AxiorHub choisit pour chaque travail le modèle et l’effort adaptés, le plus économe pour les tâches simples, le plus capable pour les travaux complexes.</p>'
            '%s<form class="m5-form" data-api="m5682/politique" data-reload="1"><fieldset class="m82-modes"><legend>Fournisseur</legend>%s</fieldset>'
            '<fieldset class="m82-prefs"><legend>Préférence de traitement</legend>%s<small>Elle ajuste la profondeur. Elle n’élargit jamais les fournisseurs permis et n’autorise aucune dépense de plus.</small></fieldset>'
            '%s<details class="m82-adv"><summary>Volet avancé</summary>'
            '<fieldset><legend>Consommation</legend>%s%s%s<small>Le quota inclus et les crédits ne se distinguent pas toujours à l’usage : réglez les limites de l’application dans ChatGPT › Paramètres › Utilisation pour rester strictement dans votre forfait. '
            'AxiorHub n’achète jamais de crédits et ne relève aucune limite.</small>'
            '<label class="m5-field">Plafond local par mission (crédits, repère)<input name="plafond_mission_credits" inputmode="decimal" value="%s"></label>'
            '<label class="m5-field">Plafond local par mission (dollars, API)<input name="plafond_mission_usd" inputmode="decimal" value="%s"></label>'
            '<label class="m5-field">Appels automatiques par jour, au plus<input name="plafond_auto_jour" inputmode="numeric" value="%d"></label>'
            '<label class="m5-field">dont modèles avancés<input name="plafond_auto_avance_jour" inputmode="numeric" value="%d"></label>'
            '<small>Tri, réponses et analyses lancés sans demande de votre part. Plafond atteint, ils attendent le lendemain ; vos propres demandes ne sont ni comptées ni bloquées. Aujourd’hui : %d appel(s), dont %d avancé(s).</small></fieldset>'
            '<fieldset><legend>Recherche dans les pièces</legend>%s<small>Désactivée, la recherche reste textuelle et n’appelle aucun modèle. Activée, seul le calcul d’empreintes passe par un modèle local : la génération reste exclusive.</small></fieldset>'
            '</details><button class="ax-btn" type="submit">Enregistrer ce mode</button><p role="status" class="m5-status"></p></form>'
            '<details class="m82-adv"><summary>Modèles de chaque connexion</summary>%s</details>'
            '<details class="m82-adv"><summary>Usage du mois et derniers choix de l’IA</summary>%s<h4>Choix de l’IA</h4>%s</details></section>') % (
        _plan_state(desk), modes, prefs,
        check('consentement', 'J’autorise l’envoi au fournisseur choisi des données pseudonymisées nécessaires', rule['consentement']),
        check('sans_cle_api', 'Aucun recours à une clé API OpenAI (toujours respecté en mode abonnement)', True),
        check('credits_autorises', 'Crédits achetés autorisés pour cette application', rule['credits_autorises']),
        check('niveau_maximal', 'Autoriser le niveau maximal (Astra, Claude Fable) pour les travaux les plus exigeants', rule['niveau_maximal']),
        _fr(rule['plafond_mission_credits'], 0), _fr(rule['plafond_mission_usd'], 2), rule['plafond_auto_jour'], rule['plafond_auto_avance_jour'], *counters(desk.c['state_dir']),
        check('semantique_locale', 'Recherche par le sens avec un modèle local (option distincte)', rule['semantique_locale']),
        _catalog(desk, rule), _usage(desk), _journal(desk))


def transfer_html(connected, local):
    """Liaison officielle poste → serveur : session chiffrée, jamais dans une adresse, un journal ou une capture."""
    field = '<label class="m5-field">Phrase de passe (12 caractères au moins)<input type="password" name="phrase" minlength="12" maxlength="200" autocomplete="new-password" required></label>'
    if local:
        if not connected:
            return ''
        return ('<details class="m82-adv"><summary>Transférer cette connexion vers le serveur du cabinet</summary>'
                '<p>Ce poste chiffre sa session ChatGPT et cesse de l’utiliser : le serveur gère ensuite seul les renouvellements.</p>'
                '<form class="m5-form" id="m82-export" data-api="m5682/session/export">%s<button class="ax-btn ghost" type="submit">Préparer le transfert</button>'
                '<p role="status" class="m5-status"></p></form></details>') % field
    return ('<details class="m82-adv"><summary>Recevoir la connexion préparée sur un poste</summary>'
            '<p>Procédure officielle pour un serveur : connectez-vous sur un poste AxiorHub, préparez le transfert, puis collez ici le paquet chiffré.</p>'
            '<form class="m5-form" data-api="m5682/session/import" data-reload="1"><label class="m5-field">Paquet chiffré<textarea name="paquet" rows="3" maxlength="60000" autocomplete="off" required></textarea></label>'
            '%s<button class="ax-btn" type="submit">Recevoir la connexion</button><p role="status" class="m5-status"></p></form></details>') % field


def banner_html(desk, prefix):
    """Aujourd'hui : une limite d'usage n'est ni une panne de messagerie ni un arrêt demandé par l'avocat."""
    from . import chatgpt5662, chatgpt5682
    found = chatgpt5682.deferred(desk.c['state_dir'])
    if not found:
        return ''
    title = 'Limite d’usage atteinte' if found.get('motif') == 'chatgpt_limite_usage_atteinte' else 'ChatGPT indisponible pour le moment'
    return ('<div class="m82-banner"><cds-inline-notification kind="warning" low-contrast hide-close-button role="status" title="%s" subtitle="%s"></cds-inline-notification>'
            '<a class="ax-btn ghost" href="%s" target="_blank" rel="noopener">Gérer l’usage</a></div>') % (
        e(title, quote=True), e('Les travaux d’IA sont reportés, sans doublon. Prochaine vérification à %s. Les travaux sans IA continuent.' % _time(found['jusqu_a']), quote=True),
        e(chatgpt5662.USAGE_URL, quote=True))
