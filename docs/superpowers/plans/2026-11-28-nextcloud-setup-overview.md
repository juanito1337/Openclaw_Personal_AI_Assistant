# Nextcloud Setup Overview Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Make the first authenticated Nextcloud setup screen a truthful, editable connection and integration overview.

**Architecture:** Extend the existing operator-only status DTO with bounded permission summaries before pagination. Render the overview in the existing setup tab and retain the existing signed editing/confirmation workflows. Do not change the launcher trust boundary.

**Tech Stack:** Existing Python setup service, vanilla JavaScript/CSS/HTML, pytest, Node test runner and isolated Firefox/WebDriver fixture.

**Spec:** `docs/superpowers/specs/2026-11-28-nextcloud-setup-overview-design.md`

## Global Constraints

- No production deployment or live account/permission changes are part of this request.
- No new dependency, generic RPC endpoint, external write probe or job start.
- No secret value in an overview, diagnostic, browser storage, fixture or log.
- Preserve bounded status responses, signed operator authorization, revisions and explicit account/access confirmations.
- Keep account, component and resource evidence scoped, complete where claimed, and subject to its original freshness limit.

## Review Focus

- A later failed authentication must invalidate an older green overview.
- Counts must cover the whole known permission inventory even when detail rows are paginated.
- A mkdir-only or excluded folder must never appear as an effective file-upload target.
- Browser time passing and disconnects must invalidate positive summary claims.
- Mixed calendar/task collections and same-named resources must retain distinct identities and grants.

---

### Task 1: Bounded account permission summary

**Files:**
- Modify: `personal_assistant/nextcloud_setup_access.py`
- Modify: `personal_assistant/nextcloud_setup.py`
- Test: `tests/test_nextcloud_setup.py`

**Interfaces:**
- Consumes: fully presented `access.rows`, snapshot/account binding and existing freshness fields, before `bound_status` trims rows.
- Produces: `access.overview`, schema version 1, bound to `account_id` and `connection_revision`; `components` keyed by VEVENT, VTODO, VCARD and FILES with known, selected, readable, creatable, updatable and blocked counts, completeness and conservative freshness. Include a bounded list of effective file-create targets and an explicit target truncation flag. Add a safe credential-storage marker without returning a password or inventing a login name.

- [x] Add behavioral tests for full counts before pagination, empty/partial inventory, legacy access, stale/failed account evidence, create_file versus mkdir, subtree exclusions and no secret disclosure.
- [x] Run those tests and verify the intended failures.
- [x] Implement the small summary in the existing setup/status path; preserve DTO bounds and pagination.
- [x] Run the affected Python setup/access tests and provide the exact summary schema and test evidence for the frontend.

### Task 2: Central first-screen overview and editing

**Files:**
- Modify: `docker/openclaw-personal-assistant-plugin/setup-ui.html`
- Modify: `docker/openclaw-personal-assistant-plugin/setup-ui.css`
- Modify: `docker/openclaw-personal-assistant-plugin/setup-ui-browser.js`
- Modify: `docker/openclaw-personal-assistant-plugin/setup-launch.html`
- Test: `tests/nextcloud_setup_ui_browser.test.mjs`

**Interfaces:**
- Consumes: existing status/discovery/Talk DTOs and Task 1's `access.overview`.
- Produces: compact account controls and six overview entries before long editors; in-page links to unchanged detail workflows.

- [x] Add failing browser-DOM tests for truthful calendar coverage, effective file targets, saved versus draft settings, account failure, stale/disconnected state and bounded/missing summary handling.
- [x] Reorder the first screen and add the overview using existing text-only rendering and expiry semantics.
- [x] Make credential entry directly reachable on the first page; preserve write-only password handling and existing confirmation flows.
- [x] Add responsive styling, keyboard-accessible in-page editing, and correct the launcher's obsolete password description.
- [x] Run all existing Node setup/auth/bridge tests, plus the new overview regressions.

### Task 3: Browser acceptance, documentation and integration review

**Files:**
- Modify: `scripts/check-nextcloud-setup-browser.py`
- Modify: `docs/NEXTCLOUD_SETUP_UI.md`
- Modify: `CHANGELOG.md`
- Regenerate: repository-owned source manifest/component inventory if required.

**Interfaces:**
- Consumes: shipped setup assets and the existing synthetic signed-RPC fixture.
- Produces: real-browser desktop/mobile acceptance evidence and updated setup guidance.

- [x] Extend real-browser fixture assertions for first-screen layout, working in-page controls and mobile overflow without copying production profiles or credentials.
- [x] Run the actual Firefox fixture, inspect screenshots and correct observed UI defects.
- [x] Update setup documentation with the overview meanings and the difference between saved status and fresh checks.
- [x] Review the complete diff independently for evidence semantics, credentials, regressions and usability; address findings.
- [x] Regenerate required inventories/manifests and run the required repository checks. Commit the reviewed development result; report that it has not been deployed.

## Validation result

The implementation and both focused/final reviews are complete. The full repository gate passed; native Firefox acceptance verified the actual 1366x814 desktop and 360x900 narrow viewport, signed editing, password handling, expiry and disconnects. Development artifacts are in `build/nextcloud-setup-overview-20261128/` (ignored). This result is committed on `codex/nextcloud-setup-overview`; production activation remains outside this task.
