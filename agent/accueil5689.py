"""5.6.89 : page Aujourd'hui (audit 5.6.88, §§ 11 et 12) : le travail préparé d'abord, un seul défilement principal.

Disposition : en tête le titre, l'état et une commande IA ; colonne principale : Travail préparé, routines compactes, décisions et relectures sur une même largeur ; colonne secondaire : liste du
matin, agenda et activité ; en bas : veille et accès secondaires. Ce module ne calcule rien : il range les mêmes morceaux que ``cockpit530.page``, avec les mêmes identifiants, pour que les scripts
(``v530.js``, ``v5614.js``) et les personnalisations de ``tableau5688`` s'appliquent sans changement. Drapeau ``accueil`` éteint : ``cockpit530.page`` rend l'ancienne page, sur les mêmes données.

Travail préparé lit le registre ``livrables5689`` ; une source en panne s'annonce comme telle, jamais comme un zéro. Les filtres restent visibles à zéro.
Alertes : décision métier, relecture et incident technique sont séparés ; un incident ne passe pas devant une échéance (§ 11).
Personnalisation : l'ordre choisi vaut dans chaque colonne ; un ordre personnel réunit les blocs de travail dans la colonne principale, journée et activité restant en colonne secondaire.
"""
from datetime import datetime, timezone
from html import escape as e
import re
from urllib.parse import urlencode

from .ui_components5689 import etat_html, etiquette, filtres, icone

COTE = ('journee', 'activite')                                   # colonne secondaire
BAS = ('veille', 'societes', 'memoire', 'reprise')               # sous les colonnes, sur toute la largeur
PREMIERS = 5                                                     # lignes par filtre ; le reste est dans Documents préparés
RESUME = 220                                                     # caractères du résumé du briefing
FILTRES = (('tout', 'Tout'), ('documents', 'Documents'), ('courriels', 'Courriels'), ('notes', 'Notes'))
ICONE_NATURE = {'courriel': 'courriel', 'note': 'modifier', 'contrat': 'contrat', 'convention': 'contrat', 'conditions_generales': 'contrat', 'formulaire': 'document_taches'}


def _famille(nature):
    return 'courriels' if nature == 'courriel' else 'notes' if nature == 'note' else 'documents'


def _heure(desk, valeur):
    from .cockpit530 import _local                  # fuseau du cabinet, comme le reste de la page
    try:
        return _local(desk, valeur, '%H h %M')
    except (TypeError, ValueError):
        return ''


def _ancien(valeur, jours):
    try:
        moment = datetime.fromisoformat(str(valeur).replace('Z', '+00:00'))
        return (datetime.now(timezone.utc) - moment).days > jours
    except (TypeError, ValueError):
        return False


def _ligne(desk, carte, prefix, jours):
    """Une ligne : titre complet (jamais tronqué), dossier, nature écrite, version et état. Le titre ouvre le détail ; ``return_to`` y ramène."""
    href = carte['href'] + '?' + urlencode({'return_to': prefix + '/aujourdhui'})
    etats = []
    if carte.get('kind') == 'courriel' and carte.get('deposit_state'):
        pass                                           # une réponse préparée se dit par son dépôt, pas par une production en cours
    elif carte.get('state') == 'preparing':
        etats.append(etiquette(carte.get('production_label') or carte['state_label'], 'info'))
    elif carte.get('state') in ('failed', 'partial'):
        etats.append(etiquette(carte['state_label'], 'erreur'))
    else:
        etats.append(etiquette(carte.get('review_label') or 'À relire', 'succes'))
    if carte.get('deposit_state'):                     # UX14 : l'état de dépôt est distinct de l'état du projet
        from .livrables_web5689 import DEPOT
        etats.append(etiquette(DEPOT.get(carte['deposit_state'], ''), 'neutre'))
    if _ancien(carte.get('updated_at'), jours):
        etats.append(etiquette('Ancien', 'attention', 'Préparé il y a plus de %d jours' % jours))
    return ('<li class="a89-ligne" data-livrable="%s" data-famille="%s"><span class="a89-pastille" data-nature="%s">%s</span><div class="a89-corps"><a class="a89-titre" href="%s">%s</a>'
            '<p class="a89-dossier">%s</p><p class="a89-meta"><span>%s</span><span>Version %d</span><span>%s</span></p></div><div class="a89-droite">%s<a class="ax-btn ghost" href="%s" aria-label="Ouvrir : %s"%s>Ouvrir</a></div></li>') % (
        e(carte['document_id'], quote=True), _famille(carte.get('kind')), e(carte.get('kind') or '', quote=True), icone(ICONE_NATURE.get(carte.get('kind'), 'document')), e(href, quote=True), e(carte['title']),
        e(carte.get('matter_label') or 'Sans dossier'), e(carte.get('nature') or ''), carte.get('version') or 1, e(_heure(desk, carte.get('updated_at'))), ''.join(etats), e(href, quote=True), e(carte['title'], quote=True),
        # 5.6.96 (I02) : « Ouvrir » ouvre le même détail en fenêtre flottante (sans script : la page du document, comme avant)
        ' data-f96-action="%s" data-f96-cle="%s" data-f96-titre="%s" data-f96-dossier="%s"' % ('reponse' if carte.get('kind') == 'courriel' else 'document', e(carte['document_id'], quote=True),
                                                                                         e(carte['title'], quote=True), e(carte.get('matter_label') or '', quote=True)))


