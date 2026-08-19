# Knowledge index and search

## M11 development status

M11.0 froze the current behavior with a fully synthetic German/English mail
corpus, Fake IMAP and temporary SQLite databases. The measured starting point,
reproduction command, privacy boundary and known coverage/locator gaps are in
[`MAIL_SEARCH_BASELINE_M110.md`](MAIL_SEARCH_BASELINE_M110.md).

M11.1 defines, but does not activate, projection schema v2. Immutable content and
occurrence records are grouped by immutable folder partitions and published by
one atomically replaced root manifest. Content identity is bound to resource and
raw SHA-256; Message-ID is evidence, not a deduplication key. Mutable folder,
UID and quarantine locator data cannot change a content digest. A complete v2
root is valid only when all expected partitions were reconciled completely and
authoritatively; only such a partition may publish tombstones. The precise
identity, crash, migration and rollback rules are recorded in
[ADR-0026](architecture/adr/0026-versionierter-mail-suchdatenvertrag.md).

Projection v1 remains readable. Its additive republication uses a separate
staging root and remains explicitly incomplete because v1 cannot prove complete
account coverage. Knowledge schema v2 adds generation, content, occurrence,
locator, tag, thread-edge and embedding-version fields without removing existing
documents or sync history. Incomplete v2 roots are rejected before the first
index write.

M11.1 changes no ranking, public search command, runtime mounts, job state or
productive mailbox. The current server-side path remains authoritative for
current mailbox questions until later M11 coverage and live-locator contracts
are implemented and accepted.

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
