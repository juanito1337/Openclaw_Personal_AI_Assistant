"""Private provenance for persistent Nextcloud actions in the core owner's store."""

from __future__ import annotations

import re
from contextlib import nullcontext

from .nextcloud_access import Scope, decide_access
from .nextcloud_connection import admit_projection, load_access_snapshot

_COMPONENTS = {"calendar": "VEVENT", "tasks": "VTODO", "contacts": "VCARD"}
_UNBOUND = "scope-unbound: Ressource in der OpenClaw-Nextcloud-Einrichtung erneut auswaehlen"


def requires_action_scope(action_type):
    # Mail owns these legacy JSON-object plans. Unknown historical operations
    # have no independent local owner and cannot expose data under active policy.
    return action_type not in {"mail.move", "mail.send", "mail.review.correct"}


def action_admission(action_type, initial=None):
    if not requires_action_scope(action_type):
        return nullcontext(None)
    if initial is None:
        initial = load_access_snapshot()
    elif not initial.managed and load_access_snapshot().managed:
        raise PermissionError("policy-changed")
    return admit_projection(initial)


def action_provenance(snapshot, action_type, resource_id, payload):
    if snapshot is None or snapshot.mode == "legacy":
        return None
    if snapshot.mode != "active":
        raise PermissionError(_UNBOUND)
    domain, _, operation = action_type.partition(".")
    target = None
    if domain == "files":
        from .nextcloud_file_access import file_context

        operation = {"create": "create_file", "mkdir": "mkdir"}.get(operation)
        if not operation or payload.get("overwrite") or payload.get("managed_invoice_register"):
            raise PermissionError("operation-not-allowed")
        target = payload.get("path")
        scope = file_context(snapshot, target, operation).scope
        if scope.resource_id != resource_id:
            raise PermissionError(_UNBOUND)
    elif domain in _COMPONENTS and operation in {"create", "update"}:
        scope = Scope(resource_id, _COMPONENTS[domain])
    else:
        raise PermissionError(_UNBOUND)
    decision = decide_access(snapshot, scope, operation, target_path=target)
    if not decision.allowed:
        raise PermissionError(decision.code)
    return {
        "binding": snapshot.binding.to_dict(),
        "scope": scope.to_dict(),
        "operation": operation,
        "target_path": target,
    }


def action_payload(snapshot, action_type, resource_id, stored, *, require_read=False):
    # All historical and local business payloads were JSON objects. Only this
    # owner emits the new array wrapper; fields inside a payload have no authority.
    if isinstance(stored, dict):
        if snapshot is not None and snapshot.mode != "legacy":
            raise PermissionError(_UNBOUND)
        return stored
    if not (
        isinstance(stored, list)
        and len(stored) == 3
        and stored[0] == "nextcloud-action-v1"
        and isinstance(stored[1], dict)
        and isinstance(stored[2], dict)
    ):
        raise PermissionError("scope-invalid")
    provenance, payload = stored[1:]
    if snapshot is None or not snapshot.binding or provenance.get("binding") != snapshot.binding.to_dict():
        raise PermissionError("policy-changed")
    if action_provenance(snapshot, action_type, resource_id, payload) != provenance:
        raise PermissionError("scope-invalid")
    if require_read:
        visibility = decide_access(
            snapshot,
            provenance["scope"],
            "read",
            target_path=provenance["target_path"],
            expected_binding=provenance["binding"],
        )
        if not visibility.allowed:
            raise PermissionError(visibility.code)
    return payload


def idempotent_payload(action_type, payload):
    # The direct create tools generate a new DTSTAMP for the same user request.
    if action_type in {"calendar.create", "tasks.create"} and isinstance(payload.get("ics"), str):
        return {
            **payload,
            "ics": re.sub(r"(?m)^DTSTAMP:[0-9]{8}T[0-9]{6}Z(?=\r?$)", "DTSTAMP:", payload["ics"]),
        }
    return payload
