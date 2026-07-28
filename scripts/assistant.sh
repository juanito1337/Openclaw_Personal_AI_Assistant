#!/bin/sh
set -eu
umask 077
SCRIPT_DIR=$(CDPATH= cd "$(dirname "$0")" && pwd)
WORKSPACE="${OPENCLAW_WORKSPACE:-$(dirname "$SCRIPT_DIR")}"
cd "$WORKSPACE"
if [ "$#" -eq 0 ]; then
  set -- status
fi
exec python3 -m personal_assistant "$@"
