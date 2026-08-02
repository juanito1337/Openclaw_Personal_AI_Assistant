# OpenClaw 3.4.0-r27.2.2

R27.1 is the cumulative container release with unified EODHD Live/Delayed
portfolio quotes for confirmed US and Xetra instruments. Program code and
dependencies live in an immutable image, while productive state, configuration
and secrets remain under `/srv/openclaw`. Every deployment stops the writers,
creates and verifies a local restore point, runs a bounded product smoke test
and automatically returns to the previous image and local data on failure.
Optional administrator hooks can additionally snapshot remote IMAP and
Nextcloud data. See `docs/DOCKER_DEPLOYMENT.md`.

For rapid live iteration, every pushed `test/**` branch builds a commit-addressed
GHCR image. On the Docker host, `./docker/scripts/live-test-branch.sh` deploys
that exact pushed commit through the same single-writer backup, smoke-test and
rollback path.

The helper checks Docker API access before touching deployment files and
verifies the full Git revision in both immutable image metadata and the running
workspace. Remigrations preserve a verified pre-publish `/srv/openclaw` backup
and link the original legacy archive into release backups, so an incomplete
legacy home can be recovered before rollback stops the current runtime.

Quick start after Docker is installed:

```bash
sudo ./docker/scripts/setup-host.sh
/srv/openclaw/deployment/scripts/migrate-live.sh --execute
/srv/openclaw/deployment/scripts/deploy.sh r27.2.2
```

## Portfolio monitor test milestone

The optional portfolio subsystem imports ClamAV-scanned Portfolio Performance
XML and strict DKB depot CSV snapshots from the controlled local inbox or an
exact dated file in `Assistent/Finanzen/Portfolio/`. DKB entry price, valuation
price, absolute/relative gain and asset class remain available through
`portfolio holdings`; arbitrary broker CSV layouts are never guessed. It
monitors confirmed ISIN/symbol/MIC mappings every 15, 30, 60, 90 or 120 minutes
through bounded EODHD Live/Delayed batch requests, exposes an exact stored quote
through `portfolio quotes get --isin`, analyzes stored
numeric series and raises deduplicated course-mark events. EODHD stock quotes
are normally delayed by about 15–20 minutes and are never labeled exchange-real-time.
Missing or critically stale held-position quotes block fresh analysis
and feed the existing job and monitoring health path. Broker login and order
execution are not implemented. See `docs/PORTFOLIO_ADVISOR.md`.

## R22.1 robust migration for existing learning history

The R21/R22 learning schema is now migrated in dependency order: missing feedback columns are added and backfilled before the subject-pattern index is created. Existing correction rows are preserved. The installer snapshots the productive SQLite database, migrates a copy, checks schema and integrity, and only then updates the live workspace and restarts services.

```bash
./scripts/assistant.sh version --verify
./scripts/assistant.sh mail learning status
./scripts/assistant.sh mail learning evaluate --limit 5000
```

## Plausibles Deck-Faelligkeitsdatum

Jede aus einer Mail erzeugte agentenverwaltete Bestellkarte erhaelt ein `dueDate`. Ein verlaesslich erkanntes Liefer-/Zustell- oder Bestelldatum hat Vorrang; fehlt es, wird das serverseitige Mail-Eingangsdatum verwendet. Bereits vorhandene plausible Deck-Daten werden nicht stillschweigend ersetzt.

## R22 learning quality and evaluation

The assistant can now measure whether correction learning improves real historical decisions without letting a correction test itself. It compares the old broad sender-only behavior, deterministic sender/subject-pattern learning, and the stored original classifier result.

```bash
./scripts/assistant.sh mail learning evaluate --limit 5000
./scripts/assistant.sh mail learning dataset-export --output "mail_agent/data/learning_dataset.json" --limit 5000
```

The aggregate report contains no sender addresses, subjects, bodies, or attachments. The optional dataset uses per-export keyed pseudonyms, omits raw identifiers and mail content, is local-only, and is written with mode `0600`.

## R21 pattern-based mail learning

Corrections now apply to the combination of sender and normalized subject pattern, not automatically to every message from a sender. Mixed senders and contradictory patterns cause the deterministic learner to abstain. Similar corrections are available to the local model as transparent examples.

Controlled correction subfolders can be created only after an explicit user request:

```bash
./scripts/assistant.sh mail learning status
./scripts/assistant.sh mail learning feedback --limit 50
./scripts/assistant.sh mail learning mixed-senders --limit 100
./scripts/assistant.sh mail learning conflicts --limit 100
./scripts/assistant.sh mail learning folder-list
./scripts/assistant.sh mail learning folder-create --parent routine --name "Versand" --label shipping --yes
./scripts/assistant.sh mail learning folder-disable --folder "Agent/Korrektur-Unwichtig/Versand" --yes
```

