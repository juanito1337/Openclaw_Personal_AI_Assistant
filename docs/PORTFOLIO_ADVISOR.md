# Portfolio monitor and decision support

## Scope and safety boundary

The portfolio tool is a read-only decision-support system. It never logs in to
DKB, stores PIN/TAN credentials, scrapes online banking, places orders or labels a
deterministic indicator as personal investment advice.

Portfolio state is stored outside the immutable image in:

```text
personal_assistant/data/portfolio.sqlite3
```

Imports are append-only snapshots. Disabling a watchlist item or alert rule keeps
its history.

## Configuration

The release defaults keep the tool and job off. Instance configuration belongs in
`personal_assistant/tools.toml`:

```bash
./scripts/assistant.sh setup portfolio \
  --provider eodhd --interval-minutes 15 --approve-permissions
```

This creates the controlled import directory and configuration but neither stores
the API key nor enables the background job.

```toml
[portfolio]
enabled = true
database = "personal_assistant/data/portfolio.sqlite3"
import_root = "personal_assistant/data/portfolio_inbox"
nextcloud_folder = "Assistent/Finanzen/Portfolio"
provider = "eodhd"
api_key_env = "PORTFOLIO_EODHD_API_KEY"
interval_minutes = 90 # 15, 30, 60, 90 or 120
stale_warning_minutes = 110
stale_critical_minutes = 180
timezone = "Europe/Berlin"
market_open = "08:00"
market_close = "22:00"
```

Store `PORTFOLIO_EODHD_API_KEY` only in the host's OpenClaw secrets directory,
never in Git, `tools.toml`, command output or logs. The adapter uses only the
fixed EODHD Live/Delayed HTTPS endpoint. EODHD requires its token as a query
parameter, so request URLs are never logged and provider failures redact the
token. Polling frequency and provider-side exchange delay are separate: every
quote stores both source time and local receipt time.

The adapter translates confirmed MIC mappings to EODHD symbols. Registered US
MICs use `.US`; Xetra uses `.XETRA`. Unknown MICs fail closed. Up to 20 mapped
instruments are fetched in one bounded request. EODHD stock snapshots are
normally delayed by about 15–20 minutes and must not be described as
exchange-real-time quotes.

The configured paid EODHD plan uses a 15-minute interval. The setup command
automatically raises freshness thresholds for longer intervals. Holdings and
enabled watchlist entries are deduplicated before the bounded provider request.
HTTP 401, 402 and 403 enter a UTC-day cooldown instead of being retried every
worker tick.

## Depot import

Copy a Portfolio Performance XML export into the configured local import root.
Every file is bounded to 25 MB, scanned by ClamAV fail-closed, rejects DTD/entity
declarations and is parsed as untrusted data.

```bash
./scripts/assistant.sh portfolio import-pp --file "depot.xml" --dry-run
./scripts/assistant.sh portfolio import-pp --file "depot.xml" --yes
./scripts/assistant.sh portfolio holdings
```

The dry-run is mandatory before a productive import in the operating workflow.
The source file is not modified. Reimporting the same SHA-256 is idempotent.
DKB PDF parsing is not part of this milestone; Portfolio Performance is the
structured intermediary.

### DKB CSV and Nextcloud snapshots

The DKB depot CSV export is supported as a strict snapshot format. It must be
UTF-8 with a semicolon delimiter and contain the German columns
`Datum der Erstellung`, `Depotnummer`, `Wertpapierbezeichnung`, `WKN`, `ISIN`,
`Einstiegskurs`, `Bewertungskurs`, `Stückzahl`, `Absoluter Gewinn`,
`Relativer Gewinn` and `Assetklasse`. These values are preserved in the latest
`portfolio holdings` result as `entry_price`, `valuation_price`,
`absolute_gain`, `relative_gain_percent` and `asset_class`. General broker CSV
formats are rejected rather than guessed. The depot snapshot does not contain an
individual purchase date; that absence does not make its entry price unavailable.
Individual absolute or relative gain cells may be empty and remain explicitly
unknown; they do not invalidate an otherwise complete position.
`portfolio holdings` reports the snapshot value currency as `currency` and the
market-data mapping currency separately as `quote_currency`. They must not be
combined without an explicit, timestamped FX conversion.

Local preview and confirmed import:

```bash
./scripts/assistant.sh portfolio import-csv --file "depot-export-31.07.2026.csv" --dry-run
./scripts/assistant.sh portfolio import-csv --file "depot-export-31.07.2026.csv" --yes
```

Versioned CSV snapshots may remain in the configured Nextcloud folder. List the
folder first and select exactly one file; the tool downloads it to a protected
temporary staging file, scans it with ClamAV, imports it and removes the staging
copy. The download is pinned to the ETag returned by the folder listing, so a
concurrently changed file fails closed. The file remains unchanged in Nextcloud.

```bash
./scripts/assistant.sh nextcloud list --path "Assistent/Finanzen/Portfolio"
./scripts/assistant.sh portfolio import-csv \
  --nextcloud-path "Assistent/Finanzen/Portfolio/depot-export-31.07.2026.csv" \
  --dry-run
```

