# ADR-0042: Inkrementeller Sync und eine priorisierte Queue

Status: Accepted

Datum: 2026-09-20

## Kontext

Der Nextcloud-Sync verarbeitete Kontakte und Kalenderobjekte bei jedem Lauf
vollständig neu. Ein langer Sync hielt zugleich den einzigen Scheduler-Slot,
bis alle Quellen abgeschlossen waren. Unveränderte Daten erzeugten dadurch
unnötige Downloads, Parsing- und SQLite-Arbeit; zeitkritische Mailarbeit konnte
einen sicheren laufenden Sync weder unterbrechen noch zwischen Teilmengen
anlaufen.

## Entscheidung

Der Sync erhält eine lokale ETag-/Locator-Inventur und deterministische,
digestgebundene Batchcursor. Remote-Metadaten werden vor Payloads gelesen.
Nur neue oder belegbar geänderte Objekte erreichen Download, Parsing und
Projektionscommit. Entfernen wird ausschließlich aus einem vollständigen,
fehlerfreien Snapshot abgeleitet; ein eindeutiges gleiches ETag darf einen
Move ohne erneutes Parsing belegen.

Die vorhandene Schedulerqueue bleibt alleinige Arbitration. Fünf feste Klassen
ergänzen Basispriorität, Deadline, Fokus und Alterung. Der Sync gibt nach jedem
begrenzten Batch sein Lease frei und reiht die Fortsetzung als Kindlauf ein.
Laufende gesunde Arbeit bleibt nicht präemptiv und alle SQLite-/externen
Schreibphasen bleiben seriell.

## Konsequenzen

- No-op-Syncs schreiben die Wissensprojektion nicht erneut.
- Einzeländerungen verursachen höchstens den notwendigen Objektpfad.
- Delete, Move, Cursorreset, Crash und Providerfehler bleiben fail-closed.
- Mail kann zwischen Sync-Batches priorisiert werden, ohne einen Write zu
  unterbrechen.
- Backgroundjobs verhungern wegen der bestehenden Alterungs-/Starvationregel
  nicht dauerhaft.
- Der Inventarbestand ist abgeleitete lokale Zustandsmetadaten und darf nie als
  Remote-Schreibautorität oder Ersatzbackup verstanden werden.
