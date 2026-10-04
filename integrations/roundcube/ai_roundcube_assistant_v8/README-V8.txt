AI Roundcube Assistant — V8
============================

Objet de la V8
--------------
La V8 rend visibles et auditables les traitements longs et les écritures vers
Open WebUI, Nextcloud et Invoice Ninja. Elle conserve les fonctions V7 et
ajoute un registre PostgreSQL. Elle ne crée jamais volontairement un objet
métier avant une confirmation explicite affichant la prévisualisation.

Nouveautés
----------
1. Gestionnaire universel des actions
   - chaque clic crée un identifiant d’action UUID ;
   - état, progression, résultat sûr et heure sont persistés dans PostgreSQL ;
   - le dernier état réapparaît après fermeture ou rechargement du menu ;
   - états terminaux : réussi, partiellement réussi, échoué ou annulé ;
   - le bouton actif et le bandeau animé indiquent immédiatement le travail.

2. Audit et journal du dossier
   - chaîne SHA-256 append-only dans ai_audit_events ;
   - journal des tâches proposées, en cours, accomplies et annulées ;
   - toute mutation gérée par la V8 porte l’identifiant de corrélation affiché ;
   - l’intégrité protège contre une modification SQL ordinaire, mais pas contre
     un administrateur PostgreSQL pouvant désactiver le trigger ou la table.

3. Calendriers et doublons
   - recherche avant écriture dans le registre V8, Open WebUI et l’agenda
     Nextcloud autorisé ;
   - blocage des doublons exacts ;
   - justification écrite obligatoire pour passer outre un doublon probable ;
   - lien logique entre les objets Open WebUI et Nextcloud ;
   - si une seule destination réussit, relancer avec les mêmes valeurs reprend
     uniquement la destination manquante.

4. Santé des connecteurs
   - tests en lecture seule de PostgreSQL, Invoice Ninja, WebDAV Nextcloud,
     Open WebUI, owuinc et connecteurs juridiques ;
   - le vert confirme seulement la lecture, jamais les droits d’écriture.

5. Navigateur Nextcloud restreint
   - aucune saisie libre de chemin dans l’interface V8 ;
   - racines et extensions autorisées exclusivement côté serveur ;
   - nœuds opaques chiffrés et authentifiés par AES-256-GCM, refus de la
     traversée de chemin et des répertoires interdits ;
   - versions ETag et contenus SHA-256 mémorisés : un fichier inchangé ou
     strictement identique est ignoré, y compris après rechargement ;
   - 25 Mo par fichier et 100 Mo par sélection dans l’interface.

6. Automations Open WebUI
   - l’IA propose au plus trois automations sans rien créer ;
   - date ambiguë ou RRULE incomplète : bouton bloqué ;
   - détection de doublon exacte avant et après confirmation ;
   - prompts limités à l’analyse et aux rappels, sans écriture externe.

7. Discussion « État du dossier »
   - inventaire les fichiers, conversations, Notes rattachées, événements,
     automations et activités ;
   - crée une discussion dédiée après confirmation ;
   - les actualisations suivantes ajoutent un message différentiel sans
     écraser l’historique ;
   - un instantané versionné empêche de recalculer si aucune source n’a changé.

Prérequis
---------
- Roundcube 1.6.7 et PHP 8.4 avec curl, DOM, fileinfo, json, mbstring,
  OpenSSL, zip et pdo_pgsql.
- PostgreSQL 16 accessible localement.
- Open WebUI avec droits sur dossiers, fichiers, connaissances, Notes,
  calendriers et automations.
- Jeton Invoice Ninja dédié déjà révoqué/remplacé si l’ancien a été divulgué.
- Compte Nextcloud dédié avec mot de passe d’application et droits minimaux.
- owuinc configuré pour le calendrier « CABINET EXEMPLE ».

1. Créer la base PostgreSQL dédiée
-----------------------------------
Le mot de passe suivant est généré localement et n’est jamais affiché. Copiez
le bloc directement depuis le fichier texte : aucun antislash ne doit être
ajouté devant les caractères `_`.

  sudo bash <<'SCRIPT'
  set -Eeuo pipefail

  install -d -o root -g www-data -m 0750 /etc/roundcube
  install -o root -g www-data -m 0640 /dev/null \
    /etc/roundcube/ai-v8-postgresql.token

  secret="$(openssl rand -hex 48)"
  printf '%s' "$secret" > /etc/roundcube/ai-v8-postgresql.token

  if ! runuser -u postgres -- psql -Atqc \
    "SELECT 1 FROM pg_roles WHERE rolname='ai_roundcube_v8'" | grep -qx 1; then
    runuser -u postgres -- psql -v ON_ERROR_STOP=1 \
      -c 'CREATE ROLE ai_roundcube_v8 LOGIN'
  fi

  printf "ALTER ROLE ai_roundcube_v8 PASSWORD '%s';\n" "$secret" | \
    runuser -u postgres -- psql -v ON_ERROR_STOP=1 >/dev/null

  if ! runuser -u postgres -- psql -Atqc \
    "SELECT 1 FROM pg_database WHERE datname='ai_roundcube_v8'" | grep -qx 1; then
    runuser -u postgres -- createdb -O ai_roundcube_v8 ai_roundcube_v8
  fi

  runuser -u postgres -- psql -v ON_ERROR_STOP=1 -d postgres \
    -c 'ALTER DATABASE ai_roundcube_v8 OWNER TO ai_roundcube_v8' >/dev/null

  unset secret
  echo "Base PostgreSQL V8 prête."
  SCRIPT

