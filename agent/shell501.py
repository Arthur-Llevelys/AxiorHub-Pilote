"""Gabarit unique 5.0.1 : menu latéral, barre du haut et assistant communs à TOUTES les pages.

Avant la 5.0.1, les pages Courriels, Documents, Échéances, Cabinet, Recherche… (modules web44x à web50x) avaient leur propre
barre de navigation en haut, alors que Agenda et tâches, Dossiers, Produire… utilisaient le menu latéral. Les deux gabarits
passent maintenant par ce module : même menu, même barre, même assistant, même thème.
"""
from html import escape as e
import threading

# 5.3.0 : menu réduit au poste de pilotage ; les rubriques historiques restent à un clic, regroupées.
PRIMARY = (('/aujourdhui', '◎', 'Aujourd’hui'), ('/courriels', '✉', 'Courriels'), ('/dossiers', '▦', 'Dossiers'), ('/planning', '◷', 'Agenda et tâches'), ('/production', '＋', 'Produire'))   # 5.6.84 : Honoraires et facturation dans Toutes les fonctions
RUBRIQUES = (('/documents','▤','Documents'),('/echeances','⚖','Échéances'),('/recherche','⌕','Recherche'),('/cabinet','▣','Cabinet'),('/mon-style','✎','Apprentissage et modèles'))

# Pages secondaires : l'entrée du menu qui reste surlignée.
ACTIVE = {'/mise-en-service':'/parametres', '/parametres/agents':'/parametres', '/parametres/agendas':'/parametres', '/agents-documents':'/production', '/parametres/proactivite':'/parametres', '/engagements':'/aujourdhui', '/veille':'/production', '/': '/', '/mail': '/', '/planning': '/planning', '/agenda': '/planning', '/taches': '/planning',
          '/verification': '/production', '/sources': '/production', '/modeles': '/production',
          '/progres': '/mon-style', '/mon-style': '/mon-style', '/autonomie': '/cabinet', '/tracabilite': '/cabinet', '/confort': '/parametres',
          '/atelier/reglages': '/parametres', '/fiche': '/dossiers', '/dossier': '/dossiers', '/chronologie': '/dossiers', '/documents/edit': '/documents',
          '/honoraires': '/cabinet', '/rendez-vous': '/cabinet', '/conflits': '/cabinet', '/prescriptions': '/cabinet',
          '/pilotage-mensuel': '/cabinet', '/rechercher': '/recherche', '/pieces': '/production', '/diagnostic': '/aujourdhui', '/ia-externe': '/parametres',
          # 5.6.43 (§ 20) : les onglets de Produire activent Produire
          '/studio': '/production', '/documents': '/production', '/missions-complexes': '/production', '/playbooks': '/production',
          '/outils-ia': '/production', '/projets': '/production'}

# Pages d'atelier sans entrée principale : regroupées sous « Outils » pour rester accessibles depuis tout écran.
TOOLS = (('/mise-en-service', 'Mise en service', 'Services réels, micro, Docker : contrôles et correctifs'),
         ('/parametres/agents', 'Agents et règles', 'Missions documentaires en langage naturel'),
         ('/agents-documents', 'Résultats des agents', 'Classement, agendas, brouillons et reprise'),
         ('/engagements', 'Engagements et suites', 'Promesses sourcées et missions en étapes'),
         ('/veille', 'Veille juridique', 'Nouveautés officielles datées'),
         ('/diagnostic', 'Pourquoi rien n’est produit ?', 'Services, erreurs, dossiers bloqués, connexions, recette'),
         ('/audience', 'Fiche d’audience', 'Conclusions, pièces attendues, note de plaidoirie, questions probables, projets à relire'),
         ('/missions-complexes', 'Missions complexes', 'Assignation, conclusions : sous-tâches par rôle, décisions, projet contrôlé'),
         ('/profils', 'Profils procéduraux', 'Registre daté des procédures approuvées par l’avocat'),
         ('/pieces', 'Pièces et bordereaux', 'Bordereau, pièces numérotées et tamponnées'),
         ('/verification', 'Vérifier', 'Citations juridiques, mentions obligatoires, relecture contradictoire'),
         ('/progres', 'Progrès', 'Apprentissage, tons par destinataire, règles, autonomie et traçabilité'),
         ('/modeles-word', 'Modèle de courrier', 'Modèle Word du cabinet pour « Nouveau courrier »'))
