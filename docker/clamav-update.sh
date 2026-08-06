#!/usr/bin/env bash
set -euo pipefail
umask 077

database=${CLAMAV_DATABASE_DIR:-/var/lib/clamav}
interval=${CLAMAV_UPDATE_INTERVAL_SECONDS:-14400}
case "$interval" in
  ''|*[!0-9]*) echo "Ungueltiges CLAMAV_UPDATE_INTERVAL_SECONDS" >&2; exit 2 ;;
esac
(( interval >= 300 )) || { echo "ClamAV-Updateintervall muss mindestens 300s betragen" >&2; exit 2; }

while true; do
  if ! freshclam --stdout --datadir="$database"; then
    echo "ClamAV-Signaturupdate fehlgeschlagen; Readiness bleibt fail-closed" >&2
  fi
  sleep "$interval" &
  wait $!
done
