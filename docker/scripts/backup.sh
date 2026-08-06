#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(CDPATH='' cd "$(dirname "$0")" && pwd)
# shellcheck source=docker/scripts/common.sh
# shellcheck source=common.sh
. "$SCRIPT_DIR/common.sh"
require_command sqlite3
require_command tar
require_command sha256sum

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
external=${EXTERNAL_BACKUP_REFERENCE:-}
previous_runtime=${PREVIOUS_RUNTIME:-${OPENCLAW_CURRENT_RUNTIME:-docker}}
legacy_home=${OPENCLAW_LEGACY_HOME:-}
legacy_migration_backup=${OPENCLAW_LEGACY_MIGRATION_BACKUP:-}
legacy_migration_member=${OPENCLAW_LEGACY_MIGRATION_MEMBER:-}
legacy_migration_sha256=${OPENCLAW_LEGACY_MIGRATION_SHA256:-}

python3 - "$destination/manifest.json" "$backup_id" "$previous" "$target" "$archive_sha" "$external" "$previous_runtime" "$legacy_home" "$legacy_migration_backup" "$legacy_migration_member" "$legacy_migration_sha256" "$previous_proxy" "$target_proxy" "$previous_maintenance" "$target_maintenance" <<'PY'
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
