"""5.6.89 (chapitre 18) : une fiche de capacité par fonction du registre (``fonctions5631``), et les actions du panneau IA.

La fiche dit ce qui existe aujourd'hui, pas ce que l'annexe propose. Trois statuts, jamais confondus :

- ``livree`` : l'action passe par un service existant du dépôt (``services`` en donne l'adresse, un test vérifie qu'elle existe) ;
- ``preparee_sans_connecteur`` : le panneau prépare un texte, mais aucun connecteur n'écrit dans l'outil : la dernière étape reste manuelle et ``manuel`` le dit ;
- ``proposition`` : capacité de service de l'annexe (``capacites_proposees``, ``proposition_annexe``). C'est un contrat de développement : elle n'apparaît jamais dans les actions du panneau.

« Livrée » ne veut pas dire « qualifiée ». L'état réel de la fonction vient de ``fonctions_etat5689`` et s'affiche à côté : testée en simulation, service non configuré, dégradée.

Une action dépend de la page, de l'objet et des droits de la personne : être sur une page n'autorise aucune écriture. Une action qui prépare un effet externe ne l'exécute jamais ; ``garde`` nomme la décision humaine
qui reste à prendre (envoyer, signer, déposer, facturer, payer), dans le service qui en a la charge et qui refait ses contrôles. ``iframe`` : une page intégrée n'est pas un connecteur, la lecture de l'écran ne contourne rien.
"""
from html import escape as e

from . import fonctions5631 as registre
from .common import Stop

NIVEAUX = {'observer': 'Observer', 'proposer': 'Proposer', 'preparer': 'Préparer en autonomie'}
RANG_NIVEAU = ('observer', 'proposer', 'preparer')
EFFETS = {'lecture': 'Lecture seule', 'proposition': 'Proposition, rien n’est enregistré', 'brouillon_interne': 'Brouillon interne à relire', 'externe_prepare': 'Préparé seulement : la décision et l’exécution restent à vous'}
GARDES = {'envoyer': 'Envoyer', 'signer': 'Signer', 'deposer': 'Déposer', 'facturer': 'Facturer', 'payer': 'Payer'}
STATUTS = {'livree': 'Disponible', 'preparee_sans_connecteur': 'Préparé ici, à terminer à la main', 'proposition': 'Proposition, pas encore une fonction'}
DISPONIBILITES = {'existe': 'Service existant', 'prepare_sans_connecteur': 'Préparé sans connecteur d’écriture'}


def _a(libelle, instruction, attendu, niveau, effet, circuit='assistance', requiert=(), droit='', categorie='', garde='', statut='livree', taches=(), besoins=()):
    return {'libelle': libelle, 'instruction': instruction, 'attendu': attendu, 'niveau': niveau, 'effet': effet, 'circuit': circuit, 'requiert': requiert, 'droit': droit, 'categorie': categorie, 'garde': garde, 'statut': statut, 'taches': taches, 'besoins': besoins}


def _t(ident, libelle, requis=(), depend_de=()):
    """Tâche d'une action : ``requis`` : champs à renseigner, sans lesquels le résultat reste un brouillon partiel ; ``depend_de`` : tâches à attendre. Les autres tâches avancent seules."""
    return {'id': ident, 'libelle': libelle, 'requis': requis, 'depend_de': depend_de}


