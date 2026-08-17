#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR=$(CDPATH='' cd "$(dirname "$0")" && pwd)
# shellcheck source=docker/scripts/common.sh
# shellcheck source=common.sh
. "$SCRIPT_DIR/common.sh"

if [[ ${EUID:-$(id -u)} -eq 0 ]]; then
  echo "Bitte als normaler Benutzer mit Docker-Rechten ausfuehren, nicht mit sudo/root." >&2
  exit 2
fi
require_command docker
require_command python3
docker info >/dev/null 2>&1 || {
  echo "Der aktuelle Benutzer kann nicht auf die Docker-API zugreifen." >&2
  echo "Nach Aufnahme in die docker-Gruppe neu anmelden oder den Aufruf einmalig mit sg docker -c starten." >&2
  exit 2
}

target_image=${1:?Signierte Runtime-Image-Referenz mit Digest angeben}
target_proxy_image=${2:-${OPENCLAW_TARGET_PROXY_IMAGE:-}}
target_maintenance_image=${3:-${OPENCLAW_TARGET_MAINTENANCE_IMAGE:-}}
[[ -n "$target_proxy_image" ]] || {
  echo "Signiertes Proxy-Image als zweites Argument oder OPENCLAW_TARGET_PROXY_IMAGE angeben." >&2
  exit 2
}
[[ -n "$target_maintenance_image" ]] || {
  echo "Signiertes Maintenance-Image als drittes Argument oder OPENCLAW_TARGET_MAINTENANCE_IMAGE angeben." >&2
  exit 2
}
previous_image=${OPENCLAW_IMAGE:-}
previous_proxy_image=${OPENCLAW_PROXY_IMAGE:-$previous_image}
previous_maintenance_image=${OPENCLAW_MAINTENANCE_IMAGE:-$previous_image}
previous_runtime=${OPENCLAW_CURRENT_RUNTIME:-docker}
expected_source_revision=${OPENCLAW_EXPECTED_SOURCE_REVISION:-}
mail_relevant_folder=${OPENCLAW_MAIL_RELEVANT_FOLDER:-}
mail_relevant_approved=${OPENCLAW_MAIL_RELEVANT_FOLDER_APPROVED:-false}
[[ -n "$previous_image" ]] || { echo "OPENCLAW_IMAGE fehlt in $ENV_FILE" >&2; exit 2; }
[[ "$target_image" != "$previous_image" ]] || echo "Hinweis: Zielimage entspricht dem aktuell eingetragenen Image."
[[ "$expected_source_revision" =~ ^[0-9a-f]{40}$ ]] || {
  echo "OPENCLAW_EXPECTED_SOURCE_REVISION muss fuer M7 den exakten 40-stelligen Git-Commit enthalten." >&2
  exit 2
}
if [[ -n "$mail_relevant_folder" && "$mail_relevant_approved" != "true" ]]; then
  echo "OPENCLAW_MAIL_RELEVANT_FOLDER braucht die ausdrueckliche Freigabe OPENCLAW_MAIL_RELEVANT_FOLDER_APPROVED=true." >&2
  exit 2
fi
if [[ -z "$mail_relevant_folder" && "$mail_relevant_approved" == "true" ]]; then
  echo "OPENCLAW_MAIL_RELEVANT_FOLDER_APPROVED=true ist ohne Zielordner ungueltig." >&2
  exit 2
fi
if [[ "$mail_relevant_folder" == *$'\n'* || "$mail_relevant_folder" == *$'\r'* ]]; then
  echo "OPENCLAW_MAIL_RELEVANT_FOLDER darf keine Zeilenumbrueche enthalten." >&2
  exit 2
fi

