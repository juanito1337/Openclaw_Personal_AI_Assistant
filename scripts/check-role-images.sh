#!/usr/bin/env bash
set -euo pipefail
umask 077
root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)

runtime=${1:?Runtime-Image angeben}
proxy=${2:?Proxy-Image angeben}
maintenance=${3:?Maintenance-Image angeben}
revision=${4:?Git-Revision angeben}
release=3.4.0-r28

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
  test "${OPENCLAW_NIX_MODE:-}" = 1
  test "$(node -p '"'"'require("/opt/openclaw-plugins/node_modules/@openclaw/brave-plugin/package.json").version'"'"')" = 2026.7.1
  test "$(node -p '"'"'require("/opt/openclaw-plugins/node_modules/@openclaw/signal/package.json").version'"'"')" = 2026.7.1
  test "$(readlink /opt/openclaw-plugins/node_modules/openclaw)" = /app
  test "$(readlink /opt/openclaw-plugins/node_modules/@openclaw/brave-plugin/node_modules/openclaw)" = /app
  test "$(readlink /opt/openclaw-plugins/node_modules/@openclaw/signal/node_modules/openclaw)" = /app
  test ! -w /opt/openclaw-plugins/node_modules/@openclaw/brave-plugin
  test ! -w /opt/openclaw-plugins/node_modules/@openclaw/signal
  test -s /opt/openclaw-plugins/personal-assistant-tools/generated-tools.json
  test -s /opt/openclaw-plugins/personal-assistant-tools/openclaw.plugin.json
  test -s /opt/openclaw-plugins/personal-assistant-tools/runtime.js
  test ! -w /opt/openclaw-plugins/personal-assistant-tools
  node --check /opt/openclaw-plugins/personal-assistant-tools/runtime.js
  node --check /opt/openclaw-plugins/personal-assistant-tools/index.js
  python3 -P -c "from mail_agent.nextcloud import NextcloudSkillClient; assert NextcloudSkillClient"
  test -s /opt/openclaw-agent/personal_assistant/connectors/nextcloud/client.py
  test -s /opt/openclaw-agent/personal_assistant/connectors/nextcloud/calendar.py
  test -s /opt/openclaw-agent/personal_assistant/connectors/nextcloud/contacts.py
  test ! -e /opt/openclaw-agent/skills/openclaw-nextcloud
  test "$(sha256sum /usr/local/libexec/openclaw/himalaya | cut -d" " -f1)" = 9529d2584add1c4343f32524e6f985e7c98d491f3b854747318020eb1ec1df7f
  test "$(himalaya --version)" = "$(/usr/local/libexec/openclaw/himalaya --version)"
  guard_output=$(himalaya envelope list --account synthetic 2>&1) && exit 1 || guard_status=$?
  test "$guard_status" = 64
  case "$guard_output" in
    *"assistant.sh mail search"*"Keine Maildaten mit grep"*) ;;
    *) printf "%s\n" "$guard_output" >&2; exit 1 ;;
  esac
  test ! -e /opt/openclaw-agent/tests
  test ! -e /opt/openclaw-agent/docs
  test ! -e /opt/openclaw-agent/docker/scripts
  test ! -e /opt/openclaw-agent/legacy
'
docker run --rm --network none \
  --entrypoint /opt/openclaw-agent/scripts/assistant.sh "$runtime" --help >/dev/null
docker run --rm --network none --entrypoint /opt/openclaw-agent/scripts/assistant.sh "$runtime" version --verify >/dev/null

plugin_config="$root/tests/fixtures/container/immutable-plugins-openclaw.json"
for plugin in brave signal personal-assistant-tools; do
  docker run --rm --network none --read-only \
    --cap-drop ALL \
    --security-opt no-new-privileges:true \
    --tmpfs /tmp:rw,nosuid,nodev,noexec,size=32m,mode=1777 \
    --tmpfs /home/node/.openclaw:rw,nosuid,nodev,noexec,size=32m,mode=0700,uid=1000,gid=1000 \
    --mount "type=bind,src=$plugin_config,dst=/home/node/.openclaw/openclaw.json,readonly" \
    --entrypoint openclaw \
    "$runtime" plugins inspect "$plugin" --runtime --json >/dev/null
