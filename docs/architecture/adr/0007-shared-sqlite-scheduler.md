# ADR-0007: Der Scheduler bleibt eine eng geteilte SQLite/WAL-Datenbank

- Status: Accepted
- Datum: 2026-08-05
- Entscheider: Data Maintainers und Operations Maintainers
- Betroffene Milestones: M3-M8

## Kontext

Mail, Sync, Portfolio und Monitoring koordinieren vollstaendige Hintergrundjobs.
Alle Prozesse laufen auf demselben Host und teilen nur Queue-, Lease- und technische
Ergebnisdaten. Zu entscheiden war, ob ein eigener Coordinator-Prozess oder SQLite
die Konsistenzgrenze bildet.

## Entscheidung

Der Scheduler bleibt unter `shared/coordination/work_scheduler.sqlite3` bei
SQLite/WAL. `personal_assistant.work_scheduler` ist alleiniger Schemaowner. Claims
verwenden `BEGIN IMMEDIATE`; Writer besitzen Busy-Timeouts. Jede laufende Arbeit ist
an Ticket, Worker-Owner, zufaelliges Lease-Token und Ablaufzeit gebunden. Verlust
wiederholter Renewals bleibt fail-closed. Die Datenbank wird nur ueber die SQLite-
Backup-API gesichert, nie durch isoliertes Kopieren von DB/WAL/SHM.

## Begruendung

Die Last ist klein, hostlokal und transaktional. Ein Netzwerkdienst wuerde einen
weiteren privilegierten Prozess, Verfuegbarkeitszustand und Recoverypfad einfuehren,
ohne heute eine nachgewiesene Konsistenz- oder Durchsatzluecke zu schliessen. Reale
Mehrprozess-, Lock-, Lease- und SIGKILL-Tests bestaetigen die benoetigten Invarianten.

## Konsequenzen

Alle Fachworker benoetigen `shared/coordination` `rw`; dies ist eine bewusst kleine
gemeinsame Mountgrenze. Fachliche Datenbanken bleiben getrennt. Ein Coordinator wird
neu bewertet, wenn mehrere Hosts, dauerhaft hohe Write-Contention oder nicht mehr
behebbare Deadlineverletzungen gemessen werden.

## Verifikation

`tests/test_state_layout_m3.py`, `tests/test_work_scheduler.py`,
`scripts/audit-state-access.py` und die dynamische Containerabnahme pruefen
Parallelzugriff, abgelaufene Leases, Crash-Recovery, Mounts und Integritaet.
