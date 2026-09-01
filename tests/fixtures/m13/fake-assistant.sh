#!/bin/sh
set -eu

command_line="$*"
case "$command_line" in
  "version --verify")
    printf '%s\n' '{"ok":true,"product":"OpenClaw Local Personal Assistant","version":"3.4.0-r28","complete":true}'
    ;;
  "status")
    printf '%s\n' '{"ok":true,"complete":true,"checked_at":"2026-09-01T00:00:00Z"}'
    ;;
  "tools list")
    printf '%s\n' '[{"id":"mail.search","command":"./scripts/assistant.sh mail search --query \"<Suchbegriff>\" --limit 50"},{"id":"nextcloud.tasks.status","command":"./scripts/assistant.sh tasks status"},{"id":"nextcloud.tasks.update","command":"./scripts/assistant.sh tasks update --uid \"<UID>\" --expected-title \"<aktueller Titel>\" --status COMPLETED --yes"}]'
    ;;
  "mail search --query Synthetic --limit 50")
    printf '%s\n' '{"ok":true,"complete":true,"results_may_be_truncated":false,"folder_errors":[],"results":[{"folder":"INBOX","mailbox_id":"42","subject":"Synthetic result"}]}'
    ;;
  "mail search --query Partial --limit 50")
    printf '%s\n' '{"ok":true,"complete":false,"results_may_be_truncated":true,"folder_errors":[{"folder":"Archive","error":"synthetic-timeout"}],"results":[]}'
    ;;
  "nextcloud list --path Assistent")
    printf '%s\n' '{"ok":true,"complete":true,"results_may_be_truncated":false,"files":[{"path":"Assistent/Synthetic.txt"}]}'
    ;;
  "tasks status")
    printf '%s\n' '{"ok":true,"complete":true,"allow_list":true,"allow_update":true}'
    ;;
  "tasks update --uid synthetic-uid --expected-title Synthetic task --status COMPLETED --yes")
    printf '%s\n' '{"ok":true,"complete":true,"postcondition_verified":true,"uid":"synthetic-uid","status":"COMPLETED","synthetic":true}'
    ;;
  "portfolio holdings")
    printf '%s\n' '{"ok":true,"complete":true,"holdings":[]}'
    ;;
  *)
    printf '%s\n' "synthetic unsupported invocation: $command_line" >&2
    exit 64
    ;;
esac
