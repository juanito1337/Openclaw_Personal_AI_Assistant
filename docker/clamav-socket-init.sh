#!/usr/bin/env bash
set -euo pipefail
umask 007

directory=/run/clamav
[[ -d "$directory" && ! -L "$directory" ]] || {
  echo "ClamAV-Socketvolume fehlt oder ist ein Symlink" >&2
  exit 2
}
# Named volumes retain the daemon ownership across deployments. Normalize the
# directory with the already granted CHOWN capability before chmod; adding the
# broader FOWNER capability is neither necessary nor permitted by the role.
chown 0:0 "$directory"
chmod 0770 "$directory"
rm -f -- "$directory/clamd.sock"
chown 100:101 "$directory"
