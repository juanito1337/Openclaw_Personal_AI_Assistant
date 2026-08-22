# Generierte Befehlsreferenz

Diese Datei wird deterministisch aus den domaenennahen typisierten Toolvertraegen erzeugt.
Nicht manuell bearbeiten; `python3 scripts/generate-command-reference.py` aktualisiert sie.
Der Katalog beschreibt bekannte Werkzeuge, nicht die live erteilten Rechte einer Instanz.

Konfigurationsfreie Sicht: `./scripts/assistant.sh tools list --catalog` und
`./scripts/assistant.sh capabilities --schema`. Live-Sicht:
`./scripts/assistant.sh tools list` und `./scripts/assistant.sh capabilities`.

## runtime

| Tool-ID | Modus | externe Wirkung | Approval | Verfuegbarkeit | Kommando | Doku | Test |
|---|---|---:|---|---|---|---|---|
| `assistant.status` | `read` | nein | `none` | `always` | `./scripts/assistant.sh status` | `AGENTS.md` | `tests/test_m5_tool_contract.py` |
| `assistant.version` | `read` | nein | `none` | `always` | `./scripts/assistant.sh version --verify` | `AGENTS.md` | `tests/test_m5_tool_contract.py` |
| `assistant.version.history` | `read` | nein | `none` | `always` | `./scripts/assistant.sh version --verify --history --limit 10` | `AGENTS.md` | `tests/test_m5_tool_contract.py` |
| `assistant.version.since` | `read` | nein | `none` | `always` | `./scripts/assistant.sh version --verify --history --since "<Version>" --limit 20` | `AGENTS.md` | `tests/test_m5_tool_contract.py` |
| `assistant.search` | `read` | nein | `none` | `always` | `./scripts/assistant.sh search "<Suchbegriff>"` | `AGENTS.md` | `tests/test_m5_tool_contract.py` |
| `assistant.monitor.status` | `read` | nein | `none` | `always` | `./scripts/assistant.sh monitor status --days 7 --live` | `AGENTS.md` | `tests/test_m5_tool_contract.py` |
| `assistant.jobs.status` | `read` | nein | `none` | `always` | `./scripts/assistant.sh jobs status --target all` | `AGENTS.md` | `tests/test_m5_tool_contract.py` |
| `assistant.scheduler.status` | `read` | nein | `none` | `always` | `./scripts/assistant.sh scheduler status` | `AGENTS.md` | `tests/test_m5_tool_contract.py` |
| `assistant.scheduler.doctor` | `read` | nein | `none` | `always` | `./scripts/assistant.sh scheduler doctor` | `AGENTS.md` | `tests/test_m5_tool_contract.py` |
| `assistant.scheduler.activity` | `read` | nein | `none` | `always` | `./scripts/assistant.sh scheduler activity` | `AGENTS.md` | `tests/test_m5_tool_contract.py` |
| `assistant.scheduler.focus` | `local-write` | nein | `adaptive-local-focus-only` | `always` | `./scripts/assistant.sh scheduler focus --topic "<mail\|portfolio\|knowledge\|planning\|operations>" --minutes 30` | `AGENTS.md` | `tests/test_m5_tool_contract.py` |
| `assistant.jobs.check` | `local-write` | nein | `job-monitoring-and-safe-mail-recovery` | `always` | `./scripts/assistant.sh jobs check --target all --deep` | `AGENTS.md` | `tests/test_m5_tool_contract.py` |
| `assistant.jobs.alerts` | `read` | nein | `none` | `always` | `./scripts/assistant.sh jobs alerts` | `AGENTS.md` | `tests/test_m5_tool_contract.py` |
| `assistant.jobs.on` | `local-write` | nein | `explicit-user-start` | `always` | `./scripts/assistant.sh jobs on standard` | `AGENTS.md` | `tests/test_m5_tool_contract.py` |
| `assistant.jobs.restart` | `local-write` | nein | `explicit-user-restart` | `always` | `./scripts/assistant.sh jobs restart standard` | `AGENTS.md` | `tests/test_m5_tool_contract.py` |
| `assistant.jobs.off` | `local-write` | nein | `explicit-user-stop` | `always` | `./scripts/assistant.sh jobs off standard` | `AGENTS.md` | `tests/test_m5_tool_contract.py` |
| `assistant.ollama.status` | `read` | nein | `none` | `always` | `./scripts/assistant.sh ollama status` | `AGENTS.md` | `tests/test_m5_tool_contract.py` |
| `assistant.ollama.check` | `read` | nein | `none` | `always` | `./scripts/assistant.sh ollama check` | `AGENTS.md` | `tests/test_m5_tool_contract.py` |
| `assistant.ollama.queue` | `read` | nein | `none` | `always` | `./scripts/assistant.sh ollama queue` | `AGENTS.md` | `tests/test_m5_tool_contract.py` |
| `assistant.ollama.start` | `local-write` | nein | `explicit-user-start` | `always` | `./scripts/assistant.sh ollama start` | `AGENTS.md` | `tests/test_m5_tool_contract.py` |
| `assistant.ollama.restart` | `local-write` | nein | `explicit-user-restart` | `always` | `./scripts/assistant.sh ollama restart` | `AGENTS.md` | `tests/test_m5_tool_contract.py` |
| `assistant.performance.mail` | `read` | nein | `none` | `always` | `./scripts/assistant.sh performance mail --limit 20` | `AGENTS.md` | `tests/test_m5_tool_contract.py` |
| `assistant.monitor.record` | `local-write` | nein | `monitoring-local-only` | `always` | `./scripts/assistant.sh monitor record --days 7 --live` | `AGENTS.md` | `tests/test_m5_tool_contract.py` |
| `assistant.monitor.history` | `read` | nein | `none` | `always` | `./scripts/assistant.sh monitor history --days 30` | `AGENTS.md` | `tests/test_m5_tool_contract.py` |
## portfolio

