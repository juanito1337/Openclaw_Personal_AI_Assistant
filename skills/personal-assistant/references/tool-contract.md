# Generierter Skill-Toolvertrag

Release: `3.4.0-r27.2.5`. Quelle: typisierte Tooldefinitionen unter
`personal_assistant/tool_catalog/` und `RELEASE.json`. Nicht manuell bearbeiten;
`python3 scripts/generate-skill-tool-contract.py` erzeugt diese Datei deterministisch.
Die statische Liste belegt keine live erteilte Berechtigung; dafuer immer
`./scripts/assistant.sh tools list` und `./scripts/assistant.sh capabilities` lesen.
Tool-IDs in der ersten Spalte sind ausschliesslich Bezeichner und niemals CLI-Syntax.
Vor `exec` immer das exakte Feld `Kommando` verwenden; insbesondere nie
`assistant.sh <gepunktete-tool-id>` ausfuehren. Im installierten Container nur
den fuehrenden Launcher durch `/opt/openclaw-agent/scripts/assistant.sh` ersetzen.

## runtime

| Tool-ID | Modus | externe Wirkung | Approval | Verfuegbarkeit | Kommando | Test |
|---|---|---:|---|---|---|---|
| `assistant.status` | `read` | nein | `none` | `always` | `./scripts/assistant.sh status` | `tests/test_m5_tool_contract.py` |
| `assistant.version` | `read` | nein | `none` | `always` | `./scripts/assistant.sh version --verify` | `tests/test_m5_tool_contract.py` |
| `assistant.version.history` | `read` | nein | `none` | `always` | `./scripts/assistant.sh version --verify --history --limit 10` | `tests/test_m5_tool_contract.py` |
| `assistant.version.since` | `read` | nein | `none` | `always` | `./scripts/assistant.sh version --verify --history --since "<Version>" --limit 20` | `tests/test_m5_tool_contract.py` |
| `assistant.search` | `read` | nein | `none` | `always` | `./scripts/assistant.sh search "<Suchbegriff>"` | `tests/test_m5_tool_contract.py` |
| `assistant.monitor.status` | `read` | nein | `none` | `always` | `./scripts/assistant.sh monitor status --days 7 --live` | `tests/test_m5_tool_contract.py` |
| `assistant.jobs.status` | `read` | nein | `none` | `always` | `./scripts/assistant.sh jobs status --target all` | `tests/test_m5_tool_contract.py` |
| `assistant.scheduler.status` | `read` | nein | `none` | `always` | `./scripts/assistant.sh scheduler status` | `tests/test_m5_tool_contract.py` |
| `assistant.scheduler.doctor` | `read` | nein | `none` | `always` | `./scripts/assistant.sh scheduler doctor` | `tests/test_m5_tool_contract.py` |
| `assistant.scheduler.activity` | `read` | nein | `none` | `always` | `./scripts/assistant.sh scheduler activity` | `tests/test_m5_tool_contract.py` |
| `assistant.scheduler.focus` | `local-write` | nein | `adaptive-local-focus-only` | `always` | `./scripts/assistant.sh scheduler focus --topic "<mail\|portfolio\|knowledge\|planning\|operations>" --minutes 30` | `tests/test_m5_tool_contract.py` |
| `assistant.jobs.check` | `local-write` | nein | `job-monitoring-and-safe-mail-recovery` | `always` | `./scripts/assistant.sh jobs check --target all --deep` | `tests/test_m5_tool_contract.py` |
| `assistant.jobs.alerts` | `read` | nein | `none` | `always` | `./scripts/assistant.sh jobs alerts` | `tests/test_m5_tool_contract.py` |
| `assistant.jobs.on` | `local-write` | nein | `explicit-user-start` | `always` | `./scripts/assistant.sh jobs on standard` | `tests/test_m5_tool_contract.py` |
| `assistant.jobs.restart` | `local-write` | nein | `explicit-user-restart` | `always` | `./scripts/assistant.sh jobs restart standard` | `tests/test_m5_tool_contract.py` |
| `assistant.jobs.off` | `local-write` | nein | `explicit-user-stop` | `always` | `./scripts/assistant.sh jobs off standard` | `tests/test_m5_tool_contract.py` |
| `assistant.ollama.status` | `read` | nein | `none` | `always` | `./scripts/assistant.sh ollama status` | `tests/test_m5_tool_contract.py` |
| `assistant.ollama.check` | `read` | nein | `none` | `always` | `./scripts/assistant.sh ollama check` | `tests/test_m5_tool_contract.py` |
| `assistant.ollama.queue` | `read` | nein | `none` | `always` | `./scripts/assistant.sh ollama queue` | `tests/test_m5_tool_contract.py` |
| `assistant.ollama.start` | `local-write` | nein | `explicit-user-start` | `always` | `./scripts/assistant.sh ollama start` | `tests/test_m5_tool_contract.py` |
| `assistant.ollama.restart` | `local-write` | nein | `explicit-user-restart` | `always` | `./scripts/assistant.sh ollama restart` | `tests/test_m5_tool_contract.py` |
| `assistant.performance.mail` | `read` | nein | `none` | `always` | `./scripts/assistant.sh performance mail --limit 20` | `tests/test_m5_tool_contract.py` |
| `assistant.monitor.record` | `local-write` | nein | `monitoring-local-only` | `always` | `./scripts/assistant.sh monitor record --days 7 --live` | `tests/test_m5_tool_contract.py` |
| `assistant.monitor.history` | `read` | nein | `none` | `always` | `./scripts/assistant.sh monitor history --days 30` | `tests/test_m5_tool_contract.py` |

