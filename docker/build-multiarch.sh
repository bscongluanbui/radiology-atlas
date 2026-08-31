#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
: "${IMAGE:?Set IMAGE to a registry tag, for example ghcr.io/OWNER/radiology-atlas:2026.08.31}"
builder=radiology-atlas-bounded
if ! docker buildx inspect "$builder" >/dev/null 2>&1; then
  docker buildx create --name "$builder" --driver docker-container --buildkitd-config docker/buildkitd.toml --use
else
  docker buildx use "$builder"
fi
docker buildx inspect --bootstrap
revision="${BUILD_REVISION:-$(date -u +%Y%m%dT%H%M%SZ)}"
docker buildx build --pull --platform linux/amd64,linux/arm64 \
  --build-arg "BUILD_REVISION=$revision" --tag "$IMAGE" --push \
  --file docker/Dockerfile .
echo "MULTIARCH_PUSHED=$IMAGE; PLATFORMS=linux/amd64,linux/arm64; REVISION=$revision"