Directly moving a message to `Agent/Routine` is still only organization. Learning occurs through configured `Agent/Korrektur-*` folders. No model fine-tuning is performed.

## R20.2 release awareness

The installed release is now read from the authoritative `RELEASE.json`. The agent can verify its version and explain every recent update through registered commands:

```bash
./scripts/assistant.sh version --verify
./scripts/assistant.sh version --verify --history --limit 10
./scripts/assistant.sh version --verify --history --since "3.4.0-r18"
```

`assistant status` and `assistant doctor` include the same verified release identity. The installer stamps installation time and previous version only after a successful update.

## R20.1 stabilization

The agent now has registered Ollama and performance commands, and automatic mail
recovery stops the timer/service and waits for the real process lock before its
safety dry-run. See `docs/JOB_CONTROL.md` and `docs/OLLAMA_PRIORITY.md`.

# Local Personal Assistant 3.4.0-r27.2.2

## R26.4 agent capability exposure fix

The agent-facing `personal-assistant` skill now advertises the same calendar,
task and contact capabilities that the backend and tool registry provide. For
questions such as “Welche Termine stehen als Nächstes an?” the agent must run:

```bash
./scripts/assistant.sh calendar status
./scripts/assistant.sh calendar list --limit 100
```

It may no longer answer that the calendar integration is create-only without
checking the registered status/list tools. Calendar and task update setup is now
shown consistently with `--allow-update --yes`. After installing R26.4, restart
the OpenClaw gateway or start a new agent session so the revised skill text is
loaded.

## R26.3 controlled CalDAV calendar and task editing

The assistant can list/search calendar events and update exactly one existing VEVENT by UID. It can also update or complete exactly one VTODO task. Update rights are opt-in and are enabled only after live CalDAV discovery confirms write access.

```bash
./scripts/assistant.sh calendar discover
./scripts/assistant.sh calendar configure --resource "<resource_id>" --allow-update --yes
./scripts/assistant.sh calendar list --limit 100
./scripts/assistant.sh calendar search --query "<Suchbegriff>" --limit 50
./scripts/assistant.sh calendar update --uid "<UID>" --expected-title "<alter Titel>" --title "<neuer Titel>" --yes

./scripts/assistant.sh tasks discover
./scripts/assistant.sh tasks configure --resource "<resource_id>" --allow-update --yes
./scripts/assistant.sh tasks list --include-completed --limit 100
./scripts/assistant.sh tasks update --uid "<UID>" --expected-title "<Titel>" --status COMPLETED --yes
```

Every update first selects the exact UID, reads the current object and uses its ETag with `If-Match`. Concurrent changes abort instead of being overwritten. Partial edits preserve alarms, attendees, recurrence data and unknown iCalendar properties. Recurring objects require an additional explicit series flag. Delete and bulk editing remain disabled. See `docs/DIRECT_CALENDAR.md` and `docs/DIRECT_TASKS.md`.

## R26.2 controlled CardDAV contact editing

The assistant can discover and select a CardDAV address book, read/search contacts, create new contacts and, when deliberately enabled, update an existing contact. Update permission is opt-in and is registered only after the server advertises write-content privileges.

```bash
./scripts/assistant.sh contacts discover
./scripts/assistant.sh contacts configure --resource "<resource_id>" --allow-update --yes
./scripts/assistant.sh contacts status
./scripts/assistant.sh contacts search --query "<Name oder E-Mail>" --limit 50
```

A write operation always targets the exact UID returned by `contacts search` or `contacts list`. `--expected-name` or `--expected-email` can guard against selecting a stale or wrong record. Only supplied fields are changed; unknown vCard properties, addresses, birthdays and photos remain intact.

```bash
./scripts/assistant.sh contacts update \
  --uid "<UID>" \
  --expected-name "Max Mustermann" \
  --phone "+49 123 456789" \
  --organization "Muster GmbH" \
  --yes
```

Repeated `--email` or `--phone` arguments replace the complete respective list. Use `--clear-emails`, `--clear-phones`, `--clear-organization` or `--clear-note` only for a deliberate removal. The CardDAV PUT uses the current ETag through `If-Match`; concurrent changes cause a conflict rather than a silent overwrite. Delete, merge and bulk update remain unavailable.

Creating a contact directly or from a selected mail remains create-only:

```bash
./scripts/assistant.sh contacts create --name "Max Mustermann" --email "max@example.com" --yes
./scripts/assistant.sh contacts from-mail --folder "INBOX" --message-id "<Mail-ID>" --expected-subject "<Betreff>" --dry-run
./scripts/assistant.sh contacts from-mail --folder "INBOX" --message-id "<Mail-ID>" --expected-subject "<Betreff>" --yes
```

Mail is searched server-side across all IMAP folders, including the read-only
review folders. Sender, subject and text body participate in the search, so older
mail is not hidden by the normal listing page size. A result is then read by exact
folder and mailbox ID:

```bash
./scripts/assistant.sh mail search --query "dj@ib-jaetzel.de" --limit 50
./scripts/assistant.sh mail read --folder "Agent/Pruefen" --message-id "<Mail-ID>" --expected-subject "Treffen TA"
```

Check `complete`, `folder_errors` and `results_may_be_truncated` before claiming
that no message exists. Partial or limited results require a narrower follow-up
search.

Replies use a mandatory two-step approval flow. `reply-draft` only stores and
prints the complete recipient, subject and body. `reply-send` accepts only that
stored draft ID and requires Jan's explicit approval:

```bash
./scripts/assistant.sh mail reply-draft --folder "Agent/Pruefen" --message-id "<Mail-ID>" --expected-subject "Treffen TA" --body "<Entwurf>"
./scripts/assistant.sh mail reply-send --draft-id "<Entwurfs-ID>" --yes
```

New messages use the same mandatory two-step approval flow:

```bash
./scripts/assistant.sh mail compose-draft --to "jonas@example.de" --subject "Vorstellung" --body "<Entwurf>"
./scripts/assistant.sh mail compose-send --draft-id "<Entwurfs-ID>" --yes
```

The direct move tool cannot move mail out of `Agent/Pruefen`,
`Agent/Termin-Pruefen` or `Agent/Virusverdacht`.

Details and safety boundaries: `docs/CARDDAV_CONTACTS.md`.

## R23.3 plausible Deck due dates

Every agent-managed order card created from mail now receives a non-empty Deck `dueDate`. The assistant uses the most relevant reliable date in this order: return deadline for active returns, expected delivery, order date, mailbox arrival date, and finally a clearly marked processing-date fallback. Existing plausible due dates are preserved.

```bash
./scripts/assistant.sh orders due-date-backfill --limit 500 --dry-run
./scripts/assistant.sh orders due-date-backfill --limit 500 --yes
```

The preview is read-only. The productive backfill updates only agent-managed cards whose due date is missing; existing dates and manual cards remain untouched.


## R26 invoice text recognition and Nextcloud annual register

The invoice interface reads the native PDF text layer first. Tesseract OCR is only a fallback when the text layer is unusable or the invoice date remains unresolved. A safely recognized invoice date controls the year/month folder even when amount, invoice number, supplier or category are incomplete.

```bash
./scripts/assistant.sh invoices status
./scripts/assistant.sh invoices review --limit 100
./scripts/assistant.sh invoices export --year 2026 --yes
./scripts/assistant.sh invoices backfill --year 2026 --limit 500 --dry-run
```

Every successfully archived invoice synchronizes the single authoritative semicolon-separated UTF-8 CSV at `Assistent/Rechnungen/<YYYY>/Rechnungen_<YYYY>.csv`. No productive local CSV copy is kept. Missing optional metadata is recorded as `Pruefen` without forcing a safely dated PDF into the review folder. See `docs/INVOICE_OCR_REGISTER.md`.

## R22.4 CalDAV discovery

The agent can discover event calendars and task lists independently without changing configuration:

```bash
./scripts/assistant.sh calendar discover
./scripts/assistant.sh tasks discover
```

Each result includes a stable `resource_id`, advertised `VEVENT`/`VTODO` components and live read/create capability. A resource is selected only after an explicit user request:

```bash
./scripts/assistant.sh calendar configure --resource "<resource_id>" --yes
./scripts/assistant.sh tasks configure --resource "<resource_id>" --yes
```

Discovery is read-only. Configure validates the component and server privileges before updating the local registry and tool settings.

## R22.2: sichere Lernentscheidungen und echte Originalmessung

- Routine/Spam aus Korrekturmustern erst ab zwei konsistenten aelteren Treffern.
- Relevante Muster schuetzen bereits nach einem eindeutigen Treffer.
- Originalentscheidung wird vor der Korrektur unveraenderlich gespeichert.
- Historische Zeilen ohne Original-Snapshot gelten als nicht messbar.
- `mail learning evaluate` zeigt Kategorienmetriken und Konfusionsmatrizen.
- `mail learning conflicts --id <conflict_id>` prueft genau einen Konflikt.

## Core commands

