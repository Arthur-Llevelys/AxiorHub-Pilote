"""5.6.82 : Paramètres › Modèles d'IA : mode, préférence, état d'usage, volet avancé, liaison poste-serveur, bandeau de limite.

Vocabulaire de l'abonnement (règles d'OpenAI) : « Utilise votre abonnement ChatGPT », « Gérer l'usage », « Limite d'usage atteinte ».
Jamais « gratuit » ni « illimité », aucune économie en euros déduite des tarifs d'API. Une valeur non accessible s'affiche « Non fournie ».
"""
from datetime import datetime, timedelta, timezone
from html import escape as e

from . import catalogue5682, conso5682, politique5682, routage5682, tache5682
from .sobriete5682 import counters

HINTS = {'local': 'Tout reste sur vos machines. Si le modèle local ne répond pas, le travail attend ou signale l’incident. Rien ne part.',
         'chatgpt': 'Tous les appels à l’IA passent par votre abonnement : Luna pour les tâches simples, Terra pour la rédaction courante, Sol pour les '
                    'analyses complexes. Limite atteinte : le travail est reporté, sans recours à un autre fournisseur. Ollama n’est pas nécessaire.',
         'mistral': 'Tous les appels passent par votre clé Studio Mistral : Mistral Small pour les tâches simples, Mistral Large pour la rédaction et l’analyse. '
                    'Utilise le quota de votre offre Mistral ; au-delà, selon vos réglages Mistral. Limite atteinte : le travail est reporté, sans recours à un autre fournisseur.',
         'anthropic': 'Tous les appels passent par votre clé Claude : Haiku pour les tâches simples, Sonnet pour la rédaction courante, Opus pour les analyses '
                      'complexes. Facturé à l’usage, dans la limite de vos plafonds.',
         'auto': 'AxiorHub choisit parmi les connexions que vous cochez, le local d’abord, puis l’abonnement, puis l’API. Le repli reste dans cette liste.',
         'routes': 'Votre installation garde les fournisseurs choisis fonction par fonction (Mode mixte, Routage).'}


def _fr(number, digits=2):
    return ('%.*f' % (digits, number)).replace('.', ',')


def _usage(desk):
    """Ancienne présentation de l'usage du mois (gardée derrière l'interrupteur de la nouvelle interface). Les corrections de C12 ne
    dépendent pas de l'interrupteur : mesure absente « Non fourni », somme partielle signalée, mois du cabinet (son fuseau)."""
    from .consommation56109 import periode
    mois = periode(desk)
    rows = conso5682.snapshot(desk.c['state_dir'], mois['debut_utc'], mois['fin_utc'])
    if not rows:
        return '<p class="ax-muted">Aucun appel enregistré ce mois-ci.</p>'
    body = ''
    def count(value, known, total):
        if value is None:
            return 'Non fourni'
        return format(value, ',').replace(',', ' ') + (' (partiel)' if known < total else '')
    for r in rows:
        if r['canal'] == 'local':
            amount = 'Pas de facturation API ; coût du poste non mesuré'
        elif r['canal'] == 'abonnement':
            amount = 'Abonnement ; coût par appel non fourni'
        elif r['unite'] == 'credits':
            amount = 'repère : %s crédits' % _fr(r['quantite']) if r['quantite'] is not None else 'Non fourni'
        elif r['unite'] == 'usd':
            amount = '%s $ estimés' % _fr(r['quantite']) if r['quantite'] is not None else 'Non fourni'
        else:
            amount = 'Non fourni'
        if r['quantite'] is not None and r['sans_usage'] and r['canal'] == 'api':
            amount += ' (partiel : %d appel(s) sans montant)' % r['sans_usage']
        body += '<tr><td>%s</td><td>%s</td><td>%d</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>' % (
            e(r['fournisseur']), e({'abonnement': 'Abonnement', 'api': 'API', 'local': 'Local'}.get(r['canal'], r['canal'])), r['appels'],
            e(count(r['entree'],r.get('entree_connues',r['appels']),r['appels'])),
            e(count(r['entree_cache'],r.get('cache_connues',r['appels']),r['appels'])),
            e(count(r['sortie'],r.get('sortie_connues',r['appels']),r['appels'])),e(amount))
    return ('<div class="m82-table"><table class="vf-table"><caption class="sr-only">Consommation du mois</caption><thead><tr><th>Connexion</th><th>Canal</th><th>Appels</th>'
            '<th>Entrée</th><th>En cache</th><th>Sortie</th><th>Consommation</th></tr></thead><tbody>%s</tbody></table></div>'
            '<p class="ax-muted">Les mesures absentes restent inconnues. Un total partiel ne constitue pas la facture du fournisseur. Les unités d’un abonnement ne donnent pas son solde restant.</p>') % body


def _plan_state(desk):
    from . import chatgpt5662, chatgpt5682
    acc = chatgpt5662.account(desk)
    if acc.get('etat') != 'connecte':
        return '<p class="m82-state">ChatGPT : non connecté.</p>'
    plan = 'Utilise votre abonnement ChatGPT' if acc.get('plan') else 'Connexion sans usage du forfait : reconnectez-vous pour l’autoriser'
    deferred = chatgpt5682.deferred(desk.c['state_dir'])
    extra = (' · <strong>Limite d’usage atteinte</strong>, prochaine vérification à %s' % e(_time(deferred['jusqu_a']))) if deferred else ''
    return ('<p class="m82-state"><strong>%s</strong>%s · limites restantes : Non fournie · crédits : Non fournie · <a href="%s" target="_blank" rel="noopener">Gérer l’usage</a></p>%s'
            % (e(plan), extra, e(chatgpt5662.USAGE_URL, quote=True), _last_failure(desk)))


