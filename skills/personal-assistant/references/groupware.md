# Nextcloud files, contacts, events and tasks

Nextcloud is a controlled remote connector, not a local directory. Never search
for `~/.nextcloud` or another local mount, and never expose the central secrets
file. Start with the registered status/discovery command. An account connected
through the [OpenClaw setup page](../../../docs/NEXTCLOUD_SETUP_UI.md) separates
server discovery, saved agent selection and currently effective rights. In active
access mode, read tools aggregate only selected resources with sufficient current
rights; every write still selects one exact stable `resource_id`. Never select
the first discovered collection or assume a resource from its display name.

Account onboarding, credential changes and Talk pairing belong to that
administrator-only page, not to agent tools. For an agent account the operator
may enter its login name and app password there; never request these secrets in
agent chat. Setup diagnostics distinguish verified identity, collection inventory,
metadata read checks and the one folder level actually opened. A saved check
with a timestamp is not a new live access test. Never invoke its control socket or
RPC through a generic tool. The GUI-capable production profile disables generic
shell/file tools; native domain tools retain their existing approval contracts.
Legacy `calendar configure`, `tasks configure` and `contacts configure` are
rejected for a GUI-managed account and direct the operator to the setup page.

The operator controls access levels on that same page: Off, Read, Read/Create,
or supported Read/Create/Update. These levels never grant server rights, replace
single-action approval or start jobs. New accounts and newly discovered resources
start without grants. VEVENT and VTODO remain separate even on the same calendar.
Files use exact folder identities and the most specific selected subtree; an
explicit Off child stays inaccessible under an allowed parent. Whole-account
root access requires its own operator confirmation. Existing managed legacy
access remains separately identified until its bounded migration is confirmed.

The active policy applies to CLI, native tools, cached contacts, local search,
sync and pending actions. Account or policy changes invalidate old authority;
stored historical rows are not current access evidence. Missing or damaged
required access state blocks Nextcloud operations. Report `scope-off`,
`operation-not-allowed`, `rights-stale`, `rights-unverified`, `policy-changed`
or `policy-integrity-error` with the registered safe next step. Never repair
these by changing configuration, expanding access or replaying an old approval.

`resources status` is the central read-only identity inventory. Stable resource
ID, business name, component, confirmed permissions and remote identifier are
separate fields. Treat `resource-id-missing`, `resource-id-stale`,
`resource-id-duplicate`, `resource-discovery-ambiguous`,
`resource-configuration-drift`, `resource-credentials-missing`,
`resource-component-missing` and `resource-permission-missing` as distinct
blockers; never repair one by choosing the first or a fuzzy discovery result.

The product contains a native Nextcloud/WebDAV connector. Its availability is
established by the live tool catalog and the registered command result, not by
looking for a local mount, a generic plugin or credentials in the workspace. A
configured remote path proves neither success nor failure on its own. In
particular, archived invoice files are listed with the registered `invoices
files --limit 100` command after `invoices status`; this command resolves the
configured invoice root and resource internally.

If a Nextcloud result reports `missing_environment`, preserve those variable
names and run the matching registered status/doctor path. Never inspect
`~/.config/personal-assistant/secrets.env`, list its parent directory, probe
`/run/openclaw-env` or `/srv/openclaw/secrets`, or use a shell fallback. The
gateway's generic file tools are workspace-only; connector credentials remain
role-mounted inputs to the registered command.

For questions about current files, contacts, events or tasks, use the matching
registered list/search tool before memory, workspace or shell search. A local
index may supplement an explicitly broad search, but it does not prove that a
remote object or configured capability is absent.

A request mentioning a Nextcloud file, document, WebDAV path or the `Assistent/`
tree is already enough to trigger this connector. Read `resources status` and
list the requested path inside its reported roots before asking Jan for a local
mount, host path, API, another skill or an upload. In active access mode, use the
exact allowed roots from status, for example `nextcloud list --path "Assistent"`.
An account-root listing is available only when that root is authorized. Managed
legacy accounts retain their existing mode until confirmed migration; standalone
legacy configuration retains its explicitly selected roots.
For a filename/content query
without an exact path, use the registered
broad `search "<Suchbegriff>"` path as supplementary indexed evidence and verify
the live file metadata through `nextcloud list`. If the requested operation needs
full binary content that no registered command exposes, report only that precise
operation as unavailable after these attempts; never claim that Nextcloud access
or its native connector is missing.

## Shared existing-object contract

- Calendar, task and contact status/list/search operations accept an optional
  `--resource "<resource_id>"`. Without it, active managed accounts read only
  selected and currently authorized collections of that component. Check `complete`, `resource_errors` and
  `results_may_be_truncated` before a completeness or absence claim.
- Preserve the resource ID on each result. With multiple collections, pass that
  exact ID to create, update and mail-derived previews/writes. A UID alone does
  not select a collection. Resource IDs are bound to server and account; account
  changes cannot retarget an old ActionPlan.
- List/search current objects, target exactly one resource and UID, and use expectation guards
  when stale selection would matter.
- Enable update access only after explicit selection and live `can_update` or
  write-content evidence.
- Update only requested fields; omitted fields remain. Clear options require an
  explicit deletion request for that field.
