# Kontrolliertes Mail-Verschieben

Einrichtung: `./scripts/assistant.sh setup mail-move --approve-permissions`.

Lesen: `./scripts/assistant.sh mail list --folder "Archiv" --limit 50`.

Ordneruebergreifend suchen:
`./scripts/assistant.sh mail search --query "Jörn Arp" --limit 50`.
Die Suche laeuft serverseitig ueber alle lesbaren IMAP-Ordner und beruecksichtigt
laut Providerquery Absender, Betreff und Textinhalt. Himalaya 1.2 liefert dafuer
jedoch keinen autoritativen Vollstaendigkeitsnachweis. Bei einem leeren
Providerergebnis prueft der Dienst deshalb zusaetzlich bounded aktuelle
Envelope-Metadaten aller lesbaren Ordner auf Absendername, Adresse/Domain und
Betreff. Dadurch werden auch extern nach `Agent/Weitergeleitet` oder in einen
anderen Ordner verschobene positive Treffer gefunden.

Der Metadatenfallback liest keinen Body und kann wegen seines Ordnerlimits keine
Abwesenheit beweisen. `complete`, `search_scope`, `metadata_fallback`,
`filter_limitations`, `folder_errors` und `results_may_be_truncated` muessen
gemeinsam ausgewertet werden. Insbesondere bleibt ein Nulltreffer mit
`server-query-not-authoritative` oder `body-search-not-verified` unvollstaendig.

Dry-Run: `./scripts/assistant.sh mail move --source "Archiv" --destination "INBOX" --message-id "123" --expected-subject "Betreff" --dry-run`.

Produktiv denselben Befehl ohne `--dry-run`. Nur vorhandene Ordner und einzelne eindeutige Mail-IDs sind erlaubt. Loeschen, EXPUNGE, Papierkorb-, Spam-/Junk- und Virusverdacht-Ziele sowie Ordneraenderungen sind verboten.
