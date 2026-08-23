# ADR 0035: Nativer read-only IMAP-Inventory-Connector

Status: angenommen für die M12-Entwicklung; produktiver Rollout offen

## Kontext

Himalaya bleibt der kontrollierte Aktionsconnector des Personal Assistants. Die
verwendete Himalaya-Schnittstelle liefert für den Vollkontoindex jedoch keine
belegten UID-/UIDVALIDITY-Snapshots und kann deshalb weder aktuelle vollständige
Coverage noch externe Moves sicher attestieren.

## Entscheidung

Ein interner Connector übernimmt ausschließlich Inventur, Backfill,
Reconciliation und Live-Locator-Prüfung. Sein Port bietet nur Capability,
vollständiges LIST, read-only Ordnersnapshot, UID-Suche, ausgewählten
`BODY.PEEK`-Abruf und Locator-Revalidierung. Ein allgemeines IMAP-Kommando oder
freier Querystring ist nicht Teil des Ports.

Der Connector:

- verwendet System-CA und zwingende TLS-Prüfung;
- liest das vorhandene Himalaya-Konto, akzeptiert aber als Authentisierungsquelle
  nur den festen rollenbezogenen Secret-Mount;
- führt keine Authentisierungskommandos aus;
- öffnet Ordner mit `EXAMINE`/read-only Semantik;
- blockiert IMAP-Schreibkommandos vor dem Netzwerk;
- läuft beim einzigen Mail-Datenowner unter demselben Prozesslock wie die
  Mailverarbeitung;
- schreibt nur Checkpoint und lokale Projektion, niemals das Postfach.

Ordneridentität wird getrennt von aktueller Coverage ausgewiesen:

- `server-stable`: Server-Mailbox-ID und vollständiger Snapshot belegt;
- `snapshot-stable`: vollständiges LIST, stabile UIDVALIDITY und vollständige
  UID-Menge belegen den aktuellen Zustand, aber keine Rename-Historie;
- `unknown`: Teilscan, Race oder fehlender Nachweis.

Ein Nulltreffer darf nur bei frischer, vollständiger, autoritativer Coverage für
alle Filter als `no-match` gelten. Sonst lautet das Suchurteil `inconclusive`.

## Folgen

Externe Moves können aus zwei vollständigen Snapshots abgeleitet werden. Ohne
serverseitige Objekt-ID kann für einen neuen Locator einmalig ein Raw-SHA-Nachweis
nötig sein; unveränderter Inhalt wird danach ohne Parser-, OCR-, ClamAV-, FTS-
oder Modellarbeit wiederverwendet. Ein unklarer Zustand erzeugt keine Tombstones.

Ein lokaler Rollback stellt keine entfernten IMAP-Objekte wieder her. Produktiver
Canary, Vollbackfill und Jobaktivierung bleiben gesondert freizugebende
Betriebsaktionen.
