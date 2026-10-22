#!/usr/bin/env bash
set -euo pipefail
umask 077

SCRIPT_DIR=$(CDPATH='' cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
DEPLOYMENT_DIR=$(CDPATH='' cd "$SCRIPT_DIR/.." && pwd)
ENV_FILE=${OPENCLAW_DEPLOY_ENV:-$DEPLOYMENT_DIR/.env}
COMPOSE_FILE=${OPENCLAW_COMPOSE_FILE:-$DEPLOYMENT_DIR/compose.yaml}

if [[ -f "$ENV_FILE" ]]; then
  set -a
  # Administrator-controlled deployment variables.
  # shellcheck disable=SC1090
  . "$ENV_FILE"
  set +a
fi

OPENCLAW_ROOT=${OPENCLAW_ROOT:-/srv/openclaw}
OPENCLAW_STATE_DIR=${OPENCLAW_STATE_DIR:-$OPENCLAW_ROOT/state}
OPENCLAW_CONFIG_DIR=${OPENCLAW_CONFIG_DIR:-$OPENCLAW_ROOT/config}
OPENCLAW_SECRETS_DIR=${OPENCLAW_SECRETS_DIR:-$OPENCLAW_ROOT/secrets}
OPENCLAW_BACKUP_DIR=${OPENCLAW_BACKUP_DIR:-$OPENCLAW_ROOT/backups/releases}
HIMALAYA_CONFIG_DIR=${HIMALAYA_CONFIG_DIR:-$OPENCLAW_CONFIG_DIR/himalaya}
export OPENCLAW_ROOT OPENCLAW_STATE_DIR OPENCLAW_CONFIG_DIR OPENCLAW_SECRETS_DIR OPENCLAW_BACKUP_DIR HIMALAYA_CONFIG_DIR

compose() {
  docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" "$@"
}

# The administrative credential owner is a writer even while domain jobs are
# disabled. Exact Docker state proves that backup/restore cannot race a login.
setup_service_is_offline() {
  local state
  state=$(docker container ls --all --filter 'name=^/openclaw-setup-service$' \
    --format '{{.State}}') || return 1
  case "$state" in
    ''|created|exited|dead) return 0 ;;
    *) return 1 ;;
  esac
}

check_nextcloud_nonce_integrity() {
  local directory=$1 database result
  if [[ -L "$directory" || ( -e "$directory" && ! -d "$directory" ) ]]; then
    echo "Ungueltiger Nextcloud-Verbindungsstore." >&2
    return 2
  fi
  database="$directory/operator-nonces.sqlite3"
  if [[ ! -e "$database" && ! -L "$database" ]]; then
    return 0
  fi
  if [[ -L "$database" || ! -f "$database" ]]; then
    echo "Ungueltige Nextcloud-Setup-Replaydatenbank." >&2
    return 2
  fi
  result=$(sqlite3 -readonly "$database" 'PRAGMA quick_check;' 2>/dev/null) || {
    echo "Nextcloud-Setup-Replaydatenbank konnte nicht verifiziert werden." >&2
    return 1
  }
  [[ "$result" == ok ]] || {
    echo "Nextcloud-Setup-Replaydatenbank ist nicht integer." >&2
    return 1
  }
}

ensure_nextcloud_connection_directory() {
  local directory="$OPENCLAW_SECRETS_DIR/nextcloud-connection"
  if [[ -L "$directory" || ( -e "$directory" && ! -d "$directory" ) ]]; then
    echo "Ungueltiger Nextcloud-Verbindungsstore." >&2
    return 2
  fi
  install -d -m 700 "$directory"
  if [[ $(stat -c '%u:%g' "$directory") != 1000:1000 ]]; then
    chown 1000:1000 "$directory" 2>/dev/null || sudo chown 1000:1000 "$directory"
  fi
}

start_setup_service_if_supported() {
  local supported
  supported=$(docker image inspect --format \
    '{{index .Config.Labels "org.opencontainers.image.openclaw.setup-owner"}}' \
    "$OPENCLAW_IMAGE") || return 1
  case "$supported" in
    1)
      compose up -d --no-deps setup-service || return 1
      wait_for_healthy setup-service 60 || return 1
      ;;
    ''|'<no value>')
      # An older verified image has no owner module; its gateway still starts
      # with --no-deps during rollback. Never execute new code in an old image.
      return 0 ;;
    *) echo "Unbekannter Setup-Owner-Imagevertrag." >&2; return 1 ;;
  esac
}

# Talk is opt-in independently of the gateway. Never infer activation from a
# Compose profile or from the presence of a CA directory.
validate_talk_proxy_flag() {
  case "${1:-false}" in
    true|false) return 0 ;;
    *) echo "OPENCLAW_TALK_PROXY_ENABLED muss true oder false sein." >&2; return 2 ;;
  esac
}

