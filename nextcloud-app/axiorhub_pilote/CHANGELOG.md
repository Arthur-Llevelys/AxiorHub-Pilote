# Journal des versions — application Nextcloud AxiorHub Pilote

## 0.4.2 — version de développement (avec le moteur 5.6.41, contrat 1.3)

- **Mode découverte avec un AxiorHub installé sur un poste de travail** (Windows 11, Ubuntu poste), pour des tests seulement :
  - *Nextcloud sur le même ordinateur* (y compris Docker Desktop sous Windows, adresse `http://host.docker.internal:<port>`) : le moteur
    5.6.41 accepte les appels venus de ce même ordinateur et se signale (`mode: poste`) ; l'interface affiche un **bandeau
    d'avertissement permanent** (tests et découverte seulement, joignable depuis cet ordinateur seulement, échanges locaux non chiffrés,
    version serveur recommandée) ;
  - *Nextcloud hébergé* : nouveau réglage personnel **« Adresse de mon AxiorHub local »** (adresse de bouclage `127.0.0.1`, `localhost`
    ou `[::1]` seulement) et entrée **« Mon AxiorHub local »** qui ouvre AxiorHub dans un nouvel onglet du navigateur, avec le même
    avertissement ; le serveur Nextcloud ne s'y connecte jamais (ni Pilote, ni recherche, ni affichage dans Nextcloud).
- Messages : « AxiorHub installé sur un poste de travail : utilisable seulement en mode découverte… » (code
  `moteur_poste_non_pris_en_charge`, appel relayé depuis un autre ordinateur) ; « AxiorHub est injoignable » oriente vers « Mon AxiorHub
  local » ; note à côté de l'adresse dans les paramètres personnels et d'administration.
- **README en français et en anglais** (`README.md`, `README.en.md`, ce dernier inclus dans le paquet) : ce que fait l'application, choix de la configuration, étapes, mode découverte (« jamais pour la production ») ; captures fictives dans `screenshots/fr` et `screenshots/en`.
- **Description de la boutique et README** : prérequis (serveur Linux, Nextcloud, HTTPS, mémoire, disque, comptes), trois étapes —
  installer la version serveur d'AxiorHub, installer l'application depuis la boutique Nextcloud, relier le compte de chaque avocat —, une
  section « Mode découverte » avec son avertissement, et un tableau des messages en cas de difficulté.

## 0.4.1 — version de développement (avec le moteur 5.6.39, contrat 1.3)

- **Changement rapide de rubrique** (audit 5.6.37, F08) : chaque demande de connexion porte un numéro ; une réponse arrivée après un
  nouveau choix est ignorée (et la demande précédente est interrompue). Le cadre affiche toujours la dernière rubrique choisie, jamais
  une rubrique plus ancienne revenue en retard.
- **Rubrique surlignée** : le menu surligne la rubrique choisie dès le clic, y compris pendant le chargement ; un choix direct rend
  obsolète toute demande encore en cours.
- Aucun changement de sécurité, de contrat ni de réglage : mise à jour simple depuis la 0.4.0.

## 0.4.0 — version de développement (avec le moteur 5.6.37, contrat 1.3)

- **L'interface AxiorHub dans Nextcloud** : la page « AxiorHub Pilote » affiche AxiorHub lui-même (Aujourd'hui, Dossiers, Courriels,
  Agenda et tâches, Produire, Paramètres, puis Corporate, Contacts & CRM, Initiatives, Outils IA, Recherche juridique, Missions), sans
  ressaisir le mot de passe : le moteur remet au compte relié une adresse de connexion à usage unique (60 secondes), échangée contre une
  session AxiorHub de 12 heures ; aucun mot de passe ni secret n'arrive dans le navigateur.
- **Menu aux couleurs d'AxiorHub** : panneau bleu nuit, logo, boutons espacés et rubrique active surlignée, comme le menu d'AxiorHub ;
  un seul menu : dans le cadre, le menu propre d'AxiorHub est masqué et « Toutes les fonctions » est une entrée du menu Nextcloud.
- **Menu repliable** : « Replier le menu » en bas du menu, bouton ☰ de Nextcloud pour le rouvrir (dans une bande sombre qui ne recouvre
  jamais AxiorHub) ; le choix est mémorisé dans le navigateur.
