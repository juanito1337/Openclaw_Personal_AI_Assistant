# Docker deployment, worker operation and rollback

Status: active operational runbook. The normative system, role, data-owner and
security model is the [architecture contract](architecture/README.md). This runbook
documents the current deployment implementation and may therefore name transitional
behavior that later milestones still have to remove.

Die verbindliche M8-Recovery-/Releasecheckliste, gemessene RTO/RPO-Grenzen und das
Canaryverfahren ohne zweiten Writer stehen im
[Recoveryvertrag](architecture/RECOVERY_AND_RELEASE.md). M8 selbst hat keinen
produktiven Stack aktiviert.

The container runtime separates the immutable program image from productive
state. The image is replaced during updates; `/srv/openclaw` remains on the
host. Gateway, background workers and diagnostic commands use the full runtime;
Proxy und ClamAV-Wartung verwenden kleinere Rollenimages. Alle drei Images stammen
aus demselben Commit und Release, waehrend separate Container jedem Prozess eine
eigene Lebensdauer, Healthgrenze und Fehlergrenze geben.

## Host layout

```text
/srv/openclaw/
├── state/
│   └── v3/                  # rollenbezogene persistente Teilbaeume
├── config/
│   ├── ca/                   # public local CA certificates (*.crt)
│   ├── himalaya/
│   ├── mail-agent.env
│   ├── personal-assistant.env
│   └── ollama-priority.env
├── secrets/                  # *.env and password files, never committed
├── backups/
│   ├── migration/
│   └── releases/
└── deployment/
    ├── compose.yaml
    ├── .env
    ├── scripts/
    └── hooks/
```

Der Stack verwendet kein Hostnetz. `backend` ist ein internes Bridge-Netz fuer
Gateway/Proxy/interne Aufrufer, `egress` nur fuer Rollen mit externen Abhaengigkeiten.
Gateway publiziert als einzigen Port standardmaessig `127.0.0.1:18789`. Nur der
Ollama-Proxy erhaelt den in ADR-0008 begruendeten Host-Gateway-Alias; sein Port 11435
bleibt ausschliesslich im Backend. Only one set of writer containers may run at a
time.

Der konfigurierte Ollama-Upstream muss vom Docker-Host-Gateway erreichbar sein.
Ein ausschliesslich an Host-Loopback gebundener Daemon ist aus einem Bridge-Container
nicht erreichbar; in diesem Fall muss der Daemon gezielt auf der Docker-Bridge oder
einer anderen kontrollierten Hostadresse lauschen und per Host-Firewall auf das
Docker-Quellnetz begrenzt werden. Der Proxy-Healthcheck prueft den Upstream und
bleibt andernfalls fail-closed. `0.0.0.0:11434` ohne passende Firewall ist keine
empfohlene Abkuerzung.

## Container and worker architecture

The stack does not build a separate image for every subsystem. Gateway, background
workers and the command-line tool start from the immutable `OPENCLAW_IMAGE`.
`OPENCLAW_PROXY_IMAGE` and `OPENCLAW_MAINTENANCE_IMAGE` are measured minimal
targets for the independent proxy and updater. All carry the same release and exact
Git revision:

```text
One immutable OpenClaw release
│
├── runtime image
│   ├── openclaw-gateway
│   └── openclaw gateway --bind lan --port 18789
│
│   ├── openclaw-mail-worker
│   └── job_loop.py mail
│
│   ├── openclaw-portfolio-worker
│   └── job_loop.py portfolio
│
│   ├── openclaw-sync-worker
│   └── job_loop.py sync
│
│   ├── openclaw-monitor-worker
│   └── job_loop.py monitor
│
│   ├── openclaw-supervisor-worker
│   └── job_loop.py supervisor
│
│   └── openclaw-agent-cli
│       └── one requested assistant.sh command, then exit
│
├── proxy-runtime image
│   └── openclaw-ollama-proxy
│       └── ollama-priority-proxy.sh serve

└── maintenance-runtime image (Maintenance profile)
    └── openclaw-clamav-update
        └── freshclam loop for the shared signature volume
```

This design provides process isolation without creating independent assistant
installations. There is one Personal Assistant, one release identity and one
persistent state tree. The workers are specialized execution processes of that
assistant, not autonomous agents.

