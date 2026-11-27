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
value = json.load(open(sys.argv[1], encoding="utf-8")).get(sys.argv[2], "")
print(str(value).lower() if isinstance(value, bool) else value)
PY
}
previous_image=$(read_manifest previous_image)
previous_proxy_image=$(read_manifest previous_proxy_image)
previous_maintenance_image=$(read_manifest previous_maintenance_image)
target_maintenance_image=$(read_manifest target_maintenance_image)
previous_clamd_image=$(read_manifest previous_clamd_image)
target_clamd_image=$(read_manifest target_clamd_image)
previous_runtime=$(read_manifest previous_runtime)
previous_updater_running=$(read_manifest previous_clamav_updater_running)
# Older backup manifests predate updater-state capture and always restarted it.
previous_updater_running=${previous_updater_running:-true}
[[ "$previous_updater_running" == true || "$previous_updater_running" == false ]] || exit 2
previous_talk_proxy_enabled=$(read_manifest previous_talk_proxy_enabled)
previous_talk_proxy_enabled=${previous_talk_proxy_enabled:-false}
validate_talk_proxy_flag "$previous_talk_proxy_enabled"
previous_talk_proxy_ip=$(read_manifest previous_talk_proxy_ip)
previous_talk_nextcloud_ip=$(read_manifest previous_talk_nextcloud_ip)
legacy_home=$(read_manifest legacy_home)
legacy_migration_backup=$(read_manifest legacy_migration_backup)
legacy_migration_member=$(read_manifest legacy_migration_member)
external_reference=$(read_manifest external_backup_reference)
previous_runtime=${previous_runtime:-docker}
previous_proxy_image=${previous_proxy_image:-$previous_image}
previous_maintenance_image=${previous_maintenance_image:-$previous_image}
rollback_maintenance_image=$previous_maintenance_image
rollback_clamd_image=${previous_clamd_image:-${target_clamd_image:-${target_maintenance_image:-$previous_maintenance_image}}}
# A pre-M14 Docker roll set has neither clamd nor its socket initializer. The
# current M14 compose contract needs both from one compatible, already verified
# target maintenance image during this one transition rollback. Retaining only
# OPENCLAW_CLAMD_IMAGE is insufficient because clamav-socket-init is sourced
# from OPENCLAW_MAINTENANCE_IMAGE.
if [[ "$previous_runtime" == "docker" && -z "$previous_clamd_image" ]]; then
  rollback_maintenance_image=${target_maintenance_image:-${target_clamd_image:-$previous_maintenance_image}}
  rollback_clamd_image=${target_clamd_image:-$rollback_maintenance_image}
fi
recovery_image=$(read_manifest recovery_maintenance_image)
if [[ -n "$recovery_image" ]]; then
  recovery_revision=$(read_manifest recovery_maintenance_revision)
  recovery_release=$(read_manifest recovery_maintenance_release)
  [[ "$previous_runtime" == docker && "$recovery_image" == "$target_maintenance_image" &&
     "$recovery_image" == "$(read_manifest recovery_clamd_image)" &&
     "$(read_manifest recovery_clamav_policy)" == vendor-v1 &&
     "$(read_manifest recovery_clamav_reason)" == previous-image-build-age-policy &&
     "$recovery_revision" =~ ^[0-9a-f]{40}$ && -n "$recovery_release" ]] || {
    echo "ClamAV-Recovery-Vertrag im Backup ist ungueltig." >&2; exit 2
  }
  rollback_maintenance_image=$recovery_image
  rollback_clamd_image=$recovery_image
fi
restore_hook=${OPENCLAW_EXTERNAL_RESTORE_HOOK:-}

if [[ "$previous_talk_proxy_enabled" == true ]]; then
  validate_talk_proxy_addresses "$previous_talk_proxy_ip" "$previous_talk_nextcloud_ip"
  [[ "$previous_runtime" == docker ]] || {
    echo "Talk-Proxy kann nicht als Teil einer Legacy-Runtime restauriert werden." >&2
    exit 2
  }

fi

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

