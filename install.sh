#!/usr/bin/env bash
set -euo pipefail
if [[ $EUID -ne 0 ]]; then echo 'Exécuter : sudo bash install.sh' >&2; exit 2; fi
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
sha256sum --check --status MANIFEST.sha256
python3 -B installer.py "$@"
# 5.6.61 : service de mise à jour signée (bouton dans Paramètres) installé et activé sans ligne de commande
if [[ $# -eq 0 && -f /opt/axiorhub-mail-agent/current/miseajour.py ]]; then
  python3 -B /opt/axiorhub-mail-agent/current/miseajour.py --installer-auto || true
fi
# 5.2.0 : recette automatique de la version installée, en arrière-plan (résultat dans « Pourquoi rien n’est produit ? »).
if [[ $# -eq 0 && -f /opt/axiorhub-mail-agent/current/recette.py ]]; then
  python3 -B /opt/axiorhub-mail-agent/current/recette.py --background || true
fi
