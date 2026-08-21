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

M11.5 adds a conservative conversation graph without changing search precedence
or enabling semantic retrieval. Canonical `Message-ID`, `In-Reply-To` and
`References` are the primary evidence. Missing, ambiguous, malformed,
self-referential and cyclic relationships fail closed. Only when no relationship
header exists may a 21-day subject/participant fallback connect a recognized
German or English reply/forward; it requires reciprocal known participants and
is always marked uncertain. Empty subjects, newsletters, digests, invoices and
payment subjects never use this fallback.

Thread and member metadata live separately from mail documents. Thread identity
is rooted in `content_id`, not occurrence or locator, so a client-side move does
not change it or rewrite body FTS. Search results expose the thread version,
position, parent evidence and uncertainty. Optional context is requested
explicitly:

```bash
./scripts/assistant.sh mail search-local --query "Projekt Aurora" \
  --context-limit 2 --limit 20
```

`--context-limit` accepts 0 through 6. Context is chronological, deduplicated and
stored only below its query hit. Every context item carries
`role=thread-context`, `query_match=false` and `evidence_for_query=false`; it
does not increase `count`, matched documents or query evidence. Query hits in the
same thread are not duplicated as context.

Ranking uses `mail-retrieval-text-v1`, which conservatively reduces strong
quote-history, quote-line, RFC signature and known disclaimer boundaries. The
original chunk is never changed and remains the source for snippets and citation.
The version and `source_body_preserved=true` are returned. Reproduce the M11.5
thread baseline with:

```bash
python3 scripts/benchmark_mail_threads_m115.py \
  --output build/m115-mail-thread-benchmark.json
```

On the 13-message synthetic M11.0 corpus the graph reproduced all 10 expected
threads and all 3 linked pairs with Pair-Precision/Recall 1.0, zero missed pairs
and a mislink rate of 0.0. This small deterministic corpus is a regression
baseline, not an estimate of productive mailbox quality.

M11.6 adds the internal, versioned `mail-embedding-v1` contract recorded in
[ADR-0031](architecture/adr/0031-versionierte-lokale-mail-embeddings.md). It does
not add an agent-facing command and does not change the `mail search-local` or
server-search precedence. Raw content SHA-256, normalized retrieval text SHA-256,
`mail-retrieval-text-v1`, chunk position, model name, full model digest and
dimension form the cache identity. Locator, folder, UID, occurrence and
quarantine state are excluded, so a move or copy shares existing vectors.

Vectors are little-endian Float32 rows in knowledge schema 5. A changed chunk is
removed with its vector by SQLite foreign keys, while a changed model digest
creates a separate cache. Bounded background batches can resume by rescanning
deterministic keys and skipping cache hits. Every provider response is checked
for exact cardinality and dimension, finite values and a nonzero norm.

The only real provider adapter calls `/api/embed` through the configured Ollama
priority coordinator. Background work uses `background`, interactive retrieval
uses `interactive`, and both carry explicit queue and upstream timeouts. A real
benchmark first checks `/api/tags` through the same coordinator and accepts only
the exact configured name plus full SHA-256 digest. Direct upstream access is not
an allowed fallback.

The initial vector search is exact cosine comparison. It needs no optional
SQLite ANN extension and is correct for the measured 11-chunk synthetic corpus.
If the provider, proxy, queue, dimension or stored vector fails, the result is
`degraded-lexical-only`; the independent FTS table remains available. Returned
items are `semantic-candidate` records with score, distance and model provenance,
not factual query evidence.

Two catalog candidates for a later target-hardware run are
`nomic-embed-text-v2-moe` (768 dimensions, 512-token catalog context, about
958 MB) and `bge-m3` (1024 dimensions, 8192-token catalog context, about 1.2 GB).
These are candidate metadata from the Ollama catalog, not measured local quality.
The development coordinator was unavailable, so no model was pulled, selected
or activated. The checked-in report compares two deterministic fake profiles
only to prove the complete measurement and failure contract:

```bash
.venv/bin/python scripts/benchmark_mail_embeddings_m116.py \
  --output build/m116-mail-embedding-benchmark.json
```

It reports Recall@5/10, MRR, nDCG@10, p50/p95, cold index and warm query time,
queue wait, model/RAM/disk fields and marks both profiles
`eligible_for_activation=false`. A later real comparison requires two already
installed models and their exact local digests:

```bash
.venv/bin/python scripts/benchmark_mail_embeddings_m116.py \
  --base-url "http://ollama-proxy:11435" \
  --model "<name-a>|sha256:<64-hex>|<dimension>|<context-chars>" \
  --model "<name-b>|sha256:<64-hex>|<dimension>|<context-chars>" \
  --output build/m116-target-hardware.json
```

This command never pulls a model. A successful target-hardware report still
needs a separate model-selection and productive activation approval.

M11.7 makes the compatible `mail search` entry agent-facing and hybrid without
activating a model or background job:

