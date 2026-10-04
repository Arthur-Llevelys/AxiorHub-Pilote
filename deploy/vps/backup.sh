#!/usr/bin/env bash
# Sauvegarde du VPS : base Nextcloud, données Nextcloud, AxiorHub, Roundcube et réglages (.env).
# Usage : sudo bash deploy/vps/backup.sh [/chemin/des/sauvegardes]   — à planifier chaque nuit (cron ou timer systemd).
# Les sauvegardes contiennent des données de clients : chiffrez-les et copiez-les hors du serveur (par exemple restic ou borg).
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEST="${1:-/var/backups/axiorhub}"
STAMP="$(date +%Y-%m-%d_%H%M)"
set -a; . "$HERE/.env"; set +a
COMPOSE=(docker compose -f "$HERE/docker-compose.yml" --env-file "$HERE/.env")
mkdir -p "$DEST/$STAMP"; chmod 700 "$DEST"
"${COMPOSE[@]}" exec -T -u www-data nextcloud php occ maintenance:mode --on
trap '"${COMPOSE[@]}" exec -T -u www-data nextcloud php occ maintenance:mode --off' EXIT
"${COMPOSE[@]}" exec -T nextcloud-db sh -c 'exec mariadb-dump --single-transaction -u root -p"$MYSQL_ROOT_PASSWORD" nextcloud' > "$DEST/$STAMP/nextcloud-db.sql"
tar -C "$HERE/data" -czf "$DEST/$STAMP/donnees.tar.gz" nextcloud axiorhub roundcube
cp "$HERE/.env" "$DEST/$STAMP/env.sauvegarde"; chmod 600 "$DEST/$STAMP/env.sauvegarde"
find "$DEST" -mindepth 1 -maxdepth 1 -type d -mtime +14 -exec rm -rf {} +
echo "Sauvegarde : $DEST/$STAMP"
