"""5.6.31 (cahier des charges additionnel, R18 / NV01–NV05) : registre commun des fonctions.

Un seul registre alimente le menu « Toutes les fonctions », sa recherche et le guide d'utilisation (R19). Chaque entrée porte un
identifiant stable, son groupe d'usage, sa route canonique (les URLs existantes sont conservées), ses anciens noms (recherche par
alias), ses prérequis et l'identifiant de son aide. Libellés et descriptions sont traduits par ``i18n5631`` (clés ``fn.<id>.*``).

Les droits ne sont pas recopiés ici : la liste visible est filtrée avec la même règle que le serveur (``standalone_auth.allowed``),
qui continue de protéger les routes. Une fonction dont le prérequis manque propose « Configurer » vers l'étape du guide d'installation.
"""
from html import escape as e

from . import i18n5631 as i18n

GROUPS = ('dossier', 'rechercher', 'communiquer', 'temps', 'modeles', 'regler')
PRIMARY = ('aujourdhui', 'dossiers', 'courriels', 'planning', 'production', 'parametres')

# identifiant, groupe, route, anciens noms / alias de recherche, prérequis (capacités)
FUNCTIONS = (
    ('aujourdhui', 'communiquer', '/aujourdhui', ('Accueil', 'Poste de pilotage', 'Briefing'), ()),
    ('dossiers', 'dossier', '/dossiers', ('Portefeuille', 'Affaires'), ('storage',)),
    ('courriels', 'communiquer', '/courriels', ('Messagerie', 'Boîte de réception', 'Brouillons'), ('mail',)),
    ('planning', 'communiquer', '/planning', ('Agenda', 'Calendrier'), ()),
    ('taches', 'communiquer', '/planning?vue=taches', ('Tâches', 'À faire'), ()),
    ('talk', 'communiquer', '/talk', ('Nextcloud Talk', 'Visio', 'Visioconférence', 'Appel', 'Discussion'), ()),   # 5.6.43 (§ 22) : facultatif
    ('production', 'dossier', '/production', ('Produire', 'Livrables'), ('ai',)),
    ('documents', 'dossier', '/documents', ('Rédiger un document', 'Courrier'), ('storage', 'ai')),
    ('echeances', 'dossier', '/echeances', ('Délais', 'Échéances de procédure', 'Retrouver un délai'), ()),
    ('audience', 'dossier', '/audiences', ('Fiche d’audience', 'Plaidoirie', 'Audiences'), ('storage',)),
    ('audiences_word', 'dossier', '/audiences?onglet=preparer', ('Audiences et Word', 'Préparer la plaidoirie', 'Révision Word', 'Réviser un document'), ('storage', 'ai')),
    ('missions_complexes', 'dossier', '/missions-complexes', ('Assignation', 'Conclusions', 'Rédiger une assignation'), ('storage', 'ai')),
    ('pieces', 'dossier', '/pieces', ('Bordereau', 'Préparer un bordereau', 'Pièces numérotées'), ('storage',)),
    ('pieces_donnees', 'dossier', '/pieces-donnees', ('GED', 'Données des pièces', 'Textes cités', 'Montants', 'Pièces jointes'), ('storage',)),   # 5.6.36
    ('outils_ia', 'dossier', '/outils-ia', ('Grilles d’analyse', 'Playbook de négociation', 'Mémos juridiques', 'Tableau comparatif'), ('ai',)),
    ('corporate', 'dossier', '/corporate', ('Secrétariat juridique', 'Approbation des comptes', 'AGO', 'Cession de titres', 'Table de capitalisation',
                                             'Registre des mouvements de titres', 'Création de société', 'Transfert de siège', 'Changement de dirigeant',
                                             'Bibliothèque de modèles', 'Statuts', 'Pacte d’associés', 'Lettre d’intention'), ('storage',)),   # 5.6.39
    ('profils', 'dossier', '/profils', ('Profils procéduraux', 'Procédures approuvées'), ()),
    ('strategie', 'dossier', '/strategy', ('Stratégie', 'Espace stratégique'), ('storage',)),
    ('studio', 'dossier', '/studio', ('Studio de production',), ('ai',)),
    ('playbooks', 'dossier', '/playbooks', ('Parcours guidés', 'Mise en demeure'), ('ai',)),
    ('recherche', 'rechercher', '/recherche', ('Rechercher', 'Recherche dans le cabinet', 'Recherche locale'), ()),
    ('recherche_juridique', 'rechercher', '/recherche-juridique', ('Jurisprudence', 'Textes'), ()),
    ('verification', 'rechercher', '/verification', ('Vérifier', 'Citations', 'Contrôle des références'), ()),
    ('sources', 'rechercher', '/sources', ('Sources juridiques', 'Légifrance', 'Judilibre'), ()),
    ('veille', 'rechercher', '/veille', ('Veille juridique', 'Nouveautés'), ()),
    ('surveillance', 'rechercher', '/surveillance', ('Surveillance proactive', 'Surveillance', 'Alertes du cabinet', 'Alertes'), ('storage',)),
    ('engagements', 'communiquer', '/engagements', ('Engagements et suites', 'Promesses'), ('mail',)),
    ('accueil_administratif', 'communiquer', '/accueil-administratif', ('Accueil téléphonique', 'Twilio', 'WhatsApp'), ('telephony',)),
    ('contacts', 'communiquer', '/contacts', ('Correspondants',), ()),
    ('contacts_crm', 'communiquer', '/contacts-crm', ('CRM', 'Clients', 'Parties adverses', 'Confrères', 'Intervenants', 'Répertoire'), ()),   # 5.6.36
    ('initiatives', 'communiquer', '/initiatives', ('Assistant proactif', 'Plan d’actions', 'Assignation reçue', 'Proactivité'), ('mail',)),
    ('associations', 'communiquer', '/associations', ('Associations proposées', 'Adresse et dossier'), ('mail',)),
    ('orchestrateur', 'communiquer', '/orchestrateur-avis', ('Orchestrateur et avis', 'Notifications'), ('mail',)),
    ('missions', 'communiquer', '/missions', ('Missions', 'Confier une mission'), ('ai',)),
    ('assistant', 'communiquer', '/assistant', ('Assistant', 'Conversation'), ('ai',)),
    ('cabinet', 'temps', '/cabinet', ('Gestion du cabinet',), ()),
    ('honoraires', 'temps', '/honoraires', ('Temps et honoraires', 'Diligences', 'Préparer une facture'), ()),
    ('relances', 'temps', '/relances', ('Impayés', 'Relances', 'Invoice Ninja', 'Factures échues'), ('invoice',)),   # 5.6.36
    ('rendez_vous', 'temps', '/rendez-vous', ('Rendez-vous',), ()),
    ('conflits', 'temps', '/conflits', ('Conflits d’intérêts',), ()),
    ('prescriptions', 'temps', '/prescriptions', ('Prescription', 'Forclusion'), ()),
    ('pilotage_mensuel', 'temps', '/pilotage-mensuel', ('Pilotage mensuel', 'Indicateurs'), ()),
    ('assistance_metier', 'temps', '/assistance-metier', ('Calculs', 'Intérêts', 'Indexation', 'Revue de facturation'), ()),
    ('modeles_word', 'modeles', '/modeles-word', ('Documents du cabinet', 'Modèles Word', 'Balises'), ()),
    ('modeles_actes', 'modeles', '/modeles', ('Modèles d’actes', 'Mentions obligatoires'), ()),
    ('mon_style', 'modeles', '/mon-style', ('Mon style', 'Apprentissage et modèles'), ()),
    ('complement_word', 'modeles', '/complement-word', ('Word', 'Office', 'Complément', 'Add-in', 'Microsoft Word'), ()),   # 5.6.36
    ('progres', 'modeles', '/progres', ('Progrès', 'Style et apprentissage', 'Tons'), ()),
    ('agents', 'modeles', '/parametres/agents', ('Agents et règles', 'Créer un agent'), ('ai',)),
    ('agents_documents', 'modeles', '/agents-documents', ('Résultats des agents', 'Reprises'), ()),
    ('regles', 'modeles', '/regles', ('Règles et autonomie', 'Règles de tri'), ()),
    ('autonomie', 'modeles', '/autonomie', ('Autonomie de l’agent', 'Mode prudent'), ()),
    ('apprentissage', 'modeles', '/apprentissage', ('Apprentissage métier', 'Règles métier'), ()),
    ('competences', 'modeles', '/competences', ('Compétences', 'Skills', 'Assistant juridique', 'Approbation des comptes', 'RGPD'), ('ai',)),
    ('projets', 'modeles', '/projets', ('Projets en attente', 'Travail préparé'), ()),
    ('preparation', 'modeles', '/preparation-proactive', ('Préparation proactive',), ()),
    ('memoire', 'modeles', '/memoire', ('Mémoire', 'Réponses envoyées'), ()),
    ('parametres', 'regler', '/parametres', ('Réglages', 'Connexions'), ()),
    ('guide_installation', 'regler', '/guide-installation', ('Installation', 'Configurer', 'Mise en route'), ()),
    ('guide_utilisation', 'regler', '/guide-utilisation', ('Aide', 'Mode d’emploi', 'Guide pratique'), ()),
    ('mise_en_service', 'regler', '/mise-en-service', ('Mise en service', 'Micro', 'Docker'), ()),
    ('diagnostic', 'regler', '/diagnostic', ('Pourquoi rien n’est produit ?', 'Diagnostic', 'État du logiciel et aide'), ()),
    ('etat_systeme', 'regler', '/etat-systeme', ('État du système', 'Incidents'), ()),
    ('mcp', 'regler', '/mcp', ('Connecteurs MCP', 'Skills', 'Plugins'), ()),
    ('routage', 'regler', '/routage-hybride', ('Routage hybride', 'IA externe', 'OpenRouter'), ()),
    ('evaluations', 'regler', '/evaluations', ('Banc juridique', 'Évaluations métier'), ('ai',)),
    ('tracabilite', 'regler', '/tracabilite', ('Traçabilité', 'Données vues par le modèle'), ()),
    ('qualite', 'regler', '/qualite', ('Qualité', 'Motifs de blocage'), ()),
    ('ecosysteme', 'regler', '/ecosysteme', ('Écosystème', 'Services du cabinet'), ()),
    ('administration', 'regler', '/administration', ('Administration', 'Raccourcis'), ()),
    ('tableau_de_bord', 'regler', '/accueil', ('Tableau de bord', 'Pilotage du cabinet', 'Dashboard'), ()),
    ('a_propos', 'regler', '/a-propos', ('À propos', 'Licence'), ()),
)
INDEX = {row[0]: row for row in FUNCTIONS}
# Étape du guide d'installation qui configure chaque capacité (NV04 : « Configurer » ouvre directement l'étape pertinente).
GUIDE_STEP = {'storage': 'stockage', 'ai': 'ia', 'mail': 'messagerie', 'calendar': 'agenda', 'telephony': 'telephonie', 'invoice': 'facturation'}


