# AxiorHub Pilote 5.6.7 — installation et prise en main

Version cumulative construite le 6 octobre 2026 à partir de la 5.6.6 publique, commit
`84a3dc658651c0c452c9d2ee2eaf10eeaf21e396`. Ce paquet contient l’application entière.
Il ne faut pas empiler les archives intermédiaires. Les configurations, index,
comptes, corrections et livrables existants restent des données privées du serveur.

La 5.6.7 apporte les missions persistantes, l’assistant transversal, la dictée locale,
la lecture vocale, des paramètres de connexions éditables et plusieurs corrections
de sécurité, de budget, de reprise et de lecture des documents. Voir
`RECETTE-5.6.7.md` pour la couverture réelle du cahier des charges et les limites.

## 1. Vérifier l’archive

Placer l’archive et son fichier `.sha256` dans le même répertoire, puis :

```bash
sha256sum -c axiorhub-mail-agent-5.6.7.tar.gz.sha256
tar -xzf axiorhub-mail-agent-5.6.7.tar.gz
cd axiorhub-mail-agent-5.6.7
```

La commande doit afficher `OK`. Le paquet contient aussi `MANIFEST.sha256`, contrôlé
par l’installateur. Un hachage détecte une modification des octets ; il n’authentifie
pas à lui seul l’éditeur. Cette livraison n’a pas de signature de publication officielle.
Les mises à jour distantes continuent d’exiger votre clé publique Minisign et une
signature valide ; l’installation manuelle ci-dessous reste possible.

## 2. Mettre à jour une installation classique /opt

Cette voie correspond à une interface existante sous `/agent-courriel/` et aux
services `axiorhub-mail-*`. Une mise à jour directe depuis la 5.6.6 est acceptée.

1. Sauvegarder `/etc/axiorhub-mail-agent`, `/var/lib/axiorhub-mail-agent` et les
   livrables distants. Le coffre nécessite ses clés `.master.key`, avec les secrets
   chiffrés : une copie des seuls fichiers `.secret` serait insuffisante.
2. Laisser finir les productions, ou les suspendre dans l’interface. L’installateur
   refuse un changement pendant qu’un producteur détient le verrou.
3. Installer les dépendances utiles puis appliquer le paquet :

```bash
sudo apt-get update
sudo apt-get install -y python3-cryptography espeak-ng
sudo bash install.sh
sudo python3 /opt/axiorhub-mail-agent/current/install-interface.py
```

Si le message « Un traitement est en cours » persiste, attendre sa fin puis arrêter
la surveillance et les workers actifs avant de relancer. Vérifier les noms de vos
unités avec `systemctl list-units 'axiorhub-mail-*'`. Ne pas supprimer `run.lock`,
les bases SQLite ni les traitements pour contourner ce contrôle.

`install-interface.py` conserve l’authentification existante. Il place la
configuration modifiable dans
`/var/lib/axiorhub-mail-agent/configuration567/config.json` et crée un lien root
depuis `/etc/axiorhub-mail-agent/config.json`. Le service peut ainsi enregistrer vos
réglages sans obtenir le droit de modifier les jetons d’API ou les autres fichiers
de `/etc`. Si un fichier runtime incompatible existe déjà, le script s’arrête pour
éviter un écrasement.

Après installation :

```bash
sudo systemctl daemon-reload
sudo systemctl restart axiorhub-mail-ui.service
sudo systemctl restart axiorhub-mail-desk-worker.service
sudo systemctl restart axiorhub-mail-agent.timer
sudo systemctl status axiorhub-mail-ui.service axiorhub-mail-desk-worker.service --no-pager
```

Redémarrer aussi votre surveillance et vos instances de workers supplémentaires
si elles étaient actives. La migration de schéma est numérotée et transactionnelle.
Les nouvelles tables complètent les données existantes ; aucun index complet n’est
reconstruit volontairement. La mise à jour sauvegarde la configuration et les bases
SQLite locales avant le changement de version.

Pour le diagnostic Open WebUI qui lit un jeton réservé à root, utiliser :

```bash
sudo python3 /opt/axiorhub-mail-agent/current/diagnostic-openwebui.py
```