## portfolio

| Tool-ID | Modus | externe Wirkung | Approval | Verfuegbarkeit | Kommando | Test |
|---|---|---:|---|---|---|---|
| `portfolio.status` | `read` | nein | `none` | `always` | `./scripts/assistant.sh portfolio status` | `tests/test_portfolio_tool.py` |
| `portfolio.setup` | `local-write` | nein | `explicit-user-permission-setup` | `always` | `./scripts/assistant.sh setup portfolio --provider eodhd --interval-minutes 15 --approve-permissions` | `tests/test_portfolio_tool.py` |
| `portfolio.doctor` | `read` | nein | `none` | `always` | `./scripts/assistant.sh portfolio doctor` | `tests/test_portfolio_tool.py` |
| `portfolio.import.pp` | `local-write` | nein | `explicit-user-import-with-preview` | `always` | `./scripts/assistant.sh portfolio import-pp --file "<Datei-im-Importordner>" --dry-run` | `tests/test_portfolio_tool.py` |
| `portfolio.import.pp.confirm` | `local-write` | nein | `explicit-user-confirmed-import` | `always` | `./scripts/assistant.sh portfolio import-pp --file "<Datei-im-Importordner>" --yes` | `tests/test_portfolio_tool.py` |
| `portfolio.import.csv` | `local-write` | nein | `explicit-user-import-with-preview` | `always` | `./scripts/assistant.sh portfolio import-csv --file "<Datei-im-Importordner>" --dry-run` | `tests/test_portfolio_tool.py` |
| `portfolio.import.csv.nextcloud` | `local-write` | nein | `explicit-user-import-with-preview` | `always` | `./scripts/assistant.sh portfolio import-csv --nextcloud-path "Assistent/Finanzen/Portfolio/<Datei-DD.MM.YYYY.csv>" --dry-run` | `tests/test_portfolio_tool.py` |
| `portfolio.import.csv.confirm` | `local-write` | nein | `explicit-user-confirmed-import` | `always` | `./scripts/assistant.sh portfolio import-csv --file "<Datei-im-Importordner>" --yes` | `tests/test_portfolio_tool.py` |
| `portfolio.import.csv.nextcloud.confirm` | `local-write` | nein | `explicit-user-confirmed-import` | `always` | `./scripts/assistant.sh portfolio import-csv --nextcloud-path "Assistent/Finanzen/Portfolio/<Datei-DD.MM.YYYY.csv>" --yes` | `tests/test_portfolio_tool.py` |
| `portfolio.holdings` | `read` | nein | `none` | `always` | `./scripts/assistant.sh portfolio holdings` | `tests/test_portfolio_tool.py` |
| `portfolio.valuation` | `read` | nein | `none` | `always` | `./scripts/assistant.sh portfolio valuation` | `tests/test_portfolio_tool.py` |
| `portfolio.watchlist` | `read` | nein | `none` | `always` | `./scripts/assistant.sh portfolio watchlist list` | `tests/test_portfolio_tool.py` |
| `portfolio.mapping.suggest` | `read` | nein | `none` | `always` | `./scripts/assistant.sh portfolio mapping suggest --isin "<ISIN>"` | `tests/test_portfolio_tool.py` |
| `portfolio.mapping.discover` | `read` | nein | `none` | `always` | `./scripts/assistant.sh portfolio mapping suggest --query "<Unternehmen-oder-Symbol>"` | `tests/test_portfolio_tool.py` |
| `portfolio.watchlist.add` | `local-write` | nein | `explicit-user-watchlist-change` | `always` | `./scripts/assistant.sh portfolio watchlist add --isin "<ISIN>" --name "<Name>" --symbol "<Symbol>" --mic "<MIC>" --currency "<ISO>" --yes` | `tests/test_portfolio_tool.py` |
| `portfolio.watchlist.disable` | `local-write` | nein | `explicit-user-watchlist-change` | `always` | `./scripts/assistant.sh portfolio watchlist disable --isin "<ISIN>" --yes` | `tests/test_portfolio_tool.py` |
| `portfolio.quotes.status` | `read` | nein | `none` | `always` | `./scripts/assistant.sh portfolio quotes status` | `tests/test_portfolio_tool.py` |
| `portfolio.quotes.get` | `read` | nein | `none` | `always` | `./scripts/assistant.sh portfolio quotes get --isin "<ISIN>"` | `tests/test_portfolio_tool.py` |
| `portfolio.quotes.refresh` | `local-write` | nein | `scheduled-market-data-refresh` | `always` | `./scripts/assistant.sh portfolio quotes refresh` | `tests/test_portfolio_tool.py` |
| `portfolio.quotes.refresh.force` | `local-write` | nein | `explicit-user-diagnostic-refresh` | `always` | `./scripts/assistant.sh portfolio quotes refresh --force` | `tests/test_portfolio_tool.py` |
| `portfolio.analyze` | `read` | nein | `none` | `always` | `./scripts/assistant.sh portfolio analyze --isin "<ISIN>"` | `tests/test_portfolio_tool.py` |
| `portfolio.research.status` | `read` | nein | `none` | `always` | `./scripts/assistant.sh portfolio research status` | `tests/test_portfolio_research.py` |
| `portfolio.research.models` | `read` | nein | `none` | `always` | `./scripts/assistant.sh portfolio research models` | `tests/test_portfolio_research.py` |
| `portfolio.research.screen` | `local-write` | nein | `provider-research-cache-local-only` | `always` | `./scripts/assistant.sh portfolio research screen --strategy "<auto\|balanced\|quality-value\|quality-growth\|dividend-quality>" --exchange "<Boerse-optional>" --sector "<Sektor-optional>" --limit 5` | `tests/test_portfolio_research.py` |
| `portfolio.research.analyze` | `local-write` | nein | `provider-research-cache-local-only` | `always` | `./scripts/assistant.sh portfolio research analyze --isin "<ISIN>" --strategy "<auto\|balanced\|quality-value\|quality-growth\|dividend-quality>"` | `tests/test_portfolio_research.py` |
| `portfolio.research.history` | `read` | nein | `none` | `always` | `./scripts/assistant.sh portfolio research history --limit 20` | `tests/test_portfolio_research.py` |
| `portfolio.philosophy.show` | `read` | nein | `none` | `always` | `./scripts/assistant.sh portfolio philosophy show` | `tests/test_portfolio_research.py` |
| `portfolio.philosophy.review` | `read` | nein | `none` | `always` | `./scripts/assistant.sh portfolio philosophy review` | `tests/test_portfolio_research.py` |
| `portfolio.philosophy.history` | `read` | nein | `none` | `always` | `./scripts/assistant.sh portfolio philosophy history --limit 20` | `tests/test_portfolio_research.py` |
| `portfolio.philosophy.set` | `local-write` | nein | `explicit-user-investment-profile-change` | `always` | `./scripts/assistant.sh portfolio philosophy set --risk-tolerance "<conservative\|balanced\|growth>" --horizon-years <1-50> --strategy "<balanced\|quality-value\|quality-growth\|dividend-quality>" --max-position-pct "<Prozent>" --max-sector-pct "<Prozent>" --preferred-sectors "<Kommaliste>" --excluded-sectors "<Kommaliste>" --notes "<Notiz>" --yes` | `tests/test_portfolio_research.py` |
| `portfolio.philosophy.feedback` | `local-write` | nein | `explicit-user-investment-feedback` | `always` | `./scripts/assistant.sh portfolio philosophy feedback --candidate-id "<Research-Kandidaten-ID>" --decision "<interested\|rejected\|watch\|bought\|sold>" --reason "<Begruendung>" --yes` | `tests/test_portfolio_research.py` |
| `portfolio.alerts` | `read` | nein | `none` | `always` | `./scripts/assistant.sh portfolio alerts list` | `tests/test_portfolio_tool.py` |
| `portfolio.alerts.add` | `local-write` | nein | `explicit-user-alert-change` | `always` | `./scripts/assistant.sh portfolio alerts add --isin "<ISIN>" --direction "<above\|below>" --threshold "<Kurs>" --currency "<ISO>" --yes` | `tests/test_portfolio_tool.py` |
| `portfolio.alerts.disable` | `local-write` | nein | `explicit-user-alert-change` | `always` | `./scripts/assistant.sh portfolio alerts disable --id "<Regel-ID>" --yes` | `tests/test_portfolio_tool.py` |
| `portfolio.performance` | `read` | nein | `none` | `always` | `./scripts/assistant.sh portfolio performance` | `tests/test_portfolio_tool.py` |
| `portfolio.job.on` | `local-write` | nein | `explicit-user-start` | `always` | `./scripts/assistant.sh jobs on portfolio` | `tests/test_portfolio_tool.py` |
| `portfolio.job.restart` | `local-write` | nein | `explicit-user-restart` | `always` | `./scripts/assistant.sh jobs restart portfolio` | `tests/test_portfolio_tool.py` |
| `portfolio.job.off` | `local-write` | nein | `explicit-user-stop` | `always` | `./scripts/assistant.sh jobs off portfolio` | `tests/test_portfolio_tool.py` |

