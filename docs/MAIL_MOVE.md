# Kontrolliertes Mail-Verschieben

Einrichtung: `./scripts/assistant.sh setup mail-move --approve-permissions`.

Lesen: `./scripts/assistant.sh mail list --folder "Archiv" --limit 50`.

Dry-Run: `./scripts/assistant.sh mail move --source "Archiv" --destination "INBOX" --message-id "123" --expected-subject "Betreff" --dry-run`.

Produktiv denselben Befehl ohne `--dry-run`. Nur vorhandene Ordner und einzelne eindeutige Mail-IDs sind erlaubt. Loeschen, EXPUNGE, Papierkorb-, Spam-/Junk- und Virusverdacht-Ziele sowie Ordneraenderungen sind verboten.