- **Description de la boutique** : tutoriel d'installation de la version serveur sur Ubuntu, en anglais et en français ; capture
  d'écran (données fictives).
- **« Ouvrir dans un onglet »** (nouvelle adresse à usage unique) et **« Vue simplifiée »** (écran de la 0.3.0 : instruction, recherche,
  fiche, recherche juridique, travaux récents).
- **Session expirée ou bloquée** : le cadre le signale, l'application demande une seule nouvelle adresse ; si le navigateur bloque la
  session dans Nextcloud (cookies tiers), un message l'explique et propose l'onglet ou la vue simplifiée.
- **Sécurité** : la page Nextcloud n'autorise dans un cadre que l'origine du moteur ; le moteur n'accepte d'être affiché que dans le
  site Nextcloud déclaré par la clé ; la session tombe avec la clé (révocation, « Délier », liaison désactivée).
- **Instruction au Pilote** : l'échec « autorisation_mission_non_verifiable » (moteur installé sur un serveur) est corrigé par le moteur
  5.6.37.
- Moteur 5.6.36 : la vue simplifiée reste utilisable ; un message demande la mise à jour pour l'interface complète.

## 0.3.0 — version de développement (avec le moteur 5.6.36, contrat 1.2)

- **Compte relié personnellement** : Paramètres personnels › AxiorHub Pilote (adresse, identifiant et mot de passe AxiorHub) ; le mot
  de passe sert une fois à obtenir une clé personnelle chiffrée, révocable des deux côtés. Les utilisateurs Nextcloud non reliés n'ont
  aucun accès ; le mode d'instance (secret partagé, groupes) reste disponible.
- **Interface complète** : Pilote (instruction, résultat attendu), Rechercher dans les dossiers, Fiche du dossier, Recherche juridique,
  Travaux récents ; choix du dossier par recherche.
- **« Moteur injoignable » corrigé** : une page HTML du moteur (base occupée, frontal demandant une connexion) n'est plus prise pour une
  panne réseau ; motif précis affiché ; une lecture est réessayée une fois quand le moteur est occupé.
- Tests unitaires PHP exécutés à chaque envoi (syntaxe PHP 8.1 et 8.4, API publique OCP), construction vérifiée.

## 0.2.0 — version de développement (avec le moteur 5.6.35)

- **Dossiers importés automatiquement** : l'enregistrement des réglages teste la liaison et importe les dossiers du moteur (groupe par
  défaut « admin »), actualisés toutes les 5 minutes ; tableau « Dossiers et groupes autorisés » dans la page d'administration (plus
  d'appel d'API manuel). Corrige « Aucun dossier ne vous est accessible ».
- **Secret partagé** : même règle que le moteur, 8 caractères au moins (32 aléatoires recommandés).
- **Résultat entièrement consultable** avant décision (sections, sources, corrections ; plus d'aperçu tronqué) ; décisions Approuver,
  Refuser, Demander une correction, rattachées à la version figée du résultat (audit F16, F12).
- **Suivi rattaché au bon travail** : une réponse tardive d'un ancien travail ne modifie plus l'écran (identifiant capturé, génération,
  requêtes abandonnées) (audit F17).
- **Travaux récents** : liste des 30 derniers travaux de l'utilisateur, réouverture d'un résultat ancien (figé) ; **historique des
  décisions** (version, auteur, date, commentaire) sous chaque résultat.
- Contrat 1.1 : opération signée vérifiée par le moteur, sélection de pièces réellement appliquée (audit F10, F11).
- `info.xml` : site, signalements et source pointent vers le dépôt dédié.

## 0.1.0 — version de développement (avec le moteur 5.6.34)

- Squelette natif PHP + Vue 3 : navigation Nextcloud, réglages administrateur, page de l'application.
- Contrat moteur `/integration/v1` : contexte signé (HMAC-SHA256, nonce, expiration courte), périmètre par groupe Nextcloud,
  manifestes de sources (file_id, ETag, taille), travaux avec clé d'idempotence, suivi borné, annulation, résultat versionné,
  approbation d'une version précise.
- Parcours natifs : choix d'un dossier accessible, préparation de la fiche du dossier, recherche juridique, suivi et approbation.
- Non qualifiée : aucune compatibilité n'est annoncée avant installation réelle et tests sur Nextcloud 32 à 35 (workflow manuel).
