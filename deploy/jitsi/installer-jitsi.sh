#!/usr/bin/env bash
# AxiorHub 5.6.70 — installation de Jitsi Meet (visioconférence libre) sur le serveur du cabinet, à côté des autres services.
#
# Ports choisis pour ne pas gêner Nextcloud, Apache, AxiorHub ni Collabora :
#   - interface web de Jitsi : 127.0.0.1:10080 (locale seulement ; publiée par le proxy HTTPS existant, voir apache-visio.example.conf) ;
#   - flux audio et vidéo : 10010/UDP (à ouvrir dans le pare-feu et chez l'hébergeur).
# Sécurité : authentification Jitsi activée (seuls les comptes du cabinet ouvrent une salle ; les invités rejoignent par le lien).
#
# Usage (en administrateur) :
#   sudo bash deploy/jitsi/installer-jitsi.sh --domaine visio.example.test [--ip-publique 203.0.113.10] [--dossier /opt/jitsi-meet]
#                                             [--port-web 10080] [--port-media 10010] [--compte cabinet]
# Prérequis : Docker et le greffon « docker compose ». Le script n'écrase jamais une installation existante (fichier .env présent).
set -euo pipefail

DOMAINE='' ; IP_PUBLIQUE='' ; DOSSIER='/opt/jitsi-meet' ; PORT_WEB='10080' ; PORT_MEDIA='10010' ; COMPTE='cabinet'
while [ $# -gt 0 ]; do
  case "$1" in
    --domaine) DOMAINE="$2"; shift 2 ;;
    --ip-publique) IP_PUBLIQUE="$2"; shift 2 ;;
    --dossier) DOSSIER="$2"; shift 2 ;;
    --port-web) PORT_WEB="$2"; shift 2 ;;
    --port-media) PORT_MEDIA="$2"; shift 2 ;;
    --compte) COMPTE="$2"; shift 2 ;;
    *) echo "Option inconnue : $1" >&2; exit 2 ;;
  esac
done
[ "$(id -u)" -eq 0 ] || { echo "À lancer en administrateur (sudo)." >&2; exit 1; }
[ -n "$DOMAINE" ] || { echo "Indiquez --domaine (par exemple visio.example.test)." >&2; exit 2; }
case "$PORT_WEB$PORT_MEDIA" in *[!0-9]*) echo "Ports invalides." >&2; exit 2 ;; esac
command -v docker >/dev/null || { echo "Docker est requis." >&2; exit 1; }
docker compose version >/dev/null 2>&1 || { echo "Le greffon « docker compose » est requis." >&2; exit 1; }
for port in "$PORT_WEB"; do
  if ss -ltn 2>/dev/null | awk '{print $4}' | grep -Eq "[:.]$port\$"; then echo "Le port $port est déjà utilisé : choisissez --port-web." >&2; exit 1; fi
done
if ss -lun 2>/dev/null | awk '{print $4}' | grep -Eq "[:.]$PORT_MEDIA\$"; then echo "Le port UDP $PORT_MEDIA est déjà utilisé : choisissez --port-media." >&2; exit 1; fi

if [ -f "$DOSSIER/.env" ]; then
  echo "Installation existante trouvée dans $DOSSIER : rien n'est écrasé. Pour relancer : cd $DOSSIER && docker compose up -d"
  exit 0
fi

echo "== Téléchargement de la dernière version stable de docker-jitsi-meet (projet officiel Jitsi)"
TAG=$(curl -fsSL https://api.github.com/repos/jitsi/docker-jitsi-meet/releases/latest | python3 -c 'import json,sys; print(json.load(sys.stdin)["tag_name"])')
case "$TAG" in stable-*) ;; *) echo "Version inattendue : $TAG" >&2; exit 1 ;; esac
TMP=$(mktemp -d)
curl -fsSL "https://github.com/jitsi/docker-jitsi-meet/archive/refs/tags/$TAG.tar.gz" -o "$TMP/jitsi.tar.gz"
mkdir -p "$DOSSIER"
tar -xzf "$TMP/jitsi.tar.gz" -C "$DOSSIER" --strip-components=1
rm -rf "$TMP"
cd "$DOSSIER"
cp env.example .env
chmod 600 .env
./gen-passwords.sh

set_env() {   # set_env CLÉ VALEUR : remplace ou ajoute une ligne CLÉ=VALEUR dans .env
  if grep -Eq "^#?[[:space:]]*$1=" .env; then sed -i -E "s|^#?[[:space:]]*$1=.*|$1=$2|" .env; else echo "$1=$2" >> .env; fi
}
CFG="$DOSSIER/config"
set_env CONFIG "$CFG"
set_env HTTP_PORT "127.0.0.1:$PORT_WEB"
set_env HTTPS_PORT "127.0.0.1:$((PORT_WEB + 363))"
set_env DISABLE_HTTPS 1
set_env ENABLE_HTTP_REDIRECT 0
set_env ENABLE_LETSENCRYPT 0
set_env PUBLIC_URL "https://$DOMAINE"
set_env TZ Europe/Paris
set_env JVB_PORT "$PORT_MEDIA"
set_env ENABLE_AUTH 1
set_env ENABLE_GUESTS 1
set_env AUTH_TYPE internal
[ -n "$IP_PUBLIQUE" ] && set_env JVB_ADVERTISE_IPS "$IP_PUBLIQUE"
mkdir -p "$CFG"/{web,transcripts,prosody/config,prosody/prosody-plugins-custom,jicofo,jvb,jigasi,jibri}

echo "== Démarrage des conteneurs"
docker compose up -d
sleep 15

MOT_DE_PASSE=$(head -c 32 /dev/urandom | base64 | tr -dc 'A-Za-z0-9' | head -c 20)
docker compose exec -T prosody prosodyctl --config /config/prosody.cfg.lua register "$COMPTE" meet.jitsi "$MOT_DE_PASSE"

if command -v ufw >/dev/null && ufw status | grep -q 'Status: active'; then
  ufw allow "$PORT_MEDIA/udp" comment 'Jitsi media (AxiorHub)' >/dev/null && echo "Pare-feu : $PORT_MEDIA/udp ouvert."
else
  echo "Pensez à ouvrir $PORT_MEDIA/udp dans le pare-feu du serveur et chez l'hébergeur."
fi

cat <<FIN

Jitsi Meet est installé dans $DOSSIER.
  Interface locale : http://127.0.0.1:$PORT_WEB  (à publier en HTTPS sur https://$DOMAINE par le proxy :
                     voir deploy/jitsi/apache-visio.example.conf, puis certbot pour le certificat)
  Flux média       : $PORT_MEDIA/UDP
  Compte organisateur : $COMPTE   mot de passe : $MOT_DE_PASSE
  (notez-le maintenant : il n'est affiché qu'une fois ; d'autres comptes : docker compose exec prosody prosodyctl
   --config /config/prosody.cfg.lua register NOM meet.jitsi MOT_DE_PASSE)

Dans AxiorHub : Paramètres › Courriels et accueil › Visioconférence, adresse https://$DOMAINE.
FIN
