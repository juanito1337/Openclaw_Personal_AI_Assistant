#!/usr/bin/env bash
set -euo pipefail
umask 077

runtime=${1:?Runtime-Image angeben}
proxy=${2:?Proxy-Image angeben}
maintenance=${3:?Maintenance-Image angeben}
revision=${4:?Git-Revision angeben}
release=3.4.0-r27.2.5

verify_labels() {
  local image=$1 role=$2 actual
  actual=$(docker image inspect --format '{{index .Config.Labels "org.opencontainers.image.openclaw.role"}} {{index .Config.Labels "org.opencontainers.image.version"}} {{index .Config.Labels "org.opencontainers.image.revision"}}' "$image")
  [[ "$actual" == "$role $release $revision" ]] || {
    echo "Unerwartete OCI-Identitaet fuer $image: $actual" >&2
    return 1
  }
}

verify_labels "$runtime" runtime
verify_labels "$proxy" proxy
verify_labels "$maintenance" maintenance

docker run --rm --network none --entrypoint /bin/sh "$runtime" -c '
  command -v openclaw
  command -v himalaya
  command -v pdftotext
  command -v tesseract
  command -v clamscan
  test "$(node -p '"'"'require("/usr/local/lib/node_modules/npm/node_modules/tar/package.json").version'"'"')" = 7.5.19
  test "$(sha256sum /usr/local/bin/himalaya | cut -d" " -f1)" = 9529d2584add1c4343f32524e6f985e7c98d491f3b854747318020eb1ec1df7f
  test ! -e /opt/openclaw-agent/tests
  test ! -e /opt/openclaw-agent/docs
  test ! -e /opt/openclaw-agent/docker/scripts
  test ! -e /opt/openclaw-agent/legacy
'
docker run --rm --network none \
  --entrypoint /opt/openclaw-agent/scripts/assistant.sh "$runtime" --help >/dev/null
docker run --rm --network none --entrypoint /opt/openclaw-agent/scripts/assistant.sh "$runtime" version --verify >/dev/null

docker run --rm --network none --entrypoint python3 "$proxy" -P -c '
import personal_assistant.ollama_priority_proxy
from pathlib import Path
assert not Path("/app").exists()
assert not Path("/usr/local/bin/openclaw").exists()
assert not Path("/usr/local/bin/himalaya").exists()
assert not Path("/usr/bin/tesseract").exists()
assert not Path("/usr/bin/clamscan").exists()
'

# Exercise the real proxy entrypoint, container-only listener normalization and
# upstream-aware healthcheck on a private fixture network. Import-only role
# checks cannot detect a contradictory runtime bind policy.
smoke_root=$(mktemp -d)
smoke_network="openclaw-proxy-role-smoke-$$"
smoke_upstream="${smoke_network}-upstream"
smoke_proxy="${smoke_network}-proxy"
cleanup_proxy_smoke() {
  docker rm -f "$smoke_proxy" "$smoke_upstream" >/dev/null 2>&1 || true
  docker network rm "$smoke_network" >/dev/null 2>&1 || true
  rm -rf -- "$smoke_root"
}
trap cleanup_proxy_smoke EXIT
mkdir -p "$smoke_root/upstream/api" "$smoke_root/workspace"
printf '%s\n' '{"version":"role-smoke"}' > "$smoke_root/upstream/api/version"
printf '%s\n' '{"layout":3}' > "$smoke_root/workspace/.layout-version.json"
printf '%s\n' \
  "OLLAMA_PRIORITY_UPSTREAM=http://$smoke_upstream:11434" \
  'OLLAMA_PRIORITY_LISTEN_HOST=127.0.0.1' \
  'OLLAMA_PRIORITY_LISTEN_PORT=11435' \
  > "$smoke_root/ollama-priority.env"
docker network create --internal "$smoke_network" >/dev/null
docker run -d \
  --name "$smoke_upstream" \
  --network "$smoke_network" \
  --read-only \
  --cap-drop ALL \
  --security-opt no-new-privileges:true \
  --mount "type=bind,src=$smoke_root/upstream,dst=/fixture,readonly" \
  --workdir /fixture \
  --entrypoint python3 \
  "$proxy" -m http.server 11434 --bind 0.0.0.0 >/dev/null
docker run -d \
  --name "$smoke_proxy" \
  --network "$smoke_network" \
  --read-only \
  --cap-drop ALL \
  --security-opt no-new-privileges:true \
  --tmpfs /tmp:rw,nosuid,nodev,noexec,size=16m,mode=1777 \
  --env OPENCLAW_ROLE=ollama-proxy \
  --env OPENCLAW_LAYOUT_MODE=verify \
  --mount "type=bind,src=$smoke_root/workspace,dst=/home/node/.openclaw/workspace,readonly" \
  --mount "type=bind,src=$smoke_root/ollama-priority.env,dst=/etc/openclaw-env/ollama-priority.env,readonly" \
  "$proxy" >/dev/null
proxy_healthy=false
for _ in $(seq 1 30); do
  if docker exec "$smoke_proxy" /opt/openclaw-agent/docker/healthcheck.sh proxy >/dev/null 2>&1; then
    proxy_healthy=true
    break
  fi
  if [[ "$(docker inspect --format '{{.State.Running}}' "$smoke_proxy" 2>/dev/null || true)" != "true" ]]; then
    break
  fi
  sleep 1
done
if [[ "$proxy_healthy" != "true" ]]; then
  echo "Proxy-Rollenstart oder Upstream-Healthcheck fehlgeschlagen." >&2
  docker logs "$smoke_proxy" >&2 || true
  exit 1
fi
cleanup_proxy_smoke
trap - EXIT

docker run --rm --network none --entrypoint python3 "$maintenance" -P -c '
import personal_assistant.clamav_health
from pathlib import Path
assert Path("/usr/bin/freshclam").is_file()
assert Path("/usr/bin/clamscan").is_file()
assert not Path("/app").exists()
assert not Path("/usr/local/bin/openclaw").exists()
assert not Path("/usr/local/bin/himalaya").exists()
assert not Path("/usr/bin/tesseract").exists()
'
echo "M7 role smoke tests successful."
