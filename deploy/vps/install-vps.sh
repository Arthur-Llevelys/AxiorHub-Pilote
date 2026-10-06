#!/usr/bin/env bash
# AxiorHub 5.6.9 — installation complète sur un VPS Debian 12 / Ubuntu 22.04-24.04 (OVH ou autre).
#
#   sudo bash deploy/vps/install-vps.sh
#
# Étapes : paquets (Docker, Apache, Certbot) → fichier .env avec secrets générés → conteneurs → hôtes virtuels Apache →
# certificats Let's Encrypt (avec votre accord) → réglages Nextcloud (OnlyOffice, Agenda, Tâches, Deck…) → récapitulatif.
# Le script peut être relancé : il ne régénère pas un secret existant et ne remplace pas un .env déjà rempli.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="$HERE/.env"
COMPOSE=(docker compose -f "$HERE/docker-compose.yml" --env-file "$ENV_FILE")

say() { printf '\n\033[1m%s\033[0m\n' "$*"; }
die() { printf '\nErreur : %s\n' "$*" >&2; exit 1; }
ask() { local q="$1" def="${2:-}" ans; read -r -p "$q${def:+ [$def]} : " ans; echo "${ans:-$def}"; }
secret() { openssl rand -base64 36 | tr -d '/+=' | cut -c1-40; }

[ "$(id -u)" -eq 0 ] || die "lancez ce script avec sudo."
. /etc/os-release
case "$ID" in debian|ubuntu) ;; *) die "système non pris en charge ($ID) : Debian 12 ou Ubuntu 22.04/24.04 attendu." ;; esac

say "1/7 · Paquets système"
apt-get update -qq
apt-get install -y -qq ca-certificates curl gnupg openssl apache2 certbot python3-certbot-apache dnsutils >/dev/null
if ! command -v docker >/dev/null 2>&1; then
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL "https://download.docker.com/linux/$ID/gpg" -o /etc/apt/keyrings/docker.asc
  chmod a+r /etc/apt/keyrings/docker.asc
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/$ID $VERSION_CODENAME stable" \
    > /etc/apt/sources.list.d/docker.list
  apt-get update -qq
  apt-get install -y -qq docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin >/dev/null
fi
systemctl enable --now docker apache2 >/dev/null

