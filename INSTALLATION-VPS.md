# Installer AxiorHub Pilote sur un VPS (OVH ou autre)

Ce guide installe sur **un seul serveur** l'ensemble du poste numérique du cabinet :

| Service | Adresse (exemple) | Rôle |
|---|---|---|
| AxiorHub Pilote | `https://agent.votre-cabinet.fr` | assistant d'exécution |
| Nextcloud | `https://cloud.votre-cabinet.fr` | dossiers, agenda, tâches, Deck, contacts |
| Roundcube | `https://webmail.votre-cabinet.fr` | webmail (messagerie existante du cabinet) |
| OnlyOffice | `https://office.votre-cabinet.fr` | édition des documents Word dans Nextcloud |
| Ollama (option) | interne | IA locale |

Apache sert de frontal HTTPS ; les conteneurs n'écoutent que sur `127.0.0.1`. Les certificats sont délivrés et
renouvelés automatiquement par **Certbot (Let's Encrypt)**.

## 1. Prérequis

- VPS **Debian 12** ou **Ubuntu 22.04 / 24.04**, accès `root` ou `sudo`.
- Mémoire : 8 Go minimum sans IA locale (OnlyOffice en consomme 2 à 3 Go). Avec l'IA locale : 32 Go et idéalement une
  carte graphique ; sans carte graphique, préférez une IA externe pseudonymisée.
- Disque : 80 Go minimum, selon le volume des dossiers.
- Une messagerie existante (IMAP/SMTP) chez votre hébergeur. Le VPS n'héberge pas de serveur de messagerie.
- Quatre noms DNS (enregistrements **A**, et **AAAA** si IPv6) pointant vers l'adresse du VPS :
  `agent.`, `cloud.`, `webmail.`, `office.` + votre domaine. Chez OVH : *Web Cloud → Noms de domaine → Zone DNS*.
- Ports 80 et 443 ouverts (pare-feu OVH « Network Firewall » le cas échéant).

## 2. Installation automatique

```bash
sudo apt update && sudo apt install -y git
git clone https://github.com/Arthur-Llevelys/AxiorHub-Pilote.git /opt/axiorhub-src
cd /opt/axiorhub-src
sudo bash deploy/vps/install-vps.sh
```

Le script :

1. installe Docker (dépôt officiel), Apache, Certbot ;
2. crée `deploy/vps/.env` (droits 600) à partir de `env.example` en vous demandant les noms de domaine, l'adresse pour
   Let's Encrypt et les serveurs IMAP/SMTP ; **les mots de passe et secrets sont générés** (`openssl rand`) ;
3. vérifie que chaque nom DNS pointe vers le serveur ;
4. construit l'image AxiorHub Pilote et démarre les conteneurs ;
5. installe les hôtes virtuels Apache (`deploy/vps/apache/*.conf`, modules `proxy`, `proxy_http`, `proxy_wstunnel`,
   `headers`, `rewrite`, `ssl`) ;
6. **après votre accord explicite aux conditions de Let's Encrypt**, lance pour chaque nom :
   `certbot --apache -d <nom> --redirect --hsts` (HTTPS, redirection HTTP→HTTPS, renouvellement automatique) ;
7. règle Nextcloud : HTTPS derrière le proxy, tâches cron, applications Agenda, Tâches, Deck, Contacts, Notes, News,
   connecteur OnlyOffice (adresse et secret JWT).

Le script peut être relancé sans risque : un `.env` existant et ses secrets sont conservés.

## 3. Premier démarrage d'AxiorHub Pilote

1. Dans Nextcloud (`https://cloud.…`, compte `admin`, mot de passe dans `deploy/vps/.env`) :
   - créez les comptes des membres du cabinet ;
   - créez un compte technique **`axiorhub`**, partagez-lui le dossier des dossiers clients (par exemple `/Dossiers`) et
     les agendas utiles ; dans *Paramètres personnels → Sécurité*, créez un **mot de passe d'application**.
2. Ouvrez `https://agent.…/signup` et créez le **premier compte : il devient administrateur**. Les inscriptions publiques
   sont ensuite fermées ; les autres comptes se créent dans **Comptes**.
3. L'**assistant d'installation** s'ouvre :
   - **Cabinet** : nom de l'avocat, ville, adresse, téléphone, courriel, spécialité. Ces informations alimentent la
     signature et les projets de documents ;
   - **Messagerie** : serveur IMAP, identifiant, mot de passe (bouton *Tester*) ;
   - **Nextcloud** : adresse `https://cloud.…`, compte `axiorhub`, mot de passe d'application, dossier racine ; le test
     découvre les agendas ;
   - **IA** : locale (Ollama), mixte (locale + externe pour les tâches lourdes) ou externe ; une IA externe exige votre
     consentement et passe toujours par la pseudonymisation ;
   - **Mode** : *observation* (AxiorHub Pilote analyse sans rien écrire) puis *brouillons*.
4. Terminez : le **Poste de pilotage** s'affiche.

Commencez en **observation** quelques jours, contrôlez les rattachements et les analyses, puis passez en mode
**brouillons** et activez les automatismes dans *Paramètres → Automatismes*.

## 4. Vérifications

```bash
cd /opt/axiorhub-src/deploy/vps
sudo docker compose --env-file .env ps
curl -fsS http://127.0.0.1:8626/healthz
sudo apachectl -S                     # hôtes virtuels actifs
sudo certbot certificates             # certificats et dates d'expiration
sudo certbot renew --dry-run          # test du renouvellement automatique
```

## 5. Sauvegardes

```bash
sudo bash deploy/vps/backup.sh /var/backups/axiorhub
```

Le script sauvegarde la base Nextcloud, les fichiers Nextcloud, les données AxiorHub Pilote et Roundcube, et le `.env`. Il
conserve 14 jours. Planifiez-le chaque nuit :

```bash
echo '30 2 * * * root bash /opt/axiorhub-src/deploy/vps/backup.sh /var/backups/axiorhub' | sudo tee /etc/cron.d/axiorhub-backup
```

Les sauvegardes contiennent des données de clients : **chiffrez-les et copiez-les hors du serveur** (restic, borg, OVH
Backup Storage…). Une sauvegarde jamais restaurée n'est pas une sauvegarde : testez une restauration.

## 6. Mise à jour

```bash
cd /opt/axiorhub-src
git pull
cd deploy/vps
sudo docker compose --env-file .env up -d --build
```

Les données (`deploy/vps/data/`) et les réglages sont conservés ; les migrations s'appliquent au démarrage.

## 7. Configuration Apache et Certbot à la main

Si vous préférez ne pas utiliser le script, pour chaque service :

```bash
sudo a2enmod proxy proxy_http proxy_wstunnel headers rewrite ssl
sudo sed 's/__DOMAIN__/agent.votre-cabinet.fr/g' deploy/vps/apache/axiorhub.conf \
  | sudo tee /etc/apache2/sites-available/axiorhub.conf
sudo a2ensite axiorhub && sudo apachectl configtest && sudo systemctl reload apache2
sudo certbot --apache -d agent.votre-cabinet.fr --redirect --hsts -m admin@votre-cabinet.fr --agree-tos
```

Faites de même avec `nextcloud.conf`, `roundcube.conf`, `office.conf`. Certbot ajoute l'hôte virtuel `*:443`
(`…-le-ssl.conf`) et programme le renouvellement (`certbot.timer`).

| Modèle | Port local | Particularités |
|---|---|---|
| `axiorhub.conf` | 8626 | délai 600 s et flux sans tampon pour le suivi en direct |
| `nextcloud.conf` | 8080 | redirections `.well-known/caldav` et `carddav`, envois sans limite de taille |
| `roundcube.conf` | 8081 | pièces jointes jusqu'à 30 Mo |
| `office.conf` | 8082 | WebSocket pour l'édition à plusieurs |

## 8. Problèmes fréquents

| Symptôme | Cause probable |
|---|---|
| Certbot : *Timeout during connect* | DNS pas encore propagé, ou ports 80/443 fermés (pare-feu OVH) |
| Nextcloud : *Accès par un domaine non approuvé* | relancer `sudo bash deploy/vps/nextcloud-post-install.sh` |
| OnlyOffice : *document inaccessible* | secret JWT différent entre `.env` et Nextcloud : relancer le script post-installation |
| AxiorHub Pilote : la page d'installation revient | l'assistant n'a pas été terminé (bouton *Terminer*), ou la messagerie / Nextcloud utilisent encore des adresses d'exemple |
| L'IA locale est très lente | modèle trop gros pour le serveur : choisir un modèle plus petit ou le mode mixte |
