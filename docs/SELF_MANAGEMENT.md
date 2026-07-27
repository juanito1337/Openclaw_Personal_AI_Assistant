# Self-configuration and repair

Self-management is declarative, narrow, audited, and reversible.

## Allowed

- discover and register read-only resources
- change settings listed by `assistant.sh settings list`
- rebuild an index or cache
- retry a completed-safe idempotent action
- diagnose connectors and timers

## Not allowed

- edit Python source
- expand permissions without approval
- install plugins without approval
- reveal or migrate secrets into workspace files
- disable TLS, policy, audit, backup, or dry-run controls
- repeat ambiguous external writes

Every safe setting change creates a config backup and settings-history record. Core
repairs are delivered as migration packages with full workspace backup and rollback.

## Job controller

The desired ON/OFF state of allowlisted background jobs is stored separately from
systemd's observed state. Health checks diagnose and record alerts. One narrowly
defined exception may recover the mail production gate automatically: a bounded
successful dry-run when all required health checks pass and only the dry-run
fingerprint is stale or missing. All other enable/start operations require an
explicit user request through `jobs on` or `jobs restart`. See `JOB_CONTROL.md`.
