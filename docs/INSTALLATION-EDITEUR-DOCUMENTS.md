# Serveur de documents EuroOffice ou OnlyOffice : installation pas à pas (5.6.108)

Ce guide installe le **serveur de documents** qui permet de **modifier** un fichier Word, Excel ou PowerPoint **dans AxiorHub**,
à plusieurs, avec l'enregistrement contrôlé (relecture du fichier enregistré, copie de sécurité en cas de conflit). Trois parcours :

- [A. Version serveur](#a-version-serveur-ubuntu-2404-avec-apache) (Ubuntu 24.04 avec Apache, comme [INSTALLATION-SERVEUR.md](INSTALLATION-SERVEUR.md)) ;
- [B. Poste Windows 11](#b-poste-windows-11) (Docker Desktop) ;
- [C. Poste Ubuntu 24.04](#c-poste-ubuntu-2404) (Docker Engine).

Puis : [réglage dans AxiorHub](#d-régler-axiorhub-et-tester), [mise à jour et arrêt](#e-mettre-à-jour-arrêter-désinstaller),
[en cas de problème](#f-en-cas-de-problème).

> **Sans serveur de documents, AxiorHub fonctionne.** Les documents Word s'affichent en **aperçu (lecture seule)** dans
> AxiorHub (documents des dossiers, livrables, lecteur) et, sur un poste, s'ouvrent dans **Word** (ou LibreOffice). Le serveur de documents sert seulement à
> **modifier dans AxiorHub**, à plusieurs, avec l'enregistrement contrôlé. Inutile de l'installer pour lire un .docx.

## Avant de commencer

| Élément | EuroOffice | OnlyOffice Docs Community |
|---|---|---|
| Image Docker | `ghcr.io/euro-office/documentserver` | `onlyoffice/documentserver` |
| Licence | AGPL-3.0 (dérivé d'OnlyOffice Docs) | AGPL-3.0 |
| Limite | — | nombre de connexions simultanées limité dans l'édition Community, selon la documentation de l'éditeur (vérifiez-la avant de choisir) |
| Essayé avec AxiorHub 5.6.108 | **oui** (Windows 11, Docker Desktop) | non : même interface publique ; commandes vérifiées avec EuroOffice |

**Épinglez une version** plutôt que `latest` : une mise à jour se fait alors quand vous le décidez. Pour EuroOffice, épinglez
l'**empreinte** de l'image (le registre ne publie pas toujours de numéro de version) ; celle essayée avec AxiorHub 5.6.108 :

```text
ghcr.io/euro-office/documentserver@sha256:2fadc1b3dc99250d6576fc0c0f34349db6725b752b08e91db9831fbb803fc5c2
```

Pour OnlyOffice, choisissez un numéro de version publié par l'éditeur et remplacez dans les commandes l'image EuroOffice par
`onlyoffice/documentserver:<version>`. Les réglages (`JWT_ENABLED`, `JWT_SECRET`, port 80 dans le conteneur) sont les mêmes.

**Le secret JWT.** C'est une longue chaîne aléatoire partagée par le serveur de documents et AxiorHub : chacun signe ses
demandes avec elle et refuse celles qui ne sont pas signées. Règles :

- générez-le vous-même (`openssl rand -hex 32` ou la commande PowerShell du parcours B) ; **ne réutilisez jamais un exemple** ;
- il est rangé dans un fichier lisible par vous seul, puis collé dans AxiorHub, qui ne le réaffiche jamais ;
- il ne doit apparaître dans **aucun journal, capture d'écran, courriel ou export**. En cas de doute, générez-en un nouveau
  (étapes « secret » et « lancer » à refaire), puis enregistrez-le dans AxiorHub.

**Mémoire et disque** (mesurés avec EuroOffice) : image de **7,2 Go** sur le disque ; le conteneur utilisait **2,5 Go de mémoire
vive** avec un document ouvert. Prévoyez au moins 8 Go de mémoire sur le poste (16 Go s'il fait aussi tourner un modèle d'IA local)
et 10 Go d'espace disque libre.

---

## A. Version serveur (Ubuntu 24.04 avec Apache)

Exemple : AxiorHub répond déjà sur `https://agent.example.test/agent-courriel` ; le serveur de documents aura son propre
sous-domaine `https://documents.example.test`. Remplacez ces noms par les vôtres.

**A1. Nom de domaine.** Chez votre hébergeur de domaine, créez un enregistrement `A` (et `AAAA` si vous avez IPv6) pour
`documents.example.test` vers l'adresse publique du serveur.
*Résultat attendu* : `getent hosts documents.example.test` affiche l'adresse du serveur.

**A2. Installer Docker.**

```bash
sudo apt update && sudo apt install -y docker.io
```

*Résultat attendu* : `sudo docker version` affiche une partie « Server ».

**A3. Créer le secret JWT** (fichier lisible par root seulement).

```bash
sudo install -d -m 700 /etc/axiorhub-documents && sudo sh -c 'umask 077; printf "JWT_ENABLED=true\nJWT_SECRET=%s\n" "$(openssl rand -hex 32)" > /etc/axiorhub-documents/documents.env'
```

*Résultat attendu* : `sudo ls -l /etc/axiorhub-documents/documents.env` montre `-rw-------  root root`.

**A4. Télécharger l'image épinglée** (une seule fois, environ 7 Go).

```bash
sudo docker pull ghcr.io/euro-office/documentserver@sha256:2fadc1b3dc99250d6576fc0c0f34349db6725b752b08e91db9831fbb803fc5c2
```

**A5. Lancer le serveur de documents**, joignable **seulement** depuis le serveur lui-même (127.0.0.1) ; Apache le publiera.

```bash
sudo docker run -d --name axiorhub-documents --restart unless-stopped -p 127.0.0.1:8080:80 --env-file /etc/axiorhub-documents/documents.env ghcr.io/euro-office/documentserver@sha256:2fadc1b3dc99250d6576fc0c0f34349db6725b752b08e91db9831fbb803fc5c2
```

*Résultat attendu* : après une à deux minutes, `curl -s http://127.0.0.1:8080/healthcheck` répond `true`.

**A6. Activer les modules Apache** (mandataire, WebSocket, en-têtes).

```bash
sudo a2enmod proxy proxy_http proxy_wstunnel headers ssl
```

**A7. Créer l'hôte virtuel** `/etc/apache2/sites-available/documents.example.test.conf` :

```apache
<VirtualHost *:80>
    ServerName documents.example.test
    ProxyPreserveHost On
    ProxyRequests Off
    RequestHeader set X-Forwarded-Proto expr=%{REQUEST_SCHEME}
    ProxyPass        / http://127.0.0.1:8080/ upgrade=websocket timeout=300
    ProxyPassReverse / http://127.0.0.1:8080/
</VirtualHost>
```

(`upgrade=websocket` fait passer la coédition en direct ; Apache 2.4.58 d'Ubuntu 24.04 le prend en charge.)

```bash
sudo a2ensite documents.example.test && sudo apache2ctl configtest && sudo systemctl reload apache2
```

*Résultat attendu* : `Syntax OK`, puis `curl -s http://documents.example.test/healthcheck` répond `true`.

**A8. Certificat HTTPS** (Certbot, comme pour AxiorHub).

```bash
sudo certbot --apache -d documents.example.test --redirect
```

*Résultat attendu* : `curl -s https://documents.example.test/healthcheck` répond `true`.

**A9. Copier le secret** pour le coller dans AxiorHub (il s'affiche dans votre terminal seulement ; ne le copiez nulle part ailleurs).

```bash
sudo sed -n 's/^JWT_SECRET=//p' /etc/axiorhub-documents/documents.env
```

**A10. Régler AxiorHub** : voir [D](#d-régler-axiorhub-et-tester), avec :

- adresse du serveur de documents : `https://documents.example.test` ;
- adresse de cette interface vue par le serveur de documents : **laissez vide** (le serveur de documents joint AxiorHub à son
  adresse publique `https://agent.example.test/agent-courriel`).

> **En cas de problème (serveur)**
> - `502 Proxy Error` : le conteneur démarre encore (attendez deux minutes) ou il est arrêté (`sudo docker ps -a`).
> - L'éditeur reste gris ou « se reconnecte » : la ligne `upgrade=websocket` manque, ou `proxy_wstunnel` n'est pas activé.
> - « Le serveur de documents joint AxiorHub » en échec : depuis le conteneur,
>   `sudo docker exec axiorhub-documents curl -s -o /dev/null -w '%{http_code}\n' https://agent.example.test/agent-courriel/office/file/essai-de-connexion-0000`
>   doit afficher `403` (AxiorHub répond et refuse ce faux jeton). `000` : le nom n'est pas joignable depuis le conteneur.
>   Si `agent.example.test` désigne une adresse privée sur votre réseau, ajoutez `-e ALLOW_PRIVATE_IP_ADDRESS=true` à la
>   commande A5 (après `docker stop` et `docker rm`, voir E).

---

## B. Poste Windows 11

Sur un poste, AxiorHub n'écoute que `127.0.0.1:8769`. Le serveur de documents tourne dans **Docker Desktop** et rejoint AxiorHub
par le nom spécial `host.docker.internal`, que la version 5.6.108 accepte pour les deux seules adresses du serveur de documents
(fichier et rappel, protégées par un jeton court et par le secret JWT).

**B1. Installer Docker Desktop** depuis le site de Docker, avec l'option **WSL 2** (proposée par défaut). Redémarrez si demandé.
Dans Docker Desktop › Settings › General, cochez **Start Docker Desktop when you sign in** : le serveur de documents ne démarre
qu'avec Docker Desktop.
*Résultat attendu* : dans PowerShell, `docker version` affiche une partie « Server ».

**B2. Ouvrir PowerShell** (menu Démarrer › PowerShell) et choisir le dossier du secret :

```powershell
$dossier = "$env:LOCALAPPDATA\AxiorHub Pilote\documents"; New-Item -ItemType Directory -Force $dossier | Out-Null
```

**B3. Créer le secret JWT** (64 caractères aléatoires, écrits dans `documents.env`, jamais affichés) :

```powershell
$o = New-Object byte[] 32; [Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($o); $s = -join ($o | ForEach-Object { $_.ToString('x2') }); [IO.File]::WriteAllText("$dossier\documents.env", "JWT_ENABLED=true`nJWT_SECRET=$s`n"); Remove-Variable s, o
```

*Résultat attendu* : aucune sortie ; le fichier `documents.env` existe dans le dossier.

**B4. Télécharger l'image épinglée** (une seule fois, environ 7 Go) :

```powershell
docker pull ghcr.io/euro-office/documentserver@sha256:2fadc1b3dc99250d6576fc0c0f34349db6725b752b08e91db9831fbb803fc5c2
```

**B5. Lancer le serveur de documents**, publié seulement sur `127.0.0.1:8080` :

```powershell
docker run -d --name axiorhub-documents --restart unless-stopped -p 127.0.0.1:8080:80 --env-file "$dossier\documents.env" ghcr.io/euro-office/documentserver@sha256:2fadc1b3dc99250d6576fc0c0f34349db6725b752b08e91db9831fbb803fc5c2
```

*Résultat attendu* : après une à deux minutes, `curl.exe -s http://127.0.0.1:8080/healthcheck` répond `true`.

**B6. Copier le secret dans le presse-papiers** (sans l'afficher) :

```powershell
((Get-Content "$dossier\documents.env") -match '^JWT_SECRET=') -replace '^JWT_SECRET=', '' | Set-Clipboard
```

**B7. Régler AxiorHub** : voir [D](#d-régler-axiorhub-et-tester), avec :

- adresse du serveur de documents : `http://127.0.0.1:8080` ;
- secret JWT : collez (Ctrl+V), puis videz le presse-papiers (copiez un autre texte) ;
- adresse de cette interface vue par le serveur de documents : `http://host.docker.internal:8769/agent-courriel`
  (8769 : port du poste ; s'il a été changé, mettez le vôtre).

> **En cas de problème (Windows)**
> - `docker` est inconnu ou « cannot connect » : Docker Desktop n'est pas lancé ; ouvrez-le et attendez « Engine running ».
> - `curl.exe` ne répond pas `true` au bout de trois minutes : `docker logs axiorhub-documents --tail 50`.
> - « Le serveur de documents joint AxiorHub » en échec : vérifiez l'adresse `http://host.docker.internal:8769/agent-courriel`
>   et le port d'AxiorHub (`poste.py --status`), puis :
>   `docker exec axiorhub-documents curl -s -o /dev/null -w "%{http_code}" http://host.docker.internal:8769/agent-courriel/office/file/essai-de-connexion-0000`
>   doit afficher `403`. Si le test signale que le serveur de documents « n'a pas pu télécharger », relancez son conteneur avec
>   `-e ALLOW_PRIVATE_IP_ADDRESS=true` (après `docker stop` et `docker rm`) ; ce n'était pas nécessaire avec EuroOffice lors de l'essai.

---

## C. Poste Ubuntu 24.04

Même principe qu'à Windows, avec **Docker Engine**. Sous Linux, `host.docker.internal` n'existe pas d'office : on le déclare
(`--add-host host.docker.internal:host-gateway`, adresse de la passerelle Docker). Comme AxiorHub n'écoute que `127.0.0.1`, un
**petit relais local** fourni par Ubuntu (`systemd-socket-proxyd`) transmet vers `127.0.0.1:8769` les seules connexions qui
arrivent par l'interface Docker. AxiorHub voit alors l'adresse source `127.0.0.1` et le nom `host.docker.internal`, qu'il accepte
pour les deux adresses du serveur de documents seulement.

Pourquoi pas `--network host` ? Avec cette option, le conteneur partage le réseau du poste : outre l'éditeur (port 80), ses services
internes (file de messages RabbitMQ, service de documents sur le port 8000) écouteraient sur toutes les interfaces du poste,
réseau local compris. Ce guide ne le recommande pas.

**C1. Installer Docker Engine** et vous ajouter au groupe `docker` (puis fermez et rouvrez la session).

```bash
sudo apt update && sudo apt install -y docker.io && sudo usermod -aG docker "$USER"
```

*Résultat attendu* : après reconnexion, `docker version` affiche une partie « Server ».

**C2. Créer le secret JWT** (fichier lisible par vous seul).

```bash
install -d -m 700 ~/.config/axiorhub-documents && (umask 077; printf 'JWT_ENABLED=true\nJWT_SECRET=%s\n' "$(openssl rand -hex 32)" > ~/.config/axiorhub-documents/documents.env)
```

*Résultat attendu* : `ls -l ~/.config/axiorhub-documents/documents.env` montre `-rw-------`.

**C3. Télécharger l'image épinglée** (une seule fois, environ 7 Go).

```bash
docker pull ghcr.io/euro-office/documentserver@sha256:2fadc1b3dc99250d6576fc0c0f34349db6725b752b08e91db9831fbb803fc5c2
```

**C4. Lancer le serveur de documents** (publié sur `127.0.0.1:8080`, nom `host.docker.internal` déclaré) :

```bash
docker run -d --name axiorhub-documents --restart unless-stopped -p 127.0.0.1:8080:80 --add-host host.docker.internal:host-gateway --env-file ~/.config/axiorhub-documents/documents.env ghcr.io/euro-office/documentserver@sha256:2fadc1b3dc99250d6576fc0c0f34349db6725b752b08e91db9831fbb803fc5c2
```

*Résultat attendu* : après une à deux minutes, `curl -s http://127.0.0.1:8080/healthcheck` répond `true`.

**C5. Relever l'adresse de la passerelle Docker** (elle sert au relais) :

```bash
docker network inspect bridge --format '{{(index .IPAM.Config 0).Gateway}}'
```

*Résultat attendu* : une adresse, notée ci-dessous `ADRESSE_PASSERELLE`.

**C6. Créer le relais** : deux fichiers systemd. Remplacez `ADRESSE_PASSERELLE` par l'adresse de C5 (et 8769 par votre port
s'il a été changé).

`/etc/systemd/system/axiorhub-relais-documents.socket` :

```ini
[Unit]
Description=Relais du serveur de documents (Docker) vers AxiorHub poste
After=docker.service

[Socket]
ListenStream=ADRESSE_PASSERELLE:8769
BindToDevice=docker0
FreeBind=true

[Install]
WantedBy=sockets.target
```

`/etc/systemd/system/axiorhub-relais-documents.service` :

```ini
[Unit]
Description=Relais du serveur de documents vers 127.0.0.1:8769
Requires=axiorhub-relais-documents.socket
After=axiorhub-relais-documents.socket

[Service]
ExecStart=/usr/lib/systemd/systemd-socket-proxyd 127.0.0.1:8769
DynamicUser=yes
NoNewPrivileges=yes
PrivateTmp=yes
```

(`BindToDevice=docker0` : seules les connexions venues des conteneurs sont relayées, jamais celles du réseau local.)

```bash
sudo systemctl daemon-reload && sudo systemctl enable --now axiorhub-relais-documents.socket
```

*Résultat attendu* (AxiorHub lancé) : `docker exec axiorhub-documents curl -s -o /dev/null -w '%{http_code}\n' http://host.docker.internal:8769/agent-courriel/office/file/essai-de-connexion-0000`
affiche `403` (AxiorHub répond et refuse ce faux jeton).

**C7. Copier le secret** pour le coller dans AxiorHub :

```bash
sed -n 's/^JWT_SECRET=//p' ~/.config/axiorhub-documents/documents.env
```

**C8. Régler AxiorHub** : voir [D](#d-régler-axiorhub-et-tester), avec les mêmes valeurs qu'à Windows :
`http://127.0.0.1:8080` et `http://host.docker.internal:8769/agent-courriel`.

> **En cas de problème (Ubuntu)**
> - `permission denied ... docker.sock` : la reconnexion après C1 n'a pas été faite.
> - C6 affiche `000` : `systemctl status axiorhub-relais-documents.socket` ; vérifiez l'adresse de C5 et que le conteneur a bien
>   été lancé avec `--add-host host.docker.internal:host-gateway`. `400` : AxiorHub refuse le nom ; il est antérieur à 5.6.108.
> - Le pare-feu (`ufw`) actif : autorisez l'interface Docker vers le relais : `sudo ufw allow in on docker0 to any port 8769 proto tcp`.
> - Si le test signale que le serveur de documents « n'a pas pu télécharger », relancez son conteneur avec
>   `-e ALLOW_PRIVATE_IP_ADDRESS=true` (après `docker stop` et `docker rm`).

---

## D. Régler AxiorHub et tester

1. Ouvrez AxiorHub › **Paramètres** › **Documents et procédure**, section **« 1 · Modifier les documents dans l'interface »**.
2. **Moteur** : Euro-Office ou ONLYOFFICE Document Server.
3. **Adresse du serveur de documents** : celle de votre parcours (A10, B7 ou C8).
4. **Secret JWT** : collez le secret. Le champ se vide à l'enregistrement et ne réaffiche jamais le secret.
5. **Adresse de cette interface vue par le serveur de documents** : celle de votre parcours (vide sur un serveur).
6. Cliquez **Enregistrer**, puis **Tester la connexion**. Le résultat s'affiche étape par étape :

| Étape | Ce qui est vérifié |
|---|---|
| Serveur joignable (healthcheck) | le serveur de documents répond |
| Bibliothèque api.js | le script de l'éditeur est joignable |
| Découverte des formats | réponse du serveur (404 accepté : WOPI désactivé, sans effet) |
| Intégration dans cette page | le serveur accepte d'être affiché dans AxiorHub |
| Secret JWT et API de commande | le serveur accepte le secret |
| Le serveur de documents joint AxiorHub | le serveur de documents télécharge un **fichier d'essai fixe** depuis AxiorHub (aucune donnée de dossier) |

7. Toutes les lignes doivent être cochées ✔. Ouvrez ensuite **Documents** › un dossier › **Modifier** sur un .docx : l'éditeur
   s'ouvre, l'état indique « Prêt » ; Ctrl+S enregistre (sinon l'enregistrement se fait à la fermeture).

Le bouton **Retirer le secret** efface le secret enregistré dans AxiorHub.

## E. Mettre à jour, arrêter, désinstaller

Fermez d'abord les documents ouverts (les modifications sont enregistrées à la fermeture). Les commandes sont les mêmes partout
(ajoutez `sudo` sur le serveur ; sur Ubuntu poste, gardez `--add-host host.docker.internal:host-gateway`).

- **Arrêter** : `docker stop axiorhub-documents` ; **redémarrer** : `docker start axiorhub-documents`.
- **Mettre à jour** : téléchargez la nouvelle version épinglée (`docker pull <image>@sha256:<nouvelle empreinte>`), puis
  `docker stop axiorhub-documents`, `docker rm axiorhub-documents`, relancez la commande de lancement (A5, B5 ou C4) avec la
  nouvelle référence, et refaites **Tester la connexion**. Le secret reste le même (fichier `documents.env`).
- **Désinstaller** : `docker stop axiorhub-documents`, `docker rm axiorhub-documents`, puis `docker image rm <image>` ; sur
  Ubuntu poste, `sudo systemctl disable --now axiorhub-relais-documents.socket` ; dans AxiorHub, videz l'adresse du serveur
  et cliquez **Retirer le secret**. Les documents restent lisibles en aperçu.

## F. En cas de problème

| Message dans AxiorHub | Cause probable | Que faire |
|---|---|---|
| Serveur joignable : connexion impossible | conteneur arrêté ou encore en démarrage | `docker ps -a`, attendre deux minutes, `docker logs axiorhub-documents --tail 50` |
| Secret JWT : « Le serveur refuse la signature » | secrets différents | recopier le secret (A9, B6, C7), enregistrer, retester |
| Le serveur de documents joint AxiorHub : « n'a pas pu télécharger » | adresse de rappel injoignable depuis le conteneur | vérifier l'adresse de rappel ; Ubuntu : relais C6 ; si besoin `ALLOW_PRIVATE_IP_ADDRESS=true` |
| L'éditeur affiche « Le jeton de sécurité du document n'était pas formé correctement » | secret différent entre AxiorHub et le conteneur | comme ci-dessus |
| Intégration dans cette page : frame-ancestors | le mandataire du serveur interdit l'affichage dans AxiorHub | autoriser l'adresse d'AxiorHub dans `frame-ancestors` |
| Adresse en HTTP alors que l'interface est en HTTPS | serveur AxiorHub en HTTPS, éditeur en HTTP | publier l'éditeur en HTTPS (parcours A) |

Le secret n'apparaît jamais dans un journal : n'envoyez pas le fichier `documents.env`, ni la sortie de A9, B6 ou C7, à un
support ou dans un ticket.

*Essai réel (5.6.108)* : EuroOffice (empreinte ci-dessus, version 9.3.7.37 annoncée par le serveur) dans Docker Desktop sous
Windows 11, AxiorHub poste de test, ouverture d'un .docx fictif, saisie, Ctrl+S, fichier modifié et rappel accepté ; avec un
mauvais secret, le test le signale et l'éditeur refuse le document. Commandes B3, B5 et B6 exécutées telles quelles, au dossier,
au nom et aux ports d'essai près (Windows PowerShell 5.1) ; relais de C6 vérifié isolément (`systemd-socket-proxyd` d'Ubuntu 24.04 dans un conteneur : AxiorHub voit
l'adresse source 127.0.0.1 et le nom `host.docker.internal`). Non essayés : OnlyOffice, un poste Ubuntu réel, un serveur réel
avec Apache et Certbot (commandes rédigées d'après la documentation de ces outils).
