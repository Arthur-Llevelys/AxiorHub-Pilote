#!/usr/bin/env bash
set -euo pipefail
domain=${1:-agent.example.com}
email=${2:-admin@example.com}
echo "Choisissez d’abord Nginx ou Apache, remplacez agent.example.com dans son fichier, puis :"
echo "sudo certbot --nginx -d ${domain} --email ${email} --agree-tos --no-eff-email"
echo "ou : sudo certbot --apache -d ${domain} --email ${email} --agree-tos --no-eff-email"
