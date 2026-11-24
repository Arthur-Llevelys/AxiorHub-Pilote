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
PRIMARY = ('aujourdhui', 'a_relire', 'courriels', 'dossiers', 'planning', 'production')   # 5.6.102 (lot N) : « À relire » sorti de Courriels

# identifiant, groupe, route, anciens noms / alias de recherche, prérequis (capacités)
FUNCTIONS = (
    ('aujourdhui', 'communiquer', '/aujourdhui', ('Accueil', 'Poste de pilotage', 'Briefing'), ()),
    ('dossiers', 'dossier', '/dossiers', ('Portefeuille', 'Affaires'), ('storage',)),
    ('courriels', 'communiquer', '/courriels', ('Messagerie', 'Boîte de réception', 'Brouillons'), ('mail',)),
    ('a_relire', 'communiquer', '/a-relire', ('Relecture', 'À valider', 'Propositions', 'Brouillons à relire', 'Projets à relire', 'Courriels à relire',
                                             'Documents à relire'), ()),   # 5.6.102 (lot N) : liste unique de relecture, accès principal distinct
    ('planning', 'communiquer', '/planning', ('Agenda', 'Calendrier'), ()),
    ('taches', 'communiquer', '/planning?vue=taches', ('Tâches', 'À faire'), ()),
    ('mes_taches', 'communiquer', '/mes-taches', ('Mes tâches', 'Mes rappels', 'Tâches confiées', 'Responsable d’une tâche'), ()),   # 5.6.88
    ('talk', 'communiquer', '/talk', ('Nextcloud Talk', 'Visio', 'Visioconférence', 'Appel', 'Discussion'), ()),   # 5.6.43 (§ 22) : facultatif
    ('production', 'dossier', '/production', ('Produire', 'Livrables'), ('ai',)),
    ('documents', 'dossier', '/documents', ('Rédiger un document', 'Courrier'), ('storage', 'ai')),
    ('echeances', 'dossier', '/echeances', ('Délais', 'Échéances de procédure', 'Retrouver un délai'), ()),
    ('audience', 'dossier', '/audiences', ('Fiche d’audience', 'Plaidoirie', 'Audiences'), ('storage',)),
    ('audiences_suivi', 'dossier', '/audiences?onglet=suivi', ('Suivi des audiences', 'Rôle d’audience', 'Audiences du cabinet', 'Audience sans avocat'), ()),   # 5.6.88
    ('audiences_word', 'dossier', '/audiences?onglet=preparer', ('Audiences et Word', 'Préparer la plaidoirie', 'Révision Word', 'Réviser un document'), ('storage', 'ai')),
    ('missions_complexes', 'dossier', '/missions-complexes', ('Assignation', 'Conclusions', 'Rédiger une assignation'), ('storage', 'ai')),
    ('pieces', 'dossier', '/pieces', ('Bordereau', 'Préparer un bordereau', 'Pièces numérotées'), ('storage',)),
    ('contentieux', 'dossier', '/contentieux', ('Contentieux', 'Matrice des prétentions', 'Pièces par partie', 'Contrôles avant acte', 'Dossier d’audience', 'Paquet à communiquer'), ()),   # 5.6.89 : ouverte depuis le dossier
    ('pieces_donnees', 'dossier', '/pieces-donnees', ('GED', 'Données des pièces', 'Textes cités', 'Montants', 'Pièces jointes'), ('storage',)),   # 5.6.36
    ('outils_ia', 'dossier', '/outils-ia', ('Grilles d’analyse', 'Playbook de négociation', 'Mémos juridiques', 'Tableau comparatif'), ('ai',)),
    ('corporate', 'dossier', '/corporate', ('Secrétariat juridique', 'Approbation des comptes', 'AGO', 'Cession de titres', 'Table de capitalisation',
                                             'Registre des mouvements de titres', 'Création de société', 'Transfert de siège', 'Changement de dirigeant',
                                             'Bibliothèque de modèles', 'Statuts', 'Pacte d’associés', 'Lettre d’intention'), ('storage',)),   # 5.6.39
    ('cessions', 'dossier', '/cessions', ('Cessions de fonds et de bail', 'Cession de fonds', 'Fonds de commerce', 'Droit au bail', 'Cession de bail'), ('storage',)),   # 5.6.84
    ('formulaires', 'dossier', '/formulaires', ('Formulaires officiels', 'Cerfa', 'Remplir un formulaire', '2759-SD', '2672-SD', 'Injonction de payer', 'Aide juridictionnelle'), ('storage',)),   # 5.6.83
    ('profils', 'dossier', '/profils', ('Profils procéduraux', 'Procédures approuvées'), ()),
    ('strategie', 'dossier', '/strategy', ('Stratégie', 'Question stratégique'), ('storage',)),   # 5.6.79 : dans le dossier, onglet Travail
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
    ('contacts_crm', 'communiquer', '/contacts-crm', ('CRM', 'Clients', 'Parties adverses', 'Confrères', 'Intervenants', 'Répertoire', 'Veille des sociétés', 'BODACC', 'Opportunités', 'Prospects', 'Pipeline',
                                                     'Suivi commercial', 'Nouveaux clients'), ()),   # 5.6.36 ; 5.6.76 : opportunités
    ('initiatives', 'communiquer', '/initiatives', ('Assistant proactif', 'Plan d’actions', 'Assignation reçue', 'Proactivité'), ('mail',)),
    ('associations', 'communiquer', '/associations', ('Associations proposées', 'Adresse et dossier'), ('mail',)),
    ('orchestrateur', 'communiquer', '/orchestrateur-avis', ('Orchestrateur et avis', 'Notifications'), ('mail',)),
    ('missions', 'communiquer', '/missions', ('Missions', 'Confier une mission'), ('ai',)),
    ('assistant', 'communiquer', '/assistant', ('Assistant', 'Conversation'), ('ai',)),
    ('cabinet', 'temps', '/cabinet', ('Gestion du cabinet',), ()),
    ('facturation', 'temps', '/facturation', ('Honoraires et facturation', 'Facturation et honoraires', 'Factures', 'Devis', 'Convention d’honoraires', 'Lettre de mission', 'Avoir',
                                              'Facture électronique', 'Factur-X', 'Honorium', 'Chiffrer une mission'), ()),   # 5.6.53
    ('honoraires', 'temps', '/honoraires', ('Temps et honoraires', 'Diligences', 'Temps passés'), ()),
    ('relances', 'temps', '/relances', ('Impayés', 'Relances', 'Invoice Ninja', 'Factures échues', 'Accusé de paiement'), ()),   # 5.6.36 ; 5.6.88 : Invoice Ninja facultatif
    ('contribution', 'temps', '/contribution', ('Contribution au chiffre d’affaires', 'Rétrocession', 'Rétrocessions', 'Collaborateurs', 'Relevé mensuel'), ()),   # 5.6.88
    ('rendez_vous', 'temps', '/rendez-vous', ('Rendez-vous',), ()),
    ('conflits', 'temps', '/conflits', ('Conflits d’intérêts',), ()),
    ('prescriptions', 'temps', '/prescriptions', ('Prescription', 'Forclusion'), ()),
    ('pilotage_mensuel', 'temps', '/pilotage-mensuel', ('Pilotage mensuel', 'Indicateurs'), ()),
    ('assistance_metier', 'temps', '/assistance-metier', ('Calculs', 'Intérêts', 'Indexation', 'Revue de facturation'), ()),
    ('modeles_word', 'modeles', '/modeles-word', ('Modèle de courrier', 'Documents du cabinet', 'Modèles Word', 'Balises'), ()),
    ('modeles_actes', 'modeles', '/modeles', ('Modèles d’actes', 'Mentions obligatoires'), ()),
    ('mon_style', 'modeles', '/mon-style', ('Mon style', 'Apprentissage et modèles'), ()),
    ('complement_word', 'modeles', '/complement-word', ('Word', 'Office', 'Complément', 'Add-in', 'Microsoft Word'), ()),   # 5.6.36
    ('progres', 'modeles', '/progres', ('Progrès', 'Style et apprentissage', 'Tons'), ()),
    ('agents', 'modeles', '/parametres/agents', ('Agents et règles', 'Créer un agent'), ('ai',)),
    ('agents_documents', 'modeles', '/agents-documents', ('Résultats des agents', 'Reprises'), ()),
    ('regles', 'modeles', '/regles', ('Règles et autonomie', 'Règles de tri'), ()),
    ('autonomie', 'modeles', '/autonomie', ('Autonomie de l’agent', 'Mode prudent'), ()),
    ('apprentissage', 'modeles', '/apprentissage', ('Apprentissage métier', 'Règles métier'), ()),
    ('mobile', 'communiquer', '/mobile', ('À valider', 'Téléphone', 'Mobile', 'Validations', 'Associer un téléphone', 'QR code'), ()),   # 5.6.74
    ('navigateur', 'rechercher', '/navigateur', ('Navigateur supervisé', 'Navigateur', 'Portail des juridictions', 'Infogreffe', 'Registre des sociétés',
                                                 'Sites autorisés'), ()),   # 5.6.73
    ('savoir_faire', 'modeles', '/savoir-faire', ('Savoir-faire du cabinet', 'Clauses approuvées', 'Listes de vérification', 'Structures de courrier'), ()),   # 5.6.59
    ('competences', 'modeles', '/competences', ('Compétences', 'Skills', 'Assistant juridique', 'Approbation des comptes', 'RGPD'), ('ai',)),
    ('projets', 'modeles', '/projets', ('Projets en attente', 'Travail préparé'), ()),
    ('livrables', 'modeles', '/livrables', ('Documents préparés', 'Livrables', 'Visionneuse', 'Exporter un document'), ()),   # 5.6.89
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
    ('securite_ia', 'regler', '/securite-ia', ('Sécurité de l’IA', 'Injection d’instructions', 'Instructions cachées'), ()),   # 5.6.59
    ('tracabilite', 'regler', '/tracabilite', ('Traçabilité', 'Données vues par le modèle'), ()),
    ('mon_compte', 'regler', '/mon-compte', ('Mon compte', 'Double authentification', 'Code à six chiffres', 'Jetons d’API', '2FA', 'TOTP'), ()),   # 5.6.88
    ('journal_audit', 'regler', '/parametres/journal-audit', ('Journal d’audit', 'Audit', 'Qui a fait quoi'), ()),   # 5.6.88
    ('vue_ia', 'regler', '/parametres/ia-visible', ('Ce que l’IA peut voir', 'Accès de l’IA', 'Confidentialité de l’IA'), ()),   # 5.6.88
    ('gouvernance_ia', 'regler', '/parametres/gouvernance', ('Données et budget de l’IA', 'Politique des données', 'Plafonds de l’IA', 'Coût mensuel', 'Prestataires'), ()),   # 5.6.89
    ('banc_essai', 'regler', '/banc-essai', ('Banc d’essai', 'Comparatif', 'Comparer à Claude Desktop', 'État des fonctions', 'Qualification', 'Conservation des fonctions'), ()),   # 5.6.89
    ('qualite', 'regler', '/qualite', ('Qualité', 'Motifs de blocage'), ()),
    ('ecosysteme', 'regler', '/ecosysteme', ('Écosystème', 'Services du cabinet'), ()),
    ('administration', 'regler', '/administration', ('Administration', 'Raccourcis'), ()),
    ('tableau_de_bord', 'regler', '/accueil', ('Tableau de bord', 'Pilotage du cabinet', 'Dashboard'), ()),
    ('a_propos', 'regler', '/a-propos', ('À propos', 'Licence'), ()),
    ('securite_serveur', 'regler', '/securite-serveur', ('Sécurité du serveur', 'Sécurité', 'Intrusion', 'Mineur', 'Serveur', 'Alerte', 'Veille de sécurité'), ()),   # 5.6.101 (lot S : page ; lot F : inscription)
    ('fichiers', 'dossier', '/fichiers', ('Explorateur de fichiers', 'Gestionnaire de fichiers', 'Fichiers Nextcloud', 'Disque local', 'Renommer par lot',
                                          'Numéroter les pièces', 'Versions d’un fichier', 'Archive ZIP', 'Empreinte SHA-256', 'Deux volets'), ('storage',)),   # 5.6.103 (lot F)
    ('telephonie_bland', 'communiquer', '/accueil-administratif?vue=bland', ('Bland', 'Téléphonie par IA', 'Agent vocal', 'Agent de prise de rendez-vous',
                                                                       'Rappel de rendez-vous', 'SMS'), ()),   # 5.6.109 (lot B, intégration I2) : onglet de la famille Accueil téléphonique (carte Bland)
)
INDEX = {row[0]: row for row in FUNCTIONS}
# 5.6.101 (lot F, F01) : famille de chaque fonction (annexe A du cahier, décision D4 : regroupement tel quel), dans l'ordre des onglets de
# la famille. Les familles, leur ordre et leurs sections sont dans ``familles56101`` ; le groupe ci-dessus reste inchangé (registre figé).
FAMILLE = {
    'dossiers': 'dossiers',
    'production': 'produire', 'projets': 'produire', 'livrables': 'produire', 'agents_documents': 'produire', 'studio': 'produire', 'preparation': 'produire',
    'documents': 'rediger', 'missions_complexes': 'rediger', 'playbooks': 'rediger', 'formulaires': 'rediger',
    'echeances': 'echeances', 'prescriptions': 'echeances',
    'audience': 'audiences', 'audiences_suivi': 'audiences', 'audiences_word': 'audiences',
    'pieces': 'pieces_contentieux', 'contentieux': 'pieces_contentieux', 'pieces_donnees': 'pieces_contentieux',
    'fichiers': 'pieces_contentieux',   # 5.6.103 (lot F) : page « Fichiers », dernier onglet de la famille (à côté des données des pièces)
    'corporate': 'societes_fonds', 'cessions': 'societes_fonds',
    'strategie': 'strategie_analyses', 'outils_ia': 'strategie_analyses', 'profils': 'strategie_analyses',
    'recherche': 'rechercher', 'recherche_juridique': 'rechercher', 'verification': 'rechercher', 'sources': 'rechercher',
    'veille': 'veille', 'surveillance': 'veille',
    'navigateur': 'navigateur',
    'aujourdhui': 'aujourdhui',
    'courriels': 'courriels', 'a_relire': 'courriels',   # 5.6.102 (lot N) : onglets « Courriels » et « À relire » (33 familles conservées)
    'planning': 'agenda_taches', 'taches': 'agenda_taches', 'mes_taches': 'agenda_taches', 'rendez_vous': 'agenda_taches', 'engagements': 'agenda_taches',
    'talk': 'talk',
    'contacts': 'contacts', 'contacts_crm': 'contacts', 'associations': 'contacts',
    'assistant': 'assistant_missions', 'missions': 'assistant_missions', 'initiatives': 'assistant_missions', 'orchestrateur': 'assistant_missions',
    'accueil_administratif': 'accueil_telephonique', 'telephonie_bland': 'accueil_telephonique',   # 5.6.109 (I2) : téléphonie par IA (Bland)
    'mobile': 'telephone',
    'cabinet': 'gestion_cabinet', 'pilotage_mensuel': 'gestion_cabinet', 'tableau_de_bord': 'gestion_cabinet', 'contribution': 'gestion_cabinet', 'conflits': 'gestion_cabinet',
    'facturation': 'facturation', 'honoraires': 'facturation', 'relances': 'facturation',
    'assistance_metier': 'calculs',
    'modeles_word': 'modeles', 'modeles_actes': 'modeles', 'complement_word': 'modeles',
    'savoir_faire': 'savoir_faire', 'mon_style': 'savoir_faire', 'progres': 'savoir_faire', 'apprentissage': 'savoir_faire', 'memoire': 'savoir_faire', 'competences': 'savoir_faire',
    'agents': 'regles_agent', 'regles': 'regles_agent', 'autonomie': 'regles_agent',
    'parametres': 'parametres', 'mcp': 'parametres', 'routage': 'parametres', 'ecosysteme': 'parametres', 'administration': 'parametres',
    'mon_compte': 'mon_compte',
    'vue_ia': 'ia_confiance', 'gouvernance_ia': 'ia_confiance', 'tracabilite': 'ia_confiance', 'securite_ia': 'ia_confiance',
    'securite_serveur': 'securite', 'journal_audit': 'securite',
    'diagnostic': 'etat_depannage', 'etat_systeme': 'etat_depannage', 'qualite': 'etat_depannage', 'mise_en_service': 'etat_depannage', 'guide_installation': 'etat_depannage',
    'evaluations': 'evaluations', 'banc_essai': 'evaluations',
    'guide_utilisation': 'aide',
    'a_propos': 'a_propos',
}
# 5.6.101 : pages dont le service refuse lui-même les autres rôles (404 neutre) alors que la règle de route les laisserait passer.
# Elles restent absentes du panneau, des onglets, du guide et de la palette pour ces rôles (F04).
ROLES = {'securite_serveur': ('administrateur',)}
# 5.6.102 (lot N) : accès principaux du menu servis hors du registre (aucune famille, aucun onglet) mais trouvables par Ctrl+K, sous leur
# nom et leurs anciens noms : la messagerie interne (5.6.50), renommée « Messagerie instantanée ». Le registre figé n'est pas touché.
HORS_REGISTRE = (('messagerie', '/messagerie', ('Messagerie', 'Messagerie du cabinet', 'Messages internes', 'Discussion interne', 'Chat',
                                                'Instant messaging', 'Instant talk')),)
