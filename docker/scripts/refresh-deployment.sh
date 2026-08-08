#!/usr/bin/env bash
set -euo pipefail
umask 077

SOURCE_ROOT=$(CDPATH='' cd "$(dirname "$0")/../.." && pwd)
TARGET_ROOT=${OPENCLAW_ROOT:-/srv/openclaw}
DEPLOYMENT="$TARGET_ROOT/deployment"

if [[ ${EUID:-$(id -u)} -eq 0 ]]; then
  echo "Bitte als normaler Benutzer ausfuehren, nicht mit sudo/root." >&2
  exit 2
fi
[[ -d "$DEPLOYMENT" ]] || {
  echo "Deployment-Verzeichnis fehlt: $DEPLOYMENT" >&2
  echo "Fuehre zuerst setup-host.sh aus." >&2
  exit 2
}

mkdir -p "$DEPLOYMENT/scripts" "$DEPLOYMENT/hooks"
install -m 600 "$SOURCE_ROOT/compose.yaml" "$DEPLOYMENT/compose.yaml"
install -m 600 "$SOURCE_ROOT/docker/deployment.env.example" "$DEPLOYMENT/.env.example"
install -m 600 "$SOURCE_ROOT/docker/openclaw-plugins/contract.json" \
  "$DEPLOYMENT/immutable-plugins.json"
install -m 700 "$SOURCE_ROOT/docker/scripts/"*.sh "$DEPLOYMENT/scripts/"
install -m 700 "$SOURCE_ROOT/docker/scripts/"*.py "$DEPLOYMENT/scripts/"
install -m 700 "$SOURCE_ROOT/personal_assistant/immutable_plugins.py" \
  "$DEPLOYMENT/scripts/immutable_plugins.py"

# Only refresh examples. Active local hooks and .env are administrator state and
# must never be overwritten by a source update.
install -m 600 "$SOURCE_ROOT/docker/hooks/pre-deploy.example.sh" \
  "$DEPLOYMENT/hooks/pre-deploy.example.sh"
install -m 600 "$SOURCE_ROOT/docker/hooks/restore.example.sh" \
  "$DEPLOYMENT/hooks/restore.example.sh"

printf 'Deployment-Bundle aktualisiert: %s\n' "$DEPLOYMENT"
printf 'Erhalten: %s/.env sowie aktive Hook-Dateien ohne .example.sh\n' "$DEPLOYMENT"