TOOL_PAGES = {'/mise-en-service', '/audience', '/missions-complexes', '/profils', '/parametres/agents', '/parametres/agendas', '/agents-documents', '/parametres/proactivite', '/engagements', '/veille', '/diagnostic', '/ia-externe', '/pieces', '/verification', '/sources', '/modeles', '/progres', '/autonomie', '/tracabilite', '/confort', '/modeles-word', '/atelier/reglages'}

AX_CSS = ('v440.css', 'v470.css', 'v480.css', 'v500.css')
ENHANCE_CSS = ('v300.css', 'v320.css', 'v330.css', 'v360.css', 'v363.css', 'v365.css', 'v370.css', 'v420.css', 'v430.css', 'v490.css')
AX_JS = ('v440.js', 'v470.js', 'v480.js', 'v500.js')
LEGACY_CSS = ('style.css', 'v15.css', 'v151.css', 'v160.css', 'v170.css', 'v180.css', 'v190.css', 'v210.css', 'v211.css', 'v230.css',
              'v364.css', 'v380.css', 'v390.css', 'v391.css', 'v392.css', 'v393.css', 'v400.css', 'v410.css', 'v420.css')
# 5.2.0 : une seule feuille par page, concaténation dans l'ordre exact de chargement (voir scripts/build-css.py).
BUNDLE = LEGACY_CSS + AX_CSS + ENHANCE_CSS + ('v520.css', 'v530.css', 'v550.css', 'v560.css', 'v561.css', 'v567.css', 'v568.css', 'v5613.css', 'v5614.css', 'v5625.css', 'v5627.css', 'v5628.css', 'v5631.css', 'v5632.css', 'v5634.css', 'v5636.css', 'v5637.css', 'v5650.css', 'v5651.css', 'v5652.css', 'v5653.css', 'v5670.css', 'v5671.css', 'v5673.css', 'v5674.css', 'v5675.css', 'v5676.css', 'v5679.css', 'v5680.css', 'v5681.css', 'v5682.css', 'v5683.css', 'v5684.css', 'v5686.css', 'v5688-securite.css', 'v5688-reversibilite.css', 'v5688-agenda.css', 'v5688-facturation.css', 'v5688-crm.css', 'v5688-cloud.css', 'v5688-societe.css', 'v5689-cabinet.css', 'v5689-livrables.css', 'v5689-dossier.css', 'v5689-carbon-jetons.css', 'v5689-carbon.css', 'v5689-ia.css', 'v5694-traces.css', 'v5694-voix.css', 'v5695-redaction.css', 'v5695-aujourdhui.css', 'v5696-accueil.css', 'v5696-affichage.css', 'v5696-courriels.css', 'v5697-fiabilite.css', 'v5697-dossiers.css', 'v5697-chatgpt.css', 'v5697-agenda.css', 'v5697-gmail.css', 'v5697-mission.css', 'v5697-sources.css', 'v5697-gestion.css', 'v5698.css', 'v5699.css', 'v56100-reactivite.css', 'v56100-dossiers.css', 'v56100-accueil.css', 'v56100-produire.css', 'v56100-lecteur.css', 'v56101-familles.css', 'v56101-securite.css', 'v56101-aujourdhui.css', 'v56101-volet.css', 'v56102-pilote.css', 'v56102-navigation.css') + ('v56103-liaison.css',)   # 5.6.103 (lot L) : liaison entre postes
BUNDLE += ('v56103-fichiers.css',)   # 5.6.103 (lot F) : page « Fichiers »
BUNDLE += ('v56103-tableau.css',)   # 5.6.103 (lot T) : volet « Tableau blanc » de la messagerie
BUNDLE += ('v56103-comptes.css',)   # 5.6.103 (intégration) : étapes de comptes Google et Microsoft
BUNDLE += ('v56106-densite.css',)   # 5.6.106 (lot D) : densité de toutes les pages (variables --ax-*, body[data-densite]) et identité du cabinet ; avant les feuilles propres aux pages
BUNDLE += ('v56106-redaction.css',)   # 5.6.106 (lot R) : rédaction compacte d'un courriel
BUNDLE += ('v56106-envoi.css',)   # 5.6.106 (lot E) : options d'envoi, bandeau « Annuler », envois programmés
BUNDLE += ('v56106-liste.css',)   # 5.6.106 (lot L) : Courriels dense (liste, actions groupées, dossiers, onglets à 390 px)
BUNDLE += ('v56106-lecteur.css',)   # 5.6.106 (lot V) : lecteur (cadre isolé, bandeaux, pièces, fil), volet du résumé, formulaires tâche et agenda
BUNDLE += ('v56106-vision.css',)   # 5.6.106 (lot I) : joindre une image ou une page (assistant), aperçu masqué, réglages des images
BUNDLE += ('v56106-routine.css',)   # 5.6.106 (lot O) : assistant « Créer une routine à partir du message », routines sur message d'Aujourd'hui
BUNDLE += ('v56106-fraude.css',)   # 5.6.106 (lot F) : pastille et bandeau « courriel potentiellement frauduleux »
BUNDLE += ('v56107-demo.css',)   # 5.6.107 (lot M) : bandeau du mode Démo, bulles de la visite guidée, « Essayer la démo »
BUNDLE += ('v56108-assistance.css',)
BUNDLE += ('v56108-accueil.css',)   # 5.6.108 (lot A) : Aujourd'hui (dossier lisible, rang, Ma journée), volet de l'assistant (Envoyer collé en bas), reçu   # 5.6.108 (lot T) : Assistance métier en cinq parcours ; Accès mobile (commandes atteignables clavier ouvert)
BUNDLE += ('v56108-agenda.css',)   # 5.6.108 (lot G) : agenda (barre visible au défilement, éditeur lisible, dates à vérifier), Produire et Dossiers denses
BUNDLE += ('v56108-questionnaire.css',)   # 5.6.108 (lot Q) : questionnaire de l'acte, formulaire et aperçu côte à côte
BUNDLE += ('v56108-sync.css',)   # 5.6.108 (lot S) : synchronisation des postes (indicateur, conflits, parcours)
BUNDLE += ('v56108-conseil.css',)   # 5.6.108 (lot K) : conseil de modèles (réglages, annonce, trace dépliable)
BUNDLE += ('v56108-editeur.css',)   # 5.6.108 (lot W) : éditeur intégré WordCraft et « Ouvrir dans Word / LibreOffice »
BUNDLE += ('v56108-fichiers.css',)   # 5.6.108 (lot F) : onglet Fichiers du dossier, remplir un PDF, outils PDF
BUNDLE += ('v56108-signaturepdf.css',)   # 5.6.108 (lot Z) : SignaturePDF, signature par lien (réglage, bouton, page d'un PDF)
BUNDLE += ('v56108-ia-locale.css',)   # 5.6.108 (lot L) : assistant « Configurer l'IA locale » (Paramètres › IA, guide de démarrage)
BUNDLE += ('v56109-repertoire.css', 'v56109-pieces.css')   # 5.6.109 (lot L) : répertoire du dossier trié (F02), barre des pièces jointes et classement du lecteur (F01)
BUNDLE += ('v56109-fiche.css', 'v56109-honoraires.css')   # 5.6.109 (lot D) : fiche Conseil / Contentieux (F04), prestations et honoraires du cabinet (F05)
BUNDLE += ('v56109-conso.css',)   # 5.6.109 (lot M) : section « Consommation et coûts » de Paramètres › Modèles d'IA (F18)
BUNDLE += ('v56109-reanalyse.css',)   # 5.6.109 (lot A) : bloc « Analyse du dossier » de la fiche (F03)
BUNDLE += ('v56109-sources.css',)   # 5.6.109 (lot S) : état des sources publiques (F11), services étrangers (G21), registre étranger et documents publics (G18-G19)
BUNDLE += ('v56109-navigateur.css',)   # 5.6.109 (lot O) : modèle du navigateur, acquittement de la prise de main, accès à ce poste (G13, G24, F12)
BUNDLE += ('v56109-historique.css', 'v56109-apparence.css')   # 5.6.109 (lot H) : Historique de l'assistant et discussions (F09), couleur du menu de gauche (F10, après Carbon)
BUNDLE += ('v56109-bland.css',)   # 5.6.109 (lot B) : carte Bland, demande à confirmer, routines « Mes agents IA »
BUNDLE += ('v56109-produire.css',)   # 5.6.109 (lot V) : Produire en trois vues, livrables structurés, modèles Word du cabinet (F08), éditeurs Craft facultatifs (F16)
BUNDLE += ('v56109-simulation.css',)   # 5.6.109 (lot N) : onglet « Simulation de décision » du dossier (F14)
BUNDLE += ('v56110-dictee.css',)   # 5.6.110 (lot D) : carte « Dictée sur ce poste » (Paramètres › Voix)
BUNDLE += ('v56110-documents.css',)   # 5.6.110 (lot S) : carte « Serveur de documents sur ce poste » (Paramètres › Documents et procédure)
BUNDLE += ('v56109-fonds.css',)   # 5.6.109 (lot Q) : « Fonds juridique local » (G23) et « Fichiers de ce poste » (F13) dans les Paramètres
BUNDLE += ('v56109-relations.css',)   # 5.6.109 (lot G2) : partage client, appels, remplacements, kit d'audience (dossier et fiche contact)
BUNDLE += ('v56109-gestion.css',)   # 5.6.109 (lot G1) : Préfacturation, Assistance régulière (fiche client), Fonds, frais et débours (dossier)
BUNDLE += ('v56109-parcours.css',)   # 5.6.109 (lot G4) : cartes « Relever mes messages procéduraux », « Offre et PDF joint », « Documents publics des sociétés » (Mes routines | Mes agents IA)
BUNDLE += ('v56109-operations.css',)   # 5.6.109 (lot G3) : « À finaliser » (Corporate, Cession), Suites et exécution (fiche), formalités supervisées, RIB CARPA (vue financière)
BUNDLE += ('v56111-activite.css',)   # 5.6.111 (lot C, L02) : indicateur d'activité htmx (#ax-occupe, role="status") ; aucun style injecté par htmx
BUNDLE += ('v56111-portail.css',)   # 5.6.111 (lot P) : « Envois sur le portail » (onglet Juridiction du dossier : préparation, code, récapitulatif, accusé)
BUNDLE += ('v56111-ergonomie.css',)   # 5.6.111 (lot U) : écarts d'ergonomie mesurés (contrastes rendus, cibles de 24 px, 375 px) et carte « Précontrôle du poste »
BUNDLE += ('v56111-conversation.css',)   # 5.6.111 (lot F) : onglet « Conversation IA » du dossier (grille, composeur, volet Document / Navigateur)
BUNDLE += ('v56112.css',)   # 5.6.112 : survol lisible des onglets du contenu ; page « Conversations IA » et onglet du dossier (Open WebUI dans un cadre)
BUNDLE_FILE = 'app520.css'