### Responsibilities and dependencies

| Container | Responsibility | Important dependency |
| --- | --- | --- |
| `openclaw-gateway` | Chat sessions, agent context, tool selection and system events | starts after the Ollama proxy is healthy |
| `openclaw-ollama-proxy` | Prioritizes interactive and background model requests and bounds concurrency | connects to the configured Ollama upstream |
| `openclaw-mail-worker` | Scheduled IMAP processing, ClamAV gates, classification and approved mail actions | uses the Ollama proxy; must be the only mail writer |
| `openclaw-portfolio-worker` | Due EODHD refreshes, quote storage, freshness checks and price alerts | uses confirmed mappings and persistent portfolio state |
| `openclaw-sync-worker` | Read-only synchronization of configured external sources into the local index | starts after the gateway is healthy |
| `openclaw-monitor-worker` | Local operational snapshots, freshness and reliability evidence | starts after the gateway is healthy |
| `openclaw-supervisor-worker` | Desired/actual job-state checks, heartbeats and alerts | remains outside the business-job scheduler |
| `openclaw-agent-cli` | Runs one registered administrative or diagnostic command | created only through the Compose `tools` profile |
| `openclaw-clamav-update` | Updates the shared ClamAV databases | owns write access to the `clamav-db` volume |

Each long-running service has its own Docker healthcheck and
`restart: unless-stopped`. A failed portfolio process can therefore restart
without taking down mail or the gateway. Dockerhealth proves only process
liveness. Readiness und das Heartbeat-Feld `business_status` sind getrennt. Ein
frischer Heartbeat kann deshalb `degraded`/`failed` oder die Zahl
aufeinanderfolgender Fehler nicht loeschen; ein explizit deaktivierter Job bleibt
beobachtbar ready.

### Getrennter State, Konfiguration und Secrets

`layout-init` migriert den gesamten State einmalig, bevor andere Rollen starten.
Dabei werden bekannte Reste eines fehlgeschlagenen, noch unveroeffentlichten
Stagings unter der exklusiven Layoutsperre entfernt. Die SQLite-Aufteilung
kompaktiert in eine explizite Datei auf demselben State-Dateisystem und ersetzt
die gepruefte Staging-Datenbank atomar; der begrenzte Container-`/tmp` ist kein
implizites Migrationsziel.
Ein bereits abgeschlossenes OpenClaw-Profil aus `IDENTITY.md`, `SOUL.md`,
`USER.md` und `openclaw-workspace-state.json` bleibt an der aktiven
Instanzwurzel. ADR-0014 beschreibt die attestierungsgebundene, fail-closed
Nachmigration fuer Layout-3-Zustaende, die diese Dateien zuvor nur unter
`local-workspace/` erhalten hatten.
Die release-eigenen `AGENTS.md` und `HEARTBEAT.md` werden ebenfalls direkt unter
`v3/instance/` verlinkt. Der unveraenderliche `personal-assistant`-Skill wird
ueber OpenClaws `skills.load.extraDirs` aus `/opt/openclaw-agent/skills`
geladen; ein Workspace-Symlink aus dem beschreibbaren State in das Image wird
von OpenClaw zu Recht nicht als vertrauenswuerdige Skillgrenze akzeptiert. Nur
diese Instanzwurzel wird als
`/home/node/.openclaw/workspace` in Gateway und Agent-CLI gemountet; das
historische `state/workspace` ist kein aktiver Agenten-Workspace.
Danach werden nur die benoetigten Teilbaeume gemountet:

```text
/srv/openclaw/state/v3/instance             -> Agent-Workspace
/srv/openclaw/state/v3/gateway              -> Gateway/Sessions
/srv/openclaw/state/v3/domains/mail         -> Mail
/srv/openclaw/state/v3/domains/orders       -> Orders
/srv/openclaw/state/v3/domains/portfolio    -> Portfolio
/srv/openclaw/state/v3/domains/monitoring   -> Monitoring
/srv/openclaw/state/v3/domains/knowledge    -> Wissensindex/Sync-Cursor
/srv/openclaw/state/v3/shared/core          -> ActionPlan/Audit
/srv/openclaw/state/v3/shared/security      -> Antiviruscache
/srv/openclaw/state/v3/shared/coordination  -> Jobs/Scheduler/Heartbeats

/srv/openclaw/config/himalaya
    -> /home/node/.config/himalaya          read-only

/srv/openclaw/config/<rolle>.env
    -> /etc/openclaw-env/<rolle>.env         einzelne read-only Datei

/srv/openclaw/secrets/<rolle>.env
    -> /run/openclaw-env/<rolle>.env         einzelne read-only Datei

/srv/openclaw/secrets/himalaya-*-password
    -> /run/openclaw-secrets/<Datei>         einzelne read-only Datei

Docker volume clamav-db
    -> /var/lib/clamav                      read-only in normal containers
                                           read/write in clamav-update
```

Die exakte `ro`/`rw`-Zuordnung steht in der
[Rollenmatrix](architecture/CONTAINER_ROLES.md) und wird gegen gerendertes Compose
getestet. Ein Fachworker sieht keine unbeteiligte Datenbank beschreibbar.

```text
mail-worker        -> mail_agent/data/mail_agent.sqlite3 and published mail search projection
portfolio-worker   -> personal_assistant/data/portfolio.sqlite3
monitor-worker     -> personal_assistant/data/monitoring.sqlite3
supervisor-worker  -> job heartbeats, alerts and scheduler state
sync-worker        -> local search index and validated read-only source projections
```

They are not five copies of the data. Persistent state exists once on the host
and survives container replacement. Release-owned code and skills come from the
image; instance configuration and runtime data remain outside it.

Der Mailworker ist alleiniger Owner der Mail-SQLite und veroeffentlicht fuer den
Sync-Worker unter `domains/mail/search_documents` unveraenderliche Datensaetze mit
einem atomar ersetzten `_projection.json`-Manifest. Der Sync-Worker validiert die
vollstaendige Generation, Pruefsummen und Aktualitaet vor einem Indexwrite und
oeffnet `mail_agent.sqlite3`, `-wal` oder `-shm` nicht. Der bestehende Mail-Mount
bleibt `ro`; dafuer werden weder Schreibrechte noch ein zweiter Datenowner
eingefuehrt. Eine fehlende, veraltete oder korrupte Projektion bleibt als
Sync-Fehler mit der letzten vollstaendigen Generation sichtbar.

Der Sync-Worker entdeckt die aktuell erreichbaren Nextcloud-Ressourcen bei jedem
Lauf live, persistiert diese Discovery aber nicht in der Core-Registry. Sein
`shared/core`-Mount bleibt entsprechend der Rollenmatrix read-only; nur
core-faehige, explizite CLI-Aufrufe duerfen `resources.toml` aktualisieren. Der
Worker schreibt Index und Syncstatus ausschliesslich unter Wissen/Koordination.
Teil- oder Quellfehler liefern Exitcode 1 und bleiben als `degraded` mit der
urspruenglichen Ursache sichtbar.

Der Monitor liest seine Core-, Wissens- und Mailquellen weiterhin ausschliesslich
read-only. Geschlossene WAL-Datenbanken ohne vorhandenes WAL werden immutable und
`query_only` geoeffnet, damit SQLite auf dem `ro`-Mount kein `-shm` anlegen muss;
ein vorhandenes WAL bleibt sichtbar oder der Lauf bricht fail-closed ab.

Supervisor-Systemereignisse erhalten die interne Gatewayadresse ueber
`OPENCLAW_GATEWAY_URL`. OpenClaw paart diese Umgebungsadresse mit dem separat
gemounteten Gateway-Credential, ohne das Secret in `--token`-/`--password`-
Prozessargumente zu kopieren. Der Worker besitzt 1 GiB RAM, nachdem produktive
cgroup-Zaehlung wiederholte OOM-Kills unter dem frueheren 512-MiB-Limit belegt
hat. Waehrend eines Eigenchecks ist sein aktuelles Resultat `running`; der
Exitcode des vorherigen Laufs wird erst nach Abschluss wieder bewertet.