| Tool-ID | Modus | externe Wirkung | Approval | Verfuegbarkeit | Kommando | Doku | Test |
|---|---|---:|---|---|---|---|---|
| `portfolio.status` | `read` | nein | `none` | `always` | `./scripts/assistant.sh portfolio status` | `docs/PORTFOLIO_ADVISOR.md` | `tests/test_portfolio_tool.py` |
| `portfolio.setup` | `local-write` | nein | `explicit-user-permission-setup` | `always` | `./scripts/assistant.sh setup portfolio --provider eodhd --interval-minutes 15 --approve-permissions` | `docs/PORTFOLIO_ADVISOR.md` | `tests/test_portfolio_tool.py` |
| `portfolio.doctor` | `read` | nein | `none` | `always` | `./scripts/assistant.sh portfolio doctor` | `docs/PORTFOLIO_ADVISOR.md` | `tests/test_portfolio_tool.py` |
| `portfolio.import.pp` | `local-write` | nein | `explicit-user-import-with-preview` | `always` | `./scripts/assistant.sh portfolio import-pp --file "<Datei-im-Importordner>" --dry-run` | `docs/PORTFOLIO_ADVISOR.md` | `tests/test_portfolio_tool.py` |
| `portfolio.import.pp.confirm` | `local-write` | nein | `explicit-user-confirmed-import` | `always` | `./scripts/assistant.sh portfolio import-pp --file "<Datei-im-Importordner>" --yes` | `docs/PORTFOLIO_ADVISOR.md` | `tests/test_portfolio_tool.py` |
| `portfolio.import.csv` | `local-write` | nein | `explicit-user-import-with-preview` | `always` | `./scripts/assistant.sh portfolio import-csv --file "<Datei-im-Importordner>" --dry-run` | `docs/PORTFOLIO_ADVISOR.md` | `tests/test_portfolio_tool.py` |
| `portfolio.import.csv.nextcloud` | `local-write` | nein | `explicit-user-import-with-preview` | `always` | `./scripts/assistant.sh portfolio import-csv --nextcloud-path "Assistent/Finanzen/Portfolio/<Datei-DD.MM.YYYY.csv>" --dry-run` | `docs/PORTFOLIO_ADVISOR.md` | `tests/test_portfolio_tool.py` |
| `portfolio.import.csv.confirm` | `local-write` | nein | `explicit-user-confirmed-import` | `always` | `./scripts/assistant.sh portfolio import-csv --file "<Datei-im-Importordner>" --yes` | `docs/PORTFOLIO_ADVISOR.md` | `tests/test_portfolio_tool.py` |
| `portfolio.import.csv.nextcloud.confirm` | `local-write` | nein | `explicit-user-confirmed-import` | `always` | `./scripts/assistant.sh portfolio import-csv --nextcloud-path "Assistent/Finanzen/Portfolio/<Datei-DD.MM.YYYY.csv>" --yes` | `docs/PORTFOLIO_ADVISOR.md` | `tests/test_portfolio_tool.py` |
| `portfolio.holdings` | `read` | nein | `none` | `always` | `./scripts/assistant.sh portfolio holdings` | `docs/PORTFOLIO_ADVISOR.md` | `tests/test_portfolio_tool.py` |
| `portfolio.valuation` | `read` | nein | `none` | `always` | `./scripts/assistant.sh portfolio valuation` | `docs/PORTFOLIO_ADVISOR.md` | `tests/test_portfolio_tool.py` |
| `portfolio.watchlist` | `read` | nein | `none` | `always` | `./scripts/assistant.sh portfolio watchlist list` | `docs/PORTFOLIO_ADVISOR.md` | `tests/test_portfolio_tool.py` |
| `portfolio.mapping.suggest` | `read` | nein | `none` | `always` | `./scripts/assistant.sh portfolio mapping suggest --isin "<ISIN>"` | `docs/PORTFOLIO_ADVISOR.md` | `tests/test_portfolio_tool.py` |
| `portfolio.mapping.discover` | `read` | nein | `none` | `always` | `./scripts/assistant.sh portfolio mapping suggest --query "<Unternehmen-oder-Symbol>"` | `docs/PORTFOLIO_ADVISOR.md` | `tests/test_portfolio_tool.py` |
| `portfolio.watchlist.add` | `local-write` | nein | `explicit-user-watchlist-change` | `always` | `./scripts/assistant.sh portfolio watchlist add --isin "<ISIN>" --name "<Name>" --symbol "<Symbol>" --mic "<MIC>" --currency "<ISO>" --yes` | `docs/PORTFOLIO_ADVISOR.md` | `tests/test_portfolio_tool.py` |
| `portfolio.watchlist.disable` | `local-write` | nein | `explicit-user-watchlist-change` | `always` | `./scripts/assistant.sh portfolio watchlist disable --isin "<ISIN>" --yes` | `docs/PORTFOLIO_ADVISOR.md` | `tests/test_portfolio_tool.py` |
| `portfolio.quotes.status` | `read` | nein | `none` | `always` | `./scripts/assistant.sh portfolio quotes status` | `docs/PORTFOLIO_ADVISOR.md` | `tests/test_portfolio_tool.py` |
| `portfolio.quotes.get` | `read` | nein | `none` | `always` | `./scripts/assistant.sh portfolio quotes get --isin "<ISIN>"` | `docs/PORTFOLIO_ADVISOR.md` | `tests/test_portfolio_tool.py` |
| `portfolio.quotes.refresh` | `local-write` | nein | `scheduled-market-data-refresh` | `always` | `./scripts/assistant.sh portfolio quotes refresh` | `docs/PORTFOLIO_ADVISOR.md` | `tests/test_portfolio_tool.py` |
| `portfolio.quotes.refresh.force` | `local-write` | nein | `explicit-user-diagnostic-refresh` | `always` | `./scripts/assistant.sh portfolio quotes refresh --force` | `docs/PORTFOLIO_ADVISOR.md` | `tests/test_portfolio_tool.py` |
| `portfolio.analyze` | `read` | nein | `none` | `always` | `./scripts/assistant.sh portfolio analyze --isin "<ISIN>"` | `docs/PORTFOLIO_ADVISOR.md` | `tests/test_portfolio_tool.py` |
| `portfolio.research.status` | `read` | nein | `none` | `always` | `./scripts/assistant.sh portfolio research status` | `docs/PORTFOLIO_ADVISOR.md` | `tests/test_portfolio_research.py` |
| `portfolio.research.models` | `read` | nein | `none` | `always` | `./scripts/assistant.sh portfolio research models` | `docs/PORTFOLIO_ADVISOR.md` | `tests/test_portfolio_research.py` |
| `portfolio.research.screen` | `local-write` | nein | `provider-research-cache-local-only` | `always` | `./scripts/assistant.sh portfolio research screen --strategy "<auto\|balanced\|quality-value\|quality-growth\|dividend-quality>" --exchange "<Boerse-optional>" --sector "<Sektor-optional>" --limit 5` | `docs/PORTFOLIO_ADVISOR.md` | `tests/test_portfolio_research.py` |
| `portfolio.research.analyze` | `local-write` | nein | `provider-research-cache-local-only` | `always` | `./scripts/assistant.sh portfolio research analyze --isin "<ISIN>" --strategy "<auto\|balanced\|quality-value\|quality-growth\|dividend-quality>"` | `docs/PORTFOLIO_ADVISOR.md` | `tests/test_portfolio_research.py` |
| `portfolio.research.history` | `read` | nein | `none` | `always` | `./scripts/assistant.sh portfolio research history --limit 20` | `docs/PORTFOLIO_ADVISOR.md` | `tests/test_portfolio_research.py` |
| `portfolio.philosophy.show` | `read` | nein | `none` | `always` | `./scripts/assistant.sh portfolio philosophy show` | `docs/PORTFOLIO_ADVISOR.md` | `tests/test_portfolio_research.py` |
| `portfolio.philosophy.review` | `read` | nein | `none` | `always` | `./scripts/assistant.sh portfolio philosophy review` | `docs/PORTFOLIO_ADVISOR.md` | `tests/test_portfolio_research.py` |
| `portfolio.philosophy.history` | `read` | nein | `none` | `always` | `./scripts/assistant.sh portfolio philosophy history --limit 20` | `docs/PORTFOLIO_ADVISOR.md` | `tests/test_portfolio_research.py` |
| `portfolio.philosophy.set` | `local-write` | nein | `explicit-user-investment-profile-change` | `always` | `./scripts/assistant.sh portfolio philosophy set --risk-tolerance "<conservative\|balanced\|growth>" --horizon-years <1-50> --strategy "<balanced\|quality-value\|quality-growth\|dividend-quality>" --max-position-pct "<Prozent>" --max-sector-pct "<Prozent>" --preferred-sectors "<Kommaliste>" --excluded-sectors "<Kommaliste>" --notes "<Notiz>" --yes` | `docs/PORTFOLIO_ADVISOR.md` | `tests/test_portfolio_research.py` |
| `portfolio.philosophy.feedback` | `local-write` | nein | `explicit-user-investment-feedback` | `always` | `./scripts/assistant.sh portfolio philosophy feedback --candidate-id "<Research-Kandidaten-ID>" --decision "<interested\|rejected\|watch\|bought\|sold>" --reason "<Begruendung>" --yes` | `docs/PORTFOLIO_ADVISOR.md` | `tests/test_portfolio_research.py` |
| `portfolio.alerts` | `read` | nein | `none` | `always` | `./scripts/assistant.sh portfolio alerts list` | `docs/PORTFOLIO_ADVISOR.md` | `tests/test_portfolio_tool.py` |
| `portfolio.alerts.add` | `local-write` | nein | `explicit-user-alert-change` | `always` | `./scripts/assistant.sh portfolio alerts add --isin "<ISIN>" --direction "<above\|below>" --threshold "<Kurs>" --currency "<ISO>" --yes` | `docs/PORTFOLIO_ADVISOR.md` | `tests/test_portfolio_tool.py` |
| `portfolio.alerts.disable` | `local-write` | nein | `explicit-user-alert-change` | `always` | `./scripts/assistant.sh portfolio alerts disable --id "<Regel-ID>" --yes` | `docs/PORTFOLIO_ADVISOR.md` | `tests/test_portfolio_tool.py` |
| `portfolio.performance` | `read` | nein | `none` | `always` | `./scripts/assistant.sh portfolio performance` | `docs/PORTFOLIO_ADVISOR.md` | `tests/test_portfolio_tool.py` |
| `portfolio.job.on` | `local-write` | nein | `explicit-user-start` | `always` | `./scripts/assistant.sh jobs on portfolio` | `docs/PORTFOLIO_ADVISOR.md` | `tests/test_portfolio_tool.py` |
| `portfolio.job.restart` | `local-write` | nein | `explicit-user-restart` | `always` | `./scripts/assistant.sh jobs restart portfolio` | `docs/PORTFOLIO_ADVISOR.md` | `tests/test_portfolio_tool.py` |
| `portfolio.job.off` | `local-write` | nein | `explicit-user-stop` | `always` | `./scripts/assistant.sh jobs off portfolio` | `docs/PORTFOLIO_ADVISOR.md` | `tests/test_portfolio_tool.py` |
## security

