# Adaptive work scheduler

## Purpose

The scheduler coordinates complete background jobs before they start. It is a
separate layer from the Ollama priority proxy:

- the scheduler chooses between mail, portfolio, knowledge sync and monitoring,
- the Ollama proxy coordinates individual model requests,
- the supervisor stays outside both queues and watches them.

Production container workers use one persistent, non-preemptive background slot.
Running safe work is not killed merely because the user changes topics.

## Commands

```bash
./scripts/assistant.sh scheduler status
./scripts/assistant.sh scheduler doctor
./scripts/assistant.sh scheduler activity
./scripts/assistant.sh scheduler focus --topic portfolio --minutes 30
```

Valid topics are `mail`, `portfolio`, `knowledge`, `planning` and `operations`.
`focus` stores only a local, expiring activity signal. It does not enable a job,
change tool permissions, approve an ActionPlan or authorize external writes.

Relevant interactive CLI commands record their topic automatically. Container
background workers set `OPENCLAW_SCHEDULER_SOURCE=background-worker` and therefore
cannot reinforce their own priority. For chat requests that do not directly call
a topic-specific tool, the agent may call `scheduler focus` when the user's
current intent is explicit.

## Priority model

Every allowlisted job has a fixed policy with:

- topic and base priority,
- queue deadline,
- maximum expected runtime,
- a human-readable purpose.

The effective score combines:

1. base priority,
2. bounded wait-time aging,
3. a bounded, decaying user-topic boost,
4. increasing deadline urgency,
5. starvation protection after a bounded wait.

The queue is persistent in:

```text
personal_assistant/data/work_scheduler.sqlite3
```

Only one live row per job is allowed. Atomic SQLite transactions select the next
job. A granted job receives a lease that the worker renews every ten seconds. An
expired lease is returned to the pending queue; a worker that repeatedly cannot
renew its lease stops its child process so a second job cannot start alongside an
untracked run.

## Common telemetry

The scheduler retains privacy-safe run metadata:

- queued, started and finished timestamps,
- wait time and duration,
- seven-day success rate plus average, maximum, p50 and p95 timings,
- result and exit code,
- bounded technical error code/detail,
- lease attempts and deadline status.

It does not store mail bodies, document contents, prompts, credentials, portfolio
statement contents or provider API keys. Finished records older than 180 days may
be pruned.

The performance monitor incorporates scheduler health and seven-day wait/runtime
aggregates. The supervisor reports stale leases and deadline misses through the
existing deduplicated OpenClaw event path.

## Independence and failure boundaries

The supervisor is never queued. A scheduler failure must therefore remain
observable. Likewise, Docker healthchecks remain independent of queue selection.
A full host, Docker daemon, gateway or notification-channel outage still requires
an external watchdog for out-of-band delivery.

The current immutable production runtime is the container deployment. Legacy
systemd units remain packaged for migration and fallback, but adaptive whole-job
arbitration is performed by the container worker loops.
