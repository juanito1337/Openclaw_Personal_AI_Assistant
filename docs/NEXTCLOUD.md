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