# ``circuit`` : ``assistance`` passe par l'ordre commun (``ui_ai5689``, ``chaine5689``) et rend le résultat normalisé ; ``pilote`` remplit l'instruction de l'assistant existant, qui garde ses producteurs et ses preuves de dépôt.
ACTIONS = {
    'expliquer': _a('Expliquer cette page', 'Explique à quoi sert cette page et ce que je peux y faire.', 'answer', 'observer', 'lecture'),
    'synthetiser': _a('Synthétiser', 'Fais une synthèse de ce qui est sélectionné, en citant les sources.', 'answer', 'observer', 'lecture', requiert=('sujet',)),
    'nouveautes': _a('Expliquer les nouveautés', 'Explique ce qui est nouveau dans ce dossier et ce que cela change.', 'answer', 'observer', 'lecture', requiert=('dossier',)),
    'retrouver_preuve': _a('Retrouver une preuve', 'Retrouve dans les sources autorisées ce qui établit le point demandé, avec version et emplacement.', 'analysis', 'observer', 'lecture', requiert=('dossier',)),
    'chronologie': _a('Établir une chronologie', 'Établis une chronologie sourcée de ce dossier. Une date inconnue reste inconnue.', 'analysis', 'proposer', 'proposition', requiert=('dossier',)),
    'contradictions': _a('Repérer contradictions et manques', 'Repère les contradictions entre les sources et les informations manquantes, sans trancher.', 'analysis', 'proposer', 'proposition', requiert=('dossier',)),
    'prochaine_etape': _a('Proposer la prochaine étape', 'Propose la prochaine étape utile et les pièces à réunir, en citant les sources.', 'action_proposal', 'proposer', 'proposition', requiert=('dossier',)),
    'suites': _a('Extraire les suites', 'Relève les suites à donner et leur auteur.', 'action_proposal', 'proposer', 'proposition', requiert=('objet',), categorie='courriels'),
    'rattacher': _a('Proposer un rattachement', 'Propose le dossier auquel rattacher ce message et explique pourquoi. Ne choisis rien sans élément certain.', 'action_proposal', 'proposer', 'proposition', requiert=('objet',), categorie='courriels'),
    'repondre_courriel': _a('Préparer une réponse', 'Prépare un projet de réponse à relire.', 'mail_draft', 'preparer', 'brouillon_interne', circuit='pilote', requiert=('objet',), droit='deposer', categorie='courriels', garde='envoyer', besoins=('mail', 'ai')),
    'preparer_document': _a('Préparer un document', 'Prépare un projet depuis un modèle validé du cabinet, dans ce dossier.', 'document', 'preparer', 'brouillon_interne', circuit='pilote', requiert=('dossier',), categorie='documents', besoins=('storage', 'ai')),
    'comparer_versions': _a('Comparer les versions', 'Compare cette version à la précédente et liste les changements, en citant chaque version.', 'analysis', 'proposer', 'proposition', requiert=('objet',), categorie='documents'),
    'analyser_modifications': _a('Préparer la relecture', 'Analyse les modifications et prépare les points à relire. Ne modifie pas le document.', 'analysis', 'proposer', 'proposition', requiert=('objet',), categorie='documents'),
    'preparer_audience': _a('Préparer l’audience', 'Prépare l’audience : chronologie, pièces utiles, trame des arguments.', 'analysis', 'preparer', 'brouillon_interne', requiert=('dossier',), categorie='agenda',
                            taches=(_t('chronologie', 'Chronologie sourcée'), _t('pieces', 'Pièces utiles'), _t('trame', 'Trame des arguments', requis=('date_audience', 'juridiction'), depend_de=('chronologie',)))),
    'qualifier_operation': _a('Qualifier l’opération', 'Qualifie l’opération et liste actes, formalités et conditions. Un projet n’est pas une opération réalisée.', 'analysis', 'proposer', 'proposition', requiert=('dossier',)),
    'verifier_conditions': _a('Vérifier conditions et preuves', 'Vérifie chaque condition de l’opération et la preuve qui l’établit. Dis ce qui manque.', 'analysis', 'proposer', 'proposition', requiert=('dossier',)),
    'proposer_diligences': _a('Proposer des diligences', 'Propose des diligences et un budget sourcés. Montants et émission restent contrôlés par le service de facturation.', 'action_proposal', 'proposer', 'proposition', requiert=('dossier',), droit='voir_honoraires', categorie='temps'),
    'preparer_facturation': _a('Préparer un projet financier', 'Prépare le projet de devis, de convention ou de facture de ce dossier.', 'action_proposal', 'preparer', 'externe_prepare', circuit='pilote', requiert=('dossier',), droit='voir_honoraires', categorie='factures', garde='facturer'),
    'proposer_fiche': _a('Proposer une fiche', 'Propose une fiche à partir des données observées, en distinguant observé et validé.', 'action_proposal', 'proposer', 'proposition', categorie='contacts'),
    'dedoublonner': _a('Repérer des doublons', 'Repère les fiches qui pourraient être en double. Ne fusionne rien.', 'action_proposal', 'proposer', 'proposition', categorie='contacts'),
    'preparer_relance': _a('Préparer une relance', 'Prépare un projet de relance à relire.', 'mail_draft', 'preparer', 'externe_prepare', circuit='pilote', requiert=('dossier',), droit='deposer', categorie='contacts', garde='envoyer', besoins=('mail', 'ai')),
    'rechercher_citer': _a('Rechercher et citer', 'Recherche les sources utiles, cite-les avec version et date, et explique leur portée.', 'answer', 'observer', 'lecture', circuit='pilote'),
    'incidence': _a('Expliquer l’incidence sur le dossier', 'Explique l’incidence de ces sources sur ce dossier. Indique date et périmètre des sources.', 'analysis', 'proposer', 'proposition', requiert=('dossier',)),
    'expliquer_incident': _a('Expliquer un état ou un incident', 'Explique l’état affiché ici et sa cause probable. Dis ce que je dois vérifier.', 'answer', 'observer', 'lecture'),
    'proposer_reglage': _a('Proposer un réglage', 'Propose un réglage et ses conséquences. N’applique rien. Aucun secret, aucun changement de droits.', 'action_proposal', 'proposer', 'proposition'),
    'preparer_texte': _a('Préparer un texte à copier', 'Prépare un texte court que je copierai moi-même dans l’outil.', 'action_proposal', 'proposer', 'proposition', statut='preparee_sans_connecteur'),
}

