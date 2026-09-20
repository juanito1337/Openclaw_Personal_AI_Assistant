# ADR-0047: Beleggebundene Fachqualitaet und geschlossene Portfoliozustaende

- Status: Accepted
- Datum: 2026-09-21

## Kontext

M10 besitzt bereits sichere Rechnungs-Preview-/Einzelapply-Pfade, bildet aber
die fuer Altbestandentscheidungen benoetigte Provenienz und eine moegliche
Pfadkorrektur nicht in einem gemeinsamen Belegvertrag ab. Der bisherige
Portfolio-Healthzustand vermischt ausserdem Konfiguration, Mapping, Frische,
Providerrecht und den bewusst ausgeschalteten Job.

## Entscheidung

Rechnungsqualitaet wird an Original-SHA-256, Scanneridentitaet,
Extraktor-/Regelversion, Feldprovenienz und getrennte Datumsrollen gebunden.
Pfadkorrekturen sind ausschliesslich digestgebundene Previewplaene. Ein
zukuenftiger Writer braucht Backup, externen Snapshot, unveraenderten ETag und
SHA sowie ein nicht vorhandenes, eindeutiges Ziel; Bulk und Overwrite sind
nicht Teil des Vertrags.

Portfolio-Diagnose verwendet die geschlossene Zustandsmenge `off`,
`configured-limited`, `healthy`, `stale`, `mapping-required` und
`provider-entitlement-denied`. Providerzugriff, Mapping, Frische und Jobintent
werden trotzdem separat ausgegeben. Ein gewuenschtes `off` ist kein Fehler.
401/402/403, Rate Limit und leere Antwort bleiben unterscheidbar; es entsteht
kein Fallback auf unbelegte Daten.

## Folgen

- Bestehende M10-Einzelapply- und Freigabegrenzen bleiben unveraendert.
- Der neue Migrationsplan autorisiert und implementiert keinen Remote-Write.
- Alt- und aktuelle Extraktion koennen auf demselben gelabelten Beleg
  reproduzierbar verglichen werden.
- Monitoring kann den gewuenschten OFF-Zustand von einer fachlichen Stoerung
  unterscheiden.
- Das deklarierte Anlageprofil bleibt append-only und wird nie aus
  Beobachtungen umgeschrieben.