Ne pas donner des droits publics au jeton pour faire fonctionner ce diagnostic.
Le retour de version se fait avec `sudo python3 upgrade.py --rollback`, depuis
ce paquet, après sauvegarde des travaux produits depuis la mise à niveau.

## 3. Nouvelle installation Docker avec vos services existants

Cette variante installe AxiorHub et ses producteurs. Elle utilise votre IMAP,
votre Nextcloud et votre Ollama existants ; elle ne crée pas ces services.

```bash
cp .env.example .env
chmod 600 .env
sudo install -d -m 0700 -o 10001 -g 10001 data
```

Éditer `.env` pour renseigner au minimum `AXIORHUB_PUBLIC_URL`, l’adresse Ollama
et votre fuseau horaire. Les identifiants IMAP/Nextcloud peuvent être saisis ensuite
dans l’assistant graphique. Remplacer les adresses `example.com` avant de tester.
Les valeurs d’exemple ne sont pas des connexions prêtes à l’emploi.

```bash
sudo docker compose --env-file .env up -d --build
sudo docker compose --env-file .env ps
sudo cat data/auth/bootstrap-token
```

Ouvrir `https://agent.votre-cabinet.fr/signup`, saisir ce **code d’installation
privé**, puis créer le premier administrateur. Le code est supprimé après la
création réussie. Les inscriptions publiques restent fermées ; les autres comptes
se créent depuis la gestion des comptes.

Le port interne est **8626**. `AXIORHUB_PORT` règle uniquement le port publié sur
l’hôte. `AXIORHUB_BIND_ADDRESS=127.0.0.1` évite une exposition directe du service.
Configurer un reverse proxy HTTPS avec `deploy/nginx-agent.example.conf` ou
`deploy/apache-agent.example.conf`, en adaptant domaine, certificats et port publié.
Le proxy doit transmettre `X-Forwarded-Proto: https` et ne pas retenir le flux SSE.

`AXIORHUB_TRUSTED_PROXY=auto-gateway` résout l’adresse exacte de la passerelle Docker
pour un proxy installé sur l’hôte. Pour un proxy dans un autre conteneur, renseigner
son adresse exacte. `*` est interdit. `auto-gateway` est refusé hors d’un conteneur.

Ne pas lancer l’application sur une adresse publique HTTP pour contourner HTTPS.
Pour un volume existant, conserver son contenu ; corriger ses droits uniquement
après sauvegarde, si le compte conteneur UID 10001 ne peut pas y écrire.

### Mise à jour d’un Docker existant

Sauvegarder le répertoire `data`, son coffre et votre `.env`. Remplacer le code de
l’application par ce paquet, en conservant les volumes et les paramètres privés,
puis exécuter `docker compose --env-file .env up -d --build`. Vérifier aussi
`AXIORHUB_TRUSTED_PROXY` et les ports si votre ancien `.env` ne les contient pas.
Ne pas exécuter `docker compose down -v` ni recopier un répertoire `data` vide sur
vos données.

## 4. Variante VPS complète

Pour un nouveau serveur Debian/Ubuntu avec DNS préparés :

```bash
sudo bash deploy/vps/install-vps.sh
```

Cette variante installe aussi Nextcloud, MariaDB, Redis, Roundcube, OnlyOffice,
Apache et Certbot ; Ollama est facultatif. Elle ne doit pas servir à remplacer
sans préparation une installation de ces services déjà en production. Le code
initial se lit dans `deploy/vps/data/axiorhub/auth/bootstrap-token`.

Le modèle Apache utilise le port publié du `.env`, pas obligatoirement 8626.
La construction effective des images et l’obtention des certificats doivent être
contrôlées sur le serveur : elles n’ont pas été exécutées dans l’environnement de
construction de cette archive.

## 5. Connexions et secrets dans l’interface

Ouvrir **Paramètres → Connexions et configuration** (`/parametres/connexions`).
Cette page est réservée à l’administrateur. Enregistrer les changements, puis
lancer les tests de connexion proposés.