# Chapitre 18 : neuf familles de travail. ``categorie`` : rubrique de « Ce que l'IA peut voir » ; ``services`` : adresses réelles du dépôt, valables pour toute la famille.
FAMILLES = {
    'courriels': {'libelle': 'Courriels et messagerie', 'categorie': 'courriels', 'actions': ('expliquer', 'synthetiser', 'suites', 'rattacher', 'repondre_courriel'), 'services': ('missions567.create', 'desk.report_for'),
                  'contexte': ('objet',), 'droits': 'Lecture selon le dossier ; brouillon : droit de dépôt. Envoi : droit d’envoi, validation humaine.', 'preuve': 'Réponse et sources, ou brouillon relu dans le dossier Brouillons.',
                  'limites': ('Un courriel affiché dans un cadre n’est pas une lecture par service : seul un service autorisé lit ou envoie.', 'Rien n’est envoyé par l’IA.')},
    'dossier': {'libelle': 'Dossier et pièces', 'categorie': 'documents', 'actions': ('expliquer', 'synthetiser', 'nouveautes', 'retrouver_preuve', 'chronologie', 'contradictions', 'prochaine_etape'), 'services': ('missions567.create', 'contexte5651.for_drafting'),
                'contexte': ('dossier', 'objet', 'version'), 'droits': 'Dossier réservé : réponse refusée avant toute recherche.', 'preuve': 'Réponse avec sources, version et emplacement.',
                'limites': ('Une allégation reste attribuée à son auteur.', 'Une date inconnue reste inconnue.')},
    'documents': {'libelle': 'Documents et modèles', 'categorie': 'documents', 'actions': ('expliquer', 'synthetiser', 'comparer_versions', 'analyser_modifications', 'contradictions', 'preparer_document'), 'services': ('missions567.create', 'docrequest520.submit', 'livrables5689.charger'),
                  'contexte': ('dossier', 'objet', 'version'), 'droits': 'Lecture selon le dossier. Une proposition de modification n’est appliquée que sur décision de l’avocat.', 'preuve': 'Version vérifiée du document, ou proposition liée à sa version de base.',
                  'limites': ('Un modèle non validé par le cabinet n’est pas proposé comme tel.', 'Le document n’est jamais modifié depuis le panneau.')},
    'agenda': {'libelle': 'Agenda et audiences', 'categorie': 'agenda', 'actions': ('expliquer', 'preparer_audience', 'prochaine_etape', 'synthetiser'), 'services': ('missions567.create', 'delais5689.calculer'),
               'contexte': ('dossier', 'objet'), 'droits': 'Lecture selon le dossier.', 'preuve': 'Réponse et sources ; une date calculée vient du moteur de règles, avec sa règle.',
               'limites': ('Aucune date de procédure n’est calculée par le modèle.',)},
    'corporate': {'libelle': 'Corporate et cessions', 'categorie': 'documents', 'actions': ('expliquer', 'qualifier_operation', 'verifier_conditions', 'preparer_document'), 'services': ('missions567.create', 'corporate5636.company_save', 'operations5683.open_cession'),
                  'contexte': ('dossier',), 'droits': 'Lecture selon le dossier.', 'preuve': 'Réponse et sources, ou acte vérifié dans le dossier.',
                  'limites': ('Un projet n’est pas une opération réalisée.', 'Les formalités restent à déposer par une personne.')},
    'temps': {'libelle': 'Temps et honoraires', 'categorie': 'factures', 'actions': ('expliquer', 'proposer_diligences', 'preparer_facturation'), 'services': ('missions567.create', 'facturation5614.missing_for'),
              'contexte': ('dossier',), 'droits': 'Droit de voir les honoraires. Émission : droit de facturation, validation humaine.', 'preuve': 'Proposition structurée ; montants et numéro viennent du service de facturation.',
              'limites': ('Aucun montant n’est décidé par le modèle.', 'Aucune facture n’est émise depuis le panneau.')},
    'crm': {'libelle': 'CRM et relations', 'categorie': 'contacts', 'actions': ('expliquer', 'proposer_fiche', 'dedoublonner', 'preparer_relance'), 'services': ('missions567.create', 'crm5636.find_or_create'),
            'contexte': ('objet',), 'droits': 'Lecture des contacts selon les droits.', 'preuve': 'Proposition structurée, à valider dans la fiche.',
            'limites': ('Une donnée observée n’est pas une donnée validée.', 'Aucune fusion sans décision.')},
    'recherche': {'libelle': 'Recherche et veille', 'categorie': '', 'actions': ('expliquer', 'rechercher_citer', 'incidence'), 'services': ('missions567.create', 'legal_research.authorities'),
                  'contexte': ('filtres',), 'droits': 'Recherche limitée aux sources autorisées.', 'preuve': 'Réponse avec version, date et périmètre des sources.',
                  'limites': ('Une référence non vérifiée est dite « à vérifier ».',)},
    'reglages': {'libelle': 'Réglages et sécurité', 'categorie': '', 'actions': ('expliquer', 'expliquer_incident', 'proposer_reglage'), 'services': ('missions567.create', 'aide5631.articles'),
                 'contexte': (), 'droits': 'Lecture selon le rôle. Modification : compte autorisé, sur la page du réglage.', 'preuve': 'Explication et proposition ; rien n’est appliqué.',
                 'limites': ('Aucun accès aux secrets.', 'Aucun changement de droits sur instruction d’un document.')},
}