## security

| Tool-ID | Modus | externe Wirkung | Approval | Verfuegbarkeit | Kommando | Test |
|---|---|---:|---|---|---|---|
| `security.antivirus.doctor` | `read` | nein | `none` | `always` | `./scripts/assistant.sh security antivirus doctor` | `tests/test_antivirus_tool.py` |
| `security.antivirus.self-test` | `read` | nein | `none` | `always` | `./scripts/assistant.sh security antivirus self-test` | `tests/test_antivirus_tool.py` |
| `security.antivirus.scan` | `read` | nein | `host-antivirus-read-only` | `always` | `./scripts/assistant.sh security antivirus scan --file "personal_assistant/data/workspace_outbox/<Datei>"` | `tests/test_antivirus_tool.py` |

## nextcloud

| Tool-ID | Modus | externe Wirkung | Approval | Verfuegbarkeit | Kommando | Test |
|---|---|---:|---|---|---|---|
| `nextcloud.list` | `read` | nein | `none` | `always` | `./scripts/assistant.sh nextcloud list --path "Assistent"` | `tests/test_nextcloud_workspace_tools.py` |
| `nextcloud.sync` | `read` | nein | `none` | `always` | `./scripts/assistant.sh nextcloud sync` | `tests/test_nextcloud_workspace_tools.py` |
| `nextcloud.workspace.mkdir` | `write` | ja | `workspace-create` | `workspace-mkdir` | `./scripts/assistant.sh nextcloud mkdir --path "{workspace_root}/<Ordner>"` | `tests/test_nextcloud_workspace_tools.py` |
| `nextcloud.workspace.write-text` | `write` | ja | `workspace-create-only` | `workspace-write-text` | `printf "%s" "<Inhalt>" \| ./scripts/assistant.sh nextcloud write-text --path "{workspace_root}/<Datei>.md"` | `tests/test_nextcloud_workspace_tools.py` |
| `nextcloud.workspace.upload` | `write` | ja | `workspace-create-only` | `workspace-upload` | `./scripts/assistant.sh nextcloud upload --local "personal_assistant/data/workspace_outbox/<Datei>" --path "{workspace_root}/<Ziel>"` | `tests/test_nextcloud_workspace_tools.py` |
| `nextcloud.workspace.move` | `write` | ja | `workspace-organize-no-overwrite` | `workspace-move` | `./scripts/assistant.sh nextcloud move --source "{workspace_root}/<Quelle>" --destination "{workspace_root}/<Ziel>"` | `tests/test_nextcloud_workspace_tools.py` |
| `nextcloud.workspace.configure` | `local-write` | nein | `explicit-user-permission` | `workspace` | `./scripts/assistant.sh setup workspace --root "{workspace_root}" --approve-permissions` | `tests/test_nextcloud_workspace_tools.py` |