_ctx = threading.local()
from .about560 import ATTRIBUTION


def _version():
    from . import __version__
    return __version__


def set_context(cfg, args=None, chemin=None):
    """Mémorise la configuration de la requête en cours (pour l'assistant : dossiers, dictée), ses paramètres (5.6.89 : retour contextuel) et son chemin réel (le gabarit ne reçoit que l'entrée de menu active)."""
    _ctx.cfg = cfg
    _ctx.args = args if args is not None else getattr(_ctx, 'args', {})
    _ctx.chemin = chemin if chemin is not None else getattr(_ctx, 'chemin', '')
    _ctx.drapeaux = None


def drapeaux():
    """5.6.89 : drapeaux de l'interface, lus une fois par requête."""
    if getattr(_ctx, 'drapeaux', None) is None:
        from .ui_drapeaux5689 import de_config
        _ctx.drapeaux = de_config(context())
    return _ctx.drapeaux


def context():
    return getattr(_ctx, 'cfg', None)


def active_primary(path):
    """Accès principal actif : table historique, sinon la destination canonique de la fonction affichée (5.6.43, § 20.3)."""
    if path in ACTIVE:
        return ACTIVE[path]
    try:
        from .fonctions5631 import INDEX, for_path
        from .navigation5643 import PRIMARY as MAIN, canonical
        fid = for_path(path)
        parent = canonical(fid) if fid else ''
        if parent in MAIN and parent in INDEX:
            return INDEX[parent][2].split('?', 1)[0]
    except Exception:   # noqa: BLE001 — l'état actif ne bloque jamais le rendu
        pass
    return path


