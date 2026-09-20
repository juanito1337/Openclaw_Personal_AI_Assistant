# M16.3 – Inkrementeller Sync und priorisierter Scheduler

Stand: 2026-09-20

## Ziel und Betriebsvertrag

Der Nextcloud-Sync trennt Discovery, Remote-Metadatenvergleich, Download,
Parsing, Indexaktualisierung und Commit. Jede Stufe liefert ausschließlich
inhaltsfreie Laufzeiten und Zähler. Ein unverändertes, belegtes ETag beendet
die Verarbeitung vor Objekt-GET, Parsing und Projektionsschreibvorgang.

Dateien, CardDAV-Kontakte und CalDAV-Ereignisse verwenden eine lokale
`sync_inventory`-Projektion. Sie bindet Remote-Locator, lokale `source_id`,
ETag und optionale Modifikationszeit. Sie ist kein zweiter Fachindex und keine
Schreibautorität für Nextcloud.

## Snapshot, Cursor und Batches

Eine erfolgreiche Metadaten-Discovery wird deterministisch nach Remote-ID
sortiert und mit SHA-256 gebunden. Der Cursor enthält ausschließlich Version,
Snapshotdigest und nächsten Offset. Standardmäßig verarbeitet ein Aufruf 100
Objekte je Ressource; `OPENCLAW_SYNC_BATCH_SIZE` kann den Wert für eine
konkrete Runtime begrenzen.

Ändert sich der Snapshot zwischen zwei Batches oder ist der Cursor ungültig,
beginnt die Verarbeitung sichtbar bei Offset null. Bereits atomar publizierte
Objekte werden anhand ihrer ETags übersprungen. Ein Crash vor der
Cursorpublikation erzeugt deshalb keine doppelten Chunks. Entfernen wird erst
nach einer vollständigen, fehlerfreien und nicht abgeschnittenen Discovery
publiziert. Providerfehler, Tiefen-/Mengengrenzen und Cursorreset dürfen keine
lokale Projektion als vermeintlich gelöscht entfernen.

Ein eindeutiges identisches ETag darf einen Move beziehungsweise Rename
belegen. In diesem Fall bleibt die vorhandene Chunkprojektion erhalten und nur
der Locator wird aktualisiert. Mehrdeutige oder fehlende ETags erzwingen den
normalen Downloadpfad; es wird keine Identität geraten.

## Scheduler-Fairness

Die bestehende persistente Queue besitzt fünf feste Klassen:

| Klasse | Verwendung |
| --- | --- |
| `interactive` | reservierte höchste Klasse; nicht durch Backgroundworker wählbar |
| `time-critical` | Mailverarbeitung |
| `normal` | Portfolioversorgung |
| `maintenance` | Mailindex-Wartung |
| `background` | Nextcloud-Sync und Monitoring |

Die Klasse ergänzt die vorhandene feste Priorität, Deadline, Fokusgewichtung
und Alterung. Nach Ablauf der Starvation-Grenze bleibt der bestehende
200-Punkte-Fairnessboost maßgeblich. Es gibt weiterhin nur eine Queue und
genau einen globalen In-flight-Slot. Gesunde Arbeit wird nicht präemptiert.

Ein unvollständiger Sync-Batch endet intern mit Exit-Code 75. Der Jobloop
schließt diesen Attempt als `completed`, gibt das Lease frei und reiht die
Fortsetzung mit `parent_run_id` erneut ein. Erst danach darf die Queue
entscheiden; eine wartende zeitkritische Mailarbeit läuft somit vor dem
nächsten Background-Batch. Exit 75 ist nur mit `OPENCLAW_BATCHED_JOB=1`
wirksam und kein öffentlicher Fachfehler.

## Single-Writer- und Fehlergrenzen

- Remote-Discovery und Downloads bleiben read-only.
- SQLite-Publikation und Inventarcommit bleiben seriell beim Sync-Owner.
- Es gibt keine parallelen externen Writes und keine zweite Schedulerqueue.
- Leaseverlust beendet den Childprozess weiter fail-closed; der Scheduler
  bewahrt den unterbrochenen Attempt und reiht denselben logischen Lauf ein.
- Der read-only Core-Mount des Sync-Workers wird bei Fehlern nicht als
  Audit-Ausweichziel beschrieben; Fehler bleiben im strukturierten Syncresultat.

## Reproduzierbare Messung

```bash
PYTHONPATH=. .venv/bin/python scripts/benchmark_sync_m163.py \
  --samples 5 --objects 100 \
  --output build/m16.3-sync-benchmark.json
```

Der Benchmark verwendet ausschließlich synthetische Inhalte unter einem
temporären Verzeichnis. Er misst p50/p95 für Walltime, Prozess-CPU,
Full-/No-op-/Einzel-Delta-Sync, logische Downloads/Projektionswrites sowie die
Schedulerentscheidung zwischen zwei Batches. M16.0 markiert diese Größen als
`not-measured`; deshalb darf M16.3 keine nicht belegte
„nicht verschlechtert“-Aussage ableiten. Der neue Wert ist die erste
reproduzierbare Vergleichsbasis für spätere Pakete.

Die auf Commit `7e05a23838e9398984ffb1056f7e960e23068b4d` mit fünf Samples
und 100 Objekten erzeugte
[maschinenlesbare Evidenz](architecture/m16.3-sync-benchmark.json) ergab:

| Pfad | Walltime p50 / p95 | CPU p50 / p95 | Downloads / Writes je Sample |
| --- | ---: | ---: | ---: |
| Full | 564,726 / 573,555 ms | 67,127 / 68,865 ms | 100 / 100 |
| No-op | 4,899 / 5,291 ms | 3,000 / 3,487 ms | 0 / 0 |
| Einzel-Delta | 9,297 / 10,660 ms | 3,755 / 4,318 ms | 1 / 1 |
| Mail zwischen Batches | 20,501 / 22,187 ms | nicht erhoben | 0 / 0 |

Das belegt den neuen inkrementellen Vertrag innerhalb der synthetischen
Messung. Es belegt weder Live-Nextcloud-Latenz noch einen Vergleich zu einem
fehlenden M16.0-Produktivwert.

## Abnahme

```bash
PYTHONPATH=. .venv/bin/pytest -q tests/test_incremental_sync_m163.py
./scripts/check-repo.sh
./scripts/assistant.sh version --verify
```

Die Tests decken No-op, Einzeländerung, Delete, Move, Providerfehler,
Cursorreset, Crash/Wiederaufnahme, Batchfreigabe, Klassenpriorität, Fairness,
Leasevertrag und inhaltsfreie Stufentelemetrie ab. Produktive Jobs werden durch
diese Abnahme nicht gestartet oder verändert.
