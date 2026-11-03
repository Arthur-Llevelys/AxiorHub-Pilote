# Réversibilité : sauvegarde, restauration, export

Depuis la version 5.6.88, le cabinet garde la maîtrise de ses données sans dépendre d'un intervenant :

- une **sauvegarde quotidienne chiffrée**, pour remettre AxiorHub en service à l'identique ;
- un **export intégral** dans des formats ouverts, pour reprendre ses données dans un autre outil ;
- deux **outils de contrôle**, qui vérifient une archive ou un export sans rien écraser.

Tout se règle dans **Paramètres, Confort et maintenance**, avec un compte administrateur.

## 1. Sauvegarde automatique

### Ce qui est sauvegardé

| Dans l'archive | Contenu |
|---|---|
| `etat/` | dossier d'état : bases SQLite (copie cohérente par l'API de sauvegarde), dont la base des comptes `comptes5648.sqlite3`, réglages, coffre des secrets, `secrets5688/`, modèles, rapports |
| `comptes/` | comptes du cabinet (`users.sqlite3` et `secrets5688/`), quand ils sont hors du dossier d'état |
| `config/` | fichiers de `/etc/axiorhub-mail-agent` (serveur) ou voisins de `config.json` (poste), et registre des dossiers |
| `manifeste.json` | taille et empreinte SHA-256 de chaque fichier |

La base des comptes et le dossier `secrets5688/` (avec sa clé `.master.key`) sont toujours dans l'archive : ils portent la double authentification, les codes de secours et les jetons d'API. Sans eux, chaque compte protégé devrait être réinitialisé après une restauration. C'est aussi pourquoi l'archive est aussi sensible que le serveur lui-même et que sa phrase doit être gardée avec soin.

**Ne sont pas sauvegardés** : les documents et courriels, qui vivent dans Nextcloud et la messagerie ; la base et les fichiers de Nextcloud (sur le VPS, `deploy/vps/backup.sh` reste nécessaire) ; les archives elles-mêmes, les exports, les caches de voix, la session du navigateur supervisé, les téléchargements de mise à jour, les copies faites par `upgrade.py`, la clé de sauvegarde et les identifiants du second emplacement.

Un fichier illisible par le service (certains fichiers de `/etc` appartiennent à root) n'arrête pas la sauvegarde : il est signalé dans l'état de la sauvegarde.

### Mise en place

1. Choisissez une **phrase** de 16 caractères au moins et notez-la hors du serveur. Elle n'est conservée nulle part. **Sans elle, aucune archive ne peut être relue, et personne ne peut la retrouver.** Sans phrase, la sauvegarde ne démarre pas.
2. Réglez l'heure, le dossier local et la rétention, puis cochez « Sauvegarder automatiquement ». Par défaut : la plus récente de chacun des 7 derniers jours, puis la plus récente de chacune des 4 semaines précédentes, soit 11 archives au plus.
3. Choisissez un second emplacement : WebDAV ou stockage compatible S3 (HTTPS obligatoire). Cochez l'autorisation de dépôt : c'est votre décision qui permet l'envoi quotidien, et seule l'archive chiffrée quitte le serveur. Pressez « Tester l'emplacement ».
4. Pressez « Sauvegarder maintenant », puis « Contrôler » sur l'archive obtenue.

Sur un serveur installé avec systemd, le service n'écrit que sous `/var/lib/axiorhub-mail-agent`. Le dossier local par défaut y est placé (`sauvegardes5688`). Pour un autre dossier, ajoutez-le à `ReadWritePaths` par un fichier de surcharge systemd du service `axiorhub-mail-desk-worker`.

S3 : l'adresse est celle du service, sans le compartiment (requêtes à chemin, `https://service/compartiment/objet`). Réservez un compartiment ou un préfixe à AxiorHub.

### Déroulement

Une sauvegarde automatique par jour, à partir de l'heure choisie ; un poste éteint à cette heure rattrape au premier passage après 30 heures sans réussite. Nouvel essai 6 heures après un échec. La pause de l'IA n'arrête pas les sauvegardes : elles n'appellent aucun modèle. Une file chargée peut retarder le départ, 30 heures au plus après la dernière réussite. Sauvegarde, export et contrôle ne tournent jamais en même temps.