# Annexe (``function_capability_matrix_proposed``) : capacités de service proposées, par groupe du registre. Elles n'existent pas sous ces noms.
PROPOSEES = {'communiquer': ('communication.read', 'communication.prepare'), 'dossier': ('matter.read', 'document.read', 'document.propose'), 'rechercher': ('search.authorized', 'source.read'),
             'temps': ('time.read', 'billing.prepare'), 'modeles': ('template.read', 'document.prepare'), 'regler': ('help.read', 'diagnostic.read')}
AIDE_SEULE = ('mon_compte', 'journal_audit', 'vue_ia', 'administration')       # l'annexe les borne à l'aide : aucune lecture de secret ni changement autonome des droits
MANUEL_TALK = 'Talk est affiché dans un cadre : AxiorHub n’y lit rien et n’y écrit rien. Copiez le texte préparé et envoyez-le vous-même. Le salon d’une réunion peut être préparé par les automatismes du rendez-vous, si le cabinet les a activés.'
MANUEL_DECK = 'Deck : seul le tableau de suivi des travaux de l’agent est synchronisé, si le cabinet l’a activé. Une carte demandée ici reste à créer à la main.'
MANUEL_NOTES = 'Notes : lu par le point hebdomadaire seulement, rien n’y est écrit. Une note préparée ici reste à coller à la main.'
# Outils intégrés (§ 18) : la disponibilité réelle dépend du connecteur.
OUTILS_INTEGRES = {'talk': {'libelle': 'Talk', 'lecture': False, 'ecriture': False, 'manuel': MANUEL_TALK, 'services': ('talk5643.settings_html', 'talk568.prepare')},
                   'deck': {'libelle': 'Deck', 'lecture': True, 'ecriture': False, 'manuel': MANUEL_DECK, 'services': ('deck530.sync',)},
                   'notes': {'libelle': 'Notes', 'lecture': True, 'ecriture': False, 'manuel': MANUEL_NOTES, 'services': ('routines520.ensure_schema',)}}