def _last_failure(desk):
    """5.6.86 : le motif donné par OpenAI, pour comprendre « ChatGPT n’a pas pu terminer la réponse »."""
    from . import chatgpt5662
    from .cockpit530 import _local
    last = desk.settings(chatgpt5662.FAILURE_KEY, {}) or {}
    if not last.get('at') or str(last['at']) < (datetime.now(timezone.utc) - timedelta(days=7)).isoformat():
        return ''
    parts = [x for x in (last.get('modele'), ('statut %d' % last['statut']) if last.get('statut') else '', last.get('code'), last.get('param'), last.get('detail')) if x]
    return '<p class="ax-muted">Dernier échec, %s : %s.</p>' % (e(_local(desk, last['at'], '%d/%m %H:%M')), e(' · '.join(parts) or 'motif non fourni par OpenAI'))


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


def card(desk, prefix=None):
    """Bloc en tête de la page : les réglages utiles d'abord, le détail dans le volet avancé. ``prefix`` (5.6.109, I2) : adresses absolues
    de la section « Consommation et coûts » ; absent, adresses relatives (ancien appel)."""
    from .ai_gateway import provider_registry
    from . import chatgpt5662
    rule = politique5682.normalize(desk.settings(politique5682.KEY, {}) or {})
    acc = chatgpt5662.account(desk)
    registry = provider_registry(desk.c)
    chatgpt_ready = True if acc.get('etat') == 'connecte' and acc.get('plan') else 'À connecter d’abord (section ChatGPT, plus bas).'
    keyed = lambda kind: any(p.get('type') == kind and p.get('secret_file') for p in registry.values())
    key_field = lambda kind, name: '' if keyed(kind) else '<label class="m5-field m82-key">Clé API %s<input type="password" name="cle_%s" maxlength="4096" autocomplete="new-password"></label>%s' % (
        name, kind, _revision_field(desk, kind))
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
    # 5.6.109 (F18) : nouvelle interface : une seule section « Consommation et coûts », après le mode de l'IA ; le pli ne garde que les
    # derniers choix. Interrupteur éteint : l'ancien tableau « Usage du mois » reste à sa place, avec les mêmes corrections de mesure.
    nouvelle, section109 = False, ''
    try:
        from . import interface56100, membres5650
        compte = membres5650.current_author() or 'cabinet'
        if interface56100.active(desk, compte):
            section109 = consommation_html(desk, compte, membres5650.viewer_role(), prefix)
            nouvelle = True
    except Exception:   # noqa: BLE001 — la page des modèles reste utilisable ; l'absence est dite, jamais remplacée par un tableau vide
        nouvelle, section109 = True, ('<section class="ax-card m109" id="consommation-couts"><h2>Consommation et coûts</h2>'
                                      '<p class="ax-alert" role="status">Section indisponible pour le moment.</p></section>')
    pli = ('<details class="m82-adv"><summary>Derniers choix de l’IA</summary>%s</details>' % _journal(desk) if nouvelle else
           '<details class="m82-adv"><summary>Usage du mois et derniers choix de l’IA</summary>%s<h4>Choix de l’IA</h4>%s</details>' % (_usage(desk), _journal(desk)))
    return ('<section class="ax-card m82" id="m82-politique"><h2>Mode de l’IA</h2>'
            '<p>L’avocat formule sa demande : AxiorHub choisit pour chaque travail le modèle et l’effort adaptés, le plus économe pour les tâches simples, le plus capable pour les travaux complexes.</p>'
            '%s<form class="m5-form" data-api="m5682/politique" data-reload="1"><fieldset class="m82-modes"><legend>Fournisseur</legend>%s</fieldset>'
            '<fieldset class="m82-prefs"><legend>Préférence de traitement</legend>%s<small>Elle ajuste la profondeur. Elle n’élargit jamais les fournisseurs permis et n’autorise aucune dépense de plus.</small></fieldset>'
            '%s<details class="m82-adv"><summary>Volet avancé</summary>'
            '<fieldset><legend>Plafonds par mission et limites de l’abonnement</legend>%s%s%s<small>Le quota inclus et les crédits ne se distinguent pas toujours à l’usage : réglez les limites de l’application dans ChatGPT › Paramètres › Utilisation pour rester strictement dans votre forfait. '
            'AxiorHub n’achète jamais de crédits et ne relève aucune limite.</small>'
            '<label class="m5-field">Plafond local par mission (crédits, repère)<input name="plafond_mission_credits" inputmode="decimal" value="%s"></label>'
            '<label class="m5-field">Plafond local par mission (dollars, API)<input name="plafond_mission_usd" inputmode="decimal" value="%s"></label>'
            '<label class="m5-field">Appels automatiques par jour, au plus<input name="plafond_auto_jour" inputmode="numeric" value="%d"></label>'
            '<label class="m5-field">dont modèles avancés<input name="plafond_auto_avance_jour" inputmode="numeric" value="%d"></label>'
            '<small>Tri, réponses et analyses lancés sans demande de votre part. Plafond atteint, ils attendent le lendemain ; vos propres demandes ne sont ni comptées ni bloquées. Aujourd’hui : %d appel(s), dont %d avancé(s).</small></fieldset>'
            '<fieldset><legend>Recherche dans les pièces</legend>%s<small>Désactivée, la recherche reste textuelle et n’appelle aucun modèle. Activée, seul le calcul d’empreintes passe par un modèle local : la génération reste exclusive.</small></fieldset>'
            '</details><button class="ax-btn" type="submit">Enregistrer ce mode</button><p role="status" class="m5-status"></p></form>'
            '<details class="m82-adv"><summary>Modèles de chaque connexion</summary>%s</details>'
            '%s</section>%s') % (
        _plan_state(desk), modes, prefs,
        check('consentement', 'J’autorise l’envoi au fournisseur choisi des données pseudonymisées nécessaires', rule['consentement']),
        check('sans_cle_api', 'Aucun recours à une clé API OpenAI (toujours respecté en mode abonnement)', True),
        check('credits_autorises', 'Crédits achetés autorisés pour cette application', rule['credits_autorises']),
        check('niveau_maximal', 'Autoriser le niveau maximal (Astra, Claude Fable) pour les travaux les plus exigeants', rule['niveau_maximal']),
        _fr(rule['plafond_mission_credits'], 0), _fr(rule['plafond_mission_usd'], 2), rule['plafond_auto_jour'], rule['plafond_auto_avance_jour'], *counters(desk.c['state_dir']),
        check('semantique_locale', 'Recherche par le sens avec un modèle local (option distincte)', rule['semantique_locale']),
        _catalog(desk, rule), pli, section109) + _mistral_card(desk) + _gemini_card(desk)


