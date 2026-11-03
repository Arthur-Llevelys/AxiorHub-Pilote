"""5.6.89 : routes ``/api440/m5689/horaires*`` et écran Paramètres › IA et automatisations › Horaires de fonctionnement.

Lecture : tous les rôles. Écriture et dérogation : droit « réglages » du cabinet (administrateur, ou avocat à qui il a été accordé), contrôle CSRF
(``check_post``) et révision lue par le formulaire ; une révision périmée reçoit 409 et rien n'est écrasé. Le compte vient de la requête authentifiée,
jamais du corps. Le rendu est complet côté serveur ; le script ``v5689-horaires.js`` n'ajoute que l'édition des plages, sans HTML venu du serveur."""
from html import escape as e
import uuid
from zoneinfo import available_timezones

from . import horaires_ia5689 as horaires
from .common import Stop, load_matters, matter_display

LECTURE = ('horaires',)
ECRITURE = ('horaires', 'horaires/apercu', 'horaires/derogation', 'horaires/derogation/retirer')
JOURS_COURTS = ('Lun', 'Mar', 'Mer', 'Jeu', 'Ven', 'Sam', 'Dim')
JOURS_LONGS = ('Lundi', 'Mardi', 'Mercredi', 'Jeudi', 'Vendredi', 'Samedi', 'Dimanche')
FUSEAUX_PROPOSES = ('Europe/Paris', 'Europe/Brussels', 'Europe/Luxembourg', 'Europe/Monaco', 'Europe/Zurich', 'America/Guadeloupe', 'America/Martinique',
                    'America/Cayenne', 'Indian/Reunion', 'Indian/Mayotte', 'Pacific/Noumea', 'Pacific/Tahiti', 'UTC')
ICONE = ('<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><circle cx="12" cy="12" r="9"/>'
         '<path d="M12 7v5l3 2"/></svg>')


def allowed(role, route):
    """Rôles admis à écrire ; le droit « réglages » du compte est contrôlé ensuite (``membres5650.category`` et ``_ecriture``). Route inconnue : refusée."""
    return route in ECRITURE and role in ('administrateur', 'avocat')


def peut_modifier(state_dir, owner, role):
    if role == 'administrateur':
        return True
    from .membres5650 import effective_rights
    try:
        return role == 'avocat' and bool(effective_rights(state_dir, owner, role).get('reglages'))
    except Stop:
        return False                                               # registre des accès illisible : refus


def _ecriture(desk, owner, role):
    if not peut_modifier(desk.c['state_dir'], owner, role):
        raise Stop('role_insuffisant')


def _erreur(error):
    from .erreurs5634 import explain
    from .web import REASONS
    from .web440 import json_out
    code = str(error).split(':')[0]
    statut = ('403 Forbidden' if code == 'role_insuffisant' else '409 Conflict' if code in ('revision_perimee', 'confirmation_pause_immediate_requise') else
              '404 Not Found' if code == 'route_inconnue' else '400 Bad Request')
    return json_out({'error': code, 'message': REASONS.get(code) or explain(code, getattr(error, 'detail', None))}, statut)


def api(env, desk, auth, prefix, name, args, method):
    try:
        return _route(env, desk, auth, name, method)
    except Stop as error:
        return _erreur(error)


def _route(env, desk, auth, name, method):
    from .membres5650 import hidden_for
    from .web440 import check_post, json_out
    from .web567 import actor
    owner, role = actor(env)
    route = name[len('m5689/'):].split('?', 1)[0]
    if method == 'GET':
        if route not in LECTURE:
            raise Stop('route_inconnue')
        return json_out(horaires.etat_public(desk))
    if method != 'POST':
        raise Stop('methode_refusee')
    if route not in ECRITURE:
        raise Stop('route_inconnue')
    data = check_post(env, auth)
    _ecriture(desk, owner, role)
    if route == 'horaires':
        etat = horaires.enregistrer(desk, data, owner, confirmer=data.get('confirmer_pause_immediate') is True)
        return json_out({**etat, 'message': 'Horaires enregistrés sur le serveur. ' + etat['synthese']})
    if route == 'horaires/apercu':
        return json_out(horaires.apercu(desk, data))
    if route == 'horaires/derogation':
        dossier = str(data.get('dossier') or '')
        if dossier in hidden_for(desk.c['state_dir'], owner, role):
            raise Stop('dossier_absent')
        etat = horaires.accorder_derogation(desk, owner, str(data.get('perimetre') or 'demandes'), dossier, str(data.get('cle') or ''))
        return json_out({**etat, 'message': 'Dérogation enregistrée pour %d minutes : elle ne lève que le planning.' % horaires.DEROGATION_MINUTES})
    etat = horaires.retirer_derogation(desk, owner)
    return json_out({**etat, 'message': 'Dérogation retirée. ' + etat['synthese']})


# ---------------------------------------------------------------- écran
def lendemain(plage):
    """Une fin antérieure au début appartient au lendemain : l'indication reste affichée en toutes lettres."""
    return plage.get('kind', 'period') == 'period' and horaires._minutes(plage.get('end', '00:00')) <= horaires._minutes(plage.get('start', '00:00'))


