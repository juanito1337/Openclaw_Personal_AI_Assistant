"""Bounded Nextcloud selection editor used exclusively by the signed setup owner."""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
import uuid
from collections import OrderedDict
from copy import deepcopy
from dataclasses import dataclass, replace
from datetime import timedelta
from typing import Never

from . import nextcloud_access as access
from .nextcloud_connection import (
    ConnectionError,
    account_namespace,
    collection_resource_id,
    connection_resources,
)
from .nextcloud_onboarding import OnboardingError

MAX_EDITS = 64
MAX_CANDIDATES = 32
PREVIEW_SECONDS = 600
MAX_DISCOVERED_ROWS = 4096  # Account discovery bounds each of the four component inventories to 1000.
MAX_PREVIEW_BYTES = 256 * 1024
MAX_PROOF_BYTES = 24 * 1024
STATUS_BYTE_LIMIT = 2 * 1024 * 1024
MAX_FILE_CREATE_TARGETS = 16
MAX_FILE_CREATE_TARGET_BYTES = 8 * 1024
ROOT_STEP = "Kontowurzel gesondert bestätigen oder einzelne Ordner wählen."
MEASURE_STEP = "Konto erneut prüfen oder den genauen Ordner öffnen."
LEGACY_STEP = "Bisherige Abläufe prüfen und die gewünschten Ressourcen einzeln auswählen."
INTEGRITY_STEP = "Installationszustand und verifizierte Sicherung durch den Betreiber prüfen lassen."
WRITE_OPS = {"create", "update", "create_file", "mkdir"}
# VTODO has no registered task search or indexed task source.
READ_OPS = {**dict.fromkeys(access.OPERATIONS, access.READ), "VTODO": access.READ - {"search"}}


def _encoded(value):
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()


def _fail(code="invalid-access-parameters") -> Never:
    raise OnboardingError(
        code,
        {
            "account-root-confirmation-required": ROOT_STEP,
            "access-preview-unavailable": "Die Vorschau fehlt oder ist abgelaufen. Bitte erneut prüfen.",
            "access-preview-changed": "Vorschau geändert. Status prüfen und Vorschau erneut öffnen.",
            "scope-unbound": "Kontobindung prüfen und eine neue Vorschau öffnen.",
            "rights-unverified": MEASURE_STEP,
            "unsupported-access-level": "Eine für diese Ressource bestätigte Zugriffsstufe wählen.",
        }.get(code, "Einrichtungsparameter und aktuellen Status prüfen."),
    )


def _exact(value, fields):
    if not isinstance(value, dict) or set(value) != set(fields):
        _fail()


def _token(value, pattern):
    return isinstance(value, str) and bool(re.fullmatch(pattern, value))


def validate(operation, params):
    fields = ("expected_revision", "expected_policy_revision")
    if operation == "access.preview":
        _exact(params, (*fields, "account_id", "edits", "confirm_root"))
        if (
            not _token(params["account_id"], r"nextcloud-account-[a-f0-9]{16}")
            or type(params["confirm_root"]) is not bool
            or not isinstance(params["edits"], list)
            or len(params["edits"]) > MAX_EDITS
        ):
            _fail()
        keys = set()
        for edit in params["edits"]:
            if isinstance(edit, dict) and "revoke_handle" in edit:
                _exact(edit, ("level", "revoke_handle"))
                handle = edit["revoke_handle"]
                if edit["level"] != "off" or not _token(handle, "[a-f0-9]{64}") or handle in keys:
                    _fail()
                keys.add(handle)
                continue
            _exact(edit, ("level", "scope"))
            scope = edit["scope"]
            _exact(scope, access.Scope.__slots__)
            component = scope["component"]
            if component not in ("VEVENT", "VTODO", "VCARD", "FILES") or edit["level"] not in (
                "off",
                "read",
                "create",
                "update",
            ):
                _fail()
            if component == "FILES":
                path = scope["path"]
                if (
                    scope["resource_id"] != params["account_id"] + "-files"
                    or edit["level"] == "update"
                    or not _token(scope["file_id"], r"[1-9][0-9]{0,31}")
                    or not isinstance(path, str)
                    or len(path.encode()) > 2048
                ):
                    _fail()
                try:
                    access.path_segments(path)
                except ValueError:
                    _fail()
            elif (
                not _token(scope["resource_id"], params["account_id"] + "-[a-f0-9]{16}")
                or scope["path"] is not None
                or scope["file_id"] is not None
            ):
                _fail()
            key = access.Scope(**scope).key
            if key in keys:
                _fail()
            keys.add(key)
    else:
        _exact(params, (*fields, "preview_id", "preview_digest", "confirm"))
        if (
            not _token(params["preview_id"], "[a-f0-9]{32}")
            or not _token(params["preview_digest"], "[a-f0-9]{64}")
            or params["confirm"] is not True
        ):
            _fail()
    if (
        not _token(params["expected_revision"], "[a-f0-9]{32}")
        or not _token(params["expected_policy_revision"], "(?:[a-f0-9]{32})?")
        or len(_encoded(params)) >= MAX_PROOF_BYTES
    ):
        _fail()


def _levels(component, allowed):
    reads = READ_OPS[component]
    result: dict[str, set[str] | frozenset[str]] = {"off": set()}
    if not reads <= allowed:
        return result
    result["read"] = set(reads)
    creates = ({"create_file", "mkdir"} if component == "FILES" else {"create"}) & allowed
    if creates:
        result["create"] = reads | creates
    if "update" in allowed and component != "FILES":
        result["update"] = reads | creates | {"update"}
    return result