def _lecture(desk, prefix):
    from . import livrables5689 as liv
    hidden, droits = liv.droits_requete()
    return liv.projection(desk, hidden, droits, revue='to_review', limite=60, prefix=prefix), liv.RECENCE


def travail_html(desk, prefix):
    """Contenu de « Travail préparé », aussi servi seul à l'actualisation (``m530/part``). Une erreur de lecture est un état, pas une liste vide."""
    try:
        data, jours = _lecture(desk, prefix)
    except Exception:   # noqa: BLE001 — l'accueil s'affiche toujours, et ne fait pas croire à une liste vide
        import logging
        logging.getLogger(__name__).exception('travail préparé')
        return etat_html('erreur', 'Documents préparés indisponibles', 'La lecture du registre a échoué : ce n’est pas un zéro. Réessayez dans un instant.',
                         '<a class="ax-btn ghost" href="%s">Ouvrir la liste complète</a>' % e(prefix + '/livrables', quote=True))
    par_nature = data['counts']['par_nature']
    comptes = {'documents': sum(n for k, n in par_nature.items() if _famille(k) == 'documents'), 'courriels': par_nature.get('courriel', 0), 'notes': par_nature.get('note', 0)}
    comptes['tout'] = sum(comptes.values())
    entrees = [(k, v, comptes[k] if data['complet'] or comptes[k] else None) for k, v in FILTRES]
    actif = 'documents' if comptes['documents'] or not comptes['tout'] else 'tout'
    panneaux = []
    for cle, libelle in FILTRES:
        lignes = [c for c in data['items'] if cle == 'tout' or _famille(c.get('kind')) == cle]
        corps = ''.join(_ligne(desk, c, prefix, jours) for c in lignes[:PREMIERS])
        if corps:
            contenu = '<ul class="a89-liste">%s</ul>' % corps
        elif comptes[cle]:
            contenu = etat_html('vide', '%d à voir dans la liste complète' % comptes[cle], 'Ils sont plus anciens que les éléments affichés ici.')
        elif data['complet']:
            contenu = etat_html('vide', 'Rien à relire dans %s' % ('le travail préparé' if cle == 'tout' else 'les ' + libelle.lower()), 'Toutes les sources ont répondu. Un nouveau projet apparaîtra ici dès qu’il est prêt.')
        else:
            contenu = etat_html('partiel', 'Liste incomplète', 'Une source n’a pas pu être lue : ce n’est pas un zéro certain.')
        suite = ''
        if comptes[cle] > PREMIERS:
            suite = '<p class="a89-suite"><a href="%s">Voir les %d éléments</a></p>' % (e(prefix + '/livrables?' + urlencode({'revue': 'to_review', 'recence': 'tout'}), quote=True), comptes[cle])
        panneaux.append('<div role="tabpanel" id="a89-liste-%s" aria-labelledby="a89-liste-onglet-%s" data-filtre-panneau="%s"%s tabindex="0">%s%s</div>' % (cle, cle, cle, '' if cle == actif else ' hidden', contenu, suite))
    pannes = [s for s in data['sources'] if s['state'] in ('unavailable', 'partial')]
    anciennes = [s for s in data['sources'] if s['state'] == 'stale']
    alerte = ''
    if pannes:
        alerte = etat_html('partiel', 'Source indisponible : liste incomplète', ' · '.join('%s : %s' % (s['label'], s['message']) for s in pannes[:3]))
    elif anciennes:
        alerte = etat_html('partiel', 'Une source n’est pas à jour', ' · '.join('%s : %s' % (s['label'], s['message']) for s in anciennes[:3]))
    vieux = data['counts']['a_relire_anciens']
    note = ('<p class="a89-note"><a href="%s">%d document(s) de plus de %d jours non relus</a> : ils restent accessibles.</p>' % (
        e(prefix + '/livrables?' + urlencode({'revue': 'to_review', 'recence': 'tout'}), quote=True), vieux, jours)) if vieux else ''
    return alerte + filtres('Nature du travail préparé', entrees, actif, 'a89-liste') + ''.join(panneaux) + note


