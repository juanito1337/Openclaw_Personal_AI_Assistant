# Chat Mail Dry-Run Implementation Plan

> **For agentic workers:** Use systematic debugging, test-driven development and verification before completion. Existing user authorization covers implementation, main publication and PC deployment.

**Goal:** Let the registered manual chat mail dry-run finish within its bounded model-work budget and return valid current-turn evidence.

**Architecture:** Keep the existing synchronous native read operation and its fixed twenty-message limit. Give only `mail.dry-run` a 900-second cooperative budget (60-second completion reserve) and 960-second native watchdog. Preserve a live run's result context for that bounded execution plus up to 1800 seconds of finalization, capped at 3600 seconds from the original run start, without renewing its original write-authorization deadline or the 180-second approval nonce.

**Tech Stack:** Existing Python CLI/catalog, generated JSON contract, Node plugin and pytest/Node harnesses; no new dependencies.

**Spec:** Jan's explicit request to fix and test the manual chat timeout, then redeploy main. The prior production validation fix demonstrated a healthy 412-second probe; native execution still had a 120-second ceiling and direct-child-only termination.

## Global Constraints

- Other native operations retain their existing 120-second execution limit.
- No remote test writes, altered mail limit, policy expansion, repeated automatic dry-runs or restarted jobs.
- No expired/evicted context revival; no extension of write authority. Ordinary run retention remains 15 minutes and 128 records.
- Preserve cooperative fail-closed validation, cancellation and bounded process-group termination.
- Deploy only reviewed/tested exact main images, verified signatures, local backup and installer rollback protection.

## Review Focus

- A result arriving after 15 minutes must be usable, while an old write authorization must remain expired.
- Repeated reads and capacity eviction must not renew or resurrect retained state.
- Cancellation or timeout must stop descendants and pipes within a bounded interval.
- A partial or timed-out dry-run must never authorize production.
- Native installed-tool acceptance must be distinguished from a model-driven chat turn and from a direct operator CLI call.

### Task 1: Bounded CLI and native execution

- [ ] Reproduce timeout/process-tree failure and write failing CLI and native lifecycle tests.
- [ ] Forward validated `--max-runtime`/`--shutdown-reserve` through `personal_assistant/cli_parsers/mail.py` and `cli_handlers/mail.py`; publish fixed defaults in `tool_catalog/mail.py`.
- [ ] Add operation-specific execution limits in `agent_tool_orchestration.py`; wire timeout, AbortSignal and scoped retention through plugin `index.js`/`runtime.js`.
- [ ] Test success beyond 120 seconds, original retention boundary, expired authorization, eviction, late result, abort before/during execution and process-tree termination.
- [ ] Update generated contracts, domain references, changelog and source/component manifests; run full repository and installed-wheel checks.

### Task 2: Review and deployment

- [ ] Independently review the complete change and resolve material findings with regressions.
- [ ] Commit, fast-forward/push main and await exact-head CI plus signed immutable images.
- [ ] Verify live predecessor, signatures, single writer and backup; deploy through the normal installer with rollback protection.
- [ ] Verify release and installed native mail dry-run evidence/finalization, then registered job/index/sync health. Record limitations explicitly.

## Decisions

Use the existing native synchronous operation: a background-job API would expand scope and require new approval/status contracts. Merely raising a global timeout would affect unrelated tools and lose evidence at the retention boundary. The scoped execution window leaves ordinary authorization lifetime unchanged.

Review ruling: a 120-second finalization grace would merely move the failure to a healthy model answer taking 121 seconds. The release supports a 1800-second provider call and 3600-second agent turn. Keep only the dry-run result context for that bounded finalization window, capped by the original 3600-second run horizon; write authority and new-call admission still expire at 900 seconds.