def _revision_field(desk, kind):
    """5.6.96 (A03) : révision du réglage Mistral lue à l'ouverture ; l'enregistrement de la clé est refusé (409) si elle a changé."""
    if kind != 'mistral':
        return ''
    from .ai_gateway import revision_of
    from .mistral5696 import connexion
    pid, _ = connexion(desk.c)
    return '<input type="hidden" name="revision_mistral" value="%d">' % revision_of(desk.settings('ai:provider:' + pid, {}))


def _mistral_card(desk):
    """5.6.96 (A01) : carte « Mistral · clé Studio » (tester, enregistrer, états, quota), juste après le mode de l'IA."""
    from .mistral5696 import carte
    return carte(desk)


def _gemini_card(desk):
    """5.6.109 (lot E, F15) : carte « Google Gemini · clé API » (tester, enregistrer, réexaminer, activer ; API ou abonnement), après Mistral."""
    from .gemini56109 import carte
    return carte(desk)


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


# ------------------------------------------------------------------------------------------------ 5.6.109 (F18) : « Consommation et coûts »
# Section unique de Paramètres › Modèles d'IA. Elle lit le relevé commun (``consommation56109.releve`` : observations, registre du budget,
# générations locales, quantités de service) : pas de second compteur, pas de seconde page. API, abonnement, IA locale et services externes
# restent séparés par unité ; une valeur inconnue s'écrit « Non fourni », jamais zéro ; un total incomplet le dit, avec le nombre d'appels.
# Tout est rendu en lecture seule : ni écriture, ni appel de modèle, ni interrogation d'un fournisseur ; utilisable IA en pause.
ETATS_REGISTRE = {'reserved': 'Réservé (estimation)', 'dispatched': 'En cours (estimation)', 'uncertain': 'Incertain', 'settled': 'Réglé', 'released': 'Libéré, rien de facturé'}
ORIGINES_REGISTRE = {'calcule_depuis_usage': 'calculé depuis l’usage', 'majorant_cache_non_detaille': 'majorant, cache non détaillé', 'majorant_tarif_cache_absent': 'majorant, tarif du cache non renseigné',
                     'releve_fournisseur': 'relevé du fournisseur', 'mesure_transmise': 'mesure transmise', 'estimation_retenue': 'estimation retenue, sans mesure', 'forfait': 'forfait'}
ORIGINES_TARIF = {'decision_de_routage': 'décision de routage', 'reglage_du_fournisseur': 'réglage du fournisseur', 'reference_datee': 'référence datée'}
ETATS_QUANTITE = {'estimee': 'Estimée', 'declaree': 'Déclarée par le service', 'rapprochee': 'Rapprochée', 'inconnue': 'Non fournie'}
ETATS_COUT = {'connu': 'connu', 'estime': 'estimé', 'inconnu': 'non renseigné'}


def _montant(micro_valeur, devise='USD'):
    """Montant dans sa devise réelle. Inconnu : « Non fourni », jamais zéro. Un petit montant garde ses décimales (un appel coûte souvent
    moins d'un centime). Le dollar garde son signe, toute autre devise son code : aucun signe n'est échangé contre un autre."""
    if micro_valeur is None:
        return 'Non fourni'
    valeur = int(micro_valeur) / 1_000_000
    texte = _fr(valeur, 2) if micro_valeur == 0 or abs(int(micro_valeur)) >= 10_000 else ('%.6f' % valeur).rstrip('0').replace('.', ',')
    return texte + (' $' if devise in ('USD', '', None) else ' ' + str(devise))


def _ou_aucun(micro_valeur, devise='USD'):
    return 'Aucun' if micro_valeur is None else _montant(micro_valeur, devise)


def _nombre(valeur, partiel=False):
    """Nombre de jetons déjà échappé ; inconnu : « Non fourni » ; somme de valeurs dont certaines manquent : marquée « partiel »."""
    if valeur is None:
        return '<span class="m109-inconnu">Non fourni</span>'
    return e(format(int(valeur), ',').replace(',', ' ')) + (' <span class="m109-etat incomplet">partiel</span>' if partiel else '')


