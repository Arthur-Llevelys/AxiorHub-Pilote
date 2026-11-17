#!/usr/bin/env bash
# AxiorHub Pilote — démo en ligne (5.6.107, lot H) : installation, mise à jour, arrêt ou retrait, sur Debian 12 / Ubuntu 22.04-24.04.
#
#   sudo bash deploy/demo/installer-demo.sh --domaine demo.example.test     installer ou mettre à jour (relançable)
#   sudo bash deploy/demo/installer-demo.sh --arreter                       arrêter la démo (toutes les instances sont effacées)
#   sudo bash deploy/demo/installer-demo.sh --retirer                       arrêter et retirer le service et le code (réglages gardés)
#
# Ce que fait l'installation : compte système « axiorhub-demo » sans connexion ; code copié dans /opt/axiorhub-demo (lecture seule) ;
# réglages dans /etc/axiorhub-demo/demo.json (créés une fois, jamais écrasés) ; service systemd durci, sans réseau ; plafonds globaux
# (mémoire, processeur, processus) calculés d'après les réglages. Il ne touche ni à Apache, ni au DNS, ni aux certificats : le
# LISEZMOI explique ces trois étapes, que l'avocat fait lui-même.
set -euo pipefail

ICI="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SOURCE="$(cd "$ICI/../.." && pwd)"
COMPTE=axiorhub-demo
CODE=/opt/axiorhub-demo
REGLAGES=/etc/axiorhub-demo/demo.json
UNITE=/etc/systemd/system/axiorhub-demo.service
PLAFONDS=/etc/systemd/system/axiorhub-demo.service.d/plafonds.conf
MANDATAIRE="${AXIORHUB_DEMO_MANDATAIRE:-www-data}"

dire() { printf '\n\033[1m%s\033[0m\n' "$*"; }
stop() { printf '\nErreur : %s\n' "$*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || stop "lancez ce script avec sudo."
ACTION=installer; DOMAINE=""
while [ $# -gt 0 ]; do
  case "$1" in
    --domaine) DOMAINE="${2:-}"; shift 2 ;;
    --arreter) ACTION=arreter; shift ;;
    --retirer) ACTION=retirer; shift ;;
    *) stop "option inconnue : $1" ;;
  esac
done

if [ "$ACTION" = arreter ]; then
  systemctl disable --now axiorhub-demo.service || true
  dire "Démo arrêtée : toutes les instances des visiteurs ont été effacées. Relancez avec --domaine pour la remettre en service."
  exit 0
fi
if [ "$ACTION" = retirer ]; then
  systemctl disable --now axiorhub-demo.service || true
  rm -f "$UNITE" "$PLAFONDS"; rmdir "$(dirname "$PLAFONDS")" 2>/dev/null || true
  systemctl daemon-reload
  rm -rf "$CODE" /var/lib/axiorhub-demo
  dire "Démo retirée. Réglages conservés dans $REGLAGES (à effacer à la main si vous le souhaitez) ; pensez à retirer l'hôte virtuel Apache."
  exit 0
fi

dire "1/6 · Vérifications"
command -v systemctl >/dev/null || stop "systemd est nécessaire."
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' || stop "Python 3.10 ou plus récent est nécessaire."
if [ -f /sys/fs/cgroup/cgroup.controllers ]; then
  echo "cgroup v2 : oui (mémoire, processeur et processus bornés par instance)."
else
  echo "cgroup v2 : non. Chaque instance se bornera par ses propres limites de processus ; le plafond global reste posé par systemd."
fi
id "$MANDATAIRE" >/dev/null 2>&1 || stop "le compte du mandataire HTTPS ($MANDATAIRE) n'existe pas ; précisez AXIORHUB_DEMO_MANDATAIRE=…"

dire "2/6 · Dépendances Python (paquets Debian)"
if ! python3 -c 'import waitress, cryptography' 2>/dev/null; then
  apt-get update -qq
  apt-get install -y -qq python3-waitress python3-cryptography >/dev/null
fi
python3 -c 'import waitress, cryptography' || stop "waitress et cryptography sont introuvables."

dire "3/6 · Compte système"
if ! id "$COMPTE" >/dev/null 2>&1; then
  useradd --system --user-group --home-dir /var/lib/axiorhub-demo --no-create-home --shell /usr/sbin/nologin "$COMPTE"
