#!/usr/bin/env bash
set -euo pipefail
umask 007

directory=/run/clamav
[[ -d "$directory" && ! -L "$directory" ]] || {
  echo "ClamAV-Socketvolume fehlt oder ist ein Symlink" >&2
  exit 2
}
rm -f -- "$directory/clamd.sock"
chmod 0770 "$directory"
chown 100:101 "$directory"
