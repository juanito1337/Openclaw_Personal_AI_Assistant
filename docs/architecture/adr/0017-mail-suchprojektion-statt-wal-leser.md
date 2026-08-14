# ADR-0017: Atomare Mail-Suchprojektion statt SQLite/WAL-Leser

- Status: Accepted
- Datum: 2026-08-14
- Entscheider: Architecture Maintainers, Mail Maintainers, Data Maintainers
- Betroffene Bereiche: Mail, Wissensindex, Sync-Worker, Containerrollen

## Kontext

Der Wissensindex benoetigt eine begrenzte, durchsuchbare Sicht auf bereits vom
Mailworker verarbeitete Nachrichten. Ein direkter read-only-Zugriff des
Sync-Workers auf `mail_agent.sqlite3` ist unter gleichzeitigem WAL-Writer keine
belastbare Containergrenze: SQLite kann Lock- und Shared-Memory-Nebenfiles
benoetigen, waehrend der read-only Bind-Mount genau diese Schreibmoeglichkeit
absichtlich verweigert. Mail-Schreibrechte fuer den Sync-Worker wuerden den
fachlichen Datenowner und die M3/M4-Single-Writer-Grenze verletzen.

Einzelne lose JSON-Snapshots bilden ebenfalls keine vollstaendige Generation ab.
Nach einem Crash koennte ein Leser neue, alte und teilweise geschriebene Dateien
vermischen oder eine veraltete Quelle faelschlich als aktuell behandeln.

## Entscheidung

Der Mailworker bleibt alleiniger Owner der Mail-SQLite und erzeugt fuer die Suche
unveraenderliche JSON-Datensaetze. Ein atomar ersetztes
`search_documents/_projection.json` referenziert genau eine vollstaendige
Generation. Jeder Eintrag bindet einen eindeutigen Stable-Key, Dateinamen,
Quellzeitpunkt und SHA-256-Pruefsumme; die sortierte Eintragsmenge bestimmt eine
reproduzierbare Quellgenerations-ID.

Der Sync-Worker liest nur diese Projektion ueber seinen bestehenden read-only
Mail-Mount. Er prueft Manifest, Schema, sichere Dateinamen, Eindeutigkeit,
Vollstaendigkeit, Pruefsummen, Quellgeneration und konfiguriertes Hoechstalter,
bevor der erste Datensatz in `knowledge.sqlite3` geschrieben wird. Er oeffnet
`mail_agent.sqlite3`, `-wal` und `-shm` nicht. Die letzte vollstaendig verarbeitete
Generation und ihr Alter werden im Wissens-Sync-Status festgehalten.

Datensatz und Manifest werden jeweils per atomarem Replace publiziert. Ein Crash
vor dem Manifest-Replace laesst die vorherige vollstaendige Generation gueltig;
nicht referenzierte unveraenderliche Dateien sind keine veroeffentlichten Daten.
Der Mailowner aktualisiert den Manifestzeitpunkt auch in einem erfolgreichen Lauf
ohne neue Mail. Fehlende, veraltete, teilweise oder korrupte Projektionen brechen
vor einem Wissensindexwrite fail-closed ab.

## Konsequenzen

Die Container-Mounts und Rollenrechte bleiben unveraendert: Mail ist fuer Sync
`ro`, Wissen fuer Sync `rw`. Der Sync-Worker wird weder zweiter Mail-Datenowner
noch SQLite/WAL-Leser. Quell- und Zielzustand besitzen getrennte Transaktionen;
die Quellgeneration im Sync-Status macht sichtbar, welcher vollstaendige Stand
zuletzt verarbeitet wurde.

Unreferenzierte alte Datensaetze koennen bis zu einer spaeteren, eigens
getesteten Retention bestehen bleiben. Sie werden nie indexiert, weil nur das
Manifest autoritativ ist. Die Projektion enthaelt weiterhin Maildaten und bleibt
deshalb im geschuetzten Mail-State; sie ist weder Buildartefakt noch Telemetrie.

## Verifikation

Hermetische Tests halten die Mail-SQLite mit WAL und `BEGIN IMMEDIATE` offen und
belegen, dass die Sync-Rolle sie nicht aufruft. Weitere Regressionen pruefen
read-only Quelle, gleichbleibende Generation bei Aktualisierung, Crash vor
Manifestpublikation, veraltete Manifestzeit, korrupte referenzierte Datensaetze
und die Meldung der letzten vollstaendigen Generation. Bestehende M3/M4-Tests
pruefen Mountrechte und Single-Writer-Grenzen weiterhin gegen Compose.
