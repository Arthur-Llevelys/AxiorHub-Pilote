# Traces des travaux (OpenTelemetry), 5.6.94

AxiorHub note les étapes de chaque travail de la file au format de données d'OpenTelemetry, sans bibliothèque supplémentaire : le
format est écrit par AxiorHub lui-même (`agent/traces5694.py`). Les traces servent à voir où le serveur perd du temps : la synthèse
« Où ça bloque » se trouve dans **Paramètres › Confort et maintenance › Charge du serveur**.

## Ce qui est noté

- **Un span par travail exécuté** (racine de sa trace) : type de travail, numéro, origine (demande ou automatisme), essai, attente
  avant démarrage, durée d'exécution, résultat (terminé, erreur, suspendu, annulé, remis en attente), code d'erreur et, pour une
  erreur inattendue, sa cause non confidentielle (classe d'exception, dernière étape IMAP ou Nextcloud, code errno, TLS ou IMAP).
- **Un span enfant par appel au modèle** : type et identifiant du fournisseur, modèle, palier, durée, jetons s'ils sont connus,
  statut, nouvel essai (même fournisseur après un échec) ou relais (autre fournisseur après un échec).
- **Un span enfant par lot Nextcloud ou IMAP** du travail : nombre de requêtes ou de commandes, octets reçus.
- **Un événement « refus »** pour chaque refus : pause de l'IA, horaires de l'IA, fournisseur non autorisé par la politique,
  plafond de dépense, dossier réservé.

## Ce qui n'est jamais noté

Liste fermée d'attributs (`traces5694.ATTRIBUTS`), chacun avec un format qui n'admet qu'un code, un nombre, un booléen ou une
empreinte. Aucun texte, aucune consigne, aucun nom de document, aucun intitulé de dossier, aucune adresse électronique, aucun nom de
personne, aucun message d'erreur de serveur. Le dossier n'apparaît que sous une empreinte opaque (HMAC-SHA256 tronqué, sel propre à
l'installation, gardé dans la base). Un attribut hors liste ou mal formé est refusé ; la liste est réappliquée à l'export.

## Stockage et coût

- Table `traces5694_spans` de `desk.sqlite3` (migration idempotente). Données techniques reconstruites par l'usage : elles ne font
  pas partie de l'export intégral ni de la sauvegarde du cabinet.
- Écritures groupées : les spans restent en mémoire et sont écrits en une transaction par paquets de 100, ou dès la fin d'un travail
  si la dernière écriture date de plus de 30 secondes (un serveur peu actif écrit donc chaque travail aussitôt, un serveur chargé
  écrit par paquets). La page et l'export écrivent d'abord le paquet en attente. Un arrêt brutal du service perd au plus ce paquet.
- Purge dans la même transaction : conservation 7 jours par défaut (1 à 30), plafond de 20 000 étapes par défaut (1 000 à 200 000),
  les traces les plus anciennes étant retirées entières. Aucun travail de fond n'est ajouté.
- Désactivable dans le réglage des traces (droit « Réglages »). Coût mesuré par l'essai `T07` : voir le compte rendu de la version.

## Export

Depuis la page Charge du serveur (droit « Réglages ») :

- **Télécharger** : fichier `traces-axiorhub-24h.otlp.json`, une requête `ExportTraceServiceRequest` au format OTLP/JSON
  (`resourceSpans` › `scopeSpans` › `spans`, identifiants `traceId` et `spanId` en hexadécimal, horodatages en nanosecondes et
  entiers 64 bits écrits en chaînes, `status.code` 0, 1 ou 2, ressource `service.name = axiorhub`).
- **Envoyer à un collecteur local** : envoi OTLP/HTTP (`POST`, `Content-Type: application/json`, chemin `/v1/traces` par défaut),
  sur décision d'un administrateur, par lots de 1 000 spans, sans proxy ni redirection suivie. Seules sont admises les adresses
  `127.0.0.1`, `localhost`, `::1` et un nom d'hôte **sans point** (machine du réseau du cabinet, par exemple un conteneur
  `otelcol`) ; un nom sans point qui se résout vers une adresse publique est refusé au moment de l'envoi. Toute autre adresse,
  et donc tout service en ligne, est refusée. AxiorHub n'envoie jamais de traces de lui-même.

## Compatibilité

Le fichier et l'envoi suivent le protocole OTLP/HTTP en JSON (port habituel 4318) :

- **OpenTelemetry Collector** : récepteur `otlp` avec le protocole `http` (`endpoint: 127.0.0.1:4318`) ; le fichier téléchargé peut
  aussi être relu par le récepteur `otlpjsonfile`.
- **Jaeger** (version 1.35 et suivantes, Jaeger 2) : OTLP/HTTP activé sur le port 4318 (`COLLECTOR_OTLP_ENABLED=true` pour Jaeger 1).
- **Grafana Tempo** : distributeur avec le récepteur `otlp` en `http` (port 4318), directement ou derrière un collecteur.

Vérifié dans les essais contre la structure `ExportTraceServiceRequest` seulement, avec un faux collecteur : la réception par un vrai
collecteur, Jaeger ou Tempo n'a pas été exercée.

Exemple de collecteur local minimal (fichier de configuration du collecteur OpenTelemetry) :

```yaml
receivers:
  otlp:
    protocols:
      http:
        endpoint: 127.0.0.1:4318
exporters:
  debug: {}
service:
  pipelines:
    traces:
      receivers: [otlp]
      exporters: [debug]
```

## Erreurs de relève sans cause (T06)

Constat sur un serveur : 49 travaux `live_mail430` en erreur sans cause lisible. Le champ des travaux est `erreur` (pas `error`) ;
il ne contenait que le code générique `action_interrompue_consulter_le_journal`, et rien n'était écrit au journal. Une coupure IMAP
après la connexion (commande `SELECT`, `SEARCH` ou `FETCH` : `imaplib.IMAP4.abort`, `ConnectionResetError`, délai, TLS) n'est pas
convertie en code par `mailbox.py` et tombait dans le `except Exception` de la boucle des travaux, qui jetait l'exception. Depuis
la 5.6.94, le travail garde `detail` : `cause` (classe), `service` (`imap`, `nextcloud`, `base`), `stage` (étape IMAP, ou
`apres_<étape>` si l'erreur n'est pas réseau) et `code` (errno, raison TLS, type d'erreur IMAP), et la trace porte les mêmes
attributs. Le message de l'exception n'est jamais conservé.
