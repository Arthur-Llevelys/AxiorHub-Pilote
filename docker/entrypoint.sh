#!/bin/sh
set -eu
AXIORHUB_INTERNAL_API_TOKEN="$(python3 /app/docker/bootstrap.py)"
export AXIORHUB_INTERNAL_API_TOKEN
exec python3 /app/standalone.py