def travail_carte(desk, prefix):
    try:
        from .livrables5689 import droits_requete, revision
        hidden, droits = droits_requete()
        jeton = revision(desk, hidden, droits)
    except Exception:   # noqa: BLE001 — sans jeton, pas d'actualisation automatique
        jeton = ''
    return ('<section class="c530-card a89-travail" id="a89-travail" aria-labelledby="a89-t-travail" data-revision="%s"><div class="c530-head"><h2 id="a89-t-travail">%sTravail préparé</h2>'
            '<a class="ax-btn ghost" href="%s">Tous les documents</a></div><div id="a89-travail-zone">%s</div><p class="a89-pied">Rien n’est envoyé sans votre validation.</p></section>') % (
        e(jeton, quote=True), icone('document'), e(prefix + '/livrables', quote=True), travail_html(desk, prefix))


def commande_html(prefix):
    """Une seule commande IA en tête : elle ouvre le Pilote, qui reprend le contexte de la page. La présence de l'IA est dite par un label écrit, pas par la seule couleur."""
    return ('<button type="button" class="ax-btn a89-mission" data-a89-pilote>%s<span>Confier une mission</span></button>' % icone('ia'))


def incidents_html(desk, prefix):
    """Incidents techniques : rangés à part, jamais au-dessus du travail. Le détail reste à un clic, avec le bouton de chaque point."""
    from .sante5636 import panel_html
    return panel_html(desk, prefix)


def bandeaux_html(bandeaux):
    """Bandeaux d'état (IA injoignable, plafond, sauvegarde) : rangés sous un titre écrit, à côté du travail, jamais devant lui. Ils ne se masquent pas."""
    return ('<section class="a89-incidents" aria-labelledby="a89-t-incidents"><h2 id="a89-t-incidents">%sIncidents techniques</h2>%s</section>' % (icone('vigilance'), bandeaux)) if bandeaux.strip() else ''


def acces_html(prefix):
    """Accès secondaires : rangés en bas, trouvables aussi dans Toutes les fonctions et par Ctrl+K."""
    liens = (('/facturation', 'Honoraires et facturation', 'facture'), ('/mon-style', 'Style du cabinet', 'modifier'), ('/diagnostic', 'Pourquoi rien n’est produit ?', 'aide'))
    return '<div class="a89-acces" role="navigation" aria-label="Accès secondaires">%s</div>' % ''.join('<a href="%s">%s<span>%s</span></a>' % (e(prefix + url, quote=True), icone(nom), e(texte)) for url, texte, nom in liens)


def resume_briefing(desk):
    """Début du dernier briefing, en texte simple : deux lignes, jamais le rapport. Rien de produit : rien d'affiché, pas de résumé inventé."""
    from . import routines520
    try:
        dernier = routines520.latest(desk, 'briefing')
    except Exception:   # noqa: BLE001 — le résumé est facultatif
        return ''
    if not dernier or not dernier.get('text'):
        return ''
    texte = re.sub(r'\s+', ' ', re.sub(r'[*_#>`]+', ' ', dernier['text'])).strip()
    if len(texte) > RESUME:
        texte = texte[:RESUME].rsplit(' ', 1)[0] + ' …'
    return ('<p class="a89-resume"><strong>Briefing du matin.</strong> %s <button type="button" class="ax-btn ghost" data-tab-open="briefing">Lire le compte rendu</button></p>' % e(texte))


def routines_bloc(barre, routines, resume=''):
    """Bloc Routines : lignes compactes (état, dernier passage, Voir, Lancer) ; les comptes rendus complets s'ouvrent dans un panneau, à la demande (§ 11)."""
    return ('%s%s<section class="c530-card a89-comptes" id="c530-routines" aria-labelledby="c530-t-routines-court"><h2 class="c530-sr" id="c530-t-routines-court">Comptes rendus</h2>'
            '<dialog class="ui89-panneau" id="a89-rapports" aria-labelledby="c530-t-routines"><button type="button" class="ax-btn ghost ui89-fermer" data-ui89-ferme>%s<span>Fermer</span></button>%s</dialog></section>') % (
        barre, resume, icone('fermer', taille=16), routines)


