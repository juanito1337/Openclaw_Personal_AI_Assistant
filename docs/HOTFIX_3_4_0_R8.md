# Hotfix 3.4.0-r8: Nextcloud durable workspace

The Personal Assistant may now use the configured `Assistent/` Nextcloud root as
persistent workspace storage. Nextcloud remains a connector, not a local mount.

## New tools

```bash
./scripts/assistant.sh nextcloud mkdir --path "Assistent/Projekte/Alpha"
printf '%s' "Text" | ./scripts/assistant.sh nextcloud write-text --path "Assistent/Notizen/test.md"
./scripts/assistant.sh nextcloud upload --local "personal_assistant/data/workspace_outbox/file.pdf" --path "Assistent/Dokumente/file.pdf"
./scripts/assistant.sh nextcloud move --source "Assistent/Dokumente/file.pdf" --destination "Assistent/Archiv/file.pdf"
```

## Security model

- Every remote path is constrained to the configured root.
- Uploads are accepted only from the controlled local outbox.
- New files use `If-None-Match: *`.
- Moves use WebDAV `Overwrite: F`.
- Delete, overwrite and sharing remain hard-denied.
- Every write is an ActionPlan with policy, idempotency and audit.
- Changing the root or expanding rights requires explicit user approval.

## Resource permissions

`nextcloud-files-main` receives:

```text
read, create, organize
```

Discovery preserves these explicit permissions and the configured allowed root.
