# Containerrollen und technische Rechte

Quelle der verbindlichen Mountmatrix sind `compose.yaml` und die maschinenlesbare
Datei [`state-access.json`](state-access.json). Der M4-Haertungsvertrag steht in
[`runtime-hardening.json`](runtime-hardening.json). Die Tests vergleichen beide
Vertraege mit gerendertem Compose. Dateioperationen koennen zusaetzlich
mit `scripts/audit-state-access.py` unter `strace` inventarisiert werden.

Mount-Kuerzel: `I` Instanzkonfiguration, `G` Gateway/Sessions, `M` Mail, `O` Orders,
`P` Portfolio, `N` Monitoring, `W` Wissensindex, `C` Core/ActionPlan, `S` Security und `Q`
geteilte Koordination. `H` ist Himalaya-Konfiguration, `K` externe Konfiguration,
`E` einzelne Env-Dateien, `X` einzelne Secretdateien und `V` das
ClamAV-Signaturvolume. Ganze Config- oder Secretwurzeln werden nicht gemountet.

Die Rolle bestimmt ab M7 auch das kleinste belegte Runtime-Target. Alle Targets
tragen denselben Release und Commit; Inhalt, Messung und Freigabe beschreibt der
[Image-Lieferkettenvertrag](IMAGE_SUPPLY_CHAIN.md).

## Rollenmatrix

| Rolle | Image-Target | Primaerer Owner | Persistenter Zugriff | Begrenzung |
| --- | --- | --- | --- | --- |
| `layout-init` | `runtime` | Layoutmigration | gesamter State `rw`; kein E/X | einziger Prozess mit universellem State-Mount; ohne Netzwerk; beendet sich vor allen Rollen |
| `ollama-proxy` | `proxy-runtime` | In-Memory-Modellqueue | I `ro`, eine Proxy-Envdatei | kein OpenClaw/Mail/OCR/ClamAV, keine Secrets/Fachdaten; einzige Host-Gateway-Ausnahme |
| `gateway` | `runtime` | Gateway/Sessions und Toolaufrufe | G/M/O/P/N/W/C/S/Q `rw`; I-Profil/Memory `rw`, I-Konfigurationsordner `ro`; H/E/X/V `ro` | interaktive Universalrolle; Konfigurationssetup ausschliesslich ueber `agent-cli`, fachliche Rechte bleiben Policy-/Approval-gebunden |
| `mail-worker` | `runtime` | Mail, Orders, delegierte ActionPlans | I `ro`, M/O/C/S/Q `rw`, H/E/X/V `ro` | nur Mail-/PA-Secrets; einziger produktiver Mailwriter |
| `sync-worker` | `runtime` | Index und Syncstatus | I/M/C `ro`, W/Q `rw`, E/X `ro` | Live-Discovery ohne Core-Persistierung; nur Nextcloud/Mail-Envdateien; keine Orders-/Portfolio-/Monitoring-DB |
| `supervisor-worker` | `runtime` | Job-Sollzustand und Heartbeats | I `ro`, Q `rw`; kein E/X | nur Beobachter; internes `backend`, keine direkte Egress-Route; 1 GiB fuer die belegte OpenClaw-CLI-Spitze |
| `portfolio-worker` | `runtime` | Portfolio/Kurse | I `ro`, P/Q `rw`, E/X `ro` | nur Portfolio-Secrets; Events ueber Q, keine Gateway-Secrets oder Maildaten |
| `monitor-worker` | `runtime` | Monitoring-Snapshots | I/M/P/W/C/S `ro`, N/Q `rw`, E/X `ro` | Quellzustand technisch read-only; einzelne benoetigte Envdateien |
| `agent-cli` | `runtime` | explizit gewaehltes Tool | G/I/M/O/P/N/W/C/S/Q `rw`, H/E/X/V `ro` | kurzlebige Universalrolle; breit nur wegen explizit gewaehlter Tools |
| `clamav-update` | `maintenance-runtime` | ClamAV-Signaturen | V `rw` | nur ClamAV/Health; keine OpenClaw-State- oder Secret-Mounts |

Alle Rollen laufen mit read-only Rootfs, `cap_drop: ALL`,
`no-new-privileges`, explizitem Nicht-root-Benutzer, sicherem `tmpfs`, PID-/CPU-/
RAM-Grenzen und begrenzter lokaler Docker-Logrotation. Root- und Hostnetz-Ausnahmen
existieren nicht. Details und exakte Zahlen stehen im maschinenlesbaren Vertrag.