# id, famille, action utile aujourd'hui, services propres, actions (None : celles de la famille), outils intégrés sans connecteur d'écriture, limites propres
LIGNES = (
    ('aujourdhui', 'dossier', 'Faire le point de la journée à partir des travaux préparés, décisions et échéances autorisés.', ('accueil5689.page',), ('expliquer', 'synthetiser', 'expliquer_incident'), ('deck',), ()),
    ('dossiers', 'dossier', 'Synthétiser un dossier, retrouver une preuve, repérer ce qui manque et proposer la suite.', ('portfolio.ensure_schema',), None, (), ()),
    ('courriels', 'courriels', 'Synthétiser un courriel, extraire les suites, proposer un rattachement, préparer une réponse à relire.', ('boite5625.listing',), None, (), ()),
    ('planning', 'agenda', 'Préparer la semaine et proposer les suites depuis les événements sourcés.', ('agenda5634.create',), ('expliquer', 'synthetiser', 'preparer_audience', 'prochaine_etape'), ('deck',), ()),
    ('taches', 'agenda', 'Prioriser les tâches et signaler les blocages.', ('taches5688.create',), ('expliquer', 'synthetiser', 'prochaine_etape'), ('deck',), ()),
    ('mes_taches', 'agenda', 'Résumer ses tâches et ce qui attend une décision.', ('taches5688.create',), ('expliquer', 'synthetiser', 'prochaine_etape'), ('deck',), ()),
    ('talk', 'courriels', 'Préparer un message ou un compte rendu à copier dans Talk.', ('talk5643.settings_html',), ('expliquer', 'preparer_texte'), ('talk',), ('Une page intégrée n’est pas un connecteur.',)),
    ('production', 'documents', 'Faire le point sur les documents en préparation et préparer un projet.', ('production390.ensure_schema',), None, (), ()),
    ('documents', 'documents', 'Préparer un document depuis un modèle validé, comparer des versions, préparer la relecture.', ('document_projects.ensure_schema',), None, (), ()),
    ('echeances', 'agenda', 'Expliquer une échéance et sa source.', ('echeances450.ensure_schema',), ('expliquer', 'synthetiser', 'prochaine_etape'), (), ()),
    ('audience', 'agenda', 'Préparer l’audience : chronologie, pièces utiles, trame des arguments.', ('audience5613.sheet',), None, (), ()),
    ('audiences_suivi', 'agenda', 'Faire le point sur les audiences à venir et ce qui reste à faire.', ('audiences5688.listing',), None, (), ()),
    ('audiences_word', 'documents', 'Préparer une trame de plaidoirie et préparer la relecture d’un document.', ('audience5613.sheet',), None, (), ()),
    ('missions_complexes', 'dossier', 'Expliquer l’état d’une mission complexe et ce qui l’attend.', ('taches5614.create',), ('expliquer', 'synthetiser', 'contradictions', 'prochaine_etape'), (), ()),
    ('pieces', 'dossier', 'Repérer les pièces manquantes et préparer un bordereau à relire.', ('pieces510.ensure_schema',), ('expliquer', 'synthetiser', 'retrouver_preuve', 'contradictions'), (), ()),
    ('contentieux', 'dossier', 'Lire la matrice des prétentions, repérer une preuve manquante et proposer la suite.', ('contentieux5689.amorcer',), None, (), ('Une date de procédure vient du moteur de délais, jamais du modèle.',)),
    ('pieces_donnees', 'dossier', 'Retrouver une donnée dans les pièces, avec son emplacement.', ('ged5636.analyse',), ('expliquer', 'retrouver_preuve', 'contradictions'), (), ()),
    ('outils_ia', 'documents', 'Choisir une grille d’analyse et comparer des versions.', ('outils5636.grid_run',), None, (), ()),
    ('corporate', 'corporate', 'Qualifier une opération, préparer les actes depuis les statuts et les détentions, vérifier conditions et preuves.', (), None, (), ()),
    ('cessions', 'corporate', 'Qualifier une cession de fonds ou de bail, préparer actes et formalités depuis le dossier.', ('cessions5684.create',), None, (), ()),
    ('formulaires', 'documents', 'Expliquer un formulaire officiel et ce qui manque pour le remplir.', ('formulaires5683.catalogue',), ('expliquer', 'synthetiser', 'contradictions'), (), ('Le formulaire rempli reste à relire et à déposer par une personne.',)),
    ('profils', 'dossier', 'Expliquer un profil de procédure approuvé.', ('profils5614.listing',), ('expliquer',), (), ()),
    ('strategie', 'dossier', 'Préparer les éléments d’une question stratégique, sans trancher.', ('strategic.ensure_schema',), ('expliquer', 'synthetiser', 'contradictions', 'prochaine_etape'), (), ()),
    ('studio', 'documents', 'Préparer un projet et comparer des versions.', ('production420.ensure_schema',), None, (), ()),
    ('playbooks', 'documents', 'Expliquer un parcours guidé et préparer le projet correspondant.', ('playbook5689.preparer_mission',), None, (), ()),
    ('recherche', 'recherche', 'Rechercher dans les sources autorisées du cabinet, citer et expliquer.', ('search490.build_query',), None, (), ()),
    ('recherche_juridique', 'recherche', 'Rechercher des textes et décisions, citer version et date, expliquer l’incidence sur le dossier.', (), None, (), ('Sans service juridique configuré, aucune décision n’est présentée comme vérifiée.',)),
    ('verification', 'recherche', 'Expliquer le résultat d’un contrôle de références.', ('verification5689.controler',), None, (), ()),
    ('sources', 'recherche', 'Expliquer le périmètre et la date des sources connectées.', ('sources470.ensure_schema',), ('expliquer', 'rechercher_citer'), (), ()),
    ('veille', 'recherche', 'Expliquer une nouveauté et son incidence sur un dossier.', ('veille5632.collect',), None, (), ()),
    ('surveillance', 'recherche', 'Expliquer une alerte et ce qu’elle demande.', ('surveillance5671.after_calendar',), ('expliquer', 'expliquer_incident'), (), ()),
    ('engagements', 'courriels', 'Relever les promesses et suites à tenir.', ('followup568.scan',), ('expliquer', 'synthetiser', 'suites'), (), ()),
    ('accueil_administratif', 'courriels', 'Résumer un appel et préparer un message à relire.', ('reception567.capabilities',), ('expliquer', 'synthetiser'), (), ('Aucun appel ni message n’est émis par l’IA.',)),
    ('contacts', 'crm', 'Retrouver un correspondant et proposer une fiche.', ('crm5636.find_or_create',), ('expliquer', 'proposer_fiche', 'dedoublonner'), (), ()),
    ('contacts_crm', 'crm', 'Proposer une fiche, repérer des doublons, préparer une relance et une suite de mission.', ('crm5676.ensure_schema',), None, (), ()),
    ('initiatives', 'courriels', 'Expliquer un plan d’actions proposé.', ('initiatives5636.analyse',), ('expliquer', 'synthetiser', 'suites'), (), ()),
    ('associations', 'courriels', 'Expliquer une association proposée entre une adresse et un dossier.', ('association5667.learned',), ('expliquer', 'synthetiser'), (), ()),
    ('orchestrateur', 'courriels', 'Expliquer un avis et la suite proposée.', ('orchestrator.ensure_schema',), ('expliquer', 'synthetiser'), (), ()),
    ('missions', 'courriels', 'Faire le point sur les missions et ce qui attend une décision.', ('missions567.listing',), ('expliquer', 'synthetiser'), (), ()),
    ('assistant', 'courriels', 'Converser avec l’assistant sur le contexte choisi.', ('assistant567.profile',), ('expliquer',), (), ()),
    ('cabinet', 'temps', 'Expliquer un indicateur de gestion.', ('cabinet560.identity',), ('expliquer', 'proposer_diligences'), (), ()),
    ('facturation', 'temps', 'Préparer devis, convention ou projet de facture depuis les diligences et accords sourcés.', ('facturation5653.ensure_schema',), None, (), ()),
    ('honoraires', 'temps', 'Proposer des diligences et un budget sourcés.', ('time500.add_manual',), None, (), ()),
    ('relances', 'temps', 'Expliquer un impayé et préparer une relance à relire.', ('relances5636.configured',), ('expliquer', 'preparer_relance'), (), ('Aucune relance n’est envoyée par l’IA.',)),
    ('contribution', 'temps', 'Expliquer un relevé de contribution.', ('contribution5688.ensure_schema',), ('expliquer',), (), ()),
    ('rendez_vous', 'temps', 'Préparer un rendez-vous à partir du dossier.', ('agenda5625.create',), ('expliquer', 'proposer_diligences'), (), ()),
    ('conflits', 'temps', 'Expliquer un résultat de contrôle de conflit, sans le lever.', ('conflicts500.check',), ('expliquer',), (), ('La décision sur un conflit reste à l’avocat.',)),
    ('prescriptions', 'agenda', 'Expliquer une prescription relevée et sa source.', ('limitation500.compute',), ('expliquer', 'synthetiser'), (), ('Aucune date n’est calculée par le modèle.',)),
    ('pilotage_mensuel', 'temps', 'Expliquer un indicateur mensuel.', ('report500.build',), ('expliquer',), (), ()),
    ('assistance_metier', 'temps', 'Expliquer un calcul : les chiffres viennent du service de calcul.', ('metier500.ensure_schema',), ('expliquer',), (), ()),
    ('modeles_word', 'documents', 'Expliquer un modèle et ses balises.', ('cabinet_docs33.ensure_schema',), ('expliquer', 'comparer_versions'), (), ()),
    ('modeles_actes', 'documents', 'Expliquer un modèle d’acte et ses mentions obligatoires.', ('templates470.listing',), ('expliquer', 'comparer_versions'), (), ()),
    ('mon_style', 'documents', 'Expliquer le style appris.', ('style550.ensure_schema',), ('expliquer',), (), ()),
    ('complement_word', 'documents', 'Expliquer le complément Word.', ('office5636.context',), ('expliquer',), (), ('Le complément Word s’installe et se lance à la main.',)),
    ('progres', 'documents', 'Expliquer les progrès du style.', ('learning480.ensure_schema',), ('expliquer',), (), ()),
    ('agents', 'reglages', 'Expliquer un agent et ses règles.', ('assistant567.profile',), ('expliquer', 'proposer_reglage'), (), ()),
    ('agents_documents', 'documents', 'Expliquer le résultat d’un agent et ce qu’il reste à reprendre.', ('file5657.mission_text',), ('expliquer', 'synthetiser'), (), ()),
    ('regles', 'reglages', 'Proposer une règle de tri sans l’enregistrer ; expliquer les règles actives.', ('rules480.ensure_schema',), None, (), ()),
    ('autonomie', 'reglages', 'Expliquer les niveaux d’autonomie.', ('autonomy480.ensure_schema',), None, (), ('Le niveau d’autonomie se règle sur sa page, jamais depuis un document.',)),
    ('apprentissage', 'reglages', 'Expliquer une règle métier apprise.', ('apprentissage5651.ensure_schema',), ('expliquer',), (), ()),
    ('mobile', 'courriels', 'Expliquer ce qui attend une validation sur le téléphone.', ('appareils5674.pairing',), ('expliquer',), (), ()),
    ('navigateur', 'recherche', 'Expliquer le navigateur supervisé et ce qu’il peut lire.', ('navigateur5673.ensure_schema',), ('expliquer',), (), ('Toute action sur un site reste confirmée par une personne.',)),
    ('savoir_faire', 'documents', 'Retrouver une clause ou une structure approuvée.', ('savoir5659.ensure_schema',), ('expliquer', 'synthetiser'), (), ()),
    ('competences', 'reglages', 'Expliquer une compétence et ses limites.', ('competences5632.resolve',), ('expliquer',), (), ()),
    ('projets', 'documents', 'Faire le point sur les projets en attente.', ('document_projects.ensure_schema',), ('expliquer', 'synthetiser'), (), ()),
    ('livrables', 'documents', 'Expliquer un document préparé, comparer ses versions et préparer sa relecture.', ('livrables5689.projection',), ('expliquer', 'synthetiser', 'comparer_versions', 'analyser_modifications', 'contradictions'), (), ()),
    ('preparation', 'dossier', 'Expliquer la préparation proposée.', ('proactive568.control',), ('expliquer', 'synthetiser'), (), ()),
    ('memoire', 'dossier', 'Retrouver une réponse déjà envoyée ; préparer une note à copier.', ('memory.SentMemory',), ('expliquer', 'synthetiser', 'preparer_texte'), ('notes',), ()),
    ('parametres', 'reglages', 'Expliquer un réglage et proposer une action contrôlée.', ('parametres5621.page',), None, (), ()),
    ('guide_installation', 'reglages', 'Expliquer une étape d’installation.', ('guide5631.api',), ('expliquer', 'expliquer_incident'), (), ()),
    ('guide_utilisation', 'reglages', 'Trouver la page utile et expliquer comment s’en servir.', ('guide5631.api',), ('expliquer',), (), ()),
    ('mise_en_service', 'reglages', 'Expliquer une étape de mise en service.', ('setup560.page',), ('expliquer', 'expliquer_incident'), (), ()),
    ('diagnostic', 'reglages', 'Expliquer pourquoi rien n’est produit.', ('diagnostic5634.alerts',), ('expliquer', 'expliquer_incident'), (), ()),
    ('etat_systeme', 'reglages', 'Expliquer un incident en cours.', ('sante5636.checks',), ('expliquer', 'expliquer_incident'), (), ()),
    ('mcp', 'reglages', 'Expliquer les connecteurs actifs.', ('mcp5614.preferred',), None, (), ()),
    ('routage', 'reglages', 'Expliquer le choix d’un modèle.', ('routage5682.for_job',), None, (), ()),
    ('evaluations', 'reglages', 'Expliquer un résultat d’évaluation.', ('evaluation410.ensure_schema',), ('expliquer',), (), ()),
    ('securite_ia', 'reglages', 'Expliquer une alerte de sécurité de l’IA.', ('bouclier5659.ensure_schema',), ('expliquer', 'expliquer_incident'), (), ()),
    ('tracabilite', 'reglages', 'Expliquer ce qu’un modèle a vu pour une réponse.', ('trace480.ensure_schema',), ('expliquer',), (), ()),
    ('mon_compte', 'reglages', 'Expliquer les options du compte.', ('securite5688.allowed',), ('expliquer',), (), ('Aucune lecture de secret ni changement autonome des droits.',)),
    ('journal_audit', 'reglages', 'Expliquer une ligne du journal.', ('securite5688.allowed',), ('expliquer',), (), ('Aucune lecture de secret ni changement autonome des droits.',)),
    ('vue_ia', 'reglages', 'Expliquer ce que l’IA peut voir.', ('vueia5688.effective',), ('expliquer',), (), ('Aucune lecture de secret ni changement autonome des droits.',)),
    ('gouvernance_ia', 'reglages', 'Expliquer la politique des données et le budget.', ('data_policy5689.etat_public',), ('expliquer', 'expliquer_incident'), (), ('Aucun changement de politique depuis le panneau.',)),
    ('banc_essai', 'reglages', 'Expliquer le protocole d’essai et l’état des fonctions.', ('banc5689.tableau', 'fonctions_etat5689.tableau'), ('expliquer',), (), ('Un essai sur fournisseur simulé ne mesure pas la qualité juridique.',)),
    ('qualite', 'reglages', 'Expliquer les motifs de blocage.', ('control470.quality',), ('expliquer', 'expliquer_incident'), (), ()),
    ('ecosysteme', 'reglages', 'Expliquer les services du cabinet.', ('nextcloud5636.folders',), ('expliquer',), (), ()),
    ('administration', 'reglages', 'Expliquer les raccourcis d’administration.', ('securite5688.allowed',), ('expliquer',), (), ('Aucune lecture de secret ni changement autonome des droits.',)),
    ('tableau_de_bord', 'reglages', 'Expliquer un indicateur du tableau de bord.', ('cockpit530.review',), ('expliquer',), (), ()),
    ('a_propos', 'reglages', 'Expliquer la version et la licence.', ('about560.ATTRIBUTION',), ('expliquer',), (), ()),
)
FICHES = {}


