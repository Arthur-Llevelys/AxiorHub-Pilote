# Démo en ligne d'AxiorHub Pilote

Ce dossier installe une **démonstration publique** d'AxiorHub Pilote sur votre serveur, à une adresse comme
`https://demo.example.test` (remplacez partout `demo.example.test` par votre sous-domaine, par exemple celui de votre site).

Chaque visiteur qui clique sur « Commencer la démo » reçoit **sa propre instance**, neuve, remplie de données fictives
(« Cabinet DURAND Avocats », dossiers DURAND c. MARTIN, ZORVAL, KERVILAC, ORLANDEC, TJ Valbrune). Personne ne voit ce qu'un autre
visiteur a saisi. Le visiteur arrive directement dans le cabinet fictif, connecté sous le compte de démonstration (aucun mot de
passe). Chaque instance est effacée après 2 h sans activité ou 6 h au total, ou dès que le visiteur clique sur « Quitter » dans le
bandeau de la démo ou sur « Terminer et tout effacer » (page `/_demo/session`).

La démo **ne fait rien à l'extérieur** : ni courriel, ni Nextcloud, ni modèle d'IA réel. Le service n'a **aucun accès réseau** :
seul votre serveur web (Apache ou nginx) lui parle, par un fichier de socket local.

## Ce qu'il faut savoir avant d'installer

- Un serveur exposé coûte des ressources : par défaut, au plus 20 visiteurs en même temps, 6 Go de mémoire et deux cœurs pour
  toute la démo. Ces plafonds protègent les autres services de votre serveur ; ajustez-les à sa taille (section **Réglages**).
- La démo et votre AxiorHub de production sont **séparés** : autre compte système, autre dossier, autre service, aucune donnée commune.
  Il est tout de même préférable, si vous le pouvez, d'installer la démo sur un serveur distinct.
- Le code de la démo est celui d'AxiorHub ; une faille de l'application resterait enfermée dans le service de démonstration, sans
  réseau ni accès à vos fichiers, mais les instances des visiteurs tournent sous le même compte système : la séparation entre
  visiteurs repose sur le répartiteur (un cookie aléatoire mène à une seule instance).

## Prérequis

- Debian 12 ou Ubuntu 22.04 / 24.04, avec systemd (cgroup v2, présent par défaut sur ces versions).
- Python 3.10 ou plus récent ; les paquets `python3-waitress` et `python3-cryptography` (installés par le script s'ils manquent).
- Apache 2.4.7 ou plus récent (ou nginx) déjà en service, avec Certbot pour le certificat.
- Un sous-domaine que vous contrôlez (enregistrement DNS).
- L'archive de la version d'AxiorHub Pilote, décompressée sur le serveur.

## Installation (une commande)

Depuis le dossier de l'archive décompressée :

```sh
sudo bash deploy/demo/installer-demo.sh --domaine demo.example.test
```

Le script, que vous pouvez relancer sans risque :

1. vérifie le système et installe `python3-waitress` et `python3-cryptography` s'ils manquent ;
2. crée le compte système `axiorhub-demo` (sans connexion possible) et permet à `www-data` de joindre le socket de la démo ;
3. copie le code dans `/opt/axiorhub-demo/versions/<version>` (lecture seule) et fait pointer `/opt/axiorhub-demo/current` dessus ;
4. crée `/etc/axiorhub-demo/demo.json` (une seule fois, jamais écrasé) et vérifie les réglages ;
5. installe et démarre le service `axiorhub-demo` avec ses plafonds globaux (mémoire, processeur, nombre de processus).

Il ne touche **ni au DNS, ni au certificat, ni à Apache** : ce sont les trois étapes suivantes.

## DNS

Chez votre registraire, créez pour le sous-domaine un enregistrement `A` vers l'adresse IPv4 de votre serveur (et `AAAA` pour l'IPv6
s'il en a une). Attendez que `dig +short demo.example.test` renvoie bien l'adresse du serveur.

## Apache

```sh
sudo cp deploy/demo/apache-demo.example.conf /etc/apache2/sites-available/axiorhub-demo.conf
sudo sed -i 's/demo\.example\.test/VOTRE-SOUS-DOMAINE/g' /etc/apache2/sites-available/axiorhub-demo.conf
sudo a2enmod ssl proxy proxy_http headers
```

L'exemple publie **seulement** la démo sur ce sous-domaine (aucune page `server-status` ni d'administration) et tient un journal
d'accès **sans adresse IP ni chemin** (date, code de réponse, taille). Pour nginx, partez de `deploy/demo/nginx-demo.example.conf`.

