# Nextcloud setup overview

## Intent

Jan wants the first Nextcloud setup screen to show the saved connection and all
relevant integration states together, and to let him configure the connection
there. The important questions are which account is connected, whether all
visible calendars are accessible and selected, and where the assistant can
create files. Tasks, contacts and Talk belong in the same overview.

## Approach

Use the existing authenticated setup tab as the central screen. This is an
implementation assumption, not a new user-approved change to the plugin trust
boundary; the clarification about the entry window remains open. The existing
embedded entry card cannot host administrator credentials. Retain its safe link.

A compact overview above the existing editors gives immediate status and
in-page access to settings. A longer wizard would hide existing state between
steps. Moving credentials into the embedded launcher would change its trust
boundary and is outside this UI change. Keep the existing browser authentication,
signed operator RPC and account/access confirmation flows.

## First screen

- A compact OpenClaw administrator connection strip.
- Saved server URL, verified account/display name, credential-storage state and
  last account check. App passwords remain write-only. Do not invent a login
  name from the verified user ID when those can differ.
- Connection inputs on this page: server, login name and app password, plus the
  existing Nextcloud browser-login alternative. An existing connection can show
  a compact summary and an explicit in-page edit action.
- Overview entries for account, calendars, task lists, address books, files and
  Talk, with textual status, scope, time and a direct link to the right editor.
- Clearly distinct actions for reloading saved status and performing fresh
  server/resource discovery.
- Detailed diagnostics, account preview, resource permissions, folder browsing
  and Talk pairing remain available lower on the same page.

## Evidence rules

The overview must preserve the integration diagnostics contract. Saved selection,
server rights and actual activity are separate facts. No broad green status may
hide partial discovery, an account failure, stale evidence or missing rights.

Calendar totals are scoped to collections visible to the connected account.
Show discovered/readable/selected counts separately. An all-calendars claim
requires complete inventory and checks, fresh account/domain evidence and no
failed or skipped checks. A complete empty result is distinct from unknown.
VEVENT and VTODO remain separate.

File creation is reported only for specific effective `create_file` grants.
`mkdir`, readable root access and advertised rights alone do not prove file
creation is permitted. Keep subtree exclusions and resource identities intact.
The UI does not claim that an upload was tested or a file delivered. Existing
content, outbox, scanner and action-approval gates remain unchanged.

Talk shows saved enablement/room/sender counts separately from the existing
account/revision/selection-bound worker evidence. Its observation expires after
90 seconds. The other components must not inherit Talk's runtime state or claim
an observed synchronization when the setup contract does not supply one.

Compute small permission totals from all status rows before pagination. Preserve
the current bounded response and continuation contract; a clipped detail page
must not turn into an all-resources claim. The summary carries its own account,
revision, completeness and conservative freshness. The browser expires positive
claims without silently refreshing timestamps. Unsupported or absent summary
data falls back to an explicit incomplete/unknown view.

## Boundaries

- No production deployment or live account/permission changes are part of this
  request. Deliver reviewed source and tested browser behavior.
- No new dependency, generic RPC endpoint, external write probe or job start.
- No secret value in an overview, diagnostic, browser storage, fixture or log.
- Keep existing preview/confirmation, conflict, cancellation and disconnect
  behavior. Unsaved edits must not masquerade as saved effective settings.
- Preserve German labels, keyboard access, visible focus, dark mode and narrow
  mobile layouts. Remote resource names remain text, never HTML.

## Acceptance

Exercise fresh complete and partial accounts, empty inventories, disabled
permissions, folder create versus mkdir, stale evidence, account failures,
pagination, malformed summary data, disconnected sessions and stale Talk worker
observations. Existing signed login and access changes must still work. Inspect
the shipped assets in the existing isolated Firefox fixture at desktop and
mobile widths; use synthetic accounts only.
