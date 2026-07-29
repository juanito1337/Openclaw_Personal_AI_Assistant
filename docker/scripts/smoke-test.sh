#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(CDPATH= cd "$(dirname "$0")" && pwd)
# shellcheck source=common.sh
. "$SCRIPT_DIR/common.sh"
mode=${1:-${OPENCLAW_WRITE_TEST_ENABLED:-true}}
limit=${OPENCLAW_WRITE_TEST_LIMIT:-3}
log_dir="$OPENCLAW_ROOT/backups/smoke-tests"
mkdir -p "$log_dir"
log="$log_dir/$(date -u +%Y%m%dT%H%M%SZ).log"

run_cli() {
  printf '\n>>> %q ' "$@" | tee -a "$log"
  printf '\n' | tee -a "$log"
  compose --profile tools run --rm --no-deps agent-cli "$@" 2>&1 | tee -a "$log"
}

run_cli /home/node/.openclaw/workspace/scripts/assistant.sh version --verify
run_cli /home/node/.openclaw/workspace/scripts/mail-agent.sh doctor
if [[ "$mode" == "true" ]]; then
  run_cli /home/node/.openclaw/workspace/scripts/mail-agent.sh run --dry-run --limit "$limit" --no-digest
  run_cli /home/node/.openclaw/workspace/scripts/mail-agent.sh production-check
  run_cli /home/node/.openclaw/workspace/scripts/mail-agent.sh run --limit "$limit" --no-digest
else
  run_cli /home/node/.openclaw/workspace/scripts/mail-agent.sh production-check || true
fi

echo "Smoke-Test erfolgreich. Log: $log"
