# AxiorHub Pilote 5.6.8 — engagements, suites de missions et voix

Livraison cumulative du 6 octobre 2026, construite à partir du paquet 5.6.7 livré.
Le paquet comprend l'application complète. Les configurations et données du
cabinet restent hors de l'archive. Les anciennes notes de version sont conservées
pour l'historique ; les guides 5.6.8 décrivent cette livraison.

## Fonctions ajoutées

- Agents documentaires configurables en français : interprétation en recette
  fermée, prévisualisation, simulation sans effet, activation, modification avec
  révision, suspension et suppression. Cinq modèles : renvoi, soit transmis,
  injonction, déclaration d'appel/convocation et nouvelles conclusions.
- Déclencheurs par nom ou intitulé du document, fenêtre de création WebDAV prouvée
  de dix jours modifiable ; option explicite de dernière modification. Aucun âge
  inventé lorsqu'une propriété manque. Surveillance progressive des dossiers et
  dossiers d'arrivée configurables, dans les seules racines autorisées.
- Chaîne persistante : extraction complète, analyse par fragments avec citations
  et cache, rattachement, MOVE conditionnel vers PROCEDURE sans écrasement,
  inscriptions dédupliquées et relues dans chaque agenda, brouillon IMAP relu,
  tâche interne et, si justifié, mission existante de conclusions en réponse.
- Agendas Nextcloud, CalDAV supplémentaires et Google Calendar. Client OAuth,
  état lié à l'utilisateur et à usage unique, PKCE, renouvellement en coffre,
  choix des calendriers, autorisation de transmission et révocation. Aucun mot
  de passe Google demandé. Rappels Google minimaux par défaut, sans invités.
- Profil de procédure par dossier : rôle, dominus litis, avocat, circuit, partie,
  déclaration d'appel, constitution adverse, majorations et incidents. Les dates
  directes et délais calculés sont distincts. Les délais d'appel non suffisamment
  prouvés restent proposés ; aucune date de fichier ne remplace une réception.
- Pages Agents et règles, Agendas et procédure, Résultats des agents ; preuves
  repliables, étapes en direct, reprise et résolution des ambiguïtés. Les six
  rubriques principales sont conservées.

- Profil personnel de proactivité : identité et alias autorisés, fuseau, rôles,
  autonomie, exceptions par dossier, vacances et plafond quotidien de plans.
- Lecture périodique du véritable dossier IMAP Envoyés, avec curseur UID/UIDVALIDITY.
  Extraction prudente des promesses et demandes de pièces, hors citations et anciens
  fils ; dates relatives calculées depuis la date source, conditions conservées.
- Journal d'engagements avec provenance, état et confirmation d'exécution distincte
  d'un projet simplement préparé. Une réponse au fil suspend la relance et demande
  une vérification des pièces ; l'avocat peut reprendre le suivi des manquants.
- Suites persistantes de huit étapes maximum, avec dépendances et réutilisation
  des missions, producteurs, corrections et fournisseurs IA existants. L'analyse
  précède la production ; suspension, annulation et reprise sont persistantes.
- Rôles Coordonnateur, Courriels, Dossiers, Contentieux, Contrats, Réunions, Veille,
  Diligences et Qualité. Ils ne correspondent pas à neuf modèles permanents.
- Relances neutres aux clients confirmés, seulement en Brouillons et après relecture
  du fil. Journal de dépôt, recherche du Message-ID, vérification du contenu et UID
  avant réussite ; aucune relance automatique répétée après erreur ou dépôt vérifié.
- Nouvelles pièces et versions raccordées à l'inventaire WebDAV existant ; audience
  et rendez-vous en ligne raccordés au watcher CalDAV. Les productions propres de
  l'agent sont écartées pour éviter une boucle d'auto-déclenchement.
- Préparation de salon Nextcloud Talk privé, sans invitation ni modification
  d'événement ; vérification serveur des participants et réconciliation d'une
  création incertaine. Un lien Talk connu est relu plutôt que recréé. Report et
  annulation suspendent l'ancien plan ; une récurrence garde son identité.
- Veille RSS/Atom de domaines officiels configurés, aux jours et à l'heure choisis.
  Date, lien, extrait et erreurs de sources sont conservés. Pas de requête de dossier
  transmise à un moteur de recherche, ni de jurisprudence inventée pour remplir le brief.
- Bouton de conversation vocale sur les pages du cabinet : transcription locale,
  dialogue par tours, correction de la transcription et lecture de la réponse.
  Le mode discussion reste une analyse ; le mode mission permet les préparations
  internes. Le contexte du dossier est fixé pour la session et le micro s'arrête à
  la fermeture, au changement de page ou à la mise en arrière-plan.
- Fonction IA distincte `voice_conversation`, routée vers le modèle rapide configuré.
- TTS eSpeak NG, adaptateurs locaux compatibles Kokoro/Chatterbox, ou ElevenLabs
  explicitement autorisé. Coffre serveur, exclusions, cache privé, plafonds et
  réservation atomique du budget vocal partagé. Extension Docker Kokoro facultative.

## Correctifs et garanties

- Aucun verrou global repris de manière réentrante pendant le dépôt d'une relance.
- Déduplication des engagements après changement d'UIDVALIDITY, sans confondre deux
  contenus différents ; conservation des anciennes coordonnées IMAP de provenance.
- Un événement écarté suspend son plan déjà lancé. Une réponse reçue ne peut pas être
  écrasée par le statut « projet préparé » d'une ancienne mission.
- Une erreur d'effet distant devient un incident explicite, avec relance manuelle.
- Droits et identité revalidés ; profils, plans et caches vocaux séparés par utilisateur.
- Nouveaux traitements repris après redémarrage avec la limite existante de trois
  tentatives et relecture avant effet. Statuts de dépôt repris dans le registre métier.
- Installeur cumulatif, migrations transactionnelles 0568/0569 et compatibilité 5.6.7 ajoutée.
- Interface classique réinstallable après mise à niveau en 5.6.8.

## Portée

Voir `RECETTE-5.6.8.md` : ces ajouts ne certifient pas une qualité juridique autonome,
une conversation WebRTC en duplex intégral, ni un support de toutes les API Talk.
Les sources de veille doivent être renseignées et testées ; l'image vocale optionnelle
n'a pas été exécutée ici. Les invitations externes, les appels téléphoniques
conversationnels et l'émission de factures restent hors de cette livraison.
