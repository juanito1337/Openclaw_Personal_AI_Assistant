# Antivirus gate

The Personal Assistant uses ClamAV as a mandatory fail-closed gate. Host and
container runtimes share the same scan contract but use different daemon
discovery:

- a legacy host installation may use `clamav-daemon.service` and `clamdscan`;
- the Docker stack uses the isolated `openclaw-clamd` process and a private Unix
  socket at `/run/clamav/clamd.sock`;
- `clamscan` is a visible, slower fallback for bounded existing operations. It
  never proves that the productive mail index is ready.

`clamdscan` is a client, not the resident daemon. Merely finding that binary is
therefore not a daemon-health result.

## Container responsibility split

```text
database.clamav.net
        |
        v
openclaw-clamav-update       egress, clamav-db rw
        |
        v
clamav-db                    atomically updated signatures
        |
        v
openclaw-clamd               no network, clamav-db ro, socket rw
        |
        v
clamav-socket                private Unix socket
        |
        +--> gateway          ro mount, supplemental group 101
        +--> mail-worker      ro mount, supplemental group 101
        +--> agent-cli        ro mount, supplemental group 101
```

The daemon receives no Personal-Assistant secrets, mail state, Nextcloud state,
Docker socket or published port. Its root filesystem is read-only, all Linux
capabilities are dropped and it runs as `100:101`. A one-shot initializer owns
the sole narrow root exception needed to prepare the otherwise private socket
volume.

Clients send bytes through the bounded ClamAV `INSTREAM` protocol. They do not
share temporary mail files with the daemon. The internal Python port can express
only `PING`, `VERSION` and `INSTREAM`, not shutdown, reload, file-path scans or
another administrative command.

## Security order

1. Export the original RFC822 message to a private client-local temporary file.
2. Stream and scan the complete raw message.
3. Parse the mail only after a clean result.
4. Extract and scan every physical attachment individually.
5. Block forwarding, calendar commands and invoice actions on infection or scan error.
6. Re-check selected invoice PDFs immediately before ActionPlan creation.
7. Scan every controlled workspace upload before a Nextcloud write.

Clean-cache entries are keyed by SHA-256 and the exact active engine/signature
identity. The transport is recorded separately. A signature update therefore
invalidates older clean evidence without relabelling existing cache rows.

## Readiness states

`security antivirus doctor` deliberately separates:

- `scanner_works`: the requested live payload was scanned successfully;
- `daemon_ready`: the configured daemon answered PING and VERSION;
- `signatures_fresh`: the active daemon identity is inside the allowed age;
- `transport`: `daemon-stream`, host `clamdscan-systemd` or `standalone`;
- `fallback_used` and its typed reason;
- `index_readiness.index_ready`.

A successful standalone scan may set `scanner_works=true`, but it never sets
`daemon_ready=true` or `index_ready=true`. Backfill and reconciliation stop
before the first Raw-Mail fetch when the daemon preflight fails. A partial run
cannot publish complete coverage, tombstone mail or authorize a negative search
claim.

## Outcomes

- clean: processing continues;
- infected: mail moves to `Agent/Virusverdacht` through the existing controlled
  mail-writer path;
- scanner error, unavailable daemon where required, stale signatures or size
  limit: fail-closed;
- no infected or suspicious content is deleted or submitted to an external
  service.

## Commands

Host compatibility setup:

```bash
sudo bash scripts/setup-antivirus-host.sh
```

Registered diagnostics:

```bash
./scripts/assistant.sh security antivirus doctor
./scripts/assistant.sh security antivirus self-test
./scripts/assistant.sh security antivirus scan \
  --file personal_assistant/data/workspace_outbox/example.pdf
```

The manual scan accepts only files inside the controlled workspace outbox.
Automatic mail scans use private client-local temporary files with mode 0600.

Hermetic M14 test after building the maintenance role:

```bash
OPENCLAW_M14_MAINTENANCE_IMAGE=openclaw-agent:m14-maintenance \
  ./scripts/check-m14-integration.sh
```

The M14 test uses a synthetic signature, `network=none` and temporary Docker
resources. It does not mount the productive signature volume or process private
mail.

## Productive index boundary

A green repository or container test does not activate the mail index. The
productive order remains:

1. verify signed image and exact revision;
2. create and restore-test the local release backup;
3. deploy and prove `clamd` readiness, Clean and EICAR;
4. run one explicitly approved bounded folder canary;
5. obtain a separate approval for the dimensioned full backfill;
6. accept only a complete, fresh, authoritative generation;
7. perform positive, Body, Move, Recent and controlled negative search checks;
8. obtain a separate approval before `jobs on mail-index`;
9. observe scanner, signatures, resources and incremental index for seven days.

The complete plan and prompts are in
[`MAIL_ANTIVIRUS_INDEX_ROLLOUT_ROADMAP.md`](MAIL_ANTIVIRUS_INDEX_ROLLOUT_ROADMAP.md).