def choix_jours(jours):
    jours = sorted(jours)
    if jours == [1, 2, 3, 4, 5, 6, 7]:
        return 'tous'
    return 'ouvres' if jours == [1, 2, 3, 4, 5] else 'perso'


def _option(valeur, libelle, choisie):
    return '<option value="%s"%s>%s</option>' % (e(valeur, quote=True), ' selected' if valeur == choisie else '', e(libelle))


def _ligne(plage, numero, lecture):
    """Une plage du formulaire ; le modèle vide utilise la même fonction. Toutes les commandes sont natives, donc atteignables au clavier."""
    off = ' disabled' if lecture else ''
    jours, genre = plage.get('days', [1, 2, 3, 4, 5, 6, 7]), plage.get('kind', 'period')
    choix = choix_jours(jours)
    cases = ''.join('<label class="m50-check" title="%s"><input type="checkbox" data-jour="%d"%s%s aria-label="%s"> %s</label>' % (
        JOURS_LONGS[i - 1], i, ' checked' if i in jours else '', off, JOURS_LONGS[i - 1], JOURS_COURTS[i - 1]) for i in range(1, 8))
    heures = ('<label>Début<input data-debut type="text" inputmode="numeric" maxlength="5" autocomplete="off" value="%s" aria-describedby="h89-format"%s></label>'
              '<label>Fin<input data-fin type="text" inputmode="numeric" maxlength="5" autocomplete="off" value="%s" aria-describedby="h89-format"%s></label>') % (
                  e(plage.get('start', '18:00'), quote=True), off, e(plage.get('end', '07:00'), quote=True), off)
    return ('<fieldset class="ax-card" data-plage data-id="%s"><legend>Plage <span data-numero>%d</span></legend>'
            '<label>Jours<select data-choix%s>%s%s%s</select></label>'
            '<div data-jours role="group" aria-label="Jours où la plage commence"%s><div class="m50-checks">%s</div></div>'
            '<label>Type<select data-genre%s>%s%s</select></label>'
            '<div data-heures%s>%s<p class="ax-muted" data-lendemain%s>La fin tombe le lendemain.</p></div>'
            '<p class="ax-alert" data-erreur role="alert" hidden></p>'
            '%s</fieldset>') % (
        e(str(plage.get('id', '')), quote=True), numero, off, _option('tous', 'Tous les jours', choix), _option('ouvres', 'Jours ouvrés', choix),
        _option('perso', 'Personnaliser', choix), ' hidden' if choix != 'perso' else '', cases, off, _option('period', 'Plage horaire', genre),
        _option('full_day', 'Journée entière', genre), ' hidden' if genre == 'full_day' else '', heures, '' if lendemain(plage) else ' hidden',
        '' if lecture else '<button type="button" class="ax-btn ghost" data-supprimer>Supprimer cette plage</button>')


def _transition(desk, etat, now):
    """Fuseau et prochain changement, toujours explicites : l'heure affichée est celle du cabinet, pas celle du poste."""
    if etat['prochaine_transition_utc']:
        zone = horaires._zone(etat['fuseau'])
        local = horaires._date(etat['prochaine_transition_utc']).astimezone(zone)
        return 'Prochain changement : %s à %s (%s).' % (horaires._jour(local, now.astimezone(zone)), local.strftime('%H:%M'), etat['fuseau'])
    return 'Aucun changement prévu (%s).' % etat['fuseau']


def _derogation(desk, etat, now, ecriture, owner, role):
    active = etat['derogation']
    if active:
        zone = horaires._zone(etat['fuseau'])
        fin = horaires._date(active['fin']).astimezone(zone)
        portee = horaires.PERIMETRES[active['perimetre']] + ((' : ' + active['dossier']) if active['dossier'] else '')
        retrait = ('<form class="m5-form m5-inline" data-api="m5689/horaires/derogation/retirer" data-reload="1"><button class="ax-btn ghost" type="submit">Retirer la dérogation</button></form>'
                   if ecriture else '')
        return ('<div id="h89-derogation"><h3>Dérogation en cours</h3><p class="ax-note">Autorisée jusqu’à %s, par %s. Périmètre : %s.</p>%s</div>' % (
            e(fin.strftime('%H:%M')), e(active['par']), e(portee), retrait))
    if not (etat['en_pause'] and ecriture):
        return ''
    from .membres5650 import hidden_for
    caches = hidden_for(desk.c['state_dir'], owner, role)
    dossiers = ''.join('<option value="%s">%s</option>' % (e(str(m['id']), quote=True), e(matter_display(m)))
                       for m in load_matters(desk.c) if m['id'] not in caches)
    perimetres = ''.join(_option(cle, libelle, 'demandes') for cle, libelle in horaires.PERIMETRES.items())
    return ('<div id="h89-derogation"><h3>Dérogation temporaire</h3>'
            '<p>Elle lève le seul planning, pour 30 minutes et dans le périmètre choisi. Elle ne lève ni une pause manuelle, ni la pause d’un dossier, ni une annulation, '
            'ni un blocage de sécurité, et ne donne aucun droit supplémentaire.</p>'
            '<form class="m5-form" data-api="m5689/horaires/derogation" data-reload="1"><input type="hidden" name="cle" value="%s">'
            '<label>Périmètre<select name="perimetre">%s</select></label><label>Dossier, pour le périmètre « Un seul dossier »<select name="dossier"><option value="">—</option>%s</select></label>'
            '<button class="ax-btn" type="submit">Autoriser exceptionnellement pendant 30 minutes</button></form></div>') % (uuid.uuid4().hex, perimetres, dossiers)


