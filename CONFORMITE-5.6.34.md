# Conformité 5.6.34 — cahier des charges « Audit et préparation de la nouvelle version » (11 octobre 2026)

Version de référence : 5.6.33. Le cahier des charges prévoit plusieurs lots (R0 à R6) ; cette version livre R0 à R4 dans le moteur, un
socle de R5 (application Nextcloud 0.1.0, non qualifiée) et la partie automatisable de R6. Elle intègre aussi deux demandes
complémentaires de l’avocat reçues pendant la réalisation (agenda local, état du système par mode d’installation).

Légende : **Réalisé** — livré et couvert par un test ; **Partiel** — livré avec la limite indiquée ; **Non réalisé** — hors de cette
livraison, avec la raison. Les tests cités sont dans `tests/test_v5634.py` sauf mention contraire.

## Registre des corrections (chapitre 3)

| Ticket | Objet | État 5.6.34 |
|---|---|---|
| B01 | Fiches de pièces contrôlées | Réalisé : validation JSON Schema côté serveur avant toute fusion ; reprise unique puis fragment « invalide » et pièce « incomplète » (jamais une fiche vide réputée réussie) ; extrait justificatif obligatoire pour chaque fait, demande, montant et point clé, retrouvé dans la version exacte du texte ; date et montant contrôlés dans le passage ; page déduite de la position de l’extrait, position (paragraphe) pour un document non paginé ; statut et erreurs par fragment (T01). Limite : la correspondance de l’extrait est textuelle (casse, espaces, guillemets normalisés) ; une paraphrase du modèle reste « à vérifier ». |
| B02 | Conserver avant de résumer | Réalisé : index complet sans plafond, déduplication par contenu et date (deux événements à des dates différentes restent distincts), positions de toutes les occurrences ; représentation courte avec « +N éléments non affichés » ; demandes des actes essentiels jamais tronquées ; fragment saturé redécoupé une fois, sinon marqué non exhaustif ; index complet consultable (`/api440/m5634/releve`) (T02). |
| B03 | Pièces en échec | Réalisé : état par pièce (pending, running, done, retry_wait, blocked), essais, prochaine reprise et cause ; ordre stable ; suite programmée même sans succès ; trois essais au plus ; panne générale du modèle → pause annoncée sans relance perpétuelle ; fragments terminés conservés (aucune relecture des fragments déjà contrôlés) ; travail interrompu repris (T03). |
| B04 | Corrections de l’avocat | Réalisé : couche de corrections indépendante des versions (auteur, date, version de départ, révision, statut) appliquée dans la fiche, la vue détaillée, les dossiers de travail et le contrat Nextcloud ; migration des anciennes corrections ; proposition de l’agent affichée à côté avec « Garder ma correction » / « Adopter la proposition » ; historique et restauration ; conflit explicite entre deux onglets (T04). |
| B05 | Couverture exacte | Réalisé : inventaire complet (illisibles, vides, protégées, non prises en charge) ; bilan recalculé après la dernière compression et toute relance ; libellé exact au lieu de « pièces lues » ; anciennes fiches affichées avec leurs chiffres d’origine (« pièce(s) transmise(s) ») (T05). |
| B06 | Registres juridiques | Réalisé : registre unique (`registre5634`) des sources du catalogue (JusticeLibre, OpenLegi, GoodLegal, LibreJustice, Legal Data Hunter) et des API REST historiques (OpenLegal, Pappers) ; plus de liste cachée ; les connecteurs récents ne sont plus écartés ; un fournisseur sans outil de lecture fournit des pistes, jamais une autorité vérifiée ; un même service pour la recherche générale et le dossier (T06). Limite : l’audience et les missions continuent d’appeler la recherche MCP existante, désormais alignée sur le même registre. |
| B07 | Écritures pendant les consultations | Réalisé : la liste des alertes ne fait plus d’UPDATE (rappels échus vus « ouverts », remis en circulation par le cycle de surveillance) ; la fiche d’audience lit l’index local daté, sans appel distant ni réglage écrit (T07). Limite : l’audit de toutes les fonctions `ensure_schema` des pages n’est pas terminé ; la mesure p95 < 2 s n’a pas été faite sur une installation réelle. |
| B08 | Suivi de préparation | Réalisé : statut HTTP et format contrôlés ; perte de session, erreur serveur et coupure réseau distinguées ; backoff borné ; interruption après 12 minutes expliquée avec « Voir le travail » et « Reprendre le suivi » ; aucun rechargement automatique (saisie en cours protégée) ; état explicite du travail (job_id, étape, échec qualifié) ; clé d’idempotence atomique par dossier (T08). Limite : la recette dans un navigateur réel des cas 401/503/coupure n’a été faite qu’en lecture de code et via l’API. |
| B09 | Personnes morales | Réalisé : la forme sociale fait partie de l’identité (SAS ALPHA ≠ SARL ALPHA), SIREN pris en compte, rapprochement proposé et confirmé par l’avocat ; parties d’un litige cité dans une décision écartées vers « À vérifier » (T09). |
| B10 | Dates et faits sans preuve | Réalisé : dates civiles validées (31 février refusé), dates partielles conservées, formulation d’origine gardée ; affirmation sans pièce valide et date égale à la date du fichier absente du texte → « À vérifier » ; correction humaine de provenance distincte (T10). |
| B11 | Cache versionné | Réalisé : clé = méthode (schéma, consignes, contrôle) + empreinte du texte extrait (+ version du fichier conservée) ; politique explicite de changement de modèle (« conserver » par défaut, « refaire » possible) ; erreur d’index qualifiée, connexion fermée (T11). |
| B12 | Validation de schéma | Réalisé : validateur complet du sous-ensemble utilisé (types stricts, bornes, tailles, énumérations, propriétés interdites, champs facultatifs, chemins d’erreur), refus de tout mot-clé non pris en charge ; une reprise unique dans `Model.ask` (T12). Choix documenté dans `agent/schema5634.py` : pas de dépendance tierce (installations hors ligne). |
| B13 | Migration 5.6.31 | Réalisé : fixture « 5.6.31 » corrigée, cas 5.6.32 et 5.6.33 ; réinstallation sans fausse promesse de retour arrière ; reçu contrôlé avant toute modification (« Aucun point de retour arrière disponible ») ; espace disque vérifié avant tout arrêt ; version de sortie centralisée ; contrôle des intitulés de fixtures ; tests root exécutés par la recette GitHub (`migrations-root`). Limite : la restauration des bases n’est pas automatique (sauvegardes conservées et listées). |
| B14 | Saisies techniques | Réalisé : sélecteurs de dossier et de document (aucun chemin ni identifiant exact) dans Audiences, Réviser et Recherche juridique ; recherche de dossier sans accents par nom, référence, client, partie, n° RG ; anciens liens redirigés ; bandeaux « VERSION 2.4.0 / 2.5.0 » retirés ; l’atelier PDF d’exemple n’est plus jamais ouvert (« non configuré » à la place). L’ancienne page « Audiences et Word » reste servie pour les liens de contrôle des préparations existantes (T14 couvert par U01/U02). |
| B15 | Diagnostic HTTP | Réalisé : erreurs structurées (code public inchangé, service, opération, étape, statut, possibilité de reprise, corrélation, ressource anonymisée), étapes encadrées dans la préparation de la fiche, fichier local absent distinct d’une adresse de serveur, `/api/show` traduit comme `/api/chat`, messages propres à chaque cause (T15). |
| B16 | Détail et Annuler | Réalisé : Détail ouvre la demande précise (`/decision?type=…&id=…`, lecture seule) ; Annuler est une opération métier durable pour les demandes de document et les missions ordinaires (en file, en cours → arrêt demandé, en échec, en attente de décision), idempotente, tracée, jamais remplacée par un échec ni relancée ; contrôle du statut au démarrage et avant le dépôt ; messages fidèles ; compteurs mis à jour (T16, U06). Limite : la confirmation native du navigateur n’a pas pu être cliquée dans l’aperçu intégré ; le chemin serveur a été vérifié dans le navigateur. |
| B17 | Guide Kokoro | Réalisé : étape « Synthèse vocale (Kokoro) » du guide d’installation, système en cours d’abord (Windows, poste Ubuntu, serveur), voix par langue en français, anglais, allemand et espagnol ; l’allemand, non proposé par Kokoro, passe par eSpeak NG ou Chatterbox. |