def _construire():
    for fid, famille, action_utile, services, actions, outils, limites in LIGNES:
        groupe = registre.INDEX[fid][1]
        f = FAMILLES[famille]
        ids = tuple(actions if actions is not None else f['actions'])
        FICHES[fid] = {'id': fid, 'groupe': groupe, 'famille': famille, 'famille_libelle': f['libelle'], 'action_utile': action_utile, 'contexte_requis': f['contexte'], 'droits': f['droits'], 'preuve': f['preuve'],
                       'limites': tuple(f['limites']) + tuple(limites), 'categorie': f['categorie'], 'services': tuple(f['services']) + tuple(services), 'actions': ids, 'outils_integres': tuple(outils),
                       'disponibilite': 'prepare_sans_connecteur' if fid == 'talk' else 'existe', 'manuel': ' '.join(OUTILS_INTEGRES[o]['manuel'] for o in outils),
                       'capacites_proposees': ('help.read',) if fid in AIDE_SEULE else PROPOSEES[groupe], 'proposition_annexe': 'proposition'}


_construire()


def fiche(fid):
    f = FICHES.get(str(fid or ''))
    if f is None:
        raise Stop('fonction_inconnue')
    return f


def _droits_effectifs(desk, owner, role):
    from .membres5650 import effective_rights
    if role == 'administrateur':
        return None
    try:
        return effective_rights(desk.c['state_dir'], owner, role)
    except Stop:
        return {}                     # registre illisible : aucun droit fin, pas d'ouverture