def _jour(texte):
    parts = str(texte or '')[:10].split('-')
    return '/'.join(reversed(parts)) if len(parts) == 3 else str(texte or '')


def _cabinet(desk, stamp):
    """Horodatage UTC d'une ligne, à l'heure du cabinet."""
    from .consommation56109 import fuseau
    try:
        return datetime.fromisoformat(str(stamp)).astimezone(fuseau(desk)).strftime('%d/%m/%Y %H:%M')
    except (TypeError, ValueError):
        return ''


def _table(legende, entetes, lignes):
    """Tableau dans son propre cadre (la page ne défile jamais de côté). ``entetes`` : (libellé, classes) ; cellules déjà échappées."""
    tete = ''.join('<th scope="col"%s>%s</th>' % ((' class="%s"' % c) if c else '', e(t)) for t, c in entetes)
    corps = ''.join('<tr>%s</tr>' % ''.join('<td%s>%s</td>' % ((' class="%s"' % entetes[i][1]) if entetes[i][1] else '', cellule) for i, cellule in enumerate(ligne))
                    for ligne in lignes)
    return ('<div class="m109-cadre"><table class="vf-table m109-table"><caption class="sr-only">%s</caption><thead><tr>%s</tr></thead><tbody>%s</tbody></table></div>'
            % (e(legende), tete, corps))


def _note(texte):
    return '<p class="m109-note">%s</p>' % e(texte)


def _quantite(s):
    """Quantité native d'un service, avec son unité : ni des dollars, ni des jetons."""
    if s['quantite_milli'] is None:
        return '<span class="m109-inconnu">Non fourni</span>'
    return e((_fr(int(s['quantite_milli']) / 1000, 3).rstrip('0').rstrip(',') or '0'))


def _resume(desk, r):
    """Premier niveau : période, coût API connu, estimé ou réservé, incertain, jetons connus, services, appels sans mesure, rapprochement."""
    api = r['api']
    registre = api['registre'] or {}
    connu = registre.get('regle_micro')
    if api.get('anciens'):
        connu = (connu or 0) + api['anciens']['micro']
    incomplet = ''
    if api['incomplet']:
        # le registre fait foi : appels incertains, en cours ou estimés sans mesure ; à défaut de réservation, les observations sans montant
        manque = sum(int(registre.get(k) or 0) for k in ('incertain_n', 'reserve_n', 'estime_n')) or api['appels_sans_montant']
        incomplet = ('<small><span class="m109-etat incomplet">total connu, incomplet</span>%s</small>'
                     % ((' %d appel(s) sans montant confirmé' % manque) if manque else ' des mesures manquent'))
    reserve = None
    for cle in ('reserve_micro', 'estime_micro'):
        if registre.get(cle) is not None:
            reserve = (reserve or 0) + registre[cle]
    entree = sortie = None
    for bloc in (api, r['abonnement']):
        entree = bloc['entree'] if entree is None else (entree if bloc['entree'] is None else entree + bloc['entree'])
        sortie = bloc['sortie'] if sortie is None else (sortie if bloc['sortie'] is None else sortie + bloc['sortie'])
    partiel = api['partiel'] or r['abonnement']['partiel']
    services = ('%d relevé(s), par service et par unité plus bas' % sum(s['releves'] for s in r['services'])) if r['services'] else 'Aucun relevé'
    return ('<dl class="m109-resume">'
            '<div><dt>Période (heure du cabinet)</dt><dd>%s<small>%s · %s</small></dd></div>'
            '<div class="m109-p"><dt>Coût API connu</dt><dd>%s%s</dd></div>'
            '<div class="m109-p"><dt>Estimé ou réservé</dt><dd>%s</dd></div>'
            '<div class="m109-p"><dt>Incertain</dt><dd>%s</dd></div>'
            '<div class="m109-t"><dt>Jetons connus en entrée</dt><dd>%s</dd></div>'
            '<div class="m109-t"><dt>Jetons connus en sortie</dt><dd>%s</dd></div>'
            '<div><dt>Services externes</dt><dd>%s</dd></div>'
            '<div><dt>Appels sans mesure</dt><dd>%d</dd></div>'
            '<div class="m109-p"><dt>Dernier rapprochement</dt><dd>%s</dd></div></dl>') % (
        e(_jour(r['periode']['debut']) + ' – ' + _jour(r['periode']['fin'])), e(r['periode']['fuseau']),
        'tout le cabinet' if r['portee'] == 'cabinet' else 'vos demandes seulement', e(_montant(connu)), incomplet, e(_ou_aucun(reserve)),
        e(_ou_aucun(registre.get('incertain_micro'))), _nombre(entree, partiel), _nombre(sortie, partiel), services, r['appels_sans_mesure'],
        e(_cabinet(desk, r['dernier_rapprochement'])) if r.get('dernier_rapprochement') else 'Aucun')


