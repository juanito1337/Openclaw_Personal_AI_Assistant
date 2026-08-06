#!/usr/bin/env bash
set -euo pipefail
umask 077

SCRIPT_DIR=$(CDPATH='' cd "$(dirname "$0")" && pwd)
# shellcheck source=docker/scripts/common.sh
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
previous_proxy_image=$(read_manifest previous_proxy_image)
previous_maintenance_image=$(read_manifest previous_maintenance_image)
previous_runtime=$(read_manifest previous_runtime)
legacy_home=$(read_manifest legacy_home)
legacy_migration_backup=$(read_manifest legacy_migration_backup)
legacy_migration_member=$(read_manifest legacy_migration_member)
external_reference=$(read_manifest external_backup_reference)
previous_runtime=${previous_runtime:-docker}
previous_proxy_image=${previous_proxy_image:-$previous_image}
previous_maintenance_image=${previous_maintenance_image:-$previous_image}
restore_hook=${OPENCLAW_EXTERNAL_RESTORE_HOOK:-}

# The executable contract is checked before stopping anything. The hook can
# still fail while restoring; in that case local rollback continues and the
# final non-zero result explicitly marks remote recovery as uncertain.
if [[ -n "$external_reference" && ! -x "$restore_hook" ]]; then
  echo "Rollback wurde vor dem Stoppen abgebrochen: externer Restore-Hook fehlt: $restore_hook" >&2
  exit 1
fi

legacy_home_ready() {
  [[ -n "$legacy_home" ]] \
    && [[ -s "$legacy_home/openclaw.json" ]] \
    && [[ -x "$legacy_home/workspace/scripts/assistant.sh" ]] \
    && [[ -x "$legacy_home/workspace/scripts/mail-agent.sh" ]]
}

restore_legacy_home_from_migration() {
  local restore_root source rescue stamp
  [[ -f "$legacy_migration_backup" && -n "$legacy_migration_member" ]] || {
    echo "Legacy-Workspace fehlt und das Release-Backup besitzt kein nutzbares Migrationsarchiv." >&2
    return 1
  }
  python3 - "$HOME" "$legacy_home" "$legacy_migration_member" <<'PY' || return 1
from pathlib import Path, PurePosixPath
import sys
home = Path(sys.argv[1]).resolve()
legacy = Path(sys.argv[2]).resolve()
try:
    relative = legacy.relative_to(home)
except ValueError as exc:
    raise SystemExit("Legacy-Home liegt ausserhalb des Benutzer-Homes.") from exc
if not relative.parts:
    raise SystemExit("Das gesamte Benutzer-Home darf nicht wiederhergestellt werden.")
member = PurePosixPath(sys.argv[3])
if member.is_absolute() or ".." in member.parts or not member.parts:
    raise SystemExit("Ungueltiger Legacy-Pfad im Backup-Manifest.")
if member.as_posix() != relative.as_posix():
    raise SystemExit("Legacy-Home und Archivpfad stimmen nicht ueberein.")
PY

  restore_root=$(mktemp -d "$OPENCLAW_ROOT/backups/migration/legacy-rollback.XXXXXX") || return 1
  tar -xzf "$legacy_migration_backup" -C "$restore_root" "$legacy_migration_member" || {
    rm -rf -- "$restore_root"
    return 1
  }
  source="$restore_root/$legacy_migration_member"
  [[ -s "$source/openclaw.json" ]] \
    && [[ -x "$source/workspace/scripts/assistant.sh" ]] \
    && [[ -x "$source/workspace/scripts/mail-agent.sh" ]] || {
      rm -rf -- "$restore_root"
      echo "Das Migrationsarchiv enthaelt keinen startfaehigen Legacy-Workspace." >&2
      return 1
    }

  stamp=$(date -u +%Y%m%dT%H%M%SZ)
  if [[ -d "$legacy_home" ]]; then
    rescue="$OPENCLAW_ROOT/backups/migration/legacy-home-before-rollback-$stamp.tar.gz"
    tar -C "$(dirname "$legacy_home")" -czf "$rescue" "$(basename "$legacy_home")" || {
      rm -rf -- "$restore_root"
      return 1
    }
    echo "Unvollstaendigen Legacy-Stand gesichert: $rescue"
  else
    mkdir -p "$legacy_home" || {
      rm -rf -- "$restore_root"
      return 1
    }
  fi
  rsync -a --delete "$source/" "$legacy_home/" || {
    rm -rf -- "$restore_root"
    return 1
  }
  rm -rf -- "$restore_root"
  legacy_home_ready
}