verify_recovery_images() {
  if [[ -n "$recovery_image" ]]; then
    OPENCLAW_EXPECTED_RELEASE="$recovery_release" "$SCRIPT_DIR/verify-image-supply-chain.sh" \
      "$recovery_image" "$recovery_revision" maintenance
    [[ "$(clamav_image_policy "$recovery_image")" == vendor-v1 ]] || exit 2
    "$SCRIPT_DIR/check-maintenance-runtime.sh" "$recovery_image" --recovery
  fi
  if [[ "$previous_talk_proxy_enabled" == true ]]; then
    docker pull "$previous_proxy_image" >/dev/null
    verify_talk_proxy_image "$previous_proxy_image"
  fi
}

fence_candidate_stack() {
  local project="${COMPOSE_PROJECT_NAME:-openclaw-agent}" states state
  echo "Stoppe aktuellen Containerstand und verifiziere den Writer-Stillstand."
  compose --profile tools --profile maintenance --profile talk down --remove-orphans >/dev/null 2>&1 || \
    compose --profile tools --profile maintenance --profile talk stop
  states=$(docker container ls --all --filter "label=com.docker.compose.project=$project" --format '{{.State}}')
  while IFS= read -r state; do
    case "$state" in
      ''|created|exited|dead) ;;
      *) echo "Rollback gesperrt: laufende oder ungeklaerte Container im Kandidatenprojekt." >&2; exit 1 ;;
    esac
  done <<< "$states"
  if ! { [[ "$(clamav_updater_running_state)" == false ]] && setup_service_is_offline && talk_proxy_is_offline; }; then
    echo "Rollback gesperrt: Kandidaten-Writer sind nicht nachweislich gestoppt." >&2; exit 1
  fi
}

if [[ "$automatic" == --automatic ]]; then
  # An already failed candidate cannot keep writing while remote verification runs.
  fence_candidate_stack
  verify_recovery_images
else
  verify_recovery_images
  fence_candidate_stack
fi

# Preserve the failed state for later diagnosis. It is deliberately excluded
# from normal retention deletion during this rollback operation.
if [[ -d "$OPENCLAW_STATE_DIR" ]]; then
  BACKUP_RETENTION_RELEASES=100000 PREVIOUS_IMAGE=${OPENCLAW_IMAGE:-unknown} \
    TARGET_IMAGE="forensic-before-rollback" PREVIOUS_RUNTIME=docker \
    PREVIOUS_TALK_PROXY_ENABLED=false TARGET_TALK_PROXY_ENABLED=false \
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

normalize_local_restore_ownership() {
  chown -R 1000:1000 "$OPENCLAW_STATE_DIR" "$HIMALAYA_CONFIG_DIR" 2>/dev/null || \
    sudo chown -R 1000:1000 "$OPENCLAW_STATE_DIR" "$HIMALAYA_CONFIG_DIR"
}

# Docker can create a missing bind-mount source as root.  Normalize those
# runtime-created directories before rsync needs to delete or replace them;
# normalizing only after the restore is too late and leaves every service down.
normalize_local_restore_ownership
echo "Stelle lokalen Zustand aus $backup_id wieder her."
OPENCLAW_RESTORE_OFFLINE=YES "$SCRIPT_DIR/restore-local-state.sh" "$backup"
normalize_local_restore_ownership

# .env is deployment state, not part of the payload archive. An old manifest
# deliberately restores OFF, including a failed first-time Talk activation.
restore_talk_proxy_configuration "$previous_talk_proxy_enabled" \
  "$previous_talk_proxy_ip" "$previous_talk_nextcloud_ip"

