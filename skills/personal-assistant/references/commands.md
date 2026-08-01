# Personal Assistant command reference

```bash
./scripts/assistant.sh doctor
./scripts/assistant.sh capabilities
./scripts/assistant.sh resources list
./scripts/assistant.sh nextcloud doctor
./scripts/assistant.sh nextcloud discover
./scripts/assistant.sh index mail
./scripts/assistant.sh index all
./scripts/assistant.sh search "Tankreinigung Wattenbek"
./scripts/assistant.sh search "Rechnung" --source-type nextcloud-file
./scripts/assistant.sh actions list
./scripts/assistant.sh settings list
```

Central setup:

```bash
./scripts/assistant.sh setup init
./scripts/assistant.sh setup nextcloud
```

Adaptive background scheduler:

```bash
./scripts/assistant.sh scheduler status
./scripts/assistant.sh scheduler doctor
./scripts/assistant.sh scheduler activity
./scripts/assistant.sh scheduler focus --topic portfolio --minutes 30
```

ActionPlan example:

```bash
./scripts/assistant.sh actions plan-upload ./rechnung.pdf Assistent/Rechnungen/2026/rechnung.pdf
./scripts/assistant.sh actions list --status approved
./scripts/assistant.sh actions execute <id>
```

Calendar and task actions require approval:

```bash
./scripts/assistant.sh actions plan-event event.ics --uid <uid> --resource <calendar-resource>
./scripts/assistant.sh actions approve <id>
./scripts/assistant.sh actions execute <id>
```

Controlled mail movement:

```bash
./scripts/assistant.sh setup mail-move --approve-permissions
./scripts/assistant.sh mail move-status
./scripts/assistant.sh mail list --folder "Archiv" --limit 50
./scripts/assistant.sh mail move --source "Archiv" --destination "INBOX" --message-id "<ID>" --expected-subject "<Betreff>" --dry-run
./scripts/assistant.sh mail move --source "Archiv" --destination "INBOX" --message-id "<ID>" --expected-subject "<Betreff>"
```

Portfolio monitor:

```bash
./scripts/assistant.sh portfolio status
./scripts/assistant.sh setup portfolio --provider eodhd --interval-minutes 15 --approve-permissions
./scripts/assistant.sh portfolio doctor
./scripts/assistant.sh portfolio import-pp --file "depot.xml" --dry-run
./scripts/assistant.sh portfolio import-pp --file "depot.xml" --yes
./scripts/assistant.sh portfolio import-csv --file "depot-export-DD.MM.YYYY.csv" --dry-run
./scripts/assistant.sh portfolio import-csv --file "depot-export-DD.MM.YYYY.csv" --yes
./scripts/assistant.sh portfolio import-csv --nextcloud-path "Assistent/Finanzen/Portfolio/depot-export-DD.MM.YYYY.csv" --dry-run
./scripts/assistant.sh portfolio import-csv --nextcloud-path "Assistent/Finanzen/Portfolio/depot-export-DD.MM.YYYY.csv" --yes
./scripts/assistant.sh portfolio holdings
./scripts/assistant.sh portfolio watchlist list
./scripts/assistant.sh portfolio watchlist add --isin "<ISIN>" --name "<Name>" --symbol "<Symbol>" --mic "<MIC>" --currency EUR --yes
./scripts/assistant.sh portfolio quotes status
./scripts/assistant.sh portfolio quotes refresh
./scripts/assistant.sh portfolio analyze --isin "<ISIN>"
./scripts/assistant.sh portfolio alerts list
./scripts/assistant.sh portfolio alerts add --isin "<ISIN>" --direction above --threshold "<Kurs>" --currency EUR --yes
./scripts/assistant.sh portfolio performance
./scripts/assistant.sh jobs on portfolio
```

No broker login or order operation is registered.
