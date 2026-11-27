#!/usr/bin/env bash
set -euo pipefail
if [[ $EUID -ne 0 ]]; then echo 'Exécuter : sudo bash install.sh' >&2; exit 2; fi
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
if [[ ! -f MANIFEST.sha256 ]]; then
  echo 'MANIFEST.sha256 absent : utilisez une archive de version. Depuis les sources, construisez cette archive avec python3 scripts/build-release.py /tmp/axiorhub-version.tar.gz, puis installez son contenu.' >&2
  exit 2
fi
if ! sha256sum --check --status MANIFEST.sha256; then
  echo "Intégrité de l'archive non vérifiée : installation arrêtée. Téléchargez de nouveau une archive de version fiable ; ne régénérez pas son manifeste pour contourner ce contrôle." >&2
  exit 2
fi
python3 -B installer.py "$@"
# 5.6.61 : service de mise à jour signée (bouton dans Paramètres) installé et activé sans ligne de commande
if [[ $# -eq 0 && -f /opt/axiorhub-mail-agent/current/miseajour.py ]]; then
  python3 -B /opt/axiorhub-mail-agent/current/miseajour.py --installer-auto || true
fi
# 5.6.101 : veille de sécurité du serveur (collecteur en lecture seule, minuteurs systemd), active par défaut, désactivable
if [[ $# -eq 0 && -f /opt/axiorhub-mail-agent/current/deploy/securite/veille-securite.py ]]; then
  python3 -B /opt/axiorhub-mail-agent/current/deploy/securite/veille-securite.py --installer-auto || true
fi
# 5.2.0 : recette automatique de la version installée, en arrière-plan (résultat dans « Pourquoi rien n’est produit ? »).
if [[ $# -eq 0 && -f /opt/axiorhub-mail-agent/current/recette.py ]]; then
  python3 -B /opt/axiorhub-mail-agent/current/recette.py --background || true
fi
