# Kontrolliertes Mail-Verschieben

Einrichtung: `./scripts/assistant.sh setup mail-move --approve-permissions`.

Lesen: `./scripts/assistant.sh mail list --folder "Archiv" --limit 50`.

Ordneruebergreifend suchen:
`./scripts/assistant.sh mail search --query "Jörn Arp" --limit 50`.
Die Suche laeuft serverseitig ueber alle lesbaren IMAP-Ordner und beruecksichtigt
Absender, Betreff und Textinhalt. Das Ergebnislimit wird erst auf die Treffer
angewendet, nicht auf die zuletzt gelisteten Nachrichten.
Die Rueckgabe markiert mit `complete`, ob alle Ordner erfolgreich durchsucht
wurden. `folder_errors` nennt Teilausfaelle; `results_may_be_truncated` fordert
bei erreichtem Trefferlimit zu einer genaueren Suchanfrage auf.

Dry-Run: `./scripts/assistant.sh mail move --source "Archiv" --destination "INBOX" --message-id "123" --expected-subject "Betreff" --dry-run`.

Produktiv denselben Befehl ohne `--dry-run`. Nur vorhandene Ordner und einzelne eindeutige Mail-IDs sind erlaubt. Loeschen, EXPUNGE, Papierkorb-, Spam-/Junk- und Virusverdacht-Ziele sowie Ordneraenderungen sind verboten.
