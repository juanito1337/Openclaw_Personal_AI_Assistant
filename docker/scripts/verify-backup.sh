#!/usr/bin/env bash
set -euo pipefail

backup=${1:?Backup-Verzeichnis oder ID angeben}
SCRIPT_DIR=$(CDPATH= cd "$(dirname "$0")" && pwd)
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
