# Conformité 5.6.39 — bibliothèque de modèles et audit 5.6.37

Version de référence : 5.6.38. Sources : demande de l’avocat du 12 octobre 2026 et audit « Audit-AxiorHub-Pilote-5.6.37 » (8 fiches,
cas P01 à P11). Les contre-tests sont dans `tests/test_v5639.py`.

## Audit 5.6.37

| Fiche | Cas | État 5.6.39 |
|---|---|---|
| F01 (P0) chiffre justifié par un autre poste, une autre devise ou un autre exercice | P01, P02, P03, C01 | **Réalisé.** `agent/comptes5639.py` : fait financier structuré (poste, valeur, signe, devise et unité du même segment) ; exercice et unité établis par les en-têtes du document (tableaux N/N-1 compris) ; rattachement ambigu laissé « à vérifier » ; preuve de chaque dimension restituée ; méthode 3 (rapports antérieurs signalés pour recontrôle). Tests `AuditFigures`. |
| F02 mise en file non transactionnelle | P04 | **Réalisé.** `Desk.enqueue(..., transactional=True)` n’exécute ni validation, ni annulation, ni notification ; `operation_submit` et `relaunch` valident projet, travail et rattachement ensemble ; `Desk.notify_queued` notifie après validation et journalise une panne sans remonter d’erreur ; `repair` rattache par `args.id`. Tests : incident avant validation (rien d’écrit) et panne de notification après validation (projet et travail liés, vus d’une autre connexion). |
| F03 réalisation d’une opération en attente ou en échec | P05, P06 | **Réalisé.** Contrat unique écran/service (`realization_state`) : parcours courant réservé aux actes prêts et produits ; parcours distinct « Décision déjà prise avec des actes établis hors d’AxiorHub » avec confirmation ; état relu dans la transaction, transition conditionnelle ; motif affiché à la place du bouton. |
| F04 date de décision ignorée | P07 | **Réalisé.** La date confirmée est la date de la décision inscrite au registre ; une date différente de la date d’AG prévue est refusée avant toute écriture, sauf confirmation explicite (date prévue conservée). |
| F05 rédactions enfants d’une initiative écartée | P08 | **Réalisé.** `cancel_children` annule les rédactions en file et demande l’arrêt des rédactions en cours ; chaque rédaction est liée à l’initiative et à sa version de validation (`initiative_children5639`) et vérifie cette autorisation au démarrage et avant son dépôt ; une revalidation ne ranime pas l’ancienne version. |
| F06 relance après paiement partiel | P09 | **Réalisé.** Corrections (destinataire, objet, corps) enregistrées avant tout refus ; état « à relire » lié au nouveau solde ; texte généré reconstruit, texte corrigé conservé ; dépôt subordonné à « J’ai relu la relance avec ce solde ». |
| F07 abandon pendant la préparation | P10 | **Réalisé.** État vérifié avant chaque dépôt ; fichiers déposés tracés au fur et à mesure ; transition finale conditionnelle ; le gestionnaire d’erreur ne remplace jamais un état clos. |
| F08 réponse Nextcloud obsolète | P11 | **Réalisé.** Application Nextcloud 0.4.1 : numéro de demande de session, demande précédente interrompue, réponse tardive ignorée ; rubrique choisie surlignée dès le clic. |

## Bibliothèque de modèles

| Demande | État 5.6.39 |
|---|---|
| Statuts EURL, SARL, SELARL, SELAS, SAS, SASU, SCI, SCM, SCP | **Réalisé.** Clauses propres aux SAS : agrément (L. 227-14), préemption, sortie conjointe, sortie forcée, inaliénabilité (L. 227-13), exclusion (L. 227-16), changement de contrôle (L. 227-17), actions de préférence (L. 228-11), directeurs généraux, comité, limitation des pouvoirs ; SARL : agrément L. 223-14, majorités L. 223-29 et L. 223-30 ; SEL : article 46 de l’ordonnance n° 2023-77 ; SCP : articles 5 et 19 de l’ordonnance ; SCM : article 38 ; SCI : articles 1857, 1861 et 1870 du Code civil. |
| Pactes d’associés et d’actionnaires, LOI, protocole et compromis sous conditions suspensives, cessions de titres et de fonds, contrats | **Réalisé** (articles 1103, 1112, 1124, 1304 et suivants du Code civil ; L. 141-12, L. 141-14, L. 141-23 du Code de commerce). |
| Modèles allemands, espagnols, anglais adaptés au droit local et contrats dans la langue de l’utilisateur | **Réalisé** : 9 modèles allemands, 9 espagnols, 8 anglais ; filtre par défaut sur le droit de la langue de l’utilisateur ; mention « à faire valider par un conseil local ». |
| Modifier les modèles avec ses variables, importer ses modèles, faciliter le travail de l’IA | **Réalisé** : personnalisation du texte, modèle Word du cabinet (paragraphes [[si …]] retirés, « ## » numérotés, mise en forme conservée), modèles ajoutés ; trame transmise à la rédaction IA. |

## Pages d’erreur (demande du 13 octobre 2026)

| Demande | État 5.6.39 |
|---|---|
| Pages 503, 404, 500, « Action non effectuée » plus graphiques, avec bouton retour et bouton recharger | **Réalisé.** `agent/erreurs_pages5639.py` et `agent/static/erreurs5639.css` (servie sans connexion ; aucun style ni script en ligne, conforme à la politique de sécurité du contenu) : 400, 401, 404, 405, 500 et 503 avec « Recharger la page », « Revenir en arrière », « Accueil ». Le retour ne vise qu’une page du même site. Le code « 402 » n’est produit par aucune route ; les 502/503/504 d’Apache sont couverts ci-dessous. |
| Diminuer le risque d’apparition | **Réalisé.** Adresse inconnue : 404 avec accueil (au lieu d’un refus 400) ; erreur inattendue sur une page : un nouvel essai automatique ; base occupée : nouvel essai automatique ; nom du site comparé sans la casse ni le port par défaut ; sur le poste, « localhost » ↔ « 127.0.0.1 » : redirection vers l’adresse configurée pour les pages ; tout autre nom reste refusé (protection contre le DNS rebinding), de même que les envois ; Apache (`ErrorDocument 502/503/504`, appliqué par `install-interface.py`, avec sauvegarde, test de configuration et retour arrière) : page « AxiorHub redémarre » qui se recharge seule pendant un redémarrage. Tests `ErrorPages`. |

## Limites

- Les modèles sont des trames de travail : l’avocat vérifie les textes en vigueur à la date de l’acte et adapte les clauses ; les mentions
  « [À VÉRIFIER] » signalent les points sensibles (droits d’enregistrement, juridiction, prix de transfert…).
- Les modèles étrangers ne constituent pas un avis sur le droit étranger ; les formalités locales (forme notariée, registre) restent à
  accomplir par un professionnel du pays.
- Un document téléchargé reste disponible 24 heures, pour le seul utilisateur qui l’a préparé.
