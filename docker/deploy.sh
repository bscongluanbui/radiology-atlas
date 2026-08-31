#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
test -f .env || { echo "Thiếu docker/.env; hãy sao chép .env.example và sửa giá trị." >&2; exit 2; }
set -a; source ./.env; set +a
: "${DDNS_HOST:?DDNS_HOST is required}"
: "${DATA_ROOT:?DATA_ROOT is required}"
test -d "$DATA_ROOT/modules" -a -f "$DATA_ROOT/module_catalogue.json" || { echo "DATA_ROOT không phải thư mục all_modules hợp lệ." >&2; exit 2; }
docker compose config --quiet
bash ./build-local.sh
docker compose up -d --remove-orphans
docker compose ps
echo "DEPLOYED=https://$DDNS_HOST"
echo "FIRST_ROOT=docker compose run --rm viewer python docker/manage.py create-root --username root"