legacy_units=(
  mail-agent.timer mail-agent.service
  personal-assistant-sync.timer personal-assistant-sync.service
  personal-assistant-supervisor.timer personal-assistant-supervisor.service
  personal-assistant-portfolio.timer personal-assistant-portfolio.service
  personal-assistant-monitor.timer personal-assistant-monitor.service
  ollama-priority-proxy.service openclaw-gateway.service
)
legacy_writer_timers=(
  mail-agent.timer
  personal-assistant-sync.timer
  personal-assistant-supervisor.timer
  personal-assistant-portfolio.timer
  personal-assistant-monitor.timer
)
container_writer_services=(
  mail-worker
  sync-worker
  supervisor-worker
  portfolio-worker
  monitor-worker
)

running_container_writers() {
  local service container running
  for service in "${container_writer_services[@]}"; do
    container="openclaw-$service"
    running=$(docker inspect --format '{{.State.Running}}' "$container" 2>/dev/null || true)
    if [[ "$running" == "true" ]]; then
      printf '%s\n' "$container"
    fi
  done
}

assert_container_writers_stopped() {
  local running
  running=$(running_container_writers)
  if [[ -n "$running" ]]; then
    echo "Docker-Writer laufen noch und blockieren Deployment/Backup:" >&2
    while IFS= read -r container; do
      printf '  %s\n' "$container" >&2
    done <<<"$running"
    return 1
  fi
}

validate_legacy_home() {
  local legacy_home=${OPENCLAW_LEGACY_HOME:-}
  [[ -n "$legacy_home" ]] \
    && [[ -s "$legacy_home/openclaw.json" ]] \
    && [[ -x "$legacy_home/workspace/scripts/assistant.sh" ]] \
    && [[ -x "$legacy_home/workspace/scripts/mail-agent.sh" ]]
}

assert_legacy_writers_disabled() {
  local unit
  for unit in "${legacy_units[@]}"; do
    if systemctl --user is-active --quiet "$unit"; then
      echo "Alter systemd-Prozess ist noch aktiv: $unit" >&2
      return 1
    fi
  done
  for unit in "${legacy_writer_timers[@]}"; do
    if systemctl --user is-enabled --quiet "$unit"; then
      echo "Alter systemd-Writer ist noch aktiviert: $unit" >&2
      return 1
    fi
  done
}

disable_legacy_writer_timers() {
  local unit
  # Stopping a timer does not change its enablement state. Verify the user
  # systemd manager first, then disable every installed/active legacy writer
  # timer so the post-stop Single-Writer gate cannot fail on stale enablement.
  systemctl --user show-environment >/dev/null
  for unit in "${legacy_writer_timers[@]}"; do
    if systemctl --user is-enabled --quiet "$unit" \
      || systemctl --user is-active --quiet "$unit"; then
      systemctl --user disable --now "$unit"
    fi
  done
}

case "$previous_runtime" in
  legacy-systemd)
    if [[ -n "$(running_container_writers)" ]]; then
      echo "Runtime-Identitaet widerspruechlich: OPENCLAW_CURRENT_RUNTIME=legacy-systemd, aber Docker-Writer laufen." >&2
      echo "Vor dem Deployment den tatsaechlichen Laufzeitstand explizit wiederherstellen oder korrigieren." >&2
      exit 2
    fi
    ;;
  docker)
    assert_legacy_writers_disabled
    ;;
  *)
    echo "Unbekannte OPENCLAW_CURRENT_RUNTIME-Identitaet: $previous_runtime" >&2
    exit 2
    ;;
esac

if [[ "$previous_runtime" == "legacy-systemd" ]] && ! validate_legacy_home; then
  echo "Legacy-Deployment abgebrochen: OPENCLAW_LEGACY_HOME ist nicht startfaehig." >&2
  exit 2
fi