## mail

| Tool-ID | Modus | externe Wirkung | Approval | Verfuegbarkeit | Kommando | Test |
|---|---|---:|---|---|---|---|
| `mail.status` | `read` | nein | `none` | `always` | `./scripts/assistant.sh mail status` | `tests/test_agent_tool_architecture.py` |
| `mail.doctor` | `read` | nein | `none` | `always` | `./scripts/assistant.sh mail doctor` | `tests/test_agent_tool_architecture.py` |
| `mail.review.status` | `read` | nein | `none` | `always` | `./scripts/assistant.sh mail review status --days 7` | `tests/test_mail_review_m9.py` |
| `mail.review.list` | `read` | nein | `none` | `always` | `./scripts/assistant.sh mail review list --reason "<Grund>" --limit 50` | `tests/test_mail_review_m9.py` |
| `mail.review.suggest` | `read` | nein | `none` | `mail-move` | `./scripts/assistant.sh mail review suggest --folder "<Ordner>" --message-id "<ID>" --expected-subject "<Betreff>"` | `tests/test_mail_review_m9.py` |
| `mail.folders.plan` | `read` | nein | `none` | `always` | `./scripts/assistant.sh mail folders plan` | `tests/test_mail_review_m9.py` |
| `mail.folders.apply` | `write` | ja | `explicit-user-create-configured-mail-folders` | `always` | `./scripts/assistant.sh mail folders apply --yes` | `tests/test_mail_review_m9.py` |
| `mail.learning.status` | `read` | nein | `none` | `always` | `./scripts/assistant.sh mail learning status` | `tests/test_agent_tool_architecture.py` |
| `mail.learning.feedback` | `read` | nein | `none` | `always` | `./scripts/assistant.sh mail learning feedback --limit 50` | `tests/test_agent_tool_architecture.py` |
| `mail.learning.not-spam` | `read` | nein | `none` | `always` | `./scripts/assistant.sh mail learning not-spam --limit 100` | `tests/test_agent_tool_architecture.py` |
| `mail.learning.mixed-senders` | `read` | nein | `none` | `always` | `./scripts/assistant.sh mail learning mixed-senders --limit 100` | `tests/test_agent_tool_architecture.py` |
| `mail.learning.conflicts` | `read` | nein | `none` | `always` | `./scripts/assistant.sh mail learning conflicts --limit 100` | `tests/test_agent_tool_architecture.py` |
| `mail.learning.evaluate` | `read` | nein | `none` | `always` | `./scripts/assistant.sh mail learning evaluate --limit 5000` | `tests/test_agent_tool_architecture.py` |
| `mail.learning.dataset-export` | `local-write` | nein | `learning-dataset-local-only` | `always` | `./scripts/assistant.sh mail learning dataset-export --output "mail_agent/data/learning_dataset.json" --limit 5000` | `tests/test_agent_tool_architecture.py` |
| `mail.learning.folder-list` | `read` | nein | `none` | `always` | `./scripts/assistant.sh mail learning folder-list` | `tests/test_agent_tool_architecture.py` |
| `mail.learning.folder-create` | `write` | ja | `explicit-user-create-correction-folder` | `always` | `./scripts/assistant.sh mail learning folder-create --parent "<routine\|important\|spam\|not-spam>" --name "<Name>" --label "<Typ>" --yes` | `tests/test_agent_tool_architecture.py` |
| `mail.learning.folder-disable` | `local-write` | nein | `explicit-user-disable-learning-folder` | `always` | `./scripts/assistant.sh mail learning folder-disable --folder "<Ordner>" --yes` | `tests/test_agent_tool_architecture.py` |
| `mail.sources.configure` | `local-write` | nein | `safe-settings` | `always` | `./scripts/assistant.sh setup mail-sources --primary "INBOX" --quarantine-folder "Spamverdacht"` | `tests/test_agent_tool_architecture.py` |
| `mail.dry-run` | `read` | nein | `none` | `always` | `./scripts/assistant.sh mail dry-run --limit 20` | `tests/test_agent_tool_architecture.py` |
| `mail.run` | `write` | ja | `configured-policy` | `always` | `./scripts/assistant.sh mail run --limit 20` | `tests/test_agent_tool_architecture.py` |
| `mail.spam-review` | `write` | ja | `quarantine-rescue-policy` | `always` | `./scripts/assistant.sh mail spam-review --limit 20` | `tests/test_agent_tool_architecture.py` |
| `mail.move-status` | `read` | nein | `none` | `mail-move` | `./scripts/assistant.sh mail move-status` | `tests/test_agent_tool_architecture.py` |
| `mail.list` | `read` | nein | `none` | `mail-move` | `./scripts/assistant.sh mail list --folder "<Ordner>" --limit 50` | `tests/test_agent_tool_architecture.py` |
| `mail.search` | `read` | nein | `none` | `mail-move` | `./scripts/assistant.sh mail search --query "<Suchbegriff>" --limit 50` | `tests/test_agent_tool_architecture.py` |
| `mail.read` | `read` | nein | `none` | `mail-move` | `./scripts/assistant.sh mail read --folder "<Ordner>" --message-id "<ID>" --expected-subject "<Betreff>"` | `tests/test_agent_tool_architecture.py` |
| `mail.reply-draft` | `local-write` | nein | `draft-only-no-send` | `mail-move` | `./scripts/assistant.sh mail reply-draft --folder "<Ordner>" --message-id "<ID>" --expected-subject "<Betreff>" --body "<Entwurf>"` | `tests/test_agent_tool_architecture.py` |
| `mail.reply-send` | `write` | ja | `explicit-user-approved-presented-draft` | `mail-move` | `./scripts/assistant.sh mail reply-send --draft-id "<Entwurfs-ID>" --yes` | `tests/test_agent_tool_architecture.py` |
| `mail.compose-draft` | `local-write` | nein | `draft-only-no-send` | `mail-move` | `./scripts/assistant.sh mail compose-draft --to "<Empfaenger>" --subject "<Betreff>" --body "<Entwurf>"` | `tests/test_agent_tool_architecture.py` |
| `mail.compose-send` | `write` | ja | `explicit-user-approved-presented-draft` | `mail-move` | `./scripts/assistant.sh mail compose-send --draft-id "<Entwurfs-ID>" --yes` | `tests/test_agent_tool_architecture.py` |
| `mail.move` | `write` | ja | `configured-mail-organize-single-message` | `mail-move` | `./scripts/assistant.sh mail move --source "<Quelle>" --destination "<Ziel>" --message-id "<ID>" --expected-subject "<Betreff>"` | `tests/test_agent_tool_architecture.py` |
| `mail.calendar-command` | `write` | ja | `trusted-owner-command` | `calendar-mail` | `Subject: {calendar_subject_prefix} <Terminbeschreibung>` | `tests/test_agent_tool_architecture.py` |

