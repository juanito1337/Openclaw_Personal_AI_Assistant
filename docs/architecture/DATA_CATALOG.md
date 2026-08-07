# Daten- und Zustandskatalog

Layout 3 liegt hostseitig unter `/srv/openclaw/state/v3`. Die folgenden Kurzpfade
sind relativ dazu. Nebenfiles `-wal`, `-shm` und `-journal` gehoeren stets zur
jeweiligen SQLite-Datenbank und duerfen nicht einzeln gesichert werden.

## Persistente Teilbaeume

| Teilbaum | Owner | Schema/Migration | Backup, Locking und Aufbewahrung |
| --- | --- | --- | --- |
| `instance/` | Instanzadministrator | TOML-Konfiguration und kontrollierte lokale Dokumente; Layoutmarker Schema 1 | in jedem Releasebackup; Worker `ro`, nur Gateway/CLI `rw`; keine Secrets |
| `gateway/` | OpenClaw Runtime | Gatewaykonfiguration, Sessions und Agentenkontext | Releasebackup; nur Gateway/CLI `rw`; Aufbewahrung nach Runtimevertrag |
| `domains/mail/` | `mail_agent.storage` | `mail_agent.sqlite3` plus Schema-Migration vor Learning/Produktivlauf | SQLite-Backup-API/Quick-Check; Mail-Prozesslock; Korrekturhistorie nie zur Reparatur loeschen |
| `domains/orders/` | `personal_assistant.orders` | `orders.sqlite3` | SQLite-Backup-API; Remote-Nachbedingung vor Retry; Historie erhalten |
| `domains/portfolio/` | `personal_assistant.portfolio` | `portfolio.sqlite3`, kontrolliertes `inbox/` | SQLite-Backup-API; Portfolio-Owner `rw`, Monitor `ro`; Imports/Quotes dauerhaft |
| `domains/monitoring/` | `personal_assistant.monitoring` | `monitoring.sqlite3` | SQLite-Backup-API; nur technische Metriken; Retention durch Monitorvertrag |
| `domains/knowledge/` | `personal_assistant.storage` | `knowledge.sqlite3` mit Dokumenten, Chunks, FTS und Sync-Cursorn | SQLite-Backup-API; Sync `rw`, Monitor `ro`; getrennt von ActionPlan/Audit |
| `shared/core/` | `personal_assistant.storage` | `assistant.sqlite3`, `resources.toml`, Action-Payloads/Workspace-Outbox | SQLite-Backup-API; Unique-Idempotenzschluessel; Audit und ActionPlan konsistent sichern |
| `shared/security/` | `personal_assistant.antivirus` | `antivirus.sqlite3`, temporaerer Scanpfad | Cache nur bei identischem SHA/Scanner; Mail/CLI `rw`, Monitor `ro` |
| `shared/coordination/` | Scheduler und JobController | `work_scheduler.sqlite3`, `job_control.json`, Heartbeats und Rollenlogs | WAL/Busy-Timeout; atomarer JSON-Replace; Worker besitzen nur eigene Heartbeats/Logs |

## Datei- und Transaktionsvertraege

| Datei | Writer | Leser | Invariante |
| --- | --- | --- | --- |
| `shared/core/assistant.sqlite3` | Gateway/CLI, Sync, Mail-ActionBridge | Core, Monitor `ro` | ActionPlan, Status und Audit laufen in Transaktionen; `idempotency_key` bleibt eindeutig |
| `domains/knowledge/knowledge.sqlite3` | Gateway/CLI, Sync | Suche, Monitor `ro` | Dokumente, Chunks, FTS und Sync-Cursor bilden einen eigenen konsistenten SQLite-Satz |
| `domains/mail/mail_agent.sqlite3` | Mailworker oder explizite Mail-CLI | Mail, Sync/Monitor `ro` | genau ein produktiver Remote-Mailwriter; lokale DB darf parallel gelesen werden |
| `domains/orders/orders.sqlite3` | Mailworker/Orders-CLI | Orders | lokale Idempotenz ersetzt keine Remote-Nachbedingung |
| `domains/portfolio/portfolio.sqlite3` | Portfolioworker/CLI | Monitor `ro` | ein Schemaowner, WAL, Pflichtdaten fail-closed |
| `domains/monitoring/monitoring.sqlite3` | Monitorworker/CLI | Monitor | keine Mailinhalte oder Credentials |
| `shared/security/antivirus.sqlite3` | kontrollierte Scanaufrufer | Monitor `ro` | Verdict ist an Hash und Scanneridentitaet gebunden |
| `shared/coordination/work_scheduler.sqlite3` | alle Fachworker und Focus-CLI | Supervisor/Monitor | `BEGIN IMMEDIATE`, atomare Claims, Owner/Token/Ablauf; stale Lease wird kontrolliert recovered |
| `shared/coordination/job_control.json` | JobController | alle Worker | atomarer Replace; nur registrierte Jobs |
| `shared/coordination/container_jobs/<job>.json` | genau der jeweilige Worker | Healthcheck/Supervisor | atomarer Replace, keine Nutzinhalte |

## Layoutmigration und Restore

`personal_assistant.runtime_layout` fuehrt den Wechsel 1/2 -> 3 unter `flock` aus:

1. Pfad-, Schreibbarkeits-, UID- und Freispeicher-Preflight,
2. `PRAGMA quick_check` aller erkannten SQLite-Dateien,
3. konsistenter Snapshot ueber SQLite `backup()`, SHA-256 und internes Manifest,
4. verlustfreie Aufteilung der bisherigen `assistant.sqlite3` in Core und Wissen,
   explizites `VACUUM INTO` auf dem State-Dateisystem gegen Datenreste, atomarer
   Replace sowie erneute Quick-Checks unter Staging,
5. atomarer Rename nach `v3/`, danach erst globaler Layoutmarker.

Ein Neustart ist idempotent. Ein unvollstaendiges bereits publiziertes Ziel bricht
fail-closed ab; bekannte unveroeffentlichte `v3-*`-Stagingreste werden unter der
Layoutsperre vor einem neuen Versuch und nach Fehlern entfernt. Unerwartete
Stagingeintraege werden nicht geloescht, sondern blockieren die Migration.
`runtime_layout restore` akzeptiert nur ein leeres Ziel und eine
passende SHA-256-Datei, blockiert Pfadtraversal und prueft alle restaurierten
SQLite-Datenbanken. Der Rueckweg ist damit als Fixture-Restore fuer Layout 1/2 und
als vollstaendiger Layout-3-Restore getestet; er wird nie automatisch ueber einen
laufenden produktiven State geschrieben.

Instanzkonfiguration bleibt unter `/srv/openclaw/config`, Secrets unter
`/srv/openclaw/secrets` und ClamAV-Signaturen im Volume `clamav-db`. Sie sind nicht
Teil des State-Layouts und folgen dem Releasebackupvertrag. Lokales Restore allein
setzt keine bereits erfolgten IMAP-/Nextcloud-/CalDAV-/CardDAV-Aenderungen zurueck.
