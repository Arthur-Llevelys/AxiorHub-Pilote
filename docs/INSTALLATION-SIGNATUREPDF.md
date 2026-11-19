# SignaturePDF : signature par lien, installation pas à pas (5.6.108)

[SignaturePDF](https://github.com/24eme/signaturepdf) est un logiciel libre de la société 24ème (licence **AGPL-3.0**, application web
en PHP) qui permet de signer un PDF dans le navigateur, seul ou **à plusieurs avec un lien partagé**. AxiorHub peut s'en servir comme
**service facultatif de la version serveur** : sur un PDF d'un dossier, le bouton **« Faire signer par lien »** dépose le document sur le
SignaturePDF du cabinet et donne le lien à adresser aux signataires ; le PDF signé revient ensuite dans le dossier, **à côté de l'original**.

SignaturePDF ne s'intègre pas dans les pages d'AxiorHub : il s'installe **à côté**, sur son propre sous-domaine HTTPS, comme le serveur de
documents ([INSTALLATION-EDITEUR-DOCUMENTS.md](INSTALLATION-EDITEUR-DOCUMENTS.md)). Les versions **poste** (Windows 11, Ubuntu 24.04)
s'en servent aussi : le cas type est un poste qui dépose sur l'instance HTTPS du serveur du cabinet (le lien donné au client pointe vers ce
serveur). Une instance installée sur le poste lui-même est possible ([H](#h-installation-locale-poste)), mais ses liens ne s'ouvrent que sur
cet ordinateur.

Sommaire : [avant de commencer](#avant-de-commencer) · [A. installer](#a-installer-sur-ubuntu-2404-avec-apache) ·
[B. stockage et purge](#b-stockage-des-pdf-et-purge) · [C. régler AxiorHub](#c-régler-axiorhub-et-tester) ·
[D. faire signer un PDF](#d-faire-signer-un-pdf) · [E. sauvegarde, mise à jour, arrêt](#e-sauvegarde-mise-à-jour-arrêt) ·
[F. en cas de problème](#f-en-cas-de-problème) · [G. licence, confidentialité, valeur juridique](#g-licence-confidentialité-valeur-juridique) ·
[H. installation locale (poste)](#h-installation-locale-poste)

## Avant de commencer

| Élément | Valeur |
|---|---|
| Logiciel | SignaturePDF, dépôt `https://github.com/24eme/signaturepdf`, branche `master` (branche stable selon l'éditeur) |
| Licence | AGPL-3.0 (voir [G](#g-licence-confidentialité-valeur-juridique)) |
| Serveur | Ubuntu 24.04 avec Apache, le serveur qui héberge déjà AxiorHub (et souvent Nextcloud) |
| Nom | un sous-domaine dédié, ici `signature.example.test` (remplacez-le par le vôtre) |
| Taille d'un PDF | 24 Mo au plus (réglage recommandé par l'éditeur) |
| Clé ou mot de passe pour AxiorHub | aucun : SignaturePDF n'en utilise pas ; son interface d'échange est protégée par Apache (étape A7) |

**Comment AxiorHub échange avec SignaturePDF.** L'éditeur ne documente pas d'interface programmable. Le code public contient toutefois des
adresses d'échange (`/api/share/new` pour déposer un PDF et créer le partage, `/api/share/get/…` pour relire le PDF signé,
`/signature/…/nblayers` pour compter les signatures, `/api/share/delete/…` pour supprimer un partage). AxiorHub ne s'en sert que si
« Tester la connexion » les trouve ; sinon, le parcours reste **manuel** : AxiorHub ouvre SignaturePDF dans un nouvel onglet, vous y
déposez le PDF, puis vous rangez le PDF signé avec « Déposer le PDF signé ». Ces adresses n'étant pas documentées, refaites le test après
chaque mise à jour de SignaturePDF (étape E2).

**Pourquoi pas la configuration Apache de la documentation officielle ?** Elle charge PHP dans Apache (`php_value` dans l'hôte virtuel,
module `mod_php`). Sur un serveur qui fait déjà tourner Nextcloud avec PHP-FPM, installer `mod_php` peut changer le mode de fonctionnement
d'Apache pour tous les sites. Ce guide utilise donc PHP-FPM avec un **groupe de processus dédié** (compte système `signaturepdf`, sans
droit sur Nextcloud ni sur AxiorHub) ; les réglages de taille sont les mêmes que ceux de l'éditeur. L'éditeur propose aussi une image Docker
(voir son `installation.md`) ; ce guide ne la décrit pas.

---

## A. Installer sur Ubuntu 24.04 avec Apache

**A1. Nom de domaine.** Chez votre hébergeur de domaine, créez un enregistrement `A` (et `AAAA` si vous avez IPv6) pour
`signature.example.test` vers l'adresse publique du serveur.
*Résultat attendu* : `getent hosts signature.example.test` affiche l'adresse du serveur.

**A2. Paquets** (liste de la documentation officielle, avec PHP-FPM et Git) :

```bash
sudo apt update && sudo apt install -y php-fpm php-cli librsvg2-bin pdftk imagemagick potrace ghostscript locales gpg git
```

*Résultat attendu* : `php -v` affiche PHP 8.3 ; `pdftk --version`, `rsvg-convert --version`, `potrace --version` et `gs --version`
répondent. (Sur Ubuntu 24.04, `pdftk` installe `pdftk-java`.)

**A3. Compte système dédié et dossiers** : le code appartient à root (personne ne peut le modifier depuis le web) ; seuls les PDF partagés
sont accessibles en écriture au compte `signaturepdf`.

```bash
sudo useradd --system --home-dir /var/lib/signaturepdf --shell /usr/sbin/nologin signaturepdf && sudo install -d -o signaturepdf -g signaturepdf -m 700 /var/lib/signaturepdf /var/lib/signaturepdf/partages
```

*Résultat attendu* : `sudo ls -ld /var/lib/signaturepdf/partages` montre `drwx------ signaturepdf signaturepdf`.

**A4. Récupérer le code** (branche `master`) :

```bash
sudo git clone https://github.com/24eme/signaturepdf.git /opt/signaturepdf && sudo git -C /opt/signaturepdf log -1 --format='%h %cd'
```

*Résultat attendu* : un identifiant de version (7 caractères) et une date. Notez-le : il sert à revenir en arrière (E2).

**A5. Configuration de SignaturePDF** (stockage des PDF partagés, chiffrement, lien de démonstration masqué) :

```bash
sudo tee /opt/signaturepdf/config/config.ini >/dev/null <<'FIN'
[globals]
PDF_STORAGE_PATH=/var/lib/signaturepdf/partages
PDF_STORAGE_ENCRYPTION=true
PDF_DEMO_LINK=false
FIN
sudo chmod 644 /opt/signaturepdf/config/config.ini
```

- `PDF_STORAGE_PATH` active la **signature à plusieurs** (partage par lien) : sans lui, SignaturePDF ne sait signer qu'un PDF à la fois,
  dans le navigateur, et AxiorHub ne peut pas s'en servir.
- `PDF_STORAGE_ENCRYPTION=true` chiffre les PDF stockés avec GPG (installé en A2) ; la clé figure dans le lien de signature, après le `#`.
- Les autres réglages (`DISABLE_ORGANIZATION`, durées de conservation proposées, signature numérique par certificat) sont décrits dans
  `config/config.ini.example` ; ils ne sont pas nécessaires à AxiorHub.

**A6. Groupe de processus PHP-FPM dédié**, avec les limites de taille de l'éditeur (24 Mo, 201 fichiers) :

```bash
sudo tee /etc/php/8.3/fpm/pool.d/signaturepdf.conf >/dev/null <<'FIN'
[signaturepdf]
user = signaturepdf
group = signaturepdf
listen = /run/php/signaturepdf.sock
listen.owner = www-data
listen.group = www-data
listen.mode = 0660
pm = ondemand
pm.max_children = 5
pm.process_idle_timeout = 60s
php_admin_value[upload_max_filesize] = 24M
php_admin_value[post_max_size] = 24M
php_admin_value[max_file_uploads] = 201
FIN
sudo php-fpm8.3 -t && sudo systemctl reload php8.3-fpm
```

*Résultat attendu* : `configuration file /etc/php/8.3/fpm/php-fpm.conf test is successful`, puis `ls -l /run/php/signaturepdf.sock`
montre le fichier.

**A7. Hôte virtuel Apache** `/etc/apache2/sites-available/signature.example.test.conf`. L'interface d'échange (`/api/`) et la purge
(`/cron`) ne répondent qu'au serveur lui-même (`Require local`) : depuis Internet, elles renvoient 403.

```apache
<VirtualHost *:80>
    ServerName signature.example.test
    DocumentRoot /opt/signaturepdf/public
    <Directory /opt/signaturepdf/public>
        Require all granted
        FallbackResource /index.php
    </Directory>
    <FilesMatch "\.php$">
        SetHandler "proxy:unix:/run/php/signaturepdf.sock|fcgi://signaturepdf"
    </FilesMatch>
    <LocationMatch "^/(api/|cron$)">
        Require local
    </LocationMatch>
</VirtualHost>
```

```bash
sudo a2enmod proxy_fcgi setenvif && sudo a2ensite signature.example.test && sudo apache2ctl configtest && sudo systemctl reload apache2
```

*Résultat attendu* : `Syntax OK`, puis `curl -s -o /dev/null -w '%{http_code}\n' http://signature.example.test/signature` affiche `200`.

**A8. Certificat HTTPS** (Certbot, comme pour AxiorHub) :

```bash
sudo certbot --apache -d signature.example.test --redirect
```

*Résultat attendu* : `curl -s -o /dev/null -w '%{http_code}\n' https://signature.example.test/signature` affiche `200`.

**A9. Contrôle de la protection** :

```bash
curl -s -o /dev/null -w '%{http_code}\n' https://signature.example.test/api/share/new
```

*Résultat attendu* : **depuis le serveur**, `405` (l'adresse existe et n'accepte que l'envoi d'un PDF) ; **depuis un autre ordinateur**,
`403`. Si le serveur lui-même obtient `403`, voir [F](#f-en-cas-de-problème).

---

## B. Stockage des PDF et purge

- **Durée de conservation.** Chaque PDF partagé est conservé pendant la durée choisie dans AxiorHub (*Paramètres › Connexions ›
  SignaturePDF* : une heure, un jour, une semaine, un mois, six mois ou un an ; un mois par défaut). SignaturePDF l'indique comme comptée
  après la dernière signature. Choisissez la plus courte qui laisse aux signataires le temps de signer : le PDF signé, lui, est rangé
  dans le dossier.
- **Purge.** SignaturePDF efface les partages arrivés à échéance quand son adresse `/cron` est appelée. Programmez cet appel toutes les
  heures, depuis le serveur (seule origine admise, A7) :

```bash
echo '17 * * * * nobody curl -fsS -o /dev/null https://signature.example.test/cron' | sudo tee /etc/cron.d/signaturepdf >/dev/null && sudo chmod 644 /etc/cron.d/signaturepdf
```

*Résultat attendu* : `sudo ls -l /etc/cron.d/signaturepdf` montre le fichier ; une heure plus tard, `sudo ls /var/lib/signaturepdf/partages`
ne contient plus les partages expirés.

- **Supprimer tout de suite.** Dans AxiorHub, « Clore la demande » avec « Supprimer aussi le PDF du service SignaturePDF » efface le
  partage (mode automatique). En parcours manuel, la page de partage de SignaturePDF propose sa propre suppression.
- **Signature à plusieurs.** Le lien est le même pour tous : chaque signataire l'ouvre, signe, et le suivant voit les signatures déjà
  apposées. Toute personne qui a le lien peut lire et signer le PDF : adressez-le seulement aux signataires.

---

## C. Régler AxiorHub et tester

1. Ouvrez AxiorHub › **Paramètres** › **Connexions**, section **« SignaturePDF (signature par lien) »** (administrateur).
2. **Adresse HTTPS du service** : `https://signature.example.test`.
3. **Conservation du PDF sur le service** : voir [B](#b-stockage-des-pdf-et-purge).
4. **Échange avec le service** : « Automatique si le service le permet » (par défaut) ou « Manuel ».
5. **Instance du cabinet** : cochez cette case seulement si l'instance est hébergée et administrée par le cabinet (celle de la partie A, ou
   l'instance locale de la partie H). AxiorHub ne le déduit jamais de l'adresse. Sans elle, l'instance est traitée comme un **tiers** :
   - chaque dépôt affiche « le PDF est déposé tel quel chez un tiers ; il ne peut pas être pseudonymisé » et demande une seconde confirmation ;
   - les documents d'un dossier réservé et les correspondances confidentielles entre avocats ne partent jamais ;
   - la politique des données s'applique comme pour un fournisseur externe (mode local strict : aucun dépôt ; registre des prestataires
     et lieux de traitement si le cabinet les exige).
6. Cliquez **Enregistrer**, puis **Tester la connexion**. Le test ne dépose rien ; il affiche :

| Étape | Ce qui est vérifié |
|---|---|
| Service joignable | la page `/signature` répond en HTTPS |
| Page de signature de SignaturePDF | la page est bien celle de SignaturePDF (sa version est affichée) |
| Signature à plusieurs activée | le partage par lien est proposé (`PDF_STORAGE_PATH`, A5) |
| Chiffrement des PDF stockés proposé | information (`PDF_STORAGE_ENCRYPTION`, A5) |
| Interface d'échange | information : présente, AxiorHub dépose et récupère lui-même ; absente, parcours manuel |

Le bouton « Faire signer par lien » n'apparaît qu'après un test réussi, et disparaît si l'adresse change. L'adresse est en HTTPS ; HTTP n'est
accepté que pour une instance locale sur l'adresse de bouclage (`http://127.0.0.1:…`, partie H).

**Sur un poste** (Windows 11, Ubuntu 24.04) : indiquez l'adresse HTTPS de l'instance du serveur du cabinet (partie A) et cochez « Instance
du cabinet ». Le poste y dépose le PDF ; le lien donné aux signataires pointe vers le serveur et s'ouvre de partout.

## D. Faire signer un PDF

1. Dans un dossier, onglet **Fichiers**, sur un PDF : **Faire signer par lien** (avocat ou administrateur, droit « envoyer »).
2. Lisez la page, cochez la confirmation, puis **Créer le lien de signature**. Rien ne part sans ce clic ; un dossier réservé reste
   réservé ; une correspondance confidentielle entre avocats est refusée.
3. **Copiez le lien** et adressez-le vous-même aux signataires (votre messagerie habituelle). AxiorHub ne l'envoie à personne.
4. La page indique le nombre de signatures apposées. Quand tous ont signé : **Récupérer le PDF signé**. Le fichier
   « … (signé le JJ-MM-AAAA).pdf » est rangé à côté de l'original, qui n'est pas modifié, puis relu ; il devient la nouvelle version du
   document dans le registre des documents quand l'original y figure.
5. **Parcours manuel** (interface d'échange absente ou choisie « Manuel ») : **Télécharger le PDF**, **Ouvrir SignaturePDF**, y déposer
   le PDF, « Partager pour signer à plusieurs », copier le lien et l'adresser ; une fois les signatures faites, télécharger le PDF signé
   depuis le lien et le ranger avec **Déposer le PDF signé**. Ce bouton sert aussi de secours en mode automatique.
6. **Clore la demande** quand elle est terminée (et, si vous le souhaitez, supprimer le PDF du service).

## E. Sauvegarde, mise à jour, arrêt

**E1. Sauvegarde.** Les PDF signés sont dans les dossiers du cabinet (déjà sauvegardés). Sur le serveur de signature, sauvegardez la
configuration : `/opt/signaturepdf/config/config.ini`, `/etc/php/8.3/fpm/pool.d/signaturepdf.conf`, les fichiers
`/etc/apache2/sites-available/signature.example.test*.conf` et `/etc/cron.d/signaturepdf`. Le dossier `/var/lib/signaturepdf/partages`
ne contient que des partages temporaires (chiffrés si A5) : sa sauvegarde est facultative et, si vous la faites, elle doit suivre la même
durée de conservation.

**E2. Mettre à jour** (commande de l'éditeur : `git pull -r` sur `master`) :

```bash
sudo git -C /opt/signaturepdf pull -r && sudo git -C /opt/signaturepdf log -1 --format='%h %cd' && sudo systemctl reload php8.3-fpm
```

*Résultat attendu* : la nouvelle version s'affiche. Refaites ensuite **Tester la connexion** dans AxiorHub. En cas de régression, revenez à
la version notée en A4 : `sudo git -C /opt/signaturepdf checkout <version>` (puis `git checkout master` pour reprendre les mises à jour).

**E3. Arrêter** (le site ne répond plus ; les fichiers restent) :

```bash
sudo a2dissite signature.example.test signature.example.test-le-ssl && sudo systemctl reload apache2
```

Pour redémarrer : `sudo a2ensite signature.example.test signature.example.test-le-ssl && sudo systemctl reload apache2`.

**E4. Désinstaller** : arrêtez (E3), puis supprimez `/etc/cron.d/signaturepdf`, `/etc/php/8.3/fpm/pool.d/signaturepdf.conf`
(`sudo systemctl reload php8.3-fpm`), `/opt/signaturepdf` et `/var/lib/signaturepdf`, et le compte (`sudo userdel signaturepdf`). Dans
AxiorHub, videz l'adresse du service : le bouton disparaît, les PDF déjà rangés restent dans les dossiers.

## F. En cas de problème

> **En cas de problème**
> - `502` ou `503` sur la page : le groupe PHP-FPM n'est pas démarré (`sudo systemctl status php8.3-fpm`, `ls -l /run/php/signaturepdf.sock`).
> - La page s'affiche en anglais : installez les langues (`sudo dpkg-reconfigure locales`, cochez `fr_FR.UTF-8`), puis
>   `sudo systemctl reload php8.3-fpm` (conseil de l'éditeur).
> - Test « Signature à plusieurs » en échec : `PDF_STORAGE_PATH` absent ou dossier non accessible au compte `signaturepdf` (A3, A5).
> - Test « Service joignable » en échec : nom, certificat (A8) ou site arrêté (E3) ; depuis le serveur :
>   `curl -sI https://signature.example.test/signature`.
> - Interface d'échange « absente » alors que A9 répond `405` depuis le serveur : AxiorHub est sur un autre serveur ; ajoutez son adresse
>   à la règle de A7 (`Require ip <adresse du serveur AxiorHub>` sous `Require local`), ou choisissez le parcours manuel.
> - A9 répond `403` depuis le serveur lui-même (serveur derrière une traduction d'adresse) : ajoutez `127.0.0.1 signature.example.test`
>   dans `/etc/hosts` du serveur, puis refaites A9.
> - Dépôt refusé « PDF trop volumineux » : 24 Mo au plus (A6). Dépôt refusé par le service (erreur 500) : droits du dossier de A3.
> - « Réponse du service perdue » dans AxiorHub : le PDF a peut-être été déposé ; il s'effacera à la fin de sa durée de conservation. Créez
>   une nouvelle demande ou passez par le parcours manuel.
> - Journaux : `sudo tail -n 50 /var/log/apache2/error.log` et `sudo journalctl -u php8.3-fpm -n 50`.

## G. Licence, confidentialité, valeur juridique

**Licence AGPL-3.0.** Utiliser SignaturePDF sans le modifier n'impose rien de plus que de laisser intactes ses mentions de licence (le
pied de page renvoie au code source). Si vous le **modifiez** et que des personnes l'utilisent par le réseau (vos signataires, par
exemple), l'AGPL-3.0 vous oblige à leur proposer le code source de **votre version modifiée**, sous la même licence. AxiorHub n'inclut
aucun code de SignaturePDF : il l'appelle par ses adresses web.

**Confidentialité.**
- Les PDF déposés restent **sur le serveur du cabinet** ; AxiorHub ne les envoie à aucun prestataire extérieur.
- Ils y sont conservés pendant la durée choisie (B), chiffrés si A5 est appliqué, puis effacés par la purge (B).
- **Le lien donne accès au document** : quiconque le détient peut le lire et le signer. Adressez-le seulement aux signataires, par un
  canal que vous maîtrisez. Dans AxiorHub, il n'est affiché qu'aux comptes qui voient le dossier ; ses clés sont rangées dans le coffre
  chiffré du serveur, jamais dans les journaux ni dans les exports.
- L'interface d'échange et la purge ne répondent qu'au serveur (A7). Les pages de signature restent publiques : c'est nécessaire pour les
  signataires. Toute personne qui connaît l'adresse peut aussi utiliser SignaturePDF pour ses propres PDF ; si vous voulez l'empêcher,
  protégez par mot de passe Apache les pages de dépôt (`/`, `/signature`, `/share`, `/sign`, `/organization`, `/compress`, `/metadata`) en
  laissant publiques les adresses `/signature/<identifiant>…` et `/image2svg` utilisées par les signataires (réglage non essayé).

**Valeur juridique (information, pas un avis).** SignaturePDF appose une **signature visible** (image ou tracé) sur le PDF. C'est en
principe une **signature électronique simple** au sens du règlement (UE) n° 910/2014 « eIDAS » : elle ne peut pas être écartée en justice
au seul motif qu'elle est électronique, mais elle ne bénéficie pas de la présomption de fiabilité de l'article 1367 du code civil, réservée
à la signature électronique qualifiée (décret n° 2017-1416). En cas de contestation, il faudra établir l'identité du signataire et
l'intégrité du document par d'autres éléments. Les prestataires de signature comme **Yousign (Youtrust)** ou **DocuSign** (connecteurs
déjà présents dans *Paramètres › Connexions*) proposent des signatures avancées ou qualifiées avec vérification d'identité, certificat et
piste d'audit. Le choix dépend de l'acte, de son enjeu et des exigences de forme qui lui sont propres : appréciez-le au cas par cas.

## H. Installation locale (poste)

> **Mise en garde.** Une instance installée sur un poste n'écoute que sur `127.0.0.1` : **ses liens ne s'ouvrent que sur cet ordinateur**.
> L'exposer au réseau du bureau est déconseillé (pas de HTTPS, pages de dépôt ouvertes à tous les appareils du réseau). **Pour faire signer
> un client à distance, utilisez l'instance du serveur du cabinet** (parties A à C), y compris depuis un poste. L'instance locale ne sert
> qu'à signer sur place (le client est devant l'écran) ou à découvrir SignaturePDF.

Dans AxiorHub, l'adresse d'une instance locale est en HTTP sur l'adresse de bouclage (`http://127.0.0.1:8090`) ; le connecteur l'accepte
et rappelle, sur la page de chaque PDF, que le lien n'est utilisable que sur ce poste. Cochez « Instance du cabinet » si ce poste
appartient au cabinet (case jamais déduite).

### H1. Ubuntu 24.04 (poste)

**H1-1. Paquets** (liste de la documentation officielle ; `php-cli` suffit au serveur PHP intégré, sans installer Apache) :

```bash
sudo apt update && sudo apt install -y php-cli librsvg2-bin pdftk imagemagick potrace ghostscript locales gpg git curl
```

*Résultat attendu* : `php -v` affiche PHP 8.3.

**H1-2. Code et stockage** (dans votre dossier personnel) :

```bash
git clone https://github.com/24eme/signaturepdf.git ~/signaturepdf && install -d -m 700 ~/.local/share/signaturepdf/partages && printf '[globals]\nPDF_STORAGE_PATH=%s\nPDF_STORAGE_ENCRYPTION=true\nPDF_DEMO_LINK=false\n' "$HOME/.local/share/signaturepdf/partages" > ~/signaturepdf/config/config.ini
```

*Résultat attendu* : `cat ~/signaturepdf/config/config.ini` montre le chemin complet du dossier de stockage.

**H1-3. Service local** (serveur PHP intégré, celui que la documentation officielle propose pour un essai, sur `127.0.0.1` seulement ;
il traite une demande à la fois, ce qui suffit pour un poste). Fichier `~/.config/systemd/user/signaturepdf.service` :

```ini
[Unit]
Description=SignaturePDF local (127.0.0.1:8090)

[Service]
WorkingDirectory=%h/signaturepdf
ExecStart=/usr/bin/php -d upload_max_filesize=24M -d post_max_size=24M -d max_file_uploads=201 -S 127.0.0.1:8090 -t public
Restart=on-failure

[Install]
WantedBy=default.target
```

```bash
systemctl --user daemon-reload && systemctl --user enable --now signaturepdf
```

*Résultat attendu* : `curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8090/signature` affiche `200`.

**H1-4. Purge horaire** :

```bash
(crontab -l 2>/dev/null; echo '17 * * * * curl -fsS -o /dev/null http://127.0.0.1:8090/cron') | crontab -
```

**H1-5. AxiorHub** : adresse `http://127.0.0.1:8090`, puis **Tester la connexion** (partie C).
Arrêter : `systemctl --user disable --now signaturepdf`. Mettre à jour : `git -C ~/signaturepdf pull -r && systemctl --user restart signaturepdf`.

### H2. Windows 11 (poste)

SignaturePDF ne fonctionne pas directement sous Windows (outils Linux : pdftk, librsvg, potrace, Ghostscript). Le projet publie un
**`Dockerfile` officiel** dans son dépôt (image de base `php:8.2-apache`) : l'installation locale passe donc par **Docker Desktop**, comme le
serveur de documents (partie B de [INSTALLATION-EDITEUR-DOCUMENTS.md](INSTALLATION-EDITEUR-DOCUMENTS.md)). La documentation de l'éditeur
cite aussi une image toute prête, `xgaia/signaturepdf`, sans dire qui la publie : ce guide construit l'image depuis le dépôt officiel.

**H2-1. Docker Desktop** installé et démarré (*Résultat attendu* : `docker version` affiche une partie « Server » dans PowerShell).

**H2-2. Construire l'image** depuis le dépôt officiel (branche `master`) :

```powershell
docker build -t signaturepdf https://github.com/24eme/signaturepdf.git#master
```

*Résultat attendu* : `docker image ls signaturepdf` montre l'image.

**H2-3. Lancer**, publié seulement sur `127.0.0.1:8090`, avec un volume pour les PDF partagés (variables documentées par l'éditeur) :

```powershell
docker run -d --name signaturepdf --restart unless-stopped -p 127.0.0.1:8090:80 -e SERVERNAME=127.0.0.1 -e PDF_STORAGE_PATH=/data -e PDF_STORAGE_ENCRYPTION=true -e PDF_DEMO_LINK=false -v signaturepdf-donnees:/data signaturepdf
```

*Résultat attendu* : `curl.exe -s -o NUL -w "%{http_code}" http://127.0.0.1:8090/signature` affiche `200`.

**H2-4. Purge horaire** (tâche planifiée de Windows) :

```powershell
schtasks /Create /SC HOURLY /TN "SignaturePDF purge" /TR "curl.exe -fsS -o NUL http://127.0.0.1:8090/cron"
```

**H2-5. AxiorHub** : adresse `http://127.0.0.1:8090`, puis **Tester la connexion**. Si « Signature à plusieurs » est en échec, le dossier
`/data` du conteneur n'est pas accessible en écriture : `docker logs signaturepdf --tail 50`.
Arrêter : `docker stop signaturepdf` ; mettre à jour : refaire H2-2, puis `docker stop signaturepdf`, `docker rm signaturepdf` et H2-3.

---

*Essais (5.6.108)* : le connecteur et les deux parcours (automatique et manuel) ont été essayés contre un **faux service local** qui
reproduit les adresses publiques de SignaturePDF (tests automatiques du lot). Les pages publiques d'une instance réelle (version `9e2811a`)
ont été lues sans rien déposer : page de signature, partage activé, chiffrement proposé, interface d'échange présente (`405`). Le dépôt
d'un PDF sur une instance réelle, une signature réelle dans un navigateur, les commandes A2 à A9, B et E (Apache, PHP-FPM, Certbot,
cron) et l'installation locale H (Ubuntu poste, Docker Desktop sous Windows 11) n'ont **pas été essayés** : ils sont rédigés d'après la
documentation de ces outils et le `Dockerfile` publié par l'éditeur. Signalez toute différence constatée.