write_test=${OPENCLAW_WRITE_TEST_ENABLED:-true}
require_external=${REQUIRE_EXTERNAL_BACKUP_FOR_WRITE_TEST:-true}
backup_hook=${OPENCLAW_EXTERNAL_BACKUP_HOOK:-}
restore_hook=${OPENCLAW_EXTERNAL_RESTORE_HOOK:-}
if [[ "$write_test" == "true" && "$require_external" == "true" ]]; then
  [[ -x "$backup_hook" ]] || { echo "Externer Backup-Hook fehlt oder ist nicht ausfuehrbar: $backup_hook" >&2; exit 2; }
  [[ -x "$restore_hook" ]] || { echo "Externer Restore-Hook fehlt oder ist nicht ausfuehrbar: $restore_hook" >&2; exit 2; }
fi

echo "Pruefe signierte M7-Images vor jeder Aenderung am laufenden Stack."
"$SCRIPT_DIR/verify-image-supply-chain.sh" "$target_image" "$expected_source_revision" runtime
"$SCRIPT_DIR/verify-image-supply-chain.sh" "$target_proxy_image" "$expected_source_revision" proxy
"$SCRIPT_DIR/verify-image-supply-chain.sh" "$target_maintenance_image" "$expected_source_revision" maintenance
echo "Pruefe ClamAV-Maintenance-Laufzeit und TLS-Transport vor dem Writer-Stopp."
"$SCRIPT_DIR/check-maintenance-runtime.sh" "$target_maintenance_image"

target_layout_min=$(
  docker image inspect \
    --format '{{index .Config.Labels "org.opencontainers.image.openclaw.layout-min"}}' \
    "$target_image" 2>/dev/null || true
)
target_layout_max=$(
  docker image inspect \
    --format '{{index .Config.Labels "org.opencontainers.image.openclaw.layout-max"}}' \
    "$target_image" 2>/dev/null || true
)
# Images before M2 had no layout labels. They are certified only for layout 1;
# an M2 state therefore fails closed before the current stack is stopped.
target_layout_min=${target_layout_min:-1}
target_layout_max=${target_layout_max:-1}
python3 "$SCRIPT_DIR/check-layout-compatibility.py" \
  --state-dir "$OPENCLAW_STATE_DIR" \
  --target-image "$target_image" \
  --target-min "$target_layout_min" \
  --target-max "$target_layout_max"

restart_previous_on_prebackup_failure() {
  local code=$?
  trap - ERR
  echo "Vorbereitendes Backup fehlgeschlagen; starte den bisherigen Stand erneut." >&2
  if [[ "$previous_runtime" == "legacy-systemd" ]]; then
    units_file="$OPENCLAW_CONFIG_DIR/legacy-active-units.txt"
    if [[ -s "$units_file" ]]; then
      while IFS= read -r unit; do
        systemctl --user enable --now "$unit" >/dev/null 2>&1 || systemctl --user start "$unit" >/dev/null 2>&1 || true
      done < "$units_file"
    fi
  else
    compose up -d >/dev/null 2>&1 || true
  fi
  exit "$code"
}
trap restart_previous_on_prebackup_failure ERR

echo "Stoppe alle schreibenden Laufzeiten."
if [[ "$previous_runtime" == "legacy-systemd" ]]; then
  disable_legacy_writer_timers
  for unit in "${legacy_units[@]}"; do
    systemctl --user stop "$unit" >/dev/null 2>&1 || true
  done
  assert_legacy_writers_disabled
else
  compose stop mail-worker sync-worker supervisor-worker portfolio-worker monitor-worker gateway ollama-proxy
  assert_container_writers_stopped
fi

external_reference=""
if [[ -n "$backup_hook" && -x "$backup_hook" ]]; then
  echo "Erzeuge externen Snapshot fuer IMAP/Nextcloud/CardDAV/CalDAV."
  external_reference=$(PREVIOUS_IMAGE="$previous_image" TARGET_IMAGE="$target_image" "$backup_hook")
  [[ -n "$external_reference" ]] || { echo "Externer Backup-Hook lieferte keine Referenz." >&2; false; }
fi

