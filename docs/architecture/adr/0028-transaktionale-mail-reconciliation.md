# ADR-0028: Transaktionale Mail-Reconciliation

- Status: Accepted
- Datum: 2026-08-20
- Entscheider: Data Maintainers, Security Maintainers
- Bezug: M11.3, ADR-0017, ADR-0026, ADR-0027

## Kontext

Nach einem autoritativen Vollkontoaufbau muessen externe Aenderungen durch
Webmail, Mailclients, Spamfilter und serverseitige Regeln in den lokalen
Suchindex gelangen. Ein erneutes Parsen, Scannen und Indizieren aller Inhalte
bei jedem Locatorwechsel waere teuer. Teilscans duerfen umgekehrt niemals eine
fehlende Mail behaupten oder den letzten vollstaendigen Stand ueberschreiben.

Die drei Identitaetsebenen aus ADR-0026 sind deshalb auch Laufzeitgrenzen:
Content traegt Parsertext und abgeleitete Suchdaten, Occurrence bezeichnet eine
physische Auspraegung und Locator bildet den aktuellen IMAP-Fundort ab. Nur ein
Connector mit stabiler Ordner-ID, UID, UIDVALIDITY, Paging und Raw-Fetch kann
einen autoritativen Reconciliation-Lauf belegen.

## Entscheidung

`mail index reconcile ... --yes` ist ein registriertes lokales Write-Werkzeug
mit der Freigabe `explicit-user-local-mail-index-reconcile`. Es haelt denselben
Mail-Prozesslock wie der Mailwriter, schreibt nur Mail-Owner-Projektion und
Checkpoint und fuehrt keine IMAP-Aktion aus. Feste Ordner-, Nachrichten-, Byte-,
Einzelmail-, Laufzeit-, Request- und Retentiongrenzen sind Teil des Befehls.

Jeder Lauf inventarisiert und scannt alle freigegebenen Ordner autoritativ. Erst
wenn alle Ordner vollstaendig sind, werden eine neue Root-Generation,
Locatorwechsel und Tombstones publiziert. Teilscan, Netzverlust, Limit,
Scannerblock oder Crash vor dem Root-Replace erhalten die vorherige Root und den
vorherigen Cursor. Ein Crash nach gueltigem Root-Replace, aber vor Cursorwrite
ist durch deterministische Wiederholung sicher.

Ein belegter Move oder Ordnerrename veraendert nur aktuelle und historische
Locator. Content- und Occurrence-ID, Parsertext, Chunks, FTS und vorbereitete
Embeddings bleiben erhalten. Eine Copy erhaelt eine weitere Occurrence; das
Verschwinden eines Locator tombstoned nur diese Occurrence. Erst ohne weitere
Occurrence verschwindet das Content-Dokument aus der aktiven Projektion. Ein
UIDVALIDITY-Reset erzeugt neue Occurrence-/Locator-Identitaet, kann aber belegten
identischen Content wiederverwenden.

Bei mehrdeutiger Identitaet darf der Reconciler genau die betroffene Raw-Mail
abrufen und per SHA-256 vergleichen. Ein unveraenderter Digest wird weder erneut
geparst noch in FTS oder Embeddings neu berechnet. ClamAV wird nur bei neuem oder
geaendertem Content sowie bei geaenderter Scanneridentitaet erneut ausgefuehrt;
Fund oder Scannerfehler blockiert fail-closed.

Der Sync-Worker validiert die vollstaendige Root-Generation und wendet Contents,
Occurrences, Locator, Dokumente, Chunks, FTS und Sync-Cursor in genau einer
SQLite-Transaktion an. Reine Locatoraenderungen schreiben keine FTS-Zeile neu.
Eine fehlgeschlagene Transaktion rollt vollstaendig zurueck und bewahrt den alten
Cursor. Retention schuetzt immer die aktive und mindestens die letzte
Rollbackgeneration und entfernt nur erzeugte unveraenderliche JSON-Artefakte.

Der Scheduler kennt eine begrenzte Allowlist-Policy `mail-index`, aber M11.3
nimmt sie absichtlich nicht in die aktivierbaren JobSpecs oder das Deployment
auf. Aktivierung, produktiver Erstlauf und Connector-Rollout bleiben separate
Nutzerauftraege.

## Konsequenzen

- No-op und reine Moves verursachen keine Body-, Parser-, OCR-, Modell- oder
  FTS-Arbeit.
- Telemetrie besteht nur aus technischen Zaehlern wie gesehen, neu, geaendert,
  verschoben, kopiert, entfernt, unveraendert, blockiert und fehlgeschlagen.
- Provider-Quarantaene bleibt `quarantine-untrusted`; Reconciliation rettet,
  verschiebt oder markiert dort keine Nachricht.
- Der aktuelle Himalaya-1.2-Pfad belegt UID/UIDVALIDITY und stabile Ordner-IDs
  nicht. Der produktive Reconcile-Befehl verweigert mit
  `authoritative-connector-required` die Publikation, statt Cursorsemantik zu
  erfinden. Ein geeigneter Connector und seine produktive Aktivierung bleiben
  offen.
- M11.3 aendert keine Suchquery, kein Ranking, keine Tags und keine semantische
  Suche; diese Arbeiten beginnen fruehestens mit M11.4.

## Verifikation

`tests/test_mail_search_reconcile_m113.py` verwendet synthetische EMLs,
Fake-Connector, Fake-ClamAV und temporaere Projektionen/SQLite-Datenbanken. Es
deckt No-op, Neu, Copy, Move, Copy/Delete, Delete, Wiederkehr, Ordnerrename,
UIDVALIDITY-Reset, Quarantaene, Mehrdeutigkeit, Scanneridentitaet, Teilscan,
Netzverlust, beide Commitgrenzen, transaktionalen FTS-Import, Retention,
Capability-Fallback, Toolfreigabe und inaktive Jobvorbereitung ab.