def _bloquee(desk, categorie):
    from .vueia5688 import blocked
    return bool(categorie) and blocked(desk, categorie)


def actions_pour(desk, fid, owner, role, ctx=None):
    """Actions du panneau pour cette page, cet objet et cette personne. Une action indisponible reste visible avec son motif, sauf si la personne n'y a pas droit.

    Serveur seul : un navigateur qui présente une action de cette liste ne l'exécute qu'après ``verifier_action``. Les propositions de l'annexe n'y figurent jamais."""
    f = fiche(fid)
    ctx = ctx or {}
    droits = _droits_effectifs(desk, owner, role)
    config = registre.capabilities(desk.c)
    dossier = bool((ctx.get('dossier') or {}).get('id'))
    objets = bool(ctx.get('objets'))
    out = []
    for ident in f['actions']:
        a = ACTIONS[ident]
        if droits is not None and a['droit'] and not droits.get(a['droit']):
            continue                  # une action hors de ses droits n'est pas proposée
        if a['circuit'] == 'pilote' and a['attendu'] == 'mail_draft' and role == 'assistant':
            continue                  # même règle que ``web567.api`` : le dépôt de brouillon sur un courriel n'est pas ouvert au rôle assistant
        motif = ''
        if _bloquee(desk, a['categorie'] or f['categorie'] if a['niveau'] != 'observer' or a['categorie'] else ''):
            motif = 'Fermé par le réglage « Ce que l’IA peut voir » ou par vos droits.'
        elif 'dossier' in a['requiert'] and not dossier:
            motif = 'Choisissez d’abord un dossier.'
        elif 'objet' in a['requiert'] and not objets:
            motif = 'Sélectionnez d’abord un élément sur la page.'
        elif 'sujet' in a['requiert'] and not (dossier or objets):
            motif = 'Choisissez un dossier ou sélectionnez un élément.'
        elif [b for b in a['besoins'] if b not in config]:
            motif = 'Service non configuré : cette préparation n’est pas disponible.'
        out.append({'id': ident, 'libelle': a['libelle'], 'instruction': a['instruction'], 'attendu': a['attendu'], 'niveau': a['niveau'], 'niveau_libelle': NIVEAUX[a['niveau']], 'effet': a['effet'], 'effet_libelle': EFFETS[a['effet']],
                    'circuit': a['circuit'], 'statut': a['statut'], 'statut_libelle': STATUTS[a['statut']], 'garde': a['garde'], 'garde_libelle': GARDES.get(a['garde'], ''), 'disponible': not motif, 'motif': motif})
    return out


