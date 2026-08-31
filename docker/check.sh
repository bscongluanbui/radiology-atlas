#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
docker compose config --quiet
bash ./build-local.sh
docker compose up -d viewer
# Deliberately leave the service running; this command may be used on a live server.
cid="$(docker compose ps -q viewer)"
for _ in $(seq 1 24); do
  status="$(docker inspect --format '{{.State.Health.Status}}' "$cid")"
  test "$status" = healthy && break
  test "$status" = unhealthy && { docker compose logs viewer; exit 1; }
  sleep 5
done
test "$(docker inspect --format '{{.State.Health.Status}}' "$cid")" = healthy
docker compose exec -T viewer python offline_anatomy_viewer/server.py --data-root /data --self-test
echo "CONTAINER_SMOKE=PASS; ARCH=$(docker inspect --format '{{.Architecture}}' "$(docker inspect --format '{{.Image}}' "$cid")")"
