#!/usr/bin/env bash
set -euo pipefail
umask 077

SCRIPT_DIR=$(CDPATH='' cd "$(dirname "$0")" && pwd)
# shellcheck source=docker/scripts/common.sh
# shellcheck source=common.sh
. "$SCRIPT_DIR/common.sh"

backup=${1:?Backup-Verzeichnis oder ID angeben}
[[ ${OPENCLAW_RESTORE_OFFLINE:-} == "YES" ]] || {
  echo "Lokaler Restore ist nur nach bestaetigtem Stop aller Writer erlaubt (OPENCLAW_RESTORE_OFFLINE=YES)." >&2
  exit 2
}
require_command rsync
require_command sqlite3
require_command tar
"$SCRIPT_DIR/verify-backup.sh" "$backup" >/dev/null
[[ -d "$backup" ]] || backup="$OPENCLAW_BACKUP_DIR/$backup"

for target in "$OPENCLAW_STATE_DIR" "$OPENCLAW_CONFIG_DIR" "$OPENCLAW_SECRETS_DIR"; do
  [[ "$(dirname "$target")" == "$OPENCLAW_ROOT" ]] || {
    echo "Restore-Ziel muss direkt unter OPENCLAW_ROOT liegen: $target" >&2
    exit 2
  }
  [[ -d "$target" ]] || {
    echo "Restore-Ziel fehlt; setup-host.sh muss die geschuetzte Hoststruktur anlegen: $target" >&2
    exit 1
  }
done

restore_root=$(mktemp -d)
cleanup_restore() {
  rm -rf -- "$restore_root"
}
trap cleanup_restore EXIT
tar -xzf "$backup/payload.tar.gz" -C "$restore_root"
for name in state config secrets; do
  source="$restore_root/$name"
  [[ -d "$source" ]] || { echo "Restore-Quelle fehlt im Backup: $name" >&2; exit 1; }
done
while IFS= read -r -d '' database; do
  result=$(sqlite3 "$database" 'PRAGMA quick_check;' 2>&1) || {
    echo "$database: $result" >&2
    exit 1
  }
  [[ "$result" == "ok" ]] || { echo "$database: $result" >&2; exit 1; }
done < <(find "$restore_root/state" -type f \( -name '*.sqlite' -o -name '*.sqlite3' -o -name '*.db' \) -print0)

rsync -a --delete "$restore_root/state/" "$OPENCLAW_STATE_DIR/"
rsync -a --delete "$restore_root/config/" "$OPENCLAW_CONFIG_DIR/"
rsync -a --delete "$restore_root/secrets/" "$OPENCLAW_SECRETS_DIR/"
echo "Lokaler Zustand verifiziert wiederhergestellt: $(basename "$backup")"