def _api_html(r):
    """API : jetons par catégorie (observations) et montants du registre (réglé, estimé ou réservé, incertain), par connexion et modèle."""
    api = r['api']
    observes = {(x['fournisseur'], x['modele']): x for x in api['lignes']}
    montants = {}
    for m in api['par_modele']:
        montants.setdefault((m['fournisseur'], m['modele']), []).append(m)
    lignes = []
    for cle in sorted(set(observes) | set(montants)):
        o = observes.get(cle)
        for rang, m in enumerate(montants.get(cle) or [None]):
            devise = m['devise'] if m else 'USD'
            reserve = None
            for k in ('reserve_micro', 'estime_micro'):
                if m and m.get(k) is not None:
                    reserve = (reserve or 0) + m[k]
            jetons = ['', '', '', '']
            appels = str(m['appels']) if m else '0'
            if o and rang == 0:
                appels = '%d' % o['appels'] + ((' <small>dont %d interrompu(s)</small>' % o['interrompus']) if o['interrompus'] else '')
                jetons = [_nombre(o['entree'], o['entree_n'] < o['appels'] or bool(o['entree_min'])), _nombre(o['cache_lu'], o['cache_lu_n'] < o['appels']),
                          _nombre(o['cache_ecrit'], o['cache_ecrit_n'] < o['appels']), _nombre(o['sortie'], o['sortie_n'] < o['appels'])]
            elif rang == 0:
                jetons = [_nombre(None)] * 4
            lignes.append([e(cle[0]), e(cle[1]), appels] + jetons + [e(_montant(m.get('regle_micro') if m else None, devise)), e(_ou_aucun(reserve, devise)),
                                                                   e(_ou_aucun(m.get('incertain_micro') if m else None, devise))])
    html = '<h3>API (facturée à l’usage)</h3>'
    if not lignes:
        return html + _note('Aucun appel à une API sur la période.')
    html += _table('Consommation des API', (('Connexion', ''), ('Modèle', ''), ('Appels', 'm109-n'), ('Entrée', 'm109-n m109-t'), ('dont cache lu', 'm109-n m109-t'),
                                            ('Cache écrit', 'm109-n m109-t'), ('Sortie', 'm109-n m109-t'), ('Coût connu', 'm109-n m109-p'), ('Estimé ou réservé', 'm109-n m109-p'),
                                            ('Incertain', 'm109-n m109-p')), lignes)
    registre = api['registre'] or {}
    if registre.get('regle_micro') is not None:
        parts = [(libelle, registre.get(k)) for k, libelle in (('calcule_micro', 'calculé depuis l’usage'), ('majorant_micro', 'majorant'), ('releve_micro', 'relevé du fournisseur rapproché'),
                                                               ('autre_micro', 'autre origine')) if registre.get(k) is not None]
        html += '<p class="m109-note m109-p">Coût connu, par origine : %s</p>' % ' · '.join('<span>%s</span> <strong>%s</strong>' % (e(libelle), e(_montant(v))) for libelle, v in parts)
    if api['appels_sans_montant']:
        html += _note('%d appel(s) sans montant calculé : usage ou tarif non fourni. Leur réservation reste comptée, incertaine, jusqu’au rapprochement.' % api['appels_sans_montant'])
    majorants = sum(x['majorants'] for x in api['lignes'])
    if majorants:
        html += _note('%d appel(s) calculé(s) en majorant : le détail du cache n’est pas fourni, toute l’entrée est comptée au tarif d’entrée.' % majorants)
    if api['historiques']:
        html += _note('%d appel(s) d’avant la 5.6.109 : relevés tels qu’ils ont été enregistrés ; aucun compteur n’est reconstitué.' % api['historiques'])
    if api.get('anciens'):
        html += _note('Estimations d’avant le registre commun (5.6.89), comprises dans le coût connu : %s pour %d appel(s).' % (_montant(api['anciens']['micro']), api['anciens']['appels']))
    return html + _note('Montants calculés par AxiorHub depuis l’usage déclaré et le tarif retenu à la date de l’appel : ce n’est pas la facture du fournisseur. '
                        'Les autres logiciels utilisant le même compte n’y figurent pas.')


def _cout_fixe(c):
    """Coût fixe du bilan : montant dans sa devise et état de la saisie, ou « non renseigné » (jamais zéro). Fragment déjà échappé."""
    if c['montant_micro'] is None:
        return '<strong>non renseigné</strong>'
    return '<strong>%s</strong> <small>%s</small>' % (e(_montant(c['montant_micro'], c['devise'])), e(ETATS_COUT.get(c['etat'], c['etat'])))


def _abonnement_html(r):
    """Abonnement : jetons réellement fournis et repère de crédits ; jamais un prix d'API appliqué au forfait, jamais un solde déduit."""
    bloc = r['abonnement']
    html = '<h3>Abonnement (forfait)</h3>'
    if bloc['lignes']:
        html += _table('Consommation des abonnements', (('Connexion', ''), ('Modèle', ''), ('Appels', 'm109-n'), ('Entrée', 'm109-n m109-t'), ('dont cache lu', 'm109-n m109-t'),
                                                        ('Sortie', 'm109-n m109-t'), ('Repère en crédits', 'm109-n')),
                       [[e(x['fournisseur']), e(x['modele']), '%d' % x['appels'], _nombre(x['entree'], x['entree_n'] < x['appels']), _nombre(x['cache_lu'], x['cache_lu_n'] < x['appels']),
                         _nombre(x['sortie'], x['sortie_n'] < x['appels']),
                         e(_fr(x['repere_credits'], 3)) + (' <span class="m109-etat incomplet">partiel</span>' if x['repere_n'] < x['appels'] else '') if x['repere_credits'] is not None
                         else '<span class="m109-inconnu">Non fourni</span>'] for x in bloc['lignes']])
    else:
        html += _note('Aucun appel par abonnement sur la période.')
    licence = next((c for c in r.get('couts_fixes') or [] if c['categorie'] == 'licence'), None)
    if licence is not None:
        html += '<p class="m109-note">Bilan du mois, catégorie « Licences » (forfaits compris) : %s</p>' % _cout_fixe(licence)
    return html + _note('Le forfait n’est pas converti au tarif de l’API : aucun prix par appel. Solde du quota : Non fourni. '
                        'Le repère en crédits est une estimation d’AxiorHub, pas les crédits garantis par le fournisseur.')


