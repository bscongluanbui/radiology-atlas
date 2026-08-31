#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
set -a; source ./.env; set +a
builder=radiology-atlas-bounded
if ! docker buildx inspect "$builder" >/dev/null 2>&1; then
  docker buildx create --name "$builder" --driver docker-container --buildkitd-config ./buildkitd.toml
fi
docker buildx inspect "$builder" --bootstrap
docker buildx build --builder "$builder" --pull --load \
  --build-arg "BUILD_REVISION=${BUILD_REVISION:-local}" \
  --tag "${VIEWER_IMAGE:-radiology-atlas-viewer:local}" --file Dockerfile ..
