# Docker deployment and rollback (R27.0.1)

R27.0.1 separates the immutable program image from productive state. The image is
replaced during updates; `/srv/openclaw` remains on the host. The release also
fixes native-to-container workspace migration, Himalaya keyring migration,
private CA handling and the ClamAV updater healthcheck.

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

1. disables the old user-level systemd writers,
2. creates an untouched migration archive,
3. copies `~/.openclaw` to `/srv/openclaw/state`,
4. rewrites active workspace paths to `/home/node/.openclaw/workspace`,
5. migrates Himalaya `secret-tool` commands to protected files in `/srv/openclaw/secrets`,
6. adds the mail-agent Nextcloud section only when all three Nextcloud credentials exist and the section was missing,
7. leaves the original live directory in place until the Docker deployment is verified.

Historical sessions and trajectories are not rewritten; only active configuration
files are changed.

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
root-owned `/srv/openclaw` child directories themselves.

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
they change a persistent desired-state file; the mail, sync and supervisor
workers observe it without requiring systemd inside the containers.

## Backup boundaries

The release backup includes local state, configuration and secrets. It does not
contain the full remote mailbox or Nextcloud server. With external hooks disabled,
a rollback restores the local agent state and previous image but cannot undo an
already successful remote mail move or CalDAV/CardDAV/Nextcloud write.