Der Monitor behaelt Core, Wissen und Mail technisch `ro`. Bei einer
geschlossenen WAL-Datenbank ohne `-wal` verwendet er die dokumentierte
immutable-/query-only-Lesesicht, sodass kein `-shm` auf dem Rollenmount erzeugt
wird. Ein vorhandenes WAL wird nicht ausgeblendet. Der Supervisor markiert seinen
laufenden Eigencheck neutral als `running`; ein alter Exitcode kann den neuen Lauf
nicht selbstreferenziell dauerhaft auf `failed` halten.
Der Supervisor oeffnet keine Fach-State-Datenbank und fuehrt keine Mail-Recovery
aus. Die begrenzte Production-Gate-Recovery laeuft vor dem produktiven Kindprozess
im alleinigen Mail-Worker. Fachworker stellen Events nur atomar in Q ein; der
Gateway konsumiert sie und verbindet sich mit seinem eigenen Credential ueber
Loopback. Eine Freigabe unsicherer Non-Loopback-WebSockets existiert nicht.

Der Gateway-Workspace bleibt fuer Identitaetsprofil, Memory und kontrollierte
Workspace-Daten beschreibbar. Die verschachtelten Mounts `mail_agent/` und
`personal_assistant/` ueberlagern ihn jedoch read-only. Damit koennen weder
OpenClaws Dateiwerkzeuge noch ein Shell-Fallback `tools.toml`, Policies oder
Subsystemkonfigurationen veraendern. Die kurzlebige, nur explizit gestartete
`agent-cli`-Rolle behaelt dafuer den notwendigen Schreibmount (ADR-0015).

## Netzmatrix

`backend` ist `internal: true`; `egress` erlaubt erforderliche externe Zugriffe.
Nur Gateway publiziert `127.0.0.1:18789`. Supervisor besitzt nur `backend`,
`layout-init` gar kein Netzwerk. Nur der Proxy erhaelt gemaess
[ADR-0008](adr/0008-container-netze-host-ollama.md) den Host-Gateway-Alias.

## Instrumentierte Zugriffsinventur

Der reproduzierbare Tracer erfasst `open/openat/creat/rename/unlink`, klassifiziert
Lese-/Schreibflags und vergleicht Pfade mit `state-access.json`:

```bash
./scripts/audit-state-access.py --role portfolio-worker \
  --root "portfolio=$FIXTURE/portfolio" \
  --root "coordination=$FIXTURE/coordination" -- <Fixture-Kommando>
```

Die M3-Messung vom 2026-08-05 beobachtete beim Portfolio-Probe nur
`portfolio.sqlite3` und `work_scheduler.sqlite3` samt WAL/SHM/Journal als
persistente Writes; Vertragsverletzungen: `0`. Der JSON-Bericht wird lokal unter
`build/m3-access-portfolio.json` erzeugt und nicht committed. Die Mountmatrix ist
nicht aus dieser Einzelmessung geraten: Tests decken alle Rollen ab, waehrend der
Tracer neue oder geaenderte Workerpfade verifiziert.

## Prozess- und Jobzustand

`restart: unless-stopped` beschreibt nur den Dockerprozess. Mail, Sync, Portfolio
und Monitoring lesen den persistenten Sollzustand aus `Q/job_control.json`.
`ON`, `OFF` und `FAILED/DEGRADED` duerfen nicht aus `docker compose ps` allein
abgeleitet werden. Alle Fachworker teilen den Scheduler in `Q`; jede Lease ist an
Ticket, Owner, Token und Ablaufzeit gebunden.

## Healthchecks

Gateway und Proxy besitzen direkte Liveness-Probes. Worker-Dockerhealth prueft nur
Prozess-Liveness: frischer Heartbeat und nicht `stopped`. Readiness prueft separat
Start-/Stopzustand und Schedulerfehler. `business_status` und
`consecutive_failures` bleiben im Heartbeat sichtbar; wiederholte Fehler koennen
daher nicht durch einen frischen Heartbeat maskiert werden. Ein bewusst
deaktivierter Job ist ready und fachlich gesund (`disabled`). Die fachliche
Tiefenpruefung bleibt `assistant jobs check --target all --deep`.
