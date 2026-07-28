#!/usr/bin/env bash
set -euo pipefail
WORKSPACE="${OPENCLAW_WORKSPACE:-$HOME/.openclaw/workspace}"
cd "$WORKSPACE"
exec ./scripts/assistant.sh nextcloud list --path "${1:-Assistent}" --max-depth "${2:-3}"