def capabilities(cfg):
    """Capacités configurées (déclarées, pas prouvées : la preuve de connexion appartient au guide)."""
    cfg = cfg or {}
    mail, nc = cfg.get('mail') or {}, cfg.get('nextcloud') or {}
    out = set()
    if mail.get('enabled', True) is not False and 'example.com' not in str(mail.get('host', 'example.com')):
        out.add('mail')
    if nc.get('local_path') or 'example.com' not in str(nc.get('url', 'example.com')):
        out.add('storage')
    if (cfg.get('ollama') or {}).get('url') or (cfg.get('ai_gateway') or {}).get('providers'):
        out.add('ai')
    if (cfg.get('calendar') or {}).get('urls') or (cfg.get('google_calendar568') or {}).get('client_id'):
        out.add('calendar')
    if (cfg.get('reception') or {}).get('telephone_enabled'):
        out.add('telephony')
    if (cfg.get('invoice_ninja') or {}).get('enabled'):
        out.add('invoice')
    return out


def visible(role, route):
    if not role or role == 'administrateur':
        return True
    from .standalone_auth import allowed
    return allowed(role, 'GET', route.split('?', 1)[0])


def entries(cfg=None, role='administrateur', lang=None):
    caps = capabilities(cfg)
    out = []
    for fid, group, route, aliases, needs in FUNCTIONS:
        if not visible(role, route):
            continue
        missing = [c for c in needs if c not in caps]
        out.append({'id': fid, 'group': group, 'route': route, 'aliases': aliases, 'missing': missing,
                    'label': i18n.t('fn.%s.label' % fid, lang), 'desc': i18n.t('fn.%s.desc' % fid, lang),
                    'help': fid, 'configure': ('/guide-installation?etape=' + GUIDE_STEP[missing[0]]) if missing else ''})
    return out


