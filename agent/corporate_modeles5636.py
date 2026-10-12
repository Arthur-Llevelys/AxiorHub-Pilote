"""5.6.36 : modèles génériques des actes du secrétariat juridique (Corporate).

Chaque modèle est une suite de paragraphes ; « # » en tête de ligne = titre en gras ; {{balise}} = donnée de la société ou de
l'opération. Ces modèles sont des trames de travail : le cabinet peut les remplacer, document par document, par ses propres modèles
Word (mêmes balises) dans Corporate › Modèles. Toute mention [À COMPLÉTER] signale une information que le logiciel ne peut pas
connaître ; aucun acte n'est signé ni déposé par AxiorHub.
"""

HEADER = ['# {{denomination}}', '{{forme_long}} au capital de {{capital_fr}}', 'Siège social : {{siege}}', '{{rcs_mention}}', '']

POUVOIR = HEADER + [
    '# POUVOIR POUR L’ACCOMPLISSEMENT DES FORMALITÉS', '',
    'Je soussigné(e), {{dirigeant}}, agissant en qualité de {{fonction_dirigeant}} de la société {{denomination}},',
    'donne pouvoir à {{mandataire}}, à l’effet d’accomplir pour le compte de la société, sur le guichet unique des formalités '
    'des entreprises (INPI), toutes les formalités consécutives à {{objet_formalite}}, et notamment : signer et déposer tous documents, '
    'effectuer toute déclaration, régler tous frais, et plus généralement faire le nécessaire.', '',
    'Fait à {{ville}}, le {{date_acte_fr}}', '', 'Signature précédée de la mention « Bon pour pouvoir »', '{{dirigeant}}']

