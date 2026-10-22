#!/usr/bin/env bash
# AxiorHub 5.6.70 — bilan et durcissement du serveur du cabinet (audit de sécurité du 22 octobre 2026).
#
#   sudo bash deploy/securite/durcir-serveur.sh                 bilan seul : rien n'est modifié
#   sudo bash deploy/securite/durcir-serveur.sh --appliquer     correctifs PHP « PHP2Shell », fail2ban (SSH, AxiorHub, Nextcloud),
#                                                               mises à jour de sécurité automatiques
#   ... --appliquer --pare-feu [--jitsi 10010]                  en plus : pare-feu ufw (SSH, HTTP, HTTPS, UDP Jitsi) — vérifiez avant
#                                                               que vos autres services n'utilisent pas d'autres ports publics.
# Rien n'est supprimé ; chaque fichier de configuration créé porte la mention « AxiorHub 5.6.70 ».
set -uo pipefail

APPLY=0 ; FIREWALL=0 ; JITSI_UDP=''
while [ $# -gt 0 ]; do
  case "$1" in
    --appliquer) APPLY=1; shift ;;
    --pare-feu) FIREWALL=1; shift ;;
    --jitsi) JITSI_UDP="$2"; shift 2 ;;
    *) echo "Option inconnue : $1" >&2; exit 2 ;;
  esac
done
[ "$(id -u)" -eq 0 ] || { echo "À lancer en administrateur (sudo)." >&2; exit 1; }
ok()   { printf '  [OK]        %s\n' "$*"; }
warn() { printf '  [À CORRIGER] %s\n' "$*"; }
info() { printf '  [INFO]      %s\n' "$*"; }
FIXED_RE='^(8\.5\.(1[2-9]|[2-9][0-9])|8\.4\.(2[7-9]|[3-9][0-9])|8\.3\.(3[6-9]|[4-9][0-9])|8\.2\.(3[5-9]|[4-9][0-9]))'

echo "== 1. PHP « PHP2Shell » (CVE-2026-109618, CVE-2026-109619 ; corrigés en 8.5.12, 8.4.27, 8.3.36, 8.2.35)"
php_packages=$(dpkg-query -W -f='${binary:Package}\n' 'php*' 2>/dev/null | grep -E '^php[0-9.]*-' || true)
if [ -n "$php_packages" ]; then
  for doc in /usr/share/doc/php*-common; do
    [ -d "$doc" ] || continue
    branch=$(basename "$doc" | sed -E 's/^php([0-9.]+)-common$/\1/')
    version=$(dpkg-query -W -f='${Version}' "php$branch-common" 2>/dev/null)
    if zgrep -q 'CVE-2026-109618' "$doc/changelog.Debian.gz" 2>/dev/null && zgrep -q 'CVE-2026-109619' "$doc/changelog.Debian.gz" 2>/dev/null; then
      ok "PHP $branch ($version) : correctif présent dans le paquet"
    elif echo "$version" | grep -Eq "$FIXED_RE"; then
      ok "PHP $branch ($version) : version corrigée"
    else
      warn "PHP $branch ($version) : correctif absent"
      NEED_PHP=1
    fi
  done
  if [ "${NEED_PHP:-0}" = 1 ] && [ "$APPLY" = 1 ]; then
    apt-get update -q && apt-get install -y --only-upgrade $php_packages
    for unit in $(systemctl list-units --type=service --no-legend 'php*-fpm.service' 2>/dev/null | awk '{print $1}') apache2.service; do
      systemctl is-active --quiet "$unit" && systemctl restart "$unit" && info "redémarré : $unit"
    done
    zgrep -l 'CVE-2026-109618' /usr/share/doc/php*-common/changelog.Debian.gz >/dev/null 2>&1 && ok "correctif installé" || \
      warn "le dépôt de la distribution ne propose pas encore le correctif : utilisez le dépôt de votre version de PHP (ex. packages.sury.org) puis relancez"
  fi
else
  info "aucun paquet PHP sur la machine"
fi
if command -v docker >/dev/null; then
  for c in $(docker ps --format '{{.Names}}' 2>/dev/null); do
    v=$(docker exec "$c" php -r 'echo PHP_VERSION;' 2>/dev/null) || continue
    [ -n "$v" ] || continue
    if echo "$v" | grep -Eq "$FIXED_RE"; then ok "conteneur $c : PHP $v"
    else warn "conteneur $c : PHP $v — mettez l'image à jour (docker compose pull && docker compose up -d dans son dossier) puis vérifiez"; fi
  done
fi

echo "== 2. Blocage automatique des tentatives d'accès (fail2ban)"
LOGS=$(ls /var/lib/axiorhub-mail-agent/securite-acces.log /var/lib/axiorhub*/*/securite-acces.log /var/lib/axiorhub*/securite-acces.log 2>/dev/null | sort -u | tr '\n' ' ')
NC_LOG=$(ls /var/www/nextcloud/data/nextcloud.log /var/www/html/data/nextcloud.log 2>/dev/null | head -1)
if command -v fail2ban-client >/dev/null; then ok "fail2ban installé"; else warn "fail2ban absent"; fi
if [ "$APPLY" = 1 ]; then
  command -v fail2ban-client >/dev/null || apt-get install -y fail2ban
  [ -n "$LOGS" ] || { mkdir -p /var/lib/axiorhub-mail-agent; touch /var/lib/axiorhub-mail-agent/securite-acces.log; LOGS=/var/lib/axiorhub-mail-agent/securite-acces.log; }
  cat > /etc/fail2ban/filter.d/axiorhub.conf <<'F'
