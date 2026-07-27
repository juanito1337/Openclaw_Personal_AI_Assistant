# Capabilities and permissions

Capabilities are represented by resource permissions and enforced by policy before an
external action is planned or executed.

## Safe autonomous capabilities

- read and search indexed data
- read-only resource discovery
- incremental synchronization
- cache and index rebuild
- settings explicitly listed by `assistant.sh settings list`

## Approval-required capabilities

- create calendar events
- create tasks
- activate credentials
- expand resource permissions
- install plugins or skills
- enable new timers

## Hard-denied capabilities

- file overwrite or delete
- contact write or delete
- calendar/task delete
- arbitrary core-code modification
- TLS/audit/security disable
- secret disclosure

`personal_assistant/capabilities.json` is the machine-readable summary. `policies.toml`
is the local enforceable policy. Prose documentation does not override code-enforced
policy.
