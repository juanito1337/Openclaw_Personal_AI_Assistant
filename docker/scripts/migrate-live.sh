#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(CDPATH= cd "$(dirname "$0")" && pwd)
# shellcheck source=common.sh
. "$SCRIPT_DIR/common.sh"

if [[ ${EUID:-$(id -u)} -eq 0 ]]; then
  echo "Bitte als normaler Benutzer mit Docker-Rechten ausfuehren, nicht mit sudo/root." >&2
  exit 2
fi

[[ ${1:-} == "--execute" ]] || {
  echo "Dieses Skript kopiert den Live-Zustand und deaktiviert die alten systemd-Writer." >&2
  echo "Aufruf nach Kontrolle: $0 --execute" >&2
  exit 2
}

SOURCE_HOME=${OPENCLAW_LIVE_HOME:-$HOME/.openclaw}
[[ -d "$SOURCE_HOME" ]] || { echo "Live-Zustand fehlt: $SOURCE_HOME" >&2; exit 2; }
require_command rsync
require_command tar
require_command python3
require_command sqlite3
require_command sha256sum

required_source_paths=(
  "$SOURCE_HOME/openclaw.json"
  "$SOURCE_HOME/workspace/scripts/assistant.sh"
  "$SOURCE_HOME/workspace/scripts/mail-agent.sh"
  "$SOURCE_HOME/workspace/mail_agent/config.toml"
  "$SOURCE_HOME/workspace/personal_assistant/config.toml"
)
for required in "${required_source_paths[@]}"; do
  [[ -e "$required" ]] || {
    echo "Migration abgebrochen: erforderlicher Legacy-Pfad fehlt: $required" >&2
    echo "Der bisherige systemd-Betrieb wurde nicht angehalten oder veraendert." >&2
    exit 2
  }
done
[[ -x "$SOURCE_HOME/workspace/scripts/assistant.sh" ]] || {
  echo "Migration abgebrochen: assistant.sh ist nicht ausfuehrbar." >&2
  exit 2
}
[[ -x "$SOURCE_HOME/workspace/scripts/mail-agent.sh" ]] || {
  echo "Migration abgebrochen: mail-agent.sh ist nicht ausfuehrbar." >&2
  exit 2
}
python3 - "$SOURCE_HOME/openclaw.json" <<'PY'
import json, sys
from pathlib import Path
path=Path(sys.argv[1])
data=json.loads(path.read_text(encoding="utf-8"))
if not isinstance(data, dict) or not isinstance(data.get("gateway"), dict):
    raise SystemExit("Migration abgebrochen: openclaw.json enthaelt keine Gateway-Konfiguration.")
if data["gateway"].get("mode") != "local":
    raise SystemExit("Migration abgebrochen: openclaw.json muss gateway.mode=local enthalten.")
PY

stamp=$(date -u +%Y%m%dT%H%M%SZ)
migration_backup="$OPENCLAW_ROOT/backups/migration/live-before-container-$stamp.tar.gz"
mkdir -p "$(dirname "$migration_backup")"
stage_root=$(mktemp -d "$OPENCLAW_ROOT/backups/migration/staging-$stamp.XXXXXX")
stage_state="$stage_root/state"
stage_config="$stage_root/config"
stage_secrets="$stage_root/secrets"
legacy_units_snapshot="$stage_root/legacy-active-units.txt"
legacy_gateway_environment="$stage_root/legacy-gateway-environment.txt"
mkdir -p "$stage_state" "$stage_config/himalaya" "$stage_secrets"
chmod 700 "$stage_root" "$stage_state" "$stage_config" "$stage_secrets"

units=(
  mail-agent.timer mail-agent.service
  personal-assistant-sync.timer personal-assistant-sync.service
  personal-assistant-supervisor.timer personal-assistant-supervisor.service
  personal-assistant-portfolio.timer personal-assistant-portfolio.service
  personal-assistant-monitor.timer personal-assistant-monitor.service
  ollama-priority-proxy.service openclaw-gateway.service
)
: > "$legacy_units_snapshot"
for unit in "${units[@]}"; do
  if systemctl --user is-active --quiet "$unit" || systemctl --user is-enabled --quiet "$unit"; then
    printf '%s\n' "$unit" >> "$legacy_units_snapshot"
  fi
done
systemctl --user show openclaw-gateway.service \
  --property=Environment --value > "$legacy_gateway_environment" 2>/dev/null || \
  : > "$legacy_gateway_environment"
chmod 600 "$legacy_gateway_environment"

cleanup_stage() {
  rm -rf -- "$stage_root"
}
trap cleanup_stage EXIT

for unit in "${units[@]}"; do
  systemctl --user disable --now "$unit" >/dev/null 2>&1 || true
done

restart_legacy_on_failure() {
  local code=$?
  trap - ERR
  echo "Migration fehlgeschlagen; aktiviere den bisherigen systemd-Betrieb erneut." >&2
  if [[ -s "$legacy_units_snapshot" ]]; then
    while IFS= read -r unit; do
      systemctl --user enable --now "$unit" >/dev/null 2>&1 || systemctl --user start "$unit" >/dev/null 2>&1 || true
    done < "$legacy_units_snapshot"
  fi
  exit "$code"
}
trap restart_legacy_on_failure ERR

if pgrep -af 'mail_agent|mail-agent|personal_assistant|openclaw.*gateway' >&2; then
  echo "Warnung: Es sind noch passende Prozesse sichtbar. Pruefe, dass kein alter Writer mehr aktiv ist." >&2
