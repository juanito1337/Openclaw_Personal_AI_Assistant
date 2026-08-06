#!/usr/bin/env bash
set -euo pipefail

backup=${1:?Backup-Verzeichnis oder ID angeben}
SCRIPT_DIR=$(CDPATH='' cd "$(dirname "$0")" && pwd)
# shellcheck source=docker/scripts/common.sh
# shellcheck source=common.sh
. "$SCRIPT_DIR/common.sh"
[[ -d "$backup" ]] || backup="$OPENCLAW_BACKUP_DIR/$backup"
[[ -d "$backup" ]] || { echo "Backup nicht gefunden: $backup" >&2; exit 2; }

archive="$backup/payload.tar.gz"
[[ -f "$archive" && -f "$archive.sha256" && -f "$backup/manifest.json" ]] || {
  echo "Backup ist unvollstaendig: $backup" >&2
  exit 1
}
(cd "$backup" && sha256sum -c payload.tar.gz.sha256)
tar -tzf "$archive" >/dev/null
python3 - "$backup/manifest.json" <<'PY'
import json, sys
from pathlib import Path
p=Path(sys.argv[1])
data=json.loads(p.read_text())
required=("backup_id","created_at","previous_image","target_image","archive_sha256")
missing=[key for key in required if not data.get(key)]
if missing: raise SystemExit("Manifestfelder fehlen: " + ", ".join(missing))
print(json.dumps({"ok": True, "backup_id": data["backup_id"], "previous_image": data["previous_image"]}, ensure_ascii=False))
PY

read_manifest() {
  python3 - "$backup/manifest.json" "$1" <<'PY'
import json, sys
print(json.load(open(sys.argv[1], encoding="utf-8")).get(sys.argv[2], ""))
PY
}

legacy_archive=$(read_manifest legacy_migration_backup)
if [[ -n "$legacy_archive" ]]; then
  legacy_member=$(read_manifest legacy_migration_member)
  expected_sha=$(read_manifest legacy_migration_sha256)
  [[ -f "$legacy_archive" && -n "$legacy_member" && -n "$expected_sha" ]] || {
    echo "Verknuepftes Legacy-Migrationsbackup ist unvollstaendig." >&2
    exit 1
  }
  actual_sha=$(sha256sum "$legacy_archive" | awk '{print $1}')
  [[ "$actual_sha" == "$expected_sha" ]] || {
    echo "SHA-256 des Legacy-Migrationsbackups stimmt nicht." >&2
    exit 1
  }
  tar -tzf "$legacy_archive" \
    "$legacy_member/openclaw.json" \
    "$legacy_member/workspace/scripts/assistant.sh" \
    "$legacy_member/workspace/scripts/mail-agent.sh" >/dev/null
fi