export PREVIOUS_IMAGE="$previous_image" PREVIOUS_PROXY_IMAGE="$previous_proxy_image" \
  PREVIOUS_MAINTENANCE_IMAGE="$previous_maintenance_image" TARGET_IMAGE="$target_image" \
  TARGET_PROXY_IMAGE="$target_proxy_image" TARGET_MAINTENANCE_IMAGE="$target_maintenance_image" \
  EXTERNAL_BACKUP_REFERENCE="$external_reference" PREVIOUS_RUNTIME="$previous_runtime"
backup_id=$("$SCRIPT_DIR/backup.sh" "${previous_image##*:}-to-${target_image##*:}")
echo "Verifiziertes Release-Backup: $backup_id"
trap - ERR

rollback_on_failure() {
  local code=$?
  trap - ERR
  echo "Deployment fehlgeschlagen (Code $code). Starte automatischen Rollback." >&2
  if ! "$SCRIPT_DIR/rollback.sh" "$backup_id" --automatic; then
    echo "Automatischer Rollback meldet einen Fehler; der Deploymentzustand ist nicht freigegeben." >&2
    exit 70
  fi
  exit "$code"
}
trap rollback_on_failure ERR
update_env_value OPENCLAW_IMAGE "$target_image"
update_env_value OPENCLAW_PROXY_IMAGE "$target_proxy_image"
update_env_value OPENCLAW_MAINTENANCE_IMAGE "$target_maintenance_image"
export OPENCLAW_IMAGE="$target_image"
export OPENCLAW_PROXY_IMAGE="$target_proxy_image"
export OPENCLAW_MAINTENANCE_IMAGE="$target_maintenance_image"

compose --profile maintenance run --rm --no-deps --entrypoint freshclam clamav-update \
  --stdout --datadir=/var/lib/clamav --verbose
compose --profile maintenance run --rm --no-deps --entrypoint python3 clamav-update \
  -P -m personal_assistant.clamav_health
compose up -d ollama-proxy gateway
wait_for_healthy ollama-proxy 180
wait_for_healthy gateway 300
# The single-quoted expression intentionally runs inside the container shell.
# shellcheck disable=SC2016
compose --profile tools run --rm --no-deps agent-cli \
  /bin/sh -c \
  'actual=$(tr -d "\r\n" </opt/openclaw-agent/SOURCE_REVISION); test "$actual" = "$1"' \
  source-revision-check "$expected_source_revision"
echo "Laufende Workspace-Quellrevision verifiziert: $expected_source_revision"
if [[ -n "$mail_relevant_folder" ]]; then
  echo "Aktiviere den explizit freigegebenen Relevant-Ordner im gesicherten Writer-Stopp-Fenster: $mail_relevant_folder"
  echo "Ein create-only angelegter IMAP-Ordner wird bei einem spaeteren lokalen Rollback nicht automatisch geloescht."
  compose --profile tools run --rm --no-deps agent-cli \
    /opt/openclaw-agent/scripts/assistant.sh mail folders activate-relevant \
    --relevant "$mail_relevant_folder" --yes
fi
"$SCRIPT_DIR/smoke-test.sh" "$write_test"
compose --profile maintenance up -d clamav-update
wait_for_healthy clamav-update 180
compose up -d mail-worker sync-worker supervisor-worker portfolio-worker monitor-worker
wait_for_healthy mail-worker 180
wait_for_healthy sync-worker 180
wait_for_healthy supervisor-worker 180
wait_for_healthy portfolio-worker 180
wait_for_healthy monitor-worker 180
compose --profile tools run --rm --no-deps agent-cli \
  /opt/openclaw-agent/scripts/assistant.sh jobs status --target all
if [[ "$previous_runtime" == "legacy-systemd" ]]; then
  assert_legacy_writers_disabled
fi
update_env_value OPENCLAW_CURRENT_RUNTIME docker
trap - ERR

echo "Deployment erfolgreich: $target_image"
echo "Rollback-Punkt: $backup_id"