```bash
./scripts/assistant.sh setup init
./scripts/assistant.sh doctor
./scripts/assistant.sh capabilities
./scripts/assistant.sh resources list
./scripts/assistant.sh index mail
./scripts/assistant.sh search "Tankreinigung Wattenbek"
```

Central Nextcloud setup:

```bash
./scripts/assistant.sh setup nextcloud
./scripts/assistant.sh nextcloud doctor
./scripts/assistant.sh nextcloud discover
./scripts/assistant.sh nextcloud sync
```

## Architecture

```text
OpenClaw Mail Interface ──┐
Nextcloud WebDAV/CardDAV ─┤
Nextcloud CalDAV ─────────┼─> Connectors -> Knowledge Index -> Search
Future Signal channel ────┘                         |
                                                   v
                                    ActionPlan -> Policy -> Outbox
```

The core is a modular monolith. No microservices, broker, or cloud database is
required. SQLite stores metadata, index state, ActionPlans, and audit entries.
Original documents remain in Nextcloud.

## Search

Version 3.4.0 uses SQLite FTS5 plus structured metadata. New mail is written to a
compact local search snapshot during productive mail processing. Existing mail
metadata can be indexed immediately. Nextcloud files are synchronized incrementally
using ETags. Text, Markdown, CSV, JSON, XML, HTML, ICS, VCF, DOCX and XLSX are parsed
with the Python standard library. PDF text is extracted with `pdftotext` when present;
otherwise PDFs remain searchable by filename and metadata.

Semantic search is an extension point and stays disabled until a dedicated local
embedding model is deliberately selected.

## Safety

- external writes use ActionPlan, policy validation, idempotency, and audit
- files are create-only in allowlisted Nextcloud roots
- overwrite and delete operations are not implemented
- contacts can be read and, only when explicitly enabled, ETag-protected updates are allowed
- calendar and task creation require approval by default
- core code, security policies, and permissions cannot be changed autonomously
- secrets live in `~/.config/personal-assistant/secrets.env` with mode `0600`
- the legacy `~/.config/mail-agent.env` remains a compatibility fallback

## Ollama priority coordination

R20 routes OpenClaw and the automatic mail interface through a loopback-only
priority coordinator. Interactive requests are scheduled before new background
classification work. Active model generations are never interrupted.

```bash
./scripts/ollama-priority-proxy.sh status
./scripts/ollama-priority-proxy.sh check-upstream
```

The technical name `mail-agent.service` remains for compatibility; it is the
automatic mail interface of the OpenClaw agent, not a second autonomous agent.

## Adaptive background scheduler

Production container workers use a persistent, non-preemptive scheduler for
complete mail, portfolio, knowledge-sync and monitoring jobs. Recent explicit
user topics receive a bounded, expiring boost; deadlines and aging prevent other
work from starving. The supervisor remains outside this queue.

```bash
./scripts/assistant.sh scheduler status
./scripts/assistant.sh scheduler doctor
./scripts/assistant.sh scheduler activity
./scripts/assistant.sh scheduler focus --topic portfolio --minutes 30
```

The focus signal is local-only and never enables a job, expands permissions or
approves an external write. See `docs/ADAPTIVE_SCHEDULER.md`.

## Services and ON switch

The assistant has a registered job controller with a persistent desired state. It
can distinguish a deliberate OFF state from an unexpected timer/service failure.

```bash
./scripts/assistant.sh jobs status --target all
./scripts/assistant.sh jobs check --target all --deep
./scripts/assistant.sh jobs on standard
./scripts/assistant.sh jobs restart standard
./scripts/assistant.sh jobs off standard
```

`jobs check` may automatically recover only a stale or missing mail dry-run
fingerprint after a bounded successful dry-run and complete revalidation. It never
uses `--force`. All other starts and repairs remain explicit.

`jobs on standard` installs missing packaged user units, starts the supervisor,
enables automatic mail processing and keeps the hourly technical monitor active.
Missing configured Agent mail folders are
recreated through the existing narrow mail setup before a productive run. New or
resolved supervisor alerts queue an immediate OpenClaw heartbeat notification; an
unchanged active error is not announced repeatedly. The optional knowledge sync
remains separately selectable with `jobs on sync`.

See `docs/JOB_CONTROL.md`.

## Validation

```bash
./scripts/check-repo.sh
```

See `docs/ASSISTANT_ARCHITECTURE.md`, `docs/CAPABILITIES.md`, `docs/SEARCH.md`,
`docs/NEXTCLOUD.md`, and `docs/SELF_MANAGEMENT.md`.

## Monitoring

