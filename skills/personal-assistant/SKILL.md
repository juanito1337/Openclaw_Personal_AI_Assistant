---
name: personal-assistant
version: 3.4.0-r27.2.4
description: Operate the local Personal Assistant and its registered tools. Use for status and diagnostics, cross-source search, mail triage, invoice archiving, Nextcloud files, CardDAV contacts, CalDAV calendars, VTODO tasks and portfolio workflows, including ClamAV-checked Portfolio Performance XML and strict DKB depot CSV imports from the controlled local inbox or an exact Nextcloud path. The assistant can list and search contacts, calendar events and tasks; it can create new objects and, when allow_update is explicitly enabled for the selected collection, update exactly one existing object by UID with ETag/If-Match protection.
---

# Personal Assistant

The Personal Assistant is the only agent. Mail is one registered tool/subsystem.
Use stable commands from the workspace only:

```bash
cd "${OPENCLAW_WORKSPACE:-$HOME/.openclaw/workspace}"
./scripts/assistant.sh version --verify
./scripts/assistant.sh tools list
./scripts/assistant.sh capabilities
```

Before claiming that a supported source cannot be read or edited, inspect the
registered tools and the source-specific status command. A disabled permission is
a configuration state, not proof that the backend lacks the function.

## Calendar events

Use these commands for calendar questions and changes:

```bash
./scripts/assistant.sh calendar discover
./scripts/assistant.sh calendar status
./scripts/assistant.sh calendar list --limit 100
./scripts/assistant.sh calendar search --query "<Suchbegriff>" --limit 50
./scripts/assistant.sh calendar create --title "<Titel>" --start "<ISO-8601>" --end "<ISO-8601>"
./scripts/assistant.sh calendar update --uid "<UID>" --expected-title "<aktueller Titel>" --title "<neuer Titel>" --yes
```

Rules:

- For “Welche Termine stehen als Nächstes an?” or similar requests, call
  `calendar status` and then `calendar list`; do not answer from memory.
- If no calendar is configured, use read-only `calendar discover` and ask Jan to
  select the exact `resource_id`.
- `calendar create` accepts no `--yes` option. Never append `--yes` to this subcommand; use `--yes` only for `calendar configure` and `calendar update`.
- Configure read/create access with:

```bash
./scripts/assistant.sh calendar configure --resource "<resource_id>" --yes
```

- Enable editing only after Jan explicitly requests it and live discovery reports
  update/write-content access:

```bash
./scripts/assistant.sh calendar configure --resource "<resource_id>" --allow-update --yes
```

- Before an update, list/search the current event and target exactly one UID.
  Never select a write target from a fuzzy title alone.
- Use expectation guards such as `--expected-title` or `--expected-start` when
  useful. Update only requested fields; omitted fields remain unchanged.
- Every update uses the current ETag through `If-Match`. On conflict, stop and
  read/search again rather than overwriting another change.
- Preserve attendees, alarms, time zones, recurrence data, exceptions and unknown
  iCalendar properties. Recurring objects require explicit series authorization.
- Calendar deletion, bulk editing and moving objects between calendars are not
  registered.

## Tasks / VTODO

Use these commands for task questions and changes:

```bash
./scripts/assistant.sh tasks discover
./scripts/assistant.sh tasks status
./scripts/assistant.sh tasks list --include-completed --limit 100
./scripts/assistant.sh tasks create --title "<Titel>" --due "<YYYY-MM-DD oder ISO-8601>"
./scripts/assistant.sh tasks update --uid "<UID>" --expected-title "<aktueller Titel>" --due "<Datum>" --yes
./scripts/assistant.sh tasks update --uid "<UID>" --status COMPLETED --yes
```

Rules:

- For “Was sind meine offenen Aufgaben?” call `tasks status` and `tasks list`.
- If no task list is configured, use read-only `tasks discover` and ask Jan to
  select the exact VTODO-capable `resource_id`.
- Enable editing only after explicit approval and live update rights:

```bash
./scripts/assistant.sh tasks configure --resource "<resource_id>" --allow-update --yes
```

- Updating, completing or reopening a task requires the exact UID and explicit
  approval. Use the current ETag with `If-Match` and preserve unrelated VTODO
  fields. Recurring tasks require explicit series authorization.
- Task deletion, bulk editing and cross-list moves are not registered.

## CardDAV contacts

```bash
./scripts/assistant.sh contacts discover
./scripts/assistant.sh contacts status
./scripts/assistant.sh contacts list --limit 100
./scripts/assistant.sh contacts search --query "<Name, E-Mail, Telefon oder Firma>" --limit 50
./scripts/assistant.sh contacts create --name "<Name>" --email "<E-Mail>" --yes
./scripts/assistant.sh contacts update --uid "<UID>" --expected-name "<aktueller Name>" --phone "<neue Nummer>" --yes
```

Configure an address book only after explicit selection. Enable updates with
`contacts configure --resource "<resource_id>" --allow-update --yes` only when
live CardDAV privileges permit them. Updates must target an exact UID, preserve
unrelated vCard properties and use ETag/`If-Match`. Do not delete, merge or bulk
edit contacts.

