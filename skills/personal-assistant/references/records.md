# Invoices and orders

## Invoice OCR and annual register

Use registered `invoices status`, `invoices list` or `invoices review` first for
questions about known invoices; memory and workspace search cannot establish an
empty register. Use only registered `invoices` commands. Native PDF text is authoritative and read
first; OCR is fallback only when text is unusable or the invoice date remains
unsafe. The confirmed invoice date comes from the PDF, never only from mail date,
filename, service/delivery/due date. A safe date controls `YYYY/MM` even when
optional fields are absent; only an unsafe date routes the PDF to review.

The sole productive register is the managed Nextcloud
`<invoice-root>/<YYYY>/Rechnungen_<YYYY>.csv`. Never create a durable local copy.
Its narrow replacement path requires ETag, SHA and schema validation. Use
`invoices export --year <YYYY> --dry-run` for an in-memory preview which changes
neither SQLite nor Nextcloud. `invoices export --year <YYYY> --yes` is an external
write: it conditionally creates or replaces only that managed register. The old
`--nextcloud` option remains a compatibility spelling and never grants approval;
an export without either `--dry-run` or `--yes` fails closed.

`invoices backfill --year <YYYY> --limit 500 --dry-run` may read and scan PDFs
inside the configured invoice root but writes neither extraction rows nor a
register. Productive backfill with `--yes` changes SQLite rows and then
conditionally replaces every touched annual register. `invoices correct ...
--yes` changes one SQLite record and then conditionally replaces the affected
registers. These are external-write tools, not local-only tools. Without the
required `--yes`, both local and remote state remain unchanged. SQLite commits
and Nextcloud updates are not one cross-system transaction: a later register
failure remains visible and requires diagnosis plus a verified backup before any
retry; never claim that the local correction was rolled back automatically.

The configured automatic invoice archive is a separate, previously authorized
job path: it creates each scanned PDF without overwrite and synchronizes the
managed register under the configured invoice/resource policy. Enabling that
workflow or its permissions still requires explicit operator approval. It does
not grant a manual export, correction or backfill approval. Backfill never
overwrites or moves the archived original. The register is not a tax filing or
DATEV booking file.

## Agent-managed order cards

Use `nextcloud.deck.orders.status` and then `nextcloud.deck.orders.list` for open
orders, delivery, tracking and returns; do not substitute memory or workspace
search for the configured board and order database.
use `orders.sync` for stored failed updates. Never invent state, tracking, amount
or dates. Only agent-managed cards on the configured board may be changed; other
boards and manual cards are outside scope.

Every managed order card needs a non-empty `dueDate` derived in order from an
active return deadline, expected delivery, order date, server-side last/first
source-mail date, and only then local processing date. Store source and confidence;
never invent a date or silently replace an existing plausible date. Preview a
historical due-date backfill first; productive backfill and historical mail import
require Jan's explicit request and dry-run. Delete, share and permission changes
are prohibited.