```bash
./scripts/assistant.sh mail index status
./scripts/assistant.sh mail index doctor
./scripts/assistant.sh mail index plan
./scripts/assistant.sh mail search --query "Projekt Aurora" --limit 20
./scripts/assistant.sh mail search --query "Rechnung" \
  --sender "billing@example.invalid" --after "2026-01-01" \
  --category invoice --context-limit 2 --limit 20
```

The default `--mode auto` uses the local path only when `mail index status`
proves a complete, authoritative and fresh generation, working FTS and a current
locator for every indexed content. Otherwise it switches visibly to the
existing server path and reports `fallback_used=true` plus exact reasons. The
diagnostic modes `--mode local` and `--mode server` cannot relax those evidence
rules; in particular, an incomplete local result never proves absence.

`mail-hybrid-rrf-v1` uses weighted reciprocal rank fusion with `k=60`.
Lexical, semantic, structured-filter and thread components use weights 1.0,
0.7, 0.10 and 0.05. Each hit reports ranks, components and match reasons. A
semantic-only item remains `role=semantic-candidate`, `query_match=false` and
`evidence_for_query=false`. With the default disabled semantic provider, the
same path remains a deterministic lexical/structured/thread search and reports
`semantic_state=disabled`. Model or proxy failure becomes
`degraded-lexical-only`; FTS evidence remains available.

Every positive local result contains content and occurrence IDs, all indexed
locators, one deterministic live locator, a query-centered snippet, tags,
thread context, score provenance and a source reference. The server checks only
the candidate folders first. A unique exact subject/sender match after a move is
`resolved-after-move`; missing or ambiguous copies trigger the safe server
fallback in auto mode. A local zero result needs no IMAP call once index
eligibility has been proved.

The returned top-level fields always include `complete`, `coverage`,
`freshness`, `index_generation`, `semantic_state`, `fallback_used`,
`folder_errors` and `results_may_be_truncated`. Server fallback cannot prove
local-only category, review, attachment or tag filters; these appear under
`filter_limitations` and keep `complete=false`.

The current Himalaya 1.2 server query also cannot prove that a successful empty
response represents a complete body-aware account search. On a server zero the
adapter scans one bounded recent envelope window in every readable folder and
matches sender name, sender address/domain and subject. This read-only fallback
finds positive metadata hits after external folder moves, but reports
`server-query-not-authoritative`, `body-search-not-verified` and
`bounded-envelope-metadata-only`. Such a zero result never proves absence; inspect
`search_scope`, `metadata_fallback` and every result's `match` fields.

Before reading a hit, use exactly its `live_locator.folder`,
`live_locator.mailbox_id` and subject:

```bash
./scripts/assistant.sh mail read \
  --folder "<live_locator.folder>" \
  --message-id "<live_locator.mailbox_id>" \
  --expected-subject "<subject>"
```

`mail read` revalidates all three fields. A moved or disappeared locator returns
`mail-locator-conflict`; the index never authorizes a read, move, draft or send.
M11.7 does not run `mail index backfill`, `mail index reconcile`, start a job,
pull a model or change productive state. The durable decision is
[ADR-0032](architecture/adr/0032-hybrid-mail-search-und-live-locator.md).

M11.8 closes the synthetic development acceptance with an isolated container
flow and one content-free aggregate benchmark:

```bash
.venv/bin/python scripts/benchmark_mail_acceptance_m118.py \
  --samples 11 --output build/m11-acceptance.json
OPENCLAW_M11_RUNTIME_IMAGE=openclaw-agent:m11-candidate \
  ./scripts/check-m11-integration.sh
```

The integration uses only `example.invalid` fixtures, an internal Docker
network, temporary volumes and no host ports, secrets or productive mounts. It
exercises Fake-IMAP, fail-closed ClamAV outcomes, projection, sync, lexical and
fake-semantic retrieval, live locators, external-style move/copy/delete,
quarantine, folder rename, UIDVALIDITY reset, network loss and worker crash.
Historical tombstoned content remains available for audit/reuse but is excluded
from active locator-coverage counts.

This acceptance does not authorize production. The current Himalaya connector
still cannot prove UID, UIDVALIDITY and stable folder identity as one
authoritative contract, and no real embedding model was selected on target
hardware. The exact backup, canary, shadow comparison, monitoring and rollback
sequence is documented in
[MAIL_SEARCH_M11_ACCEPTANCE_AND_ROLLOUT.md](MAIL_SEARCH_M11_ACCEPTANCE_AND_ROLLOUT.md)
and remains a separate explicit operation. The decision is
[ADR-0033](architecture/adr/0033-m11-abnahme-und-rolloutgrenze.md).

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

Semantic activation is deliberately not faked. Hybrid routing reports the
semantic state, but a local embedding model still needs a measured target-hardware
comparison and separate approval before configuration or indexing.