def _links(prefix, entries, active):
    from .ui_navigation5689 import icone_menu       # 5.6.89 : SVG locaux sous le thème ; sinon le signe d'origine
    carbon = drapeaux().get('theme')
    return ''.join(
        '<a href="' + e(prefix + p) + '" aria-label="' + e(label) + '" title="' + e(label) + '"' + (' aria-current="page"' if p == active else '') +
        '><span class="ws-nav-icon" aria-hidden="true">' + (icone_menu(p, icon) if carbon else e(icon)) + '</span><span class="ws-nav-label">' + e(label) + '</span></a>'
        for p, icon, label in entries)


def _primary():
    # 5.6.31 (LG02) : libellés traduits selon la langue de la requête ; routes et icônes inchangées.
    from .i18n5631 import t
    keys = {'/facturation': 'facturation', '/aujourdhui': 'aujourdhui', '/dossiers': 'dossiers', '/courriels': 'courriels', '/planning': 'planning', '/production': 'production', '/parametres': 'parametres'}
    return tuple((p, icon, t('fn.%s.label' % keys[p])) for p, icon, _ in PRIMARY)


def nav_html(prefix, active_path):
    """5.6.31 (NV01) : six accès de travail fréquents, puis un seul accès « Toutes les fonctions » (remplace Rubriques et Outils).
    5.6.43 (§ 22) : « Talk », septième accès facultatif, juste après « Agenda et tâches », pour l'utilisateur qui l'a activé."""
    from .fonctions5631 import panel_html
    from .i18n5631 import current, role, th
    active = active_primary(active_path)
    entries = list(_primary())
    try:
        from .i18n5631 import _ctx as _lang_ctx
        from .talk5643 import visible as _talk_visible
        if _talk_visible(context(), getattr(_lang_ctx, 'owner', '') or 'cabinet'):
            at = next(i for i, x in enumerate(entries) if x[0] == '/planning') + 1
            from .i18n5631 import t as _t
            entries.insert(at, ('/talk', '☏', _t('fn.talk.label')))
    except Exception:   # noqa: BLE001 — l'entrée facultative ne bloque jamais le menu
        pass
    try:   # 5.6.50 : messagerie interne du cabinet, juste après les courriels (pastille « non lus » posée par v5650.js)
        from .i18n5631 import t as _t50
        at50 = next(i for i, x in enumerate(entries) if x[0] == '/courriels') + 1
        entries.insert(at50, ('/messagerie', '❝', _t50('fn.messagerie.label')))
    except Exception:   # noqa: BLE001
        pass
    try:   # 5.6.112 : « Conversations IA » (Open WebUI du cabinet), juste après la messagerie instantanée
        from .i18n5631 import t as _t112
        at112 = next(i for i, x in enumerate(entries) if x[0] == '/messagerie') + 1
        entries.insert(at112, ('/conversations-ia', '✦', _t112('fn.conversations_ia.label')))
    except Exception:   # noqa: BLE001
        pass
    if relire_separe():   # 5.6.102 (lot N) : « À relire », accès principal distinct juste après « Aujourd'hui » (nouvelle interface)
        from .i18n5631 import t as _t102
        entries.insert(next((i + 1 for i, x in enumerate(entries) if x[0] == '/aujourdhui'), 0), ('/a-relire', '☑', _t102('fn.a_relire.label')))
    try:   # 5.6.109 (lot H, F10) : « Discussions IA » épinglé par le compte (Paramètres › Apparence) ; ouvre la même vue que « Historique » de l'assistant
        from .apparence56109 import epingle as _epingle109
        from .i18n5631 import _ctx as _lang109
        if _epingle109(context(), getattr(_lang109, 'owner', '') or 'cabinet'):
            entries.append(('/assistant?vue=historique', '🕘', 'Discussions IA'))
    except Exception:   # noqa: BLE001 — l'épingle facultative ne bloque jamais le menu
        pass
    from .i18n5631 import t as _t82
    settings = ('<a class="m82-settings" href="' + e(prefix + '/parametres') + '"' + (' aria-current="page"' if active == '/parametres' else '') + '><span aria-hidden="true">⚙</span> ' +
                e(_t82('fn.parametres.label')) + '</a>')     # 5.6.82 : hors des six entrées de travail, toujours à portée
    return ('<nav aria-label="' + th('nav.primary') + '" class="ws-primary">' + _links(prefix, entries, active) + '</nav>' +
            panel_html(prefix, active_path, context(), role(), current()) + settings)