The assistant can evaluate its technical operation using evidence from its local
assistant and mail databases, sync state, ActionPlans, index freshness, service
state, adaptive queue health and optional live Nextcloud checks:

```bash
./scripts/assistant.sh monitor status --days 7 --live
./scripts/assistant.sh monitor record --days 7 --live
./scripts/assistant.sh monitor history --days 30
```

The 0-100 value is an operational health indicator, not a claim that every AI
classification is correct. The report includes confidence, component scores,
raw evidence and concrete recommendations. Snapshots are stored locally in
`personal_assistant/data/monitoring.sqlite3`.

The production container includes a standard hourly monitor worker. For the
packaged legacy systemd runtime, the equivalent timer is:

```bash
systemctl --user enable --now personal-assistant-monitor.timer
```

## Antivirus gate

Mail and file uploads are protected by the host ClamAV integration. Install the
host service once with `sudo bash scripts/setup-antivirus-host.sh`, create the new
mail quarantine folder with `./scripts/mail-agent.sh setup`, and validate with:

```bash
./scripts/assistant.sh security antivirus doctor
./scripts/assistant.sh security antivirus self-test
```

The pipeline is fail-closed: no attachment is forwarded or uploaded to Nextcloud
unless the complete mail and all extracted attachments have a clean verdict.


## Direct calendar and task tools

The agent can create new calendar events and tasks. When the selected CalDAV collection was explicitly configured with `--allow-update`, it can also update one exact UID with ETag conflict protection:

```bash
./scripts/assistant.sh calendar status
./scripts/assistant.sh calendar list --limit 100
./scripts/assistant.sh calendar update --uid "<UID>" --expected-title "<Titel>" --location "Kiel" --yes
./scripts/assistant.sh tasks status
./scripts/assistant.sh tasks list --include-completed --limit 100
./scripts/assistant.sh tasks update --uid "<UID>" --status COMPLETED --yes
```

Creation never overwrites an existing UID. Updates are separate, partial, audited and protected with `If-Match`; delete and bulk editing remain disabled. Details: `docs/DIRECT_CALENDAR.md` and `docs/DIRECT_TASKS.md`.


## Nextcloud Deck Bestellmonitor

Der Personal Assistant kann laufende Bestellungen aus E-Mails in einem eigenen Nextcloud-Deck-Board verwalten. Details, Einrichtung und Sicherheitsgrenzen stehen in `docs/DECK_ORDERS.md`.

Wichtige Befehle:

```bash
./scripts/assistant.sh deck discover
./scripts/assistant.sh setup deck-orders --board-title "Bestellungen" --create-board --approve-permissions
./scripts/assistant.sh orders list --limit 100
```


## 3.4.0-r15 – Kontrolliertes Mail-Verschieben
Der Agent darf nach expliziter Einrichtung einzelne, eindeutig per Mail-ID ausgewaehlte Nachrichten zwischen vorhandenen Ordnern verschieben. Papierkorb, Spam/Junk, Virusverdacht, Loeschen, EXPUNGE und Ordneraenderungen bleiben gesperrt.

### Performance-Telemetrie (r18)

Der Mail-Agent schreibt privacy-sichere Laufzeitmessungen nach
`mail_agent/data/performance.jsonl`. Eine verdichtete Auswertung ist mit
`./scripts/mail-agent.sh performance --limit 20` verfuegbar. Details und
Datenschutzgrenzen stehen in `docs/PERFORMANCE_TELEMETRY.md`.


### R22.3 Nicht-Spam-Gegenlernen

```bash
./scripts/assistant.sh mail learning not-spam --limit 100
```

Ein Restore aus Spam/Quarantaene in die INBOX ist ein expliziter Nicht-Spam-Gegenbeleg fuer dasselbe Absender-/Betreffmuster. Er blockiert Spam, veraendert aber keine Routine- oder Wichtig-Einstufung. Die Herkunft `inbox-restore` bleibt maschinenlesbar.

- Rechnungssteller werden nur aus expliziten Feldern oder plausiblen Firmenkoepfen uebernommen; Woerter wie `Lieferant` innerhalb eines Firmennamens gelten nicht als Feldbezeichner.

## R24: kontrollierte Ollama-Laufzeiten

Automatische Mailverarbeitung trennt Queue- und Modellzeit, begrenzt Wiederholungen und beendet sich vor dem systemd-Startlimit kontrolliert. Telemetrie speichert Fortschritt und dedupliziert historische Mehrfacheintraege derselben `run_id`. Details: `docs/MAIL_RUNTIME_RECOVERY.md`, `docs/OLLAMA_PRIORITY.md`, `docs/PERFORMANCE_TELEMETRY.md`.