def _scope_for(conn, component, row) -> access.Scope:
    if component == "FILES":
        scope = access.Scope(account_namespace(conn) + "-files", component, row["path"], row["file_id"])
        if (
            not _token(scope.file_id, "[1-9][0-9]{0,31}")
            or scope.path is None
            or len(scope.path.encode()) > 2048
            or access.folder_path(conn, row["href"]) != scope.path
        ):
            raise ValueError("scope-invalid")
        return scope
    return access.Scope(collection_resource_id(conn, row["href"]), component)


def _scopes(snap, requested=(), *, incomplete_components=None):
    found: dict[access.Scope, dict] = {}
    inv = snap.connection["discovery_snapshot"]
    complete = inv.get("complete") is True
    grants = snap.policy.grants if snap.policy else ()
    # Previously selected rows always precede bounded new inventory.
    found.update((g.scope, {}) for g in grants)
    for component, key in (*access.COLLECTIONS.items(), ("FILES", "file_roots"), ("FILES", "folders")):
        for row in inv.get(key, []):
            try:
                scope = _scope_for(snap.connection, component, row)
            except (AttributeError, TypeError, KeyError, ValueError, ConnectionError):
                complete = False
                if incomplete_components is not None:
                    incomplete_components.add(component)
                continue
            if (
                scope not in found
                and scope not in requested
                and len(found) >= len(grants) + MAX_DISCOVERED_ROWS
            ):
                complete = False
                if incomplete_components is not None:
                    incomplete_components.add(component)
                continue
            found[scope] = row
    return found, complete


def _file_grant(by_path, path):
    segments = access.path_segments(path)
    return next(
        (
            by_path[parent]
            for size in range(len(segments), -1, -1)
            if (parent := "/".join(segments[:size])) in by_path
        ),
        None,
    )


class EvidenceIndex:
    """Validate each DAV locator once; retain every matching row (including duplicates).

    The pure evaluator can then inspect just one selected collection, or the exact
    folder and its selected ancestor, without repeatedly parsing unrelated hrefs.
    An invalid collection poisons its group just as in the full evaluator.
    """

    def __init__(self, snap):
        self.snapshot = snap
        self.collections: dict[access.Scope, list[dict]] = {}
        self.files: dict[str, list[tuple[str, dict]]] = {}
        self.invalid = set()
        self.by_scope = {}
        self.by_path = {}
        conn = snap.connection
        inv = conn["discovery_snapshot"]
        self.header = {k: inv.get(k) for k in ("server_url", "account_id", "user_id", "checked_at")}
        for component, group in access.COLLECTIONS.items():
            for raw in inv.get(group, []):
                try:
                    scope = _scope_for(conn, component, raw)
                    access.normalize_scope(conn, scope, {**self.header, group: [raw]})
                except (ValueError, TypeError, KeyError, AttributeError, ConnectionError):
                    self.invalid.add(group)
                    continue
                self.collections.setdefault(scope, []).append(raw)
        for group in ("file_roots", "folders"):
            for raw in inv.get(group, []):
                path = raw.get("path")
                if isinstance(path, str):
                    self.files.setdefault(path, []).append((group, raw))
        for grant in snap.policy.grants if snap.policy else ():
            self.by_scope[grant.scope] = grant
            if grant.scope.component == "FILES":
                self.by_path[grant.scope.path] = grant

    def scoped(self, scope):
        snap = self.snapshot
        inv = dict(self.header)
        if scope.component == "FILES":
            grant = _file_grant(self.by_path, scope.path)
            paths = {scope.path, grant.scope.path} if grant else {scope.path}
            for path in paths:
                for group, raw in self.files.get(path, []):
                    inv.setdefault(group, []).append(raw)
        else:
            grant = self.by_scope.get(scope)
            group = access.COLLECTIONS[scope.component]
            inv[group] = [] if group in self.invalid else self.collections.get(scope, [])
        policy = replace(snap.policy, grants=(grant,) if grant else ()) if snap.policy else None
        return replace(snap, connection={**snap.connection, "discovery_snapshot": inv}, policy=policy)


def _selected(snap, scope):
    if not snap.policy:
        return ()
    if scope.component == "FILES":
        target = access.path_segments(scope.path)
        grants = [
            g
            for g in snap.policy.grants
            if g.scope.component == "FILES"
            and target[: len(access.path_segments(g.scope.path))] == access.path_segments(g.scope.path)
        ]
        row = max(grants, key=lambda g: len(g.scope.path), default=None)
        return row.operations if row and (row.scope.path != scope.path or row.scope == scope) else ()
    return next((g.operations for g in snap.policy.grants if g.scope == scope), ())


def _server_blocker(snap, scope, op):
    try:
        return access.server_blocker(snap.connection, scope, op)
    except (ValueError, ConnectionError, KeyError, TypeError, AttributeError) as exc:
        code = str(exc)
        known = {"scope-unbound", "folder-identity-changed", "rights-unverified"}
        return code if code in known else "scope-invalid"


def _row(snap, scope, raw):
    operations = set(READ_OPS[scope.component]) | (access.OPERATIONS[scope.component] & WRITE_OPS)
    allowed, blockers, server_errors = set(), {}, {}
    for op in sorted(operations):
        reason = _server_blocker(snap, scope, op)
        if reason:
            server_errors[op] = reason
        else:
            allowed.add(op)
        result = access.decide_access(snap, scope, op)
        if not result.allowed:
            blockers[op] = reason or result.code
    code = next((e for e in server_errors.values() if e != "server-right-missing"), "")
    check = (raw.get("read_check") or {}).get("status")
    stamp = raw.get("checked_at")
    if raw and "checked_at" not in raw and (scope.component != "FILES" or scope.path == ""):
        stamp = snap.connection["discovery_snapshot"].get("checked_at")
    worker = scope.component != "VTODO"
    return {
        "scope": scope.to_dict(),
        "name": str(raw.get("name") or scope.path or scope.resource_id)[:512],
        "server": {
            "operations": sorted(allowed),
            "checked_at": stamp,
            "read_check": check or "unknown",
            **({"read_check_source": raw["read_check_source"]} if "read_check_source" in raw else {}),
            "state": (
                "stale"
                if code == "rights-stale"
                else "partial"
                if code and allowed
                else "unverified"
                if code
                else "verified"
            ),
            "code": code or ("" if allowed else "server-right-missing"),
        },
        "desired_operations": list(_selected(snap, scope)),
        "effective_operations": sorted(operations - blockers.keys()),
        "blockers": blockers,
        "supported_levels": list(_levels(scope.component, allowed)),
        "worker": {
            "name": "sync-worker" if worker else "not-applicable",
            "state": "unavailable" if worker else "not-applicable",
        },
        "next_step": MEASURE_STEP if code else "",
    }