def relire_separe(cfg=None, owner=None):
    """5.6.102 (lot N) : « À relire » est-il un accès principal distinct (``/a-relire``) ? Oui avec la nouvelle interface (interrupteur
    ``interface56100``, préférence du compte d'abord) ; non en présentation historique, où Courriels garde son onglet « À relire » comme
    avant. Lecture seule de la base d'état (jamais d'écriture pendant un GET) ; une erreur de lecture suit le défaut (nouvelle interface)."""
    try:
        from .interface56100 import active
        if owner is None:
            from .i18n5631 import _ctx as _lang_ctx
            owner = getattr(_lang_ctx, 'owner', '')
        return bool(active(_Desk53(cfg if cfg is not None else (context() or {})), owner or 'cabinet'))
    except Exception:   # noqa: BLE001 — la présentation par défaut (active) s'applique
        return True


class _Desk53:
    """Lecture du réglage d'épinglage sans ouvrir de bureau complet (base de l'état, lecture seule)."""
    def __init__(self, cfg):
        self.c = cfg

    def settings(self, key, default=None):
        import json
        import sqlite3
        from pathlib import Path
        db = Path(self.c.get('state_dir', '.')) / 'desk.sqlite3'
        if not db.is_file():
            return default
        con = sqlite3.connect('file:%s?mode=ro' % db.as_posix(), uri=True, timeout=2)
        try:
            row = con.execute('SELECT value FROM settings WHERE key=?', (key,)).fetchone()
        except sqlite3.Error:
            return default
        finally:
            con.close()
        return json.loads(row[0]) if row else default