Layout 3 fuehrt vor jeder Veraenderung Schreibbarkeits-, UID-, Freiplatz- und
SQLite-Checks aus. Es erzeugt ueber SQLite `backup()` einen SHA-256-verifizierten
Snapshot, baut `v3` in einem Stagingpfad und publiziert ihn atomar. Konfiguration,
Datenbanken, Sessions, Korrekturhistorie und lokale Dokumente werden erhalten. Ein
`flock` serialisiert Starts; ein unvollstaendiges Publish bricht fail-closed ab.
Restore in ein leeres Fixture ist ueber `personal_assistant.runtime_layout restore`
verifiziert. Der produktive Release-Rollback verwendet weiterhin das vor dem
Deployment bei gestoppten Writern erzeugte komplette Releasebackup.

All container code starts under `/opt/openclaw-agent`; Python uses safe-path mode
and cannot import packages from the writable workspace. Compose makes the root
filesystem read-only and runs every role non-root without Linux capabilities.
Mail-Kalender und -Kontakte verwenden die native, release-eigene
CalDAV/CardDAV-Bruecke. Ein workspace-lokaler `openclaw-nextcloud`-Community-Skill
wird weder benoetigt noch ausgefuehrt; Kalenderwrites loesen genau eine konfigurierte
Ressource auf und verwenden create-only `If-None-Match`.
Die offiziellen externen Plugins Brave und Signal liegen ebenfalls read-only
unter `/opt/openclaw-plugins`, sind durch npm-Lockdatei und Supply-Chain-Vertrag
gepinnt und werden ueber feste `plugins.load.paths` geladen. Ausfuehrbare
npm-Payloads im Gateway-State sind nicht erlaubt; `OPENCLAW_NIX_MODE=1` sperrt
Plugininstallation und -update zur Laufzeit.
Der Entry Point parst nur die fuer die konkrete Rolle fest hinterlegten Env-Dateien
als begrenzte `KEY=VALUE`-Daten. Shellauswertung, Verzeichnissuche sowie unbekannte
Schluessel sind gesperrt. PID-/CPU-/RAM-Grenzen, sichere tmpfs-Pfade und lokale
Logrotation sind pro Rolle maschinenlesbar festgelegt.

### Worker loop and scheduler

The five worker containers execute the same bounded loop implementation with a
different job name:

```text
container starts
    -> load configuration and secrets
    -> read persistent desired state
    -> if OFF: publish a ready/disabled heartbeat and wait
    -> if ON: request scheduler permission when required
    -> run exactly the allowlisted job
    -> record result and heartbeat
    -> wait for the next outer interval
    -> repeat
```

Mail, portfolio, sync and monitor are business jobs and enter the shared,
persistent adaptive scheduler. This prevents heavy background work from running
without coordination. The supervisor stays outside that queue so it can still
detect a stalled scheduler or missing worker heartbeat.

Some subsystems apply an additional internal due check. For example, the
portfolio worker may wake every 15 minutes while the configured provider
interval is 90 minutes. Runs before the next due time return
`skipped-not-due` and do not consume another EODHD request.

### Docker state versus job desired state

Container state and business-job state are intentionally separate:

| Docker process | Desired job state | Meaning |
| --- | --- | --- |
| running | `ON` | the worker performs its scheduled job |
| running | `OFF` | the worker remains observable but performs no business work |
| stopped/unhealthy | `ON` | failure; the supervisor must report the missing service or heartbeat |
| stopped | `OFF` | acceptable only when the deployment intentionally stopped that container |

The persistent desired state is controlled through the registered interface,
not by manually editing Compose:

```bash
./scripts/assistant.sh jobs status --target all
./scripts/assistant.sh jobs on portfolio
./scripts/assistant.sh jobs off portfolio
./scripts/assistant.sh jobs restart portfolio
```

This distinction explains why `docker compose ps` and `jobs status` answer
different questions. Docker reports whether a process exists; the job
controller reports whether work is intended, running and healthy.

### Agent sessions after image updates

The gateway loads the Personal-Assistant skill into an agent session. Replacing
the image updates the workspace and newly created sessions, but it cannot
rewrite context already stored in an open conversation. After an update that
changes `AGENTS.md` or `skills/personal-assistant/SKILL.md`, verify the release
and open a new chat session before testing the new agent behavior.

