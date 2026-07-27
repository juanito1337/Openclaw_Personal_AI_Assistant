# Personal Assistant 3.4.0-r6

## Architecture

The Personal Assistant is the only agent. The former mail agent is retained as a
stable, versioned mail tool. Normal operation is exposed through
`./scripts/assistant.sh mail ...`.

## New tools

- `assistant.sh tools list`
- `assistant.sh nextcloud list --path Assistent`
- `assistant.sh mail status|doctor|dry-run|run`
- automatic invoice archive through ActionPlan
- trusted owner calendar-command mail

## Invoice archive

Unambiguous routine invoice PDFs are passed from the mail tool to the
Personal-Assistant ActionPlan/Outbox. Uploads are create-only, restricted to the
configured Nextcloud root, deduplicated by SHA-256 and idempotency key, and audited.
Ambiguous attachments remain in review.

Default target:

```text
Assistent/Rechnungen/YYYY/MM/
```

## Calendar command mail

A mail creates an event only when the exact sender allowlist and subject prefix
match. Default prefix:

```text
[ASSISTENT TERMIN]
```

The selected calendar receives `read, create`; discovery preserves this explicit
permission. The mail body is only event data and cannot execute other tools.

## Nextcloud access

Nextcloud is not a local directory. Use the controlled connector and the
`nextcloud list`, `nextcloud sync`, and `search` commands.

## Central configuration

- Secrets: `~/.config/personal-assistant/secrets.env`
- Tool settings: `personal_assistant/tools.toml`
- Resources and permissions: `personal_assistant/resources.toml`
