#!/bin/sh
set -eu
umask 077
SCRIPT_DIR=$(CDPATH= cd "$(dirname "$0")" && pwd)
WORKSPACE="${OPENCLAW_WORKSPACE:-$(dirname "$SCRIPT_DIR")}"
ENV_FILE="${OLLAMA_PRIORITY_ENV_FILE:-$HOME/.config/openclaw/ollama-priority.env}"
if [ -f "$ENV_FILE" ]; then
  set -a
  # This file is created by the r20 installer with shell-safe quoted values.
  . "$ENV_FILE"
  set +a
fi
cd "$WORKSPACE"
case "${1:-serve}" in
  serve)
    shift || true
    exec python3 -m personal_assistant.ollama_priority_proxy "$@"
    ;;
  status)
    shift
    exec python3 -m personal_assistant.ollama_priority_proxy --status "$@"
    ;;
  check-upstream)
    shift
    exec python3 -m personal_assistant.ollama_priority_proxy --check "$@"
    ;;
  config)
    shift
    exec python3 -m personal_assistant.ollama_priority_proxy --print-config "$@"
    ;;
  *)
    echo "Usage: $0 {serve|status|check-upstream|config}" >&2
    exit 2
    ;;
esac
