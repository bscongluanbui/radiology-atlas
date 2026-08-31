#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
source ./common.sh
connector="${TUNNEL_CONTAINER:-thirsty_agnesi}"
image="$(docker inspect --format '{{.Config.Image}}' "$connector")"
case "$image" in cloudflare/cloudflared:*|cloudflare/cloudflared@*|docker.io/cloudflare/cloudflared:*) ;;
  *) echo "TUNNEL=FAIL; $connector is not a cloudflared container." >&2; exit 2;;
esac
test "$(docker inspect --format '{{.State.Running}}' "$connector")" = true || { echo 'TUNNEL=FAIL; start the existing connector first.' >&2; exit 2; }
network=radiology-atlas_backend
docker network inspect "$network" >/dev/null
attached="$(docker inspect --format '{{range $k, $v := .NetworkSettings.Networks}}{{println $k}}{{end}}' "$connector")"
if ! grep -Fxq "$network" <<< "$attached"; then
  docker network connect "$network" "$connector"
fi
# Do not disconnect bridge: the connector still needs outbound Internet access.
echo "TUNNEL=PASS; container=$connector; network=$network; upstream=http://viewer:8080"