def tools_html(prefix, path):
    """Conservé pour compatibilité : les outils sont désormais dans « Toutes les fonctions » (registre fonctions5631)."""
    return ''


def topbar_html(prefix, title, path='', retour=True):
    from .fonctions5631 import for_path
    from .i18n5631 import menu_html, th
    from .ui_navigation5689 import retour_html      # 5.6.89 (§ 14) : retour contextuel commun à tous les détails
    fid = for_path(path) if path else ''
    help_link = '<a class="l5631-help" href="' + e(prefix + '/guide-utilisation' + ('?fonction=' + fid if fid else '')) + '">' + th('top.help') + '</a>'
    carbon = drapeaux().get('theme')                # 5.6.89 : le signe reste dans le code (v320.js l'écrit), masqué sous le thème ; le SVG local prend sa place
    from .ui_components5689 import icone
    menu_svg, theme_svg, cherche_svg = (icone('menu'), '<span hidden aria-hidden="true">☾</span>' + icone('sombre', classe='ui89-si-clair') + icone('clair', classe='ui89-si-sombre'), icone('recherche', taille=16) + ' ') if carbon else ('', '', '')
    return ('<header class="ws-topbar"><div class="ws-topbar-leading"><button type="button" class="ws-icon-button" id="ws-sidebar-toggle" '
            'aria-controls="ws-sidebar" aria-expanded="true" aria-label="' + th('top.fold') + '" title="' + th('top.fold') + '">' + (menu_svg or '<span aria-hidden="true">☰</span>') + '</button>' +
            (retour_html(prefix, path, getattr(_ctx, 'args', {})) if retour else '') +
            '<span class="ws-ariane">' + th('top.workspace') + ' / ' + e(title) + '</span></div><div class="ws-topbar-actions"><button type="button" class="ws-icon-button" '
            'id="ws-theme-toggle" aria-label="' + th('top.dark') + '" title="' + th('top.dark') + '">' + (theme_svg or '<span aria-hidden="true">☾</span>') + '</button>'
            '<a href="' + e(prefix + '/missions') + '">' + th('top.missions') + '</a><a href="' + e(prefix + '/rechercher') + '" data-m82-quick title="Ctrl+K" aria-keyshortcuts="Control+K">' + (cherche_svg or '⌕ ') + th('top.search') + ' <kbd class="m102-kbd" data-m102-kbd aria-hidden="true">Ctrl+K</kbd></a>' +   # 5.6.102 : raccourci affiché en petit
            _sync108(prefix) +   # 5.6.108 (lot S5) : indicateur de synchronisation des postes (seulement quand un espace partagé existe)
            help_link + menu_html(prefix) + '</div></header>')


