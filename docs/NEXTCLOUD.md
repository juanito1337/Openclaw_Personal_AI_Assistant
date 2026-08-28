# Nextcloud integration

The central setup writes credentials only to:

```text
~/.config/personal-assistant/secrets.env
```

Use an app password belonging to a dedicated non-admin Nextcloud user. Share only the
required folders, calendars, address books, and task lists with that user.

```bash
./scripts/assistant.sh setup nextcloud
./scripts/assistant.sh nextcloud doctor
./scripts/assistant.sh nextcloud discover
./scripts/assistant.sh nextcloud sync
```

The immutable runtime includes its own controlled Nextcloud/WebDAV connector;
no local Nextcloud mount or separately installed agent skill is required. Use
live `tools list`/`capabilities` and registered command results to determine
availability. A request for a current remote file starts with the live read-only
listing, not with a question about a host path, API or upload:

```bash
./scripts/assistant.sh nextcloud list --path "Assistent"
```

The result identifies the native connector and reports `complete`,
`results_may_be_truncated` and `unexpanded_folder_count`. A depth-limited result
cannot prove that a file is absent; continue with the exact returned subfolder or
use the registered broad `search "<Suchbegriff>"` path as supplementary indexed
evidence. Listing metadata does not by itself expose arbitrary binary file
content.

Archived invoice files have the dedicated read-only route:

```bash
./scripts/assistant.sh invoices status
./scripts/assistant.sh invoices files --limit 100
```

The second command reads the invoice root and resource from validated runtime
tool settings. Its `complete` and `results_may_be_truncated` fields must be
checked before reporting that a file is absent.

## Provider rights

- WebDAV: list/read, create folders, create new files with `If-None-Match: *`
- CardDAV: read-only
- CalDAV: read; creation only through approved ActionPlans
- VTODO: creation only through approved ActionPlans
- no delete or overwrite implementation

Interactive discovery through the core-capable CLI stores remote collections in
`resources.toml`; the resource registry can therefore be extended without source-code
changes. The scheduled `sync-worker` performs the same live discovery without persisting
it because the core registry is intentionally mounted read-only for that role. It writes
only the local knowledge index and sync state. A failed source sync returns a non-zero
status and remains visible as a degraded job instead of attempting a core audit write.
