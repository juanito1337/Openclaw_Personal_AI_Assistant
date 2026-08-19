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
root without a separate productive connector and rollout approval.

M11.3 implements the authoritative incremental projection and knowledge-index
path defined in [ADR-0028](architecture/adr/0028-transaktionale-mail-reconciliation.md):

```bash
./scripts/assistant.sh mail index reconcile \
  --max-folders 500 --max-messages 100000 \
  --max-bytes 2000000000 --max-message-bytes 100000000 \
  --max-runtime 3600 --request-interval 0.2 \
  --retention-generations 2 --yes
```

The local-write command needs approval
`explicit-user-local-mail-index-reconcile`, holds the existing mail-owner lock
and never writes IMAP. It accepts a generation only after every released folder
was scanned completely and authoritatively. A partial scan, network error,
limit, ClamAV block or crash before root publication preserves the previous root
and cursor. A valid root published before a cursor crash is safe to replay.

Verified locator-only moves, folder renames and quarantine changes reuse content,
parser output, chunks, FTS and embeddings. An ambiguous move may fetch exactly
the affected raw message for SHA-256 verification, but unchanged content is not
parsed or indexed again. New or changed content, and a changed ClamAV scanner
identity, pass the fail-closed scan gate. Copy, move, disappearance, reappearance
and UIDVALIDITY reset remain distinct occurrence/locator events.

The sync worker applies a complete v2 generation and its cursor in one SQLite
transaction. A locator-only delta changes no FTS row. Projection retention keeps
the active and at least one verified rollback generation and never removes mail
source or the knowledge database. Technical counters report work without bodies,
addresses or subjects.

The bounded scheduler policy `mail-index` is registered but intentionally has no
activatable JobSpec, worker dispatch or deployment service in M11.3. The current
Himalaya 1.2 adapter also cannot prove UID, UIDVALIDITY and stable folder IDs, so
the live command fails closed with `authoritative-connector-required`. Connector
rollout, job activation and productive reconciliation are not part of M11.3.

M11.4 adds a separate registered read-only query path without changing the
server-search precedence:

```bash
./scripts/assistant.sh mail search-local --query "Rechnung ZX-2048" --limit 50
./scripts/assistant.sh mail search-local --query "Projekt" \
  --sender "sender@example.invalid" --folder "INBOX" \
  --after "2026-01-01" --before "2026-12-31" --limit 50
./scripts/assistant.sh mail search-local --query "" \
  --has-attachment yes --attachment-type pdf --tag "category:invoice"
```

`mail search-local` reads only the validated knowledge index. It never writes an
IMAP flag, provider label or tag. At least a query or one structured filter is
required. Available filters are `--sender`, `--participant`, `--after`,
`--before`, `--folder`, `--category`, `--review-reason`,
`--has-attachment yes|no`, `--attachment-type` and repeatable `--tag
<namespace>:<value>`. Date-only `--before` is inclusive for that calendar day.

The safe query grammar normalizes NFKC and accepts at most 500 characters and 24
terms. It supports quoted phrases and a bounded suffix `*` prefix search. Every
term is generated as quoted FTS input; user-provided `OR`, `NOT`, `NEAR`, quotes,
parentheses and punctuation never become executable raw FTS syntax.

Mail FTS separates subject, sender and body. BM25 weights are 8.0, 4.0 and 1.0.
An exact phrase receives +2.0 and an exact sender +3.0. No recency boost is
applied, so an old exact hit is not silently displaced; the returned ranking
object explains every component. Matching chunks are grouped by mail before the
final limit. Snippets are centered on the best match, bounded to 320 characters
and stripped of HTML, terminal escapes and control characters.

Active local tag namespaces are closed to `folder`, `sender`, `sender-domain`,
`participant`, `has`, `attachment-type`, `category`, `review`, `kind`, `year`,
`month` and `quarantine`. Every row carries source, source version, confidence,
structured evidence, activity and uncertainty. Missing evidence makes a
declared domain tag inactive; every model result remains an inactive
`model-proposal`. Current folder and quarantine tags are rebuilt only from the
current locator set, so an external move changes no body FTS row.

M11.2 backfill and M11.3 reconciliation resolve declared tags through a
query-only SQLite connection to the existing mail-owner database. Typed stored
classification decisions provide `category`, typed review decisions provide
`review`, and existing invoice/order/calendar extractor records provide `kind`
(plus the corresponding invoice/order category used by the CLI filter). The
resolver never creates or migrates that database, never invokes Ollama and never
uses a free model label. If no corresponding typed local record exists, only
parser- and locator-derived structural tags are published.

Always inspect `complete`, `results_may_be_truncated` and `index`. A local empty
result proves absence only when `index.absence_proven` is true, which requires a
fresh, complete, authoritative generation. Until M11.7, use the existing `mail
search` server command for current-mailbox claims and live actions.

The reproducible comparison is:

```bash
.venv/bin/python scripts/benchmark_mail_search_m114.py \
  --samples 11 --output build/m114-mail-search-benchmark.json
```

On the documented 11-sample reference run, M11.4 reached Recall@5/10 0.6500,
MRR 0.6667 and nDCG@10 0.6368 versus M11.0 local FTS 0.4833 / 0.4833 / 0.5000 /
0.4766. Date and attachment filter cases rose from zero to full Recall, exact
lexical and body cases stayed at full Recall, and duplicate hits were zero.
M11.4 p50/p95/p99 were 0.9342/2.5405/3.0160 ms versus the simultaneously
reproduced M11.0 path at 0.3346/0.5873/0.8854 ms. The additional time is visible
and comes from filter/tag provenance, coverage evidence, document grouping and
safe snippets; no arbitrary pass/fail threshold is inferred from this small
synthetic run.

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