def _rangement(prefs, blocs):
    ordre = [k for k in prefs['ordre'] if k not in prefs['masques'] and blocs.get(k)]
    cote = [k for k in ordre if k in COTE]
    if prefs['perso']:
        return [k for k in ordre if k not in COTE and k != 'style'], cote, []
    return [k for k in ordre if k not in COTE and k not in BAS and k != 'style'], cote, [k for k in ordre if k in BAS]


def _item(bloc, html):
    return '<div class="t88-item" data-bloc="%s">%s</div>' % (bloc, html)


def pause_html(desk):
    """5.6.92 : la pause générale de l'IA reste dans l'en-tête, visible même si le bloc Routines est masqué ou replié."""
    en_pause = bool(desk.settings('missions567:pause', False))
    return ('<button type="button" class="ax-btn %s a92-pause" id="a92-pause" data-paused="%s" aria-pressed="%s">%s</button>' % (
        'warn' if en_pause else 'ghost', '1' if en_pause else '0', 'true' if en_pause else 'false', e('IA en pause — reprendre' if en_pause else 'Pause de l’IA')))


def page(desk, prefix, owner, morceaux):
    """Assemble la page. ``morceaux`` vient de ``cockpit530.page`` : en-tête, bandeaux, blocs, composeur masqué, panneau, tiroir."""
    from . import tableau5688
    from .i18n5631 import role as role_courant
    prefs = tableau5688.charger(desk, owner)
    blocs = morceaux['blocs']
    if 'actuellement' not in blocs and 'actuellement' not in prefs['masques']:     # 5.6.95 (A01) : encadré « Actuellement », calculé sans IA
        from .actuellement5695 import bloc_html
        blocs = dict(blocs, actuellement=bloc_html(desk, prefix, owner, role_courant()))
    from .actuellement5695 import PLIABLES, pli_html, plis_fermes     # 5.6.95 (A03) : blocs du bas repliables, état mémorisé par personne
    fermes = plis_fermes(desk, owner)
    blocs = {k: (pli_html(k, v, k in fermes) if k in PLIABLES else v) for k, v in blocs.items()}
    plis_js = ('<script defer src="%s"></script>' % e(prefix + '/static/v5695-aujourdhui.js', quote=True)) if any(blocs.get(k) for k in PLIABLES) else ''
    principal, cote, bas = _rangement(prefs, blocs)
    tete = ''
    if principal and principal[0] == 'actuellement':     # premier (par défaut) : en haut de la page, sur toute la largeur, avant Travail préparé
        tete, principal = _item('actuellement', blocs['actuellement']), principal[1:]
    rang = ''
    if list(prefs['ordre']) == list(tableau5688.IDS):     # 5.6.96 (I01) : ordre par défaut (blocs masqués compris) ; un ordre choisi par la personne est conservé tel quel
        rang, principal, cote = rang_html(desk, prefix, owner, role_courant(), blocs, principal, cote)
    try:   # 5.6.97 (lot documents-mission, § 17) : « Dossiers sans prochaine action » et « Attentes à relancer », sous le rang, sans IA
        from . import shell501
        from .mission_web5697 import aujourdhui_html
        rang += aujourdhui_html(desk, prefix, owner, role_courant(), str((getattr(shell501._ctx, 'args', None) or {}).get('mes') or '') == '1')
    except Exception:   # noqa: BLE001 — l'accueil s'affiche toujours
        import logging
        logging.getLogger(__name__).exception('suivi des dossiers 5.6.97')
    accent = (' data-accent="%s"' % e(prefs['accent'], quote=True)) if prefs['accent'] else ''
    accent += (' data-densite="%s"' % e(prefs['densite'], quote=True)) if prefs.get('densite', 'confort') != 'confort' else ''   # 5.6.96 (U02, lot affichage) : densité par compte
    style = ''
    if blocs.get('style') and 'style' not in prefs['masques']:
        style = '<details class="a89-plus"><summary>Style du cabinet</summary>%s</details>' % blocs['style']
    col = ''.join(_item(k, blocs[k]) + (style + morceaux['composeur'] if k == 'routines' else '') for k in principal)
    if morceaux['composeur'] not in col:
        col = style + morceaux['composeur'] + col
    pause = pause_html(desk)
    note = ('<p class="c530-note">%d bloc(s) masqué(s) sur cette page. Vous pouvez les rétablir dans « Personnaliser cette page ».</p>' % len(prefs['masques'])) if prefs['masques'] else ''
    bandeau, carte, accueil_js = entete_accueil(desk, prefix, owner, role_courant(), morceaux['date'])     # 5.6.96 (F01-F04, I01) : salutation, heure, météo
    return ('<div class="c530 a89" id="c530" data-prefix="%s"%s><header class="c530-top a89-tete f96-tete"><div class="f96-identite"><h1>Aujourd’hui</h1>%s</div>'
            '<div class="a89-commandes">%s<div class="c530-statusbar" id="c530-header">%s</div>%s</div>%s</header>'
            '%s%s<div class="a89-layout"><div class="a89-principal">%s<div class="c530-cols c5657-cols">%s</div></div><div class="a89-cote" role="complementary" aria-label="Journée et activité">%s%s%s</div></div>%s%s%s%s%s%s%s%s</div>') % (
        e(prefix, quote=True), accent, bandeau, pause, morceaux['entete'], commande_html(prefix), carte,
        tete, rang, travail_carte(desk, prefix), col, morceaux['matin'], ''.join(_item(k, blocs[k]) for k in cote), bandeaux_html(morceaux['bandeaux']),
        ''.join(_item(k, blocs[k]) for k in bas), note, incidents_html(desk, prefix), tableau5688.panneau(prefs, role_courant(), desk), acces_html(prefix), morceaux['tiroir'], plis_js, accueil_js)


