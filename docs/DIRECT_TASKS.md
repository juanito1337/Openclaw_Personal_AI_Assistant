# Direkte Nextcloud-Aufgaben

Der Personal Assistant kann CalDAV-`VTODO`-Aufgaben in freigegebenen
Nextcloud-Aufgabenlisten lesen, neu anlegen und nach gesonderter Freigabe
kontrolliert bearbeiten oder abschliessen. Jede Aenderung betrifft genau eine
Aufgabe in genau einer Liste.

## Kontoverbindung ueber OpenClaw

Die [Nextcloud-Einrichtung](NEXTCLOUD_SETUP_UI.md) erkennt die Aufgabenlisten des
angemeldeten Kontos und deren Serverrechte. Ohne `--resource` zeigen `tasks status`
und `tasks list` alle freigegebenen Listen mit Herkunfts-ID und sichtbaren
Teilabdeckungen. Eine exakte Ressourcen-ID begrenzt die Abfrage auf eine Liste.

Bei mehreren Listen ist `--resource "<resource_id>"` fuer `tasks create` und
`tasks update` erforderlich. Zum Abschliessen zuerst aktuelle Liste und UID
auswaehlen, dann genau diese Liste und Aufgabe aendern:

```bash
./scripts/assistant.sh tasks status
./scripts/assistant.sh tasks list --resource "<resource_id>" --include-completed --limit 100
./scripts/assistant.sh tasks update --resource "<resource_id>" --uid "<UID>" --expected-title "Rechnung pruefen" --status COMPLETED --yes
```

Die native Einzelfreigabe und der Remote-Readback bleiben erforderlich. Ein nur
lesbares Ziel wird durch die Kontofreigabe nicht beschreibbar. Die folgenden
`configure`-Beispiele gelten fuer die Legacy-Einzelressource; GUI-verwaltete
Kontoverbindungen werden in OpenClaw aktualisiert und umgehen weder den
schreibgeschuetzten Gateway-Mount noch Serverrechte.

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

Im Container ist diese Berechtigungsaenderung eine getrennte Administratoraktion.
Der dauerhaft laufende Gateway mountet die Instanzkonfiguration absichtlich
read-only; dort darf `tasks configure` nicht ausgefuehrt und der Mount nicht
aufgeweicht werden. Nach separater ausdruecklicher Freigabe wird der oben gezeigte
registrierte Befehl einmalig ueber die kurzlebige Compose-Rolle `agent-cli`
ausgefuehrt. `tasks status` liefert dafuer bei deaktiviertem Update den
strukturierten Block `update_setup`. Dessen primaerer Befehl aktiviert das
[Standard-Betriebsprofil](STANDARD_OPERATIONS.md) gemeinsam fuer alle bereits
konfigurierten und berechtigungsgeprueften Funktionen, statt weitere technische
Einzelfreigaben zu verlangen.

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

Ein fehlgeschlagenes Remote-Update darf nicht durch eine Notiz im Modellgedaechtnis
ersetzt werden. Erfolg ist erst belegt, wenn die Antwort `ok=true`,
`after.status=COMPLETED` und `after.percent_complete=100` enthaelt.

Der Agent liest das Objekt unmittelbar vor dem PUT anhand der exakten UID und schreibt mit `If-Match` gegen die aktuelle ETag. Bei einer parallelen Aenderung wird abgebrochen. Wiederkehrende VTODOs benoetigen zusaetzlich `--allow-recurring`.

## Sicherheitsgrenzen

- keine automatische Aufgabenlistenauswahl
- Update nur mit bestaetigtem Serverrecht und konkreter Freigabe; beim Legacy-Konfigurationspfad zusaetzlich `--allow-update`, pro CLI-Aenderung `--yes`
- exakte UID sowie optional `--expected-title` und `--expected-due`
- ETag/`If-Match` verhindert stilles Ueberschreiben
- unbekannte Eigenschaften, Alarme und nicht genannte Felder bleiben erhalten
- wiederkehrende Aufgaben nur mit ausdruecklicher Serienfreigabe
- jeder Schreibzugriff laeuft ueber Policy, ActionPlan und Audit
- Loeschen, Massenbearbeitung und Verschieben zwischen Aufgabenlisten bleiben gesperrt