| Connexion | Réglages et précautions |
|---|---|
| IMAP | Serveur, port TLS, identifiant, mot de passe, noms exacts des dossiers. `Drafts` et `Sent` doivent être distincts de `INBOX`. |
| Nextcloud | URL HTTPS, compte technique, mot de passe d’application, racines autorisées. Sélectionner explicitement les agendas et les listes de tâches. |
| Ollama | URL joignable depuis le conteneur et nom exact d’un modèle installé. `localhost` dans Docker désigne le conteneur. |
| Invoice Ninja | URL de base HTTPS et **API Token** du compte autorisé. Ce connecteur lit les factures ; il ne crée ni n’envoie de factures. Ne pas renseigner `APP_KEY`, clé de chiffrement interne d’Invoice Ninja. |
| SMTP | Serveur, port, STARTTLS/SSL, identifiant, mot de passe, adresse expéditrice. Utilisé notamment pour la réinitialisation des mots de passe. La remise effective du courriel doit être testée sur votre serveur. |
| Dictée | Activer le pont vocal local, renseigner son URL et son jeton si nécessaire. La reconnaissance vocale n’est pas fournie par Ollama. |
| Interfaces | URL réelle de Roundcube et d’Open WebUI, sans laisser les adresses d’exemple. |

Une case de secret vide conserve le secret existant. Un nouveau secret saisi ici
est chiffré dans le coffre local, avec fichier 0600 et clé privée locale. Les
anciens secrets texte restent compatibles ; ils ne sont pas tous rechiffrés
automatiquement. L’accès root ou la copie de l’intégralité du coffre permet de
les déchiffrer : le coffre ne constitue pas un HSM.

La sauvegarde graphique utilise une révision : si un autre administrateur a changé
les réglages, recharger le formulaire avant de renvoyer. Les traitements en cours
conservent leur instantané ; les nouveaux traitements reprennent les réglages.

Les paramètres des fournisseurs IA, fonctions, budgets et MCP restent dans leurs
écrans existants. Les variables Docker/.env sont inventoriées dans un **plan de
déploiement** sans secrets. Elles ne sont pas appliquées par exécution root depuis
le navigateur. Modifier seulement `.env` après le premier démarrage ne remplace
pas automatiquement toutes les connexions déjà enregistrées.

### IA externe et budget

Le local reste la voie par défaut. Un appel externe nécessite une politique
compatible, l’autorisation de la fonction et du fournisseur, un dossier non exclu,
des tarifs et des plafonds renseignés. Les mêmes contrôles s’appliquent au secours.
Un tarif absent ne signifie plus « gratuit » : l’appel est refusé jusqu’au réglage.
Les réservations simultanées comptent dans le budget. Après un timeout externe,
une réservation incertaine reste conservée ; elle n’est pas libérée en prétendant
que le fournisseur n’a rien facturé.

## 6. Travailler par mission

Le bouton robot ouvre un panneau repliable sur chaque page. Depuis un dossier,
le dossier courant et les documents sélectionnés sont repris automatiquement.
La page **Missions** présente les demandes persistantes et leurs résultats.

1. Décrire le résultat : « Prépare une note de plaidoirie à partir de ces conclusions ».
2. Joindre jusqu’à trois fichiers ou sélectionner les documents du dossier.
3. Choisir **Préparer automatiquement** ou **Proposer seulement**, puis démarrer.
4. Suivre l’activité et ouvrir le résultat ; suspendre ou relancer si nécessaire.

Une ambiguïté de dossier provoque une question ciblée. Les demandes et leurs
identifiants sont persistants : un double clic ne crée pas deux missions. La
préparation ne demande pas une validation de chaque étape interne. Elle ne donne
pas le droit d’envoyer, signer, déposer auprès d’un tiers ou établir une facture.

Une mission choisit actuellement un livrable principal : réponse, projet Word ou
brouillon lié à un courriel. Ce n’est pas encore un planificateur complet de missions
composées. L’échéance saisie donne du contexte ; elle ne crée pas seule un événement
CalDAV. Pour les routines automatiques, régler aussi les automatismes du cabinet.

Les dépôts Word de ce parcours sont préparés localement, déposés à une destination
stable et relus pour vérifier le SHA-256. En cas d’incertitude, le travail reste
reprenable sans écraser un fichier différent. Les brouillons IMAP utilisent les
contrôles de relecture existants. Une preuve technique n’atteste pas la justesse
juridique du texte : la relecture reste nécessaire.

