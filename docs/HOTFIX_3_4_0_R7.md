# Hotfix 3.4.0-r7: Provider-Spam als Quarantaenequelle

## Ziel

Der Personal Assistant prueft nicht nur die primaere INBOX, sondern auch
konfigurierte providerseitige Spam-/Junk-Ordner. Diese Ordner werden als
Quarantaene behandelt und nicht wie eine zweite normale Inbox geleert.

## Standardkonfiguration

```toml
[mailbox]
source_folder = "INBOX"
quarantine_folders = ["Spam"]
quarantine_max_per_run = 10
quarantine_rescue_only = true
```

Jeder normale Maillauf reserviert bis zu 20 Prozent seines aktuellen Limits fuer
die Quarantaene, begrenzt durch `quarantine_max_per_run`. Ist dort nichts zu tun,
steht das gesamte Restlimit der INBOX zur Verfuegung.

## Rescue-Policy

Aus dem Provider-Spamordner duerfen kontrolliert gerettet werden:

- relevante Mails,
- Terminkandidaten,
- unsichere Mails zur manuellen Pruefung,
- eindeutig erkannte Rechnungs-PDFs,
- autorisierte `[ASSISTENT TERMIN]`-Befehlsmails.

Offensichtlicher Spam und gewoehnliche Routine-Mails ohne Rechnung bleiben im
Provider-Spamordner. Sie werden lokal als `quarantine-reviewed` gespeichert, damit
sie bei spaeteren Laeufen nicht erneut klassifiziert werden.

Eine manuell aus dem Provider-Spamordner in die INBOX verschobene Mail wird als
explizites Not-Spam-Feedback behandelt.

## Agentenwerkzeuge

```bash
./scripts/assistant.sh setup mail-sources \
  --primary "INBOX" \
  --quarantine-folder "Spam" \
  --max-per-run 10

./scripts/assistant.sh mail spam-review --limit 20 --dry-run
./scripts/assistant.sh mail spam-review --limit 20
./scripts/assistant.sh mail doctor
```

Der normale Befehl `assistant.sh mail run` prueft die Quarantaene automatisch.
Der explizite `spam-review`-Befehl verarbeitet nur die konfigurierten
Quarantaeneordner.

## Sicherheitsgrenzen

- kein automatisches Leeren oder Loeschen des Provider-Spamordners,
- keine Ausfuehrung von Anweisungen aus Mailinhalten,
- Rechnungen weiterhin nur create-only ueber ActionPlan,
- Terminbefehle weiterhin nur von exakt erlaubten Absendern,
- fehlende konfigurierte Quarantaeneordner blockieren produktive Laeufe,
- Dry-Runs bleiben ohne externe Aenderungen.