## Incidents signalés (chapitre 4)

| Observation | État |
|---|---|
| O01 — 404 sur la préparation de fiche | Outillé : chaque étape porte désormais service, étape et corrélation (B15) ; la cause de l’incident réel reste à établir avec le journal du travail concerné sur le serveur du cabinet (non consulté). |
| O02 — police FKGroteskNeue bloquée | Vérifié : aucune référence à cette police ni à `frontend-cdn.perplexity.ai` dans le logiciel ; la CSP reste restrictive ; un test interdit toute police externe dans les actifs. L’appel provient très probablement d’une extension ou d’un contenu injecté dans le navigateur. |

## Profil juridique (chapitre 5)

Réalisé : profils Juridique (0,3 ; 0,9 ; 40 ; 0 ; 1,0 ; 64 ; contexte automatique ≤ 32 768) et Extraction factuelle (0) ; résolveur unique ;
argument absent ≠ zéro explicite ; options transmises à Ollama, paramètres non pris en charge signalés pour les fournisseurs externes ;
valeurs effectives conservées par fonction ; migration conservant les réglages existants avec proposition d’adoption ; panneau Modèles
locaux (Tester sans pièce réelle, Restaurer, réglages avancés expliqués, virgule décimale) (L01–L06). Limite : les valeurs restent un
point de départ à évaluer sur le banc du cabinet.

