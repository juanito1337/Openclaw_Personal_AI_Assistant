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

Discovery stores remote collections in `resources.toml`. The resource registry can be
extended without source-code changes.
