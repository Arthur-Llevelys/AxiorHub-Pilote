# AxiorHub Pilote 5.6.7 — couverture et recette

Date : 6 octobre 2026. Base : version 5.6.6 publique, commit
`84a3dc658651c0c452c9d2ee2eaf10eeaf21e396`.

La livraison est une évolution réelle du code existant, cumulative et installable.
Elle conserve les fournisseurs IA, le routage hybride, les corrections, les modèles
Word, les analyses longues, les producteurs, les intégrations et la file persistante.
Les nouveaux modules les raccordent au parcours par mission ; ils ne constituent
pas un moteur LLM parallèle.

Ce document distingue la présence d’une fonction, sa vérification locale et sa
recette sur les services du cabinet. Le passage des tests avec des doubles de
services ne certifie pas la qualité juridique ni le fonctionnement de vos serveurs.

## 1. Fonctions de la 5.6.7

| Demande du cahier des charges | Livraison | Limite ou suite nécessaire |
|---|---|---|
| Interface par mission | Robot repliable sur chaque page, dossier et sélection repris, page Missions, plan, état et résultat persistants. | Un livrable principal par mission ; le planificateur de sous-missions indépendantes et l’édition générale du plan restent à développer. |
| Moins de validations | Deux niveaux explicites : préparer les travaux internes, ou proposer seulement. Ambiguïté signalée avant dépôt. | Les permissions, exclusions et décisions juridiques restent bloquantes. Une préférence d’initiative n’autorise pas un envoi. |
| Résultat et réactivité | État immédiatement enregistré, identifiant de demande stable, SSE existant et actualisation ciblée des cartes ; reprise au retour sur la page. | Recette visuelle, focus clavier, mobile et navigateur à effectuer ; certaines pages historiques conservent leurs anciens formulaires. |
| Dossiers et documents | Contexte automatiquement borné au dossier choisi ; sélection transmise à l’analyse, provenance et couverture conservées. | Les recherches générales utilisent encore une sélection RAG ; elles ne prétendent pas lire tout le cabinet à chaque question. |
| Livrables contrôlés | Dépôt Word à destination stable, octets préparés localement, relecture SHA-256, reprise sans écrasement ; contrôles IMAP existants conservés. | Les autres producteurs historiques ne sont pas tous migrés vers le nouveau protocole de dépôt. Recette IMAP/Nextcloud réelle indispensable. |
| Voix de travail | Dictée locale via pont existant ; texte corrigible avant action ; lecture locale des réponses et du briefing ; pause et arrêt. | Pas de conversation audio bidirectionnelle continue, ni lecture exhaustive de tous les types de livrables. eSpeak et le micro non testés physiquement ici. |
| Personnalisation utile | Prénom, ton, longueur, initiative, mode discret et lexique personnel ; préférences reprises par les producteurs. | Icône facultative, sans photo animée ; aucune règle juridique ne devient active silencieusement. |
| Accueil administratif | Webhooks Twilio signés, menu par touches, réception de textes WhatsApp Business Cloud signés, tâches locales relues, déduplication. | Désactivé par défaut. Pas de téléphone conversationnel, d’appels WhatsApp, de réponse sortante, de téléchargement de médias ni de mesure du coût téléphonique. |
| Connexions graphiques | IMAP, Nextcloud, agendas/listes, Ollama, SMTP, Invoice Ninja, interfaces, voix et réception ; validation et secrets chiffrés. | Les fournisseurs IA et MCP gardent leurs écrans propres. Les paramètres d’infrastructure restent un plan de déploiement non exécuté. |
| Apprentissage métier | Textes avant/après conservés jusqu’à 120 000 caractères explicitement, préférences et règles existantes conservées, cache invalidé par les réglages pertinents. | Pas de nouvel entraînement, ni simulation automatique complète de toutes les règles sur l’historique. |
| Mesure de pertinence | Scores non mesurés indiqués comme tels ; anciennes mesures sans version de score exclues des recommandations. | Il manque un corpus réel anonymisé et une notation indépendante de la vérité juridique. Les scores locaux seuls ne choisissent pas infailliblement un modèle. |
| Installation autonome | Paquet cumulatif, Docker complet fourni, migration de schéma numérotée, bootstrap protégé, paramètres classiques modifiables sans rendre /etc inscriptible. | Image non construite ici ; résolution des images en digest, SBOM de l’image finale et recette de restauration complète restent nécessaires. |

