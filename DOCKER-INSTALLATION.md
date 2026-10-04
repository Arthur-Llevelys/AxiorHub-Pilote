# AxiorHub Pilote autonome — Docker (AxiorHub Pilote seul)

Ce mode lance AxiorHub Pilote seul, à côté d'une messagerie et d'un Nextcloud existants. Pour installer tout le poste du
cabinet sur un serveur neuf (Nextcloud, Roundcube, OnlyOffice, Apache, Let's Encrypt), suivez
[docs/INSTALLATION-VPS.md](docs/INSTALLATION-VPS.md).

## Démarrage

```bash
cp .env.example .env
chmod 600 .env
editor .env                       # au minimum AXIORHUB_PUBLIC_URL et AXIORHUB_INTERNAL_API_TOKEN (openssl rand -hex 32)
docker compose build --pull
docker compose up -d
docker compose ps
curl -fsS http://127.0.0.1:8626/healthz
```

Le service écoute sur `127.0.0.1:8626`. Publiez-le derrière un frontal HTTPS (ci-dessous), puis :

1. ouvrez `https://agent.votre-cabinet.fr/signup` : **le premier compte créé est administrateur** ; les inscriptions
   publiques sont ensuite fermées et les autres comptes se créent dans **Comptes** (voir
   [docs/COMPTES-ET-ROLES.md](docs/COMPTES-ET-ROLES.md)) ;
2. l'**assistant d'installation** s'ouvre : cabinet, messagerie, Nextcloud, IA, mode de fonctionnement. Chaque
   connexion se teste avant d'être enregistrée ; les mots de passe vont dans `data/secrets` (droits 600).

La configuration générée dans `data/config.json` démarre avec les automatismes de production désactivés et l'IA en mode
local. Commencez en observation, puis activez **Production utile et mesurée** dans *Paramètres*. Une IA externe ne
s'active qu'après consentement et passe toujours par la pseudonymisation.

Pour activer les mises à jour distantes, copiez la clé publique Minisign du projet dans `data/update-minisign.pub`,
configurez `AXIORHUB_UPDATE_METADATA_URL`, puis choisissez le canal dans *Paramètres → Maintenance*. La clé privée de
signature ne doit jamais se trouver sur le serveur applicatif.

## HTTPS avec Apache et Let's Encrypt

```bash
sudo apt update
sudo apt install -y apache2 certbot python3-certbot-apache
sudo a2enmod proxy proxy_http headers rewrite ssl
sed 's/__DOMAIN__/agent.votre-cabinet.fr/g' deploy/vps/apache/axiorhub.conf | sudo tee /etc/apache2/sites-available/axiorhub.conf
sudo a2ensite axiorhub
sudo apachectl configtest && sudo systemctl reload apache2
sudo certbot --apache -d agent.votre-cabinet.fr --redirect --hsts
```

## HTTPS avec Nginx

```bash
sudo apt install -y nginx certbot python3-certbot-nginx
sudo cp deploy/nginx-agent.example.conf /etc/nginx/sites-available/axiorhub.conf
sudo sed -i 's/agent\.example\.com/agent.votre-cabinet.fr/g' /etc/nginx/sites-available/axiorhub.conf
sudo ln -s /etc/nginx/sites-available/axiorhub.conf /etc/nginx/sites-enabled/axiorhub.conf
sudo nginx -t
sudo certbot --nginx -d agent.votre-cabinet.fr
```

## Données

Sauvegardez `data/` chiffré : il contient la configuration, les secrets, les bases et les comptes. Ne publiez jamais
`.env`, `data/`, les bases SQLite ni les secrets.

Avant de publier une image, figez l'image de base par digest et produisez un SBOM de l'image construite. Le fichier
`SBOM.cdx.json` livré décrit les dépendances directes déclarées ; il ne remplace pas le scan des paquets transitifs.
