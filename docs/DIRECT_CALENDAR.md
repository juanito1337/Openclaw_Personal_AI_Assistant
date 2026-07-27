# Direktes Nextcloud-Kalenderwerkzeug

Der Personal Assistant kann Termine in genau einem bewusst ausgewaehlten Nextcloud-CalDAV-Kalender lesen, suchen, neu anlegen und – nach gesonderter Freigabe – kontrolliert bearbeiten.

## Discovery und Freigabe

```bash
./scripts/assistant.sh calendar discover
```

Discovery ist read-only. Es werden nur Sammlungen mit `VEVENT` ausgegeben, einschliesslich stabiler `resource_id` und der live erkannten Rechte `can_read`, `can_create` und `can_update`.

Kalender mit Lesen und Anlegen konfigurieren:

```bash
./scripts/assistant.sh calendar configure \
  --resource "<resource_id>" \
  --timezone "Europe/Berlin" \
  --yes
```

Bestehende Termine zusaetzlich bearbeitbar machen:

```bash
./scripts/assistant.sh calendar configure \
  --resource "<resource_id>" \
  --timezone "Europe/Berlin" \
  --allow-update \
  --yes
```

`--allow-update` wird nur akzeptiert, wenn die Live-Discovery Schreibzugriff auf bestehende CalDAV-Objekte meldet. Die Konfiguration selbst aendert keinen Termin.

## Lesen und suchen

```bash
./scripts/assistant.sh calendar status
./scripts/assistant.sh calendar list --limit 100
./scripts/assistant.sh calendar search --query "Werkstatt" --limit 50
```

List und Search liefern die exakte iCalendar-UID. Diese UID ist fuer jeden Schreibzugriff erforderlich; eine unscharfe Auswahl nur anhand des Titels ist nicht erlaubt.

## Termin anlegen

```bash
./scripts/assistant.sh calendar create \
  --title "Termin" \
  --start "2026-08-03T14:00:00+02:00" \
  --end "2026-08-03T15:00:00+02:00" \
  --location "Kiel"
```

Neue Objekte werden mit `If-None-Match: *` create-only angelegt.

## Termin bearbeiten

```bash
./scripts/assistant.sh calendar update \
  --uid "<UID aus list/search>" \
  --expected-title "Alter Titel" \
  --title "Neuer Titel" \
  --start "2026-08-03T15:00:00+02:00" \
  --duration-minutes 45 \
  --location "Hamburg" \
  --yes
```

Felder koennen einzeln geaendert werden. Nicht genannte Eigenschaften bleiben erhalten. Ort oder Beschreibung werden nur mit `--clear-location` beziehungsweise `--clear-description` bewusst entfernt.

Das Objekt wird unmittelbar vor der Aenderung anhand der exakten UID gelesen. Der PUT verwendet die dabei erhaltene ETag als `If-Match`. Wurde der Termin parallel geaendert, bricht der Agent ab und fordert eine neue Auswahl statt still zu ueberschreiben.

Wiederkehrende Termine sind standardmaessig gesperrt. Eine Serienaenderung erfordert den expliziten Schalter:

```bash
./scripts/assistant.sh calendar update \
  --uid "<UID>" \
  --title "Neuer Serientitel" \
  --allow-recurring-series \
  --yes
```

Dabei wird nur die Master-Komponente der Serie aktualisiert; Ausnahmen und unbekannte iCalendar-Eigenschaften bleiben erhalten.

## Sicherheitsgrenzen

- keine automatische Kalenderauswahl
- Update nur nach `--allow-update` bei der Konfiguration und `--yes` pro Aenderung
- exakte UID sowie optional `--expected-title` und `--expected-start` als Auswahlwache
- ETag/`If-Match` gegen paralleles Ueberschreiben
- Teilaktualisierung erhaelt Teilnehmer, Alarme, Zeitzonen, benutzerdefinierte Felder und nicht genannte Eigenschaften
- Wiederholungsserien nur mit gesonderter ausdruecklicher Freigabe
- jede Aenderung als auditierter ActionPlan mit Vorher-/Nachher-Ergebnis
- Terminloeschung, Kalenderfreigaben und Massenbearbeitung bleiben gesperrt
