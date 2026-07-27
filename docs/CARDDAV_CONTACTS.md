# CardDAV-Kontaktwerkzeug (3.4.0-r26.2)

## Zweck

Der Personal Assistant kann ein ausgewaehltes Nextcloud-Adressbuch per CardDAV lesen, durchsuchen, neue Kontakte create-only anlegen und bestehende Kontakte nach ausdruecklichem Nutzerauftrag kontrolliert aktualisieren. Loeschen, automatisches Zusammenfuehren und Massenbearbeitung bleiben gesperrt.

## Discovery, Auswahl und Rechte

```bash
./scripts/assistant.sh contacts discover
./scripts/assistant.sh contacts configure --resource "<resource_id>" --allow-update --yes
./scripts/assistant.sh contacts status
```

Discovery ist read-only. `configure` prueft das ausgewaehlte Adressbuch live. `--allow-update` wird nur akzeptiert, wenn Lesen erlaubt ist und der CardDAV-Server `write` oder `write-content` fuer dieses Adressbuch meldet. Ohne `--allow-update` bleibt das Aktualisierungswerkzeug deaktiviert.

## Lesen und eindeutige Auswahl

```bash
./scripts/assistant.sh contacts list --limit 100
./scripts/assistant.sh contacts search --query "Muster GmbH" --limit 50
```

Die Ausgabe enthaelt strukturierte Felder und die stabile Kontakt-UID, aber keine rohe vCard. Ein Update muss genau diese UID verwenden. Eine unscharfe Auswahl nur anhand eines Namens ist fuer Schreibzugriffe nicht zulaessig.

## Bestehenden Kontakt aktualisieren

```bash
./scripts/assistant.sh contacts update \
  --uid "<UID aus search/list>" \
  --expected-name "Max Mustermann" \
  --phone "+49 123 456789" \
  --organization "Muster GmbH" \
  --yes
```

Aenderbare Felder:

- `--name`
- wiederholtes `--email` ersetzt alle vorhandenen E-Mail-Adressen
- `--clear-emails` entfernt alle E-Mail-Adressen
- wiederholtes `--phone` ersetzt alle vorhandenen Telefonnummern
- `--clear-phones` entfernt alle Telefonnummern
- `--organization` oder `--clear-organization`
- `--note` oder `--clear-note`

Nicht angegebene Felder bleiben unveraendert. Insbesondere UID, Postanschrift, Geburtstag, Foto und unbekannte Nextcloud-/vCard-Erweiterungen werden aus der bestehenden Karte uebernommen.

`--expected-name` und `--expected-email` sind zusaetzliche Schutzwachen. Stimmen sie nicht mehr mit dem aktuell gelesenen Kontakt ueberein, wird nicht geschrieben. Eine E-Mail-Adresse, die bereits einem anderen Kontakt gehoert, wird blockiert. Ein neuer Name, der bereits existiert, wird standardmaessig ebenfalls blockiert; `--allow-name-collision` darf nur nach bewusster Pruefung verwendet werden.

Unmittelbar vor dem PUT wird die aktuelle vCard samt ETag gelesen. Der Schreibzugriff verwendet `If-Match`. Hat Nextcloud die Karte zwischenzeitlich geaendert, antwortet der Server mit einem Konflikt und der Agent bricht ab. Er versucht nicht, die fremde Aenderung zu ueberschreiben.

Jede Aktualisierung laeuft als auditierter `contacts.update`-ActionPlan und benoetigt die ausdrueckliche Freigabe `--yes`.

## Neuen Kontakt create-only anlegen

```bash
./scripts/assistant.sh contacts create \
  --name "Max Mustermann" \
  --email "max@example.com" \
  --phone "+49 123 456789" \
  --organization "Muster GmbH" \
  --yes
```

Der CardDAV-PUT verwendet `If-None-Match: *`. Eine identische E-Mail-Adresse erzeugt keine zweite Karte. Ein gleicher Name mit anderer E-Mail wird standardmaessig blockiert und muss bewusst mit `--allow-name-collision` freigegeben werden.

## Kontakt aus einer Mail

Zuerst die Mail-ID eindeutig ermitteln:

```bash
./scripts/assistant.sh mail list --folder "INBOX" --limit 50
```

Dann Vorschau und erst danach produktive Anlage:

```bash
./scripts/assistant.sh contacts from-mail \
  --folder "INBOX" \
  --message-id "<Mail-ID>" \
  --expected-subject "<Betreff>" \
  --dry-run

./scripts/assistant.sh contacts from-mail \
  --folder "INBOX" \
  --message-id "<Mail-ID>" \
  --expected-subject "<Betreff>" \
  --yes
```

Die komplette `.eml` wird lokal virengeprueft. No-reply-, Mailer-Daemon- und andere automatische Absender werden nicht automatisch angelegt.

## Sicherheitsgrenzen

- kein Kontakt-Loeschen
- kein automatischer Merge
- keine Aktualisierung ohne exakte UID
- keine Aktualisierung ohne aktiviertes `allow_update` und ausdrueckliches `--yes`
- kein stilles Ueberschreiben bei ETag-Konflikt
- kein automatisches Leeren nicht genannter Felder
- keine automatische Anlage oder Pflege aller Mailabsender
- keine Ausgabe roher vCards oder Mailtexte im Kontaktbefehl