1. Contrôle de l'espace disque et du volume maximal (1 Gio reste toujours libre).
2. Copie cohérente de chaque base, l'une après l'autre, dans un dossier temporaire à côté des données. Ces copies, en clair, ne vont jamais à l'emplacement de sauvegarde.
3. Écriture de l'archive chiffrée au fil de l'eau, sous un nom provisoire.
4. Relecture : empreinte du fichier, déchiffrement complet, contrôle de chaque fichier contre le manifeste. Alors seulement, l'archive prend son nom définitif.
5. Rétention locale.
6. Dépôt au second emplacement, relecture complète, puis rétention distante.

Une erreur ne supprime aucune ancienne archive. La rétention ne touche que les fichiers nommés exactement `axiorhub-AAAAMMJJ-HHMM-xxxxxxxxxxxx.axbk` (et leurs fichiers provisoires) : tout autre fichier du dossier est laissé en place.

### Suivi

Paramètres affiche la dernière sauvegarde, sa taille, son emplacement et son erreur éventuelle. **Aujourd'hui** affiche une alerte si la dernière réussite date de plus de 48 heures, ou si la sauvegarde n'a jamais été mise en place. Si l'administrateur désactive la sauvegarde (autre dispositif en place), l'alerte disparaît.

Une sauvegarde dont l'archive locale est bonne mais dont le dépôt distant échoue est « partielle » : elle ne compte pas comme réussite, pour que l'alerte finisse par se déclencher.

### Phrase et clé

La clé tirée de la phrase (scrypt) est gardée dans le coffre du serveur, pour que la sauvegarde de nuit tourne sans personne. Conséquence : quelqu'un qui prendrait le contrôle du serveur pourrait lire les archives. Le chiffrement protège les archives **hors du serveur** : disque volé, second emplacement compromis.

Changer de phrase ne rechiffre pas les anciennes archives : elles restent lisibles avec l'ancienne phrase, qu'il faut garder jusqu'à leur disparition par la rétention. Le bouton « Contrôler » ne les ouvre plus ; la ligne de commande, si.

## 2. Restaurer

Principe : **rien n'écrase les données en place**. L'outil extrait dans un dossier vide ; la remise en place est un geste de l'administrateur, service arrêté.

Depuis le dossier d'installation (`/opt/axiorhub-mail-agent/current` sur un serveur), avec le Python du logiciel. La phrase est demandée au clavier, jamais en argument :

```
python3 -m agent.sauvegarde5688 controler ARCHIVE.axbk
python3 -m agent.sauvegarde5688 extraire ARCHIVE.axbk /chemin/vers/dossier-vide
```

`controler` déchiffre et vérifie sans rien écrire. `extraire` refuse un dossier non vide et vérifie chaque fichier. Seule la phrase est nécessaire : ces commandes fonctionnent sur une machine neuve où AxiorHub est installé. Pour un script, `--phrase-fichier FICHIER` lit la phrase dans un fichier ; supprimez-le ensuite.

Serveur (systemd) :

1. Installez la même version d'AxiorHub, ou une version plus récente, puis arrêtez les services `axiorhub-mail-*`.
2. Mettez de côté le dossier d'état actuel (`/var/lib/axiorhub-mail-agent`) : renommez-le, ne le supprimez pas.
3. Copiez `etat/` vers `/var/lib/axiorhub-mail-agent`, fichiers cachés compris (`.master.key`), avec l'utilisateur `axiorhub-mail` comme propriétaire et des droits privés (dossiers 700, fichiers 600).
4. Comparez `config/` à `/etc/axiorhub-mail-agent` et ne replacez que ce qui diffère, en root. Replacez `comptes/` s'il est présent.
5. Redémarrez les services. Ouvrez **État du système** et **Mise en service**.
6. Dans Paramètres, ressaisissez la phrase et les identifiants du second emplacement : ils ne sont pas dans l'archive, par sécurité.

VPS (Docker) : même démarche sous `deploy/vps/data/axiorhub/state`, conteneurs arrêtés. Nextcloud se restaure à part, depuis `deploy/vps/backup.sh`.

Poste Windows : quittez AxiorHub, renommez le dossier de données, copiez `etat/` à sa place, relancez.

Faites un essai de restauration sur une machine de test au moins une fois par an : une sauvegarde qu'on n'a jamais relue n'est pas une garantie.

Depuis la 5.6.89, un démarrage sans erreur ne suffit pas à conclure. Sur une copie prise au même instant que l'archive, comparez les deux états :

```
python3 -m agent.reversibilite5689 comparer ETAT_SOURCE ETAT_RESTAURE
```

La commande relit chaque table métier et contrôle que décisions, versions et références retrouvent leur cible. Voir aussi docs/EXPLOITATION-5.6.89.md, qui dit qui fait quoi selon l'hébergement.

