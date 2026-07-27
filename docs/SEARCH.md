# Knowledge index and search

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
