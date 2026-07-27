# Workspace audit

Audit target: uploaded `workspace(3).zip`.

## Verdict

The uploaded workspace was operational but did **not** meet the requested clean, legacy-free, GitHub-ready standard. It mixed source code with production state, contained contradictory old/new implementations, shipped broad third-party skills, and had one failing test. The most serious contradiction was that the documented bounce fix was not present in the active `rules.py`.

## Inventory of the uploaded archive

- 201 files, about 6.1 MB
- `legacy/` with 20 obsolete triage/forwarding scripts
- about 3.5 MB of runtime state under `mail_agent/data/`
- SQLite database plus WAL/SHM files
- 16 complete `.eml` payloads
- logs, lock, setup state, generated ICS files, caches, and seven timestamped backups
- 62 compiled `__pycache__` files
- personal addresses, private-network endpoints, personal memory, and historical instructions
- about 0.94 MB of vendored skills, dominated by the broad Nextcloud community skill

## Critical findings

### 1. Production mail data was packaged as source

The archive included the live database, complete original emails, pending calendar files, logs, and processing state. This is a privacy leak and makes a Git repository non-reproducible. `.gitignore` existed, but the ZIP process did not respect it.

**Resolution in the cleaned baseline:** runtime state is absent; only empty `.gitkeep` directories are tracked. `config.toml`, `rules.toml`, data, personal OpenClaw files, `.clawhub`, and third-party skills are ignored.

### 2. The active bounce code was an old unsafe implementation

The active rule treated any sender local-part `postmaster` or `mailer-daemon` as a bounce. That is the exact cause of advertising from a `postmaster@...` address being forwarded. A README claimed the fix was installed, but active code still contained the legacy heuristic.

**Resolution:** standards-based DSN detection is restored. A bounce now needs a `message/delivery-status` report or a bounce subject plus an independent strong signal. Spam rules are checked first and domain rules match subdomains. Three regression tests cover the incident and a real DSN.

### 3. Source contained multiple historical implementations and instructions

`legacy/`, old change notes, backup files, stale `README.txt`, old memory, and old timer commands contradicted the active application. This creates a high risk that an agent or developer selects the wrong path.

**Resolution:** the cleaned baseline contains one application, one README, one changelog, one custom skill, and consolidated docs.

## High-severity findings

### 4. Test suite was not green

The uploaded workspace passed 61 of 62 tests. The failing Nextcloud install test exposed a real bug: an explicitly configured skill path could silently fall back to another installed workspace copy, causing stale code to be used and masking installation failures.

**Resolution:** explicit non-default paths are authoritative. Fallback discovery is allowed only for the default workspace location.

### 5. `--limit` was not a global run limit

Correction folders were each queried with the full limit before the inbox budget was calculated. A run with `--limit 20` could therefore process up to 80 correction messages before touching the inbox.

**Resolution:** one shared budget now covers every correction folder and the inbox. A regression test verifies this.

### 6. Deployment templates were stale and dangerous

The repository service template used `--limit 100` with digest enabled, while the timer ran every ten minutes. This contradicted the tested hourly, no-digest, limit-20 setup and could recreate excessive load.

**Resolution in 3.2.2:** systemd templates and the interval helper shared one canonical hourly service. **Current 3.3.1 behavior:** bounded drain batches with a 20-minute idle timer, no digest, no `--force`, and ZIP forwarding without an IMAP Sent-copy append.

### 7. Automation could invoke `--force`

Historical OpenClaw jobs had already demonstrated that an automated actor could bypass the dry-run gate. The CLI accepted `--force` non-interactively.

**Resolution:** `--force` is now rejected unless the process has an interactive terminal, `MAIL_AGENT_ALLOW_FORCE=YES`, and the user types exactly `FORCE`.

### 8. Third-party skill surface was too broad

The vendored Nextcloud skill contains capabilities far beyond this mail agent’s needs. The local wrapper limits its own calls, but leaving the broad skill visible inside the workspace increases code size and prompt/tool attack surface. Himalaya and Signal skills were also unnecessary for automatic operation; Signal was disabled and its skill targeted macOS.

**Resolution:** no third-party skills are vendored. They are installed and verified locally when needed. The tracked custom skill documents only the narrow mail-agent interface.

## Medium-severity findings

- Package version said `3.1.0` while 3.2 features were active.
- Python defaults contained personal email addresses.
- Threshold defaults, setup recommendations, and live config disagreed.
- Config and rule backups accumulated next to source files.
- SQLite had no recorded schema version.
- The full ClawHub lock and unrelated skill files participated in the dry-run fingerprint.
- Root `AGENTS.md` was mostly generic social/voice/group-chat guidance; `MEMORY.md` and `TOOLS.md` contained stale `--limit 100`, Signal, and legacy references.
- Several modules are too large for comfortable extension: setup/help, calendar, classifier, storage, CLI, Nextcloud, and app orchestration.

**Resolutions in the cleaned baseline:** neutral examples, version 3.2.2, consistent balanced thresholds, bounded runtime backups, SQLite `user_version`, targeted Nextcloud fingerprinting, concise Codex/OpenClaw instructions, and consolidated docs.

## Strengths retained

- no `shell=True` command construction
- stable Message-ID based identity with hash fallback
- process lock and dry-run fingerprint gate
- hard rules and feedback before model inference
- structured batch output with strict local IDs and safe splitting/fallback
- original EML forwarding and duplicate-send protection
- malformed-charset fallback
- future-time recheck for calendar creation
- PDF signature validation, attachment-local extraction, SHA-256 deduplication, and no-overwrite Nextcloud upload
- conservative review paths when confidence is insufficient

## Remaining architectural work

The cleaned baseline has 24 internal modules and no import cycles, which is a good base. The remaining maintainability risk is concentration: `setup_assistant.py` has 944 lines, `calendar.py` 799, `classifier.py` 733, `storage.py` 647, `cli.py` 600, and `app.py` 596. The longest function is the embedded help renderer at 410 lines; configuration validation, interactive Nextcloud setup, guide rendering, rule evaluation, migration, CLI dispatch, invoice processing, and routing are also large.

The cleaned baseline deliberately avoids a risky full rewrite. Before adding projects, tasks, and document extraction, the next refactor should:

1. split classifier transport, prompt building, validation, and batching;
2. split calendar extraction, approval, ICS, and provider adapters;
3. replace monolithic routing with persisted `ActionPlan` plus idempotent outbox execution;
4. split storage by domain and add numbered migrations;
5. introduce narrow provider interfaces for calendar, files, tasks, projects, and document extraction;
6. add disposable integration environments for Nextcloud and IMAP;
7. add project/provenance tables before implementing project question answering.

## Validation of cleaned baseline

The cleaned baseline compiles and all 68 tests pass. Repository checks also reject tracked runtime data, legacy directories, vendored third-party skills, backups, compiled files, and private message payloads.