## contacts

| Tool-ID | Modus | externe Wirkung | Approval | Verfuegbarkeit | Kommando | Test |
|---|---|---:|---|---|---|---|
| `nextcloud.contacts.discover` | `read` | nein | `none` | `always` | `./scripts/assistant.sh contacts discover` | `tests/test_carddav_contact_tools.py` |
| `nextcloud.contacts.configure` | `local-write` | nein | `explicit-user-addressbook-selection` | `always` | `./scripts/assistant.sh contacts configure --resource "<resource_id>" --allow-update --yes` | `tests/test_carddav_contact_tools.py` |
| `nextcloud.contacts.status` | `read` | nein | `none` | `contacts` | `./scripts/assistant.sh contacts status` | `tests/test_carddav_contact_tools.py` |
| `nextcloud.contacts.list` | `read` | nein | `none` | `contacts-list` | `./scripts/assistant.sh contacts list --limit 100` | `tests/test_carddav_contact_tools.py` |
| `nextcloud.contacts.search` | `read` | nein | `none` | `contacts-list` | `./scripts/assistant.sh contacts search --query "<Suchbegriff>" --limit 50` | `tests/test_carddav_contact_tools.py` |
| `nextcloud.contacts.update` | `write` | ja | `explicit-user-contact-update-etag-guarded` | `contacts-update` | `./scripts/assistant.sh contacts update --uid "<UID>" --expected-name "<aktueller Name>" --phone "<neue Telefonnummer>" --yes` | `tests/test_carddav_contact_tools.py` |
| `nextcloud.contacts.create` | `write` | ja | `explicit-user-contact-create-only` | `contacts-create` | `./scripts/assistant.sh contacts create --name "<Name>" --email "<E-Mail>" --phone "<Telefon>" --organization "<Firma>" --yes` | `tests/test_carddav_contact_tools.py` |
| `nextcloud.contacts.from-mail-preview` | `read` | nein | `none` | `contacts-create` | `./scripts/assistant.sh contacts from-mail --folder "<Ordner>" --message-id "<Mail-ID>" --expected-subject "<Betreff>" --dry-run` | `tests/test_carddav_contact_tools.py` |
| `nextcloud.contacts.from-mail-create` | `write` | ja | `explicit-user-contact-from-mail-create-only` | `contacts-create` | `./scripts/assistant.sh contacts from-mail --folder "<Ordner>" --message-id "<Mail-ID>" --expected-subject "<Betreff>" --yes` | `tests/test_carddav_contact_tools.py` |

