#!/usr/bin/env bash
set -euo pipefail

# This hook must create a recoverable snapshot of every remote system the agent
# may modify during the product smoke test: IMAP mailbox and Nextcloud files,
# contacts, calendars and tasks. Replace this example with your TrueNAS/ZFS,
# Nextcloud or provider-specific backup commands.
#
# Print a stable snapshot/reference identifier on stdout. deploy.sh records it
# in the release backup manifest.

echo "External backup hook is not configured" >&2
exit 2
