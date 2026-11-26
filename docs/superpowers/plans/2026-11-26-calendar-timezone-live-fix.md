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

- [x] Add failing service/connector regressions for exact property-level 404, invalid DAV shape/scope, HTTP/transport failure and invalid timezone observance/rule evidence.
- [x] Expose only allowlisted `reason`, `property_status` and `http_status` alongside the existing failure `code`; keep incomplete/absence behavior unchanged.
- [x] Run the targeted calendar and scoped-access suites and review the diagnosis delta.
- [x] Build a uniquely identified local immutable image and run only the registered status/today commands through an isolated agent-cli overlay against the currently selected calendar.
- [x] Record bounded technical results, including exact resource scope and observation timestamp, without event contents.

## 2. Correct the observed cause

- [x] Record the precise observed failure and its supporting protocol evidence below before implementing the fix.
- [x] Add a synthetic regression reproducing that response/definition and independently expected local-day results; observe the failure against the old behavior.
- [x] Make the smallest correction that passes the regression while preserving unknown/mismatch fail-closed cases.
- [x] Verify recurrence, floating/all-day, 23/25-hour days, partial errors, exact scope and empty-day evidence. Update the existing reference/ADR where the accepted evidence changes.
- [x] Review independently and repeat the isolated live check until the real selected calendar returns a complete, untruncated day with no resource errors.

## 3. Publish and deploy

- [ ] Regenerate release-owned contracts/manifests through existing scripts; run repository and installed-wheel checks.
- [ ] Commit the reviewed source, fast-forward main and wait for the exact SHA's full container CI, reproducibility, scans, signatures, attestations and published-image smokes.
- [ ] Confirm production baseline, existing desired jobs, free capacity and legacy writer exclusion; back up the versioned host bundle.
- [ ] Refresh the bundle and deploy the three verified immutable digests, using the read-only product smoke mode for this invocation and restoring its original setting afterwards.
- [ ] Require a verified local release backup before activation; preserve automatic rollback on any product smoke failure.
- [ ] Verify live product/source identity, all roles, jobs, antivirus, model coordinator and the exact registered calendar daily query. Record the resulting production receipt.

## Live root-cause evidence

The previous rollout on 2026-11-23 returned `calendar-timezone-unverified`, one positive event, `complete=false`, and no absence proof. The exact lower-level cause was not observable. Today's baseline verifies production revision `807c374437ff470f1ce81e171664c466f9e247f0`, product `3.4.0-r29.0.6`, legacy selected calendar access and configured `Europe/Berlin`. These facts do not prove the remote timezone property.

The immutable diagnostic source `4a0951f` received a valid, selected-collection
`calendar-timezone` property-level 404 at `2026-11-26T14:12:39Z`. The registered
query still returned one event and correctly remained incomplete. Receipt:
`build/calendar-timezone-20261126/live-timezone-diagnosis.json`; no event contents
were recorded. RFC 4791 makes this collection property optional. Its absence
does not establish a server timezone; Sabre expansion can erase the original
floating/UTC distinction.

The first guarded fallback candidate `fd1447d` retained the one positive live
event but exposed additional provenance and omitted-series failures. Its full
repository and installed-wheel checks were interrupted with exit 143 before
further source changes; they are not completion evidence. A diagnostic follow-up
`b4f38f8` at `2026-11-26T14:41:21Z` classified eight embedded timezone rule
failures, two invalid standalone durations and one omitted unbounded yearly
TZID series. A conservative day-nonoverlap proof is required before irrelevant
historical objects can be excluded. The normal three-role pinned Docker build
of `fd1447d` passed, including the repaired zstd package pin; no production
container was activated.

The independently reviewed candidate `d7f8f63` passed the isolated live read at
`2026-11-26T14:53:07Z`: one event, `complete=true`,
`results_may_be_truncated=false`, no resource errors, all selected resources and
the exact 2026-11-26 day in Europe/Berlin. Receipt:
`build/calendar-timezone-20261126/d7f8f63-live-today.json`. All 184 combined
calendar, timezone, response-bound and managed-foreground tests passed before
that immutable candidate was built. Production was not changed by this test.

## Correction invariants

Only the exact scoped empty `calendar-timezone` property in a 404 propstat
activates the alternative evidence path. Read the original VEVENT objects with
the existing scoped no-time-range REPORT, run a fresh expanded day REPORT, then
read the original objects again. Bound each body to 4 MiB and each raw snapshot
to 1,000 objects. Require identical complete href, strong ETag and UID maps
around expansion. Expanded objects must match those originals. A malformed,
limited, redirected, changed or denied response never proves an empty day.

Project standalone original events locally in the configured day timezone.
Use server-expanded recurrence only when every original temporal property is
independent of the missing collection timezone; unsupported floating or custom
timezone series remain incomplete. This does not introduce a recurrence engine,
change the remote calendar, or expand account permissions. Both legacy and
active policy paths preserve the selected resource and publication fence.

## Independent runtime prerequisite

Before any candidate activation, the existing production model coordinator
reports its upstream version probe timing out. It is reachable with an empty
queue but unhealthy; normal deployment does not restart the external model
server, and rollback also requires that upstream. The registered status must
recover before the downtime window. This is independent of the calendar error.
