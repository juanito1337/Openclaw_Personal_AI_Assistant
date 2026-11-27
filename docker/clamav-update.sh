#!/usr/bin/env bash
set -euo pipefail
umask 077
exec python3 -P -m personal_assistant.clamav_update "$@"
