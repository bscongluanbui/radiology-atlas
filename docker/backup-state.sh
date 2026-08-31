#!/usr/bin/env bash
set -euo pipefail
umask 077
cd "$(dirname "$0")"
destination="${1:-./backups}"
mkdir -p "$destination"
name="atlas-state-$(date -u +%Y%m%dT%H%M%SZ).tar.gz"
docker volume inspect radiology-atlas_atlas-state >/dev/null
# Pause only the viewer briefly so SQLite/WAL form a consistent backup; data is never copied.
docker compose stop viewer
trap 'docker compose start viewer >/dev/null' EXIT
docker run --rm -v radiology-atlas_atlas-state:/source:ro -v "$(cd "$destination" && pwd):/backup" \
  -e "BACKUP_FILE=$name" -e "BACKUP_UID=$(id -u)" -e "BACKUP_GID=$(id -g)" alpine:3.22 \
  sh -c 'umask 077; tar -C /source -czf "/backup/$BACKUP_FILE" . && chown "$BACKUP_UID:$BACKUP_GID" "/backup/$BACKUP_FILE"'
docker compose start viewer >/dev/null
trap - EXIT
echo "STATE_BACKUP=$destination/$name"