## Parcours (chapitres 6 et 7)

| Recette | État |
|---|---|
| U01 Audiences | Réalisé : espace unique (Fiche sans appel IA / Préparer), plusieurs adversaires (une préparation par écriture adverse), pièces complémentaires, téléversement d’écritures absentes du dossier (dépôt sans remplacement, empreinte vérifiée, accepté seulement s’il est tracé pour ce dossier), redirections. Limite : le choix d’une audience provisoire et le rapprochement automatique avec un avis de renvoi restent dans les écrans existants. |
| U02 Réviser | Réalisé : Produire › Réviser, sélection du DOCX par identifiant et version, original jamais remplacé (moteur de révision existant). |
| U03 Recherche | Réalisé : question générale sans dossier, sources du registre, cartes de résultats, détail technique replié ; commandes de pièces déplacées dans le dossier. Limite : « Conserver dans le dossier » / « Ajouter à un argument » non ajoutés. |
| U04 Alertes du cabinet | Réalisé : renommage, libellés et explications, action utile mise en avant, incidents techniques séparés, Annuler la dernière action, Rouvrir, rappel ne dépassant pas la date utile, traçabilité, réouverture sur changement matériel. |
| U05 Fonctions | Réalisé : matrice des 64 fonctions (`agent/fonctions5634.py`) et contrôle de couverture ; libellés et groupes du catalogue mis à jour. Limite : la séance avec un avocat non technicien reste à organiser. |
| U06 Aujourd’hui | Réalisé (voir B16). |
| U07 Résumé | Réalisé : libellé « Résumé » (Summary, Zusammenfassung, Resumen), bloc sur toute la largeur intérieure mesuré dans un navigateur à 375, 768, 1 280 et 1 920 px (écart < 1 px, aucun débordement), contrôle décrit exactement. |

## Dossier et missions (chapitre 8)

Partiel : modèle de preuve (statuts, versions de sources, passages) mis en œuvre pour les relevés de pièces et la fiche ; l’objet
SourceVersion complet et le graphe de sous-tâches des missions restent à généraliser aux missions complexes (lot suivant).

## Application Nextcloud (chapitre 9)

Partiel : application 0.1.0 (PHP + Vue 3, actifs compilés inclus) et contrat moteur `/integration/v1` (contexte signé HMAC, nonce,
expiration, périmètre, idempotence, annulation, résultat versionné, approbation d’une version précise, erreurs normalisées), testé côté
moteur. **Non qualifiée** : aucune installation réelle sur Nextcloud 32 à 35 n’a été faite (pas de serveur de recette) ; le workflow
`nextcloud-app.yml` (manuel) installe et active l’application sur la matrice. Signature et publication sur l’App Store : responsable
du projet. Recettes N01 et N02 : non réalisées.