## 1. Build or publish the image

Lokaler isolierter Rollenbuild (kein Deployment):

```bash
./docker/scripts/build-local.sh \
  openclaw-agent:r27.2.5-local \
  openclaw-agent:r27.2.5-local-proxy \
  openclaw-agent:r27.2.5-local-maintenance
```

For the normal GitHub flow, push a release tag such as `r27.2.5`. The
`container.yml` workflow tests the repository, scans all targets, generates SBOM
and Provenance, signs all immutable digests and publishes the three images to GHCR.
The [supply-chain contract](architecture/IMAGE_SUPPLY_CHAIN.md) documents the exact
checks. The production host needs Docker and logs into the private registry once:

```bash
echo "$GITHUB_TOKEN" | docker login ghcr.io -u juanito1337 --password-stdin
```

Use a fine-grained token with read access to packages. Do not store it in Git.
The deployment verifier runs the digest-pinned Cosign image from the supply-chain
lock with a read-only root filesystem. It mounts only the existing Docker registry
configuration read-only, so no separately installed host Cosign binary is needed.

Deployment receives three `name@sha256:...` references and the exact 40-character
commit in `OPENCLAW_EXPECTED_SOURCE_REVISION`. `deploy.sh` verifies signature,
SLSA-Provenance, SPDX-SBOM, release, revision and role for every image before it
stops any running container. Tags such as `latest`, an unsigned digest or a role
mismatch are rejected without changing the running stack.

Before any writer is stopped, the exact signed maintenance digest additionally
starts `freshclam` and `clamscan` in isolated containers and completes one bounded,
certificate-verified libcurl TLS handshake with `database.clamav.net`. The same
behavioral preflight runs in the CI role smoke. A loader/ABI, certificate, DNS or
transport failure therefore stops the rollout before the production interruption;
the later real signature update remains fail-closed and still triggers rollback if
it fails after the verified backup.

### Fast live-test loop

Branches below `test/**` publish a container automatically after the repository
check. Each image receives a readable branch tag and an immutable
`sha-<12 Zeichen>` tag. Newer pushes to the same test branch cancel an older
in-progress build.

The image stores the complete Git commit as OCI label, runtime environment and
`SOURCE_REVISION`. Status and Doctor compare these values with `RELEASE.json`,
`VERSION` and the actual executable paths. The persistent layout marker records
the applied layout, release and revision but never selects executable code.

Development machine:

```bash
git switch -c test/mail-review-replies
git push -u origin test/mail-review-replies
```

After the `Container image` action is green, use the same clean, pushed commit
on the Docker host:

```bash
git switch test/mail-review-replies
git pull --ff-only
./docker/scripts/live-test-branch.sh
```

The helper deploys the immutable SHA image through the normal deployment path.
It does not start a second stack: existing writers are stopped first, the local
release backup is created and verified, and a failed smoke test rolls back.
Remote IMAP, SMTP or Nextcloud effects still cannot be undone by restoring only
the local backup.

Before changing the deployment bundle, the helper verifies access to the Docker
API. It never changes host group membership itself. It also passes the complete
Git commit to the deployment, which verifies both the image label and the source
revision visible inside the running tool container.

## 2. Prepare the host

```bash
sudo ./docker/scripts/setup-host.sh
nano /srv/openclaw/deployment/.env
```

The local release backup of state, configuration, secrets and SQLite databases
is mandatory before a write-enabled smoke test. External hooks are optional for
this installation because the agent uses a restricted Nextcloud account and
critical data is backed up separately:

```dotenv
REQUIRE_EXTERNAL_BACKUP_FOR_WRITE_TEST=false
OPENCLAW_EXTERNAL_BACKUP_HOOK=
OPENCLAW_EXTERNAL_RESTORE_HOOK=
```

Enable the requirement and provide both executable hooks when a deployment must
also be able to roll back remote IMAP moves, Nextcloud files, CardDAV contacts,
CalDAV events or VTODO tasks automatically.

## 3. Migrate a native installation once

```bash
/srv/openclaw/deployment/scripts/migrate-live.sh --execute
```

The migration:

1. rejects an incomplete legacy source before stopping any service,
2. records and disables the old user-level systemd writers,
3. creates and verifies an untouched migration archive containing the executable workspace,
4. builds the complete state/config/secret result in a private staging directory,
5. rewrites active State-/Workspacepfade to `/home/node/.openclaw`,
6. migrates Himalaya `secret-tool` commands to protected files in `/srv/openclaw/secrets`,
7. preserves an existing gateway token/password or creates one protected token when the legacy gateway used no authentication,
8. points the mail classifier and Gateway model providers at the container-owned
   Ollama priority proxy,
9. validates required files and every staged SQLite database before publishing anything below `/srv/openclaw`,
10. adds the mail-agent Nextcloud section only when all three Nextcloud credentials exist and the section was missing,
11. creates a verified backup of an existing `/srv/openclaw` state before a remigration publishes its staged result,
12. prefers credentials matching the explicit gateway authentication mode and safely replaces an incompatible stale container secret in the staged copy,
13. records the verified legacy archive, archive member and SHA-256 for a later automatic rollback,
14. leaves the original live directory untouched until the Docker deployment is verified.

Der Layout-3-Init normalisiert zusaetzlich die tatsaechlich publizierte
`v3/instance/mail_agent/config.toml`, `v3/gateway/openclaw.json` und vorhandene
`v3/gateway/agents/*/agent/models.json` auf `http://ollama-proxy:11435`. Diese
idempotente Pruefung laeuft auch bei spaeteren Containerstarts. Sie uebersetzt nur
den bekannten nativen Loopback-Prioritaetsproxy auf Port 11435; andere
Providerendpunkte brechen fail-closed ab. Die Legacy-Workspace-Kopie bleibt fuer
einen Rollback unveraendert.

Bei einer vorhandenen Ollama-Providerdefinition ergaenzt Layout-Init fehlende,
explizite Laufzeitgrenzen: `models.providers.ollama.timeoutSeconds=1800` und
`agents.defaults.timeoutSeconds=3600`. Damit deckt der Providervertrag die
konfigurierte Proxy-Wartezeit plus Upstream-Zeit ab, waehrend der gesamte
Agentenlauf eine groessere endliche Obergrenze behaelt. Bereits gesetzte
Betreiberwerte werden nicht ersetzt. `assistant.sh ollama check` fragt aus
Container-Clientrollen den privaten Proxy-Health-Endpunkt ab; nur die Proxyrolle
selbst benoetigt ihre geschuetzte Upstream-Konfiguration.

Aus Releases vor ADR-0014 falsch nach `local-workspace/` verschobene
`IDENTITY.md`, `SOUL.md`, `USER.md` und der abgeschlossene Workspace-Setupstatus
werden nur dann wieder aktiv, wenn OpenClaws SHA-256-Attestierung jede
abweichende aktive Datei als automatisch generierte Vorlage belegt. Ein bereits
abgeschlossener oder bearbeiteter aktiver Setup wird niemals ueberschrieben.
Historische `TOOLS.md`- und `MEMORY.md`-Anweisungen bleiben zur bewussten Sichtung
quarantiniert.

Vor der SQLite-Gesamtpruefung ersetzt die Migration Brave und Signal durch ihre
read-only Imagepfade, synchronisiert den generierten `installed_plugin_index`
transaktional auf Version, Integritaet und Pfad des Imagevertrags und entfernt
ihre alten npm-Projektverzeichnisse nur aus dem Staging. Jedes nicht im
Imagevertrag enthaltene Plugin stoppt die Migration fail-closed.

Historical sessions and trajectories are not rewritten; only active configuration,
die generierten Plugin-Metadaten und die ersetzten npm-Payloads im Staging werden
geaendert. A repeated migration preserves the previously recorded
legacy-unit activation set even when those units are already disabled.

## 4. Private Nextcloud CA

Place only public CA certificates in:

```text
/srv/openclaw/config/ca/*.crt
```

At container startup, the entrypoint combines the system trust store with these
certificates and exports the resulting runtime bundle for Python/OpenSSL,
`requests` and Node.js. Never place a private key in this directory.

## 5. First deployment

