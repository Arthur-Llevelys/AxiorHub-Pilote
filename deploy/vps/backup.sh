#!/usr/bin/env bash
# Snapshot cohérent : producteurs arrêtés, Nextcloud en maintenance, SQLite vérifiées dans l'archive.
# Les archives contiennent les dossiers, secrets ET clés du coffre : stockage privé et chiffrement externe nécessaires.
set -euo pipefail
umask 077
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEST="${1:-/var/backups/axiorhub}"
STAMP="$(date +%Y-%m-%d_%H%M%S)"
COMPOSE=(docker compose -f "$HERE/docker-compose.yml" --env-file "$HERE/.env")
mkdir -p "$DEST"; chmod 700 "$DEST"
WORK="$(mktemp -d "$DEST/.snapshot-XXXXXX")"
mapfile -t RUNNING < <("${COMPOSE[@]}" ps --status running --services)
STOPPED=()
MAINTENANCE_OFF=false
cleanup() {
  local rc=$?
  if ((${#STOPPED[@]})); then "${COMPOSE[@]}" start "${STOPPED[@]}" >/dev/null || rc=1; fi
  if $MAINTENANCE_OFF; then "${COMPOSE[@]}" exec -T -u www-data nextcloud php occ maintenance:mode --off >/dev/null || rc=1; fi
  if [[ -d "$WORK" ]]; then rm -rf -- "$WORK"; fi
  exit "$rc"
}
trap cleanup EXIT
NC_RUNNING=false
for name in "${RUNNING[@]}"; do [[ "$name" != nextcloud ]] || NC_RUNNING=true; done
if $NC_RUNNING; then
  mode="$("${COMPOSE[@]}" exec -T -u www-data nextcloud php occ config:system:get maintenance)"
  if [[ "$mode" != true && "$mode" != 1 ]]; then
    "${COMPOSE[@]}" exec -T -u www-data nextcloud php occ maintenance:mode --on >/dev/null
    MAINTENANCE_OFF=true
  fi
fi
for name in "${RUNNING[@]}"; do
  case "$name" in axiorhub|axiorhub-worker|axiorhub-worker2|axiorhub-watcher|nextcloud|nextcloud-cron|roundcube|onlyoffice) STOPPED+=("$name");; esac
done
if ((${#STOPPED[@]})); then "${COMPOSE[@]}" stop -t 300 "${STOPPED[@]}"; fi
"${COMPOSE[@]}" exec -T nextcloud-db sh -c 'exec mariadb-dump --single-transaction -u root -p"$MYSQL_ROOT_PASSWORD" nextcloud' > "$WORK/nextcloud-db.sql"
# 5.6.88 : les archives de la sauvegarde automatique (Paramètres) et les exports ont leur propre rétention : ils n'entrent pas dans ce snapshot.
# Cette sauvegarde automatique couvre AxiorHub seul ; ce script reste nécessaire pour Nextcloud et sa base. Voir docs/REVERSIBILITE-5.6.88.md.
tar -C "$HERE/data" --exclude='sauvegardes5688' --exclude='exports5688' --exclude='.travail5688' -czf "$WORK/donnees.tar.gz" nextcloud axiorhub roundcube
cp "$HERE/.env" "$WORK/env.sauvegarde"
cp "$HERE/docker-compose.yml" "$WORK/docker-compose.sauvegarde.yml"
python3 "$HERE/verify-backup.py" "$WORK/donnees.tar.gz" > "$WORK/verification.json"
(cd "$WORK"; sha256sum donnees.tar.gz nextcloud-db.sql env.sauvegarde docker-compose.sauvegarde.yml verification.json > SHA256SUMS)
mv "$WORK" "$DEST/$STAMP"
printf 'Sauvegarde vérifiée : %s\n' "$DEST/$STAMP"
# Aucun ancien snapshot n'est supprimé automatiquement : rétention à définir dans le logiciel de sauvegarde.
