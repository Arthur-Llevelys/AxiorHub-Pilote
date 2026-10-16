"""Gabarit unique 5.0.1 : menu latéral, barre du haut et assistant communs à TOUTES les pages.

Avant la 5.0.1, les pages Courriels, Documents, Échéances, Cabinet, Recherche… (modules web44x à web50x) avaient leur propre
barre de navigation en haut, alors que Agenda et tâches, Dossiers, Produire… utilisaient le menu latéral. Les deux gabarits
passent maintenant par ce module : même menu, même barre, même assistant, même thème.
"""
from html import escape as e
import threading

# 5.3.0 : menu réduit au poste de pilotage ; les rubriques historiques restent à un clic, regroupées.
PRIMARY = (('/aujourdhui', '◎', 'Aujourd’hui'), ('/dossiers', '▦', 'Dossiers'), ('/courriels', '✉', 'Courriels'), ('/planning', '◷', 'Agenda et tâches'), ('/production', '＋', 'Produire'), ('/parametres', '⚙', 'Paramètres'))
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
         ('/modeles-word', 'Documents du cabinet', 'Modèles Word et livrables'))
TOOL_PAGES = {'/mise-en-service', '/audience', '/missions-complexes', '/profils', '/parametres/agents', '/parametres/agendas', '/agents-documents', '/parametres/proactivite', '/engagements', '/veille', '/diagnostic', '/ia-externe', '/pieces', '/verification', '/sources', '/modeles', '/progres', '/autonomie', '/tracabilite', '/confort', '/modeles-word', '/atelier/reglages'}

AX_CSS = ('v440.css', 'v470.css', 'v480.css', 'v500.css')
ENHANCE_CSS = ('v300.css', 'v320.css', 'v330.css', 'v360.css', 'v363.css', 'v365.css', 'v370.css', 'v420.css', 'v430.css', 'v490.css')
AX_JS = ('v440.js', 'v470.js', 'v480.js', 'v500.js')
LEGACY_CSS = ('style.css', 'v15.css', 'v151.css', 'v160.css', 'v170.css', 'v180.css', 'v190.css', 'v210.css', 'v211.css', 'v230.css',
              'v364.css', 'v380.css', 'v390.css', 'v391.css', 'v392.css', 'v393.css', 'v400.css', 'v410.css', 'v420.css')
# 5.2.0 : une seule feuille par page, concaténation dans l'ordre exact de chargement (voir scripts/build-css.py).
BUNDLE = LEGACY_CSS + AX_CSS + ENHANCE_CSS + ('v520.css', 'v530.css', 'v550.css', 'v560.css', 'v561.css', 'v567.css', 'v568.css', 'v5613.css', 'v5614.css', 'v5625.css', 'v5627.css', 'v5628.css', 'v5631.css', 'v5632.css', 'v5634.css', 'v5636.css', 'v5637.css', 'v5650.css', 'v5651.css', 'v5652.css')
BUNDLE_FILE = 'app520.css'

_ctx = threading.local()
from .about560 import ATTRIBUTION


def _version():
    from . import __version__
    return __version__


def set_context(cfg):
    """Mémorise la configuration de la requête en cours (pour l'assistant : dossiers, dictée)."""
    _ctx.cfg = cfg


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
    return ''.join(
        '<a href="' + e(prefix + p) + '" aria-label="' + e(label) + '" title="' + e(label) + '"' + (' aria-current="page"' if p == active else '') +
        '><span class="ws-nav-icon" aria-hidden="true">' + e(icon) + '</span><span class="ws-nav-label">' + e(label) + '</span></a>'
        for p, icon, label in entries)


def _primary():
    # 5.6.31 (LG02) : libellés traduits selon la langue de la requête ; routes et icônes inchangées.
    from .i18n5631 import t
    keys = {'/aujourdhui': 'aujourdhui', '/dossiers': 'dossiers', '/courriels': 'courriels', '/planning': 'planning', '/production': 'production', '/parametres': 'parametres'}
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
    return ('<nav aria-label="' + th('nav.primary') + '" class="ws-primary">' + _links(prefix, entries, active) + '</nav>' +
            panel_html(prefix, active_path, context(), role(), current()))


def tools_html(prefix, path):
    """Conservé pour compatibilité : les outils sont désormais dans « Toutes les fonctions » (registre fonctions5631)."""
    return ''


