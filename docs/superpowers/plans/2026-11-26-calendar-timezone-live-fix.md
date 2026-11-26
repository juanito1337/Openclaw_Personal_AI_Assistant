# Calendar timezone live fix implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Diagnose the real Nextcloud timezone failure, correct its proven cause, verify a complete daily agenda against the selected live calendar, and deploy the reviewed main revision successfully.

**Architecture:** Extend the existing registered calendar read path with bounded, content-free error evidence. Reproduce the observed server case in hermetic tests before changing timezone interpretation. Live candidate commands run in an immutable, short-lived agent-cli container with read-only production configuration and disposable database/log mounts; production activation uses the existing signed-image installer and verified backup/rollback path.

**Tech Stack:** Python, CalDAV, existing typed CLI/native-tool contract, pytest, immutable Docker role images, GitHub Actions and Sigstore.

**Spec:** `docs/architecture/adr/0052-belegte-kalender-tagesabfrage.md`, `docs/INTEGRATION_DIAGNOSTICS.md`, and Jan's explicit 2026-11-26 instruction to fix, verify live and deploy main.

## Global constraints

- Preserve exact selected-resource, account, rights and current-day evidence binding.
- Never infer a server timezone from a local setting or accept incomplete results as absence proof.
- Keep successful partial events; diagnostics contain no credentials, raw DAV/ICS or event content.
- No remote calendar/configuration write, permission expansion, productive source patch or second writer.
- A failed live acceptance blocks rollout; production smoke failure requires verified local rollback.

## Review focus

- Distinguish a missing property from denied, malformed, wrong-resource or unsupported evidence.
- Cover valid server response variations without accepting ambiguity or cross-resource evidence.
- Preserve floating, all-day, recurring and DST day-boundary semantics.
- Keep active-policy transport restrictions and legacy resource selection intact.
- Verify the actual live candidate before the production downtime window.

## 1. Diagnose without changing completeness

**Files:** `personal_assistant/calendar_agenda.py`, `personal_assistant/connectors/nextcloud/calendar.py`, the error projection in `personal_assistant/service.py`, and calendar agenda/today tests.

- [ ] Add failing service/connector regressions for exact property-level 404, invalid DAV shape/scope, HTTP/transport failure and invalid timezone observance/rule evidence.
- [ ] Expose only allowlisted `reason`, `property_status` and `http_status` alongside the existing failure `code`; keep incomplete/absence behavior unchanged.
- [ ] Run the targeted calendar and scoped-access suites and review the diagnosis delta.
- [ ] Build a uniquely identified local immutable image and run only the registered status/today commands through an isolated agent-cli overlay against the currently selected calendar.
- [ ] Record bounded technical results, including exact resource scope and observation timestamp, without event contents.

## 2. Correct the observed cause

- [ ] Record the precise observed failure and its supporting protocol evidence below before implementing the fix.
- [ ] Add a synthetic regression reproducing that response/definition and independently expected local-day results; observe the failure against the old behavior.
- [ ] Make the smallest correction that passes the regression while preserving unknown/mismatch fail-closed cases.
- [ ] Verify recurrence, floating/all-day, 23/25-hour days, partial errors, exact scope and empty-day evidence. Update the existing reference/ADR where the accepted evidence changes.
- [ ] Review independently and repeat the isolated live check until the real selected calendar returns a complete, untruncated day with no resource errors.

## 3. Publish and deploy

- [ ] Regenerate release-owned contracts/manifests through existing scripts; run repository and installed-wheel checks.
- [ ] Commit the reviewed source, fast-forward main and wait for the exact SHA's full container CI, reproducibility, scans, signatures, attestations and published-image smokes.
- [ ] Confirm production baseline, existing desired jobs, free capacity and legacy writer exclusion; back up the versioned host bundle.
- [ ] Refresh the bundle and deploy the three verified immutable digests, using the read-only product smoke mode for this invocation and restoring its original setting afterwards.
- [ ] Require a verified local release backup before activation; preserve automatic rollback on any product smoke failure.
- [ ] Verify live product/source identity, all roles, jobs, antivirus, model coordinator and the exact registered calendar daily query. Record the resulting production receipt.

## Live root-cause evidence

The previous rollout on 2026-11-23 returned `calendar-timezone-unverified`, one positive event, `complete=false`, and no absence proof. The exact lower-level cause was not observable. Today's baseline verifies production revision `807c374437ff470f1ce81e171664c466f9e247f0`, product `3.4.0-r29.0.6`, legacy selected calendar access and configured `Europe/Berlin`. These facts do not prove the remote timezone property.
