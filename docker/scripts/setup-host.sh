#!/usr/bin/env bash
set -euo pipefail

SOURCE_ROOT=$(CDPATH='' cd "$(dirname "$0")/../.." && pwd)
TARGET_ROOT=${OPENCLAW_ROOT:-/srv/openclaw}
DEPLOYMENT="$TARGET_ROOT/deployment"

if [[ ${EUID:-$(id -u)} -ne 0 || -z ${SUDO_USER:-} ]]; then
  echo "Bitte mit sudo als normal angemeldeter Benutzer ausfuehren: sudo $0" >&2
  exit 2
fi
OWNER=$SUDO_USER

command -v docker >/dev/null 2>&1 || {
  echo "Docker ist nicht installiert. Installiere Docker Engine mit Compose-Plugin und starte das Skript erneut." >&2
  exit 2
}
docker compose version >/dev/null 2>&1 || {
  echo "Das Docker-Compose-Plugin fehlt." >&2
  exit 2
}
for command in sqlite3 rsync tar sha256sum python3; do
  command -v "$command" >/dev/null 2>&1 || {
    echo "Host-Werkzeug fehlt: $command" >&2
    echo "Ubuntu: sudo apt install sqlite3 rsync coreutils tar python3" >&2
    exit 2
  }
done
sudo -u "$OWNER" -H docker info >/dev/null 2>&1 || {
  echo "Der Benutzer $OWNER darf Docker noch nicht verwenden." >&2
  echo "Fuege ihn zur docker-Gruppe hinzu und melde dich danach neu an:" >&2
  echo "  sudo usermod -aG docker $OWNER" >&2
  exit 2
}

sudo install -d -m 700 "$TARGET_ROOT/state" "$TARGET_ROOT/config/himalaya" "$TARGET_ROOT/config/ca" \
  "$TARGET_ROOT/secrets" "$TARGET_ROOT/backups/releases" "$TARGET_ROOT/backups/migration" \
  "$DEPLOYMENT/scripts" "$DEPLOYMENT/hooks"
sudo cp "$SOURCE_ROOT/compose.yaml" "$DEPLOYMENT/compose.yaml"
sudo cp "$SOURCE_ROOT/docker/deployment.env.example" "$DEPLOYMENT/.env.example"
sudo cp "$SOURCE_ROOT/docker/openclaw-plugins/contract.json" \
  "$DEPLOYMENT/immutable-plugins.json"
sudo cp "$SOURCE_ROOT/docker/scripts/"*.sh "$DEPLOYMENT/scripts/"
sudo cp "$SOURCE_ROOT/docker/scripts/"*.py "$DEPLOYMENT/scripts/"
sudo cp "$SOURCE_ROOT/personal_assistant/immutable_plugins.py" \
  "$DEPLOYMENT/scripts/immutable_plugins.py"
sudo cp "$SOURCE_ROOT/docker/hooks/"*.sh "$DEPLOYMENT/hooks/"
if [[ ! -f "$DEPLOYMENT/.env" ]]; then
  sudo cp "$DEPLOYMENT/.env.example" "$DEPLOYMENT/.env"
fi
if [[ ! -f "$TARGET_ROOT/config/ollama-priority.env" ]]; then
  sudo cp "$SOURCE_ROOT/docker/config/ollama-priority.env.example" "$TARGET_ROOT/config/ollama-priority.env"
fi
if [[ ! -f "$TARGET_ROOT/config/mail-agent.env" ]]; then
  sudo cp "$SOURCE_ROOT/docker/config/mail-agent.env.example" "$TARGET_ROOT/config/mail-agent.env"
fi
if [[ ! -f "$TARGET_ROOT/config/personal-assistant.env" ]]; then
  sudo cp "$SOURCE_ROOT/docker/config/personal-assistant.env.example" "$TARGET_ROOT/config/personal-assistant.env"
fi
for secret in gateway.env mail-agent.env personal-assistant.env \
  himalaya-imap-password himalaya-smtp-password; do
  if [[ ! -e "$TARGET_ROOT/secrets/$secret" ]]; then
    sudo install -m 600 /dev/null "$TARGET_ROOT/secrets/$secret"
  fi
done
sudo chmod 600 "$DEPLOYMENT/immutable-plugins.json"
sudo chmod 700 "$DEPLOYMENT/scripts/"*.sh "$DEPLOYMENT/scripts/"*.py "$DEPLOYMENT/hooks/"*.sh
sudo chmod 600 "$TARGET_ROOT/secrets/"*
sudo chown -R "$OWNER":"$OWNER" "$DEPLOYMENT" "$TARGET_ROOT/config" "$TARGET_ROOT/secrets" "$TARGET_ROOT/backups"
sudo chown -R 1000:1000 "$TARGET_ROOT/state" "$TARGET_ROOT/config/himalaya"

cat <<MSG
Hoststruktur erstellt: $TARGET_ROOT

Naechste Schritte:
1. $DEPLOYMENT/.env bearbeiten.
2. Externes Backup/Restore unter $DEPLOYMENT/hooks konfigurieren.
3. Bei privatem GHCR-Image: docker login ghcr.io
4. Bestehenden Live-Agenten migrieren:
   $DEPLOYMENT/scripts/migrate-live.sh --execute
MSG