DOCS = {
    # ------------------------------------------------------------------ approbation des comptes
    'convocation': ('Convocation à l’assemblée générale', HEADER + [
        '# CONVOCATION À L’ASSEMBLÉE GÉNÉRALE ORDINAIRE ANNUELLE', '',
        'Madame, Monsieur,',
        'Nous avons l’honneur de vous convoquer à l’assemblée générale ordinaire annuelle qui se tiendra le {{date_ag_fr}} à {{heure_ag}}, '
        'à {{lieu_ag}}, à l’effet de délibérer sur l’ordre du jour suivant :',
        '— rapport de gestion du {{fonction_dirigeant}} sur l’exercice clos le {{exercice_clos_fr}} ;',
        '{{ligne_cac}}',
        '— rapport spécial sur les conventions visées à l’article {{article_conventions}} du Code de commerce ;',
        '— approbation des comptes de l’exercice clos le {{exercice_clos_fr}} et quitus au {{fonction_dirigeant}} ;',
        '— affectation du résultat de l’exercice ;', '— approbation des conventions réglementées ;', '— pouvoirs pour l’accomplissement des formalités.', '',
        'Les documents prévus par la loi et les statuts sont tenus à votre disposition au siège social.',
        'Si vous ne pouvez assister personnellement à cette assemblée, vous pouvez vous y faire représenter au moyen du pouvoir ci-joint.', '',
        'Fait à {{ville}}, le {{date_acte_fr}}', 'Le {{fonction_dirigeant}}', '{{dirigeant}}']),
    'procuration': ('Procuration (pouvoir de représentation)', HEADER + [
        '# POUVOIR', '',
        'Je soussigné(e) [À COMPLÉTER : nom de l’associé], titulaire de [À COMPLÉTER] titres de la société {{denomination}},',
        'donne pouvoir à [À COMPLÉTER : mandataire] de me représenter à l’assemblée générale ordinaire annuelle du {{date_ag_fr}}, '
        'de prendre part aux délibérations, de voter sur les résolutions inscrites à l’ordre du jour et de signer la feuille de présence '
        'et le procès-verbal.', '', 'Fait à [À COMPLÉTER], le [À COMPLÉTER]', '', 'Signature précédée de la mention « Bon pour pouvoir »']),
    'feuille_presence': ('Feuille de présence', HEADER + [
        '# FEUILLE DE PRÉSENCE', 'Assemblée générale ordinaire annuelle du {{date_ag_fr}}', '',
        '{{tableau_associes}}', '',
        'Total des titres présents ou représentés : [À COMPLÉTER] sur {{total_titres}} titres.', '',
        'Certifiée sincère et véritable par le président de séance :', '{{dirigeant}}']),
    'rapport_gestion': ('Rapport de gestion', HEADER + [
        '# RAPPORT DE GESTION DU {{fonction_dirigeant_maj}} SUR L’EXERCICE CLOS LE {{exercice_clos_fr}}', '',
        '{{intro_rapport}}', '',
        '# 1. Situation et activité de la société au cours de l’exercice', '{{activite}}', '',
        '# 2. Résultats de l’activité', '{{chiffres_cles}}', '',
        '# 3. Événements importants survenus depuis la clôture', '{{evenements}}', '',
        '# 4. Évolution prévisible et perspectives', '{{perspectives}}', '',
        '# 5. Activité en matière de recherche et de développement', '[À COMPLÉTER ou « Néant »]', '',
        '# 6. Proposition d’affectation du résultat', '{{texte_affectation}}', '',
        '# 7. Dividendes distribués au titre des trois derniers exercices (article 243 bis du CGI)', '{{dividendes_anterieurs}}', '',
        '# 8. Dépenses non déductibles fiscalement (article 39-4 du CGI)', '[À COMPLÉTER ou « Néant »]', '',
        '# 9. Conventions réglementées', '{{texte_conventions}}', '',
        'Nous vous invitons à approuver les comptes et les résolutions qui vous sont soumises.', '',
        'Fait à {{ville}}, le {{date_acte_fr}}', 'Le {{fonction_dirigeant}}', '{{dirigeant}}', '',
        '{{avertissement_ia}}']),
    'annexe_rapport': ('Annexe au rapport de gestion', HEADER + [
        '# ANNEXE AU RAPPORT DE GESTION — EXERCICE CLOS LE {{exercice_clos_fr}}', '',
        '# Informations sur les délais de paiement (articles L. 441-14 et D. 441-6 du Code de commerce)',
        'Factures reçues et émises non réglées à la date de clôture dont le terme est échu : [À COMPLÉTER : tableau établi par l’expert-comptable].', '',
        '# Résultats de la société au cours des cinq derniers exercices', '[À COMPLÉTER : tableau des résultats, le cas échéant]', '',
        '# Chiffres clés relevés dans les comptes annuels', '{{chiffres_cles}}']),
    'rapport_special': ('Rapport spécial sur les conventions réglementées', HEADER + [
        '# RAPPORT SPÉCIAL SUR LES CONVENTIONS VISÉES À L’ARTICLE {{article_conventions}} DU CODE DE COMMERCE', '',
        'Exercice clos le {{exercice_clos_fr}}', '',
        '{{texte_conventions}}', '',
        'Fait à {{ville}}, le {{date_acte_fr}}', 'Le {{fonction_dirigeant}}', '{{dirigeant}}']),
    'projet_resolutions': ('Projet de résolutions', HEADER + [
        '# TEXTE DES RÉSOLUTIONS PROPOSÉES — {{organe_titre_court}} DU {{date_ag_fr}}', '',
        '# PREMIÈRE RÉSOLUTION — Approbation des comptes',
        '{{decideur}}, après avoir pris connaissance du rapport de gestion{{mention_cac}}, approuve les comptes annuels de l’exercice clos le '
        '{{exercice_clos_fr}}, tels qu’ils lui ont été présentés, faisant apparaître {{libelle_resultat}} de {{resultat_fr}}, ainsi que les '
        'opérations traduites dans ces comptes ou résumées dans ces rapports. En conséquence, {{decideur_min}} donne quitus au {{fonction_dirigeant}} '
        'de l’exécution de son mandat pour ledit exercice.', '',
        '# DEUXIÈME RÉSOLUTION — Affectation du résultat', '{{decideur}} {{texte_affectation}}', '{{dividendes_anterieurs}}', '',
        '# TROISIÈME RÉSOLUTION — Conventions réglementées', '{{decideur}}, {{texte_conventions_resolution}}', '',
        '# QUATRIÈME RÉSOLUTION — Pouvoirs',
        '{{decideur}} confère tous pouvoirs au porteur d’un original, d’une copie ou d’un extrait du présent procès-verbal pour accomplir '
        'toutes formalités de dépôt et de publicité.']),
    'pv_agoa': ('Procès-verbal d’approbation des comptes', HEADER + [
        '# {{organe_titre}}', '# DU {{date_ag_fr}}', '',
        '{{preambule_pv}}', '',
        'Le président de séance rappelle que les documents prévus par la loi ont été tenus à la disposition des associés dans les délais légaux, '
        'puis présente l’ordre du jour : approbation des comptes de l’exercice clos le {{exercice_clos_fr}} et quitus ; affectation du résultat ; '
        'conventions réglementées ; pouvoirs.', '',
        '# PREMIÈRE RÉSOLUTION — Approbation des comptes',
        '{{decideur}}, après avoir pris connaissance du rapport de gestion{{mention_cac}}, approuve les comptes annuels de l’exercice clos le '
        '{{exercice_clos_fr}} faisant apparaître {{libelle_resultat}} de {{resultat_fr}}, et donne quitus au {{fonction_dirigeant}}.',
        '{{vote}}', '',
        '# DEUXIÈME RÉSOLUTION — Affectation du résultat', '{{decideur}} {{texte_affectation}}', '{{dividendes_anterieurs}}', '{{vote}}', '',
        '# TROISIÈME RÉSOLUTION — Conventions réglementées', '{{decideur}}, {{texte_conventions_resolution}}', '{{vote}}', '',
        '# QUATRIÈME RÉSOLUTION — Pouvoirs',
        '{{decideur}} confère tous pouvoirs au porteur d’un original, d’une copie ou d’un extrait du présent procès-verbal pour accomplir toutes '
        'formalités.', '{{vote}}', '',
        'De tout ce que dessus, il a été dressé le présent procès-verbal, signé {{signataires_pv}}.', '', '{{dirigeant}}']),
    'extrait_pv': ('Extrait du procès-verbal (affectation du résultat)', HEADER + [
        '# EXTRAIT DU {{organe_titre}} DU {{date_ag_fr}}', '',
        '# DEUXIÈME RÉSOLUTION — Affectation du résultat', '{{decideur}} {{texte_affectation}}', '{{vote}}', '',
        'Pour extrait certifié conforme,', 'Le {{fonction_dirigeant}}', '{{dirigeant}}']),
    'pouvoir_inpi': ('Pouvoir pour les formalités (INPI)', POUVOIR),
    # ------------------------------------------------------------------ transfert de siège
    'decision_siege': ('Décision de transfert du siège social', HEADER + [
        '# {{organe_titre_modif}}', '# DU {{date_effet_fr}}', '',
        '{{decideur_modif}} décide de transférer le siège social de la société, à compter du {{date_effet_fr}}, du {{ancien_siege}} au {{nouveau_siege}}, '
        'et de modifier corrélativement l’article [À COMPLÉTER] des statuts, désormais rédigé comme suit :',
        '« Le siège social est fixé : {{nouveau_siege}}. »', '',
        '{{decideur_modif}} confère tous pouvoirs au porteur d’un original ou d’une copie des présentes pour accomplir les formalités.', '',
        '{{dirigeant}}']),
    'annonce_siege': ('Annonce légale — transfert de siège', [
        '# {{denomination}}', '{{forme_long}} au capital de {{capital_fr}}', 'Siège : {{ancien_siege}}', '{{rcs_mention}}', '',
        'Aux termes {{source_decision}} du {{date_effet_fr}}, il a été décidé de transférer le siège social au {{nouveau_siege}} à compter du '
        '{{date_effet_fr}}. L’article [À COMPLÉTER] des statuts a été modifié en conséquence. {{mention_ressort}}', 'Pour avis.']),
    # ------------------------------------------------------------------ dirigeants
    'decision_dirigeant': ('Décision relative au dirigeant', HEADER + [
        '# {{organe_titre_modif}}', '# DU {{date_effet_fr}}', '',
        '{{texte_sortant}}', '',
        '{{decideur_modif}} nomme en qualité de {{fonction}}, à compter du {{date_effet_fr}} et pour {{duree_mandat}} : {{entrant_identite}}.', '',
        '{{entrant}} a fait savoir par avance qu’il/elle acceptait ces fonctions et qu’aucune incompatibilité ni interdiction ne s’opposait à sa '
        'nomination.', '',
        '{{decideur_modif}} confère tous pouvoirs au porteur d’un original ou d’une copie des présentes pour accomplir les formalités.', '',
        '{{dirigeant}}']),
    'demission': ('Lettre de démission', [
        '{{sortant}}', '', 'À l’attention de la société {{denomination}}', '{{siege}}', '', 'Fait à [À COMPLÉTER], le {{date_acte_fr}}', '',
        'Objet : démission de mes fonctions de {{fonction}}', '',
        'Madame, Monsieur,', 'Par la présente, je vous informe de ma décision de démissionner de mes fonctions de {{fonction}} de la société '
        '{{denomination}}, avec effet au {{date_effet_fr}}.', '', 'Je vous prie d’agréer l’expression de mes salutations distinguées.', '', '{{sortant}}']),
    'acceptation_fonctions': ('Acceptation des fonctions', HEADER + [
        '# ACCEPTATION DES FONCTIONS', '',
        'Je soussigné(e) {{entrant_identite}},', 'déclare accepter les fonctions de {{fonction}} de la société {{denomination}} auxquelles j’ai été '
        'nommé(e) à compter du {{date_effet_fr}}, et déclare n’être frappé(e) d’aucune incompatibilité ni interdiction susceptible de m’empêcher '
        'd’exercer ces fonctions.', '', 'Fait à [À COMPLÉTER], le {{date_acte_fr}}', '', '{{entrant}}']),
    'non_condamnation': ('Déclaration de non-condamnation et de filiation', [
        '# DÉCLARATION SUR L’HONNEUR DE NON-CONDAMNATION ET DE FILIATION', '',
        'Je soussigné(e) {{entrant_identite}},', 'Filiation : père [À COMPLÉTER], mère [À COMPLÉTER],', '',
        'déclare sur l’honneur, conformément à l’article A. 123-51 du Code de commerce, n’avoir fait l’objet d’aucune condamnation pénale ni de '
        'sanction civile ou administrative de nature à m’interdire de gérer, administrer ou diriger une personne morale.', '',
        'Fait à [À COMPLÉTER], le {{date_acte_fr}}, pour servir et valoir ce que de droit.', '', '{{entrant}}']),
    'annonce_dirigeant': ('Annonce légale — changement de dirigeant', [
        '# {{denomination}}', '{{forme_long}} au capital de {{capital_fr}}', 'Siège : {{siege}}', '{{rcs_mention}}', '',
        'Aux termes {{source_decision}} du {{date_effet_fr}}, {{entrant}} a été nommé(e) en qualité de {{fonction}}{{mention_remplacement}}.',
        'Pour avis.']),
    # ------------------------------------------------------------------ autres modifications
    'decision_modification': ('Décision de modification', HEADER + [
        '# {{organe_titre_modif}}', '# DU {{date_effet_fr}}', '',
        '{{decideur_modif}} décide, à compter du {{date_effet_fr}}, la modification suivante : {{nature}}.', '',
        'En conséquence, l’article {{article}} des statuts est désormais rédigé comme suit :', '« {{nouvelle_redaction}} »', '',
        'Ancienne rédaction : « {{ancienne_redaction}} »', '',
        '{{decideur_modif}} confère tous pouvoirs au porteur d’un original ou d’une copie des présentes pour accomplir les formalités.', '',
        '{{dirigeant}}']),
    'annonce_modification': ('Annonce légale — modification', [
        '# {{denomination}}', '{{forme_long}} au capital de {{capital_fr}}', 'Siège : {{siege}}', '{{rcs_mention}}', '',
        'Aux termes {{source_decision}} du {{date_effet_fr}}, il a été décidé : {{nature}}. Nouvelle mention : {{nouvelle_redaction}}. '
        'Ancienne mention : {{ancienne_redaction}}.', 'Pour avis.']),
    # ------------------------------------------------------------------ cession de titres
    'acte_cession': ('Acte de cession de titres', [
        '# ACTE DE CESSION DE {{nature_titres_maj}}', '', '# ENTRE LES SOUSSIGNÉS :', '',
        '{{cedant}}, ci-après « le Cédant »,', 'd’une part,', '', 'ET', '', '{{cessionnaire}}, ci-après « le Cessionnaire »,', 'd’autre part,', '',
        '# IL A ÉTÉ CONVENU CE QUI SUIT :', '',
        '# Article 1 — Cession',
        'Le Cédant cède au Cessionnaire, qui accepte, {{nombre}} {{nature_titres}} ({{categorie}}) de la société {{denomination}}, {{forme_long}} '
        'au capital de {{capital_fr}}, dont le siège est {{siege}}, {{rcs_mention}}.', '',
        '# Article 2 — Prix', 'La cession est consentie moyennant le prix de {{prix_unitaire_fr}} par titre, soit un prix total de {{prix_total_fr}}, '
        'payé ce jour [À COMPLÉTER : modalités de paiement].', '',
        '# Article 3 — Propriété et jouissance', 'Le Cessionnaire sera propriétaire des titres cédés à compter de ce jour et aura droit aux '
        'dividendes mis en distribution postérieurement. {{mention_agrement}}', '',
        '# Article 4 — Déclarations', 'Le Cédant déclare que les titres cédés sont libres de tout nantissement, gage ou droit quelconque. '
        '[À COMPLÉTER : garanties, le cas échéant]', '',
        '# Article 5 — Formalités et frais', 'Le transfert sera inscrit au registre des mouvements de titres sur présentation de l’ordre de mouvement. '
        'Les droits d’enregistrement sont à la charge du Cessionnaire.', '',
        'Fait à [À COMPLÉTER], le {{date_cession_fr}}, en [À COMPLÉTER] exemplaires.', '', 'Le Cédant                                   Le Cessionnaire']),
    'ordre_mouvement': ('Ordre de mouvement de titres', HEADER + [
        '# ORDRE DE MOUVEMENT DE TITRES', '',
        'Le soussigné {{cedant}} donne l’ordre à la société {{denomination}} de virer au compte de {{cessionnaire}} '
        '{{nombre}} {{nature_titres}} ({{categorie}}) inscrites à son compte.', '',
        'Date de l’ordre : {{date_cession_fr}}', '', 'Signature du titulaire', '{{cedant}}']),
    'agrement': ('Décision d’agrément du cessionnaire', HEADER + [
        '# {{organe_titre_modif}}', '# DU {{date_acte_fr}}', '',
        '{{decideur_modif}}, informé(e) du projet de cession de {{nombre}} {{nature_titres}} par {{cedant}} au profit de {{cessionnaire}}, '
        'au prix de {{prix_unitaire_fr}} par titre, décide d’agréer {{cessionnaire}} en qualité de nouvel associé, conformément à l’article '
        '[À COMPLÉTER] des statuts.', '', '{{dirigeant}}']),
    'attestation_inscription': ('Attestation d’inscription en compte', HEADER + [
        '# ATTESTATION D’INSCRIPTION EN COMPTE', '',
        'Je soussigné(e) {{dirigeant}}, {{fonction_dirigeant}} de la société {{denomination}}, atteste que {{nombre}} {{nature_titres}} ({{categorie}}) '
        'ont été inscrites le {{date_cession_fr}} au compte de {{cessionnaire}}, sous le numéro de mouvement {{numero_mouvement}} du registre des '
        'mouvements de titres.', '', 'Fait à {{ville}}, le {{date_acte_fr}}', '', '{{dirigeant}}']),
    # ------------------------------------------------------------------ création
    'statuts': ('Statuts', [
        '# {{denomination}}', '{{forme_long}} au capital de {{capital_fr}}', 'Siège social : {{siege}}', '', '# STATUTS', '',
        '{{preambule_statuts}}', '',
        '# Article 1 — Forme', 'Il est formé entre les propriétaires des {{nature_titres}} ci-après créées et de celles qui pourraient l’être '
        'ultérieurement une {{forme_long}} régie par les dispositions légales applicables et par les présents statuts.', '',
        '# Article 2 — Objet', 'La société a pour objet, en France et à l’étranger : {{objet}}.', '',
        '# Article 3 — Dénomination', 'La dénomination de la société est : {{denomination}}.', '',
        '# Article 4 — Siège social', 'Le siège social est fixé : {{siege}}.', '',
        '# Article 5 — Durée', 'La durée de la société est fixée à {{duree}} années à compter de son immatriculation au registre du commerce et '
        'des sociétés.', '',
        '# Article 6 — Apports', '{{texte_apports}}', '',
        '# Article 7 — Capital social', 'Le capital social est fixé à {{capital_fr}}, divisé en {{total_titres}} {{nature_titres}} de {{nominal_fr}} '
        'chacune, entièrement souscrites et {{liberation}}.', '',
        '# Article 8 — Direction', '{{texte_direction}}', '',
        '# Article 9 — Décisions collectives', '{{texte_decisions}}', '',
        '# Article 10 — Transmission des {{nature_titres}}', '{{texte_transmission}}', '',
        '# Article 11 — Exercice social', 'L’exercice social commence le {{debut_exercice}} et se termine le {{date_cloture_fr}}. Par exception, '
        'le premier exercice sera clos le [À COMPLÉTER].', '',
        '# Article 12 — Affectation des résultats', 'Sur le bénéfice de l’exercice diminué, le cas échéant, des pertes antérieures, il est '
        'prélevé 5 % pour constituer la réserve légale, ce prélèvement cessant d’être obligatoire lorsque la réserve atteint le dixième du '
        'capital. Le solde est affecté sur décision {{organe_affectation}}.', '',
        '# Article 13 — Nomination du premier dirigeant', '{{texte_premier_dirigeant}}', '',
        '[À COMPLÉTER : clauses particulières propres au projet (agrément, préemption, inaliénabilité, exclusion, commissaire aux comptes…)]', '',
        'Fait à [À COMPLÉTER], le {{date_acte_fr}}, en [À COMPLÉTER] originaux.', '', '{{signatures_associes}}']),
    'liste_souscripteurs': ('Liste des souscripteurs', [
        '# {{denomination}}', '{{forme_long}} en formation au capital de {{capital_fr}}', 'Siège social : {{siege}}', '',
        '# LISTE DES SOUSCRIPTEURS', '', '{{tableau_associes}}', '',
        'Total : {{total_titres}} {{nature_titres}} de {{nominal_fr}}, soit {{capital_fr}}.', '',
        'Certifiée exacte par {{dirigeant}}, {{fonction_dirigeant}}.']),
    'attestation_domiciliation': ('Attestation de domiciliation', [
        '# ATTESTATION DE DOMICILIATION', '',
        'Je soussigné(e) [À COMPLÉTER : propriétaire, bailleur ou dirigeant], atteste mettre à disposition de la société {{denomination}}, '
        '{{forme_long}} en cours de formation, les locaux situés {{siege}} pour y établir son siège social.', '',
        'Fait à [À COMPLÉTER], le {{date_acte_fr}}', '', '[Signature]']),
    'annonce_constitution': ('Annonce légale — constitution', [
        '# AVIS DE CONSTITUTION', '',
        'Aux termes d’un acte sous signature privée en date du {{date_acte_fr}}, il a été constitué une société présentant les caractéristiques '
        'suivantes : Forme : {{forme_long}}. Dénomination : {{denomination}}. Siège : {{siege}}. Objet : {{objet}}. Durée : {{duree}} ans à compter '
        'de l’immatriculation au RCS. Capital : {{capital_fr}}. {{mention_direction_annonce}} Immatriculation au RCS de [À COMPLÉTER].',
        'Pour avis.']),
    # ------------------------------------------------------------------ autre diligence en droit des sociétés
    'decision_diligence': ('Décision — autre diligence', HEADER + [
        '# {{organe_titre_modif}}', '# DU {{date_effet_fr}}', '',
        'Objet : {{intitule}}', '',
        '{{preambule_diligence}}', '',
        '{{texte_resolutions}}', '',
        '{{decideur_modif}} confère tous pouvoirs au porteur d’un original, d’une copie ou d’un extrait des présentes pour accomplir toutes '
        'formalités de dépôt et de publicité.', '',
        'De tout ce que dessus, il a été dressé le présent acte, signé {{signataires_pv}}.', '', '{{dirigeant}}']),
    'annonce_diligence': ('Annonce légale — autre diligence', [
        '# {{denomination}}', '{{forme_long}} au capital de {{capital_fr}}', 'Siège : {{siege}}', '{{rcs_mention}}', '',
        'Aux termes {{source_decision}} du {{date_effet_fr}}, il a été décidé : {{intitule}}. {{mention_annonce}}', 'Pour avis.']),
}

