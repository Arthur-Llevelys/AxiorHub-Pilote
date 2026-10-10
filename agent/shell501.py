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
          '/atelier/reglages': '/parametres', '/fiche': '/dossiers', '/chronologie': '/dossiers', '/documents/edit': '/documents',
          '/honoraires': '/cabinet', '/rendez-vous': '/cabinet', '/conflits': '/cabinet', '/prescriptions': '/cabinet',
          '/pilotage-mensuel': '/cabinet', '/rechercher': '/recherche', '/pieces': '/production', '/diagnostic': '/aujourdhui', '/ia-externe': '/parametres'}

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
BUNDLE = LEGACY_CSS + AX_CSS + ENHANCE_CSS + ('v520.css', 'v530.css', 'v550.css', 'v560.css', 'v561.css', 'v567.css', 'v568.css', 'v5613.css', 'v5614.css', 'v5625.css')
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
    return ACTIVE.get(path, path)


def _links(prefix, entries, active):
    return ''.join(
        '<a href="' + e(prefix + p) + '" aria-label="' + e(label) + '" title="' + e(label) + '"' + (' aria-current="page"' if p == active else '') +
        '><span class="ws-nav-icon" aria-hidden="true">' + e(icon) + '</span><span class="ws-nav-label">' + e(label) + '</span></a>'
        for p, icon, label in entries)


def nav_html(prefix, active_path):
    active = active_primary(active_path)
    opened = ' open' if active in {p for p, _, _ in RUBRIQUES} else ''
    return ('<nav aria-label="Navigation principale" class="ws-primary">' + _links(prefix, PRIMARY, active) + '</nav>'
            '<details class="ws-rubriques"' + opened + '><summary>Rubriques</summary><nav aria-label="Rubriques" class="ws-primary ws-sub">' +
            _links(prefix, RUBRIQUES, active) + '</nav></details>')


def tools_html(prefix, path):
    opened = ' open' if path in TOOL_PAGES else ''
    return ('<div class="ws-secondary"><details%s><summary>Outils</summary>' % opened + ''.join(
        '<a href="' + e(prefix + p) + '" title="' + e(hint) + '"' + (' aria-current="page"' if p == path else '') + '>' + e(label) + '</a>'
        for p, label, hint in TOOLS) + '</details></div>')


def topbar_html(prefix, title):
    return ('<header class="ws-topbar"><div class="ws-topbar-leading"><button type="button" class="ws-icon-button" id="ws-sidebar-toggle" '
            'aria-controls="ws-sidebar" aria-expanded="true" aria-label="Replier le menu" title="Replier le menu"><span aria-hidden="true">☰</span></button>'
            '<span>Espace de travail / ' + e(title) + '</span></div><div class="ws-topbar-actions"><button type="button" class="ws-icon-button" '
            'id="ws-theme-toggle" aria-label="Activer le thème sombre" title="Activer le thème sombre"><span aria-hidden="true">☾</span></button>'
            '<a href="' + e(prefix + '/missions') + '">Missions</a><a href="' + e(prefix + '/rechercher') + '">⌕ Rechercher dans le cabinet</a></div></header>')


def head_html(title, prefix, css=(), head=''):
    # 5.5.0 : « ?v=version » change l'adresse à chaque version ; ni le navigateur, ni un proxy, ni l'application mobile ne gardent l'ancienne feuille.
    from . import __version__ as version
    links = ''.join('<link rel="stylesheet" href="' + e(prefix) + '/static/' + name + '?v=' + e(version) + '">' for name in (BUNDLE_FILE,) + tuple(c for c in css if c not in BUNDLE))
    return ('<!doctype html><html lang="fr"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            '<title>' + e(title) + ' · AxiorHub Pilote</title>' + links + head + '</head><body>')


def sidebar_html(prefix, active_path):
    return ('<aside id="ws-sidebar"><div class="brand"><div class="ws-brand-lockup"><img src="' + e(prefix) + '/static/axiorhub-icon.png" width="38" height="38" alt="">'
            '<span class="ws-brand-name">AxiorHub</span></div><span class="ws-brand-subtitle">Pilote · Agent IA Avocat</span></div>' + nav_html(prefix, active_path) + tools_html(prefix, active_path) +
            '<p class="ws-about">Version ' + e(_version()) + ' · ' + e(ATTRIBUTION) + '<br><a href="' + e(prefix) + '/a-propos">À propos · licence AGPL</a></p></aside>')


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
    html = (head_html(title, prefix, css, head) + toast + sidebar_html(prefix, path) + '<main id="ws-main">' + topbar_html(prefix, title) + body + '</main>' + scripts + '</body></html>')
    from .ui_helpers import enhance
    return enhance(html, prefix, csrf, audio, matters, path, selected_matter)