# Étape du guide d'installation qui configure chaque capacité (NV04 : « Configurer » ouvre directement l'étape pertinente).
GUIDE_STEP = {'storage': 'stockage', 'ai': 'ia', 'mail': 'messagerie', 'calendar': 'agenda', 'telephony': 'telephonie', 'invoice': 'facturation'}


def capabilities(cfg):
    """Capacités configurées (déclarées, pas prouvées : la preuve de connexion appartient au guide)."""
    cfg = cfg or {}
    mail, nc = cfg.get('mail') or {}, cfg.get('nextcloud') or {}
    out = set()
    if mail.get('enabled', True) is not False and 'example.com' not in str(mail.get('host', 'example.com')):
        out.add('mail')
    if nc.get('local_path') or nc.get('stockage_cloud') or 'example.com' not in str(nc.get('url', 'example.com')):
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


def visible(role, route, fid=''):
    if not role or role == 'administrateur':
        return True
    fid = fid or next((row[0] for row in FUNCTIONS if row[2] == route), '')
    if fid in ROLES and role not in ROLES[fid]:
        return False
    from .standalone_auth import allowed
    return allowed(role, 'GET', route.split('?', 1)[0])


def entries(cfg=None, role='administrateur', lang=None):
    caps = capabilities(cfg)
    out = []
    for fid, group, route, aliases, needs in FUNCTIONS:
        if not visible(role, route, fid):
            continue
        missing = [c for c in needs if c not in caps]
        out.append({'id': fid, 'group': group, 'route': route, 'aliases': aliases, 'missing': missing, 'famille': FAMILLE.get(fid, ''),
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
    """Panneau « Toutes les fonctions » : 5.6.101 (lot F, F01, V101-07) les 33 familles de l'annexe A, par section, au lieu des fonctions
    une à une ; recherche par nom ou besoin (alias compris) sur toutes les fonctions, affichées « Fonction (famille) »."""
    lang = lang or i18n.current()
    from .navigation5643 import canonical, visibility   # 5.6.43 (cahier v9, § 20) : l'ancien tableau de bord mène toujours à l'accueil unique
    from . import familles56101 as fam
    rows = entries(cfg, role, lang)
    current = fam.fonction_de(fam.chemin_de_page(path)) or for_path(path)
    groups = fam.panneau(rows, current, prefix, lang)
    # Toutes les fonctions restent trouvables par leur nom ou leurs anciens noms ; le résultat ouvre leur page (qui porte la barre de sa
    # famille). Masquées tant que la recherche est vide.
    index = []
    for r in rows:
        kind = visibility(r['id'])
        target = r if kind != 'hidden' else next((x for x in rows if x['id'] == canonical(r['id'])), None)
        if not target:
            continue
        nom = fam.libelle(r['famille'], lang) if r['famille'] else ''
        # 5.6.101 (intégration, P02) : nom et description sont déjà affichés dans l'entrée (la recherche lit aussi le texte affiché) ;
        # data-search ne garde que les anciens noms et la famille
        index.append('<li hidden data-search-only data-search="' + e(' '.join(tuple(r['aliases']) + ((nom,) if nom else ())).lower(), quote=True) + '">'
                     '<a href="' + e(prefix + target['route'], quote=True) + '"><strong>' + e(target['label'] if kind == 'hidden' else fam.titre_fonction(r['id'], lang)) +
                     '</strong><small>' + e(r['desc']) + '</small></a>' +
                     ('<a class="f5631-configure" href="' + e(prefix + r['configure'], quote=True) + '">' + i18n.th('menu.configure', lang) + '</a>' if r['missing'] else '') +
                     '</li>')
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
        if not FAMILLE.get(fid):   # 5.6.101 (F01) : chaque fonction a sa famille
            problems.append((fid, 'famille'))
        if not i18n.catalog('fr').get('fn.%s.label' % fid) or not i18n.catalog('fr').get('fn.%s.desc' % fid):
            problems.append((fid, 'libelle'))
        if fid not in aide5631.articles('fr'):
            problems.append((fid, 'aide'))
        for need in needs:
            if need not in GUIDE_STEP:
                problems.append((fid, 'prerequis'))
    return problems
