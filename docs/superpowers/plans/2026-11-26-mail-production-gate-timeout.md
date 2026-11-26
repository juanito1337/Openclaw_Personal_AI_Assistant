# Mail Production Gate Timeout Implementation Plan

> **For agentic workers:** Use TDD and independently review the integrated change.

**Goal:** Complete bounded mail release validation without killing healthy model
work early, then verify Mail, Index and Sync on the PC.

**Architecture:** Give the existing single-batch dry-run a cooperative deadline.
Both automatic recovery callers use the same bounded recovery contract; each
model attempt recalculates its budget and a partial validation fails closed.

**Tech Stack:** Python 3.11+, unittest/pytest, existing immutable Docker installer.

**Spec:** `docs/superpowers/specs/2026-11-26-mail-production-gate-timeout-design.md`

## Global constraints

- Preserve five-message validation, fingerprint, AV, writer lock and approvals.
- Production changes only through the established signed-image deployment.
- Technical receipts contain no mail contents or secrets.
- Baseline is the clean, already fully tested `5a6022c` worktree; new branch
  `codex/fix-mail-production-gate-20261126`.

## Review focus

- A schema/token retry consumes elapsed time rather than resetting its deadline.
- Partial dry-runs and model timeouts never stamp a valid release gate.
- Readiness failure never starts recovery or productive work unless allowlisted.
- A watchdog failure identifies its phase and cannot leave a child holding pipes.
- A new worker heartbeat never substitutes for a completed index generation.

## Tasks

### 1. Per-attempt model budgets

Files: `mail_agent/classifier.py`, `tests/test_r261_ollama_reliability.py` or a
dedicated `tests/test_mail_recovery_runtime.py`.

- [x] Add a fake-clock transport regression: first response consumes budget,
  a schema/token retry must shrink its headers/socket budget or stop before HTTP.
- [x] Observe RED, move the existing timeout calculation into each real attempt,
  observe GREEN with the classifier regression suite.

### 2. Bounded single-batch validation

Files: `mail_agent/app.py`, `mail_agent/cli.py`, relevant run/CLI tests.

- [x] Test the real single-batch run with a simulated deadline exhaustion and
  verify explicit error/failed dry-run stamp; test a completed batch still passes.
- [x] Add optional `max_runtime_seconds` and completion reserve to `MailAgent.run`;
  wire the existing CLI runtime options for dry-runs without switching to drain.
- [x] Preserve completed results, clear the deadline in finally, and fail dry-run
  validation when timeout metrics show incomplete model work.

### 3. Recovery phase contract

Files: `personal_assistant/mail_worker.py`, `personal_assistant/job_control.py`,
small shared recovery constants/helper if needed, `tests/test_gateway_event_relay.py`,
`tests/test_job_control.py` and targeted process tests.

- [x] Test distinct readiness/dry-run/recheck failures; no productive command
  after failure; persist fingerprint-bound cooldown on a recovery timeout.
- [x] Give the five-message recovery the explicit 900-second runtime/reserve and
  960-second outer watchdog; keep readiness separate. Both callers agree.
- [x] Bound cleanup of captured children, with a real synthetic-process test.
- [x] Confirm a fully completed dry-run and successful recheck open the gate.

### 4. Review, release and live acceptance

- [x] Independently review the integrated diff; update existing runtime/mail
  documentation and changelog with the final behavior.
- [ ] Run affected regressions, full repository checks, installed-wheel/container
  checks required by release scripts, and exact-head CI/signature verification.
- [ ] Build/publish the versioned fix and use the authorized normal deployment
  with verified backup and rollback; never modify productive source in place.
- [ ] Collect native Mail/Index/Sync completion, current index-generation/import
  evidence; report any actual failure instead of a health-only success.

## Ledger

- Investigation: phase overlap proven by registered worker gate records and
  technical performance telemetry. The operator CLI `mail dry-run --limit 5`
  measured a complete live validation through the normal coordinator.
- Ruling: implementation choices within Jan's explicit repair request are routine;
  proceed without another approval round, while preserving concrete write gates.
- Live calibration: registered `mail dry-run --limit 5` completed in 412.08 seconds
  (20:02:04–20:08:56 UTC), all five actions successful, no errors/fallback/timeouts.
  Two model requests consumed 404.46 seconds. The registered CLI deliberately enforces
  interactive priority, so this confirms inference duration but does not replace
  final maintenance-priority worker acceptance. The selected 900/60/960 budget is
  retained; both earlier 240/300 watchdogs were below this healthy run's duration.
- Task 1: two fake-clock retry regressions RED, then 41 classifier/queue/drain
  tests and six subtests GREEN. No runtime/model-priority configuration changed.
- Task 2: six CLI/run regressions RED, then 27 affected tests GREEN. Independent
  review identified finalization/cleanup and partial-result edge cases; fixes are
  being verified before integration. The same dry-run completion rule also covers
  the existing drain entry, without changing productive drain continuations.
- Task 3: nine expected failing reports RED, then 70 tests and three subtests
  GREEN, including real descendant-pipe cleanup. Captured-stage cleanup only;
  broader scheduler/owner shutdown behavior is outside this repair.

- Follow-up review: cooldown now binds only the verified current fingerprint;
  real failed stamps cannot reset it. Both recovery callers share bounded process
  group cleanup. In containers, every jobs-check role observes the single mail
  owner without quiescing or running recovery. 78 tests and nine subtests passed.
- Follow-up app review: 12 additional regressions reproduced work beginning in
  the reserve and spam-review timeout fallback granting release. All CLI stamp
  writers now share completion validation; 142 tests and 16 subtests passed.
- Diagnostic boundary: if the hard deadline passes during the append-only
  telemetry finish itself, that isolated record may already say completed. The
  returned CLI error and persisted failed gate remain authoritative. Final live
  acceptance uses the gate, actual owner completion and imported index generation.
- Final independent review: all five findings resolved; 79 tests and nine
  subtests passed. The first full repository and installed-wheel runs each found
  only the outdated jobs-check description in the reviewed golden contract
  (2,575 other tests passed). Updated that exact description to match the new
  container ownership contract before repeating release checks.
