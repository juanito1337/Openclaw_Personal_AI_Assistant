#!/usr/bin/env bash
set -euo pipefail

image=${1:?Maintenance-Image angeben}

common=(
  docker run --rm --read-only
  --cap-drop ALL
  --security-opt no-new-privileges:true
)

case ${2:-} in
  --signatures|--recovery)
    # Match compose.yaml's project/volume contract without resolving service
    # environment files or starting another updater. Only an existing database
    # may be mounted: Docker must not silently bootstrap an empty volume here.
    project=${COMPOSE_PROJECT_NAME:-openclaw-agent}
    [[ "$project" =~ ^[a-z0-9][a-z0-9_-]*$ ]] || {
      echo "Ungueltiges Compose-Projekt fuer die ClamAV-Signaturpruefung." >&2
      exit 2
    }
    volume="${project}_clamav-db"
    identity=$(docker volume inspect --format \
      '{{.Name}} {{index .Labels "com.docker.compose.project"}} {{index .Labels "com.docker.compose.volume"}}' \
      "$volume") || {
      echo "Bestehendes ClamAV-Signaturvolume ist nicht verifizierbar; Writer bleiben unveraendert." >&2
      exit 1
    }
    [[ "$identity" == "$volume $project clamav-db" ]] || {
      echo "ClamAV-Signaturvolume gehoert nicht zum ausgewaehlten Compose-Projekt." >&2
      exit 1
    }
    if [[ "$2" == --recovery ]]; then
      python3 "$(dirname "$0")/check-clamav-recovery.py" "$image" "$volume"
      echo "Isolierter ClamAV-Recovery-Start auf verifizierter Datenbankgeneration bestaetigt."
      exit 0
    fi
    "${common[@]}" --network bridge --user 100:101 \
      --mount "type=volume,src=$volume,dst=/var/lib/clamav,readonly,volume-nocopy" \
      --env CLAMAV_DATABASE_DIR=/var/lib/clamav \
      --entrypoint python3 "$image" -P -m personal_assistant.clamav_vendor --check
    echo "Vorhandene ClamAV-Signaturen mit Kandidat-Maintenance-Laufzeit verifiziert."
    exit 0
    ;;
  '') ;;
  *) echo "Unbekannte Maintenance-Pruefoption." >&2; exit 2 ;;
esac

"${common[@]}" --network none --entrypoint /usr/bin/freshclam "$image" --version >/dev/null
"${common[@]}" --network none --entrypoint /usr/bin/clamscan "$image" --version >/dev/null
"${common[@]}" --network none --entrypoint /usr/sbin/clamd "$image" --version >/dev/null
"${common[@]}" --network none --entrypoint python3 "$image" \
  -P -c 'import personal_assistant.clamd_client; import personal_assistant.clamd_health'
"${common[@]}" --entrypoint python3 "$image" \
  -P -m personal_assistant.clamav_transport

echo "ClamAV-Maintenance-Laufzeit und TLS-Transport verifiziert: $image"