## Nextcloud workspace

Use the controlled connector, never search for a local Nextcloud mount:

```bash
./scripts/assistant.sh nextcloud list --path "Assistent"
./scripts/assistant.sh nextcloud sync
./scripts/assistant.sh nextcloud mkdir --path "Assistent/<Ordner>"
printf '%s' "<Inhalt>" | ./scripts/assistant.sh nextcloud write-text --path "Assistent/<Datei>.md"
./scripts/assistant.sh nextcloud upload --local "personal_assistant/data/workspace_outbox/<Datei>" --path "Assistent/<Ziel>"
./scripts/assistant.sh nextcloud move --source "Assistent/<Quelle>" --destination "Assistent/<Ziel>"
```

Files are created only inside allowlisted roots and are never silently
replaced. Deletion and public sharing are not registered.

## Mail and invoices

```bash
./scripts/assistant.sh mail status
./scripts/assistant.sh mail doctor
./scripts/assistant.sh mail dry-run --limit 20
./scripts/assistant.sh mail run --limit 20
./scripts/assistant.sh mail search --query "<Suchbegriff>" --limit 50
./scripts/assistant.sh mail read --folder "<Ordner>" --message-id "<ID>" --expected-subject "<Betreff>"
./scripts/assistant.sh mail reply-draft --folder "<Ordner>" --message-id "<ID>" --expected-subject "<Betreff>" --body "<Entwurf>"
./scripts/assistant.sh mail reply-send --draft-id "<Entwurfs-ID>" --yes
./scripts/assistant.sh mail compose-draft --to "<Adresse>" --subject "<Betreff>" --body "<Entwurf>"
./scripts/assistant.sh mail compose-send --draft-id "<Entwurfs-ID>" --yes
./scripts/assistant.sh invoices status
./scripts/assistant.sh invoices list --year <YYYY> --limit 100
./scripts/assistant.sh invoices review --limit 100
```

`mail search` searches server-side across every readable IMAP folder, including
review folders. Every whitespace-separated query term must match somewhere in
the sender, subject or text body; the result limit is applied after server-side
filtering, so old mail is not hidden by the normal listing page size.

Inspect `complete`, `folder_errors` and `results_may_be_truncated` in every search
result. If `complete` is false, identify the failed folders and describe the
result as partial; never claim that a mail does not exist. If
`results_may_be_truncated` is true, refine the query before concluding that a
specific message is absent. Select a result only by its exact folder and mailbox
ID before calling `mail read`.

Invoice PDFs use native text first and OCR only as fallback. A reliable invoice
date determines the year/month folder independently of optional metadata. The
only productive register is the managed Nextcloud file
`<invoice-root>/<YYYY>/Rechnungen_<YYYY>.csv`; no durable local register is kept.

Reply and compose drafts never send mail. Present the complete recipient,
subject and body to Jan, then use the matching `*-send` command only after his
explicit approval. Never retry a failed or delivery-uncertain send
automatically.

## Tool completeness and failure handling

A supported capability must exist in all four places: stable CLI command, tool
registry, operating instructions and regression tests. Hidden helper scripts are
not agent tools.

On a failed tool call, preserve the exact error, run the relevant status/doctor
command and report evidence. Do not convert a temporary configuration or network
failure into a claim that the capability does not exist.

## Adaptive background work

```bash
./scripts/assistant.sh scheduler status
./scripts/assistant.sh scheduler doctor
./scripts/assistant.sh scheduler activity
./scripts/assistant.sh scheduler focus --topic "<mail|portfolio|knowledge|planning|operations>" --minutes 30
```

Use `scheduler focus` only for an explicit current user topic. It is a local,
expiring preference signal, not an approval or permission change. Never queue an
arbitrary command, never put the supervisor into the business-job queue and never
interrupt healthy in-flight work merely to change topic priority. When a queue
deadline or lease fails, run `scheduler doctor` and
`jobs check --target all --deep`.

## Portfolio monitor

