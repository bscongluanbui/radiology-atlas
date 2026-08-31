#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
source ./common.sh
cid="$(docker compose ps -q viewer)"
current=""
rollback=radiology-atlas-viewer:rollback
if test -n "$cid"; then
  # Capture the RUNNING image ID before latest moves to a new digest.
  current="$(docker inspect --format '{{.Image}}' "$cid")"
  docker image tag "$current" "$rollback"
fi
docker compose pull viewer
docker compose run --rm --no-deps viewer python docker/preflight.py
if docker compose up -d --no-deps --force-recreate --pull never --wait --wait-timeout 180 viewer; then
  echo "UPDATE=PASS; previous_image=${current:-none}; rollback_tag=$rollback"
  exit 0
fi
echo 'UPDATE=FAIL; state and data preserved.' >&2
if test -n "$current"; then
  VIEWER_IMAGE="$rollback" docker compose up -d --no-deps --force-recreate --pull never --wait --wait-timeout 180 viewer
  echo "ROLLBACK=PASS; image=$current; state=preserved"
fi
exit 1