def status_view(snap, editor, account_failure):
    incomplete: set[str] = set()
    scopes, complete = _scopes(snap, incomplete_components=incomplete) if snap.connection else ({}, True)
    rows: list[dict] = []
    view = {
        "mode": snap.mode,
        "binding": snap.binding.to_dict() if snap.binding else None,
        "rows": rows,
        "complete": complete,
        "code": "" if complete else "inventory-incomplete",
        "next_step": LEGACY_STEP if snap.mode == "legacy" else MEASURE_STEP if not complete else "",
    }
    if snap.connection:
        resources = {r.id: r for r in connection_resources(snap.connection)} if snap.mode == "legacy" else {}
        index = EvidenceIndex(snap)
        configured = {g.scope: g.operations for g in index.by_scope.values()}
        for scope, raw in scopes.items():
            row = _row(index.scoped(scope), scope, raw)
            if snap.mode == "legacy":
                old = _legacy_operations(scope, resources.get(scope.resource_id))
                if scope.component != "FILES" or scope.path == "":
                    configured[scope] = old
                effective = old.intersection(row["server"]["operations"])
                row["effective_operations"] = sorted(effective)
                for op in effective:
                    row["blockers"].pop(op, None)
            rows.append(row)
        editor.present_rows(snap, rows, account_failed=bool(account_failure))
        if access.inventory_matches_account(snap.connection, snap.connection["discovery_snapshot"]):
            view["overview"] = _status_overview(
                index, rows, scopes, configured, incomplete, editor.service._freshness, account_failure
            )
    return view


def _checks_complete(inventory, component, scopes):
    files = component == "FILES"
    key = "files" if files else access.COLLECTIONS[component]
    diagnostics = inventory.get("diagnostics")
    domain = diagnostics.get(key) if isinstance(diagnostics, dict) else None
    if files:
        # Account discovery counts the root and immediate children. Later own
        # observations can add deep folders or replace/remove a saved row.
        first_level = {
            scope.path for scope, row in scopes.items()
            if row and scope.component == "FILES" and "/" not in scope.path
        }
        if "" not in first_level:
            return False
        if isinstance(domain, dict) and "discovered" in domain:
            discovered = domain["discovered"]
            if type(discovered) is not int or discovered != len(first_level):
                return False
    if diagnostics is not None:
        return (
            isinstance(domain, dict)
            and domain.get("inventory_complete") is True
            and (domain.get("status") == "ok" if files else domain.get("status") in ("ok", "empty"))
            and (files or domain.get("checks_complete") is True)
        )
    # Older snapshots can report a complete inventory but must explicitly
    # include its section. The separate per-row checks still apply.
    return (
        inventory.get("complete") is True
        and not inventory.get("results_may_be_truncated")
        and not inventory.get("errors")
        and not inventory.get("limitations")
        and all(k in inventory for k in (("file_roots", "folders") if files else (key,)))
    )


def _oldest_freshness(*checks):
    return max(checks, key=lambda check: (check["age_seconds"] is None, check["age_seconds"] or 0))


def _overview_check(checked_at, freshness):
    return {**freshness, "checked_at": checked_at if freshness["age_seconds"] is not None else None}


def _status_overview(index, rows, scopes, configured, incomplete, freshness, account_failure):
    """Summarize saved, presented evidence before the operator page is clipped."""
    conn = index.snapshot.connection
    inventory = conn["discovery_snapshot"]
    checked_at = inventory.get("checked_at")
    if account_failure:
        checked_at = account_failure["checked_at"]
    account_check = _overview_check(checked_at, freshness(checked_at))
    if account_failure:
        account_check["state"] = "failed"
    components = {
        component: dict.fromkeys(("known", "selected", "readable", "creatable", "updatable", "blocked"), 0)
        | {
            "complete": not account_failure and account_check["state"] != "unknown"
            and component not in incomplete and _checks_complete(inventory, component, scopes),
            "freshness": account_check,
        }
        for component in access.OPERATIONS
    }
    checks = {
        scope: _overview_check(row["server"]["checked_at"], row["server"]["freshness"])
        for scope, row in zip(scopes, rows, strict=True)
    }
    legacy = index.snapshot.mode == "legacy"
    if legacy:
        legacy_root_check = next(
            (check for scope, check in checks.items() if scope.component == "FILES" and scope.path == ""),
            _overview_check(None, freshness(None)),
        )
    targets = []
    for scope, row in zip(scopes, rows, strict=True):
        component = components[scope.component]
        known = bool(scopes[scope])
        desired = set(configured.get(scope, ()))
        check = checks[scope]
        component["known"] += known
        component["selected"] += bool(desired)
        read_status = row["server"]["read_check"]
        verified = (
            read_status == "ok" and row["server"]["state"] == "verified" and check["state"] != "unknown"
        )
        if (known and not verified) or (desired and not known):
            component["complete"] = False
        if known and read_status in ("ok", "failed") and not account_failure:
            component["freshness"] = _oldest_freshness(component["freshness"], check)

        evidence = _oldest_freshness(account_check, check)
        if scope.component == "FILES":
            grant = _file_grant(index.by_path, scope.path)
            if grant is not None:
                evidence = _oldest_freshness(evidence, checks[grant.scope])
            elif legacy:
                evidence = _oldest_freshness(evidence, legacy_root_check)
        fresh = evidence["state"] == "fresh" and not account_failure
        effective = set(row["effective_operations"]) if fresh else set()
        component["blocked"] += bool(desired - effective)
        component["readable"] += "read" in effective
        component["updatable"] += "update" in effective
        create_operation = "create_file" if scope.component == "FILES" else "create"
        component["creatable"] += create_operation in effective
        if "create_file" in effective and not _masked_scope(scope, conn["app_password"]):
            targets.append({"scope": row["scope"], "freshness": evidence})

    targets.sort(key=lambda row: (row["scope"]["path"], row["scope"]["file_id"]))
    targets = _bounded_list(targets[:MAX_FILE_CREATE_TARGETS], MAX_FILE_CREATE_TARGET_BYTES - 2)
    return {
        "schema_version": 1,
        "account_id": account_namespace(conn),
        "connection_revision": conn["revision"],
        "components": components,
        "file_create_targets": targets,
        "file_create_targets_truncated": len(targets) < components["FILES"]["creatable"],
    }


