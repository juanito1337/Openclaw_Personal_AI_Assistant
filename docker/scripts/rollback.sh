#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(CDPATH= cd "$(dirname "$0")" && pwd)
# shellcheck source=common.sh
. "$SCRIPT_DIR/common.sh"

if [[ ${EUID:-$(id -u)} -eq 0 ]]; then
  echo "Bitte als normaler Benutzer mit Docker-Rechten ausfuehren, nicht mit sudo/root." >&2
  exit 2
fi
require_command rsync
require_command tar
backup_id=${1:?Backup-ID angeben}
automatic=${2:-}
backup="$OPENCLAW_BACKUP_DIR/$backup_id"
"$SCRIPT_DIR/verify-backup.sh" "$backup" >/dev/null

read_manifest() {
  python3 - "$backup/manifest.json" "$1" <<'PY'
import json, sys
print(json.load(open(sys.argv[1], encoding="utf-8")).get(sys.argv[2], ""))
PY
}
previous_image=$(read_manifest previous_image)
previous_runtime=$(read_manifest previous_runtime)
legacy_home=$(read_manifest legacy_home)
external_reference=$(read_manifest external_backup_reference)
previous_runtime=${previous_runtime:-docker}

echo "Stoppe aktuellen Containerstand."
compose down --remove-orphans >/dev/null 2>&1 || true

# Preserve the failed state for later diagnosis. It is deliberately excluded
# from normal retention deletion during this rollback operation.
if [[ -d "$OPENCLAW_STATE_DIR" ]]; then
  BACKUP_RETENTION_RELEASES=100000 PREVIOUS_IMAGE=${OPENCLAW_IMAGE:-unknown} \
    TARGET_IMAGE="forensic-before-rollback" PREVIOUS_RUNTIME=docker \
    EXTERNAL_BACKUP_REFERENCE="" "$SCRIPT_DIR/backup.sh" "forensic-before-rollback" >/dev/null || true
fi

if [[ -n "$external_reference" ]]; then
  restore_hook=${OPENCLAW_EXTERNAL_RESTORE_HOOK:-}
  [[ -x "$restore_hook" ]] || {
    echo "Externe Daten wurden moeglicherweise geaendert, aber der Restore-Hook fehlt: $restore_hook" >&2
    exit 1
  }
  echo "Stelle externen Snapshot wieder her: $external_reference"
  "$restore_hook" "$external_reference"
fi

echo "Stelle lokalen Zustand aus $backup_id wieder her."
restore_root=$(mktemp -d)
cleanup_restore() {
  rm -rf "$restore_root"
}
trap cleanup_restore EXIT
tar -xzf "$backup/payload.tar.gz" -C "$restore_root"
for name in state config secrets; do
  source="$restore_root/$name"
  target="$OPENCLAW_ROOT/$name"
  [[ -d "$source" ]] || { echo "Restore-Quelle fehlt im Backup: $name" >&2; exit 1; }
  [[ -d "$target" ]] || {
    echo "Restore-Ziel fehlt; setup-host.sh muss die geschuetzte Hoststruktur anlegen: $target" >&2
    exit 1
  }
  rsync -a --delete "$source/" "$target/"
done
cleanup_restore
trap - EXIT
chown -R 1000:1000 "$OPENCLAW_STATE_DIR" "$HIMALAYA_CONFIG_DIR" 2>/dev/null || \
  sudo chown -R 1000:1000 "$OPENCLAW_STATE_DIR" "$HIMALAYA_CONFIG_DIR"

if [[ "$previous_runtime" == "legacy-systemd" ]]; then
  [[ -n "$legacy_home" ]] || { echo "Legacy-Home fehlt im Backup-Manifest" >&2; exit 1; }
  echo "Stelle den vorherigen systemd-Workspace wieder her: $legacy_home"
  mkdir -p "$legacy_home"
  rsync -a --delete "$OPENCLAW_STATE_DIR/" "$legacy_home/"
  update_env_value OPENCLAW_CURRENT_RUNTIME legacy-systemd
  units_file="$OPENCLAW_CONFIG_DIR/legacy-active-units.txt"
  if [[ -s "$units_file" ]]; then
    while IFS= read -r unit; do
      systemctl --user enable --now "$unit" >/dev/null 2>&1 || systemctl --user start "$unit" >/dev/null 2>&1 || true
    done < "$units_file"
  fi
  echo "Rollback auf den vorherigen systemd-Betrieb abgeschlossen."
else
  update_env_value OPENCLAW_IMAGE "$previous_image"
  update_env_value OPENCLAW_CURRENT_RUNTIME docker
  export OPENCLAW_IMAGE="$previous_image"
  docker pull "$previous_image" >/dev/null
  compose --profile maintenance run --rm --entrypoint freshclam clamav-update --verbose || true
  compose up -d ollama-proxy gateway
  wait_for_healthy ollama-proxy 180
  wait_for_healthy gateway 300
  compose --profile maintenance up -d clamav-update
  compose up -d mail-worker sync-worker supervisor-worker
  wait_for_healthy mail-worker 180
  wait_for_healthy sync-worker 180
  wait_for_healthy supervisor-worker 180
  echo "Rollback erfolgreich: $previous_image"
fi
ln -sfn "$backup_id" "$OPENCLAW_BACKUP_DIR/latest"
[[ "$automatic" == "--automatic" ]] || echo "Wiederhergestellt aus: $backup_id"