if [[ "$previous_runtime" == "legacy-systemd" ]] && ! legacy_home_ready; then
  echo "Legacy-Workspace ist unvollstaendig; stelle ihn aus dem verifizierten Migrationsarchiv wieder her."
  restore_legacy_home_from_migration || {
    echo "Rollback wurde vor dem Stoppen der aktuellen Container abgebrochen." >&2
    exit 1
  }
fi

echo "Stoppe aktuellen Containerstand."
compose down --remove-orphans >/dev/null 2>&1 || true

# Preserve the failed state for later diagnosis. It is deliberately excluded
# from normal retention deletion during this rollback operation.
if [[ -d "$OPENCLAW_STATE_DIR" ]]; then
  BACKUP_RETENTION_RELEASES=100000 PREVIOUS_IMAGE=${OPENCLAW_IMAGE:-unknown} \
    TARGET_IMAGE="forensic-before-rollback" PREVIOUS_RUNTIME=docker \
    EXTERNAL_BACKUP_REFERENCE="" "$SCRIPT_DIR/backup.sh" "forensic-before-rollback" >/dev/null || true
fi

external_restore_failed=0
if [[ -n "$external_reference" ]]; then
  echo "Stelle externen Snapshot wieder her: $external_reference"
  if ! "$restore_hook" "$external_reference"; then
    external_restore_failed=1
    echo "Externer Restore fehlgeschlagen; lokaler Rollback wird fortgesetzt. Remote-Zustand ist unklar." >&2
  fi
fi

echo "Stelle lokalen Zustand aus $backup_id wieder her."
OPENCLAW_RESTORE_OFFLINE=YES "$SCRIPT_DIR/restore-local-state.sh" "$backup"
chown -R 1000:1000 "$OPENCLAW_STATE_DIR" "$HIMALAYA_CONFIG_DIR" 2>/dev/null || \
  sudo chown -R 1000:1000 "$OPENCLAW_STATE_DIR" "$HIMALAYA_CONFIG_DIR"

if [[ "$previous_runtime" == "legacy-systemd" ]]; then
  legacy_home_ready || {
    echo "Legacy-Rollback abgebrochen: Der wiederhergestellte Workspace ist nicht startfaehig." >&2
    exit 1
  }
  echo "Verwende den unveraenderten systemd-Workspace weiter: $legacy_home"
  update_env_value OPENCLAW_IMAGE "$previous_image"
  update_env_value OPENCLAW_PROXY_IMAGE "$previous_proxy_image"
  update_env_value OPENCLAW_MAINTENANCE_IMAGE "$previous_maintenance_image"
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
  update_env_value OPENCLAW_PROXY_IMAGE "$previous_proxy_image"
  update_env_value OPENCLAW_MAINTENANCE_IMAGE "$previous_maintenance_image"
  update_env_value OPENCLAW_CURRENT_RUNTIME docker
  export OPENCLAW_IMAGE="$previous_image"
  export OPENCLAW_PROXY_IMAGE="$previous_proxy_image"
  export OPENCLAW_MAINTENANCE_IMAGE="$previous_maintenance_image"
  docker pull "$previous_image" >/dev/null
  docker pull "$previous_proxy_image" >/dev/null
  docker pull "$previous_maintenance_image" >/dev/null
  compose --profile maintenance run --rm --entrypoint freshclam clamav-update --verbose || true
  compose up -d ollama-proxy gateway
  wait_for_healthy ollama-proxy 180
  wait_for_healthy gateway 300
  compose --profile maintenance up -d clamav-update
  compose up -d mail-worker sync-worker supervisor-worker portfolio-worker monitor-worker
  wait_for_healthy mail-worker 180
  wait_for_healthy sync-worker 180
  wait_for_healthy supervisor-worker 180
  wait_for_healthy portfolio-worker 180
  wait_for_healthy monitor-worker 180
  echo "Rollback erfolgreich: $previous_image"
fi
ln -sfn "$backup_id" "$OPENCLAW_BACKUP_DIR/latest"
[[ "$automatic" == "--automatic" ]] || echo "Wiederhergestellt aus: $backup_id"
if [[ $external_restore_failed -ne 0 ]]; then
  echo "Lokaler Rollback abgeschlossen, externer Restore jedoch fehlgeschlagen: $external_reference" >&2
  exit 1
fi