| Tool-ID | Modus | externe Wirkung | Approval | Verfuegbarkeit | Kommando | Doku | Test |
|---|---|---:|---|---|---|---|---|
| `security.antivirus.doctor` | `read` | nein | `none` | `always` | `./scripts/assistant.sh security antivirus doctor` | `AGENTS.md#host-antivirus-and-attachment-gate` | `tests/test_antivirus_tool.py` |
| `security.antivirus.self-test` | `read` | nein | `none` | `always` | `./scripts/assistant.sh security antivirus self-test` | `AGENTS.md#host-antivirus-and-attachment-gate` | `tests/test_antivirus_tool.py` |
| `security.antivirus.scan` | `read` | nein | `host-antivirus-read-only` | `always` | `./scripts/assistant.sh security antivirus scan --file "personal_assistant/data/workspace_outbox/<Datei>"` | `AGENTS.md#host-antivirus-and-attachment-gate` | `tests/test_antivirus_tool.py` |
## nextcloud

| Tool-ID | Modus | externe Wirkung | Approval | Verfuegbarkeit | Kommando | Doku | Test |
|---|---|---:|---|---|---|---|---|
| `nextcloud.list` | `read` | nein | `none` | `always` | `./scripts/assistant.sh nextcloud list --path "Assistent"` | `docs/NEXTCLOUD.md` | `tests/test_nextcloud_workspace_tools.py` |
| `nextcloud.sync` | `read` | nein | `none` | `always` | `./scripts/assistant.sh nextcloud sync` | `docs/NEXTCLOUD.md` | `tests/test_nextcloud_workspace_tools.py` |
| `nextcloud.workspace.mkdir` | `write` | ja | `workspace-create` | `workspace-mkdir` | `./scripts/assistant.sh nextcloud mkdir --path "{workspace_root}/<Ordner>"` | `docs/NEXTCLOUD.md` | `tests/test_nextcloud_workspace_tools.py` |
| `nextcloud.workspace.write-text` | `write` | ja | `workspace-create-only` | `workspace-write-text` | `printf "%s" "<Inhalt>" \| ./scripts/assistant.sh nextcloud write-text --path "{workspace_root}/<Datei>.md"` | `docs/NEXTCLOUD.md` | `tests/test_nextcloud_workspace_tools.py` |
| `nextcloud.workspace.upload` | `write` | ja | `workspace-create-only` | `workspace-upload` | `./scripts/assistant.sh nextcloud upload --local "personal_assistant/data/workspace_outbox/<Datei>" --path "{workspace_root}/<Ziel>"` | `docs/NEXTCLOUD.md` | `tests/test_nextcloud_workspace_tools.py` |
| `nextcloud.workspace.move` | `write` | ja | `workspace-organize-no-overwrite` | `workspace-move` | `./scripts/assistant.sh nextcloud move --source "{workspace_root}/<Quelle>" --destination "{workspace_root}/<Ziel>"` | `docs/NEXTCLOUD.md` | `tests/test_nextcloud_workspace_tools.py` |
| `nextcloud.workspace.configure` | `local-write` | nein | `explicit-user-permission` | `workspace` | `./scripts/assistant.sh setup workspace --root "{workspace_root}" --approve-permissions` | `docs/NEXTCLOUD.md` | `tests/test_nextcloud_workspace_tools.py` |
## mail