done

# Reproduce the native-to-container failure path with a legacy generated plugin
# index. The gateway must rewrite only that index, remain fully offline and
# become ready without invoking npm or mutating its read-only image payload.
(
  gateway_volume="openclaw-plugin-gateway-role-smoke-$$"
  gateway_container="${gateway_volume}-gateway"
  # Invoked indirectly by the EXIT trap below.
  # shellcheck disable=SC2329
  cleanup_gateway_smoke() {
    docker rm -f "$gateway_container" >/dev/null 2>&1 || true
    docker volume rm -f "$gateway_volume" >/dev/null 2>&1 || true
  }
  trap cleanup_gateway_smoke EXIT
  docker volume create "$gateway_volume" >/dev/null
  docker run --rm --network none \
    --mount "type=volume,src=$gateway_volume,dst=/home/node/.openclaw" \
    --mount "type=bind,src=$root/tests/fixtures/container/init-legacy-plugin-index.py,dst=/fixture/init.py,readonly" \
    --entrypoint python3 \
    "$runtime" /fixture/init.py
  docker run -d \
    --name "$gateway_container" \
    --network none \
    --read-only \
    --cap-drop ALL \
    --security-opt no-new-privileges:true \
    --tmpfs /tmp:rw,nosuid,nodev,noexec,size=64m,mode=1777 \
    --tmpfs /var/lib/openclaw:rw,nosuid,nodev,noexec,size=64m,mode=0700,uid=1000,gid=1000 \
    --env OPENCLAW_ROLE=gateway \
    --env OPENCLAW_LAYOUT_MODE=verify \
    --env OPENCLAW_EVENT_QUEUE_DIR=/var/lib/openclaw/gateway-events \
    --mount "type=volume,src=$gateway_volume,dst=/home/node/.openclaw" \
    --mount "type=bind,src=$plugin_config,dst=/home/node/.openclaw/openclaw.json,readonly" \
    --mount "type=bind,src=$root/tests/fixtures/container/layout-v3.json,dst=/home/node/.openclaw/workspace/.layout-version.json,readonly" \
    --mount "type=bind,src=$root/tests/fixtures/container/empty.env.example,dst=/run/openclaw-env/mail-agent.env,readonly" \
    --mount "type=bind,src=$root/tests/fixtures/container/empty.env.example,dst=/run/openclaw-env/personal-assistant.env,readonly" \
    --mount "type=bind,src=$root/tests/fixtures/container/gateway.env.example,dst=/run/openclaw-env/gateway.env,readonly" \
    "$runtime" >/dev/null
  gateway_ready=0
  for _ in $(seq 1 60); do
    if docker exec "$gateway_container" \
      /opt/openclaw-agent/docker/healthcheck.sh gateway >/dev/null 2>&1; then
      gateway_ready=1
      break
    fi
    if [[ $(docker inspect -f '{{.State.Running}}' "$gateway_container") != true ]]; then
      break
    fi
    sleep 2
  done
  if [[ $gateway_ready != 1 ]]; then
    echo "Immutable Plugin-Gateway wurde nicht bereit." >&2
    docker top "$gateway_container" -eo pid,args >&2 || true
    docker logs "$gateway_container" >&2 || true
    exit 1
  fi
  docker exec "$gateway_container" \
    python3 -P -m personal_assistant.gateway_events enqueue \
    --text "Hermetischer Gateway-Relay-Smoke" >/dev/null
  event_delivered=0
  for _ in $(seq 1 30); do
    if docker exec "$gateway_container" /bin/sh -c \
      'test -z "$(find /var/lib/openclaw/gateway-events/pending -name "*.json" -type f -print -quit)"' && \
      docker exec "$gateway_container" \
        python3 -P -m personal_assistant.gateway_events status >/dev/null 2>&1; then
      event_delivered=1
      break
    fi
    sleep 1
  done
  if [[ $event_delivered != 1 ]]; then
    echo "Gateway-lokaler Ereignisrelay konnte den Smoke-Event nicht zustellen." >&2
    docker logs "$gateway_container" >&2 || true
    exit 1
  fi
  gateway_logs=$(docker logs "$gateway_container" 2>&1)
  if grep -Eq 'npm view|Failed to update|permission denied|EACCES' <<<"$gateway_logs"; then
    echo "Gateway versuchte eine unzulaessige Pluginmutation zur Laufzeit." >&2
    printf '%s\n' "$gateway_logs" >&2
    exit 1
  fi
  docker exec "$gateway_container" python3 -P -c '
import json
import sqlite3
from pathlib import Path

config = json.loads(Path("/home/node/.openclaw/openclaw.json").read_text())
assert config["tools"]["fs"]["workspaceOnly"] is True
connection = sqlite3.connect("/home/node/.openclaw/state/openclaw.sqlite")
row = connection.execute(
    "SELECT install_records_json FROM installed_plugin_index WHERE index_key = ?",
    ("installed-plugin-index",),
).fetchone()
assert row is not None
records = json.loads(row[0])
assert records["brave"]["installPath"] == "/opt/openclaw-plugins/node_modules/@openclaw/brave-plugin"
assert records["signal"]["installPath"] == "/opt/openclaw-plugins/node_modules/@openclaw/signal"
assert records["brave"]["resolvedVersion"] == "2026.7.1"
assert records["signal"]["resolvedVersion"] == "2026.7.1"
assert connection.execute("PRAGMA quick_check").fetchone() == ("ok",)
'
)

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
# GitHub's host UID differs from the fixed image UID 1000. These three files
# contain public fixtures only; make their read-only bind mounts traversable
# without relaxing any productive config or secret permission.
chmod 0755 "$smoke_root" "$smoke_root/upstream" "$smoke_root/upstream/api" "$smoke_root/workspace"
chmod 0444 \
  "$smoke_root/upstream/api/version" \
  "$smoke_root/workspace/.layout-version.json" \
  "$smoke_root/ollama-priority.env"
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
  --network-alias ollama-proxy \
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
# The supervisor is a client on the private backend network and intentionally
# has neither the proxy's upstream configuration nor a direct egress route.
# Its registered status and live-check commands must therefore query the fixed
# service endpoint instead of attempting to construct another proxy server
# configuration.
docker run --rm \
  --network "$smoke_network" \
  --read-only \
  --cap-drop ALL \
  --security-opt no-new-privileges:true \
  --tmpfs /tmp:rw,nosuid,nodev,noexec,size=16m,mode=1777 \
  --env OPENCLAW_ROLE=supervisor-worker \
  --entrypoint /opt/openclaw-agent/scripts/assistant.sh \
  "$runtime" ollama status >/dev/null
docker run --rm \
  --network "$smoke_network" \
  --read-only \
  --cap-drop ALL \
  --security-opt no-new-privileges:true \
  --tmpfs /tmp:rw,nosuid,nodev,noexec,size=16m,mode=1777 \
  --env OPENCLAW_ROLE=gateway \
  --entrypoint /opt/openclaw-agent/scripts/assistant.sh \
  "$runtime" ollama check >/dev/null
cleanup_proxy_smoke
trap - EXIT

docker run --rm --network none --entrypoint python3 "$maintenance" -P -c '
import personal_assistant.clamav_health
import personal_assistant.clamav_transport
from pathlib import Path
assert Path("/usr/bin/freshclam").is_file()
assert Path("/usr/bin/clamscan").is_file()
assert not Path("/app").exists()
assert not Path("/usr/local/bin/openclaw").exists()
assert not Path("/usr/local/bin/himalaya").exists()
assert not Path("/usr/bin/tesseract").exists()
'
"$root/docker/scripts/check-maintenance-runtime.sh" "$maintenance"
echo "M7 role smoke tests successful."
