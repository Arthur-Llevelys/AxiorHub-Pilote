#!/usr/bin/env bash
set -euo pipefail
if [[ $EUID -ne 0 ]]; then echo 'Exécuter avec sudo.' >&2; exit 2; fi
systemctl disable --now axiorhub-mail-agent.timer axiorhub-mail-agent-cleanup.timer
systemctl stop axiorhub-mail-agent.service axiorhub-mail-agent-cleanup.service
echo 'Assistant arrêté. Configuration, journal et brouillons conservés.'