| Tool-ID | Modus | externe Wirkung | Approval | Verfuegbarkeit | Kommando | Doku | Test |
|---|---|---:|---|---|---|---|---|
| `mail.status` | `read` | nein | `none` | `always` | `./scripts/assistant.sh mail status` | `skills/personal-assistant/SKILL.md` | `tests/test_agent_tool_architecture.py` |
| `mail.doctor` | `read` | nein | `none` | `always` | `./scripts/assistant.sh mail doctor` | `skills/personal-assistant/SKILL.md` | `tests/test_agent_tool_architecture.py` |
| `mail.review.status` | `read` | nein | `none` | `always` | `./scripts/assistant.sh mail review status --days 7` | `skills/personal-assistant/references/mail.md` | `tests/test_mail_review_m9.py` |
| `mail.review.list` | `read` | nein | `none` | `always` | `./scripts/assistant.sh mail review list --reason "<Grund>" --limit 50` | `skills/personal-assistant/references/mail.md` | `tests/test_mail_review_m9.py` |
| `mail.review.suggest` | `read` | nein | `none` | `mail-move` | `./scripts/assistant.sh mail review suggest --folder "<Ordner>" --message-id "<ID>" --expected-subject "<Betreff>"` | `skills/personal-assistant/references/mail.md` | `tests/test_mail_review_m9.py` |
| `mail.review.correct` | `write` | ja | `explicit-user-review-correction` | `mail-move` | `./scripts/assistant.sh mail review correct --source "Agent/Pruefen" --message-id "<ID>" --expected-subject "<Betreff>" --verdict "<relevant\|routine\|spam>" --yes` | `skills/personal-assistant/references/mail.md` | `tests/test_mail_review_m9.py` |
| `mail.folders.plan` | `read` | nein | `none` | `always` | `./scripts/assistant.sh mail folders plan` | `skills/personal-assistant/references/mail.md` | `tests/test_mail_review_m9.py` |
| `mail.folders.apply` | `write` | ja | `explicit-user-create-configured-mail-folders` | `always` | `./scripts/assistant.sh mail folders apply --yes` | `skills/personal-assistant/references/mail.md` | `tests/test_mail_review_m9.py` |
| `mail.folders.activate-relevant` | `write` | ja | `explicit-user-configure-and-create-relevant-folder` | `always` | `./scripts/assistant.sh mail folders activate-relevant --relevant "Agent/Relevant" --yes` | `skills/personal-assistant/references/mail.md` | `tests/test_mail_review_m9.py` |
| `mail.index.status` | `read` | nein | `none` | `always` | `./scripts/assistant.sh mail index status` | `skills/personal-assistant/references/mail.md` | `tests/test_mail_hybrid_search_m117.py` |
| `mail.index.doctor` | `read` | nein | `none` | `always` | `./scripts/assistant.sh mail index doctor` | `skills/personal-assistant/references/mail.md` | `tests/test_mail_hybrid_search_m117.py` |
| `mail.index.plan` | `read` | nein | `none` | `always` | `./scripts/assistant.sh mail index plan` | `skills/personal-assistant/references/mail.md` | `tests/test_mail_search_backfill_m112.py` |
| `mail.index.backfill` | `local-write` | nein | `explicit-user-local-mail-index-backfill` | `always` | `./scripts/assistant.sh mail index backfill --page-size 50 --max-pages 200 --max-messages 10000 --max-bytes 1000000000 --max-message-bytes 100000000 --max-runtime 3600 --request-interval 0.2 --yes` | `skills/personal-assistant/references/mail.md` | `tests/test_mail_search_backfill_m112.py` |
| `mail.index.reconcile` | `local-write` | nein | `explicit-user-local-mail-index-reconcile` | `always` | `./scripts/assistant.sh mail index reconcile --max-folders 500 --max-messages 100000 --max-bytes 2000000000 --max-message-bytes 100000000 --max-runtime 3600 --request-interval 0.2 --retention-generations 2 --yes` | `skills/personal-assistant/references/mail.md` | `tests/test_mail_search_reconcile_m113.py` |
| `mail.search.local` | `read` | nein | `none` | `always` | `./scripts/assistant.sh mail search-local --query "<Suchbegriff>" --limit 50` | `skills/personal-assistant/references/mail.md` | `tests/test_mail_search_lexical_m114.py` |
| `mail.learning.status` | `read` | nein | `none` | `always` | `./scripts/assistant.sh mail learning status` | `skills/personal-assistant/SKILL.md` | `tests/test_agent_tool_architecture.py` |
| `mail.learning.feedback` | `read` | nein | `none` | `always` | `./scripts/assistant.sh mail learning feedback --limit 50` | `skills/personal-assistant/SKILL.md` | `tests/test_agent_tool_architecture.py` |
| `mail.learning.not-spam` | `read` | nein | `none` | `always` | `./scripts/assistant.sh mail learning not-spam --limit 100` | `skills/personal-assistant/SKILL.md` | `tests/test_agent_tool_architecture.py` |
| `mail.learning.mixed-senders` | `read` | nein | `none` | `always` | `./scripts/assistant.sh mail learning mixed-senders --limit 100` | `skills/personal-assistant/SKILL.md` | `tests/test_agent_tool_architecture.py` |
| `mail.learning.conflicts` | `read` | nein | `none` | `always` | `./scripts/assistant.sh mail learning conflicts --limit 100` | `skills/personal-assistant/SKILL.md` | `tests/test_agent_tool_architecture.py` |
| `mail.learning.feedback-forget` | `local-write` | nein | `explicit-user-feedback-delete` | `always` | `./scripts/assistant.sh mail learning forget-feedback --id <ID> --yes` | `skills/personal-assistant/SKILL.md` | `tests/test_learning_patterns.py` |
| `mail.learning.evaluate` | `read` | nein | `none` | `always` | `./scripts/assistant.sh mail learning evaluate --limit 5000` | `skills/personal-assistant/SKILL.md` | `tests/test_agent_tool_architecture.py` |
| `mail.learning.dataset-export` | `local-write` | nein | `learning-dataset-local-only` | `always` | `./scripts/assistant.sh mail learning dataset-export --output "mail_agent/data/learning_dataset.json" --limit 5000` | `skills/personal-assistant/SKILL.md` | `tests/test_agent_tool_architecture.py` |
| `mail.learning.folder-list` | `read` | nein | `none` | `always` | `./scripts/assistant.sh mail learning folder-list` | `skills/personal-assistant/SKILL.md` | `tests/test_agent_tool_architecture.py` |
| `mail.learning.folder-create` | `write` | ja | `explicit-user-create-correction-folder` | `always` | `./scripts/assistant.sh mail learning folder-create --parent "<routine\|important\|spam\|not-spam>" --name "<Name>" --label "<Typ>" --yes` | `skills/personal-assistant/SKILL.md` | `tests/test_agent_tool_architecture.py` |
| `mail.learning.folder-disable` | `local-write` | nein | `explicit-user-disable-learning-folder` | `always` | `./scripts/assistant.sh mail learning folder-disable --folder "<Ordner>" --yes` | `skills/personal-assistant/SKILL.md` | `tests/test_agent_tool_architecture.py` |
| `mail.sources.configure` | `local-write` | nein | `safe-settings` | `always` | `./scripts/assistant.sh setup mail-sources --primary "INBOX" --quarantine-folder "Spamverdacht"` | `skills/personal-assistant/SKILL.md` | `tests/test_agent_tool_architecture.py` |
| `mail.dry-run` | `read` | nein | `none` | `always` | `./scripts/assistant.sh mail dry-run --limit 20` | `skills/personal-assistant/SKILL.md` | `tests/test_agent_tool_architecture.py` |
| `mail.run` | `write` | ja | `configured-policy` | `always` | `./scripts/assistant.sh mail run --limit 20` | `skills/personal-assistant/SKILL.md` | `tests/test_agent_tool_architecture.py` |
| `mail.spam-review` | `write` | ja | `quarantine-rescue-policy` | `always` | `./scripts/assistant.sh mail spam-review --limit 20` | `skills/personal-assistant/SKILL.md` | `tests/test_agent_tool_architecture.py` |
| `mail.move-status` | `read` | nein | `none` | `mail-move` | `./scripts/assistant.sh mail move-status` | `skills/personal-assistant/SKILL.md` | `tests/test_agent_tool_architecture.py` |
| `mail.list` | `read` | nein | `none` | `mail-move` | `./scripts/assistant.sh mail list --folder "<Ordner>" --limit 50` | `skills/personal-assistant/SKILL.md` | `tests/test_agent_tool_architecture.py` |
| `mail.search` | `read` | nein | `none` | `mail-move` | `./scripts/assistant.sh mail search --query "<Suchbegriff>" --limit 50` | `skills/personal-assistant/references/mail.md` | `tests/test_mail_hybrid_search_m117.py` |
| `mail.read` | `read` | nein | `none` | `mail-move` | `./scripts/assistant.sh mail read --folder "<Ordner>" --message-id "<ID>" --expected-subject "<Betreff>"` | `skills/personal-assistant/SKILL.md` | `tests/test_agent_tool_architecture.py` |
| `mail.reply-draft` | `local-write` | nein | `draft-only-no-send` | `mail-move` | `./scripts/assistant.sh mail reply-draft --folder "<Ordner>" --message-id "<ID>" --expected-subject "<Betreff>" --body "<Entwurf>"` | `skills/personal-assistant/SKILL.md` | `tests/test_agent_tool_architecture.py` |
| `mail.reply-send` | `write` | ja | `explicit-user-approved-presented-draft` | `mail-move` | `./scripts/assistant.sh mail reply-send --draft-id "<Entwurfs-ID>" --yes` | `skills/personal-assistant/SKILL.md` | `tests/test_agent_tool_architecture.py` |
| `mail.compose-draft` | `local-write` | nein | `draft-only-no-send` | `mail-move` | `./scripts/assistant.sh mail compose-draft --to "<Empfaenger>" --subject "<Betreff>" --body "<Entwurf>"` | `skills/personal-assistant/SKILL.md` | `tests/test_agent_tool_architecture.py` |
| `mail.compose-send` | `write` | ja | `explicit-user-approved-presented-draft` | `mail-move` | `./scripts/assistant.sh mail compose-send --draft-id "<Entwurfs-ID>" --yes` | `skills/personal-assistant/SKILL.md` | `tests/test_agent_tool_architecture.py` |
| `mail.move` | `write` | ja | `configured-mail-organize-single-message` | `mail-move` | `./scripts/assistant.sh mail move --source "<Quelle>" --destination "<Ziel>" --message-id "<ID>" --expected-subject "<Betreff>"` | `skills/personal-assistant/SKILL.md` | `tests/test_agent_tool_architecture.py` |
| `mail.calendar-command` | `write` | ja | `trusted-owner-command` | `calendar-mail` | `Subject: {calendar_subject_prefix} <Terminbeschreibung>` | `skills/personal-assistant/SKILL.md` | `tests/test_agent_tool_architecture.py` |
## contacts

