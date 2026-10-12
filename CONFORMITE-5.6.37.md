# Conformité 5.6.37 — application Nextcloud : interface AxiorHub et instruction au Pilote

Version de référence : 5.6.36. Demandes de l’avocat du 12 octobre 2026 sur l’application Nextcloud AxiorHub Pilote (0.3.0) :
retrouver l’interface d’AxiorHub adaptée à Nextcloud, aux couleurs d’AxiorHub, avec un menu repliable, corriger l’onglet Pilote qui
échouait, décrire l’installation serveur pour la boutique Nextcloud ; puis corrections de l’audit du code 5.6.36 (fiches F01 à F16).

Légende : **Réalisé** : livré et couvert par un test. **Partiel** : livré avec la limite indiquée. Sauf mention contraire, les tests
cités sont dans `tests/test_v5637.py` (29 tests). Toutes les données d’essai sont fictives.

## Correctif

| Demande | État 5.6.37 |
|---|---|
| Onglet Pilote : « autorisation_mission_non_verifiable », « Instruction au Pilote — Échec, traitement interrompu » | **Réalisé.** Cause : la mission créée depuis Nextcloud appartenait au compte relié (compte du serveur) ; sur une installation serveur, sans comptes propres, le contrôle d’autorité qui précède chaque effet ne pouvait vérifier que le propriétaire « cabinet » et refusait la mission. Correctif : `missions567.owner_for` — mission au cabinet sur un serveur (comme les missions lancées depuis l’interface), au compte relié en distribution Docker (comptes du cabinet, vérifiés). Tests : `MissionOwner` (mission réelle créée par l’intégration, contrôle d’autorité réussi) ; `test_v5636.PersonalKeys` ajusté. |

## Interface AxiorHub dans Nextcloud (application 0.4.0)

| Demande | État 5.6.37 |
|---|---|
| Retrouver l’interface d’AxiorHub, adaptée à Nextcloud | **Réalisé.** La page « AxiorHub Pilote » affiche AxiorHub lui-même dans un cadre ; les rubriques (Aujourd’hui, Dossiers, Courriels, Agenda et tâches, Produire, Paramètres, puis Corporate, Contacts & CRM, Initiatives, Outils IA, Recherche juridique, Missions) sont dans le menu de gauche ; dans le cadre, le menu propre d’AxiorHub est masqué (un seul menu, celui de Nextcloud, avec « Toutes les fonctions ») et la page prend toute la largeur. « Ouvrir dans un onglet » ; « Vue simplifiée » (écran 0.3.0). Vérifié dans le navigateur (banc local : moteur 5.6.37 et page hôte sur une autre origine, application compilée) ; tests PHP `EmbedSessionTest` (7). |
| Un seul menu dans Nextcloud | **Réalisé.** Dans le cadre, le menu et le bouton ☰ d’AxiorHub sont masqués ; « Toutes les fonctions » est une entrée du menu Nextcloud qui ouvre la page `/toutes-les-fonctions` (mêmes groupes et liens que le panneau d’AxiorHub). Test : `test_single_menu_in_nextcloud_and_all_functions_page` ; vérifié dans le navigateur. |
| Menu de gauche repliable | **Réalisé.** « Replier le menu » en bas du menu ; le bouton ☰ de Nextcloud le rouvre, dans une bande sombre qui ne recouvre jamais AxiorHub ; choix mémorisé dans le navigateur. Vérifié dans le navigateur (replié, rouvert, rechargé). |
| Description pour la boutique d’applications Nextcloud | **Réalisé.** `info.xml` : résumé et description en anglais et en français (logiciel destiné aux avocats français), tutoriel pas à pas d’installation et de réglage de la version serveur sur Ubuntu, capture d’écran (données fictives), ordre et longueurs conformes au schéma `info.xsd`. La signature de l’application et l’inscription sur la boutique restent à faire par l’éditeur (certificat Nextcloud). |
| Couleurs d’AxiorHub (cadre bleu foncé à gauche, boutons espacés) | **Réalisé.** Menu de l’application : panneau #17191e, logo et nom AxiorHub, boutons de 42 px espacés de 4 px, rubrique active #303b4b, séparateurs — valeurs relevées sur le menu d’AxiorHub et contrôlées dans le navigateur. |
| Sans ressaisir le mot de passe, sans affaiblir la sécurité | **Réalisé.** Contrat 1.3 : `POST /integration/v1/session` signé avec la clé personnelle remet une adresse de connexion à usage unique (60 s ; seule l’empreinte est conservée) ; `/nextcloud-session` la réclame une seule fois et pose un cookie de session de 12 h (HttpOnly, chemin d’AxiorHub ; Secure, SameSite=None, Partitioned en HTTPS). La session tombe avec la clé (révocation, « Délier », liaison désactivée). Cadre permis au seul site Nextcloud déclaré par la clé, et seulement pour cette session ; connexion par mot de passe : `frame-ancestors 'none'` inchangé. Jeton CSRF et contrôle d’origine des écritures inchangés. Page d’arrivée limitée aux chemins internes. Rôle assistant et mode d’instance : pas de session. Tests : `NextcloudSession` (7), essai de bout en bout PHP → moteur (adresse, cookie, en-têtes, usage unique). |
| Session expirée ou bloquée | **Réalisé.** Dans un cadre sans session : page sans donnée (aucune fenêtre de mot de passe) qui prévient l’application ; celle-ci demande une seule nouvelle adresse, puis explique le blocage et propose l’onglet ou la vue simplifiée. Vérifié dans le navigateur (adresse périmée remplacée automatiquement). Test : `test_frame_without_session_asks_nextcloud_for_a_new_one`. |

