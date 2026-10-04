#!/bin/sh
set -eu
if [ -z "${AXIORHUB_INTERNAL_API_TOKEN:-}" ]; then
  AXIORHUB_INTERNAL_API_TOKEN="$(python3 /app/docker/bootstrap.py)"
  export AXIORHUB_INTERNAL_API_TOKEN
else
  python3 /app/docker/bootstrap.py >/dev/null
fi
exec python3 /app/standalone.py
