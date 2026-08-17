#!/usr/bin/env bash
set -euo pipefail

image=${1:?Maintenance-Image angeben}

common=(
  docker run --rm --read-only
  --cap-drop ALL
  --security-opt no-new-privileges:true
)

"${common[@]}" --network none --entrypoint /usr/bin/freshclam "$image" --version >/dev/null
"${common[@]}" --network none --entrypoint /usr/bin/clamscan "$image" --version >/dev/null
"${common[@]}" --entrypoint python3 "$image" \
  -P -m personal_assistant.clamav_transport

echo "ClamAV-Maintenance-Laufzeit und TLS-Transport verifiziert: $image"
