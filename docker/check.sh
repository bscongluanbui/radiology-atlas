#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
docker compose config --quiet
docker compose exec -T viewer python docker/preflight.py
docker compose exec -T viewer python -c "import platform,urllib.request; assert urllib.request.urlopen('http://127.0.0.1:8080/healthz',timeout=5).status==200; print('CONTAINER_SMOKE=PASS; ARCH='+platform.machine())"
# Read-only checks only: do not build, restart or change an existing service.
