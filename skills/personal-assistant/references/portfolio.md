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
ISIN-to-symbol/MIC mapping. For an unconfirmed instrument use `portfolio mapping
suggest --isin "<ISIN>"`: EODHD supplies exact active ISIN candidates and Ollama
may select only a returned candidate plus one of its allowlisted MICs. Treat the
result as an unstored proposal, never as confirmation.

For every question about Jan's stocks, securities, depot positions or holdings,
use `portfolio holdings` first. Do not search memory, the writable workspace or
local files to decide whether a portfolio exists. Only a successful registered
holdings result may establish that the depot is empty. DKB snapshots preserve
`entry_price`, `valuation_price`, `absolute_gain`, `relative_gain_percent` and
`asset_class`; an entry price but no individual purchase date is expected. Empty
gain values remain unknown. `currency` belongs to snapshot values;
`quote_currency` belongs to the confirmed market mapping and must not be silently
replaced by a newer import.

`portfolio.holdings` is the Tool-ID, not a command. In the installed container
execute `/opt/openclaw-agent/scripts/assistant.sh portfolio holdings`; the dotted
form `/opt/openclaw-agent/scripts/assistant.sh portfolio.holdings` is invalid.

For a request for latest/current prices, value, profit or return, first use
`portfolio quotes status`. If the registered result is due, stale or missing and
provider configuration plus mappings are valid, use `portfolio quotes refresh`;
then use `portfolio valuation`, which performs the controlled currency conversion.
The ordinary refresh is the registered scheduled-market-data refresh and needs no
invented `--yes`; `--force` is only for an explicitly requested diagnostic refresh.
Never manually combine holdings and quote output, and never compare USD and EUR
without controlled FX. Missing, stale or invalid equity/FX quotes make the result
incomplete and must not be summed as a complete total.

Run the registered setup, mapping, doctor, refresh, status, valuation and job
commands yourself; never ask Jan to copy a `docker exec` wrapper. If an instrument
has no confirmed mapping, first read holdings and watchlist, then run `portfolio
mapping suggest --isin "<ISIN>"` yourself. The command must call Ollama through
the coordinator and reject every symbol, currency, candidate ID or MIC that was
not bounded by the exact EODHD response. Present one bounded plan containing the
returned exact ISIN, name, symbol, MIC and currency. Wait for explicit approval,
execute that exact `portfolio watchlist add ... --yes` command, and verify the
watchlist before refreshing quotes. Then run quote status and valuation. On
failure run `portfolio doctor` and `jobs check --target all --deep`.
Enabling the portfolio job is a separate approval: after it is granted, execute
`jobs on portfolio` yourself and verify with `jobs status --target portfolio
--deep`. Do not broaden an approval to another ISIN, mapping, job or permission.

When `portfolio status` or `portfolio quotes status` reports `ok: false`,
`state: failed`, zero coverage or missing/critical holdings, always run
`portfolio doctor` and `jobs check --target all --deep` before replying. Merge the
evidence and enumerate all independent blockers; do not report missing mappings as
the sole cause while `configuration_ok` or `api_key_present` is false. The response
must end with the next bounded action: request one exact mapping approval when its
five fields were returned by the registered mapping suggestion; if suggestion is
uncertain, report that result without guessing. Secret provisioning remains Jan's
host action and must never expose the key in chat.

For one current price resolve the exact ISIN, then use `portfolio quotes get
--isin`. Report price, currency, observation time, provider and stale/critical
flags. `portfolio quotes status` is health metadata and accepts no `--detailed`
option. Do not inspect SQLite directly, invent switches or substitute web search
while the registered read tool is available.

If refresh is blocked, stop and report the exact contract failure. A missing
`PORTFOLIO_EODHD_API_KEY` requires Jan to provision the secret; an unconfirmed
symbol/MIC/currency requires an explicit mapping choice through the registered
mapping-suggestion and watchlist commands. Never guess either value or claim an
old snapshot price is current. Never inspect or edit `personal_assistant/tools.toml`
to recover from a portfolio error. Run the registered doctor and deep job check;
protected setup changes belong to the explicitly approved `agent-cli` path.

EODHD is the only quote provider. It uses confirmed forms such as `RHM.XETRA`,
batches at most 20 market/FX symbols and normally returns prices delayed by about
15–20 minutes. Never call them exchange-real-time, fall back to another provider
or expose the API key. A missing/critically stale held-position quote blocks fresh
analysis and requires portfolio doctor plus deep job checks.
