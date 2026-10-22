#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(CDPATH='' cd "$(dirname "$0")" && pwd)
# shellcheck source=docker/scripts/common.sh
# shellcheck source=common.sh
. "$SCRIPT_DIR/common.sh"
require_command sqlite3
require_command tar
require_command sha256sum

if [[ -e "$OPENCLAW_SECRETS_DIR/nextcloud-connection" || -L "$OPENCLAW_SECRETS_DIR/nextcloud-connection" ]]; then
  require_command docker
  if ! setup_service_is_offline; then
    echo "Backup abgebrochen: Setup-Service muss fuer ein konsistentes Verbindungsbackup gestoppt sein." >&2
    exit 2
  fi
fi

validate_talk_proxy_flag "${OPENCLAW_TALK_PROXY_ENABLED:-false}"
# Caddy renews CA/leaf state independently of the domain writers. Refuse a
# manual online backup rather than taking a possibly inconsistent key store.
# Legacy installations without Talk state retain their offline backup path.
if [[ -d "$OPENCLAW_SECRETS_DIR/talk-proxy-pki" || ${OPENCLAW_TALK_PROXY_ENABLED:-false} == true ]]; then
  require_command docker
  if ! talk_proxy_is_offline; then
    echo "Backup abgebrochen: Talk-Proxy muss fuer ein konsistentes CA-Backup gestoppt sein." >&2
    exit 2
  fi
fi

previous_talk_proxy_enabled=${PREVIOUS_TALK_PROXY_ENABLED:-${OPENCLAW_TALK_PROXY_ENABLED:-false}}
target_talk_proxy_enabled=${TARGET_TALK_PROXY_ENABLED:-${OPENCLAW_TALK_PROXY_ENABLED:-false}}
validate_talk_proxy_flag "$previous_talk_proxy_enabled"
validate_talk_proxy_flag "$target_talk_proxy_enabled"
previous_talk_proxy_ip=${PREVIOUS_TALK_PROXY_IP:-}
previous_talk_nextcloud_ip=${PREVIOUS_TALK_NEXTCLOUD_IP:-}
target_talk_proxy_ip=${TARGET_TALK_PROXY_IP:-${OPENCLAW_TALK_PROXY_IP:-}}
target_talk_nextcloud_ip=${TARGET_TALK_NEXTCLOUD_IP:-${OPENCLAW_TALK_NEXTCLOUD_IP:-}}
if [[ "$previous_talk_proxy_enabled" == true ]]; then
  # Deployment passes the pre-stop snapshot. A manual offline backup reads
  # only those two public values from the stopped container, never from a
  # possibly already edited candidate .env file.
  if [[ ! -v PREVIOUS_TALK_PROXY_IP || ! -v PREVIOUS_TALK_NEXTCLOUD_IP ]]; then
    require_command docker
    previous_talk_proxy_ip=$(talk_proxy_container_address OPENCLAW_TALK_PROXY_IP)
    previous_talk_nextcloud_ip=$(talk_proxy_container_address OPENCLAW_TALK_NEXTCLOUD_IP)
  fi
  validate_talk_proxy_addresses "$previous_talk_proxy_ip" "$previous_talk_nextcloud_ip"
fi
if [[ "$target_talk_proxy_enabled" == true ]]; then
  validate_talk_proxy_addresses "$target_talk_proxy_ip" "$target_talk_nextcloud_ip"
fi

label=${1:-manual}
stamp=$(date -u +%Y%m%dT%H%M%SZ)
safe_label=$(printf '%s' "$label" | tr -cs 'A-Za-z0-9._-' '-')
backup_id="${stamp}_${safe_label}"
destination="$OPENCLAW_BACKUP_DIR/$backup_id"
mkdir -p "$destination"

for required in "$OPENCLAW_STATE_DIR" "$OPENCLAW_CONFIG_DIR" "$OPENCLAW_SECRETS_DIR"; do
  [[ -d "$required" ]] || { echo "Backupquelle fehlt: $required" >&2; exit 2; }
  [[ "$(dirname "$required")" == "$OPENCLAW_ROOT" ]] || {
    echo "Backupquelle muss direkt unter OPENCLAW_ROOT liegen: $required" >&2
    exit 2
  }
done

integrity="$destination/sqlite-integrity.txt"
: > "$integrity"
while IFS= read -r -d '' db; do
  result=$(sqlite3 "$db" 'PRAGMA quick_check;' 2>&1) || {
    printf '%s: %s\n' "$db" "$result" | tee -a "$integrity" >&2
    exit 1
  }
  printf '%s: %s\n' "$db" "$result" >> "$integrity"
  [[ "$result" == "ok" ]] || { echo "SQLite-Pruefung fehlgeschlagen: $db" >&2; exit 1; }
done < <(find "$OPENCLAW_STATE_DIR" -type f \( -name '*.sqlite' -o -name '*.sqlite3' -o -name '*.db' \) -print0)
check_nextcloud_nonce_integrity "$OPENCLAW_SECRETS_DIR/nextcloud-connection"

