#!/bin/sh
set -eu
umask 077
SCRIPT_DIR=$(CDPATH='' cd "$(dirname "$0")" && pwd)
WORKSPACE="${OPENCLAW_WORKSPACE:-$(dirname "$SCRIPT_DIR")}"
ENV_FILE="${OLLAMA_PRIORITY_ENV_FILE:-$HOME/.config/openclaw/ollama-priority.env}"
if [ "${OPENCLAW_RUNTIME:-}" != "container" ] && [ -f "$ENV_FILE" ]; then
  set -a
  # Legacy host runtime only. Containers receive strictly parsed values from
  # personal_assistant.container_entrypoint and never source this file.
  # shellcheck disable=SC1090
  . "$ENV_FILE"
  set +a
fi
cd "$WORKSPACE"
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
fi
run_python() {
  if [ "${OPENCLAW_RUNTIME:-}" = "container" ]; then
    exec python3 -P "$@"
  fi
  exec python3 "$@"
}
case "${1:-serve}" in
  serve)
    shift || true
    run_python -m personal_assistant.ollama_priority_proxy "$@"
    ;;
  status)
    shift
    run_python -m personal_assistant.ollama_priority_proxy --status "$@"
    ;;
  check-upstream)
    shift
    run_python -m personal_assistant.ollama_priority_proxy --check "$@"
    ;;
  config)
    shift
    run_python -m personal_assistant.ollama_priority_proxy --print-config "$@"
    ;;
  *)
    echo "Usage: $0 {serve|status|check-upstream|config}" >&2
    exit 2
    ;;
esac