def _local_html(r):
    """IA locale : modèle, générations, jetons connus, durée ; pas de prix d'API, et pas de coût total nul pour autant."""
    bloc = r['local']
    html = '<h3>IA locale</h3>'
    if bloc is None:
        return html + _note('Relevé du modèle local : réservé à l’administrateur, sans filtre de compte, de dossier ni de mission (ces générations ne portent ni compte ni dossier).')
    if bloc['modeles']:
        def duree(x):
            if not bloc.get('duree_mesuree') or not x['duree_ms']:
                return '<span class="m109-inconnu">Non fourni</span>'
            return e(_fr(int(x['duree_ms']) / 60000, 1) + ' min')
        html += _table('Générations du modèle local', (('Modèle', ''), ('Générations', 'm109-n'), ('Entrée', 'm109-n m109-t'), ('Sortie', 'm109-n m109-t'), ('Durée', 'm109-n')),
                       [[e(x['modele']), '%d' % x['appels'], _nombre(x['entree'], x['entree_n'] < x['appels']), _nombre(x['sortie'], x['sortie_n'] < x['appels']), duree(x)]
                        for x in bloc['modeles']])
    else:
        html += _note('Aucune génération locale sur la période.')
    postes = [c for c in r.get('couts_fixes') or [] if c['categorie'] in ('hebergement', 'energie', 'stockage', 'maintenance')]
    if postes:
        html += '<p class="m109-note">Coûts du poste renseignés au bilan du mois :</p><ul class="m109-liens">%s</ul>' % ''.join(
            '<li><span>%s</span> : %s</li>' % (e(c['libelle']), _cout_fixe(c)) for c in postes)
    return html + _note('Pas de facturation API ; coût du poste non mesuré. Le coût du local n’est pas nul : matériel, énergie, stockage et maintenance se renseignent dans le bilan du mois, '
                        'avec leur méthode. Aucune consommation électrique n’est déduite sans mesure.')


def _services_html(r):
    html = '<h3>Services externes</h3>'
    if r['services']:
        html += _table('Quantités des services externes', (('Service', ''), ('Unité', ''), ('Relevés', 'm109-n'), ('Quantité', 'm109-n'), ('État', '')),
                       [[e(s['fournisseur']), e(s['unite'].replace('_', ' ')), '%d' % s['releves'],
                         _quantite(s) + (' <span class="m109-etat incomplet">partiel</span>' if s['quantite_milli'] is not None and s['connus'] < s['releves'] else ''),
                         e(ETATS_QUANTITE.get(s['etat'], s['etat']))] for s in r['services']])
    else:
        html += _note('Aucune quantité de service relevée sur la période.')
    return html + _note('Chaque service garde son unité : ses crédits ne sont ni des dollars ni des jetons, et ne s’additionnent ni entre services ni aux coûts de l’IA. '
                        'L’achat de crédits et leur consommation ne sont pas deux dépenses.')