```bash
cd /srv/openclaw/deployment
export OPENCLAW_EXPECTED_SOURCE_REVISION=<40-stelliger-commit>
./scripts/deploy.sh \
  'ghcr.io/.../openclaw@sha256:<runtime-digest>' \
  'ghcr.io/.../openclaw@sha256:<proxy-digest>' \
  'ghcr.io/.../openclaw@sha256:<maintenance-digest>'
```

The deployment sequence is deliberately strict:

1. verify Cosign signature, SLSA provenance, SPDX SBOM, release, role and exact
   source revision for all three immutable target digests,
2. pull the verified images and check target-image state-layout limits,
3. abort an incompatible downgrade before stopping the current stack,
4. require that `OPENCLAW_CURRENT_RUNTIME` agrees with observed systemd/Docker
   writers, explicitly disable installed legacy writer timers (stopping alone does
   not change their enablement), then stop every writer and verify the complete
   Single-Writer gate,
5. create an optional external snapshot when a hook is configured,
6. run SQLite quick checks,
7. archive state/config/secrets,
8. verify SHA-256 and extract the archive into a temporary restore test,
9. run `layout-init` and start only Ollama proxy and gateway; the entrypoint
   migrates layout 1/2 to 3,
10. run version/doctor/dry-run checks,
11. process at most `OPENCLAW_WRITE_TEST_LIMIT` real messages when enabled,
12. start mail, sync and supervisor workers only after success,
13. verify worker health and the current job heartbeat after the workers have
    actually started.

Any failing command after the verified backup triggers `rollback.sh`
automatically, including a failure inside the Compose shell helper. A runtime
identity mismatch fails earlier without stopping or backing up anything; it must
be resolved explicitly because the deployer does not guess which writer is
authoritative. If the preparatory backup fails after a legacy shutdown, the
recorded legacy activation set is enabled again before the deployer returns the
original error.

Rollback restores the contents of the existing protected `state`, `config` and
`secrets` roots in place. It does not require permission to delete the
root-owned `/srv/openclaw` child directories themselves. When the previous
runtime was systemd, rollback validates and restarts the untouched legacy home;
it never replaces `~/.openclaw` with container state. If that legacy home is
incomplete, rollback verifies and restores it from the migration archive linked
in the release backup before stopping the current containers. If no verified
legacy source is available, rollback aborts while the current runtime is still
running.

The deployment also verifies that all legacy writer services are inactive and
their timers disabled before and after the container workers start. A remaining
legacy writer is a hard failure and triggers recovery instead of allowing two
writers.

## 6. Later updates from Git

The host deployment scripts and `compose.yaml` are outside the image. Refresh
them from the checked-out Git revision before running the release deployment:

```bash
git switch main
git pull --ff-only
./docker/scripts/refresh-deployment.sh
cd /srv/openclaw/deployment
export OPENCLAW_EXPECTED_SOURCE_REVISION="$(git rev-parse HEAD)"
./scripts/deploy.sh '<runtime@sha256:...>' '<proxy@sha256:...>' \
  '<maintenance@sha256:...>'
```

`refresh-deployment.sh` updates `compose.yaml`, `.env.example`, deployment
scripts sowie den gepinnten Pluginvertrag und dessen Migrationshelfer. It does
not overwrite the productive `.env` or active local hooks.

Tool code plus release-owned defaults and baseline policies are read from the
new image on every update. Persistent `tools.toml` and `policies.toml` files are
instance overrides; account/resource selections and explicit permission grants
remain outside the image. New write permissions are never granted by an image
update. The gateway mounts both instance configuration directories read-only;
generic agent file or shell tools cannot patch them after a failed domain call.
At each layout start the fixed container data paths in `tools.toml` are repaired
idempotently while resource selections and permission grants remain unchanged.
Administrative setup therefore runs only in the explicitly selected, short-lived
`agent-cli` role. For the direct mail tools, approve the required `read`, `move` and
`forward` permissions once with:

```bash
cd /srv/openclaw/deployment
docker compose --env-file .env --profile tools run --rm agent-cli \
  /opt/openclaw-agent/scripts/assistant.sh \
  setup mail-move --approve-permissions
```

