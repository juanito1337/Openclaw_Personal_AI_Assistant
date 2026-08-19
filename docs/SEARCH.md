# Knowledge index and search

## M11 development status

M11.0 has frozen the current behavior with a fully synthetic German/English
mail corpus, Fake IMAP and temporary SQLite databases. The measured starting
point, reproduction command, privacy boundary and known coverage/locator gaps
are documented in
[`MAIL_SEARCH_BASELINE_M110.md`](MAIL_SEARCH_BASELINE_M110.md). M11.0 changes no
schema, ranking, CLI, runtime configuration or productive mailbox. The current
server-side path remains authoritative for current mailbox questions until the
later M11 coverage and live-locator contracts are implemented and accepted.

## Sources

- mail-agent message metadata and summaries
- compact full-text snapshots for newly processed mail
- Nextcloud files below allowlisted roots
- CardDAV contacts
- CalDAV calendar events

## Incremental state

- mail: stable key and last update timestamp
- files: path, ETag, size, SHA-256, and modified time
- contacts: UID and address-book resource
- events: UID, ETag, calendar resource, and configured time horizon

## Query

```bash
./scripts/assistant.sh search "Tankreinigung Wattenbek"
./scripts/assistant.sh search "Rechnung" --source-type nextcloud-file
./scripts/assistant.sh search "Lessingplatz" --source-type email
```

SQLite FTS5 supplies fast lexical search. Metadata filters restrict source type and
resource. Search results contain source IDs, URIs, snippets, and metadata for citation.

Semantic search is deliberately not faked. The provider interface is prepared, but a
local embedding model must be selected and tested before activation.
