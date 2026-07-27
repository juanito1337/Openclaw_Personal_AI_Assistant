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