def topbar_html(prefix, title, path=''):
    from .fonctions5631 import for_path
    from .i18n5631 import menu_html, th
    fid = for_path(path) if path else ''
    help_link = '<a class="l5631-help" href="' + e(prefix + '/guide-utilisation' + ('?fonction=' + fid if fid else '')) + '">' + th('top.help') + '</a>'
    return ('<header class="ws-topbar"><div class="ws-topbar-leading"><button type="button" class="ws-icon-button" id="ws-sidebar-toggle" '
            'aria-controls="ws-sidebar" aria-expanded="true" aria-label="' + th('top.fold') + '" title="' + th('top.fold') + '"><span aria-hidden="true">☰</span></button>'
            '<span>' + th('top.workspace') + ' / ' + e(title) + '</span></div><div class="ws-topbar-actions"><button type="button" class="ws-icon-button" '
            'id="ws-theme-toggle" aria-label="' + th('top.dark') + '" title="' + th('top.dark') + '"><span aria-hidden="true">☾</span></button>'
            '<a href="' + e(prefix + '/missions') + '">' + th('top.missions') + '</a><a href="' + e(prefix + '/rechercher') + '">⌕ ' + th('top.search') + '</a>' +
            help_link + menu_html(prefix) + '</div></header>')


def head_html(title, prefix, css=(), head=''):
    # 5.5.0 : « ?v=version » change l'adresse à chaque version ; ni le navigateur, ni un proxy, ni l'application mobile ne gardent l'ancienne feuille.
    from . import __version__ as version
    links = ''.join('<link rel="stylesheet" href="' + e(prefix) + '/static/' + name + '?v=' + e(version) + '">' for name in (BUNDLE_FILE,) + tuple(c for c in css if c not in BUNDLE))
    from .i18n5631 import current
    return ('<!doctype html><html lang="' + current() + '"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            '<title>' + e(title) + ' · AxiorHub Pilote</title>' + links + head + '</head><body>')


def sidebar_html(prefix, active_path):
    from .i18n5631 import th
    return ('<aside id="ws-sidebar"><div class="brand"><div class="ws-brand-lockup"><img src="' + e(prefix) + '/static/axiorhub-icon.png" width="38" height="38" alt="">'
            '<span class="ws-brand-name">AxiorHub</span></div><span class="ws-brand-subtitle">' + th('side.subtitle') + '</span></div>' + nav_html(prefix, active_path) +
            '<p class="ws-about">' + th('side.version', version=_version()) + ' · ' + e(ATTRIBUTION) + '<br><a href="' + e(prefix) + '/a-propos">' + th('side.about') + '</a></p></aside>')


def render(title, body, prefix, csrf, path, cfg=None, css=(), scripts='', head='', selected_matter='', toast='', page_title=True):
    """Page complète : menu latéral + barre + contenu, puis assistant et mécanismes communs (``ui_helpers.enhance``)."""
    cfg = cfg or context() or {}
    matters = []
    try:
        from .common import load_matters
        matters = load_matters(cfg) if cfg.get('matters_file') else []
    except Exception:
        matters = []
    audio = bool((cfg.get('audio') or {}).get('enabled'))
    from .navigation5643 import production_tabs_html   # 5.6.43 (§ 20) : onglets de Produire sur chacune de ses pages
    body = production_tabs_html(prefix, path) + body
    html = (head_html(title, prefix, css, head) + toast + sidebar_html(prefix, path) + '<main id="ws-main">' + topbar_html(prefix, title, path) + body + '</main>' + scripts +
            _messages_json() + '<script defer src="' + e(prefix) + '/static/v5631.js"></script><script defer src="' + e(prefix) + '/static/v5632.js"></script><script defer src="' + e(prefix) + '/static/v5634.js"></script><script defer src="' + e(prefix) + '/static/v5636.js"></script></body></html>')
    from .ui_helpers import enhance
    return enhance(html, prefix, csrf, audio, matters, path, selected_matter)


def _messages_json():
    """5.6.31 (R17) : messages traduits dont les scripts ont besoin (donnée JSON, jamais exécutée)."""
    import json
    from .i18n5631 import js_messages
    return ('<script type="application/json" id="l5631-msg">' + json.dumps(js_messages(('js.',)), ensure_ascii=False).replace('</', '<' + chr(92) + '/') +
            '</script>')