| Tool-ID | Modus | externe Wirkung | Approval | Verfuegbarkeit | Kommando | Doku | Test |
|---|---|---:|---|---|---|---|---|
| `nextcloud.contacts.discover` | `read` | nein | `none` | `always` | `./scripts/assistant.sh contacts discover` | `docs/CARDDAV_CONTACTS.md` | `tests/test_carddav_contact_tools.py` |
| `nextcloud.contacts.configure` | `local-write` | nein | `explicit-user-addressbook-selection` | `always` | `./scripts/assistant.sh contacts configure --resource "<resource_id>" --allow-update --yes` | `docs/CARDDAV_CONTACTS.md` | `tests/test_carddav_contact_tools.py` |
| `nextcloud.contacts.status` | `read` | nein | `none` | `contacts` | `./scripts/assistant.sh contacts status` | `docs/CARDDAV_CONTACTS.md` | `tests/test_carddav_contact_tools.py` |
| `nextcloud.contacts.list` | `read` | nein | `none` | `contacts-list` | `./scripts/assistant.sh contacts list --limit 100` | `docs/CARDDAV_CONTACTS.md` | `tests/test_carddav_contact_tools.py` |
| `nextcloud.contacts.search` | `read` | nein | `none` | `contacts-list` | `./scripts/assistant.sh contacts search --query "<Suchbegriff>" --limit 50` | `docs/CARDDAV_CONTACTS.md` | `tests/test_carddav_contact_tools.py` |
| `nextcloud.contacts.update` | `write` | ja | `explicit-user-contact-update-etag-guarded` | `contacts-update` | `./scripts/assistant.sh contacts update --uid "<UID>" --expected-name "<aktueller Name>" --phone "<neue Telefonnummer>" --yes` | `docs/CARDDAV_CONTACTS.md` | `tests/test_carddav_contact_tools.py` |
| `nextcloud.contacts.create` | `write` | ja | `explicit-user-contact-create-only` | `contacts-create` | `./scripts/assistant.sh contacts create --name "<Name>" --email "<E-Mail>" --phone "<Telefon>" --organization "<Firma>" --yes` | `docs/CARDDAV_CONTACTS.md` | `tests/test_carddav_contact_tools.py` |
| `nextcloud.contacts.from-mail-preview` | `read` | nein | `none` | `contacts-create` | `./scripts/assistant.sh contacts from-mail --folder "<Ordner>" --message-id "<Mail-ID>" --expected-subject "<Betreff>" --dry-run` | `docs/CARDDAV_CONTACTS.md` | `tests/test_carddav_contact_tools.py` |
| `nextcloud.contacts.from-mail-create` | `write` | ja | `explicit-user-contact-from-mail-create-only` | `contacts-create` | `./scripts/assistant.sh contacts from-mail --folder "<Ordner>" --message-id "<Mail-ID>" --expected-subject "<Betreff>" --yes` | `docs/CARDDAV_CONTACTS.md` | `tests/test_carddav_contact_tools.py` |
## calendar