if [[ "$previous_runtime" == "legacy-systemd" ]]; then
  legacy_home_ready || {
    echo "Legacy-Rollback abgebrochen: Der wiederhergestellte Workspace ist nicht startfaehig." >&2
    exit 1
  }
  echo "Verwende den unveraenderten systemd-Workspace weiter: $legacy_home"
  update_env_value OPENCLAW_IMAGE "$previous_image"
  update_env_value OPENCLAW_PROXY_IMAGE "$previous_proxy_image"
  update_env_value OPENCLAW_MAINTENANCE_IMAGE "$rollback_maintenance_image"
  update_env_value OPENCLAW_CLAMD_IMAGE "$rollback_clamd_image"
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
  update_env_value OPENCLAW_MAINTENANCE_IMAGE "$rollback_maintenance_image"
  update_env_value OPENCLAW_CLAMD_IMAGE "$rollback_clamd_image"
  update_env_value OPENCLAW_CURRENT_RUNTIME docker
  export OPENCLAW_IMAGE="$previous_image"
  export OPENCLAW_PROXY_IMAGE="$previous_proxy_image"
  export OPENCLAW_MAINTENANCE_IMAGE="$rollback_maintenance_image"
  export OPENCLAW_CLAMD_IMAGE="$rollback_clamd_image"
  docker pull "$previous_image" >/dev/null
  docker pull "$previous_proxy_image" >/dev/null
  docker pull "$rollback_maintenance_image" >/dev/null
  docker pull "$rollback_clamd_image" >/dev/null
  # Old backups predate this dedicated slot; create an empty directory only.
  # Existing restored credentials remain unchanged.
  ensure_nextcloud_connection_directory
  start_setup_service_if_supported
  bootstrap_result=0
  # Older signed images use the original FreshClam bootstrap.
  compose --profile maintenance run --rm --no-deps --entrypoint sh clamav-update -ec '
    if test -f /opt/openclaw-agent/personal_assistant/clamav_update.py; then
      exec python3 -P -m personal_assistant.clamav_update --once
    fi
    python3 -P -m personal_assistant.clamav_health >/dev/null 2>&1 || exec freshclam --datadir=/var/lib/clamav
  ' || bootstrap_result=$?
  if [[ "$previous_updater_running" == true ]]; then
    compose --profile maintenance up -d --no-deps clamav-update
    [[ "$(clamav_updater_running_state)" == true ]] || exit 70
  fi
  (( bootstrap_result == 0 )) || exit "$bootstrap_result"
  compose up -d clamd
  wait_for_healthy clamd 300
  # clamd and its one-shot socket initializer are already healthy. Traversing
  # dependencies again would rerun the initializer against the live socket.
  compose up -d --no-deps ollama-proxy gateway
  wait_for_healthy ollama-proxy 180
  wait_for_healthy gateway 300
  if [[ "$previous_talk_proxy_enabled" == true ]]; then
    start_talk_proxy
  fi

  compose up -d --no-deps mail-worker sync-worker supervisor-worker portfolio-worker monitor-worker
  wait_for_healthy mail-worker 180
  wait_for_healthy sync-worker 180
  wait_for_healthy supervisor-worker 180
  wait_for_healthy portfolio-worker 180
  wait_for_healthy monitor-worker 180
  echo "Vorherige Anwendung und Worker gestartet: $previous_image"
  if [[ -n "$recovery_image" ]]; then
    echo "Verifizierte ClamAV-Recovery-Rolle: $recovery_image (vendor-v1)."
    echo "Die vorherige Anwendung behaelt ihre 48-Stunden-Grenze; bei aelterer Daily bleibt der Index gesperrt."
    echo "Separater Fachstatus des wiederhergestellten Anwendungsstands:"
    if ! compose --profile tools run --rm --no-deps agent-cli \
      /opt/openclaw-agent/scripts/assistant.sh jobs status --target all --deep; then
      echo "Fachstatus meldet eine Stoerung oder konnte nicht vollstaendig verifiziert werden." >&2
    fi
  fi
fi
ln -sfn "$backup_id" "$OPENCLAW_BACKUP_DIR/latest"
[[ "$automatic" == "--automatic" ]] || echo "Wiederhergestellt aus: $backup_id"
if [[ $external_restore_failed -ne 0 ]]; then
  echo "Lokaler Rollback abgeschlossen, externer Restore jedoch fehlgeschlagen: $external_reference" >&2
  exit 1
fi
