# Historische Personal-Assistant-Architektur vor dem Containervertrag

> Archiviert in M1. Dieses Dokument ist kein aktiver Architekturvertrag.

## Modular monolith

The assistant remains one local Python codebase with separate systemd jobs and SQLite
state. This keeps operations simple while enforcing strong module boundaries.

- `mail_agent/`: mailbox triage and mail-specific automation
- `personal_assistant/connectors/`: technical integrations
- `personal_assistant/knowledge.py`: ingestion and indexing
- `personal_assistant/registry.py`: dynamic accounts, calendars, address books, task lists, and roots
- `personal_assistant/policy.py`: hard safety decisions
- `personal_assistant/actions.py`: ActionPlan and execution boundary
- `personal_assistant/storage.py`: assistant database, FTS index, sync state, audit, and outbox
- `personal_assistant/settings.py`: narrow safe setting changes

## Connector boundary

The core depends on connector interfaces, not free-form skills. The existing local
Nextcloud skill remains available for legacy mail-calendar functions, but the new core
uses restricted WebDAV, CardDAV, and CalDAV providers directly. This avoids granting
the language model unrestricted Nextcloud commands.

## Failure isolation

Mail processing and knowledge synchronization use independent services. A Nextcloud,
indexing, or document-extraction problem cannot block inbox triage.

## Future modules

Signal, projects, properties, taxes, tasks, and additional mail accounts must register
resources and capabilities rather than adding special cases to the core.
