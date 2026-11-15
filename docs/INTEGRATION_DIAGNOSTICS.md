# Integration diagnostics development contract

This requirement applies to all future integration development and changes.
Implement it in the affected integration's existing status, doctor or setup path.
It does not create a new integration framework, agent tool or authorization path.
Existing typed approvals, data owners, resource boundaries and release gates
continue to apply.

## Independent evidence

The supported user-facing diagnostics must distinguish these questions. Combine
presentation where useful, but never substitute one answer for another.

| Check | What its evidence proves |
| --- | --- |
| Availability | The selected service/API was reachable through the required transport. A failed request does not by itself prove the service is absent. |
| Authentication | The selected account was authenticated and its identity verified. A public status endpoint is insufficient. |
| Capabilities | The required feature is explicitly supported, missing or still unknown. An unavailable capability response leaves support unknown. |
| Resources and rights | Which exact resources were discovered, which accesses were checked, and which rights the server reported. Metadata reads and advertised write rights remain separate. |
| Saved selection | Which account, resources, senders and enable flags are configured at the current revision. Stored configuration is not a fresh server check. |
| Runtime activity | Which current worker operation was observed for the same account and selection, when it occurred, and whether the observation is fresh. Configuration or process existence alone is insufficient. |

Declare a check non-applicable when the integration has no corresponding
capability, selection or background worker. Describe the scope of every positive
claim: one checked folder level does not prove recursive access, a partial list
does not prove absence, and one successful worker poll does not prove delivery.
No general green status may conceal a failed, unknown or incomplete prerequisite
of the operation the user wants to perform.

## Bounded checks and visible outcomes

Each observation needs its source, account/resource scope, check time and result.
Show freshness separately from the age of saved configuration. Bound network
time, response size, resource count and diagnostic output; an exhausted limit
must report partial or unchecked scope. Keep independently successful results
when another check fails. Do not re-label cached results as a new live check.

Use stable technical codes and a concrete next step available through the
supported UI or registered tool. Distinguish at least:

- unknown or unchecked: the required evidence has not been obtained;
- partial: only the stated subset was checked;
- disabled: the configured feature is intentionally off;
- stale: the last observation is older than its defined freshness window;
- failed: a check was attempted and returned a definite error;
- successful: the stated check and its scope are backed by current evidence.

Stale evidence proves neither a running worker nor an outage. An empty complete
resource list differs from a failed or truncated list. A configuration change
invalidates observations tied to the previous account or revision. A safe next
step must not silently change credentials, grant rights, enable/restart a worker,
relax TLS or repeat an uncertain external operation.

Diagnostics contain no credentials, tokens, raw request/response payloads or
mail, file or chat content. Keep technical metadata and counters bounded.
Probe messages, uploads and other external test writes are not part of default
diagnostics; any delivery test is a separate explicitly authorized operation.

## Nextcloud Talk application

The setup page separates stored discovery results, saved room/sender pairing and
the account worker's observation. Discovery checks Talk capability information
and the authenticated room API with visible completeness and rights. Required
`chat-reference-id` support is separate from room visibility and send permission.
A saved sender allowlist does not prove current room membership; participant
checks belong to their explicit, bounded selection/validation step.

Only an observation of an actual successful poll can establish current polling.
It must match the account and current configuration and be no more than 90
seconds old. The initial successful chat request that sets the cursor without
replaying history counts as a poll. Process startup, a saved enable flag and a
successful room inventory do not. The observation is not a fresh access check of
every selected room. Missing, mismatched, stale, failed or uncertain worker
evidence remains visible. Even a healthy poll does not prove receipt or delivery
of an individual message, mobile notifications or end-to-end chat completion.
The setup checks send no delivery-test message by default.

## Verification

Behavioral tests must cover successful and failed authentication/capability/API
checks, incomplete inventories, unknown or insufficient rights, empty results,
disabled and unpaired configurations, changed account/revision, missing or stale
worker observations, real poll success/failure, and processing/delivery
uncertainty where applicable. Verify the visible UI preserves these distinctions
and provides the safe next step. Include secret/content redaction and bounded
malformed-input cases. A fixture, cached observation or saved flag must never
produce an unsupported live success claim.

Document which checks are hermetic and which require a separately authorized
live acceptance. Test success does not establish a production connection or
authorize a deployment.
