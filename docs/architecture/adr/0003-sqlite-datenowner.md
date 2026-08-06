# ADR-0003: SQLite-Grenzen folgen fachlichen Datenownern

- Status: Accepted
- Datum: 2026-08-05
- Entscheider: Data Maintainers
- Betroffene Milestones: M1-M8

## Kontext

OpenClaw nutzt mehrere SQLite-Dateien fuer Mail, Core/Audit, Antivirus, Orders,
Portfolio, Monitoring und Scheduler. Eine gemeinsame Universaldatenbank wuerde
Fehler-, Backup- und Migrationsgrenzen koppeln.

## Entscheidung

Jede SQLite-Datei besitzt genau einen im Datenkatalog benannten fachlichen Owner.
Writer und Leser werden explizit dokumentiert. Fachfremde Leser verwenden, wo
moeglich, read-only Verbindungen. Schemaaenderungen sind vorwaertsgerichtete,
idempotente Migrationen; produktive Datenbanken werden nie als Reparatur geloescht.

## Konsequenzen

Subsysteme koennen getrennt migriert und diagnostiziert werden. Layout 3 setzt diese
Grenzen als rollenbezogene Mounts um; nur Core, Security und Koordination werden
fuer dokumentierte Aufrufer geteilt.

## Verifikation

Datenkatalogtest, SQLite-Quick-Checks in Backup/Restore und subsystembezogene
Migrationstests.

## Offene Fragen

Keine fuer M3. Die Schedulerentscheidung ist in ADR-0007 festgehalten.
