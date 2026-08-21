#!/bin/sh
set -eu

if [ "${1:-}" = "--version" ] && [ "$#" -eq 1 ]; then
  exec /usr/local/libexec/openclaw/himalaya --version
fi

printf '%s\n' \
  'Direkter Himalaya-Aufruf ist kein registriertes Agentenwerkzeug.' \
  'Fuer Postfachsuchen verwenden: /opt/openclaw-agent/scripts/assistant.sh mail search --query "<Suchbegriff>" --limit 50' \
  'Keine Maildaten mit grep, rg, find oder einer Shell-Pipeline durchsuchen.' >&2
exit 64
