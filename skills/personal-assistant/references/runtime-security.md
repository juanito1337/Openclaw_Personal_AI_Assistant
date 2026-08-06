# Runtime, security and background work

## Release and diagnostics

Before discussing installed version, update success or recent changes, run
`./scripts/assistant.sh version --verify`. Use its `--history` options for release
history. A failed verification is an operational integrity error; never guess.

For runtime state use registered commands such as `status`, `doctor`, `jobs`,
`scheduler`, `ollama`, `monitor`, `performance` and `security antivirus` from the
[generated tool contract](tool-contract.md). Never inspect or edit the scheduler,
job-control or monitoring databases as an operational shortcut.

## Jobs and single writer

- `ON`, `OFF` and `FAILED/DEGRADED` are different states. Never report a job as
  working unless its registered command returned evidence.
- The desired-state file controls container jobs. Start, restart or repair a job
  only after Jan explicitly requests it, except the narrowly documented safe mail
  dry-run repair in the operating contract.
- Exactly one productive writer may own a write domain. Legacy systemd writers
  and container writers must never overlap.
- The supervisor remains outside the business-job scheduler. A worker must hold
  and renew its lease; repeated renewal failure is fail-closed. Queue wait itself
  is not a failure.
- `scheduler focus` is a bounded local priority hint. It cannot enable a job,
  expand rights, approve an ActionPlan or execute arbitrary commands.

## Ollama

Use only registered `ollama status`, `check`, `queue`, `start` and `restart`
commands. Start/restart requires an explicit request. Never stop the coordinator
from inside the agent, silently bypass it or rewrite model URLs. Verify both proxy
and upstream after recovery.

## Antivirus

ClamAV is a mandatory fail-closed gate for complete raw mail, every physical
attachment and controlled workspace uploads. Scan before parsing/writing where the
workflow requires it. Cache a clean result only for the identical SHA-256 and
scanner/signature identity. Infected mail goes to the configured quarantine;
scanner errors block forwarding and writes. Never upload suspicious material to
an external scanner or disable antivirus to make an operation succeed.

## Monitoring and failure handling

Use `monitor status --days 7 --live`, `monitor record` and `monitor history` for
technical health. Report score, confidence/coverage, weakest component, evidence,
recommendations and the limitation that true classification precision needs
confirmed correction labels.

On every tool failure preserve the exact error and run the domain status/doctor.
For service-backed tools also run `jobs check --target all --deep`. Do not change
credentials, policy, permissions, forwarding or security gates as an automatic
repair.
