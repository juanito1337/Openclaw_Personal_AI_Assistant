#!/bin/sh
set -eu
umask 077

SCRIPT_DIR=$(CDPATH='' cd "$(dirname "$0")" && pwd)
DEFAULT_WORKSPACE=$(dirname "$SCRIPT_DIR")
WORKSPACE="${OPENCLAW_WORKSPACE:-$DEFAULT_WORKSPACE}"
cd "$WORKSPACE"

# Ohne Argumente direkt den zustandsabhaengigen Einrichtungsassistenten zeigen.
if [ "$#" -eq 0 ]; then
  set -- guide
fi

if [ "${OPENCLAW_RUNTIME:-}" = "container" ]; then
  IMAGE_ROOT=${OPENCLAW_IMAGE_ROOT:-/opt/openclaw-agent}
  case "$SCRIPT_DIR" in
    "$IMAGE_ROOT"/scripts) ;;
    *)
      echo "Containerstart aus beschreibbarem oder unbekanntem Skriptpfad abgelehnt: $SCRIPT_DIR" >&2
      exit 126
      ;;
  esac
  export PYTHONPATH="$IMAGE_ROOT"
  export PYTHONSAFEPATH=1
  exec python3 -P -m mail_agent "$@"
fi
exec python3 -m mail_agent "$@"
