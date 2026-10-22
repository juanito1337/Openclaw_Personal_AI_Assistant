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
install -m 700 "$SOURCE_ROOT/personal_assistant/ollama_priority_config.py" \
  "$DEPLOYMENT/scripts/ollama_priority_config.py"

# Compose bind-mounts an exact file. Existing installations receive only an
# empty, disabled credential slot; no bot, channel or runtime is activated.
install -d -m 700 "$TARGET_ROOT/secrets"
talk_secret="$TARGET_ROOT/secrets/nextcloud-talk-bot-secret"
if [[ ! -e "$talk_secret" ]]; then
  install -m 600 /dev/null "$talk_secret"
elif [[ -L "$talk_secret" || ! -f "$talk_secret" ]]; then
  echo "Ungueltiger Nextcloud-Talk-Secretpfad: $talk_secret" >&2
  exit 2
else
  chmod 600 "$talk_secret"
fi

# Preserve existing PKI, create empty slots only. In particular never place the
# exported public CA in config/ca: trust import remains an explicit operation.
for directory in "$TARGET_ROOT/secrets/talk-proxy-pki" "$TARGET_ROOT/config/talk-proxy-public"; do
  if [[ -L "$directory" || ( -e "$directory" && ! -d "$directory" ) ]]; then
    echo "Ungueltiger Talk-Proxy-Verzeichnispfad: $directory" >&2
    exit 2
  fi
  install -d -m 700 "$directory"
  if [[ $(stat -c '%u:%g' "$directory") != 1000:1000 ]]; then
    chown 1000:1000 "$directory" 2>/dev/null || sudo chown 1000:1000 "$directory"
  fi
done

# Creating the private slot does not log in or activate any account or room.
connection_directory="$TARGET_ROOT/secrets/nextcloud-connection"
if [[ -L "$connection_directory" || ( -e "$connection_directory" && ! -d "$connection_directory" ) ]]; then
  echo "Ungueltiger Nextcloud-Verbindungsstore." >&2
  exit 2
fi
install -d -m 700 "$connection_directory"
if [[ $(stat -c '%u:%g' "$connection_directory") != 1000:1000 ]]; then
  chown 1000:1000 "$connection_directory" 2>/dev/null || sudo chown 1000:1000 "$connection_directory"
fi

# Only refresh examples. Active local hooks and .env are administrator state and
# must never be overwritten by a source update.
install -m 600 "$SOURCE_ROOT/docker/hooks/pre-deploy.example.sh" \
  "$DEPLOYMENT/hooks/pre-deploy.example.sh"
install -m 600 "$SOURCE_ROOT/docker/hooks/restore.example.sh" \
  "$DEPLOYMENT/hooks/restore.example.sh"

printf 'Deployment-Bundle aktualisiert: %s\n' "$DEPLOYMENT"
printf 'Erhalten: %s/.env sowie aktive Hook-Dateien ohne .example.sh\n' "$DEPLOYMENT"