say "2/7 · Réglages (.env)"
if [ ! -f "$ENV_FILE" ]; then
  cp "$HERE/env.example" "$ENV_FILE"
  BASE=$(ask "Nom de domaine du cabinet (ex. votre-cabinet.fr)")
  [ -n "$BASE" ] || die "nom de domaine requis."
  AGENT=$(ask "Adresse d'AxiorHub" "agent.$BASE"); CLOUD=$(ask "Adresse de Nextcloud" "cloud.$BASE")
  MAIL=$(ask "Adresse du webmail" "webmail.$BASE"); OFFICE=$(ask "Adresse du serveur de documents" "office.$BASE")
  EMAIL=$(ask "Courriel pour Let's Encrypt (avis d'expiration)")
  IMAP=$(ask "Serveur IMAP de la messagerie du cabinet"); SMTP=$(ask "Serveur SMTP de la messagerie du cabinet")
  sed -i \
    -e "s|^DOMAIN_AGENT=.*|DOMAIN_AGENT=$AGENT|" -e "s|^DOMAIN_CLOUD=.*|DOMAIN_CLOUD=$CLOUD|" \
    -e "s|^DOMAIN_MAIL=.*|DOMAIN_MAIL=$MAIL|" -e "s|^DOMAIN_OFFICE=.*|DOMAIN_OFFICE=$OFFICE|" \
    -e "s|^LETSENCRYPT_EMAIL=.*|LETSENCRYPT_EMAIL=$EMAIL|" -e "s|^IMAP_HOST=.*|IMAP_HOST=$IMAP|" -e "s|^SMTP_HOST=.*|SMTP_HOST=$SMTP|" \
    -e "s|^AXIORHUB_PUBLIC_URL=.*|AXIORHUB_PUBLIC_URL=https://$AGENT|" -e "s|^AXIORHUB_ROUNDCUBE_URL=.*|AXIORHUB_ROUNDCUBE_URL=https://$MAIL|" \
    "$ENV_FILE"
  if [ "$(ask "Activer l'IA locale Ollama sur ce serveur ? (o/N ; nécessite beaucoup de mémoire)" "n")" = "o" ]; then
    sed -i "s|^COMPOSE_PROFILES=.*|COMPOSE_PROFILES=ollama|" "$ENV_FILE"
  fi
fi
for key in NEXTCLOUD_ADMIN_PASSWORD MYSQL_PASSWORD MYSQL_ROOT_PASSWORD ONLYOFFICE_JWT_SECRET AXIORHUB_INTERNAL_API_TOKEN; do
  if grep -q "^$key=a-generer$" "$ENV_FILE"; then sed -i "s|^$key=a-generer$|$key=$(secret)|" "$ENV_FILE"; fi
done
chmod 600 "$ENV_FILE"
set -a; . "$ENV_FILE"; set +a

say "3/7 · Contrôle DNS"
PUBLIC_IP=$(curl -fsS https://api.ipify.org || true)
for d in "$DOMAIN_AGENT" "$DOMAIN_CLOUD" "$DOMAIN_MAIL" "$DOMAIN_OFFICE"; do
  RESOLVED=$(dig +short A "$d" | tail -n1)
  if [ -n "$PUBLIC_IP" ] && [ "$RESOLVED" != "$PUBLIC_IP" ]; then
    echo "  Attention : $d pointe vers « ${RESOLVED:-rien} » et non vers ce serveur ($PUBLIC_IP). Les certificats échoueront tant que le DNS n'est pas corrigé."
  else
    echo "  $d → ${RESOLVED:-?} : OK"
  fi
done

say "4/7 · Conteneurs (construction d'AxiorHub, téléchargement des images)"
mkdir -p "$HERE/data/axiorhub" "$HERE/data/nextcloud" "$HERE/data/mariadb" "$HERE/data/roundcube" "$HERE/data/onlyoffice/data" "$HERE/data/onlyoffice/logs" "$HERE/data/ollama"
chown -R 10001:10001 "$HERE/data/axiorhub"
chmod 700 "$HERE/data"
"${COMPOSE[@]}" up -d --build

say "5/7 · Apache (hôtes virtuels)"
a2enmod -q proxy proxy_http proxy_wstunnel headers rewrite ssl >/dev/null
install_site() {
  local tpl="$1" domain="$2" name="$3"
  [[ "$domain" =~ ^[A-Za-z0-9.-]+$ ]] || die "nom de domaine invalide."
  [[ "$AXIORHUB_PORT" =~ ^[0-9]+$ ]] && ((AXIORHUB_PORT>=1 && AXIORHUB_PORT<=65535)) || die "port AxiorHub invalide."
  sed -e "s|__DOMAIN__|$domain|g" -e "s|__PORT__|$AXIORHUB_PORT|g" "$HERE/apache/$tpl" > "/etc/apache2/sites-available/$name.conf"
  a2ensite -q "$name" >/dev/null
}
install_site axiorhub.conf "$DOMAIN_AGENT" axiorhub
install_site nextcloud.conf "$DOMAIN_CLOUD" axiorhub-nextcloud
install_site roundcube.conf "$DOMAIN_MAIL" axiorhub-webmail
install_site office.conf "$DOMAIN_OFFICE" axiorhub-office
apachectl configtest
systemctl reload apache2
if command -v ufw >/dev/null 2>&1 && ufw status | grep -q "Status: active"; then ufw allow 'Apache Full' >/dev/null; ufw allow OpenSSH >/dev/null; fi

say "6/7 · Certificats HTTPS (Let's Encrypt)"
echo "Certbot va demander des certificats pour : $DOMAIN_AGENT, $DOMAIN_CLOUD, $DOMAIN_MAIL, $DOMAIN_OFFICE."
echo "Cela implique d'accepter les conditions d'utilisation de Let's Encrypt : https://letsencrypt.org/repository/"
if [ "$(ask "Acceptez-vous ces conditions et lancez-vous la demande ? (o/N)" "n")" = "o" ]; then
  for d in "$DOMAIN_AGENT" "$DOMAIN_CLOUD" "$DOMAIN_MAIL" "$DOMAIN_OFFICE"; do
    certbot --apache -d "$d" --redirect --hsts --non-interactive --agree-tos -m "$LETSENCRYPT_EMAIL" || echo "  Certificat non obtenu pour $d : vérifiez le DNS puis relancez : certbot --apache -d $d --redirect --hsts"
  done
  systemctl reload apache2
  echo "Renouvellement automatique : $(systemctl is-enabled certbot.timer 2>/dev/null || echo 'cron de certbot')."
else
  echo "Certificats non demandés. Plus tard : certbot --apache -d <domaine> --redirect --hsts"
fi

say "7/7 · Nextcloud (applications et serveur de documents)"
bash "$HERE/nextcloud-post-install.sh" || echo "Réglages Nextcloud à terminer : relancez « sudo bash deploy/vps/nextcloud-post-install.sh » dans quelques minutes."

say "Installation terminée"
cat <<EOF
  AxiorHub   : https://$DOMAIN_AGENT  → créez le compte administrateur (/signup), puis suivez l'assistant d'installation.
  Code privé de première inscription : $HERE/data/axiorhub/auth/bootstrap-token (lecture avec sudo cat ; ne pas publier).
  Nextcloud  : https://$DOMAIN_CLOUD  → compte « $NEXTCLOUD_ADMIN_USER », mot de passe dans $ENV_FILE (NEXTCLOUD_ADMIN_PASSWORD).
               Créez-y un compte technique « axiorhub » et un mot de passe d'application pour l'assistant d'AxiorHub.
  Webmail    : https://$DOMAIN_MAIL
  Documents  : https://$DOMAIN_OFFICE (utilisé par Nextcloud et AxiorHub)
  Sauvegardes : sudo bash deploy/vps/backup.sh (à planifier chaque nuit, copie hors du serveur conseillée).
EOF