fi

mkdir -p "$OPENCLAW_STATE_DIR" "$OPENCLAW_CONFIG_DIR" "$OPENCLAW_SECRETS_DIR" "$HIMALAYA_CONFIG_DIR"
backup_paths=(.openclaw)
for path in .config/himalaya .config/openclaw .config/personal-assistant .config/mail-agent.env; do
  [[ -e "$HOME/$path" ]] && backup_paths+=("$path")
done
tar -C "$HOME" -czf "$migration_backup" "${backup_paths[@]}"
sha256sum "$migration_backup" > "$migration_backup.sha256"
sha256sum -c "$migration_backup.sha256" >/dev/null
tar -tzf "$migration_backup" .openclaw/workspace/scripts/assistant.sh >/dev/null

rsync -a --delete "$SOURCE_HOME/" "$stage_state/"
if [[ -d "$OPENCLAW_CONFIG_DIR" ]]; then
  rsync -a "$OPENCLAW_CONFIG_DIR/" "$stage_config/"
fi
if [[ -d "$OPENCLAW_SECRETS_DIR" ]]; then
  rsync -a "$OPENCLAW_SECRETS_DIR/" "$stage_secrets/"
fi
if [[ -d "$HOME/.config/himalaya" ]]; then
  rsync -a --delete "$HOME/.config/himalaya/" "$stage_config/himalaya/"
fi
if [[ -f "$HOME/.config/openclaw/ollama-priority.env" ]]; then
  cp "$HOME/.config/openclaw/ollama-priority.env" "$stage_config/ollama-priority.env"
fi
if [[ -f "$HOME/.config/mail-agent.env" ]]; then
  cp "$HOME/.config/mail-agent.env" "$stage_secrets/mail-agent.env"
fi
if [[ -f "$HOME/.config/personal-assistant/secrets.env" ]]; then
  cp "$HOME/.config/personal-assistant/secrets.env" "$stage_secrets/personal-assistant.env"
fi
install -m 600 "$legacy_units_snapshot" "$stage_config/legacy-active-units.txt"
chmod -R go-rwx "$stage_config" "$stage_secrets" || true

python3 "$SCRIPT_DIR/migrate-container-state.py" \
  --state-dir "$stage_state" \
  --config-dir "$stage_config" \
  --secrets-dir "$stage_secrets" \
  --source-workspace "$SOURCE_HOME/workspace" \
  --target-workspace "/home/node/.openclaw/workspace" \
  --enable-nextcloud-if-configured \
  --ensure-gateway-auth \
  --normalize-ollama-proxy \
  --legacy-gateway-environment-file "$legacy_gateway_environment"

staged_required_paths=(
  "$stage_state/openclaw.json"
  "$stage_state/workspace/scripts/assistant.sh"
  "$stage_state/workspace/scripts/mail-agent.sh"
  "$stage_state/workspace/mail_agent/config.toml"
  "$stage_state/workspace/personal_assistant/config.toml"
  "$stage_config/ollama-priority.env"
  "$stage_secrets/gateway.env"
)
for required in "${staged_required_paths[@]}"; do
  [[ -s "$required" ]] || {
    echo "Staging-Pruefung fehlgeschlagen: erforderliche Datei fehlt oder ist leer: $required" >&2
    false
  }
done
[[ -x "$stage_state/workspace/scripts/assistant.sh" ]]
[[ -x "$stage_state/workspace/scripts/mail-agent.sh" ]]

while IFS= read -r -d '' database; do
  result=$(sqlite3 "$database" 'PRAGMA quick_check;' 2>&1) || {
    echo "SQLite-Pruefung fehlgeschlagen: $database: $result" >&2
    false
  }
  [[ "$result" == "ok" ]] || {
    echo "SQLite-Pruefung fehlgeschlagen: $database: $result" >&2
    false
  }
done < <(find "$stage_state" -type f \( -name '*.sqlite' -o -name '*.sqlite3' -o -name '*.db' \) -print0)

mkdir -p "$OPENCLAW_STATE_DIR" "$OPENCLAW_CONFIG_DIR" "$OPENCLAW_SECRETS_DIR" "$HIMALAYA_CONFIG_DIR"
rsync -a --delete "$stage_state/" "$OPENCLAW_STATE_DIR/"
rsync -a --delete "$stage_config/" "$OPENCLAW_CONFIG_DIR/"
rsync -a --delete "$stage_secrets/" "$OPENCLAW_SECRETS_DIR/"
chmod -R go-rwx "$OPENCLAW_CONFIG_DIR" "$OPENCLAW_SECRETS_DIR" "$HIMALAYA_CONFIG_DIR" || true
chown -R 1000:1000 "$OPENCLAW_STATE_DIR" "$HIMALAYA_CONFIG_DIR" 2>/dev/null || \
  sudo chown -R 1000:1000 "$OPENCLAW_STATE_DIR" "$HIMALAYA_CONFIG_DIR"

update_env_value OPENCLAW_CURRENT_RUNTIME legacy-systemd
update_env_value OPENCLAW_LEGACY_HOME "$SOURCE_HOME"
trap - ERR
echo "Migration abgeschlossen. Sicherheitskopie: $migration_backup"
echo "Der alte Live-Ordner wurde nicht geloescht. Starte jetzt deploy.sh mit dem gewuenschten Image."