validate_talk_proxy_addresses() {
  python3 - "$1" "$2" <<'PY'
import ipaddress, sys
networks = tuple(ipaddress.ip_network(value) for value in (
    "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "fc00::/7",
))
addresses = []
for key, value in zip(("OPENCLAW_TALK_PROXY_IP", "OPENCLAW_TALK_NEXTCLOUD_IP"), sys.argv[1:]):
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        raise SystemExit(f"{key} muss eine feste private IP-Adresse sein.") from None
    if "%" in value or not any(address in network for network in networks):
        raise SystemExit(f"{key} muss eine feste private IP-Adresse sein.")
    addresses.append(address)
if addresses[0] == addresses[1]:
    raise SystemExit("Talk-Proxy und Nextcloud muessen unterschiedliche IP-Adressen verwenden.")
PY
}

talk_proxy_container_address() {
  local key=$1
  case "$key" in
    OPENCLAW_TALK_PROXY_IP|OPENCLAW_TALK_NEXTCLOUD_IP) ;;
    *) echo "Unbekannter oeffentlicher Talk-Konfigurationsschluessel." >&2; return 2 ;;
  esac
  # Select only one named, non-secret address inside Docker's template. Never
  # print or capture the complete environment (which may contain credentials).
  docker container inspect --format \
    "{{range .Config.Env}}{{if eq (index (split . \"=\") 0) \"$key\"}}{{println (index (split . \"=\") 1)}}{{end}}{{end}}" \
    openclaw-talk-proxy
}

restore_talk_proxy_configuration() {
  local enabled=$1 address=${2:-} nextcloud=${3:-}
  validate_talk_proxy_flag "$enabled"
  if [[ "$enabled" == true ]]; then
    validate_talk_proxy_addresses "$address" "$nextcloud"
    update_env_value OPENCLAW_TALK_PROXY_IP "$address"
    update_env_value OPENCLAW_TALK_NEXTCLOUD_IP "$nextcloud"
    export OPENCLAW_TALK_PROXY_IP="$address" OPENCLAW_TALK_NEXTCLOUD_IP="$nextcloud"
  fi
  update_env_value OPENCLAW_TALK_PROXY_ENABLED "$enabled"
  export OPENCLAW_TALK_PROXY_ENABLED="$enabled"
}

talk_proxy_container_state() {
  local state
  # --all and the exact state avoid overlooking restart-policy activity or a
  # paused writer. Absence is distinct from a failed Docker API request.
  state=$(docker container ls --all --filter 'name=^/openclaw-talk-proxy$' \
    --format '{{.State}}') || return 1
  case "$state" in
    '') printf 'absent\n' ;;
    created|running|restarting|paused|exited|dead|removing) printf '%s\n' "$state" ;;
    *) echo "Talk-Proxy-Zustand ist nicht eindeutig nachgewiesen." >&2; return 1 ;;
  esac
}

talk_proxy_running_state() {
  local state
  state=$(talk_proxy_container_state) || return 1
  case "$state" in
    running|restarting) printf 'true\n' ;;
    absent|created|exited|dead) printf 'false\n' ;;
    *)
      # A paused service must not silently become unpaused through recovery.
      echo "Talk-Proxy ist pausiert oder wird entfernt; vor Deployment explizit klaeren." >&2
      return 1 ;;
  esac
}

talk_proxy_is_offline() {
  local state
  state=$(talk_proxy_container_state) || return 1
  case "$state" in
    absent|created|exited|dead) return 0 ;;
    *) return 1 ;;
  esac
}

verify_talk_proxy_image() {
  local image=$1 supported
  supported=$(docker image inspect --format \
    '{{index .Config.Labels "org.opencontainers.image.openclaw.talk-proxy"}}' "$image") || return 1
  [[ "$supported" == "1" ]] || {
    echo "Proxy-Image unterstuetzt den integrierten Talk-Proxy nicht: $image" >&2
    return 1
  }
}

start_talk_proxy() {
  compose --profile talk up -d --no-deps talk-proxy || return 1
  wait_for_healthy talk-proxy 180
}

require_command() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "Erforderlicher Befehl fehlt: $1" >&2
    exit 2
  }
}

update_env_value() {
  local key=$1 value=$2
  python3 - "$ENV_FILE" "$key" "$value" <<'PY'
from pathlib import Path
import sys
path=Path(sys.argv[1])
key=sys.argv[2]
value=sys.argv[3]
lines=path.read_text(encoding="utf-8").splitlines() if path.exists() else []
out=[]
found=False
for line in lines:
    if line.startswith(key + "="):
        out.append(f"{key}={value}")
        found=True
    else:
        out.append(line)
if not found:
    out.append(f"{key}={value}")
path.write_text("\n".join(out) + "\n", encoding="utf-8")
PY
}

wait_for_healthy() {
  local service=$1 timeout=${2:-180} elapsed=0 id state
  while (( elapsed < timeout )); do
    id=$(compose ps -q "$service" 2>/dev/null || true)
    if [[ -n "$id" ]]; then
      state=$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$id" 2>/dev/null || true)
      if [[ "$state" == "healthy" || "$state" == "running" ]]; then
        return 0
      fi
      if [[ "$state" == "unhealthy" || "$state" == "exited" || "$state" == "dead" ]]; then
        echo "$service ist $state" >&2
        compose logs --tail=120 "$service" >&2 || true
        return 1
      fi
    fi
    sleep 5
    elapsed=$((elapsed + 5))
  done
  echo "Healthcheck-Zeitlimit fuer $service ueberschritten" >&2
  compose logs --tail=120 "$service" >&2 || true
  return 1
}
