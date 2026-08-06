# Nextcloud files, contacts, events and tasks

Nextcloud is a controlled remote connector, not a local directory. Never search
for `~/.nextcloud` or another local mount, and never expose the central secrets
file. Start with the registered status/discovery command and let Jan select the
exact stable `resource_id`; never select the first discovered collection.

## Shared existing-object contract

- List/search current objects, target exactly one UID and use expectation guards
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
creation begins with an exact mail selection and dry-run.

## Calendar and VTODO

`VEVENT` identifies event support and `VTODO` task support. Use calendar tools for
events and task tools for To-Dos; do not substitute one for the other. Calendar
and task create are bounded registered writes; existing-object update, complete
or reopen requires exact UID and explicit approval. Do not describe the calendar
integration as create-only.

## Durable workspace

Remote paths stay inside the configured `Assistent/` root. Folder creation is
idempotent; uploads and text files are create-only; moves use no-overwrite
semantics and stay inside the root. Local uploads must originate in the controlled
workspace outbox and pass ClamAV. Delete, overwrite, public sharing and permission
changes remain prohibited. Every write passes ActionPlan, policy, idempotency and
audit.

For a locally completed ActionPlan, external idempotency is valid only while the
expected remote postcondition still exists. A missing create-only target may be
recreated; different content at the destination is a conflict.
