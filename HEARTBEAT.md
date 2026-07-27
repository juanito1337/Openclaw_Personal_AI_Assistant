# Heartbeat policy

Heartbeat must check the registered job controller and report actionable failures.
It must not claim that a tool is working based only on an earlier message.

Run:

```bash
./scripts/assistant.sh version --verify
./scripts/assistant.sh jobs check --target all --deep
```

Reporting rules:

- If release verification fails, report the exact version/document mismatch. Never infer the installed version from memory.
- If the heartbeat was triggered by an update installation event, include the exact installed version and summarize `changes` from the version command once.

- If `new_alerts` or `active_alerts` are present, report the affected job, exact
  issue, relevant health-check evidence and the safe next command. A supervisor
  system event may have triggered this heartbeat immediately after a state change.
- Clearly distinguish deliberate `OFF` from unexpected `FAILED/DEGRADED`.
- Do not repeatedly report a healthy state.
- The `jobs check` command itself may perform the single allowlisted mail recovery:
  stop timer/service, wait for the real mail lock, run a bounded successful dry-run
  for a stale/missing fingerprint, revalidate and start normally. A temporary lock
  conflict is retried without the long cooldown. Report whether recovery succeeded.
- Do not perform any other enable, restart or productive action during heartbeat.

- Learning diagnostics (`mail learning status`, `mixed-senders`, `conflicts`) are read-only and may be used when a learning problem is reported. Never create or disable a correction folder during heartbeat.
- Calendar/task discovery is read-only but should run only for an explicit discovery request, not on every heartbeat. Never configure a calendar or task list during heartbeat.
- CardDAV contact discovery/list/search are read-only but should run only for an explicit contact request. Never configure an address book or create a contact during heartbeat.
- Invoice OCR status and review lists are read-only but should run only after a reported invoice problem or explicit request. Never run productive backfill, metadata correction or CSV export during heartbeat.
- Deck due-date preview is read-only but should run only after a reported missing-date problem or explicit request. Never run the productive due-date backfill during heartbeat.

- Do not run a full learning evaluation on every heartbeat. Use it only after a reported learning problem, an explicit request, or enough new corrections to justify comparison. Never export a dataset during heartbeat.

Heartbeat must not:

- invoke `--force`,
- execute pending ActionPlans,
- change credentials or permissions,
- enable timers or start services outside the allowlisted `jobs check` mail recovery,
- upload, overwrite, or delete files,
- create, change, or delete calendar events or tasks,
- install plugins, skills, or packages,
- modify source code.

Additional safe checks when needed:

```bash
./scripts/assistant.sh doctor
./scripts/assistant.sh status
./scripts/assistant.sh jobs alerts
./scripts/assistant.sh mail status
./scripts/assistant.sh ollama status
./scripts/assistant.sh ollama queue
./scripts/assistant.sh performance mail --limit 20
```

## R24 health expectations

- `mail-agent.service` may be `activating/start` while its oneshot run is active, but should normally exit inside its 2400-second budget.
- Repeated `run_id` rows must not inflate summaries.
- Rising `upstream_timeouts`, persistent running attempts, or runs reaching the systemd 50-minute limit require investigation.
