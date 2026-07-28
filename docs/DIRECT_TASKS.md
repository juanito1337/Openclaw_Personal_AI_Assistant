# Direkte Nextcloud-Aufgaben

Der Personal Assistant kann CalDAV-`VTODO`-Aufgaben in genau einer bewusst ausgewaehlten Nextcloud-Aufgabenliste lesen, neu anlegen und – nach gesonderter Freigabe – kontrolliert bearbeiten oder abschliessen.

## Discovery und Freigabe

```bash
./scripts/assistant.sh tasks discover
```

Discovery ist read-only und listet nur Sammlungen mit `VTODO`. Ausgegeben werden stabile `resource_id`, alle angebotenen Komponenten sowie `can_read`, `can_create` und `can_update`.

Lesen und Anlegen aktivieren:

```bash
./scripts/assistant.sh tasks configure \
  --resource "<resource_id>" \
  --timezone "Europe/Berlin" \
  --yes
```

Bestehende Aufgaben zusaetzlich bearbeitbar machen:

```bash
./scripts/assistant.sh tasks configure \
  --resource "<resource_id>" \
  --timezone "Europe/Berlin" \
  --allow-update \
  --yes
```

Nur lesend konfigurieren:

```bash
./scripts/assistant.sh tasks configure \
  --resource "<resource_id>" \
  --read-only \
  --yes
```

`--allow-update` benoetigt Leserechte und live bestaetigten Schreibzugriff auf bestehende CalDAV-Objekte. Es kann deshalb nicht mit `--create-only` kombiniert werden.

## Lesen und anlegen

```bash
./scripts/assistant.sh tasks status
./scripts/assistant.sh tasks list --limit 100
./scripts/assistant.sh tasks list --include-completed --limit 100
./scripts/assistant.sh tasks create \
  --title "Rechnung pruefen" \
  --due "2026-08-05" \
  --priority 3 \
  --category "Finanzen"
```

Neue Aufgaben werden mit `If-None-Match: *` create-only angelegt. Die Liste liefert die exakte VTODO-UID fuer spaetere Aenderungen.

## Aufgabe bearbeiten

```bash
./scripts/assistant.sh tasks update \
  --uid "<UID aus tasks list>" \
  --expected-title "Rechnung pruefen" \
  --due "2026-08-07" \
  --priority 2 \
  --description "Mit Einkauf abstimmen" \
  --yes
```

Moegliche Teilaktualisierungen umfassen Titel, Start, Faelligkeit, Beschreibung, Prioritaet, Kategorien, Status und Prozentfortschritt. Nicht genannte Felder bleiben erhalten. Bewusstes Leeren erfolgt nur ueber `--clear-due`, `--clear-start`, `--clear-description` oder `--clear-categories`.

Aufgabe abschliessen:

```bash
./scripts/assistant.sh tasks update \
  --uid "<UID>" \
  --expected-title "Rechnung pruefen" \
  --status COMPLETED \
  --yes
```

Dabei werden `STATUS:COMPLETED`, `PERCENT-COMPLETE:100` und ein Abschlusszeitpunkt gesetzt. Eine Aufgabe kann mit `--status NEEDS-ACTION` bewusst wieder geoeffnet werden.

Der Agent liest das Objekt unmittelbar vor dem PUT anhand der exakten UID und schreibt mit `If-Match` gegen die aktuelle ETag. Bei einer parallelen Aenderung wird abgebrochen. Wiederkehrende VTODOs benoetigen zusaetzlich `--allow-recurring`.

## Sicherheitsgrenzen

- keine automatische Aufgabenlistenauswahl
- Update nur nach `--allow-update` bei der Konfiguration und `--yes` pro Aenderung
- exakte UID sowie optional `--expected-title` und `--expected-due`
- ETag/`If-Match` verhindert stilles Ueberschreiben
- unbekannte Eigenschaften, Alarme und nicht genannte Felder bleiben erhalten
- wiederkehrende Aufgaben nur mit ausdruecklicher Serienfreigabe
- jeder Schreibzugriff laeuft ueber Policy, ActionPlan und Audit
- Loeschen, Massenbearbeitung und Verschieben zwischen Aufgabenlisten bleiben gesperrt