- Preserve unknown/custom properties, addresses, photos, alarms, attendees,
  timezones, recurrence and exceptions. Use current ETag with `If-Match`; on 412
  stop, read again and never overwrite.
- Recurring objects require explicit series authorization. Deletion, bulk edit,
  silent merge and cross-collection moves are not registered.

## Contacts

Creation is create-only and never modifies a matching vCard. Name/e-mail
collisions require review; never merge automatically. Update one UID from a fresh
list/search result. Repeated email/phone arguments replace those full lists, so
use them only when Jan explicitly requested that replacement. Mail-derived contact
creation begins with an exact mail selection and dry-run. On an active access
policy, confirm the unchanged contact preview with its returned
`--preview-digest` and the same contact fields and selected resource. Account,
grant, source or candidate changes require a fresh preview. The digest remains
optional for legacy connections.

## Calendar and VTODO

`VEVENT` identifies event support and `VTODO` task support. Use calendar tools for
events and task tools for To-Dos; do not substitute one for the other. Calendar
and task create are bounded registered writes; existing-object update, complete
or reopen requires exact UID and explicit approval. Do not describe the calendar
integration as create-only.

For legacy single-calendar configuration, the central direct
`nextcloud.calendar.resource_id` is a diagnostic recovery
target for a stale typed mail-calendar reference only when the old ID has exactly
zero live matches and the direct ID has exactly one VEVENT match with create
privilege. Recovery reports `resource-configuration-drift` and is not healthy.
Permanently align the IDs only through the registered preview/digest-bound local
calendar migration after explicit approval. It changes no remote object or
permission. The untyped legacy mail-agent calendar selector is no longer an
identity path. Ambiguity, a second arbitrary calendar, missing VEVENT or missing
privileges remain fail-closed.

For an explicit “create calendar events from this mail” request, use the native
mail-to-calendar workflow and never manually translate mail prose into an
unbound `calendar create` call. Select and read exactly one current mail by
folder, mailbox ID and expected subject. Then call the registered `calendar
from-mail ... --dry-run` preview. The preview is read-only, scans the full raw
mail and every physical attachment through ClamAV, normalizes supported airport
timezones from a closed map, exposes field provenance and abstains on missing or
conflicting data.

Create exactly one unchanged preview candidate at a time with its current
preview digest and candidate ID. Each candidate requires its own approval and
ActionPlan. Success requires a unique remote UID read-back, a nonempty ETag and
matching title, start, end, location and description. A stale source/digest,
unknown timezone, mismatch or uncertain delivery blocks completion. Two flight
segments are two independent creates; stop after a failure and never reuse an
approval, bulk-write, delete or claim automatic rollback.

After a turn limit, stale approval or partial result, do not ask an unbound
yes/no retry question. State the typed blocker and completed candidate count,
then provide a complete new instruction such as “Trage Hin- und Rueckflug aus
der zuvor ausgewaehlten Mail als zwei Termine in meinen Kalender ein.” The new
turn must reselect and revalidate the source and obtain fresh per-candidate
approvals.

For “complete task” requests, run `tasks status` and then `tasks list
--include-completed --limit 100`; never use memory as the task source. With one
exact resource/UID/title and live `update_allowed=true`, use
`tasks update --resource "<resource_id>" --uid "<UID>"
--expected-title "<aktueller Titel>" --status COMPLETED --yes`, then require
`after.status=COMPLETED` and `after.percent_complete=100` as the success evidence.
The release-owned `standard` profile exposes supported update access for an
already enabled and exactly selected task resource at every process start,
within the active agent policy and current server rights. It never overrides a
Read-only or Off selection. Therefore use the
registered completion command directly when `tasks status` reports
`update_allowed=true`; do not ask Jan for another technical unlock. If status is
still false, treat the missing resource, credential or confirmed permission as an
operational failure and do not bypass it. `setup standard-operations --yes` is
only the operator compatibility/repair action for an explicitly restricted or
incompletely migrated installation. It may register a missing normal permission
only after current read-only DAV discovery confirms the exact resource and never
changes server ACLs or weakens concrete action approval and ETag guards. Never run
it from the gateway, edit `tools.toml`, change the read-only mount or claim that an
internal note replaced the CalDAV update.

## Durable workspace

Remote paths stay inside the roots reported by `resources status`. Active managed
accounts expose only their authorized subtrees; legacy configuration retains
its selected roots such as `Assistent/`. Never reinterpret an old resource ID as
the newly connected account. Folder creation is
idempotent; uploads and text files are create-only; moves use no-overwrite
semantics and stay inside the root. Local uploads must originate in the controlled
workspace outbox and pass ClamAV. Delete, overwrite, public sharing and permission
changes remain prohibited. Every write passes ActionPlan, policy, idempotency and
audit. In active access mode, unsupported MOVE and remote invoice-register
workflows remain blocked until they have their own configured contract; an
ordinary file grant does not activate them. C permits creating files and K
permits creating subfolders, only when separately measured and selected.

For a locally completed ActionPlan, external idempotency is valid only while the
expected remote postcondition still exists. A missing create-only target may be
recreated only with current binding, checks and required fresh authorization;
different content at the destination is a conflict. A delivery-uncertain action
does not authorize a retry. Local invoice, order and portfolio history retains
its independent owner and never grants access to remote archives.
