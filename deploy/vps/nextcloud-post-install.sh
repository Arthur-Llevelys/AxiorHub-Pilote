#!/usr/bin/env bash
# Réglages de Nextcloud après la première installation : applications utiles au cabinet et connexion au serveur de documents.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
set -a; . "$HERE/.env"; set +a
COMPOSE=(docker compose -f "$HERE/docker-compose.yml" --env-file "$HERE/.env")
occ() { "${COMPOSE[@]}" exec -T -u www-data nextcloud php occ "$@"; }

echo "Attente de la fin de l'installation de Nextcloud…"
for _ in $(seq 1 60); do
  if occ status 2>/dev/null | grep -q "installed: true"; then break; fi
  sleep 10
done
occ status | grep -q "installed: true" || { echo "Nextcloud n'est pas encore prêt."; exit 1; }

occ config:system:set overwriteprotocol --value=https
occ config:system:set overwrite.cli.url --value="https://$DOMAIN_CLOUD"
occ config:system:set trusted_domains 1 --value="$DOMAIN_CLOUD"
occ config:system:set trusted_proxies 0 --value=127.0.0.1
occ config:system:set trusted_proxies 1 --value=172.16.0.0/12
occ config:system:set default_phone_region --value=FR
occ config:system:set maintenance_window_start --type=integer --value=1
occ background:cron
for app in calendar tasks deck contacts notes news onlyoffice; do
  occ app:install "$app" >/dev/null 2>&1 || occ app:enable "$app" >/dev/null 2>&1 || echo "  Application $app : à installer depuis l'interface de Nextcloud."
done
occ config:app:set onlyoffice DocumentServerUrl --value="https://$DOMAIN_OFFICE/"
occ config:app:set onlyoffice jwt_secret --value="$ONLYOFFICE_JWT_SECRET"
occ config:app:set onlyoffice jwt_header --value="Authorization"
occ maintenance:repair --include-expensive >/dev/null 2>&1 || true
echo "Nextcloud prêt : Agenda, Tâches, Deck, Contacts, Notes, Nouvelles et serveur de documents connectés."