## 2. Correctifs de l’audit 5.6.6

« Corrigé localement » signifie que le parcours visé a été modifié et vérifié dans
la recette locale. « Partiel » précise un écart restant du cahier des charges.

| Référence | État | Modification et vérification |
|---|---|---|
| C-01 — secours externe | Corrigé localement | Contrôle du contexte d’origine, des exclusions, fonctions et consentements avant construction du modèle de secours. Tests de refus avant appel. |
| C-02 — API Bearer | Corrigé localement | Le Bearer de service atteint l’API sans session navigateur ; les en-têtes de rôle injectés par le client sont supprimés. Une API anonyme renvoie un 401 JSON. |
| C-03 — OnlyOffice | Corrigé localement | Routes serveur-à-serveur traversant l’authentification externe avec leur Authorization d’origine ; contrôles JWT internes conservés. |
| C-04 — rôles | Corrigé localement | Mutations non administrateur sur liste explicite, refus des nouvelles mutations inconnues ; réglages sensibles réservés à l’administrateur. |
| C-05 — premier compte / threads / proxy | Corrigé localement | Code privé de première inscription, transaction exclusive, connexions SQLite par thread, proxy exact et rejet de `*`. |
| C-06 — coût et concurrence | Corrigé localement | Montants finis et prix explicites, réservations atomiques comptées dans l’enveloppe, incertitude conservée après erreur externe. Les estimations ne remplacent pas la facture du fournisseur. |
| C-07 — sources de configuration | Partiel | Catalogue typé, révisions, coffre, SMTP lu depuis la configuration effective, migration des paramètres classiques vers /var/lib. Les anciens réglages SQLite IA/MCP subsistent et .env n’est pas une source de vérité universelle après bootstrap. |
| C-08 — assistant d’installation | Corrigé localement | Noms IMAP distincts, pas de sélection implicite des agendas, fin conditionnée à des tests récents correspondant aux connexions saisies. |
| C-09 — port Docker | Corrigé localement | Port conteneur fixe 8626 distinct du port hôte ; modèle Apache VPS utilisant le port publié. |
| C-10 — Ollama Docker | Corrigé localement | Hôtes locaux Docker reconnus dans les réglages validés ; URL conteneur/hôte expliquée et modèle exact testé. |
| C-11 — faux voyant vert | Corrigé localement | Liveness locale distincte de readiness et des heartbeats des workers ; service arrêté ou heartbeat ancien détecté. Aucun voyant système n’est présenté comme une preuve de dépôt distant. |
| C-12 — verrou global | Partiel | Productions Word, réponse au courriel et assistant autorisées en parallèle sur des dossiers différents, verrou exclusif par dossier et contrôle global de mise à jour. Les lanes de tous les producteurs et la capacité globale de chaque LLM ne sont pas complètement refondues. |
| C-13 — reprise | Partiel | Jobs ciblés reprenables, tentatives bornées, rattachement d’un job orphelin à sa mission, identifiant et octets stables pour le dépôt Word. Tous les effets historiques ne disposent pas encore d’une lease/outbox uniforme. |
| C-14 — Invoice Ninja | Corrigé localement pour la lecture | Filtrage des factures actives non archivées et ouvertes, pagination contrôlée, montants exacts conservés, pas d’effacement du cache ancien sur résultat partiel. Aucun endpoint de création n’a été ajouté. |
| C-15 — banc juridique | Partiel | Score vide devenu non évalué, version du score persistée, résultats historiques incomparables exclus. La détection indépendante d’une fausse affirmation sans claims déclarés reste une tâche du futur banc. |
| C-16 — documents longs | Partiel | Pages non extraites signalées, fingerprint du cache incluant réglages et règles, analyses des sélections et des pièces ; citations physiques quand elles sont disponibles. Les documents dépassant les limites explicites sont arrêtés, pas partitionnés sans borne. La synthèse ne garantit pas une profondeur juridique exhaustive. |
| C-17 — corrections et réécriture | Partiel | Fin des petites troncatures avant/après et des instructions de réécriture ; révision Word longue par fragments complets et cache. Certaines intentions de boutons historiques restent à unifier et la cohérence globale d’une révision doit être relue. |
| C-18 — archive privée | Corrigé localement | Liste des fichiers publics Git ou RELEASE-FILES, refus des chemins d’exploitation et des liens symboliques, manifeste, extraction contrôlée et scanner de confidentialité. Un scanner à motifs ne garantit pas l’absence de toute donnée non reconnaissable. |
| C-19 — sauvegarde | Partiel | Snapshot VPS avec producteurs arrêtés, maintenance Nextcloud, export MariaDB, extraction et contrôle effectif des SQLite de l’archive. Restauration complète avec services distants non effectuée ici. |
| C-20 — architecture / recette | Partiel | Modules dédiés pour politiques, permissions, budgets, coffre, missions, dépôt, configuration et accueil ; scénarios concurrence, signatures et vrai HTTP Waitress. Monolithe historique et tests de doubles encore présents ; pas de recette navigateur ou d’image réelle ici. |