```bash
./scripts/assistant.sh portfolio status
./scripts/assistant.sh portfolio doctor
./scripts/assistant.sh setup portfolio --provider eodhd --interval-minutes 90 --approve-permissions
./scripts/assistant.sh portfolio import-pp --file "<Datei>" --dry-run
./scripts/assistant.sh portfolio import-pp --file "<Datei>" --yes
./scripts/assistant.sh portfolio import-csv --file "<DKB-CSV>" --dry-run
./scripts/assistant.sh portfolio import-csv --file "<DKB-CSV>" --yes
./scripts/assistant.sh portfolio import-csv --nextcloud-path "Assistent/Finanzen/Portfolio/<Datei-DD.MM.YYYY.csv>" --dry-run
./scripts/assistant.sh portfolio import-csv --nextcloud-path "Assistent/Finanzen/Portfolio/<Datei-DD.MM.YYYY.csv>" --yes
./scripts/assistant.sh portfolio holdings
./scripts/assistant.sh portfolio valuation
./scripts/assistant.sh portfolio watchlist list
./scripts/assistant.sh portfolio watchlist add --isin "<ISIN>" --name "<Name>" --symbol "<Symbol>" --mic "<MIC>" --currency "<ISO>" --yes
./scripts/assistant.sh portfolio watchlist disable --isin "<ISIN>" --yes
./scripts/assistant.sh portfolio quotes status
./scripts/assistant.sh portfolio quotes get --isin "<ISIN>"
./scripts/assistant.sh portfolio quotes refresh
./scripts/assistant.sh portfolio quotes refresh --force
./scripts/assistant.sh portfolio analyze --isin "<ISIN>"
./scripts/assistant.sh portfolio alerts list
./scripts/assistant.sh portfolio alerts add --isin "<ISIN>" --direction "<above|below>" --threshold "<Kurs>" --currency "<ISO>" --yes
./scripts/assistant.sh portfolio alerts disable --id "<Regel-ID>" --yes
./scripts/assistant.sh portfolio performance
./scripts/assistant.sh jobs status --target portfolio --deep
./scripts/assistant.sh jobs on portfolio
./scripts/assistant.sh jobs restart portfolio
./scripts/assistant.sh jobs off portfolio
```

Do not claim that CSV import is unavailable or propose manually reading a DKB
depot CSV before checking `portfolio status` and the registered `import-csv`
tools. Import Portfolio Performance XML only from the configured local portfolio
inbox. For CSV, accept only the strict DKB depot export schema. A Nextcloud CSV must be
an exact, dated file directly below the configured portfolio folder; list that
folder first, and require the filename date to match the CSV snapshot date.
Never overwrite an older Nextcloud snapshot or describe arbitrary broker CSV as
supported. Scan fail-closed with ClamAV and run a dry-run before an explicitly
approved `--yes` import. Never
guess ISIN-to-symbol/MIC mapping; Jan must confirm it. A missing or critically
stale held-position quote blocks fresh analysis and must be diagnosed with
`portfolio doctor` plus `jobs check --target all --deep`.

For a question about a held quantity, entry price, valuation price or the
snapshot's absolute/relative gain, call `portfolio holdings` before claiming
that the data is unavailable. Strict DKB CSV imports preserve
`entry_price`, `valuation_price`, `absolute_gain`, `relative_gain_percent` and
`asset_class`. Do not search mail/documents for a purchase receipt unless the
requested field is actually empty in the current holdings result. A DKB depot
snapshot contains an entry price but no individual purchase date; distinguish
those fields instead of claiming both are missing.
The two gain fields may legitimately be empty in individual DKB rows; preserve
that as unknown without rejecting the otherwise valid snapshot.
In `portfolio holdings`, `currency` is the currency of entry price, valuation
price and snapshot gains; `quote_currency` is the currency of the EODHD mapping.
It remains empty until the mapping is confirmed, and importing a newer DKB
snapshot must not replace an already confirmed quote currency.
Never treat a USD quote as directly comparable to an EUR entry price without a
controlled FX conversion.

For every question about current depot value, current profit/loss or current
return, call `portfolio valuation`. This is the authoritative deterministic
calculation: it converts each latest EODHD equity quote into the DKB snapshot
currency with the stored, timestamped EODHD FX quote (for example
`EURUSD.FOREX`) and returns position values plus currency-safe totals. Do not
manually subtract `portfolio quotes get` output from `portfolio holdings`, even
when the currencies happen to look familiar. If `portfolio valuation` is
incomplete because an equity/FX quote is missing, stale or has an invalid source
timestamp, state that limitation and do not invent or sum a partial total.

For a current/latest price question, resolve the exact ISIN from `portfolio
holdings` or `portfolio watchlist list`, then call `portfolio quotes get --isin
"<ISIN>"`. Report `price`, `currency`, `observed_at`, `provider` and the
stale/critical flags. `portfolio quotes status` is health metadata and accepts no `--detailed` option.
Do not inspect SQLite directly, invent CLI switches or
substitute a web search while the registered read tool can answer the question.
Use `portfolio analyze` only when trend indicators or a stored series are
requested, not as the primary single-price lookup.

EODHD is the only quote provider. It uses confirmed EODHD forms such as
`RHM.XETRA` and `TSLA.US`, batches at most 20 market-data symbols including any
required FX pair, and normally
returns stock prices delayed by about 15–20 minutes. Never call these prices
exchange-real-time, fall back to Twelve Data or expose `PORTFOLIO_EODHD_API_KEY`.
The setup accepts 15, 30, 60, 90 or 120 minutes. For a free 20-call daily
allowance, 90 minutes is the conservative starting point; do not promise exact
provider-call accounting without evidence from the provider account.

Portfolio outputs are informational. Never access DKB credentials, scrape the
broker, execute orders or turn deterministic SMA/RSI values into an invented
buy/sell promise. Technical pipeline health belongs to `monitor status`; signal
quality is a separate evidence report. Details: `docs/PORTFOLIO_ADVISOR.md`.
