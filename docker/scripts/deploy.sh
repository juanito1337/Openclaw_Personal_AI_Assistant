#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(CDPATH= cd "$(dirname "$0")" && pwd)
# shellcheck source=common.sh
. "$SCRIPT_DIR/common.sh"

if [[ ${EUID:-$(id -u)} -eq 0 ]]; then
  echo "Bitte als normaler Benutzer mit Docker-Rechten ausfuehren, nicht mit sudo/root." >&2
  exit 2
fi
require_command docker
require_command python3

target_arg=${1:?Image-Tag oder vollstaendige Image-Referenz angeben}
repository=${OPENCLAW_IMAGE_REPOSITORY:-ghcr.io/juanito1337/openclaw_personal_ai_assistant}
if [[ "$target_arg" == */*:* || "$target_arg" == *@sha256:* ]]; then
  target_image=$target_arg
else
  target_image="$repository:$target_arg"
fi
previous_image=${OPENCLAW_IMAGE:-}
previous_runtime=${OPENCLAW_CURRENT_RUNTIME:-docker}
[[ -n "$previous_image" ]] || { echo "OPENCLAW_IMAGE fehlt in $ENV_FILE" >&2; exit 2; }
[[ "$target_image" != "$previous_image" ]] || echo "Hinweis: Zielimage entspricht dem aktuell eingetragenen Image."

write_test=${OPENCLAW_WRITE_TEST_ENABLED:-true}
require_external=${REQUIRE_EXTERNAL_BACKUP_FOR_WRITE_TEST:-true}
backup_hook=${OPENCLAW_EXTERNAL_BACKUP_HOOK:-}
restore_hook=${OPENCLAW_EXTERNAL_RESTORE_HOOK:-}
if [[ "$write_test" == "true" && "$require_external" == "true" ]]; then
  [[ -x "$backup_hook" ]] || { echo "Externer Backup-Hook fehlt oder ist nicht ausfuehrbar: $backup_hook" >&2; exit 2; }
  [[ -x "$restore_hook" ]] || { echo "Externer Restore-Hook fehlt oder ist nicht ausfuehrbar: $restore_hook" >&2; exit 2; }
fi

echo "Ziehe Zielimage: $target_image"
docker pull "$target_image"
# Pin remote images to the immutable registry digest. Local-only images do not
# have RepoDigests and intentionally keep their explicit local tag.
resolved_target=$(docker image inspect --format '{{range .RepoDigests}}{{println .}}{{end}}' "$target_image" 2>/dev/null | awk 'NF {print; exit}')
if [[ -n "$resolved_target" ]]; then
  echo "Verwende unveraenderlichen Image-Digest: $resolved_target"
  target_image=$resolved_target
fi

echo "Stoppe alle schreibenden Laufzeiten."
if [[ "$previous_runtime" == "legacy-systemd" ]]; then
  for unit in mail-agent.timer mail-agent.service personal-assistant-sync.timer personal-assistant-sync.service personal-assistant-supervisor.timer personal-assistant-supervisor.service ollama-priority-proxy.service openclaw-gateway.service; do
    systemctl --user stop "$unit" >/dev/null 2>&1 || true
  done
else
  compose stop mail-worker sync-worker supervisor-worker gateway ollama-proxy >/dev/null 2>&1 || true
fi

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

external_reference=""
if [[ -n "$backup_hook" && -x "$backup_hook" ]]; then
  echo "Erzeuge externen Snapshot fuer IMAP/Nextcloud/CardDAV/CalDAV."
  external_reference=$(PREVIOUS_IMAGE="$previous_image" TARGET_IMAGE="$target_image" "$backup_hook")
  [[ -n "$external_reference" ]] || { echo "Externer Backup-Hook lieferte keine Referenz." >&2; false; }
fi

export PREVIOUS_IMAGE="$previous_image" TARGET_IMAGE="$target_image" EXTERNAL_BACKUP_REFERENCE="$external_reference" PREVIOUS_RUNTIME="$previous_runtime"
backup_id=$("$SCRIPT_DIR/backup.sh" "${previous_image##*:}-to-${target_image##*:}")
echo "Verifiziertes Release-Backup: $backup_id"
trap - ERR

rollback_on_failure() {
  local code=$?
  trap - ERR
  echo "Deployment fehlgeschlagen (Code $code). Starte automatischen Rollback." >&2
  "$SCRIPT_DIR/rollback.sh" "$backup_id" --automatic || true
  exit "$code"
}
trap rollback_on_failure ERR
update_env_value OPENCLAW_IMAGE "$target_image"
export OPENCLAW_IMAGE="$target_image"

compose --profile maintenance run --rm --entrypoint freshclam clamav-update --verbose || true
compose up -d ollama-proxy gateway
wait_for_healthy ollama-proxy 180
wait_for_healthy gateway 300
"$SCRIPT_DIR/smoke-test.sh" "$write_test"
compose --profile maintenance up -d clamav-update
compose up -d mail-worker sync-worker supervisor-worker portfolio-worker
wait_for_healthy mail-worker 180
wait_for_healthy sync-worker 180
wait_for_healthy supervisor-worker 180
wait_for_healthy portfolio-worker 180
compose --profile tools run --rm --no-deps agent-cli \
  /home/node/.openclaw/workspace/scripts/assistant.sh jobs status --target all
update_env_value OPENCLAW_CURRENT_RUNTIME docker
trap - ERR

echo "Deployment erfolgreich: $target_image"
echo "Rollback-Punkt: $backup_id"