def rang_html(desk, prefix, owner, role, blocs, principal, cote):
    """5.6.96 (I01) : rang sous « Actuellement » : à gauche « À décider / À relire » (trois premières actions, puis la file complète dépliable), à droite
    « Ma journée » (bloc existant, précédé des échéances confirmées du jour), dans une grille ``minmax(0, 2fr) minmax(0, 1fr)``. Les blocs gardent leur
    identifiant (masquage et personnalisation de ``tableau5688`` inchangés) ; un bloc masqué n'y figure pas. Un incident rend la disposition d'avant."""
    try:
        from . import accueil_web5696 as acc
        gauche = droite = ''
        if 'file' in principal:
            gauche = _item('file', acc.decider_html(desk, prefix, owner, role, blocs['file']))
        if 'journee' in cote:
            droite = _item('journee', acc.journee_html(desk, prefix, owner, role, blocs['journee']))
    except Exception:   # noqa: BLE001 — l'accueil s'affiche toujours
        import logging
        logging.getLogger(__name__).exception('rang de l’accueil 5.6.96')
        return '', principal, cote
    if not (gauche or droite):
        return '', principal, cote
    principal = [k for k in principal if not (k == 'file' and gauche)]
    cote = [k for k in cote if not (k == 'journee' and droite)]
    if 'routines' in principal and blocs.get('routines'):   # 5.6.98 : « Routines du cabinet » juste sous « Actuellement », au-dessus de la file
        gauche = _item('routines', blocs['routines']) + gauche
        principal = [k for k in principal if k != 'routines']
    if gauche and droite:   # 5.6.98 : routines puis file dans une même colonne, à côté de « Ma journée » : plus de vide sous la file
        gauche = '<div class="f98-gauche">%s</div>' % gauche
    return '<div class="f96-rang%s">%s%s</div>' % ('' if gauche and droite else ' f96-rang-seul', gauche, droite), principal, cote


def entete_accueil(desk, prefix, owner, role, date_texte):
    """5.6.96 (lot accueil) : bandeau « Bonjour {Prénom}, nous sommes le…, il est… » et options d'accueil (F01, F02), cellule météo à droite de
    l'en-tête (F04) et script de l'heure, de la météo et des fenêtres flottantes (I02). Une erreur ne cache jamais la page : la date seule revient."""
    try:
        from . import accueil_web5696 as acc
        bandeau = acc.bandeau_html(desk, owner) + acc.options_html(desk, owner)
        return bandeau, acc.carte_html(desk, owner, role, prefix), '<script defer src="%s"></script>' % e(prefix + '/static/v5696-accueil.js', quote=True)
    except Exception:   # noqa: BLE001 — l'accueil s'affiche toujours
        import logging
        logging.getLogger(__name__).exception('bandeau de l’accueil 5.6.96')
        return '<p class="c530-date">%s</p>' % e(date_texte), '', ''