fi
usermod -aG "$COMPTE" "$MANDATAIRE"       # le mandataire joint le socket (/run/axiorhub-demo, 0750) ; rien d'autre de la démo
echo "Compte $COMPTE prêt ; $MANDATAIRE peut joindre le socket de la démo."

dire "4/6 · Code (lecture seule)"
VERSION="$(python3 -c "import re,sys; print(re.search(r\"__version__ = '([^']+)'\", open(sys.argv[1]).read()).group(1))" "$SOURCE/agent/__init__.py")"
CIBLE="$CODE/versions/$VERSION"
rm -rf "$CIBLE.nouveau"; install -d -m 0755 "$CIBLE.nouveau"
tar -C "$SOURCE" --exclude=./.git --exclude=./tests --exclude=./data --exclude=./.env -cf - . | tar -C "$CIBLE.nouveau" -xf -
chown -R root:root "$CIBLE.nouveau"; chmod -R go-w,a+rX "$CIBLE.nouveau"
rm -rf "$CIBLE"; mv "$CIBLE.nouveau" "$CIBLE"; ln -sfn "$CIBLE" "$CODE/current"
echo "Version $VERSION installée dans $CIBLE."

dire "5/6 · Réglages"
install -d -m 0750 -o root -g "$COMPTE" /etc/axiorhub-demo
if [ ! -f "$REGLAGES" ]; then
  [ -n "$DOMAINE" ] || stop "première installation : précisez --domaine (par exemple --domaine demo.example.test)."
  python3 - "$ICI/demo.example.json" "$REGLAGES" "$DOMAINE" <<'PY'
import json, re, sys
source, cible, domaine = sys.argv[1:4]
if not re.fullmatch(r'[a-z0-9]([a-z0-9.-]{0,251}[a-z0-9])?', domaine.lower()):
    sys.exit('domaine invalide : ' + domaine)
r = json.load(open(source, encoding='utf-8'))
r['origine'] = 'https://' + domaine.lower()
open(cible, 'w', encoding='utf-8').write(json.dumps(r, ensure_ascii=False, indent=2) + '\n')
PY
  echo "Réglages créés : $REGLAGES"
else
  echo "Réglages existants conservés : $REGLAGES"
fi
chown root:"$COMPTE" "$REGLAGES"; chmod 0640 "$REGLAGES"
( cd "$CODE/current" && runuser -u "$COMPTE" -- python3 -E -s -m agent.demo_hote56107 reglages --reglages "$REGLAGES" >/dev/null ) \
  || stop "réglages refusés : corrigez $REGLAGES (le message ci-dessus indique la valeur en cause)."

dire "6/6 · Service systemd"
install -m 0644 "$ICI/axiorhub-demo.service" "$UNITE"
install -d -m 0755 "$(dirname "$PLAFONDS")"
( cd "$CODE/current" && runuser -u "$COMPTE" -- python3 -E -s -m agent.demo_hote56107 reglages --reglages "$REGLAGES" ) | python3 -c '
import json, sys
r = json.load(sys.stdin)
print("# Calculé par installer-demo.sh d après %s : ne pas modifier à la main, relancer l installation." % sys.argv[1])
print("[Service]")
print("MemoryMax=%dM" % r["memoire_totale_mo"])
print("MemorySwapMax=0")
print("CPUQuota=%d%%" % r["cpu_total_pourcent"])
print("TasksMax=%d" % (r["instances_max"] * r["processus_max"] + 64))
' "$REGLAGES" > "$PLAFONDS"
systemctl daemon-reload
systemctl enable axiorhub-demo.service >/dev/null
systemctl restart axiorhub-demo.service
sleep 2
systemctl is-active --quiet axiorhub-demo.service || stop "le service ne démarre pas : journalctl -u axiorhub-demo -n 50"

dire "Démo installée."
cat <<FIN
Étapes suivantes (voir deploy/demo/LISEZMOI.md) :
  1. DNS : un enregistrement A (et AAAA) pour votre sous-domaine vers ce serveur.
  2. Apache : copier deploy/demo/apache-demo.example.conf, remplacer demo.example.test, a2ensite, puis certbot --apache -d <sous-domaine>.
  3. Test fumée : python3 $CODE/current/deploy/demo/test-fumee.py https://<sous-domaine>
Le socket du répartiteur : /run/axiorhub-demo/demo.sock. État : cd $CODE/current && sudo -u $COMPTE python3 -m agent.demo_hote56107 etat
FIN