def _plafonds_html(desk, r, lien):
    """Plafonds existants (cabinet, mois, fournisseur, mission) : mêmes valeurs que « Données et budget de l'IA », où ils se règlent."""
    etat = r.get('plafonds')
    if etat is None:
        return ''
    lignes = []
    for p in etat['plafonds']:
        if 'erreur' in p:
            lignes.append([e(p['libelle']), 'Illisible', 'Inconnu', ''])
            continue
        part = (100 * p['engage_micro'] // p['plafond_micro']) if p['plafond_micro'] else None
        alerte = '' if part is None or part < 80 else ' <span class="m109-etat incomplet">%s</span>' % ('plafond atteint' if part >= 100 else 'proche du plafond')
        lignes.append([e(p['libelle']) + ((' : <strong>%s</strong>' % e(p['cle'])) if p.get('cle') and p['perimetre'] == 'fournisseur' else ''), e(_montant(p['plafond_micro'])), e(_montant(p['engage_micro'])),
                       ('%d %%' % part if part is not None else '') + alerte])
    rule = politique5682.normalize(desk.settings(politique5682.KEY, {}) or {})
    html = '<h3>Plafonds</h3>' + (_table('Plafonds de l’IA', (('Plafond', ''), ('Limite', 'm109-n'), ('Engagé ce mois', 'm109-n'), ('Part', 'm109-n')), lignes) if lignes else _note('Aucun plafond fixé.'))
    html += _note('Plafond par mission : %s pour les API, %s crédits de repère pour un abonnement. Les étapes et les sous-agents d’une mission partagent le plafond de leur mission.'
                  % (_montant(int(round(float(rule['plafond_mission_usd']) * 1_000_000))), _fr(rule['plafond_mission_credits'], 0)))
    if etat['incertains']:
        html += _note('%d appel(s) incertain(s) pour %s : ce montant reste compté dans les plafonds tant qu’il n’est pas rapproché.' % (etat['incertains'], _montant(etat['incertains_micro'])))
    return html + '<ul class="m109-liens"><li><a href="%s#budget">Régler les plafonds du cabinet</a></li><li><a href="%s#bilan">Bilan du mois et coûts fixes</a></li></ul>' % (lien, lien)


def _mission_html(r):
    m = r.get('mission')
    if m is None:
        return ''
    if not m['appels']:
        return '<h3>Mission</h3>' + _note('Aucun appel facturable enregistré pour cette mission sur votre périmètre : ce n’est pas un coût nul.')
    try:
        plafond = _montant(int(round(float(m['plafond_usd']) * 1_000_000)))
    except (TypeError, ValueError):
        plafond = 'Non fourni'
    parts = ' · '.join('<span>%s</span> <strong>%s</strong>' % (libelle, e(_montant(m[cle]))) for cle, libelle in (
        ('reel_micro', 'réglé'), ('estime_micro', 'estimé sans mesure'), ('provisoire_micro', 'en cours'), ('incertain_micro', 'incertain')))
    return ('<h3>Mission</h3>' + _note('%d appel(s), étapes et sous-agents compris.' % m['appels']) + '<p class="m109-note m109-p">%s · <span>plafond de la mission</span> <strong>%s</strong></p>' % (parts, e(plafond)) +
            _note('Le total de la mission est la somme de ses appels : aucun montant récapitulatif ne s’y ajoute.'))


def _tarif_texte(t):
    """Tarif retenu pour l'appel, tel qu'il a été figé : prix par million de jetons, origine, date du relevé de la référence. Fragment échappé."""
    if not t:
        return 'Non enregistré'
    prix = t.get('prix') or {}
    valeur = lambda k: '<strong>%s</strong>' % (e(str(prix[k]).replace('.', ',')) if prix.get(k) is not None else 'non renseigné')
    origine = e(ORIGINES_TARIF.get(t.get('origine'), str(t.get('origine') or '')))
    if t.get('releve'):
        origine += ' <span>%s</span>' % e(_jour(t['releve']))
    return ('<span>entrée</span> %s · <span>cache lu</span> %s · <span>sortie</span> %s <small>dollars par million de jetons</small> <small>%s</small>'
            % (valeur('entree_hors_cache'), valeur('cache_lu'), valeur('sortie_facturable'), origine))


def _detail_html(desk, r):
    """Détail paginé des appels : modèle réellement utilisé, quantités, état de la mesure, tarif retenu et origine, état au registre.
    Ni texte d'un échange, ni pièce, ni secret : des nombres et des identifiants techniques seulement."""
    from .consommation56109 import MESURES, ORIGINES
    d = r.get('detail')
    if d is None:
        return ('<details class="m109-detail" data-m109-detail><summary>Détail des appels</summary>'
                '<p class="m109-note">Le détail se charge à l’ouverture. Il est aussi dans l’export CSV.</p></details>')
    lignes = []
    for a in d['appels']:
        registre = 'Aucune réservation'
        if a['registre_etat']:
            registre = '<span>%s</span>' % e(ETATS_REGISTRE.get(a['registre_etat'], a['registre_etat']))
            if a['registre_etat'] == 'settled' and a['retenu_micro'] is not None:
                registre += ' <strong>%s</strong> <small>%s</small>' % (e(_montant(a['retenu_micro'])), e(ORIGINES_REGISTRE.get(a['origine_registre'] or '', 'origine non enregistrée')))
            elif a['registre_etat'] in ('reserved', 'dispatched', 'uncertain'):
                registre += ' <strong>%s</strong>' % e(_montant(a['plafond_micro']))
            if a['releve_micro'] is not None:
                registre += ' <small>relevé du fournisseur</small> <strong>%s</strong>' % e(_montant(a['releve_micro'], a['releve_devise'] or 'USD'))
        montant = ('<strong>%s</strong> ' % e(_montant(a['montant_micro'], a['devise'] or 'USD'))) if a['montant_micro'] is not None else ''
        lignes.append([e(_cabinet(desk, a['at'])), e(a['fournisseur']), e(a['modele']) + ((' <small>%s</small>' % e(a['etape'])) if a.get('etape') else ''),
                       _nombre(a['entree']), _nombre(a['cache_lu']), _nombre(a['cache_ecrit']), _nombre(a['sortie']),
                       e(MESURES.get(a['mesure'] or '', a['mesure'])) + ('' if a['etat'] == 'done' else ' <small>appel interrompu</small>')
                       + ((' <small>révision %d</small>' % a['revision']) if a['revision'] else ''),
                       montant + '<small>%s</small>' % e(ORIGINES.get(a['etat_montant'] or '', a['etat_montant'])), _tarif_texte(a.get('tarif')) if a['canal'] == 'api' else 'Sans tarif d’API',
                       registre])
    html = '<details class="m109-detail" data-m109-detail data-m109-charge open><summary>Détail des appels</summary>'
    if not lignes:
        return html + _note('Aucun appel observé sur la période avec ces filtres.') + '</details>'
    html += _table('Détail des appels', (('Date', ''), ('Connexion', ''), ('Modèle et étape', ''), ('Entrée', 'm109-n m109-t'), ('dont cache lu', 'm109-n m109-t'), ('Cache écrit', 'm109-n m109-t'),
                                         ('Sortie', 'm109-n m109-t'), ('Mesure', ''), ('Montant calculé', 'm109-p'), ('Tarif retenu', 'm109-p'), ('Registre du budget', 'm109-p')), lignes)
    page, pages = r['page'], d['pages']
    html += '<p class="m109-pages"><span>Page %d sur %d · %d appel(s)</span>' % (page, pages, d['total'])
    if page > 1:
        html += '<button type="button" class="ax-btn ghost" data-m109-page="%d">Page précédente</button>' % (page - 1)
    if page < pages:
        html += '<button type="button" class="ax-btn ghost" data-m109-page="%d">Page suivante</button>' % (page + 1)
    return html + '</p></details>'


def vue_consommation(desk, owner, role, args=None, prefix=None):
    """Fragment de la vue (résumé, modes, plafonds, détail) pour les filtres donnés ; relu par ``v56109-conso.js`` à chaque changement de
    filtre. Les contrôles de droits et de filtres lèvent ``Stop`` avant toute donnée : l'appelant en fait un refus neutre."""
    from .consommation56109 import releve
    r = releve(desk, owner, role, args)
    if not r['disponible']:
        return ('<p class="ax-alert" role="status">Relevé indisponible : le registre de la consommation n’est pas encore prêt (il l’est au démarrage du service). '
                'Aucun total n’est affiché à sa place.</p>')
    lien = e(('' if prefix is None else prefix + '/') + 'parametres/gouvernance', quote=True)
    return _resume(desk, r) + _api_html(r) + _abonnement_html(r) + _local_html(r) + _services_html(r) + _plafonds_html(desk, r, lien) + _mission_html(r) + _detail_html(desk, r)


def consommation_html(desk, owner='cabinet', role='administrateur', prefix=None):
    """Section « Consommation et coûts » : filtres, choix d'affichage, vue du mois en cours rendue par le serveur (lisible sans script).
    ``prefix`` absent (appel depuis ``card``, qui ne le reçoit pas) : adresses relatives à la page des paramètres."""
    from .ai_gateway import PURPOSES, provider_registry
    from .common import Stop, load_matters, matter_display
    from .consommation56109 import CANAUX, PERIODES
    racine = '' if prefix is None else prefix + '/'
    try:
        vue = vue_consommation(desk, owner, role, {}, prefix)
    except Stop as error:
        vue = '<p class="ax-alert" role="status">%s</p>' % e('Relevé indisponible : le contrôle des droits n’a pas pu être fait.' if str(error) == 'controle_indisponible'
                                                             else 'Relevé indisponible pour le moment.')
    option = lambda valeur, texte, choisi=False: '<option value="%s"%s>%s</option>' % (e(str(valeur), quote=True), ' selected' if choisi else '', e(texte))
    try:
        dossiers = sorted(((str(m.get('id')), matter_display(m)) for m in load_matters(desk.c)), key=lambda x: x[1].lower())
    except Exception:   # noqa: BLE001 — registre des dossiers illisible : le filtre est simplement absent
        dossiers = []
    champs = ('<label>Période<select name="periode">%s</select></label>'
              '<label data-m109-perso hidden>Du<input type="date" name="debut"></label><label data-m109-perso hidden>Au<input type="date" name="fin"></label>'
              '<label>Fournisseur<select name="fournisseur">%s</select></label>'
              '<label>Modèle<input name="modele" maxlength="160" autocomplete="off"></label>'
              '<label>Canal<select name="canal">%s</select></label>'
              '<label>Fonction<select name="fonction">%s</select></label>'
              '<label>Dossier<select name="dossier">%s</select></label>'
              '<label>Mission<input name="mission" maxlength="120" autocomplete="off"></label>') % (
        ''.join(option(k, v, k == 'mois') for k, v in PERIODES.items()),
        option('', 'Tous') + ''.join(option(k, k) for k in sorted(provider_registry(desk.c))),
        option('', 'Tous') + ''.join(option(k, v) for k, v in CANAUX.items()),
        option('', 'Toutes') + ''.join(option(k, v[0]) for k, v in PURPOSES.items()),
        option('', 'Tous') + ''.join(option(k, v) for k, v in dossiers))
    if role == 'administrateur':
        champs += '<label>Utilisateur<input name="utilisateur" maxlength="200" autocomplete="off"></label>'
    return ('<section class="ax-card m109" id="consommation-couts" data-m109="m56109/conso" data-affichage="tout"><h2>Consommation et coûts</h2>'
            '<p class="ax-muted">Un seul relevé pour les API, les abonnements, l’IA locale et les services externes, chacun dans son unité. '
            'L’ouvrir ne consomme rien : ni génération, ni interrogation d’un fournisseur. Une valeur non fournie reste « Non fourni », jamais zéro.</p>'
            '<form class="m109-filtres" method="get" data-m109-filtres>%s<p class="m109-actions"><button class="ax-btn" type="submit">Afficher</button>'
            '<a class="ax-btn ghost" data-m109-export href="%sapi440/m56109/conso/export">Exporter (CSV)</a><span role="status" class="m5-status" data-m109-statut></span></p></form>'
            '<fieldset class="m109-affichage"><legend>Affichage</legend>'
            '<label><input type="radio" name="m109-affichage" value="tout" checked> Prix et tokens</label>'
            '<label><input type="radio" name="m109-affichage" value="prix"> Prix</label>'
            '<label><input type="radio" name="m109-affichage" value="tokens"> Tokens</label>'
            '<small>Ce choix masque des colonnes ; il ne change rien à ce qui est enregistré pour les plafonds.</small></fieldset>'
            '<div class="m109-vue" data-m109-vue aria-live="polite">%s</div>'
            '<script defer src="%sstatic/v56109-conso.js"></script></section>') % (champs, e(racine, quote=True), vue, e(racine, quote=True))
