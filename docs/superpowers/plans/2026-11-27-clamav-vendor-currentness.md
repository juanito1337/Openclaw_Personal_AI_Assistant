# ClamAV vendor currentness implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans. Execute with behavioral RED/GREEN tests and an independent final review.

**Goal:** Replace signature-build-age gating by recent official vendor equality and automatically adopt newly offered signatures.

**Architecture:** One updater publishes bounded currentness evidence in the shared database volume. Offline consumers require matching disk and attested resident generations; deployment can bootstrap by a read-only live vendor comparison.

**Tech Stack:** Existing Python standard library, Alpine BusyBox DNS lookup, FreshClam, immutable Docker images.

**Spec:** `docs/superpowers/specs/2026-11-27-clamav-vendor-currentness-design.md`

## Global Constraints

- Poll 300s; probe timeout 10s; proof TTL 660s; vendor TXT maximum age 43200s, no future timestamps.
- No new worker/daemon network or write permissions. No arbitrary HTTP signature downloads.
- Failed, missing, stale, contradictory or partially applied proof is not currentness.
- Single signature writer; official signed downloads remain FreshClam-owned.
- Preserve source manifests, component inventory, installed-wheel tests and signed deployment rollback.
- Existing explicit user authorization covers this policy replacement and the pending main/PC rollout.

## Review Focus

- Main/bytecode updates with unchanged daily must invalidate resident proof and scan cache.
- A daemon restart or reused socket path cannot resurrect a previous resident receipt.
- Vendor regression, stale DNS metadata, timeout and CDN lag never renew a false success.
- Bootstrap from the current old runtime has no vendor receipt and must remain deployable.
- Failed updater/backup/deploy must restore the prior updater state without two writers.

## Shared interfaces (owned by Task 1)

`personal_assistant.clamav_vendor` provides:

- `VendorError(code: str)` with `.code` (safe technical code).
- `query_vendor(*, now: float | None = None) -> dict`: returns `{source, checked_at, record_timestamp, versions}`; versions maps all three groups to positive integers.
- `database_snapshot(database_dir: Path) -> dict`: returns `{versions, files, generation}`. `files[group]` contains `name, size, mtime_ns, ctime_ns, inode, device, sha256`. Generation is deterministic SHA-256 of versions plus content digests.
- `disk_matches(database_dir: Path, snapshot: dict) -> bool`: bounded header/stat checks; never follows symlinks.
- `verify_snapshot(database_dir: Path, snapshot: dict) -> None`: authenticate all three CVD/CLD databases through pinned libclamav in a child with a 120-second deadline, retaining the same file identity before and after. Raise VendorError on any failure.
- `publish_status(database_dir: Path, status: dict) -> None`: fixed atomic `openclaw-vendor-status.json`, <=16384 bytes, mode0640. Adds schema1. Does not grant success by itself.
- `read_status(database_dir: Path) -> dict`: bounded structural read, raises VendorError on invalid/missing evidence.
- `vendor_readiness(database_dir: Path, *, now: float | None = None) -> dict`: returns `{ok, state, reasons, source, checked_at, record_timestamp, versions, database, generation}` where possible. Current requires state `current`, fresh vendor evidence, disk equality and exact three-version equality.
- `python3 -P -m personal_assistant.clamav_vendor --check`: read-only live vendor + local snapshot comparison, JSON and exit0 iff equal; no receipt required/written. Default database `/var/lib/clamav` or `CLAMAV_DATABASE_DIR`.

Status receipt: `{schema:1, source, checked_at, record_timestamp, versions, state, database?, error?}`. States `current`, `update-required`, `updating`, `failed`; failure never preserves an earlier current state.

### Task 1: Vendor evidence core (root)

- [x] RED: old-but-current accepted, all3 versions parsed, stale/future/malformed/oversized DNS and receipts rejected, missing/ambiguous/symlink files rejected, generation/file-change checks.
- [x] Implement shared APIs and readonly CLI; BusyBox DNS-only subprocess has hard timeout and bounded output validation.
- [x] GREEN focused tests and inspect live read-only candidate behavior later in built image.

### Task 2: Updater and deployment lifecycle

Own `personal_assistant/clamav_update.py`, `docker/clamav-update.sh`, Compose/deployment integration and associated tests.

- [x] RED: same old latest skips unnecessary download; new any-group publishes update-required before immediate FreshClam; download/CDN/probe failures remain blocked; lock serializes; periodic updates remain bounded.
- [x] Implement `python3 -P -m personal_assistant.clamav_update --once` and normal loop. Shared-volume flock covers the whole pass. Preserve FreshClam cooldown, authenticate disk after bootstrap/download, verify vendor+disk after success, no downgrade. Periodic same-generation maintenance rehashes without FreshClam or scanner interruption.
- [x] RED/GREEN deploy from no receipt, stop old updater before one-shot, restore prior state on prebackup/deployment failure; use vendor `--check` for readonly preflight and new updater `--once` for publication.
- [x] Expose necessary nonsecret fixed paths/budgets; keep mounts/roles confined.

### Task 3: Resident evidence and consumers

Own `clamav_health.py`, `clamd_health.py`, `clamd_runner.py`, `antivirus.py`, `docker/clamd.sh` and associated tests.

- [x] RED: old matching vendor generation accepted; stale/unknown/newer vendor blocked; main/bytecode-only change reloads; failed daemon startup cannot attest.
- [x] Implement supervised daemon start/restart and atomic resident proof in existing socket volume, bound to actual socket and child instance. Stop/expiry removes eligibility.
- [x] Make disk/daemon/registered readiness consume shared vendor proof and require actual resident generation; preserve build age as telemetry only.
- [x] RED/GREEN scan-cache generation binding and generation change during scan; diagnostic boundedness and explicit legacy-unverified outcome.

### Task 4: Review, full validation, publication and PC acceptance

- [x] Update operating docs/settings diagnostics and release history, regenerate source manifest and component inventory.
- [x] Independent task/interface and full diff review; fix demonstrated findings with regressions.
- [ ] Run required source and isolated wheel checks; publish main and wait exact-revision CI and signed images.
- [ ] Verify read-only candidate vendor preflight, host deployment bundle backup/refresh, local release backup and rollback protections, then deploy.
- [ ] Verify current product/runtime identity and completed Mail, its own Index, and subsequent Sync with matching generation.
