#!/usr/bin/env bash
set -euo pipefail
mode=${1:-workspace}
workspace=${OPENCLAW_WORKSPACE:-/home/node/.openclaw/workspace}

[[ -r "$workspace/VERSION" ]]
[[ -x "$workspace/scripts/assistant.sh" ]]
[[ -x "$workspace/scripts/mail-agent.sh" ]]

case "$mode" in
  gateway)
    curl --fail --silent --show-error --max-time 8 http://127.0.0.1:${OPENCLAW_GATEWAY_PORT:-18789}/healthz >/dev/null
    ;;
  proxy)
    "$workspace/scripts/ollama-priority-proxy.sh" status >/dev/null
    ;;
  worker)
    job=${2:?job name required}
    file=${OPENCLAW_JOB_STATUS_DIR:-$workspace/personal_assistant/data/container_jobs}/$job.json
    python3 - "$file" <<'PY'
from datetime import datetime, timezone
import json, sys
from pathlib import Path
p=Path(sys.argv[1])
data=json.loads(p.read_text())
ts=datetime.fromisoformat(str(data["updated_at"]).replace("Z", "+00:00"))
if ts.tzinfo is None: ts=ts.replace(tzinfo=timezone.utc)
age=(datetime.now(timezone.utc)-ts.astimezone(timezone.utc)).total_seconds()
if age > 180: raise SystemExit(f"stale heartbeat: {age:.0f}s")
if data.get("state") == "stopped": raise SystemExit("worker stopped")
PY
    ;;
  workspace)
    "$workspace/scripts/assistant.sh" version --verify >/dev/null
    ;;
  *)
    echo "unknown healthcheck mode: $mode" >&2
    exit 2
    ;;
esac