## Audit du code 5.6.36 (16 fiches)

Chaque fiche a son contre-test reprenant le scénario de l’audit (classes `AuditFacade`, `AuditManifest`, `AuditCorporate`,
`AuditAutomations`).

| Fiche | Correction 5.6.37 | Test |
|---|---|---|
| F01 (P1) La façade Docker supprimait les signatures Nextcloud | Pour `/integration/v1/` seulement, les deux en-têtes de transport du contrat signé (`X-AxiorHub-Context`, `X-AxiorHub-Key`) sont conservés ; les en-têtes d’identité (USER, ROLE…) restent supprimés ; liste centralisée. | `test_f01_…` : appairage, me, dossiers, déliaison à travers StandaloneAuth ; identité usurpée ignorée ; mauvaise signature et clé révoquée refusées. |
| F02 (P1) Compte désactivé, clé encore valide | Clé liée à l’identité stable du compte et à une version des autorisations ; désactivation, changement de rôle et réinitialisation l’incrémentent (une réactivation ne ressuscite pas la clé) ; compte, activité et rôle revérifiés à CHAQUE requête signée (comptes du cabinet ou compte de l’interface serveur) ; refus si l’état ne peut être établi. Clés 5.6.36 rattachées une fois au compte actuel. | `test_f02_…` |
| F03 (P1) Un assistant pouvait approuver | Capacités du contrat par rôle actuel : décider (approuver, refuser, demander une correction) et ouvrir l’interface = avocat ou administrateur ; contrôle avant toute lecture du résultat ou écriture. | `test_f03_…` : 403, aucune décision écrite. |
| F04 (P1) Manifeste : mauvaise pièce ou mauvaise version | Pièce identifiée par son nom (et chemin relatif) ; ETag et empreinte du texte vérifient la version ; contradiction = erreur explicite (`version_differente_de_l_index`, `etag_d_une_autre_piece`, `empreinte_du_texte_differente`) ; empreinte des octets signalée « non vérifiée » ; versions acceptées conservées, revérifiées à la création du travail et avant lecture (pièce changée = retirée et signalée). | `test_f04_…` ; `test_v5635` ajusté. |
| F05 (P0) Chiffres Corporate mal justifiés | Contrôle strict par chiffre : extrait non vide retrouvé dans les comptes, même poste, montant (signe, parenthèses) DANS l’extrait, devise, unité (unités, milliers, millions) et exercice ; cause et extrait (page) restitués dans « Actes préparés » ; méthode versionnée (2), rapports antérieurs signalés « à recontrôler » avec « Relancer la préparation ». | `test_f05_…` (N01, N02, N03, signe, kEUR, deux lignes, exercice, témoin conforme) |
| F06 (P1) Annulations cumulées | Cumul par associé comparé aux titres détenus, opération entière refusée avant écriture ; homonymes refusés ; positions négatives existantes affichées comme anomalie du registre (jamais masquées). | `test_f06_…` (4 000 + 4 000 refusé, 3 000 + 3 000 accepté) |
| F07 (P1) Souscriptions négatives | Chaque ligne : nom, apport > 0, nombre entier > 0, apport = nombre × nominal ; capital et nominal strictement positifs (aussi dans la fiche société) ; erreur qui cite la ligne. | `test_f07_…` : aucune société, souscription ni opération. |
| F08 (P1) Rollback partiel | Schémas créés avant la transaction ; plus aucun commit ni journal dans les écritures internes (société, contacts) ; une seule transaction par opération et par réalisation. | `test_f08_…` : échec simulé à la mise en file et au premier mouvement, aucune écriture partielle. |
| F09 (P1) Registres modifiés avant validation | La saisie prépare un PROJET (contrôles complets dont l’affectation, actes préparés) ; fiche, registres, capital et dirigeants ne changent qu’à « Enregistrer la réalisation » (date, justificatif ; avocat ou administrateur) ; « Abandonner le projet » ; opérations 5.6.36 signalées. | `test_f09_…` ; tests Corporate 5.6.36 ajustés. |
| F10 (P1) File pleine | Opération et demande de travail dans la même transaction (file pleine = rien n’est écrit) ; clé d’idempotence du formulaire ; opérations sans travail rattachées ou signalées « préparation à relancer » avec bouton. | `test_f10_…` |
| F11 (P1) Initiative écartée encore exécutée | Version de validation ; écarter l’invalide et annule le travail en attente ; état et version revérifiés au démarrage et avant chaque étape ; transition finale conditionnelle. | `test_f11_…` : aucun événement créé. |
| F12 (P1) Rédaction en attente affichée faite | Étape « rédaction en cours » liée à son travail ; « document préparé » seulement quand le fichier existe ; échec visible, relance de la seule étape ; plan « rédactions en cours » jusqu’à la fin. | `test_f12_…` ; test 5.6.36 ajusté. |
| F13 (P2) Niveau de relance après écarter | Niveau calculé sur les seules relances dont l’envoi est confirmé (« Marquer comme envoyée ») ; écarter n’incrémente pas ; un seul projet ouvert par facture. | `test_f13_…` ; test 5.6.36 ajusté. |
| F14 (P1) Relance après paiement | Facture relue avant dépôt (Invoice Ninja, lecture seule) : réglée ou annulée = projet retiré ; solde modifié = projet actualisé (corrections de l’avocat conservées) ; API muette = réserve explicite. | `test_f14_…` |
| F15 (P2) Routine du jour perdue | État de la routine (réservée, en attente, lancée), prochain essai, dernière erreur ; une file pleine reste récupérable le jour même, une seule exécution. | `test_f15_…` |
| F16 (P1) Journées entières vers Outlook | Dates civiles de minuit à minuit (fin exclusive, même fuseau) ; lecture : un instant UTC de la veille à 23 h redevient la bonne date ; événements horaires inchangés. | `test_f16_…` (un jour, deux jours en été, aller-retour) |

Amélioration ergonomique suggérée par l’audit (focus clavier de la fenêtre du Pilote) : non traitée dans cette version.

## Limites

- **Partiel — domaines différents** : quand AxiorHub et Nextcloud ne partagent pas le même domaine, la session est un cookie tiers ;
  certains navigateurs le bloquent (l’application le détecte et propose l’onglet). Même domaine recommandé (README de l’application).
- **Partiel — distribution Docker** : la couche des comptes du cabinet demande sa propre connexion ; l’application propose l’onglet.
- Application non installée sur un Nextcloud réel depuis le poste de construction : essais PHP (unitaires, de bout en bout) et
  application compilée chargée dans une page hôte de recette.
- F14 : un paiement saisi hors d’Invoice Ninja n’est pas connu ; F16 : non essayé avec un compte Microsoft réel.
- Une révocation est prise en compte par l’interface en 30 secondes au plus (mémoire de vérification des sessions).
