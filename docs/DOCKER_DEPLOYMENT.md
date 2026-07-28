# Docker deployment and rollback (R27.0)

R27.0 separates the immutable program image from productive state. The image is
replaced during updates; `/srv/openclaw` remains on the host.

## Host layout

```text
/srv/openclaw/
├── state/                    # mounted as /home/node/.openclaw
├── config/
│   ├── himalaya/
│   ├── mail-agent.env
│   ├── personal-assistant.env
│   └── ollama-priority.env
├── secrets/                  # *.env, never committed
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
./docker/scripts/build-local.sh openclaw-agent:r27.0-local
```

For the normal GitHub flow, push a release tag such as `r27.0`. The
`container.yml` workflow tests the repository and publishes the image to GHCR.
The production host logs into the private registry once:

```bash
echo "$GITHUB_TOKEN" | docker login ghcr.io -u juanito1337 --password-stdin
```

Use a fine-grained token with read access to packages. Do not store it in Git.

## 2. Prepare the host

```bash
sudo ./docker/scripts/setup-host.sh
nano /srv/openclaw/deployment/.env
```

Copy and implement the external backup hooks:

```bash
cp /srv/openclaw/deployment/hooks/pre-deploy.example.sh \
   /srv/openclaw/deployment/hooks/pre-deploy.sh
cp /srv/openclaw/deployment/hooks/restore.example.sh \
   /srv/openclaw/deployment/hooks/restore.sh
chmod 700 /srv/openclaw/deployment/hooks/pre-deploy.sh \
          /srv/openclaw/deployment/hooks/restore.sh
```

The hooks are mandatory by default for a write-enabled product smoke test.
They must snapshot and restore the remote systems that are not part of the local
Docker volume: IMAP, Nextcloud files, CardDAV contacts, CalDAV calendars and
VTODO tasks. A local volume backup alone cannot undo a remote mail move or a
successful CalDAV PUT.

## 3. Migrate the current live installation once

```bash
/srv/openclaw/deployment/scripts/migrate-live.sh --execute
```

The migration:

1. disables the old user-level systemd writers,
2. creates an untouched migration archive,
3. copies `~/.openclaw` to `/srv/openclaw/state`,
4. copies Himalaya and environment configuration,
5. leaves the original live directory in place.

Review `/srv/openclaw/deployment/.env`, especially `OPENCLAW_IMAGE`, the Ollama
upstream and both external hooks.

## 4. First deployment and later updates

```bash
cd /srv/openclaw/deployment
./scripts/deploy.sh r27.0
```

The deployment sequence is deliberately strict:

1. pull the target image and pin its immutable registry digest,
2. stop every writer,
3. create the external snapshot,
4. run SQLite quick checks,
5. archive state/config/secrets,
6. verify SHA-256 and extract the archive into a temporary restore test,
7. start only Ollama proxy and gateway,
8. run version/doctor/dry-run checks,
9. process at most `OPENCLAW_WRITE_TEST_LIMIT` real messages when enabled,
10. start mail, sync and supervisor workers only after success.

Any failing command triggers `rollback.sh` automatically.

## 5. Manual rollback

List release backups:

```bash
ls -1 /srv/openclaw/backups/releases
```

Restore one backup:

```bash
/srv/openclaw/deployment/scripts/rollback.sh \
  20260728T120000Z_r26.4-to-r27.0
```

The current failed state is saved for analysis, the remote restore hook is
called with the recorded snapshot reference, the local archive is restored and
the previous image is started.

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
itself contain the full remote mailbox or Nextcloud server. Keep the external
hook requirement enabled unless the write smoke test is disabled:

```dotenv
OPENCLAW_WRITE_TEST_ENABLED=false
REQUIRE_EXTERNAL_BACKUP_FOR_WRITE_TEST=true
```

Disabling the requirement while allowing writes makes a complete automatic
rollback impossible and is therefore not the recommended production setting.
