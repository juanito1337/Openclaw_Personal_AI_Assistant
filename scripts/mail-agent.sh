#!/bin/sh
set -eu
umask 077

SCRIPT_DIR=$(CDPATH= cd "$(dirname "$0")" && pwd)
DEFAULT_WORKSPACE=$(dirname "$SCRIPT_DIR")
WORKSPACE="${OPENCLAW_WORKSPACE:-$DEFAULT_WORKSPACE}"
cd "$WORKSPACE"

# Ohne Argumente direkt den zustandsabhaengigen Einrichtungsassistenten zeigen.
if [ "$#" -eq 0 ]; then
  set -- guide
fi

exec python3 -m mail_agent "$@"
