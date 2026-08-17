# Job control, failure reporting and the ON switch

The job controller keeps a persistent **desired state** separate from the observed
Docker worker or legacy systemd state. This allows the assistant to distinguish:

- `ON`: intended to run and timer is enabled/active,
- `OFF`: deliberately disabled by the user,
- `DEGRADED` or `FAILED`: intended to run but unavailable, inactive or unhealthy.

## Commands

```bash
./scripts/assistant.sh jobs status --target all
./scripts/assistant.sh jobs check --target all --deep
./scripts/assistant.sh jobs alerts
./scripts/assistant.sh jobs on standard
./scripts/assistant.sh jobs restart standard
./scripts/assistant.sh jobs off standard
```

`standard` contains the supervisor and automatic mail processing. The optional
knowledge sync is selected explicitly with `sync` or together with `all`. The
optional portfolio quote worker is selected explicitly with `portfolio` or
together with `all`; it defaults to OFF.

The technical monitor is a standard job and records a live performance snapshot
every hour. In the container runtime, mail, sync, portfolio and monitor workers
enter the persistent adaptive scheduler before starting. The supervisor never
enters that queue.

The supervisor remains active when productive jobs are deliberately switched off.
This is intentional: without an independent monitor, an inactive job cannot report
that it is inactive.

## Safe repair boundary

A check normally observes and reports only. There is one narrow automatic
recovery. In the Docker runtime it belongs to the single `mail-worker`, before
that worker starts its productive child. The supervisor remains read-only. In
the frozen legacy runtime the controller retains the equivalent ordered unit
recovery. When every required mail health check is healthy and the only
production blocker is a missing or stale successful dry-run fingerprint, it may:

1. run `mail-agent.sh run --dry-run --no-digest --limit 5`,
2. require return code 0, valid JSON, no errors and successful actions,
3. verify the machine-readable production gate again,
4. start the normal productive mail child without `--force` (or reset/start the
   existing service in the legacy runtime),
5. emit an OpenClaw event reporting success or failure.

A failed automatic dry-run is not repeated for 30 minutes, preventing a retry loop.
All other `on` and `restart` actions require an explicit user instruction and
operate only on the fixed job allowlist. In the active Docker runtime they change
the persistent desired-state file consumed by the worker loops; they do not install
or enable host units.

The productive preflight may also run the existing setup only when configured
`Agent/...` folders are missing. It never changes credentials, forwarding, policies,
permissions or antivirus behavior.

## Containerbetrieb und Legacy-Kompatibilitaet

The active container runtime uses:

```bash
./scripts/assistant.sh jobs on standard
```

This command requires an explicit request and changes only the container
desired-state contract. Native user units and the old interval helper are frozen
under `legacy/systemd/`, verified by `legacy/systemd/manifest.json` and reserved for
a verified rollback. The helper additionally requires
`OPENCLAW_ENABLE_LEGACY_SYSTEMD=YES`; it is not a deployment entrypoint. A legacy
writer and the container mail writer must never run together.

The supervisor records lightweight checks every five minutes. It never opens a
domain database or runs mail recovery in the container runtime. A newly detected
or resolved alert is atomically queued below
`shared/coordination/gateway_events/`. Only the gateway-local relay consumes that
bounded queue and invokes OpenClaw through `ws://127.0.0.1`; worker roles neither
receive a gateway credential nor disable the non-loopback WebSocket protection.
The heartbeat then runs the deeper check and reports the concrete evidence. An
unchanged active alert is not queued repeatedly. Local state is stored at:

```text
personal_assistant/data/job_control.json
```

It also checks `personal_assistant/data/work_scheduler.sqlite3` when present.
Expired leases or missed scheduler deadlines use the same deduplicated alert
path. Queue state is available through:

```bash
./scripts/assistant.sh scheduler status
./scripts/assistant.sh scheduler doctor
```

## Tool failure procedure

When a tool command fails, the agent must:

1. preserve and report the exact command result,
2. run the registered status/doctor command,
3. run `jobs check --target all --deep` for service-backed tools,
4. report diagnosis and safe next step,
5. allow only the documented stale dry-run auto-recovery; otherwise restart only after an explicit user request.

## Reporting boundary

The supervisor can persist alerts while OpenClaw is unavailable. Delivery through
OpenClaw requires the gateway and the configured conversation channel to be
working. A complete host or gateway outage therefore needs an independent external
watchdog if an out-of-band notification is required.

The portfolio worker checks every 15 minutes and honours the configured 15- or
30-minute due interval. Its health check fails closed when a held-position quote
is unavailable or critically stale. This produces the same deduplicated job
alert and OpenClaw system-event path as other service failures.

Adaptive priority never overrides portfolio freshness indefinitely. Deadline
urgency and starvation aging eventually outrank a temporary chat-topic boost.


## Mail recovery ordering

For a stale production fingerprint the container mail worker uses this fixed
sequence while it already owns the only writer lease:

1. run `mail-agent.sh production-check`,
2. require the machine-readable `auto_recoverable` flag,
3. run the bounded maintenance-priority dry-run,
4. recheck `production-check`,
5. start the unchanged productive drain command only after a green gate.

A lock collision is transient. It is retried up to three times and does not write
the 30-minute recovery cooldown. The lock file is never deleted. Genuine model,
JSON or classification failures retain the existing cooldown.

The legacy controller first stops timer and service and verifies the real lock
before the same dry-run. That compatibility path is not used by the container
supervisor.
