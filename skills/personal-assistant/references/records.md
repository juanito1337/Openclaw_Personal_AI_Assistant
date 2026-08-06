# Invoices and orders

## Invoice OCR and annual register

Use only registered `invoices` commands. Native PDF text is authoritative and read
first; OCR is fallback only when text is unusable or the invoice date remains
unsafe. The confirmed invoice date comes from the PDF, never only from mail date,
filename, service/delivery/due date. A safe date controls `YYYY/MM` even when
optional fields are absent; only an unsafe date routes the PDF to review.

The sole productive register is the managed Nextcloud
`<invoice-root>/<YYYY>/Rechnungen_<YYYY>.csv`. Never create a durable local copy.
Its narrow replacement path requires ETag, SHA and schema validation. Backfill
reads only the configured invoice root, scans each PDF and never overwrites or
moves the archived original. Correction, productive backfill and manual rebuild
require Jan's explicit instruction. The register is not a tax filing or DATEV
booking file.

## Agent-managed order cards

Use `nextcloud.deck.orders.list` for open orders, delivery, tracking and returns;
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
