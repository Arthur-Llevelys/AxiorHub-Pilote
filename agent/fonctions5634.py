"""5.6.34 (chapitre 7, U05) : matrice de migration des 64 fonctions du registre — destination et fonction conservée, anciens liens.

Le registre reste unique (``fonctions5631``) ; ce module donne, pour CHAQUE identifiant, la destination fixée par le cahier des charges
et les redirections des anciens liens. Une fonction déplacée reste disponible par son service ; masquer un doublon du menu ne supprime
pas son moteur. Un test vérifie que les 64 identifiants ont une ligne et que chaque route répond.
"""
MIGRATION = {
    'aujourdhui': 'Aujourd’hui → conversation, briefing, résumé pleine largeur, décisions ouvrables et annulables, activité et veille',
    'dossiers': 'Dossiers → portefeuille et ouverture de fiche',
    'courriels': 'Courriels → boîte, pièces jointes et brouillons',
    'planning': 'Agenda et tâches → Agenda en ligne (calendriers configurés) / Agenda personnel (local, jamais copié), Tâches, Organiser la semaine',
    'facturation': 'Facturation et honoraires (5.6.53) → page unique : à préparer, documents, règlements, chiffrage (Honorium), réglages et modèles ; épinglable au menu principal',
    'navigateur': 'Navigateur supervisé (5.6.73) → Rechercher : consultation de portails et registres dans une fenêtre visible du poste, actions engageantes validées par l’avocat',
    'savoir_faire': 'Savoir-faire du cabinet (5.6.59) → Modèles et apprentissage : ressources versionnées, anonymisées, approuvées, utilisées par l’agent',
    'securite_ia': 'Sécurité de l’IA (5.6.59) → Paramètres : alertes sur les instructions cachées dans les courriels, documents et images',
    'talk': 'Talk (5.6.43, facultatif) → accès principal après Agenda et tâches, activé par chaque utilisateur dans Paramètres › Connexions',
    'taches': 'Agenda et tâches → vue Tâches',
    'production': 'Produire → choix du travail et validations (Rédiger, Réviser, Bordereau, Mission complète)',
    'documents': 'Produire → Rédiger un document ; moteur conservé',
    'echeances': 'Dossier → Échéances ; vue globale dans Agenda',
    'audience': 'Audiences → Fiche sans nouvel appel IA',
    'audiences_word': 'Audiences → Préparer ; révision déplacée dans Produire › Réviser',
    'missions_complexes': 'Produire → Mission complète et sous-tâches',
    'pieces': 'Dossier → Pièces ; bordereau accessible depuis Produire',
    'profils': 'Paramètres → Profils procéduraux validés',
    'strategie': 'Dossier → Arguments et analyse de scénarios',
    'studio': 'Produire → modes avancés de rédaction',
    'playbooks': 'Produire → Parcours guidés',
    'recherche': 'Recherche → Fichiers et messages du cabinet',
    'recherche_juridique': 'Recherche → Textes et décisions (question générale sans dossier) ; même service dans le Dossier',
    'verification': 'Recherche → Vérifier une référence ; contrôle dans Produire',
    'sources': 'Paramètres → Sources juridiques ; raccourci dans Recherche',
    'veille': 'Aujourd’hui → Veille ; paramètres des thèmes et sources',
    'surveillance': 'Aujourd’hui → Alertes du cabinet ; accès global conservé',
    'engagements': 'Courriels → Suites à donner ; tâches liées visibles dans Agenda',
    'accueil_administratif': 'Communications → Accueil téléphonique et messages',
    'contacts': 'Communications → Contacts ; choix contextuel du destinataire',
    'associations': 'Courriels → Rattachements à confirmer',
    'orchestrateur': 'Aujourd’hui → Activité et avis à valider',
    'missions': 'Aujourd’hui → Confier une mission ; suivi commun dans Produire',
    'assistant': 'Aujourd’hui et Dossier → conversation avec l’agent',
    'cabinet': 'Gestion du cabinet → vue de synthèse',
    'honoraires': 'Gestion du cabinet → Temps et facturation',
    'rendez_vous': 'Agenda → Rendez-vous et propositions de créneaux',
    'conflits': 'Gestion du cabinet → Conflits ; contrôles dans Dossier',
    'prescriptions': 'Dossier → Délais et prescription ; vue globale Agenda',
    'pilotage_mensuel': 'Gestion du cabinet → Indicateurs mensuels',
    'assistance_metier': 'Gestion du cabinet → Calculs et assistance métier',
    'modeles_word': 'Paramètres → Modèles du cabinet ; choix dans Produire',
    'modeles_actes': 'Paramètres → Modèles et mentions des actes',
    'mon_style': 'Paramètres → Style du cabinet',
    'progres': 'Paramètres → Style et apprentissage ; même espace que Mon style',
    'agents': 'Paramètres → Agents et automatisations',
    'agents_documents': 'Produire → Résultats et reprises des agents',
    'regles': 'Paramètres → Automatisations et règles',
    'autonomie': 'Paramètres → Automatisations et niveau d’autorisation',
    'apprentissage': 'Paramètres → Préférences métier validées',
    'competences': 'Paramètres → Compétences ; sélection dans une demande',
    'projets': 'Produire → Travaux et validations en attente',
    'preparation': 'Produire → Travaux proposés ; réglage dans Automatisations',
    'memoire': 'Dossier → Mémoire et corrections ; préférences dans Paramètres',
    'parametres': 'Paramètres → accueil des réglages et diagnostics',
    'guide_installation': 'Paramètres → Configurer pas à pas',
    'guide_utilisation': 'Paramètres → Guide d’utilisation et aide contextuelle',
    'mise_en_service': 'Paramètres → Mise en service ; parcours administrateur avancé',
    'diagnostic': 'Paramètres → État et aide',
    'etat_systeme': 'Paramètres → État et aide ; incidents techniques (selon le mode d’installation)',
    'mcp': 'Paramètres → Connexions ; sources juridiques et autres MCP',
    'routage': 'Paramètres → IA et choix local ou externe',
    'evaluations': 'Paramètres → Évaluations métier ; rôle autorisé',
    'tracabilite': 'Paramètres → Journaux ; preuve contextualisée dans chaque résultat',
    'qualite': 'Produire → Contrôles ; vue globale dans État et aide',
    'ecosysteme': 'Paramètres → Connexions et services',
    'administration': 'Paramètres → Administration ; accès selon le rôle',
    'tableau_de_bord': 'Gestion du cabinet → Tableau de bord détaillé',
    'a_propos': 'Paramètres → À propos, version, licence et source',
    # 5.6.36 : fonctions nouvelles (aucun ancien lien à rediriger)
    'contacts_crm': 'Toutes les fonctions → Contacts & CRM ; bloc « Intervenants » dans le Dossier',
    'initiatives': 'Toutes les fonctions → Initiatives ; « Proposer un plan d’actions » depuis un courriel',
    'pieces_donnees': 'Dossier → Pièces › Données des pièces ; recherche dans tout le cabinet',
    'outils_ia': 'Toutes les fonctions → Outils IA (grilles, playbooks, mémos) ; playbooks aussi dans le complément Word',
    'corporate': 'Toutes les fonctions → Corporate (sociétés, opérations, actes, table de capitalisation, registres)',
    'relances': 'Toutes les fonctions → Relances d’impayés ; carte dans Initiatives',
    'complement_word': 'Toutes les fonctions → Complément Word (manifeste et volet)',
}
# Anciens liens → nouveau parcours (le dossier « matter » est conservé) ; les liens de contrôle (hearing=, word=, job=) restent servis.
REDIRECTS = {'/audience': '/audiences', '/audiences-word': '/audiences'}
NEW_ROUTES = ('/audiences', '/reviser', '/decision', '/surveillance', '/recherche-juridique')


def check():
    """Identifiants sans ligne de migration, ou lignes orphelines."""
    from .fonctions5631 import INDEX
    return sorted(set(INDEX) ^ set(MIGRATION))