Falls `mail-agent.sh doctor` eine konfigurierte Kalender-Ressourcen-ID meldet,
die in der aktuellen Discovery nicht mehr vorkommt, wird sie nicht automatisch
auf einen Kalender mit aehnlichem Namen oder Share-Pfad umgestellt. Zuerst die
aktuellen IDs read-only ermitteln und danach genau eine davon ausdruecklich fuer
die Kalender-Mailfunktion auswaehlen:

```bash
docker compose --env-file .env --profile tools run --rm agent-cli \
  /opt/openclaw-agent/scripts/assistant.sh nextcloud discover
docker compose --env-file .env --profile tools run --rm agent-cli \
  /opt/openclaw-agent/scripts/assistant.sh setup tools \
  --calendar-resource '<exakte-nextcloud-calendar-id>' \
  --approve-permissions
```

Der zweite Befehl veraendert die lokale Toolkonfiguration und darf deshalb erst
nach bewusster Auswahl der im ersten Befehl ausgegebenen Ressource laufen.

### M10-Rechnungs-Canary

Eine gruene M10-Entwicklungs- oder Imageabnahme autorisiert weder Reprocessing
noch Registerersatz. Der separate
[`M10-Rolloutvertrag`](INVOICE_M10_ROLLOUT.md) verlangt fuer einen spaeteren
Auftrag dasselbe signierte Rollenset, einen belegten Single-Writer-Zustand, ein
verifiziertes lokales Backup, einen verifizierten externen Nextcloud-Snapshot,
eine read-only Status-/Audit-Baseline und genau eine angezeigte Vorschau. Erst
eine danach erneut erteilte Einzelfreigabe darf den unveraenderten Hash/Digest
anwenden. M10.8 fuehrt diesen produktiven Ablauf nicht aus.

### Erste M9-Mailordner-Aktivierung

Eine vor M9 bestehende Mailkonfiguration besitzt noch kein `folders.relevant`.
Der Produktsmoke blockiert diesen Zustand absichtlich, damit kein Writer mit
einem still gewaehlten Ziel startet. Nach ausdruecklicher Freigabe kann der
Test-Branch den exakten Zielordner innerhalb des bereits gesicherten
Writer-Stopp-Fensters konfigurieren und create-only anlegen:

```bash
sg docker -c './docker/scripts/live-test-branch.sh \
  --activate-relevant-folder Agent/Relevant --yes'
```

Der Ablauf lehnt einen abweichenden bereits konfigurierten Zielordner ab, legt
keine weiteren fehlenden Ordner an und verschiebt keine Mail. Ein lokal
erfolgreicher Rollback loescht den eventuell bereits erzeugten externen
IMAP-Ordner nicht; dafuer ist ein verifizierter externer Restore-Hook erforderlich.
Nach erfolgreicher Erstaktivierung wird der normale Deploymentaufruf ohne diese
Option verwendet.

## 7. Manual rollback

List release backups:

```bash
ls -1 /srv/openclaw/backups/releases
```

Restore one backup:

```bash
/srv/openclaw/deployment/scripts/rollback.sh \
  20260805T120000Z_r27.2.4-to-r27.2.5
```

The current failed state is saved for analysis, the optional remote restore hook
is called when a snapshot reference exists, the local archive is restored and
the previous Docker image is started.

## Operations

```bash
cd /srv/openclaw/deployment

docker compose --env-file .env ps
docker compose --env-file .env logs -f gateway
docker compose --env-file .env logs -f mail-worker

docker compose --env-file .env --profile tools run --rm agent-cli \
  /opt/openclaw-agent/scripts/assistant.sh status
```

The agent's `jobs on/off/status` commands remain available. In container mode
they change a persistent desired-state file; the mail, sync, portfolio, monitor
and supervisor workers observe it without requiring systemd inside the
containers. Mail, sync, portfolio and monitor runs enter the shared adaptive
scheduler; the supervisor remains outside it.

```bash
docker compose --env-file .env --profile tools run --rm agent-cli \
  /opt/openclaw-agent/scripts/assistant.sh scheduler status
```

## Backup boundaries

The release backup includes local state, configuration and secrets. It does not
contain the full remote mailbox or Nextcloud server. With external hooks disabled,
a rollback restores the local agent state and previous image but cannot undo an
already successful remote mail move or CalDAV/CardDAV/Nextcloud write.
