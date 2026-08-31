#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
set -a; source ./.env; set +a
current="$(docker compose images -q viewer 2>/dev/null || true)"
rollback="radiology-atlas-viewer:rollback"
if test -n "$current"; then docker image tag "$current" "$rollback"; fi
bash ./build-local.sh
restore_previous() {
  echo "UPDATE=FAILED; DATA=unchanged; STATE=preserved; RESTORING=$rollback" >&2
  if test -n "$current"; then
    VIEWER_IMAGE="$rollback" docker compose up -d --no-deps --force-recreate viewer
  fi
}
if ! docker compose up -d --no-deps viewer; then restore_previous; exit 1; fi
cid="$(docker compose ps -q viewer)"
for _ in $(seq 1 24); do
  status="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$cid")"
  test "$status" = healthy && { echo "UPDATE=PASS; ROLLBACK_IMAGE=${rollback}"; exit 0; }
  test "$status" = unhealthy && break
  sleep 5
done
restore_previous
exit 1
