# ADR-0010: M6-Legacy-Isolation und direkte Upgrade-Untergrenze

- Status: Accepted
- Datum: 2026-08-06
- Entscheider: Architecture Maintainers, Operations Maintainers
- Betroffene Milestones: M6-M8

## Kontext

Das Repository enthielt neben dem Docker-Stack weiterhin direkt beworbene systemd-
Helfer, ein zweites Agenten-Skill, einen ungenutzten Nextcloud-Dateiclient und drei
nur von ihren eigenen Tests importierte Konfigurationsmigrationen. Gleichzeitig
muss der verifizierte Rueckweg in ein bereits vorhandenes Legacy-Home erhalten
bleiben.

## Entscheidung

Direkte Upgrades werden ab `3.4.0-r26.1` unterstuetzt. Ein versioniertes Fixture
belegt, dass dessen bereits migrierte Mailkonfiguration vom aktuellen Parser geladen
wird. Aeltere direkte Upgrades muessen zuerst mit dem historischen Release bis
r26.1 migriert werden; die isolierten R25-, R26- und R26.1-Einmalskripte gehoeren
nicht mehr zum aktuellen Produkt.

Native systemd-Dateien liegen ausschliesslich unter `legacy/systemd/`. Dieses
Paket ist als Kompatibilitaet eingefroren, besitzt ein eigenes SHA-256-Manifest und
ist weder aktiver Installer noch primaerer Jobpfad. Helfer und registrierter
Job-Controller verlangen fuer eine Aktivierung explizit
`OPENCLAW_ENABLE_LEGACY_SYSTEMD=YES`; das aktive Image enthaelt das Paket nicht.
Legacy-Rollback bleibt bis zu einer separaten M8-ADR mit erfolgreicher
Container-zu-Container-Recovery erhalten. Das im aktuellen Repository direkt
gepruefte Rollbackpaket beginnt bei `3.4.0-r27.2.5`. Ein aelteres bestehendes
Legacy-Home ist nur ueber seinen eigenen, im Releasebackup verifizierten Home-/
Archivnachweis unterstuetzt und wird nicht aus dem aktuellen Checkout rekonstruiert.

Nextcloud-Dateizugriffe und Rechnungsuploads verwenden ausschliesslich den zentralen
Connector hinter Policy, ActionPlan, Idempotenz und Audit. Der alte Mail-Dateiclient
und der doppelte Listen-Wrapper werden entfernt. Mail-Kontakt- und Kalendersignale
verwenden dieselben release-eigenen CalDAV/CardDAV-Bausteine; ein breiter,
workspace-lokaler Community-Skill ist kein Runtime-Bestandteil mehr.

## Konsequenzen

- Neue Deployments koennen nicht versehentlich die eingefrorenen Writer installieren.
- Ein bestehendes Legacy-Home bleibt als verifizierte Rueckfallquelle unangetastet.
- Unterstuetzte Upgrade- und Rollbackgrenzen sind maschinenlesbar.
- Datenbank-, State-Layout- und Container-Migrationen bleiben aktiv und duerfen
  nicht aufgrund ihres Namens entfernt werden.

## Verifikation

Komponenten-Inventar, Legacy-Paketmanifest, r26.1-Fixture, Negativtests fuer entfernte
Befehle und Module, vorhandene Datenbank-/Container-Migrationstests sowie die
vollstaendige Wheel- und Containerabnahme.