# Documents produits par opération : (document, coché par défaut, condition éventuelle sur les données)
OPERATIONS = {
    'agoa': ('Approbation des comptes', ['convocation', 'procuration', 'feuille_presence', 'rapport_gestion', 'annexe_rapport', 'rapport_special',
                                         'projet_resolutions', 'pv_agoa', 'extrait_pv', 'pouvoir_inpi']),
    'siege': ('Transfert du siège social', ['decision_siege', 'annonce_siege', 'pouvoir_inpi']),
    'dirigeant': ('Changement de dirigeant', ['decision_dirigeant', 'demission', 'acceptation_fonctions', 'non_condamnation', 'annonce_dirigeant',
                                              'pouvoir_inpi']),
    'modification': ('Autre modification statutaire', ['decision_modification', 'annonce_modification', 'pouvoir_inpi']),
    'cession': ('Cession de titres', ['acte_cession', 'ordre_mouvement', 'agrement', 'attestation_inscription']),
    'diligence': ('Autre diligence en droit des sociétés', ['decision_diligence', 'annonce_diligence', 'pouvoir_inpi']),
    'creation': ('Création de société', ['statuts', 'liste_souscripteurs', 'non_condamnation', 'attestation_domiciliation', 'annonce_constitution',
                                         'pouvoir_inpi']),
}
