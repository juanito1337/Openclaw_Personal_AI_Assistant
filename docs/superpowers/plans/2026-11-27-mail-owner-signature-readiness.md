# Mail Owner Signature Readiness Implementation Plan

> **For agentic workers:** Use systematic debugging, test-driven development and verification before completion. Jan explicitly authorized implementation, main publication, PC deployment and completed Mail/Index/Sync verification.

**Goal:** Explain and resolve the degraded Mail-Owner without weakening the existing ClamAV gate, and prevent misleading healthy diagnostics.

**Architecture:** Preserve the 172800-second signature age limit and single writer. Use the actual signature timestamp for disk and resident-daemon health. Expose current scanner readiness independently from the integrity of the previously imported index. Fresh upstream signatures remain a real prerequisite, not something a code change may invent.

**Tech Stack:** Existing Python, Bash, Docker and pytest; no new dependencies.

**Spec:** User request and AGENTS.md integration diagnostics/runtime contracts. Live evidence: index attempt 2026-11-27 07:00:51–07:00:53 UTC failed in `require_index_antivirus_ready`; active and disk signatures 28194 dated 2026-11-25 06:26:59 UTC exceeded 48 hours. Updater succeeded; official DNS still advertised 28194. Docker health incorrectly used the later download mtime.

## Global Constraints

- No increased age threshold, changed safety gate, replacement signature source, forced timestamp or unverified freshness.
- No remote mail mutations or direct IMAP, no production source patching.
- Keep valid previous index data available; distinguish search integrity from ability to reconcile now.
- Preserve verified immutable release, backup, rollback and single-writer deployment.
- Separate historical successful job runs from new current evidence.

## Review Focus

- A newly copied old signature must remain stale.
- A responsive daemon with old resident signatures must fail readiness even when disk files are fresh.
- Missing or malformed timestamp evidence must fail closed.
- A previously healthy index must not imply current ingestion readiness; useful partial evidence must remain.
- A deployment must not stop the running stack when a known fresh-signature prerequisite fails.

### Task 1: Signature health

Files: `personal_assistant/clamav_health.py`, `personal_assistant/clamd_health.py`, `personal_assistant/antivirus.py`, focused ClamAV tests.

- [x] Write and observe failing tests for old signature/fresh mtime, resident lag, invalid timestamp and exact age boundary.
- [x] Share timestamp-age interpretation using an existing lightweight maintenance module; preserve all safety limits.
- [x] Run focused tests and independently review.

### Task 2: Current index diagnostics

Files: `personal_assistant/services/mail.py`, matching service tests and domain documentation.

- [x] Reproduce healthy imported-index diagnostics hiding unavailable scanner prerequisites.
- [x] Add bounded current index-readiness evidence to registered doctor, without changing stored-search eligibility or scanning mail.
- [x] Test missing/failed/stale and healthy readiness while retaining index integrity evidence.

### Task 3: Release and activation

- [x] Review pre-deployment freshness verification; validate before stopping writers.
- [ ] Document cause and corrective behavior; update release manifests.
- [ ] Run full source and installed-wheel checks, review, publish main and await exact-head CI/signed images.
- [ ] Verify current upstream/active signatures, predecessor and rollback health, then deploy with verified backup.
- [ ] Observe completed Mail, own Index and corresponding Sync runs and final registered health. If upstream remains stale, report the exact external blocker without claiming activation or completion.

## Decisions

The signature gate is behaving correctly. Code changes repair contradictory diagnostics, not the upstream publication schedule. Unrelated telemetry artifacts found during diagnosis are excluded from this change; no historical telemetry is deleted or reclassified.

Review corrections: diagnostic PING/VERSION requests have an absolute five-second
deadline each; productive INSTREAM budgets are unchanged. The immutable daemon
explicitly emits UTC. For legacy systemd, a matching local database identity
obtained under UTC supplies the build timestamp; an unknown/mismatched identity
cannot authorize freshness. Registered readiness includes the proof source.
Both review findings were reproduced before correction with behavioral tests.

Existing Docker deployments check the candidate against the selected, labelled
signature volume read-only before stopping writers. Fresh/legacy installations
retain their existing initial bootstrap. Permanent host installer updates use
the supported `refresh-deployment.sh` path with a separate verified backup of
the changed release-owned installer files before activation.
