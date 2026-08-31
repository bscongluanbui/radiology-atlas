#!/usr/bin/env bash
# Shared by deployment scripts; .env is an operator-owned Bash-compatible file.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
test -f .env || { echo 'CONFIG=FAIL; create docker/.env from .env.example first.' >&2; exit 2; }
set -a; source ./.env; set +a
: "${DDNS_HOST:?Set DDNS_HOST in .env}"
: "${DATA_ROOT:?Set DATA_ROOT in .env}"
case "$DDNS_HOST" in *://*|*/*|*,*|*:*|*' '*) echo 'CONFIG=FAIL; DDNS_HOST must be a hostname only.' >&2; exit 2;; esac
case "${VIEWER_IMAGE:-ghcr.io/bscongluanbui/radiology-atlas:latest}" in
  ghcr.io/bscongluanbui/radiology-atlas:*|ghcr.io/bscongluanbui/radiology-atlas@sha256:*) ;;
  *) echo 'CONFIG=FAIL; replace the old local VIEWER_IMAGE with ghcr.io/bscongluanbui/radiology-atlas:latest in .env.' >&2; exit 2;;
esac
test -f "$DATA_ROOT/module_catalogue.json" && test -d "$DATA_ROOT/modules" || {
  echo 'DATA=FAIL; DATA_ROOT must contain module_catalogue.json and modules/. Upload real data first.' >&2; exit 2;
}
docker compose config --quiet
