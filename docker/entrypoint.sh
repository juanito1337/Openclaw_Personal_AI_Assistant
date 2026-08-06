#!/usr/bin/env bash
set -euo pipefail
umask 077

# Environment files are parsed as data by container_entrypoint. No file is
# evaluated as shell code and no directory is searched for additional files.
exec python3 -P -m personal_assistant.container_entrypoint "$@"