## Certificat

Le plus simple, avec Certbot :

```sh
sudo certbot certonly --apache -d VOTRE-SOUS-DOMAINE
sudo a2ensite axiorhub-demo && sudo apache2ctl configtest && sudo systemctl reload apache2
```

Les chemins du certificat dans l'exemple (`/etc/letsencrypt/live/<sous-domaine>/`) sont ceux de Certbot ; le renouvellement est
automatique.

## Test fumée (après l'installation)

Depuis le serveur ou votre poste :

```sh
python3 /opt/axiorhub-demo/current/deploy/demo/test-fumee.py https://VOTRE-SOUS-DOMAINE
```

Il vérifie l'accueil et la mention vie privée, les en-têtes de sécurité, le refus des pages d'administration, le cookie de session
(`HttpOnly`, `Secure`, `SameSite=Lax`), la réponse d'une instance, puis termine cette instance et vérifie qu'elle est effacée. Il ne
crée qu'**une** instance ; avec `--sans-instance`, il n'en crée aucune. « Tout est bon. » et le code de sortie 0 : la démo est en place.

## Réglages

Fichier `/etc/axiorhub-demo/demo.json` (modèle : `deploy/demo/demo.example.json`). Après une modification :
`sudo bash deploy/demo/installer-demo.sh` (recalcule les plafonds globaux et redémarre ; les instances en cours sont effacées).
Une clé inconnue ou une valeur hors bornes **empêche le démarrage** et le message dit laquelle : rien n'est corrigé en silence.

| Réglage | Défaut | Rôle |
|---|---|---|
| `origine` | `https://demo.example.test` | Adresse publique de la démo (https, sans chemin). |
| `inactivite_minutes` | 120 | Effacement après ce délai sans activité. |
| `duree_max_minutes` | 360 | Effacement après cette durée, même en activité. |
| `instances_max` | 20 | Visiteurs simultanés ; au-delà : « Démo complète, réessayez dans quelques minutes ». |
| `creations_par_ip_heure` | 5 | Démarrages par adresse et par heure (adresse gardée en mémoire seulement, jamais écrite). |
| `memoire_mo` / `cpu_pourcent` / `processus_max` | 512 / 50 / 64 | Plafonds **par instance** (100 % = un cœur). |
| `memoire_totale_mo` / `cpu_total_pourcent` | 6144 / 200 | Plafonds de **toute** la démo. |
| `cpu_secondes` | 1800 | Temps de calcul total d'une instance. |
| `taille_max_mo` | 200 | Taille du dossier d'une instance ; au-delà, l'instance est arrêtée et effacée. |
| `corps_max_mo` | 10 | Taille maximale d'un envoi (pièce jointe, formulaire). |
| `delai_demarrage_secondes` | 90 | Une instance qui n'a pas démarré dans ce délai est effacée. |
| `journal_jours` | 7 | Conservation du journal technique. |
| `chemins_refuses` | `[]` | Adresses supplémentaires à refuser (par exemple `"/studio"`). |

Chaque réglage peut aussi être donné par une variable d'environnement `AXIORHUB_DEMO_<NOM EN MAJUSCULES>`.

## Mise à jour

Décompressez la nouvelle archive, puis depuis son dossier :

```sh
sudo bash deploy/demo/installer-demo.sh
```