### Format de l'archive

Pour pouvoir relire une archive sans AxiorHub :

```
MAGIC "AXIORHUB-SAUVEGARDE-1\n"
longueur de l'en-tête (4 octets, gros-boutiste), en-tête JSON
blocs : longueur du chiffré (4 octets), chiffré AES-256-GCM + étiquette
```

L'en-tête donne le sel et les paramètres de scrypt (`n`, `r`, `p`, longueur 32, phrase en UTF-8 normalisée NFC), le préfixe de 7 octets, et la clé propre à l'archive enveloppée (`nonce de 12 octets + chiffré`, AES-256-GCM, données associées : MAGIC). Le nonce d'un bloc est : préfixe, numéro du bloc sur 4 octets gros-boutistes, puis `01` pour le dernier bloc et `00` sinon. Les données associées sont le SHA-256 de tout ce qui précède le premier bloc. Une fois déchiffré, le flux est un `tar` compressé en gzip.

## 3. Export intégral

**Paramètres, Export intégral du cabinet.** Le travail se fait en arrière-plan et sa progression s'affiche sur la page.

| Dossier du ZIP | Contenu |
|---|---|
| `dossiers/` | registre, fiches, faits, synthèses, parties, événements, échéances, prescriptions |
| `contacts/` | contacts, liens aux dossiers, sociétés, opportunités |
| `factures/` | factures et devis, versions, paiements, imputations, clients, estimations |
| `temps/` | temps validés et proposés, conditions d'honoraires |
| `agenda/`, `taches/`, `notes/` | événements, tâches, notes et mémos |
| `messagerie_interne/` | fils, messages, participants |
| `modeles/`, `competences/` | modèles, bibliothèque, compétences, savoir-faire |
| `documents/` | inventaire et empreintes connues ; les documents eux-mêmes si l'option est cochée |
| `MANIFEST.json`, `LISEZMOI.txt` | taille et SHA-256 de chaque fichier ; description du contenu |

Chaque table existe en `.json` (valeurs exactes) et en `.csv` (point-virgule, UTF-8, pour un tableur). Dans le CSV, une cellule commençant par `=`, `+`, `-` ou `@` reçoit une apostrophe, pour qu'aucune formule ne s'exécute à l'ouverture.

- **Secrets** : mots de passe, hachés, jetons, clés et coffre sont absents, comme la base des comptes et `secrets5688/` (double authentification, codes de secours, jetons d'API). L'export part d'une liste fermée de tables et retire colonnes, clés JSON et valeurs reconnaissables.
- **Dossiers réservés** : exclus par défaut, avec ce qui s'y rattache. Les inclure demande une confirmation, tracée dans le journal et rappelée dans le `LISEZMOI.txt`. Limite : une note issue d'un courriel n'est écartée que si son dossier est connu.
- **Accès** : administrateur seulement. Demande, téléchargement, suppression et expiration sont tracés dans le journal d'audit.
- **Messagerie interne** : toutes les conversations y figurent, y compris les échanges directs entre membres du cabinet (hors dossiers réservés exclus).
- **Fichier temporaire** : non chiffré, gardé dans le dossier privé du serveur. Il est supprimé dès le premier téléchargement complet, ou après 24 heures. Trois exports prêts au plus. Une fois téléchargé, il relève de votre responsabilité : il contient des données couvertes par le secret professionnel.
- **Volume** : 40 Gio au plus, 2 millions de lignes par table. Un dépassement arrête l'export : aucun export partiel n'est livré. Documents : 100 Mio par fichier et 20 Gio au total ; ce qui dépasse reste listé comme non inclus dans `documents/inventaire`.

L'inventaire donne l'emplacement, l'étiquette Nextcloud et l'empreinte du texte lu quand le logiciel la connaît. L'empreinte du fichier n'y figure que si le document a été copié dans l'export.

## 4. Vérifier un export

**Paramètres, Vérifier un export** : le fichier est envoyé par morceaux, comparé à son manifeste, puis supprimé du serveur. Hors du logiciel :

```
python3 -m agent.export5688 verifier EXPORT.zip
```

La vérification signale un fichier manquant, ajouté, modifié ou de taille différente, et un chemin dangereux, sans rien extraire sur le disque. Elle prouve que le contenu correspond au manifeste, pas qui l'a créé : le manifeste n'est pas signé. Pour conserver une preuve d'origine, gardez à part l'empreinte du ZIP affichée à sa création (elle figure aussi dans le journal d'audit).