# AxiorHub 5.6.70 : blocages décidés par AxiorHub (sondes, échecs de connexion) appliqués par le pare-feu
[Definition]
failregex = AXIORHUB-BLOCAGE ip=<HOST>
ignoreregex =
F
  {
    echo "# AxiorHub 5.6.70"
    echo "[sshd]"; echo "enabled = true"; echo "maxretry = 5"; echo "findtime = 10m"; echo "bantime = 1h"
    echo; echo "[axiorhub]"; echo "enabled = true"; echo "filter = axiorhub"; echo "logpath = $LOGS"; echo "maxretry = 1"; echo "findtime = 1d"; echo "bantime = 1d"
    if [ -n "$NC_LOG" ]; then
      echo; echo "[nextcloud]"; echo "enabled = true"; echo "filter = nextcloud"; echo "logpath = $NC_LOG"; echo "maxretry = 5"; echo "findtime = 10m"; echo "bantime = 1h"
    fi
  } > /etc/fail2ban/jail.d/axiorhub.local
  if [ -n "$NC_LOG" ] && [ ! -f /etc/fail2ban/filter.d/nextcloud.conf ]; then
    cat > /etc/fail2ban/filter.d/nextcloud.conf <<'F'
# AxiorHub 5.6.70 : échecs de connexion Nextcloud (journal JSON de Nextcloud)
[Definition]
_groupsre = (?:(?:,?\s*"\w+":(?:"[^"]+"|\w+))*)
failregex = ^\{%(_groupsre)s,?\s*"remoteAddr":"<HOST>"%(_groupsre)s,?\s*"message":"Login failed:
            ^\{%(_groupsre)s,?\s*"remoteAddr":"<HOST>"%(_groupsre)s,?\s*"message":"Trusted domain error.
datepattern = ,?\s*"time"\s*:\s*"%%Y-%%m-%%d[T ]%%H:%%M:%%S(%%z)?"
F
  fi
  systemctl enable --now fail2ban >/dev/null 2>&1; systemctl restart fail2ban
  fail2ban-client status | sed 's/^/  /'
else
  info "journal AxiorHub : ${LOGS:-(créé au premier blocage)} ; journal Nextcloud : ${NC_LOG:-non trouvé}"
fi

echo "== 3. Mises à jour de sécurité automatiques"
if dpkg -s unattended-upgrades >/dev/null 2>&1 && grep -q '"1"' /etc/apt/apt.conf.d/20auto-upgrades 2>/dev/null; then ok "actives"
else
  warn "inactives"
  if [ "$APPLY" = 1 ]; then
    apt-get install -y unattended-upgrades
    printf 'APT::Periodic::Update-Package-Lists "1";\nAPT::Periodic::Unattended-Upgrade "1";\n' > /etc/apt/apt.conf.d/20auto-upgrades
    ok "activées (correctifs de sécurité de la distribution chaque jour)"
  fi
fi

echo "== 4. Accès SSH"
SSHD=$(sshd -T 2>/dev/null || true)
echo "$SSHD" | grep -qi '^permitrootlogin no' && ok "connexion root interdite" || warn "connexion root par SSH autorisée (PermitRootLogin) — à interdire après vérification de votre propre accès"
echo "$SSHD" | grep -qi '^passwordauthentication no' && ok "mots de passe SSH refusés (clés seulement)" || info "mots de passe SSH acceptés : préférez les clés (PasswordAuthentication no) — non modifié par ce script"

echo "== 5. Pare-feu"
if command -v ufw >/dev/null && ufw status | grep -q 'Status: active'; then ok "ufw actif"; ufw status | sed 's/^/    /'
else
  warn "pare-feu ufw inactif"
  info "ports publics actuellement ouverts :"; ss -ltnu 2>/dev/null | awk 'NR>1 && $5 !~ /^(127\.|\[::1\])/ {print "    " $1 " " $5}' | sort -u
  if [ "$APPLY" = 1 ] && [ "$FIREWALL" = 1 ]; then
    apt-get install -y ufw >/dev/null
    ufw allow OpenSSH; ufw allow 80/tcp; ufw allow 443/tcp
    [ -n "$JITSI_UDP" ] && ufw allow "$JITSI_UDP/udp" comment 'Jitsi media (AxiorHub)'
    ufw --force enable && ok "ufw activé (SSH, HTTP, HTTPS${JITSI_UDP:+, $JITSI_UDP/udp})"
  else
    info "activation seulement avec --appliquer --pare-feu, après vérification de la liste ci-dessus"
  fi
fi

echo "== 6. Signes de compromission PHP (depuis le 17 octobre 2026, à examiner)"
for d in /var/www /var/lib/docker/volumes; do
  [ -d "$d" ] || continue
  find "$d" -xdev -type f -name '*.php' -newermt '2026-10-17' -size -200k 2>/dev/null | grep -v -E '/(apps|3rdparty|lib|core|updater)/' | head -20 | sed 's/^/    fichier PHP récent : /'
done
info "fichiers PHP créés ou modifiés hors des répertoires de code, s'il y en a : à vérifier un par un (rien n'est supprimé)"
[ "$APPLY" = 1 ] || echo "Bilan seul : relancez avec --appliquer pour corriger."
