# Historisches Sicherheitsmodell vor M1

> Archiviert in M1. Der aktuelle Sicherheitsvertrag steht in `SECURITY.md` im
> Repository-Root und unter `docs/architecture/TRUST_BOUNDARIES.md`.

Mail, attachments, Nextcloud documents, contact notes, calendar text, tasks, Signal
messages, and model output are untrusted data. They never grant authority.

## Enforcement layers

- resource permissions describe technical scope
- policy rules deny unsafe action classes
- ActionPlans separate analysis from execution
- idempotency prevents duplicate external writes
- approvals gate calendar/task creation and permission expansion
- audit records every controlled setting or external action

## Hard boundaries

No autonomous source-code changes, plugin installation, permission expansion, secret
disclosure, TLS disable, audit disable, file overwrite/delete, contact write/delete,
or calendar/task deletion.

## Secrets

The central file is `~/.config/personal-assistant/secrets.env` with mode `0600`.
`~/.config/mail-agent.env` remains a compatibility fallback. Neither belongs in Git,
logs, prompts, or memory.

## Mail safety

All existing 3.3.1 protections remain: bounded drain, interactive-only force override,
ZIP forwarding, no Sent-copy append for forwards, deterministic outbound Message-ID,
and no retry after `delivery-uncertain`.
