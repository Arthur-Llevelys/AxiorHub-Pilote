"""5.6.43 (cahier v9, § 20) : une destination canonique par besoin, menu complémentaire court, onglets de Produire.

Le registre ``fonctions5631.FUNCTIONS`` reste la source unique des fonctions (routes, alias, prérequis, aide). Ce module ajoute, pour
chaque fonction, sa destination canonique et sa visibilité :

- ``primary`` : l'un des six accès principaux (jamais répété dans « Toutes les fonctions ») ;
- ``panel`` : entrée du menu complémentaire (6 à 10 entrées visibles) ;
- ``tab`` : onglet d'une destination principale (Produire) ;
- ``sub`` : sous-section d'une destination ; trouvée par la recherche de fonctions, qui ouvre sa page ;
- ``hidden`` : ancienne entrée retirée du menu ; ses alias renvoient vers la destination canonique.

Aucune page n'est supprimée et aucune route n'est redirigée à l'aveugle : les URL existantes restent servies.
"""
from html import escape as e

PRIMARY = ('aujourdhui', 'dossiers', 'courriels', 'planning', 'production', 'parametres')
CONDITIONAL_PRIMARY = ('talk',)   # § 22 : septième accès principal, seulement pour l'utilisateur qui l'active

# Onglets de Produire (destination unique pour lancer, suivre et relire un travail).
PRODUCTION_TABS = (
    ('production', '/production', 'Résultats'),
    ('studio', '/studio', 'Rédiger'),
    ('documents', '/documents', 'Documents'),
    ('missions_complexes', '/missions-complexes', 'Missions'),
    ('playbooks', '/playbooks', 'Parcours guidés'),
    ('outils_ia', '/outils-ia', 'Outils IA'),
    ('projets', '/projets', 'Projets préparés'),
)
PRODUCTION_PATHS = {route: fid for fid, route, _ in PRODUCTION_TABS}
# Sous-vues demandées par le cahier (/production?vue=…) : elles ouvrent l'onglet correspondant.
PRODUCTION_VIEWS = {'resultats': '/production', 'rediger': '/studio', 'documents': '/documents', 'mission': '/missions-complexes',
                    'parcours': '/playbooks', 'analyses': '/outils-ia', 'projets': '/projets'}

# Menu complémentaire : besoins distincts des six accès principaux (cible 6 à 10 entrées visibles).
PANEL = ('recherche', 'facturation', 'cabinet', 'contacts_crm', 'mon_style', 'corporate', 'assistance_metier', 'assistant', 'guide_utilisation')

# Destination canonique des sous-sections (trouvées par la recherche ; leur page reste servie).
PARENT = {
    # Dossiers et sous-sections du dossier choisi
    'echeances': 'dossiers', 'audience': 'dossiers', 'audiences_word': 'dossiers', 'pieces': 'dossiers', 'pieces_donnees': 'dossiers',
    'profils': 'dossiers', 'strategie': 'dossiers',
    # Agenda et tâches
    'taches': 'planning', 'rendez_vous': 'planning',
    # Produire
    'studio': 'production', 'documents': 'production', 'missions_complexes': 'production', 'playbooks': 'production',
    'outils_ia': 'production', 'projets': 'production', 'agents_documents': 'production', 'missions': 'production',
    # Recherche et ses modes
    'recherche_juridique': 'recherche', 'verification': 'recherche', 'sources': 'recherche', 'veille': 'recherche', 'surveillance': 'recherche',
    # Contacts & CRM
    'contacts': 'contacts_crm',
    # Gestion du cabinet
    'honoraires': 'cabinet', 'relances': 'cabinet', 'conflits': 'cabinet', 'prescriptions': 'cabinet', 'pilotage_mensuel': 'cabinet',
    # Modèles et apprentissage
    'modeles_word': 'mon_style', 'modeles_actes': 'mon_style', 'savoir_faire': 'mon_style', 'securite_ia': 'parametres', 'progres': 'mon_style', 'apprentissage': 'mon_style', 'memoire': 'mon_style',
    # Décisions et travaux : Aujourd'hui / Courriels
    'initiatives': 'aujourdhui', 'engagements': 'aujourdhui', 'associations': 'courriels', 'orchestrateur': 'courriels',
    'accueil_administratif': 'courriels',
    # Paramètres de l'agent, Diagnostic et suivi, Connexions et intégrations
    'agents': 'parametres', 'regles': 'parametres', 'autonomie': 'parametres', 'competences': 'parametres', 'preparation': 'parametres',
    'diagnostic': 'parametres', 'etat_systeme': 'parametres', 'qualite': 'parametres', 'evaluations': 'parametres', 'tracabilite': 'parametres',
    'mise_en_service': 'parametres', 'mcp': 'parametres', 'routage': 'parametres', 'ecosysteme': 'parametres', 'complement_word': 'parametres',
    'guide_installation': 'parametres', 'administration': 'parametres', 'a_propos': 'parametres',
}
HIDDEN = {'tableau_de_bord': 'aujourdhui'}   # ancien tableau de bord : retiré du menu ; « Aujourd'hui » est l'accueil de travail unique
PARENT_LABELS = {'dossiers': 'Dossiers', 'planning': 'Agenda et tâches', 'production': 'Produire', 'recherche': 'Recherche',
                 'contacts_crm': 'Contacts & CRM', 'cabinet': 'Gestion du cabinet', 'mon_style': 'Modèles et apprentissage',
                 'aujourdhui': 'Aujourd’hui', 'courriels': 'Courriels', 'parametres': 'Paramètres'}


def visibility(fid):
    if fid in PRIMARY or fid in CONDITIONAL_PRIMARY:
        return 'primary'
    if fid in PANEL:
        return 'panel'
    if fid in HIDDEN:
        return 'hidden'
    if PARENT.get(fid) == 'production' and fid in PRODUCTION_PATHS.values():
        return 'tab'
    return 'sub'


def canonical(fid):
    return HIDDEN.get(fid) or PARENT.get(fid) or fid


def production_tabs_html(prefix, path):
    """Rangée d'onglets de Produire, rendue sur chacune de ses pages ; l'onglet actif est un lien ordinaire marqué aria-current."""
    current = PRODUCTION_PATHS.get(path)
    if not current:
        return ''
    return ('<nav class="ws-tabs production-tabs" aria-label="Rubriques de Produire">' + ''.join(
        '<a href="' + e(prefix + route, quote=True) + '"' + (' aria-current="page"' if fid == current else '') + '>' + e(label) + '</a>'
        for fid, route, label in PRODUCTION_TABS) + '</nav>')
