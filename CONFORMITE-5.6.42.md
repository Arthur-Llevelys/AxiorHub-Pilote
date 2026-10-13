# Conformité 5.6.42 — audit 5.6.41 et version ExApp

Version de référence : 5.6.41. Demandes de l’avocat du 13 octobre 2026 : générer une version 5.6.42 sur la base de l’audit
« Audit-AxiorHub-Pilote-5.6.41 » et une version ExApp pour Nextcloud sur la base du cahier des charges « AxiorHub Pilote ExApp ».

## Audit 5.6.41

| Fiche | Priorité | État 5.6.42 | Recette |
|---|---|---|---|
| F01 Unité comptable attribuée à tort | P0 | **Corrigé.** Périmètre des déclarations d’unité (ligne, tableau ou section, document), conflit = unité non établie, source jointe, méthode 5 et recontrôle des rapports antérieurs. | R01 refusé ; T01 accepté ; deux tableaux, pied de tableau, mention propre à une ligne, saut de page, conflit (`UnitScope`). |
| F02 Anciens modèles Word sans champs | P1 | **Corrigé.** Version des métadonnées distincte, reconstruction à la première ouverture hors transaction, idempotente, libellés conservés, source illisible signalée. | R02a et source inaccessible (`LibraryAudit5641`). |
| F03 Word modifié, métadonnées périmées | P1 | **Corrigé.** Empreinte comparée avant préparation, nouvelle version et écarts présentés, refus explicite, aperçu et préparation liés à la version affichée, « Actualiser le modèle ». | R02b, page affichée avant le changement, actualisation sans changement. |
| F04 Retours à la ligne perdus | P2 | **Corrigé.** Lecture XML ordonnée (sauts de ligne, tabulations), distincte de la lecture des balises. | R03, tabulation, balise répartie entre runs. |
| F05 Instantané remplacé sans alerte | P1 | **Corrigé.** Jeton = instantané exact, empreinte vérifiée, erreur qualifiée propagée au travail ; sans jeton, sélection automatique conservée. | R04, rédaction refusée, demande sans jeton. |
| F06 Titres cités pris pour des sections | P1 | **Corrigé.** Couverture structurelle (intitulés et corps), sections absentes, citées ou vides, contrôle indisponible signalé, affichage dans la relecture. | R05, intitulés numérotés et reformulés, chapitre vide. |
| F07 Purge d’une trame utilisée | P1 | **Corrigé.** Relation explicite demande–instantané, références établies avant suppression (transaction), lecture impossible = purge différée et journalisée. | R06, relation sans marqueur, instantané non référencé supprimé. |
| Performance de la purge | — | **Fait.** Index sur l’expiration et la création, candidats seulement, lots bornés. | Lecture des seuls candidats. |
| Ergonomie Nextcloud : trois configurations | — | **Fait (application 0.4.3).** Choix guidé, champs pertinents, « Tester l’adresse », dernier test réussi. | Tests PHP et `NextcloudApp043`. |
| Ergonomie de la fiche (progression, version, actualisation) | — | **Fait.** Progression des informations, version et empreinte du Word, état de synchronisation, champs nouveaux et disparus. | Fiche contrôlée dans R02a et R02b. |
| Écarts avant la relecture | — | **Fait.** Couverture dans les notes et le résultat du travail ; « contrôle indisponible » distinct d’un succès. | R04 et R05. |
| Gestion des utilisateurs dans les paramètres | — | **Fait.** Rubrique « Comptes » : administrateur, identifiant, changement de mot de passe (mot de passe actuel exigé) ; comptes du cabinet pour la distribution autonome. | `Accounts`. |

Recette native (navigateur, Windows, Word, mobile, Nextcloud réel) : à mener ; les contrôles de routes et la suite ne la remplacent pas.

## Cahier des charges ExApp

| Lot | État | Où |
|---|---|---|
| L0 Décisions | Propositions retenues : identifiant `axiorhub`, application PHP maintenue, Nextcloud 32 à 35, amd64. Certificat du nouvel identifiant et dépôt GitHub à créer par le responsable du projet. | — |
| L1 Mode ExApp du moteur | **Fait.** Stockage dans le volume, authentification déléguée, rôles par groupes, Nextcloud par AppAPI, superviseur commun avec la version poste ; version serveur et postes inchangés. | `agent/exapp5642.py`, `exapp.py`, tests `ExApp`. |
| L2 Image et passerelle | **Fait, à qualifier sur un serveur d’essai** : `Dockerfile` Alpine, `start.sh` de référence, cycle de vie, en-têtes ; essai de bout en bout en processus local (faux Nextcloud) réussi ; installation réelle par HaRP non essayée (pas de Docker sur la machine de développement). | Dépôt AxiorHub-Pilote-ExApp 0.1.0. |
| L3 Intégration Nextcloud | **En partie** : menu supérieur, page, rôles, utilisateur de service ; restent les actions au nom de l’utilisateur connecté, les notifications, la page d’administration. | — |
| L4 Migration | **À faire.** | — |
| L5 Sécurité, sauvegarde, performance | **À faire** (procédure de sauvegarde du volume documentée). | — |
| L6 Publication | **À faire** par le responsable du projet : image ghcr.io (publiée par la recette GitHub pour un tag), certificat, boutique. | — |
