# Job control, failure reporting and the ON switch

The job controller keeps a persistent **desired state** separate from the actual
systemd state. This allows the assistant to distinguish:

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

The supervisor remains active when productive jobs are deliberately switched off.
This is intentional: without an independent monitor, an inactive job cannot report
that it is inactive.

## Safe repair boundary

A check normally observes and reports only. There is one narrow automatic recovery:
when every required mail health check is healthy and the only production blocker is
a missing or stale successful dry-run fingerprint, the controller may:

1. run `mail-agent.sh run --dry-run --no-digest --limit 5`,
2. require return code 0, valid JSON, no errors and successful actions,
3. verify the machine-readable production gate again,
4. reset the failed unit and start the normal mail service without `--force`,
5. emit an OpenClaw event reporting success or failure.

A failed automatic dry-run is not repeated for 30 minutes, preventing a retry loop.
All other `on` and `restart` actions require an explicit user instruction and operate
only on the fixed allowlist of packaged user units.

The productive preflight may also run the existing setup only when configured
`Agent/...` folders are missing. It never changes credentials, forwarding, policies,
permissions or antivirus behavior.

## Supervisor installation

The first explicit start installs missing packaged user units without overwriting
existing local units:

```bash
./scripts/assistant.sh jobs on standard
```

The supervisor records lightweight checks every five minutes. A newly detected
or resolved alert also queues an immediate OpenClaw system event; the heartbeat
then runs the deeper check and reports the concrete evidence. An unchanged active
alert is not queued repeatedly. Local state is stored at:

```text
personal_assistant/data/job_control.json
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


## R20.1 mail recovery ordering

For a stale production fingerprint the controller uses this fixed sequence:

1. stop `mail-agent.timer`,
2. stop `mail-agent.service`,
3. query `mail-agent.sh lock-status` and wait for the real `flock`,
4. run the bounded maintenance-priority dry-run,
5. recheck `production-check`,
6. reset failures, enable the timer and start the service asynchronously.

A lock collision is transient. It is retried up to three times and does not write
the 30-minute recovery cooldown. The lock file is never deleted. Genuine model,
JSON or classification failures retain the existing cooldown.