| Tool-ID | Modus | externe Wirkung | Approval | Verfuegbarkeit | Kommando | Doku | Test |
|---|---|---:|---|---|---|---|---|
| `nextcloud.calendar.discover` | `read` | nein | `none` | `always` | `./scripts/assistant.sh calendar discover` | `docs/DIRECT_CALENDAR.md` | `tests/test_direct_calendar_tool.py` |
| `nextcloud.calendar.configure` | `local-write` | nein | `explicit-user-calendar-selection` | `always` | `./scripts/assistant.sh calendar configure --resource "<resource_id>" --allow-update --yes` | `docs/DIRECT_CALENDAR.md` | `tests/test_direct_calendar_tool.py` |
| `nextcloud.calendar.status` | `read` | nein | `none` | `calendar` | `./scripts/assistant.sh calendar status` | `docs/DIRECT_CALENDAR.md` | `tests/test_direct_calendar_tool.py` |
| `nextcloud.calendar.list` | `read` | nein | `none` | `calendar-list` | `./scripts/assistant.sh calendar list --limit 100` | `docs/DIRECT_CALENDAR.md` | `tests/test_direct_calendar_tool.py` |
| `nextcloud.calendar.search` | `read` | nein | `none` | `calendar-list` | `./scripts/assistant.sh calendar search --query "<Suchbegriff>" --limit 50` | `docs/DIRECT_CALENDAR.md` | `tests/test_direct_calendar_tool.py` |
| `nextcloud.calendar.update` | `write` | ja | `explicit-user-calendar-update-etag-guarded` | `calendar-update` | `./scripts/assistant.sh calendar update --uid "<UID>" --expected-title "<aktueller Titel>" --start "<ISO-8601>" --yes` | `docs/DIRECT_CALENDAR.md` | `tests/test_direct_calendar_tool.py` |
| `nextcloud.calendar.create` | `write` | ja | `configured-calendar-create-only` | `calendar-create` | `./scripts/assistant.sh calendar create --title "<Titel>" --start "<ISO-8601>" --end "<ISO-8601>" --location "<Ort>" --description "<Beschreibung>"` | `docs/DIRECT_CALENDAR.md` | `tests/test_direct_calendar_tool.py` |
## tasks