## Distribution (chapitre 10)

Partiel : paquets serveur, poste Ubuntu et Windows construits depuis la même source ; version unique ; actifs versionnés. Non réalisé
dans cet environnement : installation et mise à jour réelles sur Ubuntu 24.04 et Windows 11 hors du test automatisé de l’application
Windows, signature des exécutables, nomenclature par paquet (SBOM global inchangé).

## Demandes complémentaires de l’avocat

| Demande | État |
|---|---|
| État du système selon le mode d’installation | Réalisé : serveur Linux (systemd), poste Linux et Windows (processus réellement lancés, journaux, signe de vie, port de l’interface ; jamais `systemctl` sous Windows) ; fichier d’authentification réellement utilisé au démarrage ; autorisations Windows lues dans la liste de contrôle d’accès (vert seulement si aucun accès large, « non vérifié » si illisible) ; alertes « fonctionnalité / cause vérifiée / action » ; connecteurs « Non configuré », « À tester » ou date du dernier test. Vérifié dans le navigateur et sur les fichiers réels d’un poste Windows. |
| Agenda local par défaut | Réalisé : rendez-vous, audiences, échéances, tâches datées, rattachement aux dossiers, rappels (notification dans l’application, une seule fois), sauvegarde quotidienne (30 jours) et sauvegarde à la demande, export .ics, import .ics ou adresse iCal HTTPS (Nextcloud, Google ; séries simples développées), visibles par la fiche d’audience et le briefing ; création d’événement sans calendrier de planification → agenda local ; synchronisation Nextcloud en option (nouvel essai automatique). Limite : la synchronisation Google directe passe toujours par le module d’agendas existant. |
| Open WebUI sans section Courriel | Réalisé : la version classique d’Open WebUI n’a pas de section Courriel ; le logiciel ne renvoie plus vers `…/mail` (lien et cadre retirés) ; la messagerie se consulte dans la boîte de réception d’AxiorHub (Courriels), Roundcube restant accessible ; Open WebUI reste proposé pour la conversation. |
| Traduction des boutons et textes écrits par les scripts (« Commencer la dictée », « Suivi des engagements … », « Modifier », « Réviser un document », la mention d’auteur du menu, textes des Échéances et des Paramètres…) | Réalisé : traducteur du navigateur (`static/v5634t.js`) chargé en premier sur chaque page, même dictionnaire que le serveur, boîtes de confirmation comprises ; règles communes (modèles à donnée insérée, segments « · », phrases successives, icône ou puce en tête, fil d’Ariane) ; empreinte du dictionnaire pour ne jamais réutiliser une copie périmée ; ≈ 1 300 phrases ajoutées en anglais, allemand et espagnol après relevé automatique de 300 pages et des 36 scripts ; libellés cités dans les traductions et les fiches d’aide alignés sur l’écran. Restent volontairement en français : données du cabinet (noms, objets, noms de dossiers Nextcloud comme « Procédure »), références légales, formules de courriers français, exemples de commandes vocales (interprétées en français). Limite : un message renvoyé par le serveur n’est traduit que s’il figure au dictionnaire. |
| Rapports dans la langue choisie (« BRIEFING DU MATIN ») | Réalisé : briefing, tri, bilan et résumé oral rédigés dans la langue du cabinet ou, à défaut, celle de l’avocat (titres des versions sans IA traduits, consigne de langue au modèle, résumé oral contrôlé dans la langue) ; les brouillons de réponse aux correspondants restent en français. Un rapport déjà produit n’est pas réécrit : le suivant est dans la nouvelle langue. |
| Dépôt GitHub dédié pour l’application Nextcloud | Préparé localement (`AxiorHub-Pilote-Nextcloud` : application à la racine, licence, recette d’installation réelle, analyse de confidentialité propre) ; création du dépôt et publication soumises à l’accord de l’avocat. |
