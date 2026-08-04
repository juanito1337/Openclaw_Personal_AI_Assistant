# Docker deployment, worker architecture and rollback (R27.2.3)

The container runtime separates the immutable program image from productive
state. The image is replaced during updates; `/srv/openclaw` remains on the
host. Gateway, background workers and diagnostic commands use the same image and
source revision, while separate containers give each process an independent
lifecycle, healthcheck and failure boundary.

## Host layout

```text
/srv/openclaw/
├── state/                    # mounted as /home/node/.openclaw
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

The container uses host networking on Linux. This preserves the current local
Ollama proxy and gateway addresses and avoids exposing extra Docker bridge ports.
Only one set of writer containers may run at a time.

## Container and worker architecture

The stack does not build a separate image for every subsystem. Gateway, proxy,
background workers and the command-line tool all start from the same immutable
`OPENCLAW_IMAGE`. Compose gives each container a different command:

```text
One immutable OpenClaw image
│
├── openclaw-gateway
│   └── openclaw gateway --bind lan --port 18789
│
├── openclaw-ollama-proxy
│   └── ollama-priority-proxy.sh serve
│
├── openclaw-mail-worker
│   └── job_loop.py mail
│
├── openclaw-portfolio-worker
│   └── job_loop.py portfolio
│
├── openclaw-sync-worker
│   └── job_loop.py sync
│
├── openclaw-monitor-worker
│   └── job_loop.py monitor
│
├── openclaw-supervisor-worker
│   └── job_loop.py supervisor
│
└── openclaw-agent-cli
    └── one requested assistant.sh command, then exit

Maintenance profile:
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
without taking down mail or the gateway. Conversely, a healthy container only
proves that its process and heartbeat are healthy; it does not automatically
mean that the corresponding business job is enabled.

### Shared state, configuration and secrets

All normal containers mount the same host-owned paths:

```text
/srv/openclaw/state
    -> /home/node/.openclaw                 read/write

/srv/openclaw/config
    -> /etc/openclaw-agent                  read-only

/srv/openclaw/config/himalaya
    -> /home/node/.config/himalaya          read-only

/srv/openclaw/secrets
    -> /run/openclaw-secrets                read-only

Docker volume clamav-db
    -> /var/lib/clamav                      read-only in normal containers
                                           read/write in clamav-update
```

The workers consequently see the same configuration, tool registry and local
databases, but normally operate on separate subsystem data:

```text
mail-worker        -> mail_agent/data/mail_agent.sqlite3
portfolio-worker   -> personal_assistant/data/portfolio.sqlite3
monitor-worker     -> personal_assistant/data/monitoring.sqlite3
supervisor-worker  -> job heartbeats, alerts and scheduler state
sync-worker        -> local search index and source caches
```

They are not five copies of the data. Persistent state exists once on the host
and survives container replacement. Release-owned code and skills come from the
image; instance configuration and runtime data remain outside it.

At startup, the common entrypoint copies the image-owned source, scripts,
documentation and Personal-Assistant skill into the persistent workspace. It
preserves `config.toml`, `rules.toml`, `resources.toml`, `policies.toml`,
`tools.toml` and every `data/` directory. A shared filesystem lock serializes
this synchronization when several containers start together.

### Worker loop and scheduler

The five worker containers execute the same bounded loop implementation with a
different job name:

