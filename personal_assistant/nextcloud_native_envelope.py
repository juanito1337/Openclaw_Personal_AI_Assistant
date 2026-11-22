"""Validate the native adapter’s expected-access envelope against a supplied snapshot."""

from __future__ import annotations

import json
import os

from .nextcloud_access import AccessSnapshot, ConnectionError

EXPECTED_ACCESS = "OPENCLAW_NC_EXPECTED_ACCESS"
NATIVE_CALL = "OPENCLAW_NC_NATIVE_CALL"
MAX_ENVELOPE_BYTES = 16384
_BINDING_KEYS = ("account_id", "owner_user_id", "generation", "policy_revision")


def native_access_projection(snapshot: AccessSnapshot) -> dict:
    if not snapshot.managed:
        raise ConnectionError("policy-integrity-error")
    if snapshot.mode == "active" and snapshot.binding:
        return {"mode": "active", "binding": {k: getattr(snapshot.binding, k) for k in _BINDING_KEYS}}
    if snapshot.mode in {"legacy", "unconfigured"} and snapshot.binding is None:
        return {"mode": snapshot.mode, "binding": None}
    raise ConnectionError("policy-integrity-error")


def _unique_object(items):
    obj = dict(items)
    if len(obj) != len(items):
        raise ValueError()
    return obj


def verify_native_access(snapshot: AccessSnapshot) -> None:
    """Check under the caller's admission lock in addition to normal scope guards."""
    marker = os.environ.get(NATIVE_CALL)
    raw = os.environ.get(EXPECTED_ACCESS)
    if marker is None and raw is None:
        return  # Direct CLI/worker. Normal per-target checks apply.
    if marker != "1" or raw is None:
        raise ConnectionError("scope-unbound")
    try:
        if len(raw) > MAX_ENVELOPE_BYTES or len(raw.encode()) > MAX_ENVELOPE_BYTES:
            raise ValueError()
        expected = json.loads(raw, object_pairs_hook=_unique_object)
        if not isinstance(expected, dict) or set(expected) != {"mode", "binding"}:
            raise ValueError()
        mode, binding = expected["mode"], expected["binding"]
        if mode == "active":
            if not isinstance(binding, dict) or set(binding) != set(_BINDING_KEYS):
                raise ValueError()
            if any(not isinstance(v, str) or not v or len(v) > 2048 for v in binding.values()):
                raise ValueError()
        elif mode not in ("legacy", "unconfigured") or binding is not None:
            raise ValueError()
    except (ValueError, RecursionError):
        raise ConnectionError("scope-invalid") from None
    if expected != native_access_projection(snapshot):
        raise ConnectionError("policy-changed")
