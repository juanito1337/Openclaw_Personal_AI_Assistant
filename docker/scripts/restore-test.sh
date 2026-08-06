#!/usr/bin/env bash
set -euo pipefail
backup=${1:?Backup-Verzeichnis oder ID angeben}
SCRIPT_DIR=$(CDPATH='' cd "$(dirname "$0")" && pwd)
# shellcheck source=docker/scripts/common.sh
# shellcheck source=common.sh
. "$SCRIPT_DIR/common.sh"
[[ -d "$backup" ]] || backup="$OPENCLAW_BACKUP_DIR/$backup"
"$SCRIPT_DIR/verify-backup.sh" "$backup" >/dev/null
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
tar -xzf "$backup/payload.tar.gz" -C "$work"
for required in state config secrets; do
  [[ -d "$work/$required" ]] || { echo "Restore-Test: $required fehlt" >&2; exit 1; }
done
while IFS= read -r -d '' db; do
  result=$(sqlite3 "$db" 'PRAGMA quick_check;' 2>&1) || { echo "$db: $result" >&2; exit 1; }
  [[ "$result" == "ok" ]] || { echo "$db: $result" >&2; exit 1; }
done < <(find "$work/state" -type f \( -name '*.sqlite' -o -name '*.sqlite3' -o -name '*.db' \) -print0)
echo "Restore-Test erfolgreich: $backup"