## calendar

| Tool-ID | Modus | externe Wirkung | Approval | Verfuegbarkeit | Kommando | Test |
|---|---|---:|---|---|---|---|
| `nextcloud.calendar.discover` | `read` | nein | `none` | `always` | `./scripts/assistant.sh calendar discover` | `tests/test_direct_calendar_tool.py` |
| `nextcloud.calendar.configure` | `local-write` | nein | `explicit-user-calendar-selection` | `always` | `./scripts/assistant.sh calendar configure --resource "<resource_id>" --allow-update --yes` | `tests/test_direct_calendar_tool.py` |
| `nextcloud.calendar.status` | `read` | nein | `none` | `calendar` | `./scripts/assistant.sh calendar status` | `tests/test_direct_calendar_tool.py` |
| `nextcloud.calendar.list` | `read` | nein | `none` | `calendar-list` | `./scripts/assistant.sh calendar list --limit 100` | `tests/test_direct_calendar_tool.py` |
| `nextcloud.calendar.search` | `read` | nein | `none` | `calendar-list` | `./scripts/assistant.sh calendar search --query "<Suchbegriff>" --limit 50` | `tests/test_direct_calendar_tool.py` |
| `nextcloud.calendar.update` | `write` | ja | `explicit-user-calendar-update-etag-guarded` | `calendar-update` | `./scripts/assistant.sh calendar update --uid "<UID>" --expected-title "<aktueller Titel>" --start "<ISO-8601>" --yes` | `tests/test_direct_calendar_tool.py` |
| `nextcloud.calendar.create` | `write` | ja | `configured-calendar-create-only` | `calendar-create` | `./scripts/assistant.sh calendar create --title "<Titel>" --start "<ISO-8601>" --end "<ISO-8601>" --location "<Ort>" --description "<Beschreibung>"` | `tests/test_direct_calendar_tool.py` |