| Tool-ID | Modus | externe Wirkung | Approval | Verfuegbarkeit | Kommando | Doku | Test |
|---|---|---:|---|---|---|---|---|
| `nextcloud.tasks.discover` | `read` | nein | `none` | `always` | `./scripts/assistant.sh tasks discover` | `docs/DIRECT_TASKS.md` | `tests/test_direct_tasks_tool.py` |
| `nextcloud.tasks.configure` | `local-write` | nein | `explicit-user-task-list-selection` | `always` | `./scripts/assistant.sh tasks configure --resource "<resource_id>" --allow-update --yes` | `docs/DIRECT_TASKS.md` | `tests/test_direct_tasks_tool.py` |
| `nextcloud.tasks.status` | `read` | nein | `none` | `tasks` | `./scripts/assistant.sh tasks status` | `docs/DIRECT_TASKS.md` | `tests/test_direct_tasks_tool.py` |
| `nextcloud.tasks.list` | `read` | nein | `none` | `tasks-list` | `./scripts/assistant.sh tasks list --include-completed --limit 100` | `docs/DIRECT_TASKS.md` | `tests/test_direct_tasks_tool.py` |
| `nextcloud.tasks.update` | `write` | ja | `explicit-user-task-update-etag-guarded` | `tasks-update` | `./scripts/assistant.sh tasks update --uid "<UID>" --expected-title "<aktueller Titel>" --status COMPLETED --yes` | `docs/DIRECT_TASKS.md` | `tests/test_direct_tasks_tool.py` |
| `nextcloud.tasks.create` | `write` | ja | `configured-tasks-create-only` | `tasks-create` | `./scripts/assistant.sh tasks create --title "<Titel>" --due "<YYYY-MM-DD oder ISO-8601>" --priority <0-9> --description "<Beschreibung>"` | `docs/DIRECT_TASKS.md` | `tests/test_direct_tasks_tool.py` |
## orders