Nextcloud filenames are immutable and must contain `DD.MM.YYYY`; that date must
match the one snapshot date inside the CSV. Productive import still requires a
separate call with `--yes`. Existing snapshots are never overwritten.

## Instrument mapping and watchlist

An ISIN alone is insufficient for reliable intraday quotes. Generate a read-only
proposal first:

```bash
./scripts/assistant.sh portfolio mapping suggest --isin "DE000BASF111"
```

The command starts with one bounded EODHD Search API lookup for the exact active
ISIN. EODHD supplies symbol, exchange, name and currency. Because EODHD reports a
combined `US` exchange in ordinary search results, a primary US candidate is
verified through the same provider's server-side `exchange=NASDAQ` and, only when
needed, `exchange=NYSE` filters. A verified NASDAQ result is narrowed to canonical
MIC `XNAS`; a verified NYSE result to `XNYS`. Ollama runs through the priority
coordinator and may select only one returned candidate plus an MIC from that
candidate's now provider-bounded allowlist. Invented candidate IDs, symbols,
currencies and MICs fail closed. The proposal is not written to the database and
always reports that explicit approval is still required.

If exactly one primary US candidate has been confirmed by EODHD's exchange
filter, only that candidate is sent to Ollama. The structured output schema then
permits only `status=candidate`, its provider candidate ID and its single canonical
MIC. This prevents a contradictory `uncertain` result after the model has already
identified the provider-confirmed venue, without allowing model-created data.

After checking the proposed fields, confirm the exact provider symbol, ISO 10383
MIC and currency separately:

```bash
./scripts/assistant.sh portfolio watchlist add \
  --isin "DE000BASF111" --name "BASF SE" \
  --symbol "BAS" --mic "XETR" --currency "EUR" --yes
./scripts/assistant.sh portfolio watchlist list
```

An imported holding without a confirmed mapping is a failed required quote, not
silently guessed. An uncertain Ollama result remains unmapped. Watchlist changes
and alarm changes require explicit `--yes`.

## Quotes, analysis and course marks

```bash
./scripts/assistant.sh portfolio quotes status
./scripts/assistant.sh portfolio quotes get --isin "DE000BASF111"
./scripts/assistant.sh portfolio quotes refresh
./scripts/assistant.sh portfolio valuation
./scripts/assistant.sh portfolio analyze --isin "DE000BASF111"
./scripts/assistant.sh portfolio alerts add \
  --isin "DE000BASF111" --direction above \
  --threshold "55.00" --currency EUR --yes
./scripts/assistant.sh portfolio alerts list
```

Use `quotes get` for one stored price including currency, provider, source time
and freshness. `quotes status` reports coverage/health and accepts no
`--detailed` option. Analysis uses stored numeric OHLCV observations, not chart screenshots. It
reports SMA20/50/200 and RSI14 only when enough observations exist. A critically stale or
missing required quote returns `decision=abstain`; it cannot produce a fresh
trend claim. Outside the configured market window, the last observation remains
explicitly timestamped and is not falsely described as a live quote.

`portfolio valuation` is the only supported current depot-gain calculation. A
refresh requests each required pair, such as `EURUSD.FOREX`, in the same
at-most-20-symbol EODHD Live/Delayed batch as the equity quotes and stores rate,
source time and receipt time separately. For a USD equity in an EUR DKB
snapshot, EODHD's `EURUSD` value is USD per EUR, so the implementation divides
the USD amount by that rate. The command exposes the original quote, converted
unit price, position value, cost basis, gain and the exact FX observation. If
any required quote or FX rate is missing, critically stale or has an invalid
source timestamp, it returns `incomplete` and deliberately omits totals rather
than summing mixed currencies. Equity and FX observations remain separately
timestamped because their markets and provider delays can differ legitimately.

Course marks trigger once per crossing. A rule must clear its hysteresis before a
new crossing can trigger, and a cooldown prevents rapid repeats. A new event is
stored locally and queues an immediate OpenClaw system event. This is an
informational signal, not an order instruction.

## Job and monitoring integration

The packaged worker checks every 15 minutes and refreshes only when the configured
15-, 30-, 60-, 90- or 120-minute interval is due:

```bash
./scripts/assistant.sh jobs on portfolio
./scripts/assistant.sh jobs status --target portfolio --deep
./scripts/assistant.sh jobs alerts
./scripts/assistant.sh monitor status --days 7 --live
```

The portfolio job is optional and defaults to OFF. Enabling or restarting it
requires Jan's explicit instruction. A held position with an unavailable,
unmapped or critically stale quote makes the job fail; a watchlist-only gap is
degraded. The supervisor deduplicates unchanged failures and queues a new or
resolved OpenClaw system event.

The overall monitor includes market-data pipeline health, but trading-signal
performance is deliberately separate:

```bash
./scripts/assistant.sh portfolio performance
```

Until completed forward observation windows exist, it reports
`insufficient_data` instead of inventing win rates. Future signal evaluation must
state sample size, coverage, forward returns, benchmark adjustment and drawdown.

## Delivery limitation

OpenClaw events require a functioning host, gateway and configured conversation
channel. A full host or gateway outage needs an independent external watchdog.
Direct out-of-band delivery for data-source failures is a later milestone; local
alerts and system events are implemented here.
