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

M11.2 adds two registered operational contracts without changing that search
precedence:

```bash
./scripts/assistant.sh mail index plan
./scripts/assistant.sh mail index backfill \
  --page-size 50 --max-pages 200 --max-messages 10000 \
  --max-bytes 1000000000 --max-message-bytes 100000000 \
  --max-runtime 3600 --request-interval 0.2 --yes
```

The plan inventories readable folders and reports paging, raw fetch, UID,
UIDVALIDITY, UIDNEXT, MODSEQ, CONDSTORE, QRESYNC and IDLE independently. It does
not write an index. The second command needs the unchanged explicit approval,
holds the mail-owner lock and writes only a private local checkpoint and v2
staging projection. Page, message, byte, single-message, run-time and request
interval limits are mandatory. Completed page partitions precede their atomic
checkpoint, so a crash repeats at most one deterministic page.

Every complete RFC822 message and every decoded physical attachment passes the
existing fail-closed ClamAV gate before parsing/body publication. A finding,
scanner error or size/decode failure leaves only a content-free checkpoint
status and makes coverage incomplete. Attachment bytes are not full-text indexed
or sent externally.

Himalaya 1.2 proves numbered paging and raw export, but does not expose the IMAP
UIDVALIDITY/delta capability set through this connector. Its bounded page-number
fallback therefore uses mailbox ID plus raw digest for occurrence identity and
must publish `complete=false`, even after every page was read. No UID, cursor or
absence proof is invented. The staging root is
`<mail-data>/search_backfill_v2/projection`; it does not replace the active v1
root before M11.3.

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