def _fuseaux():
    try:
        connus = available_timezones()
    except (OSError, ValueError, ImportError):
        connus = frozenset()
    return ''.join('<option value="%s"></option>' % e(nom, quote=True) for nom in FUSEAUX_PROPOSES if nom in connus)


def section_html(desk, prefix, owner, role, now=None):
    """Rubrique complète : réglage modifiable pour le rôle autorisé, lecture seule sinon. Il n'est jamais activé par cet écran sans l'action de l'utilisateur."""
    now = horaires._utc(now)
    etat = horaires.etat_public(desk, now)
    politique = etat['politique']
    ecriture = peut_modifier(desk.c['state_dir'], owner, role)
    lecture = not ecriture
    off = ' disabled' if lecture else ''
    incident = '<p class="ax-alert" role="alert"><strong>Incident :</strong> %s Les nouveaux appels à l’IA sont bloqués ; les services sans IA continuent.</p>' % e(etat['incident_message']) if etat['incident'] else ''
    notes = ''.join('<p class="ax-note">%s</p>' % e(texte) for texte in etat['explications'])
    suspendus = ('<p class="ax-muted">%d travail(aux) suspendu(s) par la pause programmée, repris à la fin de la plage.</p>' % etat['travaux_suspendus']) if etat['travaux_suspendus'] else ''
    tardives = ('<p class="ax-muted">%d réponse(s) reçue(s) après le début d’une pause : conservée(s) comme résultat partiel, jamais publiée(s) ; le travail repris recalcule à partir des sources du moment.</p>'
                % etat['reponses_tardives']) if etat['reponses_tardives'] else ''
    plages = ''.join(_ligne(p, i, lecture) for i, p in enumerate(politique['windows'], 1))
    vide = '<p class="ax-muted" id="h89-vide"%s>Aucune plage enregistrée.</p>' % (' hidden' if plages else '')
    boutons = '' if lecture else (
        '<p><button type="button" class="ax-btn ghost" id="h89-raccourci">Pause chaque jour de 18 h à 7 h</button> '
        '<button type="button" class="ax-btn ghost" id="h89-ajouter">Ajouter une plage</button></p>'
        '<p id="h89-apercu" role="status"></p><p class="ax-alert" id="h89-erreur" role="alert" hidden></p>'
        '<p><button type="submit" class="ax-btn" id="h89-enregistrer">Enregistrer</button></p>')
    droit = '' if ecriture else '<p class="ax-note">Consultation seule : ce réglage demande le droit « Réglages » du cabinet.</p>'
    modele = '' if lecture else '<template id="h89-modele">%s</template>' % _ligne({'days': [1, 2, 3, 4, 5, 6, 7], 'kind': 'period', 'start': '18:00', 'end': '07:00'}, 0, False)
    return ('<section class="ax-card" id="h89-horaires" aria-labelledby="h89-titre" data-revision="%d"><h2 id="h89-titre">%s Horaires de fonctionnement</h2>'
            '<p>Le cabinet choisit quand l’IA travaille. Le réglage agit sur le serveur, même si aucune page n’est ouverte. La pause manuelle reste indépendante : '
            '« Reprendre » ne modifie jamais ces horaires. Les sauvegardes, l’édition humaine et les actions sans IA restent disponibles.</p>'
            '<p id="h89-synthese" role="status"><strong>%s</strong></p><p class="ax-muted" id="h89-transition">%s</p>%s%s%s%s%s'
            '<form id="h89-formulaire" novalidate%s><label class="m50-check"><input type="checkbox" role="switch" id="h89-actif"%s%s> Programmer les pauses de l’IA</label>'
            '<label>Fuseau horaire du cabinet<input type="text" id="h89-fuseau" list="h89-fuseaux" maxlength="100" autocomplete="off" value="%s"%s></label><datalist id="h89-fuseaux">%s</datalist>'
            '<p class="ax-muted" id="h89-format">Heures au format 24 h, par exemple 18:00 ; 00:00 ou 24:00 pour minuit. Les jours choisis sont ceux où la plage commence. '
            'Une fin antérieure au début tombe le lendemain.</p><div id="h89-plages">%s</div>%s%s%s</form>%s</section>') % (
        politique['revision'], ICONE, e(etat['synthese']), e(_transition(desk, etat, now)), incident, notes, suspendus, tardives, droit,
        ' aria-disabled="true"' if lecture else '', ' checked' if politique['enabled'] else '', off, e(politique['timezone'], quote=True), off, _fuseaux(),
        plages, vide, boutons, modele, _derogation(desk, etat, now, ecriture, owner, role))