Le nouveau code est installé à côté de l'ancien (`/opt/axiorhub-demo/versions/`), le lien `current` change et le service redémarre :
les démonstrations en cours sont effacées (les visiteurs retrouvent l'accueil). Les réglages sont conservés.

## Arrêt

```sh
sudo bash deploy/demo/installer-demo.sh --arreter     # arrête la démo ; toutes les instances sont effacées
sudo bash deploy/demo/installer-demo.sh --retirer     # retire le service, le code et les données (réglages gardés)
```

Pensez ensuite à désactiver l'hôte virtuel : `sudo a2dissite axiorhub-demo && sudo systemctl reload apache2`.

## Surveillance

- État du service : `systemctl status axiorhub-demo` ; erreurs : `journalctl -u axiorhub-demo -n 50`.
- Instances en cours et refus de la dernière heure :
  `cd /opt/axiorhub-demo/current && sudo -u axiorhub-demo python3 -m agent.demo_hote56107 etat`
  (fichier `/var/lib/axiorhub-demo/etat.json`, mis à jour toutes les 30 secondes).
- Journal technique : `/var/lib/axiorhub-demo/journal/demo-AAAA-MM-JJ.log` — une ligne par événement (création, fin, refus), sans
  adresse ni saisie, effacé après 7 jours.
- Mémoire et processeur de toute la démo : `systemctl status axiorhub-demo` (lignes `Memory` et `CPU`).
- Le test fumée peut être lancé régulièrement avec `--sans-instance` (aucune instance créée).

La démo n'a **aucune page d'administration** : tout se règle et se lit sur le serveur.

## Variante Docker

Si vous préférez Docker, tout tient dans un conteneur sans réseau (`network_mode: none`), en lecture seule, sans privilège, avec
les instances dans un espace mémoire borné effacé à l'arrêt. Depuis le dossier de l'archive :

```sh
sudo install -d -o 10001 -g www-data -m 0750 deploy/demo/socket
cp deploy/demo/demo.example.json deploy/demo/demo.json     # puis remplacer l'origine
docker compose -f deploy/demo/compose.yml up -d --build
```

Dans l'hôte virtuel Apache, remplacez le chemin du socket par `<dossier de l'archive>/deploy/demo/socket/demo.sock`.
Dans le conteneur, la mémoire et le temps de calcul de chaque instance sont bornés par ses propres limites de processus ; le
plafond global est celui du conteneur (`mem_limit`, `cpus`, à accorder avec `memoire_totale_mo` et `cpu_total_pourcent`).
Mise à jour : nouvelle archive puis la même commande `up -d --build` ; arrêt : `docker compose -f deploy/demo/compose.yml down` ;
surveillance : `docker exec axiorhub-demo python3 -m agent.demo_hote56107 etat`.

## Vie privée

Page d'accueil (mention reprise à l'écran) : données fictives seulement, aucun compte, aucune donnée conservée au-delà de la session.
Concrètement :

- un seul cookie technique, aléatoire, qui relie le navigateur à son instance ; le serveur n'en garde que l'empreinte ;
- l'adresse IP du visiteur ne sert qu'à limiter le nombre de démarrages, en mémoire et sous forme d'empreinte, jamais écrite ;
- ce que le visiteur saisit reste dans son instance et disparaît avec elle ; un redémarrage du service efface tout ;
- le journal de la démo ne contient ni adresse, ni chemin, ni saisie ; celui d'Apache, avec l'exemple fourni, non plus ;
- les moteurs de recherche sont priés de ne pas indexer la démo (`robots.txt`, `X-Robots-Tag`).

## Comment c'est construit (pour le prestataire)

- `agent/demo_hote56107.py` : le **répartiteur** (page d'accueil, cookie, plafonds, nettoyage, transmission vers l'instance, en-têtes
  de sécurité, refus des chemins d'administration). Il écoute sur `/run/axiorhub-demo/demo.sock`.
- `deploy/demo/instance.py` : une **instance** (un processus par visiteur) : entre dans son sous-groupe cgroup, pose ses limites,
  refuse toute prise réseau, crée le cabinet fictif par le mode Démo d'AxiorHub, refuse de servir si le mode Démo n'est pas actif,
  puis écoute sur le socket de son dossier.
- `deploy/demo/axiorhub-demo.service` : service systemd sans réseau (`PrivateNetwork=yes`, `RestrictAddressFamilies=AF_UNIX`,
  `IPAddressDeny=any`), sans privilège (`NoNewPrivileges`, aucune capacité), système en lecture seule sauf l'état et le socket,
  sous-groupes délégués pour borner chaque instance.
- `deploy/demo/compose.yml` et `Dockerfile` : la variante Docker.