## tasks

| Tool-ID | Modus | externe Wirkung | Approval | Verfuegbarkeit | Kommando | Test |
|---|---|---:|---|---|---|---|
| `nextcloud.tasks.discover` | `read` | nein | `none` | `always` | `./scripts/assistant.sh tasks discover` | `tests/test_direct_tasks_tool.py` |
| `nextcloud.tasks.configure` | `local-write` | nein | `explicit-user-task-list-selection` | `always` | `./scripts/assistant.sh tasks configure --resource "<resource_id>" --allow-update --yes` | `tests/test_direct_tasks_tool.py` |
| `nextcloud.tasks.status` | `read` | nein | `none` | `tasks` | `./scripts/assistant.sh tasks status` | `tests/test_direct_tasks_tool.py` |
| `nextcloud.tasks.list` | `read` | nein | `none` | `tasks-list` | `./scripts/assistant.sh tasks list --include-completed --limit 100` | `tests/test_direct_tasks_tool.py` |
| `nextcloud.tasks.update` | `write` | ja | `explicit-user-task-update-etag-guarded` | `tasks-update` | `./scripts/assistant.sh tasks update --uid "<UID>" --expected-title "<aktueller Titel>" --due "<YYYY-MM-DD oder ISO-8601>" --yes` | `tests/test_direct_tasks_tool.py` |
| `nextcloud.tasks.create` | `write` | ja | `configured-tasks-create-only` | `tasks-create` | `./scripts/assistant.sh tasks create --title "<Titel>" --due "<YYYY-MM-DD oder ISO-8601>" --priority <0-9> --description "<Beschreibung>"` | `tests/test_direct_tasks_tool.py` |

