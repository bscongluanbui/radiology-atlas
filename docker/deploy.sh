#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
source ./common.sh
mode="${1:-tunnel}"
case "$mode" in tunnel|direct) ;; *) echo 'Usage: bash deploy.sh [tunnel|direct]' >&2; exit 2;; esac
docker compose pull viewer
docker compose run --rm --no-deps viewer python docker/preflight.py
docker compose up -d --no-deps --wait --wait-timeout 180 viewer
if test "$mode" = tunnel; then
  bash ./attach-tunnel.sh
else
  docker compose --profile direct pull caddy
  docker compose --profile direct up -d --wait --wait-timeout 180 caddy
fi
docker compose ps
echo "DEPLOY=PASS; mode=$mode; hostname=$DDNS_HOST"
echo 'FIRST_ROOT=docker compose exec viewer python docker/manage.py create-root --username root'