def _sync108(prefix):
    try:
        from .sync56108_web import indicateur_entete
        return indicateur_entete(context(), prefix)
    except Exception:   # noqa: BLE001 — l'en-tête s'affiche toujours
        return ''


def head_html(title, prefix, css=(), head=''):
    # 5.5.0 : « ?v=version » change l'adresse à chaque version ; ni le navigateur, ni un proxy, ni l'application mobile ne gardent l'ancienne feuille.
    from . import __version__ as version
    links = ''.join('<link rel="stylesheet" href="' + e(prefix) + '/static/' + name + '?v=' + e(version) + '">' for name in (BUNDLE_FILE,) + tuple(c for c in css if c not in BUNDLE))
    from .i18n5631 import current
    return ('<!doctype html><html lang="' + current() + '"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            '<title>' + e(title) + ' · AxiorHub Pilote</title>' + links + head + '</head><body>')


def sidebar_html(prefix, active_path):
    from .i18n5631 import th
    # 5.6.106 (lot D) : en tête, « [logo] Nom du cabinet » (pastille aux initiales sans logo), la marque AxiorHub dessous en petit ;
    # sans nom ni logo, ou sous la présentation précédente, la marque d'origine reste seule. Script de densité chargé sur toutes les pages.
    from .identite56106 import bloc_html as _identite106
    identite = _identite106(prefix)
    marque = ('<div class="brand id106-brand">' + identite + '</div>') if identite else (
        '<div class="brand"><div class="ws-brand-lockup"><img src="' + e(prefix) + '/static/axiorhub-icon.png" width="38" height="38" alt="">'
        '<span class="ws-brand-name">AxiorHub</span></div><span class="ws-brand-subtitle">' + th('side.subtitle') + '</span></div>')
    try:   # 5.6.107 (lot M) : bandeau permanent du mode Démo (démo seulement), dans le menu latéral : il ne recouvre jamais une action ; « Essayer la démo » à l'accueil d'une installation neuve
        from .demo56107 import bandeau_html as _bandeau107, carte_essai_html as _essai107
        _demo107 = _bandeau107(prefix, context()) + _essai107(prefix, context(), getattr(_ctx, 'chemin', '') or active_path, getattr(_ctx, 'args', {}))
    except Exception:   # noqa: BLE001 — un défaut du bandeau ne bloque jamais une page
        _demo107 = ''
    try:   # 5.6.109 (lot H, F10) : couleurs du menu validées pour ce compte (attributs data-ax109-*, aucun style en ligne) ; sans réglage : rendu inchangé
        from .apparence56109 import attributs_sidebar as _couleurs109
        from .i18n5631 import _ctx as _lang109
        _attributs109 = _couleurs109(context(), getattr(_lang109, 'owner', '') or 'cabinet')
    except Exception:   # noqa: BLE001 — réglage illisible : le rendu du produit
        _attributs109 = ''
    return ('<aside id="ws-sidebar"' + _attributs109 + '>' + marque + nav_html(prefix, active_path) + _demo107 +
            '<p class="ws-about">' + th('side.version', version=_version()) + ' · ' + e(ATTRIBUTION) + '<br><a href="' + e(prefix) + '/a-propos">' + th('side.about') + '</a></p>'
            '<script defer src="' + e(prefix) + '/static/v56106-densite.js"></script>'
            '<script defer src="' + e(prefix) + '/static/v56109-apparence.js"></script><script defer src="' + e(prefix) + '/static/v56109-historique.js"></script></aside>')   # 5.6.109 (lot H) : apparence du menu (F10), Historique dans l'assistant (F09)


def render(title, body, prefix, csrf, path, cfg=None, css=(), scripts='', head='', selected_matter='', toast='', page_title=True):
    """Page complète : menu latéral + barre + contenu, puis assistant et mécanismes communs (``ui_helpers.enhance``)."""
    cfg = cfg or context() or {}
    matters = []
    try:
        from .common import load_matters
        matters = load_matters(cfg) if cfg.get('matters_file') else []
    except Exception:
        matters = []
    from .dictee56110 import audio_actif as _audio56110
    audio = _audio56110(cfg)   # 5.6.110 (lot D) : passerelle activée ou dictée sur ce poste active
    # 5.6.101 (lot F, F02) : barre d'onglets de la famille de la page (les onglets de Produire deviennent ceux de la famille Produire) et
    # marqueur du compteur d'usage (F05 : signalé en POST par v56101-familles.js, jamais écrit pendant ce GET)
    from .familles56101 import chemin_de_page, onglets_html
    from .i18n5631 import role as _role101
    body = onglets_html(prefix, chemin_de_page(path), _role101()) + body
    try:
        from .usage56101 import balise_html as _usage101
        _balise101 = _usage101(prefix, path, cfg)
    except Exception:   # noqa: BLE001 — le compteur facultatif ne bloque jamais une page
        _balise101 = ''
    if _balise101 or 'f101-onglets' in body or 'data-f101-reglage' in body:
        scripts = (scripts or '') + _balise101 + '<script defer src="' + e(prefix) + '/static/v56101-familles.js"></script>'
    try:   # 5.6.101 (lot M) : écoute du lecteur pour la mesure du temps, servie seulement à la personne qui l'a activée (lecture seule)
        from .chrono56101 import script_html as _chrono101
        scripts = (scripts or '') + _chrono101(cfg, prefix)
    except Exception:   # noqa: BLE001 — la mesure facultative ne bloque jamais une page
        pass
    html = (head_html(title, prefix, css, head) + toast + sidebar_html(prefix, path) + '<main id="ws-main">' + topbar_html(prefix, title, path, 'data-ui89-retour' not in body) + body + '</main>' + scripts +
            _messages_json() + '<script defer src="' + e(prefix) + '/static/v5631.js"></script><script defer src="' + e(prefix) + '/static/v5632.js"></script><script defer src="' + e(prefix) + '/static/v5634.js"></script><script defer src="' + e(prefix) + '/static/v5636.js"></script><script defer src="' + e(prefix) + '/static/v5670.js"></script><script defer src="' + e(prefix) + '/static/v5671.js"></script><script defer src="' + e(prefix) + '/static/v5673.js"></script><script defer src="' + e(prefix) + '/static/v5674.js"></script></body></html>')
    from .ui_helpers import enhance
    return enhance(html, prefix, csrf, audio, matters, path, selected_matter)


def _messages_json():
    """5.6.31 (R17) : messages traduits dont les scripts ont besoin (donnée JSON, jamais exécutée)."""
    import json
    from .i18n5631 import js_messages
    return ('<script type="application/json" id="l5631-msg">' + json.dumps(js_messages(('js.',)), ensure_ascii=False).replace('</', '<' + chr(92) + '/') +
            '</script>')