| Tool-ID | Modus | externe Wirkung | Approval | Verfuegbarkeit | Kommando | Doku | Test |
|---|---|---:|---|---|---|---|---|
| `nextcloud.deck.orders.status` | `read` | nein | `none` | `orders` | `./scripts/assistant.sh orders status` | `docs/DECK_ORDERS.md` | `tests/test_order_deck_tool.py` |
| `nextcloud.deck.orders.list` | `read` | nein | `none` | `orders` | `./scripts/assistant.sh orders list --limit 100` | `docs/DECK_ORDERS.md` | `tests/test_order_deck_tool.py` |
| `nextcloud.deck.discover` | `read` | nein | `none` | `orders` | `./scripts/assistant.sh deck discover` | `docs/DECK_ORDERS.md` | `tests/test_order_deck_tool.py` |
| `mail.orders.import` | `read` | nein | `none` | `orders` | `./scripts/assistant.sh mail orders-import --limit 500 --dry-run` | `docs/DECK_ORDERS.md` | `tests/test_order_deck_tool.py` |
| `nextcloud.deck.orders.sync` | `write` | ja | `managed-order-cards-only` | `orders` | `./scripts/assistant.sh orders sync --limit 500` | `docs/DECK_ORDERS.md` | `tests/test_order_deck_tool.py` |
| `nextcloud.deck.orders.due-date-preview` | `read` | nein | `none` | `orders` | `./scripts/assistant.sh orders due-date-backfill --limit 500 --dry-run` | `docs/DECK_ORDERS.md` | `tests/test_order_deck_tool.py` |
| `nextcloud.deck.orders.due-date-backfill` | `write` | ja | `managed-order-cards-missing-due-only` | `orders` | `./scripts/assistant.sh orders due-date-backfill --limit 500 --yes` | `docs/DECK_ORDERS.md` | `tests/test_order_deck_tool.py` |
## invoices

| Tool-ID | Modus | externe Wirkung | Approval | Verfuegbarkeit | Kommando | Doku | Test |
|---|---|---:|---|---|---|---|---|
| `mail.invoice-archive` | `write` | ja | `configured-invoice-archive-and-managed-register-sync` | `invoices` | `./scripts/assistant.sh mail run --limit 20` | `docs/INVOICE_OCR_REGISTER.md` | `tests/test_invoice_effect_contract_m101.py` |
| `assistant.invoices.status` | `read` | nein | `none` | `invoices` | `./scripts/assistant.sh invoices status` | `docs/INVOICE_OCR_REGISTER.md` | `tests/test_invoice_ocr_register.py` |
| `assistant.invoices.audit` | `read` | nein | `none` | `invoices` | `./scripts/assistant.sh invoices audit` | `docs/INVOICE_OCR_REGISTER.md` | `tests/test_invoice_backlog_audit_m107.py` |
| `assistant.invoices.list` | `read` | nein | `none` | `invoices` | `./scripts/assistant.sh invoices list --year <YYYY> --limit 100` | `docs/INVOICE_OCR_REGISTER.md` | `tests/test_invoice_ocr_register.py` |
| `assistant.invoices.review` | `read` | nein | `none` | `invoices` | `./scripts/assistant.sh invoices review --limit 100` | `docs/INVOICE_OCR_REGISTER.md` | `tests/test_invoice_ocr_register.py` |
| `assistant.invoices.export` | `read` | nein | `none` | `invoices` | `./scripts/assistant.sh invoices export --year <YYYY> --dry-run` | `docs/INVOICE_OCR_REGISTER.md` | `tests/test_invoice_effect_contract_m101.py` |
| `assistant.invoices.export-nextcloud` | `write` | ja | `explicit-user-managed-register-replace` | `invoices` | `./scripts/assistant.sh invoices export --year <YYYY> --yes` | `docs/INVOICE_OCR_REGISTER.md` | `tests/test_invoice_effect_contract_m101.py` |
| `assistant.invoices.backfill-preview` | `read` | nein | `none` | `invoices` | `./scripts/assistant.sh invoices backfill --year <YYYY> --limit 500 --dry-run` | `docs/INVOICE_OCR_REGISTER.md` | `tests/test_invoice_effect_contract_m101.py` |
| `assistant.invoices.backfill` | `write` | ja | `explicit-user-backfill-and-managed-register-replace` | `invoices` | `./scripts/assistant.sh invoices backfill --year <YYYY> --limit 500 --yes` | `docs/INVOICE_OCR_REGISTER.md` | `tests/test_invoice_effect_contract_m101.py` |
| `assistant.invoices.reprocess-preview` | `read` | nein | `none` | `invoices` | `./scripts/assistant.sh invoices reprocess --status "<review\|unclassified>" --source-year <YYYY> --limit 100 --dry-run` | `docs/INVOICE_OCR_REGISTER.md` | `tests/test_invoice_reprocess_preview_m105.py` |
| `assistant.invoices.reprocess-apply` | `write` | ja | `explicit-user-single-invoice-reprocess` | `invoices` | `./scripts/assistant.sh invoices reprocess-apply --hash "<SHA256>" --expected-preview-sha256 "<Digest>" --yes` | `docs/INVOICE_OCR_REGISTER.md` | `tests/test_invoice_reprocess_apply_m106.py` |
| `assistant.invoices.correct` | `write` | ja | `explicit-user-correction-and-managed-register-replace` | `invoices` | `./scripts/assistant.sh invoices correct --hash <SHA256> --date <YYYY-MM-DD> --number "<Nr>" --supplier "<Steller>" --category "<Kategorie>" --gross "<Betrag>" --yes` | `docs/INVOICE_OCR_REGISTER.md` | `tests/test_invoice_effect_contract_m101.py` |