## 3. Scénarios ajoutés et résultats

Les tests de la 5.6.7 couvrent notamment :

- dossier exclu retrouvé dans un objet JSON ou un chemin, fonction externe
  interdite, absence de consentement et refus avant construction d’un secours ;
- réservations budgétaires simultanées et maintien de la réserve incertaine ;
- secret chiffré, permissions du coffre, mauvaise clé et ancien secret compatible ;
- bootstrap protégé, Bearer préservé, absence de session sur flux/API, callback
  OnlyOffice et mutations inconnues refusées ;
- double clic et double démarrage concurrent d’un plan, propriétaire distinct,
  conversation suivie, suspension, révocation et job orphelin après redémarrage ;
- dépôt Word relu, destination déjà différente refusée, octets locaux altérés
  refusés et reprise sans second PUT ;
- verrous sur le même dossier et parallélisme sur des dossiers différents ;
- document sélectionné entier transmis à l’analyse progressive et couverture
  conservée après récupération des autres sources ;
- corrections longues conservant leur fin, révision longue couvrant tous les
  fragments, réutilisation du cache et invalidation après nouvelle consigne ;
- profil ne pouvant étendre l’autorité, lexique numérique refusé, briefing discret
  sans noms clients et appel de synthèse fixe sans shell ;
- export de configuration sans secrets, valeurs invalides et révision périmée ;
- filtrage/pagination Invoice Ninja, résultat partiel non déclaré vert ;
- webhooks signés, signature invalide refusée, doublon sans seconde tâche,
  média WhatsApp non téléchargé et contenu entrant non exécuté par un LLM ;
- vérification de SQLite réellement extraites d’une archive ;
- serveur Waitress réel sur un port local, POST de mission en HTTP et doublon
  persistant, briefing JSON sans nouvelle inférence.

Le résultat chiffré de la suite complète et les contrôles du paquet figurent dans
le fichier de livraison `VALIDATION-5.6.7.md`. Les tests historiques ont été
conservés ; les assertions de version et les fixtures ont été adaptées aux gardes
plus stricts, sans désactiver les refus de sécurité.

## 4. Recettes non effectuées ici

- Construction/démarrage de l’image Docker et certificats HTTPS.
- Import du vrai outil dans votre Open WebUI et callbacks de votre OnlyOffice.
- IMAP, CalDAV/WebDAV, Invoice Ninja et SMTP réels du cabinet.
- Comparaison juridique de modèles sur les dossiers anonymisés du cabinet.
- Audio réellement produit par eSpeak-NG, micro, Whisper, navigateur et mobile.
- Numéro Twilio et application Meta Business réels, coût et consentements du canal.
- Restauration complète Nextcloud/MariaDB, charge prolongée et panne de réseau
  lors d’un effet distant réel.

Le nom, le logo et les licences existantes ont été conservés. `SBOM.cdx.json` et
`THIRD_PARTY_NOTICES.md` décrivent les composants déclarés, dont eSpeak-NG ajouté.
Ils ne sont pas un SBOM résolu d’image construite. Aucune publication GitHub,
image publique ou modification du serveur du cabinet n’a été effectuée pour
générer cette livraison.
