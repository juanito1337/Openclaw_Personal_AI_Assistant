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

stamp=$(date -u +%Y%m%dT%H%M%SZ)
migration_backup="$OPENCLAW_ROOT/backups/migration/live-before-container-$stamp.tar.gz"
mkdir -p "$(dirname "$migration_backup")"

units=(
  mail-agent.timer mail-agent.service
  personal-assistant-sync.timer personal-assistant-sync.service
  personal-assistant-supervisor.timer personal-assistant-supervisor.service
  ollama-priority-proxy.service openclaw-gateway.service
)
mkdir -p "$OPENCLAW_CONFIG_DIR"
legacy_units_file="$OPENCLAW_CONFIG_DIR/legacy-active-units.txt"
: > "$legacy_units_file"
for unit in "${units[@]}"; do
  if systemctl --user is-active --quiet "$unit" || systemctl --user is-enabled --quiet "$unit"; then
    printf '%s\n' "$unit" >> "$legacy_units_file"
  fi
done
for unit in "${units[@]}"; do
  systemctl --user disable --now "$unit" >/dev/null 2>&1 || true
done

restart_legacy_on_failure() {
  local code=$?
  trap - ERR
  echo "Migration fehlgeschlagen; aktiviere den bisherigen systemd-Betrieb erneut." >&2
  if [[ -s "$legacy_units_file" ]]; then
    while IFS= read -r unit; do
      systemctl --user enable --now "$unit" >/dev/null 2>&1 || systemctl --user start "$unit" >/dev/null 2>&1 || true
    done < "$legacy_units_file"
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

rsync -a --delete "$SOURCE_HOME/" "$OPENCLAW_STATE_DIR/"
if [[ -d "$HOME/.config/himalaya" ]]; then
  rsync -a --delete "$HOME/.config/himalaya/" "$HIMALAYA_CONFIG_DIR/"
fi
if [[ -f "$HOME/.config/openclaw/ollama-priority.env" ]]; then
  cp "$HOME/.config/openclaw/ollama-priority.env" "$OPENCLAW_CONFIG_DIR/ollama-priority.env"
fi
if [[ -f "$HOME/.config/mail-agent.env" ]]; then
  cp "$HOME/.config/mail-agent.env" "$OPENCLAW_SECRETS_DIR/mail-agent.env"
fi
if [[ -f "$HOME/.config/personal-assistant/secrets.env" ]]; then
  cp "$HOME/.config/personal-assistant/secrets.env" "$OPENCLAW_SECRETS_DIR/personal-assistant.env"
fi
chmod -R go-rwx "$OPENCLAW_CONFIG_DIR" "$OPENCLAW_SECRETS_DIR" "$HIMALAYA_CONFIG_DIR" || true
chown -R 1000:1000 "$OPENCLAW_STATE_DIR" "$HIMALAYA_CONFIG_DIR" 2>/dev/null || \
  sudo chown -R 1000:1000 "$OPENCLAW_STATE_DIR" "$HIMALAYA_CONFIG_DIR"

update_env_value OPENCLAW_CURRENT_RUNTIME legacy-systemd
update_env_value OPENCLAW_LEGACY_HOME "$SOURCE_HOME"
trap - ERR
echo "Migration abgeschlossen. Sicherheitskopie: $migration_backup"
echo "Der alte Live-Ordner wurde nicht geloescht. Starte jetzt deploy.sh mit dem gewuenschten Image."
