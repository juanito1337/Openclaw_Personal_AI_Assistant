#!/usr/bin/env bash
set -euo pipefail
mode=${1:-workspace}
workspace=${OPENCLAW_WORKSPACE:-/home/node/.openclaw/workspace}
image_root=${OPENCLAW_IMAGE_ROOT:-/opt/openclaw-agent}

[[ -r "$image_root/VERSION" ]]
[[ -x "$image_root/scripts/assistant.sh" ]]
[[ -x "$image_root/scripts/mail-agent.sh" ]]

case "$mode" in
  gateway)
    curl --fail --silent --show-error --max-time 8 "http://127.0.0.1:${OPENCLAW_GATEWAY_PORT:-18789}/healthz" >/dev/null
    ;;
  proxy)
    # Docker health processes do not inherit variables parsed by PID 1. The
    # listener is a protected M4 constant; /healthz also probes the upstream.
    curl --fail --silent --show-error --max-time 8 "http://127.0.0.1:11435/healthz" >/dev/null
    ;;
  worker|worker-readiness|worker-business)
    job=${2:?job name required}
    file=${OPENCLAW_JOB_STATUS_DIR:-$workspace/personal_assistant/data/container_jobs}/$job.json
    python3 -P -m personal_assistant.container_health "${mode#worker}" "$file"
    ;;
  clamav)
    python3 -P -m personal_assistant.clamav_health >/dev/null
    ;;
  workspace)
    "$image_root/scripts/assistant.sh" version --verify >/dev/null
    ;;
  *)
    echo "unknown healthcheck mode: $mode" >&2
    exit 2
    ;;
esac
