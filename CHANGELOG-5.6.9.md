# AxiorHub Pilote 5.6.9 — économe, réactif, décisions à portée de clic

Livraison cumulative du 6 octobre 2026, construite à partir du paquet 5.6.8 (qui contient lui-même 5.6.5 à 5.6.7). Le paquet
comprend l'application complète ; les configurations et données du cabinet restent hors de l'archive.

## Ce que corrige la 5.6.9 dans la 5.6.8

- **/favicon.ico** répondait 400 à chaque page ; il est servi.
- **Téléphone** : le menu latéral complet s'affichait en tête de chaque page ; il est replié par défaut sur petit écran (le choix de
  l'utilisateur reste mémorisé).
- **Requête de profil en double** à chaque page (deux scripts) ; une seule lecture, partagée.
- **Relectures périodiques** (10 à 20 s, cinq scripts) : espacées à 45–60 s quand le flux en direct est connecté ; ce flux déclenche
  déjà les mises à jour. Moins de requêtes, interface plus réactive.
- **Lectures de fichiers d'état sans encodage** (relance automatique, configuration, santé, agents documentaires, authentification) :
  UTF-8 explicite. Sans cela, une relance pouvait être refusée comme « différente » sur un système dont l'encodage n'est pas UTF-8.
- **Scripts d'installation** importables hors Linux (module `pwd` facultatif) : les tests les concernant s'exécutent partout.

## Régime économe (activé d'office)

Réglable dans Outils › IA externe sûre › « Régime économe et consommation ».

- Contrôles périodiques espacés : état 30 → 120 min, pilotage 30 → 120 min, tests métier 60 min → quotidiens, cycle de production
  5 → 15 min, classement du portefeuille et indexation globale → quotidiens, rapprochement 15 → 60 min. Les courriels, la surveillance
  des documents et vos demandes ne sont pas ralentis.
- Quota journalier (20 par défaut) pour les analyses automatiques qui appellent le modèle (fiche vivante, mémoire, faits, mémoire
  opérationnelle, avis, échéances). Au-delà, l'analyse est reportée et signalée ; une demande explicite passe toujours.
- Contrôle par un second modèle réservé aux textes destinés à des tiers (courriels, actes, documents). Les préparations internes
  (rendez-vous, comptes rendus, réponses de l'assistant, stratégie, audience) sont marquées « non contrôlées, relecture par l'avocat ».
- Comptabilité des jetons : les générations locales (Ollama) sont désormais comptées (jetons entrés/sortis, durée) par fonction, aux
  côtés des appels externes et de leur coût estimé ; tableau sur 7 jours.
- Profil économe de modèles : un bouton route le tri, la lecture des pièces jointes, la conversation vocale et le contrôle vers le plus
  petit modèle de conversation installé dans Ollama ; les rédactions gardent leur modèle.

## « À décider » et règles documentaires

- Bloc « À décider » en tête d'Aujourd'hui : document au dossier ambigu (les dossiers plausibles relevés par l'agent sont proposés en
  premier, jamais choisis d'office), date de création à prouver, suite de mission bloquée, engagement à préciser. Décision sur place,
  reprise immédiate.
- Les dossiers plausibles sont conservés avec l'exécution et proposés aussi dans « Résultats des agents ».
- Sous-dossier de classement au choix dans la règle : PROCEDURE, PIECES, CORRESPONDANCES, EXPERTISES, HONORAIRES (liste fermée ; aucun
  chemin libre fourni par un modèle).

## Voix

- Lecture phrase par phrase : la première phrase est lue pendant que les suivantes se préparent ; parler ou appuyer sur Arrêter vide la
  file. Tours plus courts (800 ms de silence, 400 ms minimum).
- Capacité `progressive_speech` exposée ; `full_duplex_streaming` reste faux : il s'agit de tours successifs, la transcription locale
  n'est pas un flux continu.

## Accueil téléphonique conversationnel (administratif)

- Nouveaux réglages : « Accueil conversationnel » et « J'accepte que la voix de l'appelant soit transcrite par Twilio ». Sans les deux,
  l'accueil par touches reste tel quel.
- L'appelant dit l'objet de son appel ; un motif fermé est reconnu par mots-clés (rappel, document, rendez-vous, message ; « urgent »
  est signalé) ; une seconde question recueille le nom et, s'il le souhaite, le dossier ; une tâche est enregistrée, une seule fois par
  appel. Aucun modèle, aucun conseil juridique, aucune information de dossier, aucune réponse improvisée. Les propos sont cités comme
  données, jamais exécutés.
- Les deux étapes sont signées par Twilio (adresse complète, paramètres `step` et `intent` seuls admis).

## Invoice Ninja en écriture (après validation)

- Réglage « Autoriser la création de factures en brouillon… » (désactivé par défaut).
- Page Temps et honoraires : « Créer la facture en brouillon » à partir des temps validés non facturés d'un dossier (client Invoice
  Ninja lié, taux horaire requis) ; « Transmettre » un temps validé comme tâche (time_log).
- Chaque écriture a une clé d'idempotence, est relue par GET, puis inscrite ; en cas de doute (réponse perdue), elle est marquée « à
  vérifier dans Invoice Ninja » et jamais rejouée. Aucun paramètre d'envoi, de marquage « envoyé » ou « payé » n'est utilisé.

## Mise en service

- Outils › Mise en service : services, moteur de traitements, IMAP, dossier Envoyés, Nextcloud, Ollama, transcription locale, synthèse
  vocale, Invoice Ninja, Google Calendar, Talk, accueil, régime économe — avec le correctif à appliquer pour chaque échec.
- `python3 manage.py readiness` imprime le même rapport (Docker : `docker compose exec axiorhub python3 manage.py readiness`).
- Test du micro et du HTTPS dans le navigateur (niveau mesuré sur place, aucun son transmis).

## Limites qui demeurent

- La voix reste une conversation par tours ; un duplex intégral exigerait une transcription en flux continu.
- L'accueil téléphonique est administratif : il ne répond à aucune question juridique.
- Invoice Ninja : brouillons et temps seulement ; l'envoi, la relance et l'encaissement restent faits dans Invoice Ninja.
- Les règles documentaires exécutent un ensemble fermé d'actions ; une réception procédurale non prouvée ou un régime de délai incomplet
  reste une proposition à confirmer, désormais visible et réglable depuis Aujourd'hui.
- Les services réels (comptes, micro, Docker) doivent être installés et vérifiés sur le serveur : l'écran Mise en service le guide, il ne
  le fait pas à votre place.