## orders

| Tool-ID | Modus | externe Wirkung | Approval | Verfuegbarkeit | Kommando | Test |
|---|---|---:|---|---|---|---|
| `nextcloud.deck.orders.status` | `read` | nein | `none` | `orders` | `./scripts/assistant.sh orders status` | `tests/test_order_deck_tool.py` |
| `nextcloud.deck.orders.list` | `read` | nein | `none` | `orders` | `./scripts/assistant.sh orders list --limit 100` | `tests/test_order_deck_tool.py` |
| `nextcloud.deck.discover` | `read` | nein | `none` | `orders` | `./scripts/assistant.sh deck discover` | `tests/test_order_deck_tool.py` |
| `mail.orders.import` | `read` | nein | `none` | `orders` | `./scripts/assistant.sh mail orders-import --limit 500 --dry-run` | `tests/test_order_deck_tool.py` |
| `nextcloud.deck.orders.sync` | `write` | ja | `managed-order-cards-only` | `orders` | `./scripts/assistant.sh orders sync --limit 500` | `tests/test_order_deck_tool.py` |
| `nextcloud.deck.orders.due-date-preview` | `read` | nein | `none` | `orders` | `./scripts/assistant.sh orders due-date-backfill --limit 500 --dry-run` | `tests/test_order_deck_tool.py` |
| `nextcloud.deck.orders.due-date-backfill` | `write` | ja | `managed-order-cards-missing-due-only` | `orders` | `./scripts/assistant.sh orders due-date-backfill --limit 500 --yes` | `tests/test_order_deck_tool.py` |

## invoices

| Tool-ID | Modus | externe Wirkung | Approval | Verfuegbarkeit | Kommando | Test |
|---|---|---:|---|---|---|---|
| `mail.invoice-archive` | `write` | ja | `automatic-create-only` | `invoices` | `./scripts/assistant.sh mail run --limit 20` | `tests/test_invoice_ocr_register.py` |
| `assistant.invoices.status` | `read` | nein | `none` | `invoices` | `./scripts/assistant.sh invoices status` | `tests/test_invoice_ocr_register.py` |
| `assistant.invoices.list` | `read` | nein | `none` | `invoices` | `./scripts/assistant.sh invoices list --year <YYYY> --limit 100` | `tests/test_invoice_ocr_register.py` |
| `assistant.invoices.review` | `read` | nein | `none` | `invoices` | `./scripts/assistant.sh invoices review --limit 100` | `tests/test_invoice_ocr_register.py` |
| `assistant.invoices.export` | `local-write` | nein | `managed-invoice-register` | `invoices` | `./scripts/assistant.sh invoices export --year <YYYY>` | `tests/test_invoice_ocr_register.py` |
| `assistant.invoices.export-nextcloud` | `write` | ja | `explicit-user-export-create-only` | `invoices` | `./scripts/assistant.sh invoices export --year <YYYY> --nextcloud --yes` | `tests/test_invoice_ocr_register.py` |
| `assistant.invoices.backfill-preview` | `read` | nein | `none` | `invoices` | `./scripts/assistant.sh invoices backfill --year <YYYY> --limit 500 --dry-run` | `tests/test_invoice_ocr_register.py` |
| `assistant.invoices.backfill` | `local-write` | nein | `explicit-user-invoice-backfill` | `invoices` | `./scripts/assistant.sh invoices backfill --year <YYYY> --limit 500 --yes` | `tests/test_invoice_ocr_register.py` |
| `assistant.invoices.correct` | `local-write` | nein | `explicit-user-correction` | `invoices` | `./scripts/assistant.sh invoices correct --hash <SHA256> --date <YYYY-MM-DD> --number "<Nr>" --supplier "<Steller>" --category "<Kategorie>" --gross "<Betrag>" --yes` | `tests/test_invoice_ocr_register.py` |
