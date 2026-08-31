#!/usr/bin/env bash
set -euo pipefail
# Only this application's builder cache and untagged application images.
# Running containers, tagged local/rollback images, data and volumes remain intact.
if docker buildx inspect radiology-atlas-bounded >/dev/null 2>&1; then
  docker buildx prune --builder radiology-atlas-bounded --force \
    --filter 'until=168h' --max-used-space 2GB --reserved-space 256MB
fi
docker image prune --force --filter 'label=org.opencontainers.image.title=Radiology Atlas Viewer'
echo "CLEANUP=PASS; DATA=unchanged; VOLUMES=unchanged; TAGGED_IMAGES=retained"