def for_path(path, args=None):
    """Fonction correspondant à la page affichée (aide contextuelle « Comment utiliser cette page »)."""
    path = path or ''
    if path == '/planning' and (args or {}).get('vue') == 'taches':
        return 'taches'
    from .shell501 import ACTIVE
    for fid, group, route, aliases, needs in FUNCTIONS:
        if route.split('?', 1)[0] == path:
            return fid
    legacy = {'/rechercher': 'recherche', '/dossier': 'dossiers', '/fiche': 'dossiers', '/chronologie': 'dossiers', '/pilotage': 'tableau_de_bord',
              '/dashboard': 'tableau_de_bord', '/guide': 'guide_utilisation', '/ia-externe': 'parametres', '/confort': 'parametres',
              '/atelier/reglages': 'parametres', '/parametres/agendas': 'parametres', '/parametres/proactivite': 'parametres',
              '/parametres/assistant': 'parametres', '/parametres/connexions': 'parametres', '/agenda': 'planning', '/taches': 'taches',
              '/documents/edit': 'documents', '/mail': 'courriels', '/': 'courriels'}
    if path in legacy:
        return legacy[path]
    active = ACTIVE.get(path)
    return for_path(active) if active and active != path else ''


def panel_html(prefix, path, cfg=None, role='administrateur', lang=None):
    """Panneau « Toutes les fonctions » : groupes d'usage, recherche par nom ou besoin (alias compris), état de disponibilité."""
    lang = lang or i18n.current()
    from .navigation5643 import PARENT_LABELS, canonical, visibility   # 5.6.43 (cahier v9, § 20)
    rows = entries(cfg, role, lang)
    current = for_path(path)
    if current and visibility(current) != 'panel':
        current = canonical(current)   # une sous-section marque l'entrée de sa destination (ex. Conflits → Gestion du cabinet)
    groups = []
    for g in GROUPS:
        items = [r for r in rows if r['group'] == g and visibility(r['id']) == 'panel']   # ni accès principaux, ni doublons
        if not items:
            continue
        links = ''.join(
            '<li data-search="' + e(' '.join((r['label'], r['desc']) + tuple(r['aliases'])).lower(), quote=True) + '">'
            '<a href="' + e(prefix + r['route'], quote=True) + '"' + (' aria-current="page"' if r['id'] == current else '') + '>'
            '<strong>' + e(r['label']) + '</strong><small>' + e(r['desc']) + '</small></a>' +
            ('<a class="f5631-configure" href="' + e(prefix + r['configure'], quote=True) + '">' + i18n.th('menu.configure', lang) + '</a>' if r['missing'] else '') +
            '</li>' for r in items)
        groups.append('<section class="f5631-group" data-group="' + g + '"><h3>' + i18n.th('grp.%s.label' % g, lang) + '</h3><p>' +
                      i18n.th('grp.%s.desc' % g, lang) + '</p><ul>' + links + '</ul></section>')
    # 5.6.43 : toutes les autres fonctions restent trouvables par leur nom ou leurs anciens noms ; le résultat ouvre leur page, rangée dans
    # sa destination canonique (affichée). Masquées tant que la recherche est vide.
    index = []
    for r in rows:
        kind = visibility(r['id'])
        if kind == 'panel':
            continue
        target = r if kind != 'hidden' else next((x for x in rows if x['id'] == canonical(r['id'])), None)
        if not target:
            continue
        where = PARENT_LABELS.get(canonical(r['id']), '') if kind in ('sub', 'tab', 'hidden') else ''
        index.append('<li hidden data-search-only data-search="' + e(' '.join((r['label'], r['desc']) + tuple(r['aliases'])).lower(), quote=True) + '">'
                     '<a href="' + e(prefix + target['route'], quote=True) + '"><strong>' + e(target['label'] if kind == 'hidden' else r['label']) +
                     '</strong><small>' + e(('Dans « %s »' % where) if where else r['desc']) + '</small></a></li>')
    if index:
        groups.append('<section class="f5631-group" data-group="index" hidden><h3>' + i18n.th('menu.search', lang) + '</h3><ul>' + ''.join(index) +
                      '</ul></section>')
    return ('<details class="f5631-all" id="f5631-all"><summary id="f5631-toggle">' + i18n.th('menu.all', lang) + '</summary>'
            '<div class="f5631-panel" role="region" aria-label="' + i18n.th('menu.all', lang) + '">'
            '<label class="f5631-search"><span>' + i18n.th('menu.search', lang) + '</span><input type="search" id="f5631-q" autocomplete="off" placeholder="' +
            i18n.th('menu.search_hint', lang) + '"></label><p class="f5631-empty" id="f5631-empty" hidden>' + i18n.th('menu.no_result', lang) + '</p>' +
            ''.join(groups) + '<p class="f5631-help"><a href="' + e(prefix + '/guide-utilisation', quote=True) + '">' + i18n.th('menu.usage_guide', lang) +
            '</a></p></div></details>')


def audit():
    """Contrôle NV05 / R18 étape 5 : groupe connu, route servie, libellé, description et article d'aide présents."""
    from . import aide5631
    problems = []
    seen = set()
    for fid, group, route, aliases, needs in FUNCTIONS:
        if fid in seen:
            problems.append((fid, 'doublon'))
        seen.add(fid)
        if group not in GROUPS:
            problems.append((fid, 'groupe'))
        if not i18n.catalog('fr').get('fn.%s.label' % fid) or not i18n.catalog('fr').get('fn.%s.desc' % fid):
            problems.append((fid, 'libelle'))
        if fid not in aide5631.articles('fr'):
            problems.append((fid, 'aide'))
        for need in needs:
            if need not in GUIDE_STEP:
                problems.append((fid, 'prerequis'))
    return problems