def verifier_action(desk, fid, ident, owner, role, ctx):
    """Refait le contrôle à l'exécution : le navigateur a pu présenter n'importe quel identifiant, n'importe quelle page."""
    action = next((a for a in actions_pour(desk, fid, owner, role, ctx) if a['id'] == ident), None)
    if action is None:
        raise Stop('action_non_disponible')
    if not action['disponible']:
        raise Stop('action_non_disponible')
    return {**action, **{k: ACTIONS[ident][k] for k in ('taches', 'categorie')}}


def etat_de(desk, fid, owner, role, cache=None):
    """État réel de la fonction, relié au tableau du lot de qualification : c'est lui qui dit si un service est là, jamais la fiche."""
    from .fonctions_etat5689 import etat_fonction
    return etat_fonction(desk, fid, owner, role, cache)


def vue(desk, fid, owner='cabinet', role='administrateur', cache=None):
    """Fiche publique, avec son état et ses actions. Une proposition y reste nommée proposition."""
    f = fiche(fid)
    etat = etat_de(desk, fid, owner, role, cache)
    return {'id': fid, 'libelle': _libelle(fid), 'groupe': f['groupe'], 'famille': f['famille_libelle'], 'action_utile': f['action_utile'], 'contexte_requis': list(f['contexte_requis']), 'droits': f['droits'], 'preuve': f['preuve'], 'limites': list(f['limites']),
            'disponibilite': f['disponibilite'], 'disponibilite_libelle': DISPONIBILITES[f['disponibilite']], 'manuel': f['manuel'], 'services': list(f['services']), 'actions': actions_pour(desk, fid, owner, role),
            'proposition': {'statut': 'proposition', 'statut_libelle': STATUTS['proposition'], 'capacites': list(f['capacites_proposees'])}, 'outils_integres': [dict(OUTILS_INTEGRES[o], id=o) for o in f['outils_integres']],
            'etat': {k: etat[k] for k in ('etat', 'etat_libelle', 'motif', 'motif_libelle', 'externe', 'perimetre_autorise', 'preuve', 'limites')}}


def _libelle(fid):
    from .i18n5631 import t
    return t('fn.%s.label' % fid)


def tableau(desk, owner='cabinet', role='administrateur'):
    """Les fiches que cette personne peut voir : même règle que le menu. Rien n'est figé : l'état se lit à chaque appel."""
    cache = {}
    lignes = [vue(desk, fid, owner, role, cache) for fid in FICHES if registre.visible(role, registre.INDEX[fid][2])]
    return {'fiches': lignes, 'total': len(lignes), 'statuts': STATUTS, 'avertissement': 'Une capacité de l’annexe reste une proposition tant qu’aucun service ne la porte.'}


def manuel_html(fid):
    f = fiche(fid)
    return '<p class="ia89-manuel">%s</p>' % e(f['manuel']) if f['manuel'] else ''
