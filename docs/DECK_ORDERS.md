# Nextcloud Deck Bestellmonitor

Version 3.4.0-r14 ergaenzt ein kontrolliertes Bestellwerkzeug fuer Nextcloud Deck.

## Architektur

- Sichtbare Oberflaeche: Nextcloud Deck Board `Bestellungen`
- Lokale Wahrheitsschicht: `personal_assistant/data/orders.sqlite3`
- Automatische Quelle: bereits virengepruefte und klassifizierte E-Mails
- Schreibzugriff: nur das konfigurierte Board und nur agentenverwaltete Karten
- Keine Loeschungen, keine Board-Freigaben, keine Aenderung manueller Karten

## Spalten

1. Bestellt
2. Auftragsbestaetigung
3. Versandvorbereitung
4. Versendet
5. In Zustellung
6. Zugestellt
7. Retoure
8. Erstattet / Abgeschlossen
9. Pruefen

## Einrichtung

Nextcloud Deck aktivieren. Danach:

```bash
./scripts/assistant.sh deck discover
./scripts/assistant.sh setup deck-orders --board-title "Bestellungen" --create-board --approve-permissions
```

Die interaktive Bestaetigung `APPROVE` ist erforderlich. Ein vorhandenes Board kann mit `--board-id` verwendet werden.

## Befehle

```bash
./scripts/assistant.sh orders status
./scripts/assistant.sh orders list --limit 100
./scripts/assistant.sh orders list --status shipped
./scripts/assistant.sh orders sync --limit 500
./scripts/assistant.sh orders due-date-backfill --limit 500 --dry-run
./scripts/assistant.sh mail orders-import --limit 500 --dry-run
```

Nach einem geprueften Dry-Run kann der historische Import ohne `--dry-run` wiederholt werden.

## Faelligkeitsdatum

Jede neu aus einer Mail erzeugte agentenverwaltete Karte erhaelt ein `dueDate`.
Die Auswahl erfolgt in dieser Reihenfolge:

1. Retourenfrist bei einer aktiven Retoure
2. erwartete Lieferung oder Zustellung
3. Bestelldatum
4. serverseitiges Eingangsdatum der letzten beziehungsweise ersten Quellmail
5. klar gekennzeichnetes lokales Verarbeitungsdatum als letzter Fallback

Quelle und Konfidenz werden lokal gespeichert und im verwalteten Kartenbereich angezeigt. Ein bereits vorhandenes plausibles Datum wird nicht durch spaetere Ereignisse ueberschrieben.

Bestehende agentenverwaltete Karten ohne Datum werden zuerst read-only geprueft:

```bash
./scripts/assistant.sh orders due-date-backfill --limit 500 --dry-run
```

Nach ausdruecklicher Freigabe:

```bash
./scripts/assistant.sh orders due-date-backfill --limit 500 --yes
```

Manuelle oder nicht markierte Karten bleiben unveraendert.

## Automatik

Der Mailklassifizierer liefert ein strukturiertes OrderSignal. Automatisch verarbeitet werden nur echte Bestellereignisse in legitimen Routine- oder relevanten Mails und nur oberhalb des konfigurierten Konfidenzschwellwerts. Ohne Bestellnummer oder Trackingnummer wird ein unsicherer Datensatz in `Pruefen` angelegt statt eine bestehende Bestellung blind zu veraendern.

## Deck-Sicherheit

Jede Agentenkarte besitzt einen unsichtbaren Managed-Marker. Updates und Verschiebungen sind nur erlaubt, wenn Karte, lokale Datenbank und Marker zusammenpassen. Fremde oder manuell erstellte Karten bleiben unveraendert. Schlaegt Deck fehl, bleibt das Ereignis lokal mit `sync_status=error` gespeichert und kann mit `orders sync` erneut synchronisiert werden.

## Grenzen der ersten Version

- Status basiert auf E-Mails, nicht auf Live-Carrier-APIs.
- Ohne Versand- oder Zustellmail wird kein Status erfunden.
- Teillieferungen werden als Trackingliste innerhalb einer Bestellung zusammengefasst.
- Rechnungen bleiben im bestehenden Nextcloud-Rechnungsarchiv; Deck fuehrt nur Bestellstatus und Metadaten.
