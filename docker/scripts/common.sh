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