def _wire_size(value):
    # Match SetupHandler._reply, including its default JSON whitespace.
    return len(json.dumps(value, ensure_ascii=False).encode())


def _bounded_list(items, budget):
    result = []
    for item in items:
        size = _wire_size(item) + 2
        if size > budget:
            break
        result.append(item)
        budget -= size
    return result


def status_page_params(params):
    if not params:
        return None, 0
    _exact(params, ("expected_revision", "access_offset"))
    offset = params["access_offset"]
    if (
        not _token(params["expected_revision"], "[a-f0-9]{32}")
        or type(offset) is not int
        or not 1 <= offset < MAX_DISCOVERED_ROWS + access.MAX_GRANTS
    ):
        _fail()
    return params["expected_revision"], offset


def bound_status(result, offset=0):
    if not offset and _wire_size({"ok": True, "result": result}) < STATUS_BYTE_LIMIT:
        return result
    # Keep policy rows and the associated signed continuation ahead of duplicate
    # diagnostic inventory. Nothing trimmed from this DTO changes the stored policy.
    rows = result["access"]["rows"]
    if offset >= len(rows) and offset:
        _fail("access-page-unavailable")
    preview = result["preview"]
    summary = {k: preview[k] for k in ("server_url", "user_id", "checked_at", "files_depth") if k in preview}
    if isinstance(preview.get("display_name"), str):
        summary["display_name"] = preview["display_name"][:512]
    summary.update(complete=False, results_may_be_truncated=True, limitations=["operator-response-limit"])
    for key in ("calendars", "task_lists", "addressbooks", "file_roots", "folders", "talk_rooms", "errors"):
        summary[key] = _bounded_list(preview.get(key, []), STATUS_BYTE_LIMIT // 128)
    if _wire_size(preview.get("diagnostics", {})) < STATUS_BYTE_LIMIT // 16:
        summary["diagnostics"] = preview.get("diagnostics", {})
    result["preview"] = summary
    result["complete"] = False
    result["resources"] = _bounded_list(result["resources"], STATUS_BYTE_LIMIT // 32)
    failure = result.get("refresh_failure")
    if failure and _wire_size(failure) > STATUS_BYTE_LIMIT // 32:
        result["refresh_failure"] = {
            k: failure[k] for k in ("account_id", "revision", "state", "code", "checked_at")
        }
    detail = result["access"]
    detail.update(rows=[], next_offset=None)
    budget = STATUS_BYTE_LIMIT - _wire_size({"ok": True, "result": result}) - 512
    detail["rows"] = _bounded_list(rows[offset:], budget)
    end = offset + len(detail["rows"])
    if end < len(rows):
        if end == offset:
            _fail("access-page-unavailable")
        detail.update(
            next_offset=end,
            complete=False,
            code="operator-response-limit",
            next_step="Weitere Freigaben für dieselbe Verbindungsrevision laden.",
        )
    elif offset:
        detail.update(
            complete=False,
            code="operator-response-limit",
            next_step="Diese Zeilen ergänzen die vorherigen Abschnitte derselben Revision.",
        )
    return result


def integrity_status():
    return {
        "connected": False,
        "revision": "",
        "state": "failed",
        "code": "policy-integrity-error",
        "access": {
            "mode": "integrity-error",
            "binding": None,
            "rows": [],
            "complete": False,
            "code": "policy-integrity-error",
            "next_step": INTEGRITY_STEP,
        },
    }


def _withheld(code, scope=None, operation=None, next_step=MEASURE_STEP):
    return {
        "scope": scope.to_dict() if scope else None,
        "operation": operation,
        "code": code,
        "next_step": next_step,
    }


def _legacy_operations(scope, resource):
    if resource is None or (
        scope.component != "FILES" and scope.component not in resource.metadata["components"]
    ):
        return set()
    old: set[str] = set()
    if "read" in resource.permissions:
        old.update(READ_OPS[scope.component])
    if "create" in resource.permissions:
        old.update({"create_file", "mkdir"} if scope.component == "FILES" else {"create"})
    if scope.component != "FILES" and "update" in resource.permissions:
        old.add("update")
    return old


def _legacy_origin(snap):
    """Capture the finite effective GUI projection before any inventory replacement."""
    conn = snap.connection
    inventory = conn["discovery_snapshot"]
    proof = {
        "account_id": account_namespace(conn),
        "owner_user_id": conn["user_id"],
        "origin_revision": conn["revision"],
        "grants": [],
        "configured": [],
        "limitations": [],
    }
    if not snap.managed or not access.inventory_matches_account(conn, inventory):
        proof["limitations"].append("legacy-scope-unconfirmed")
        return proof
    resources = {r.id: r for r in connection_resources(conn)}
    rows, complete = _scopes(snap)
    if not complete:
        proof["limitations"].append("legacy-scope-unconfirmed")
    if len(resources) > MAX_EDITS:
        proof["limitations"] = ["legacy-bounded-explicit-selection"]
        return proof
    index = EvidenceIndex(snap)
    for scope in rows:
        if scope.component == "FILES" and scope.path != "":
            continue
        old = _legacy_operations(scope, resources.get(scope.resource_id))
        if not old:
            continue
        proof["configured"].append(access.Grant(scope, tuple(sorted(old))).to_dict())
        measured = set(_row(index.scoped(scope), scope, rows[scope])["server"]["operations"])
        proof["grants"].append(access.Grant(scope, tuple(sorted(old & measured))).to_dict())
    if len(proof["grants"]) > MAX_EDITS or len(resources) > MAX_EDITS:
        proof.update(grants=[], configured=[], limitations=["legacy-bounded-explicit-selection"])
    return proof


def updated_connection(snap, replacement):
    """Retain legacy baseline and account-bound measured folders across same-account inventory updates."""
    # Only a successfully verified replacement inventory enters this function.
    replacement = dict(replacement)
    replacement.pop("setup_account_failure", None)
    old = snap.connection
    if not old or account_namespace(old) != account_namespace(replacement):
        return replacement
    if snap.mode == "legacy" and snap.managed:
        origin = old.get("legacy_access_origin")
        return {**replacement, "legacy_access_origin": origin if origin is not None else _legacy_origin(snap)}
    if not snap.policy:
        return replacement
    old_inv = old["discovery_snapshot"]
    new_inv = replacement["discovery_snapshot"]
    if not access.inventory_matches_account(old, new_inv) or not access.inventory_matches_account(
        old, old_inv
    ):
        return replacement
    # The account scan covers depth one. It can invalidate an earlier observation
    # but never renew its timestamp or expand its C/K flags.
    root_rows: dict[str, list[dict]] = {}
    for row in new_inv.get("folders", []):
        root_rows.setdefault(row.get("path"), []).append(row)
    old_roots = {r.get("path"): r for r in old_inv.get("folders", []) if "/" not in str(r.get("path"))}
    selected = {g.scope.path: g.scope for g in snap.policy.grants if g.scope.component == "FILES"}
    retained = {}
    for row in old_inv.get("folders", []):
        path = row.get("path")
        if (
            not path
            or path not in selected
            or row.get("file_id") != selected[path].file_id
            or row.get("source_account_id") != new_inv["account_id"]
            or not row.get("checked_at")
        ):
            continue
        top = path.split("/")[0]
        now = root_rows.get(top, [])
        previous = old_roots.get(top, {})
        if (
            len(now) != 1
            or not now[0].get("file_id")
            or now[0]["file_id"] != previous.get("file_id")
            or now[0].get("privileges_verified") is not True
            or now[0].get("can_read") is not True
        ):
            continue
        # A new exact check supersedes the prior check. A parent inventory
        # may retain it only for the same identity and flags.
        if top == path and (
            now[0].get("read_check", {}).get("status") != "skipped"
            or row.get("file_permissions") != now[0].get("file_permissions")
        ):
            continue
        retained[path] = row
    if retained:
        remaining = [r for r in new_inv.get("folders", []) if r.get("path") not in retained]
        return {
            **replacement,
            "discovery_snapshot": {**new_inv, "folders": remaining + list(retained.values())},
        }
    return replacement


def _legacy(snap, confirm_root):
    conn = snap.connection
    origin = conn.get("legacy_access_origin")
    if origin is None:
        origin = _legacy_origin(snap)
    if (
        not isinstance(origin, dict)
        or origin.get("account_id") != account_namespace(conn)
        or (
            origin.get("owner_user_id") != conn["user_id"]
            or not _token(origin.get("origin_revision"), "[a-f0-9]{32}")
        )
    ):
        return {}, {}, [_withheld("legacy-scope-unconfirmed", next_step=LEGACY_STEP)]
    proof = {**origin, "account_root_confirmed": confirm_root, "connection_revision": conn["revision"]}
    current = _legacy_origin(snap)
    before = {access.Scope(**g["scope"]): g["operations"] for g in current["grants"] if g["operations"]}
    withheld = [
        _withheld(
            code,
            next_step=(
                "Alle bisherigen Freigaben außerhalb der angezeigten Auswahl werden entzogen. Auswahl prüfen."
                if code == "legacy-bounded-explicit-selection"
                else LEGACY_STEP
            ),
        )
        for code in sorted(set(origin["limitations"]) | set(current["limitations"]))
    ]
    original = {access.Scope(**g["scope"]): g["operations"] for g in origin["grants"]}
    configured = {access.Scope(**g["scope"]): g["operations"] for g in origin["configured"]}
    candidate, rejected = access.migration_candidate(snap, proof)
    after = {g.scope: list(g.operations) for g in candidate.grants if g.operations}
    account_failed = bool(access.account_failure_observation(snap))
    if account_failed:
        before, after = {}, {}
    scopes = before.keys() | configured.keys() | original.keys()
    for scope in sorted(scopes, key=lambda s: (s.key, s.file_id or "")):
        old = set(before.get(scope, ())) | set(configured.get(scope, ())) | set(original.get(scope, ()))
        for op in sorted(old - set(after.get(scope, ()))):
            step = MEASURE_STEP
            if account_failed:
                reason = "rights-unverified"
            elif op not in configured.get(scope, ()):
                reason, step = "legacy-outside-original-selection", LEGACY_STEP
            elif scope.path == "" and not confirm_root:
                reason, step = "account-root-confirmation-required", ROOT_STEP
            else:
                reason = _server_blocker(snap, scope, op)
                if not reason:
                    if op not in original.get(scope, ()):
                        reason, step = "legacy-original-proof-missing", LEGACY_STEP
                    elif scope.component == "FILES" and "file-scope-incomplete" in rejected:
                        reason = "file-scope-incomplete"
                    else:
                        reason = "legacy-right-unconfirmed"
            withheld.append(_withheld(reason, scope, op, step))
    for code in ("legacy-scope-ambiguous", "file-scope-incomplete"):
        if code in rejected:
            withheld.append(_withheld(code, next_step=LEGACY_STEP))
    if not conn["discovery_snapshot"].get("complete") or not before:
        withheld.append(_withheld("legacy-scope-unconfirmed", next_step=LEGACY_STEP))
    return before, after, withheld


def _workflows(components, legacy, binding_changes):
    effects = []
    if binding_changes:
        effects.extend(
            [
                {
                    "workflow": "pending-actions",
                    "effect": "Alte Einzelfreigaben und Pläne brauchen eine neue Bindung.",
                },
                {
                    "workflow": "search-and-sync",
                    "effect": "Alte Indexstände verlieren ihre Bindung. Sync-Jobstatus separat prüfen.",
                },
            ]
        )
    descriptions = {
        "VEVENT": ("calendar", "Terminsuche und Terminbearbeitung prüfen den ausgewählten Kalender."),
        "VTODO": ("tasks", "Lesen, Anlegen und Abschließen prüfen die gewählte Aufgabenliste."),
        "VCARD": ("contacts", "Kontaktsuche und Kontaktänderungen prüfen das gewählte Adressbuch."),
        "FILES": ("workspace", "Die Auswahl gilt für den Unterbaum. Engere Ordnerfreigaben haben Vorrang."),
    }
    for component, (workflow, effect) in descriptions.items():
        if component in components or legacy:
            effects.append({"workflow": workflow, "effect": effect})
    if legacy:
        for workflow in ("invoice-archive", "mail-calendar", "deck-orders"):
            effects.append(
                {
                    "workflow": workflow,
                    "effect": "Kein bestätigtes Automationsziel. Einrichtung getrennt prüfen.",
                }
            )
    return effects


@dataclass
class Candidate:
    identifier: str
    params: dict
    binding: access.Binding
    deadline: float
    expires_at: str
    digest: str = ""


def _masked_scope(scope: access.Scope, secret: str) -> bool:
    return scope.component == "FILES" and any(
        secret in part for part in (scope.path or "", scope.file_id or "")
    )


class AccessEditor:
    def __init__(self, service):
        self.service = service
        self.candidates: dict[str, Candidate] = {}
        self.folders = OrderedDict()
        self._selector_key = secrets.token_bytes(32)

    def _revoke_handle(self, snap, scope):
        wire = _encoded([snap.binding.to_dict(), snap.connection["revision"], scope.to_dict()])
        return hmac.digest(self._selector_key, wire, "sha256").hex()

    def present_rows(self, snap, rows, *, account_failed=None):
        grants = {g.scope for g in snap.policy.grants} if snap.policy else set()
        secret = snap.connection["app_password"]
        if account_failed is None:
            account_failed = bool(self.service.account_failure(snap))
        for row in rows:
            row["server"]["freshness"] = self.service._freshness(row["server"]["checked_at"])
            scope = access.Scope(**row["scope"])
            if account_failed:
                row["blockers"].update(dict.fromkeys(row["effective_operations"], "rights-unverified"))
                row["effective_operations"] = []
                row["supported_levels"] = ["off"]
                row["next_step"] = "Konto erneut prüfen."
            if _masked_scope(scope, secret):
                if scope in grants:
                    row["revoke_handle"] = self._revoke_handle(snap, scope)
                    row["supported_levels"] = ["off"]
                else:
                    row["supported_levels"] = []

    def _resolve_edits(self, snap, params):
        if not any("revoke_handle" in edit for edit in params["edits"]):
            return params
        masked = {}
        secret = snap.connection["app_password"]
        for grant in snap.policy.grants if snap.policy else ():
            scope = grant.scope
            if _masked_scope(scope, secret):
                masked[self._revoke_handle(snap, scope)] = scope.to_dict()
        edits = []
        for edit in params["edits"]:
            if "revoke_handle" in edit:
                scope = masked.get(edit["revoke_handle"])
                if scope is None:
                    _fail("scope-unbound")
                edit = {"scope": scope, "level": "off"}
            edits.append(edit)
        resolved = {**params, "edits": edits}
        validate("access.preview", resolved)
        return resolved

    def prune(self):
        for actor, candidate in list(self.candidates.items()):
            if candidate.deadline <= self.service.clock():
                del self.candidates[actor]

    def _folder_key(self, conn, path):
        inventory = conn["discovery_snapshot"]
        return (conn["server_url"], conn["user_id"], inventory.get("account_id"), conn["revision"], path)

    def opening(self, conn, path):
        # A failed or contradictory newer check must not reuse an older measurement.
        key = self._folder_key(conn, path)
        for actor, candidate in list(self.candidates.items()):
            if candidate.params["expected_revision"] == conn["revision"] and (
                not candidate.params["expected_policy_revision"]
                or any(e["scope"]["path"] == path for e in candidate.params["edits"])
            ):
                del self.candidates[actor]
        if key not in self.folders and len(self.folders) >= 128:
            self.folders.popitem(last=False)
        self.folders[key] = None
        self.folders.move_to_end(key)

    def opened(self, snap, path, result):
        conn = snap.connection
        if result.get("path") != path or not access.inventory_matches_account(
            conn, conn["discovery_snapshot"]
        ):
            return None
        matches = [row for row in result.get("folders", []) if row.get("path") == path]
        if len(matches) != 1:
            return None
        raw = {
            **matches[0],
            "checked_at": matches[0].get("checked_at", result.get("checked_at")),
            "source_account_id": conn["discovery_snapshot"]["account_id"],
        }
        try:
            scope = _scope_for(conn, "FILES", raw)
        except (ValueError, TypeError, AttributeError, KeyError, ConnectionError):
            return None
        # Only the raw own row from the authenticated PROPFIND can provide G/C/K.
        self.folders[self._folder_key(conn, path)] = raw
        measured = self._measured(snap, [scope])
        return _row(measured, scope, raw)

    def observe_folder(self, snap, path):
        """Publish new evidence for a saved or previously measured path before replying."""
        conn = snap.connection
        inventory = conn["discovery_snapshot"]
        group = "file_roots" if not path else "folders"
        old = [row for row in inventory.get(group, []) if row.get("path") == path]
        selected = any(
            g.scope.component == "FILES" and g.scope.path == path
            for g in (snap.policy.grants if snap.policy else ())
        )
        # A newly browsed unselected folder stays a temporary candidate. Once an
        # older own check exists (or a grant references it), it must be superseded.
        if not snap.managed or not (
            selected or any(r.get("read_check", {}).get("status") == "ok" for r in old)
        ):
            return snap
        raw = self.folders.get(self._folder_key(conn, path))
        if old == ([raw] if raw else []):
            return snap
        # Carry the owner's other exact observations from this same account and
        # revision into the new inventory. This keeps independently browsed draft
        # folders selectable without renewing a timestamp or changing any grant.
        prefix = self._folder_key(conn, path)[:-1]
        observed = {key[-1]: row for key, row in self.folders.items() if key[:-1] == prefix}
        next_inventory = dict(inventory)
        unavailable = {p for p, row in observed.items() if row is None}
        lost_evidence = False
        for section in ("file_roots", "folders"):
            applicable = {p: r for p, r in observed.items() if (section == "file_roots") == (p == "")}
            stored_rows = inventory.get(section, [])
            lost_evidence |= any(row.get("path") in unavailable for row in stored_rows)
            next_inventory[section] = [r for r in stored_rows if r.get("path") not in applicable]
            next_inventory[section].extend(r for r in applicable.values() if r is not None)
        if lost_evidence:
            next_inventory["complete"] = False
            diagnostics = inventory.get("diagnostics")
            files = diagnostics.get("files") if isinstance(diagnostics, dict) else None
            if isinstance(files, dict):
                root_readable = any(
                    (row.get("read_check") or {}).get("status") == "ok"
                    for row in next_inventory["file_roots"]
                )
                next_inventory["diagnostics"] = {**diagnostics, "files": {
                    **files, "inventory_complete": False, "root_readable": root_readable,
                    "status": "partial" if root_readable else "failed",
                }}
        updated = {**conn, "discovery_snapshot": next_inventory}
        if snap.mode == "legacy" and "legacy_access_origin" not in updated:
            updated["legacy_access_origin"] = _legacy_origin(snap)
        saved = self.service.store.save(updated, expected_revision=conn["revision"])
        current = self.service.store.snapshot()
        if current.binding != snap.binding or current.connection["revision"] != saved["revision"]:
            raise ConnectionError("nextcloud-policy-conflict")
        return current

    def _measured(self, snap, scopes):
        conn = snap.connection
        inv = dict(conn["discovery_snapshot"])
        for scope in scopes:
            if scope.component != "FILES":
                continue
            key = self._folder_key(conn, scope.path)
            if key not in self.folders:
                continue
            group = "file_roots" if scope.path == "" else "folders"
            rows = [r for r in inv.get(group, []) if r.get("path") != scope.path]
            raw = self.folders[key]
            inv[group] = rows + ([raw] if raw else [])
        return replace(snap, connection={**conn, "discovery_snapshot": inv})

    def _snapshot(self, params, snap=None):
        snap = snap or self.service.store.snapshot()
        if not snap.managed or not snap.connection:
            _fail("scope-unbound")
        if not access.inventory_matches_account(snap.connection, snap.connection["discovery_snapshot"]):
            _fail("scope-unbound")
        if account_namespace(snap.connection) != params["account_id"]:
            _fail("scope-unbound")
        self.service._revision(params, snap.connection)
        if (snap.binding.policy_revision if snap.binding else "") != params["expected_policy_revision"]:
            raise ConnectionError("nextcloud-policy-conflict")
        if not params["edits"] and snap.mode != "legacy":
            _fail()
        return snap

    def _plan(self, snap, params, binding):
        edits = [(access.Scope(**edit["scope"]), edit["level"]) for edit in params["edits"]]
        measured = self._measured(snap, [s for s, _ in edits])
        rows, _ = _scopes(measured, {scope for scope, _ in edits})
        legacy = snap.mode == "legacy"
        if legacy:
            before, after, withheld = _legacy(snap, params["confirm_root"])
            for name, next_step in (
                ("invoice-archive", "Das Ziel für das Rechnungsarchiv separat prüfen."),
                ("mail-calendar", "Die Kalenderauswahl für den Mailablauf separat prüfen."),
                ("deck-orders", "Board und Stack für Aufträge separat prüfen."),
            ):
                withheld.append(_withheld("legacy-" + name + "-unbound", next_step=next_step))
            touched = set(before) | set(after)
        else:
            before = {g.scope: list(g.operations) for g in snap.policy.grants}
            after, withheld, touched = dict(before), [], set()
        index = EvidenceIndex(measured)
        for scope, level in edits:
            if _masked_scope(scope, snap.connection["app_password"]) and level != "off":
                _fail("unsupported-access-level")
            if scope not in rows:
                _fail("rights-unverified")
            if (
                legacy
                and scope not in before
                and any(
                    w["code"] in ("legacy-bounded-explicit-selection", "legacy-scope-unconfirmed")
                    for w in withheld
                )
            ):
                withheld.append(_withheld("legacy-before-unconfirmed", scope, next_step=LEGACY_STEP))
            row = _row(index.scoped(scope), scope, rows[scope])
            levels = _levels(scope.component, set(row["server"]["operations"]))
            if level not in levels:
                _fail("unsupported-access-level")
            ops = sorted(levels[level])
            if scope.path == "" and set(ops) - set(before.get(scope, ())) and not params["confirm_root"]:
                _fail("account-root-confirmation-required")
            if legacy and scope.path == "" and ops and not params["confirm_root"]:
                _fail("account-root-confirmation-required")
            for op in sorted(
                ({"create_file", "mkdir"} if scope.component == "FILES" else {"create"}) - set(ops)
            ):
                if level in ("create", "update"):
                    withheld.append(_withheld("server-right-missing", scope, op))
            for old in list(after):
                if old != scope and old.key == scope.key:
                    del after[old]
                    touched.add(old)
            after[scope] = ops
            touched.add(scope)
        if self.service.account_failure(snap):
            existing = {g.scope: g.operations for g in snap.policy.grants} if snap.policy else {}
            if any(set(ops) - set(existing.get(scope, ())) for scope, ops in after.items()):
                _fail("rights-unverified")
        if len(after) > access.MAX_GRANTS:
            raise OnboardingError(
                "access-grant-limit",
                f"Maximal {access.MAX_GRANTS} Freigaberegeln. "
                "Bei voller Richtlinie nur bestehende Regeln bearbeiten; engere Aus-Regeln bleiben erhalten.",
            )
        policy = access.Policy(
            binding, tuple(access.Grant(s, tuple(after[s])) for s in sorted(after, key=lambda s: s.key))
        )
        changes = [
            {
                "scope": s.to_dict(),
                "name": str(rows.get(s, {}).get("name") or s.path or s.resource_id)[:512],
                "before_operations": before.get(s, list(_selected(snap, s))),
                "after_operations": after.get(s, []),
            }
            for s in sorted(touched, key=lambda s: (s.key, s.file_id or ""))
        ]
        return (
            measured,
            policy,
            {
                "changes": changes,
                "withheld": [
                    item
                    for item in withheld
                    if item["scope"] is None
                    or item["operation"] not in after.get(access.Scope(**item["scope"]), ())
                ],
                "affected_workflows": _workflows(
                    {s.component for s in touched}, legacy, legacy or policy != snap.policy
                ),
            },
        )

    def _view(self, actor, candidate, snap):
        measured, policy, diff = self._plan(snap, candidate.params, candidate.binding)
        if snap.policy:
            saved = {g.scope for g in snap.policy.grants}
            for change in diff["changes"]:
                scope = access.Scope(**change["scope"])
                if scope in saved and _masked_scope(scope, snap.connection["app_password"]):
                    change["revoke_handle"] = self._revoke_handle(snap, scope)
        view = {
            "state": "ready",
            "preview_id": candidate.identifier,
            "expires_at": candidate.expires_at,
            "expected_revision": candidate.params["expected_revision"],
            "expected_policy_revision": candidate.params["expected_policy_revision"],
            **diff,
        }
        if len(_encoded(view)) > MAX_PREVIEW_BYTES:
            _fail("access-preview-too-large")
        payload = [actor, candidate.params, policy.to_dict(), measured.connection["discovery_snapshot"], view]
        digest = hashlib.sha256(_encoded(payload)).hexdigest()
        return measured, policy, {**view, "preview_digest": digest}

    def preview(self, actor, params, snapshot):
        validate("access.preview", params)
        snap = self._snapshot(params, snapshot)
        params = self._resolve_edits(snap, params)
        self.candidates.pop(actor, None)
        if len(self.candidates) >= MAX_CANDIDATES:
            _fail("too-many-access-previews")
        candidate = Candidate(
            uuid.uuid4().hex,
            deepcopy(params),
            snap.binding or access.new_policy(snap.connection).binding,
            self.service.clock() + PREVIEW_SECONDS,
            (self.service.wall_clock() + timedelta(seconds=PREVIEW_SECONDS)).isoformat(),
        )
        _, _, view = self._view(actor, candidate, snap)
        candidate.digest = view["preview_digest"]
        self.candidates[actor] = candidate
        return view

    def apply(self, actor, params):
        validate("access.apply", params)
        candidate = self.candidates.get(actor)
        if not candidate or not hmac.compare_digest(candidate.identifier, params["preview_id"]):
            _fail("access-preview-unavailable")
        del self.candidates[actor]
        if not hmac.compare_digest(candidate.digest, params["preview_digest"]) or any(
            candidate.params[k] != params[k] for k in ("expected_revision", "expected_policy_revision")
        ):
            _fail("access-preview-changed")
        snap = self._snapshot(candidate.params)
        measured, policy, view = self._view(actor, candidate, snap)
        if self.service.clock() >= candidate.deadline or not hmac.compare_digest(
            candidate.digest, view["preview_digest"]
        ):
            _fail("access-preview-changed")
        record = {**measured.connection, "access_policy": policy.to_dict()}
        record.pop("legacy_access_origin", None)
        saved = self.service.store.save(
            record,
            expected_revision=params["expected_revision"],
            expected_policy_revision=params["expected_policy_revision"],
            confirm_account_root=candidate.params["confirm_root"],
        )
        current = self.service.store.snapshot()
        if (
            not current.policy
            or current.policy.grants != policy.grants
            or current.binding.account_id != candidate.binding.account_id
            or current.binding.owner_user_id != candidate.binding.owner_user_id
            or current.connection["revision"] != saved["revision"]
            or (snap.binding and current.binding.generation != snap.binding.generation)
        ):
            raise ConnectionError("nextcloud-connection-readback-failed")
        return self.service._status(current)
