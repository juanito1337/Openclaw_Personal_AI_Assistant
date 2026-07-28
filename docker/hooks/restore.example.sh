#!/usr/bin/env bash
set -euo pipefail
reference=${1:?Externe Snapshot-Referenz fehlt}

# Restore the IMAP/Nextcloud/CardDAV/CalDAV snapshot created by pre-deploy.sh.
# Replace this example with provider-specific restore commands.
echo "External restore hook is not configured for: $reference" >&2
exit 2