```text
container starts
    -> load configuration and secrets
    -> read persistent desired state
    -> if OFF: publish an idle/healthy heartbeat and wait
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

Local build:

```bash
./docker/scripts/build-local.sh openclaw-agent:r27.0.1-local
```

For the normal GitHub flow, push a release tag such as `r27.0.1`. The
`container.yml` workflow tests the repository and publishes the image to GHCR.
The production host logs into the private registry once:

```bash
echo "$GITHUB_TOKEN" | docker login ghcr.io -u juanito1337 --password-stdin
```

Use a fine-grained token with read access to packages. Do not store it in Git.

### Fast live-test loop

Branches below `test/**` publish a container automatically after the repository
check. Each image receives a readable branch tag and an immutable
`sha-<12 Zeichen>` tag. Newer pushes to the same test branch cancel an older
in-progress build.

The image stores the complete Git commit as its source revision. The container
workspace marker combines release version and source revision, so a test image
refreshes the packaged source even when `VERSION` is intentionally unchanged.

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
5. rewrites active workspace paths to `/home/node/.openclaw/workspace`,
6. migrates Himalaya `secret-tool` commands to protected files in `/srv/openclaw/secrets`,
7. preserves an existing gateway token/password or creates one protected token when the legacy gateway used no authentication,
8. points the mail classifier at the container-owned Ollama priority proxy,
9. validates required files and every staged SQLite database before publishing anything below `/srv/openclaw`,
10. adds the mail-agent Nextcloud section only when all three Nextcloud credentials exist and the section was missing,
11. creates a verified backup of an existing `/srv/openclaw` state before a remigration publishes its staged result,
12. prefers credentials matching the explicit gateway authentication mode and safely replaces an incompatible stale container secret in the staged copy,
13. records the verified legacy archive, archive member and SHA-256 for a later automatic rollback,
14. leaves the original live directory untouched until the Docker deployment is verified.

Historical sessions and trajectories are not rewritten; only active configuration
files are changed. A repeated migration preserves the previously recorded
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
./scripts/deploy.sh r27.0.1
```

The deployment sequence is deliberately strict:

1. pull the target image and pin its immutable registry digest,
2. stop every writer,
3. create an optional external snapshot when a hook is configured,
4. run SQLite quick checks,
5. archive state/config/secrets,
6. verify SHA-256 and extract the archive into a temporary restore test,
7. start only Ollama proxy and gateway,
8. run version/doctor/dry-run checks,
9. process at most `OPENCLAW_WRITE_TEST_LIMIT` real messages when enabled,
10. start mail, sync and supervisor workers only after success,
11. verify worker health and the current job heartbeat after the workers have
    actually started.

Any failing command triggers `rollback.sh` automatically.

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
./scripts/deploy.sh r27.0.1
```

`refresh-deployment.sh` updates `compose.yaml`, `.env.example` and deployment
scripts. It does not overwrite the productive `.env` or active local hooks.

Tool code plus release-owned defaults and baseline policies are read from the
new image on every update. Persistent `tools.toml` and `policies.toml` files are
instance overrides; account/resource selections and explicit permission grants
remain outside the image. New write permissions are never granted by an image
update. For the direct mail tools, approve the required `read`, `move` and
`forward` permissions once with:

```bash
cd /srv/openclaw/deployment
docker compose --env-file .env --profile tools run --rm agent-cli \
  /home/node/.openclaw/workspace/scripts/assistant.sh \
  setup mail-move --approve-permissions
```

## 7. Manual rollback

List release backups:

```bash
ls -1 /srv/openclaw/backups/releases
```

Restore one backup:

```bash
/srv/openclaw/deployment/scripts/rollback.sh \
  20260728T120000Z_r27.0-to-r27.0.1
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
  /home/node/.openclaw/workspace/scripts/assistant.sh status
```

The agent's `jobs on/off/status` commands remain available. In container mode
they change a persistent desired-state file; the mail, sync, portfolio, monitor
and supervisor workers observe it without requiring systemd inside the
containers. Mail, sync, portfolio and monitor runs enter the shared adaptive
scheduler; the supervisor remains outside it.

```bash
docker compose --env-file .env --profile tools run --rm agent-cli \
  /home/node/.openclaw/workspace/scripts/assistant.sh scheduler status
```

## Backup boundaries

The release backup includes local state, configuration and secrets. It does not
contain the full remote mailbox or Nextcloud server. With external hooks disabled,
a rollback restores the local agent state and previous image but cannot undo an
already successful remote mail move or CalDAV/CardDAV/Nextcloud write.
