# Historische Architektur vor dem Containervertrag

> Archiviert in M1. Dieses Dokument ist kein aktiver Architekturvertrag.

The project is a modular monolith with two operational sub-systems:

1. `mail_agent/` performs mailbox triage and mail-specific actions.
2. `personal_assistant/` provides resources, policies, indexing, search, ActionPlans,
   Nextcloud connectors, and future cross-domain capabilities.

The mail agent is intentionally not folded into the assistant core. This protects the
working mailbox pipeline and isolates Nextcloud/index failures from mail processing.

See `ASSISTANT_ARCHITECTURE.md` for module boundaries and extension rules.
