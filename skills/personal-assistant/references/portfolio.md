# Portfolio

Use registered portfolio commands from the
[generated tool contract](tool-contract.md). Portfolio outputs are informational;
never access broker credentials, scrape DKB, place orders or turn SMA/RSI values
into a buy/sell promise.

Do not claim that CSV import is unavailable before `portfolio status` and the
registered import tools were checked. Import Portfolio Performance XML only from
the configured local inbox. Accept CSV only in the strict DKB depot schema. A
Nextcloud CSV must be the exact dated file directly below the configured portfolio
folder; list it first and require filename date to match snapshot date. Scan
fail-closed with ClamAV, dry-run, then require explicit `--yes`. Never guess an
ISIN-to-symbol/MIC mapping.

For holdings questions use `portfolio holdings`. DKB snapshots preserve
`entry_price`, `valuation_price`, `absolute_gain`, `relative_gain_percent` and
`asset_class`; an entry price but no individual purchase date is expected. Empty
gain values remain unknown. `currency` belongs to snapshot values;
`quote_currency` belongs to the confirmed market mapping and must not be silently
replaced by a newer import.

For current value/profit/return use `portfolio valuation`, which performs the
controlled currency conversion. Never manually combine holdings and quote output,
and never compare USD and EUR without controlled FX. Missing, stale or invalid
equity/FX quotes make the result incomplete and must not be summed as a complete
total.

For one current price resolve the exact ISIN, then use `portfolio quotes get
--isin`. Report price, currency, observation time, provider and stale/critical
flags. `portfolio quotes status` is health metadata and accepts no `--detailed`
option. Do not inspect SQLite directly, invent switches or substitute web search
while the registered read tool is available.

EODHD is the only quote provider. It uses confirmed forms such as `RHM.XETRA`,
batches at most 20 market/FX symbols and normally returns prices delayed by about
15–20 minutes. Never call them exchange-real-time, fall back to another provider
or expose the API key. A missing/critically stale held-position quote blocks fresh
analysis and requires portfolio doctor plus deep job checks.