Le rôle dédié est propriétaire de sa seule base. Ne lui donnez aucun rôle
superuser, CREATEDB, CREATEROLE ou accès à une autre base métier.

2. Créer la clé de signature du navigateur
-------------------------------------------

  sudo install -o root -g www-data -m 0640 /dev/null \
    /etc/roundcube/ai-v8-signing.key
  sudo bash -c 'openssl rand -hex 32 > /etc/roundcube/ai-v8-signing.key'

Ne réutilisez ni le mot de passe Nextcloud, ni le jeton Invoice Ninja.

3. Configurer le plugin
-----------------------
Ajoutez au config.inc.php existant les paramètres manquants de
config.v8.inc.php.sample. Ne remplacez pas le reste du fichier.

Points obligatoires :
- ai_v8_pg_dsn doit viser la base ai_roundcube_v8 ;
- ai_v8_pg_user doit être ai_roundcube_v8 ;
- les deux secrets doivent être root:www-data, mode 0640 ;
- ai_nextcloud_allowed_roots doit contenir seulement les répertoires que le
  plugin peut révéler et lire ;
- ai_nextcloud_calendar_name doit être exactement « CABINET EXEMPLE » ;
- l’URL Nextcloud est la racine HTTPS, sans /apps/files ni /apps/calendar.

4. Installer
------------

  cd /tmp
  sha256sum -c ai-roundcube-assistant-v8.tar.gz.sha256
  mkdir -p /tmp/ai-roundcube-v8
  tar -xzf ai-roundcube-assistant-v8.tar.gz -C /tmp/ai-roundcube-v8
  cd /tmp/ai-roundcube-v8
  sudo bash install-v8.sh

L’installateur :
- vérifie PHP et JavaScript ;
- applique la migration PostgreSQL transactionnelle ;
- sauvegarde le plugin complet sous /root ;
- installe PHP, JavaScript, CSS et la migration ;
- ne modifie jamais config.inc.php ni les secrets.

Le fichier .sha256 est normalement livré à côté de l’archive, et non dedans.
Si vous avez extrait seulement l’archive, vérifiez l’empreinte communiquée
avant extraction depuis le répertoire qui contient les deux fichiers.

5. Tests de réception obligatoires
----------------------------------
Effectuez d’abord les tests avec un projet, un calendrier et des fichiers sans
donnée confidentielle.

A. Progression persistante
1. Lancez « Résumer ».
2. Vérifiez notification, spinner, bouton actif et identifiant d’audit.
3. Fermez puis rouvrez le menu pendant l’action : l’état doit réapparaître.

B. Confirmation et double-clic
1. Double-cliquez rapidement sur « Enregistrer tout » puis annulez.
2. Aucun objet ne doit être créé avant la confirmation.
3. Après confirmation, une seule conversation doit exister.

C. Événements et succès partiel
1. Créez un événement test dans les deux calendriers.
2. Recommencez avec les mêmes valeurs : le doublon exact doit être bloqué.
3. Rendez temporairement une destination indisponible, créez un autre test,
   puis rétablissez-la et relancez : seule la destination manquante doit être
   créée. Le même logical_event_id doit être conservé.

D. Nextcloud restreint
1. Vérifiez que seules les racines autorisées apparaissent.
2. Testez un répertoire parent, un lien symbolique/WebDAV et une extension
   interdite : ils doivent rester invisibles ou être refusés.
3. Importez un PDF autorisé et contrôlez son indexation dans la connaissance.
4. Réimportez-le sans modification : il doit être annoncé comme identique et
   aucune seconde ressource Open WebUI ne doit subsister.

E. Automations et état du dossier
1. Testez une date explicite puis une date ambiguë (« la semaine prochaine »).
2. La date ambiguë ne doit pas pouvoir être créée sans correction.
3. Créez « État du dossier », modifiez une source, puis actualisez : l’ancien
   contenu doit rester et seule une mise à jour différentielle est ajoutée.

F. Santé et audit
1. Ouvrez « Santé des connecteurs » puis coupez un connecteur de test : son
   état doit passer en erreur sans créer d’objet.
2. Contrôlez les tables :

  sudo -u postgres psql -d ai_roundcube_v8 -c \
    "SELECT action_id,action_type,status,progress,updated_at FROM ai_integration_actions ORDER BY updated_at DESC LIMIT 20;"
  sudo -u postgres psql -d ai_roundcube_v8 -c \
    "SELECT correlation_id,action,phase,target,status,occurred_at FROM ai_audit_events ORDER BY occurred_at DESC LIMIT 30;"

Sécurité et limites franches
----------------------------
- Un appel owuinc reste piloté par le modèle. La V8 affiche son résultat, mais
  il faut contrôler l’objet dans Nextcloud après création.
- Une empreinte évite les doubles créations connues du plugin ; elle ne donne
  pas une garantie distribuée absolue si un service distant crée l’objet puis
  coupe la connexion avant de répondre.
- La détection « probable » repose sur l’heure et la similarité du titre ; elle
  peut produire des faux positifs ou manquer un doublon très différent.
- Les fichiers indexés doivent être contrôlés : succès d’import ne signifie pas
  extraction OCR juridiquement fiable.
- La synthèse et l’État du dossier restent des travaux préparatoires. Aucun
  résultat IA ne remplace la lecture des pièces et la vérification des sources.

Correctif du 5 septembre 2026
-----------------------------
Les paramètres booléens des transitions d’action sont envoyés explicitement
sous la forme « true » ou « false ». Cette correction évite l’erreur
« Journal PostgreSQL V8 indisponible » qui pouvait apparaître immédiatement
après la création d’une action alors que la connexion à la base fonctionnait.
