# Mail production-gate timeout repair

Jan requested diagnosis and repair of the live timeout, followed by successful
Mail, Index and Sync execution evidence. Existing authorization covers the normal
reviewed image/update workflow needed to verify the repair on this PC.

## Evidence and cause

On main `5a6022c`, the code-bound dry-run fingerprint correctly became stale.
Registered worker diagnostics show `auto_recoverable=true` with that sole blocker.
Registered performance telemetry shows an interrupted `run-dry` waiting on its
second Ollama request: the first completed successfully after 125.65 seconds.
The gateway recovery killed this five-message run at 240 seconds; the worker has
the same problem at 300 seconds. Both apply one outer timeout to multiple model
requests whose individual queue and inference budgets can already exceed it.
Sync rejects the resulting stale mail projection, and the owner skips the index.

## Approach

Keep the five-message validation and every existing security, fingerprint,
single-writer and completion gate. Use a separate bounded recovery budget,
cooperative single-batch runtime accounting, and per-attempt remaining-time
calculation. Readiness remains a separately bounded check. A failed budget is an
explicit failed validation, never a successful empty/partial dry-run.

A global timeout increase would leave nested retries and partial results
uncontrolled. Replacing validation with a smaller sample would change its scope.
Neither is selected. A preliminary recovery budget of 900 seconds, a 60-second
cooperative completion reserve and an outer 960-second watchdog will be checked
against the controlled live five-message run before release. No queue/permission,
model or safety configuration is weakened.

## Boundaries and acceptance

Changes stay in existing mail run/classifier/recovery code and its regression
tests. No proxy rewrite, mailbox content changes, new credentials, raw IMAP or
in-place production patches. Timeouts identify readiness/dry-run/recheck; failed
recovery retains the fingerprint-bound cooldown. Only a complete successful
dry-run permits the normal postcheck and productive command.

In containers, all `jobs check` callers observe the existing mail owner instead
of running foreground recovery or changing its desired state. This keeps the
native status call independent of the longer owner validation. Legacy recovery
retains the same bounded validation contract.

The explicit native `mail.dry-run` tool still has the separate 120-second plugin
limit. The 412-second calibration used the registered operator CLI, not that
plugin call. Fixing the interactive limit would also require bounded child
cleanup and respecting remaining turn retention; it is outside this production
recovery change. A native timeout is unknown, never evidence of successful
validation. Native approval and evidence lifetimes remain unchanged.

Use reviewed immutable images, verified local backup and standard rollback.
Final live evidence must show completed successful Mail and Index owner work,
a new complete current index generation and successful Sync import; container
health or a fresh owner heartbeat alone is insufficient.