## 7. Voix et comportement

**Paramètres → Voix et comportement** (`/parametres/assistant`) permet de choisir
un prénom, le ton, la longueur, le niveau d’initiative et un lexique de dictée.
Ces préférences ne changent pas les droits, les faits du dossier ni les règles
juridiques approuvées. Le profil est personnel dans la distribution avec comptes.

La dictée passe par le pont local, puis affiche le texte pour correction avant la
mission. Chaque enregistrement est limité à 60 secondes et 8 Mo. Les nombres,
dates et références ne sont pas remplacés automatiquement par le lexique.

La lecture du briefing et des réponses utilise **eSpeak-NG local**. Pause et arrêt
sont disponibles. Aucun secours vocal externe implicite n’est utilisé. Le briefing
est calculé depuis les données en cache, sans nouveau LLM, avec un mode discret
qui masque les intitulés sensibles. La qualité de voix et les permissions du micro
doivent être vérifiées sur votre navigateur. L’avatar reste une icône facultative.

## 8. Accueil téléphonique et WhatsApp — optionnel

Les fonctions restent désactivées tant que l’administrateur n’a pas approuvé le
périmètre administratif et renseigné les comptes officiels.

| Canal | Capacité livrée | Configuration |
|---|---|---|
| Twilio Voice | Annonce automatisée, choix par touches, tâche locale de rappel ou d’information administrative. Aucun accès à un dossier par simple numéro appelant. | Numéro Twilio, Auth Token, URL HTTPS exacte du webhook `/reception567/twilio` ; faire pointer le webhook entrant du fournisseur vers cette URL. |
| WhatsApp Business Cloud | Réception de textes entrants et création d’une tâche locale, avec déduplication. Aucun téléchargement de média ni réponse sortante automatique. | Application Meta et numéro Business éligible, Phone Number ID, App Secret et Verify Token ; webhook `/reception567/whatsapp`. |

Les signatures sont vérifiées sur le contenu d’origine. Une signature invalide
ou un mauvais numéro Business est refusé. Le consentement à recevoir un message
ne donne pas une autorisation d’action juridique. Les coûts du fournisseur ne sont
pas calculés par ce module. Aucun enregistrement audio d’appel n’est créé.
La rétention des messages entrants est limitée à 90 jours ; la rétention des tâches
de suivi est distincte. Une purge de message n’efface pas automatiquement la tâche.

Cette version n’implémente pas un avocat parlant au téléphone, les appels WhatsApp,
le transfert téléphonique généralisé ni les messages WhatsApp sortants. La recette
des comptes réels du fournisseur reste à effectuer.

## 9. Recette sur votre serveur

- Vérifier connexion, permissions et absence de secrets dans les exports.
- Créer une mission sur un dossier fictif ; vérifier progression, double clic,
  suspension et reprise après redémarrage.
- Déposer un Word de test puis le relire dans Nextcloud ; vérifier nom, contenu et preuve.
- Faire préparer un brouillon de test ; vérifier son UID et son contenu dans le
  vrai dossier IMAP, sans l’envoyer.
- Tester un long PDF connu, notamment sa dernière page, et un PDF scanné : une
  page illisible doit être signalée, pas comptée comme lue.
- Vérifier un dossier exclu des API externes et un budget dépassé.
- Lire le briefing puis le réécouter ; vérifier la dictée avec dates, noms et montants.
- Tester les webhooks seulement si les comptes et le périmètre sont configurés.

`/healthz` indique que l’interface locale répond ; `/readyz` exige notamment une
installation terminée et un worker récent. Aucun de ces voyants ne prouve à lui
seul qu’un brouillon existe dans IMAP ou qu’un document existe dans Nextcloud.

Pour le VPS complet, `sudo bash deploy/vps/backup.sh` réalise un snapshot avec
producteurs arrêtés et vérifie les bases SQLite extraites de l’archive. La
restauration complète de Nextcloud/MariaDB doit encore être répétée sur une
installation isolée. Les sauvegardes contiennent des données et des clés privées ;
ne pas les publier avec les archives du logiciel.
