# Runtime, security and background work

## Release and diagnostics

Before discussing installed version, update success or recent changes, run
`./scripts/assistant.sh version --verify`. Use its `--history` options for release
history. A failed verification is an operational integrity error; never guess.
An unqualified version question always means the OpenClaw Local Personal Assistant
product release returned as `product` and `version`, not an embedded OpenClaw
core/plugin/CLI or model version. Report a core/platform version only when it was
explicitly requested and a registered runtime command provides evidence; label it
separately and never substitute it for the product release.

For runtime state use registered commands such as `status`, `doctor`, `jobs`,
`scheduler`, `ollama`, `monitor`, `performance` and `security antivirus` from the
[generated tool contract](tool-contract.md). Never inspect or edit the scheduler,
job-control or monitoring databases as an operational shortcut.

Generic memory and workspace search are not runtime discovery. For a named
domain, prefer its registered list/search/status tool over `assistant.search`;
use the broad search only for its documented indexed sources and keep its default
bounded result path.

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
- In the container runtime the supervisor is observation-only for domain state.
  The narrow mail dry-run recovery runs before the productive child inside the
  single mail worker. Worker alerts use the bounded coordination queue; only the
  gateway-local relay owns gateway credentials and connects over loopback. Never
  enable insecure private WebSockets or give gateway credentials to a worker.
- A successfully persisted and delivered child-job degradation is a healthy
  supervisor observation, not a supervisor process failure. Preserve the child's
  `DEGRADED`/`FAILED` result and alert, but do not report the observer itself as
  degraded. Missing supervisor state, scheduler/relay health failures or failed
  alert delivery still fail the observer cycle closed.
- `scheduler focus` is a bounded local priority hint. It cannot enable a job,
  expand rights, approve an ActionPlan or execute arbitrary commands.

## Ollama

Use only registered `ollama status`, `check`, `queue`, `start` and `restart`
commands. Start/restart requires an explicit request. Never stop the coordinator
from inside the agent, silently bypass it or rewrite model URLs. Verify both proxy
and upstream after recovery. A registered domain tool such as `portfolio mapping
suggest --isin "<ISIN>"` may use Ollama only through this coordinator; its model
output remains bounded by that domain tool's deterministic validation and approval
contract.

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

Generic filesystem tools are restricted to the controlled workspace by
`tools.fs.workspaceOnly=true`. They are never credential discovery: do not
read, list, search, glob, stat or retry the parent of
`~/.config/personal-assistant`, `/run/openclaw-env`,
`/run/openclaw-secrets`, `/srv/openclaw/secrets`, `secrets.env` or another
`*.env` credential file. Do not use `exec` to bypass that boundary. A
`missing_environment` result is diagnosed only with the registered domain
status/doctor command and may expose variable names, never secret values.