archive="$destination/payload.tar.gz"
tar --numeric-owner -C "$OPENCLAW_ROOT" -czf "$archive" \
  "$(basename "$OPENCLAW_STATE_DIR")" \
  "$(basename "$OPENCLAW_CONFIG_DIR")" \
  "$(basename "$OPENCLAW_SECRETS_DIR")"
(cd "$destination" && sha256sum payload.tar.gz > payload.tar.gz.sha256)
archive_sha=$(sha256sum "$archive" | awk '{print $1}')
previous=${PREVIOUS_IMAGE:-${OPENCLAW_IMAGE:-unknown}}
target=${TARGET_IMAGE:-${OPENCLAW_IMAGE:-unknown}}
previous_proxy=${PREVIOUS_PROXY_IMAGE:-${OPENCLAW_PROXY_IMAGE:-$previous}}
target_proxy=${TARGET_PROXY_IMAGE:-${OPENCLAW_PROXY_IMAGE:-$target}}
previous_maintenance=${PREVIOUS_MAINTENANCE_IMAGE:-${OPENCLAW_MAINTENANCE_IMAGE:-$previous}}
target_maintenance=${TARGET_MAINTENANCE_IMAGE:-${OPENCLAW_MAINTENANCE_IMAGE:-$target}}
previous_clamd=${PREVIOUS_CLAMD_IMAGE:-${OPENCLAW_CLAMD_IMAGE:-}}
target_clamd=${TARGET_CLAMD_IMAGE:-${OPENCLAW_CLAMD_IMAGE:-$target_maintenance}}
external=${EXTERNAL_BACKUP_REFERENCE:-}
previous_runtime=${PREVIOUS_RUNTIME:-${OPENCLAW_CURRENT_RUNTIME:-docker}}
legacy_home=${OPENCLAW_LEGACY_HOME:-}
legacy_migration_backup=${OPENCLAW_LEGACY_MIGRATION_BACKUP:-}
legacy_migration_member=${OPENCLAW_LEGACY_MIGRATION_MEMBER:-}
legacy_migration_sha256=${OPENCLAW_LEGACY_MIGRATION_SHA256:-}

python3 - "$destination/manifest.json" "$backup_id" "$previous" "$target" "$archive_sha" "$external" "$previous_runtime" "$legacy_home" "$legacy_migration_backup" "$legacy_migration_member" "$legacy_migration_sha256" "$previous_proxy" "$target_proxy" "$previous_maintenance" "$target_maintenance" "$previous_clamd" "$target_clamd" "$previous_talk_proxy_enabled" "$target_talk_proxy_enabled" "$previous_talk_proxy_ip" "$previous_talk_nextcloud_ip" "$target_talk_proxy_ip" "$target_talk_nextcloud_ip" <<'PY'
from datetime import datetime, timezone
import json, os, socket, sys
from pathlib import Path
path=Path(sys.argv[1])
payload={
  "schema_version": 1,
  "backup_id": sys.argv[2],
  "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
  "host": socket.gethostname(),
  "previous_image": sys.argv[3],
  "target_image": sys.argv[4],
  "archive_sha256": sys.argv[5],
  "external_backup_reference": sys.argv[6],
  "previous_runtime": sys.argv[7],
  "legacy_home": sys.argv[8],
  "legacy_migration_backup": sys.argv[9],
  "legacy_migration_member": sys.argv[10],
  "legacy_migration_sha256": sys.argv[11],
  "previous_proxy_image": sys.argv[12],
  "target_proxy_image": sys.argv[13],
  "previous_maintenance_image": sys.argv[14],
  "target_maintenance_image": sys.argv[15],
  "previous_clamd_image": sys.argv[16],
  "target_clamd_image": sys.argv[17],
  "previous_talk_proxy_enabled": sys.argv[18] == "true",
  "target_talk_proxy_enabled": sys.argv[19] == "true",
  "previous_talk_proxy_ip": sys.argv[20],
  "previous_talk_nextcloud_ip": sys.argv[21],
  "target_talk_proxy_ip": sys.argv[22],
  "target_talk_nextcloud_ip": sys.argv[23],
  "state_directory": os.environ.get("OPENCLAW_STATE_DIR", ""),
  "verified": False,
}
path.write_text(json.dumps(payload, indent=2, ensure_ascii=False)+"\n", encoding="utf-8")
PY

"$SCRIPT_DIR/verify-backup.sh" "$destination" >/dev/null
"$SCRIPT_DIR/restore-test.sh" "$destination" >/dev/null
python3 - "$destination/manifest.json" <<'PY'
import json, sys
from pathlib import Path
p=Path(sys.argv[1]); data=json.loads(p.read_text()); data["verified"]=True
p.write_text(json.dumps(data, indent=2, ensure_ascii=False)+"\n")
PY
ln -sfn "$backup_id" "$OPENCLAW_BACKUP_DIR/latest"

retention=${BACKUP_RETENTION_RELEASES:-10}
mapfile -t old < <(find "$OPENCLAW_BACKUP_DIR" -mindepth 1 -maxdepth 1 -type d -printf '%f\n' | sort -r | tail -n +$((retention + 1)))
for item in "${old[@]}"; do rm -rf "${OPENCLAW_BACKUP_DIR:?}/$item"; done

printf '%s\n' "$backup_id"
