"""Read-only diagnostics for the managed account Talk channel.

Reads only the worker's bounded content-free observation, never its SQLite
inbox, and never starts a worker. A live API probe is separate from worker health.
"""

from __future__ import annotations

import json
import math
import os
import re
import stat
import time
from pathlib import Path
from typing import Any

from .nextcloud_onboarding import (
    NextcloudCredentials,
    OnboardingError,
    SafeNextcloudTransport,
    credential_headers,
)
from .nextcloud_talk_account import account_identity

MAX_OBSERVATION_BYTES = 16 * 1024
OBSERVATION_MAX_AGE_SECONDS = 90
OBSERVATION_CODES = {
    "talk-disabled",
    "talk-configured",
    "talk-paired-no-history-replay",
    "talk-poll-ok",
    "talk-poll-failed",
}
COUNT_KEYS = ("accepted", "processing", "uncertain", "completed", "delivery_uncertain")


def invalid_managed_status(code: str) -> dict[str, Any]:
    return {
        "ok": False,
        "mode": "managed-account",
        "channel": "nextcloud-account-talk",
        "state": "invalid",
        "configured": False,
        "enabled": False,
        "connected": False,
        "issues": [code],
        "warnings": [],
        "ready_for_activation": False,
    }


def _observation(connection: dict[str, Any], *, now: float) -> dict[str, Any]:
    missing = {"present": False, "fresh": False, "healthy": False, "code": "talk-worker-not-observed"}
    configured = os.environ.get("OPENCLAW_CORE_DATA_DIR", "/var/lib/openclaw/core")
    directory = Path(configured)
    if not directory.is_absolute():
        return {**missing, "code": "talk-worker-status-path-invalid"}
    # Same projection path as TalkInbox.status_path, without constructing the
    # inbox (which would create/lock/migrate its database).
    path = directory / "nextcloud-talk.status.json"
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except FileNotFoundError:
        return missing
    except OSError:
        return {**missing, "code": "talk-worker-status-unreadable"}
    try:
        with os.fdopen(descriptor, "rb") as handle:
            info = os.fstat(handle.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077:
                raise ValueError
            raw = handle.read(MAX_OBSERVATION_BYTES + 1)
        if len(raw) > MAX_OBSERVATION_BYTES:
            raise ValueError
        value = json.loads(raw)
        if not isinstance(value, dict) or value.get("schema_version") != 1:
            raise ValueError
        observed_value = value.get("observed_at")
        if isinstance(observed_value, bool) or not isinstance(observed_value, (int, float)):
            raise ValueError
        observed = float(observed_value)
        if not math.isfinite(observed) or observed <= 0 or observed > now + 5:
            raise ValueError
        if type(value.get("enabled")) is not bool or type(value.get("connected")) is not bool:
            raise ValueError
        if value.get("code") not in OBSERVATION_CODES:
            raise ValueError
        counts = value.get("counts", {})
        if not isinstance(counts, dict):
            raise ValueError
        selected_counts = {key: counts[key] for key in COUNT_KEYS if key in counts}
        if any(type(count) is not int or count < 0 for count in selected_counts.values()):
            raise ValueError
        age = max(0.0, now - observed)
        account_matches = value.get("account") == account_identity(connection)
        revision_matches = value.get("connection_revision") == connection["revision"]
        selection_matches = value.get("room_count") == len(connection.get("talk", {}).get("room_tokens", []))
        fresh = age <= OBSERVATION_MAX_AGE_SECONDS
        healthy = bool(
            fresh
            and account_matches
            and revision_matches
            and selection_matches
            and value["enabled"]
            and value["connected"]
            and value["code"] in {"talk-poll-ok", "talk-paired-no-history-replay"}
        )
        return {
            "present": True,
            "fresh": fresh,
            "healthy": healthy,
            "observed_at": observed,
            "age_seconds": round(age, 3),
            "max_age_seconds": OBSERVATION_MAX_AGE_SECONDS,
            "account_matches": account_matches,
            "revision_matches": revision_matches,
            "selection_matches": selection_matches,
            "enabled": value["enabled"],
            "connected": value["connected"],
            "code": value["code"],
            "counts": selected_counts,
        }
    except (OSError, ValueError, TypeError, RecursionError):
        return {**missing, "present": True, "code": "talk-worker-status-invalid"}


def managed_status(connection: dict[str, Any]) -> dict[str, Any]:
    talk = connection.get("talk", {})
    enabled = talk.get("enabled") is True
    rooms = talk.get("room_tokens", [])
    users = talk.get("allowed_user_ids", [])
    if (
        type(talk.get("enabled", False)) is not bool
        or not isinstance(rooms, list)
        or len(rooms) > 32
        or any(not isinstance(room, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", room) for room in rooms)
        or not isinstance(users, list)
        or len(users) > 32
        or any(not isinstance(user, str) or not 1 <= len(user) <= 512 for user in users)
        or (enabled and (not rooms or not users))
    ):
        return invalid_managed_status("talk-pairing-invalid")
    worker = _observation(connection, now=time.time())
    issues: list[str] = []
    warnings: list[str] = []
    state = "disabled"
    if not enabled:
        warnings.append("Talk ist in der gespeicherten Kontokonfiguration ausgeschaltet.")
        if worker.get("connected") is True:
            warnings.append("Die letzte Workerbeobachtung liegt vor der bestaetigten Abschaltung.")
    elif worker["code"] in {
        "talk-worker-status-invalid",
        "talk-worker-status-path-invalid",
        "talk-worker-status-unreadable",
    }:
        state = "invalid"
        issues.append(worker["code"])
    elif not worker["present"]:
        state = "not-observed"
        issues.append("talk-worker-not-observed")
    elif not worker.get("account_matches") or not worker.get("revision_matches"):
        state = "pending"
        issues.append("talk-worker-configuration-not-observed")
    elif not worker["fresh"]:
        state = "stale"
        issues.append("talk-worker-status-stale")
        warnings.append("Eine alte Beobachtung beweist weder einen laufenden Worker noch einen Ausfall.")
    elif worker["code"] == "talk-poll-failed":
        state = "failed"
        issues.append("talk-poll-failed")
    elif not worker["healthy"]:
        state = "pending"
        issues.append("talk-worker-connection-not-observed")
    else:
        state = "active"
        counts = worker.get("counts", {})
        if counts.get("uncertain", 0) or counts.get("delivery_uncertain", 0):
            state = "degraded"
            issues.append("talk-processing-or-delivery-uncertain")
    return {
        "ok": not issues,
        "mode": "managed-account",
        "channel": "nextcloud-account-talk",
        "state": state,
        "desired_state": "ON" if enabled else "OFF",
        "configured": bool(rooms and users),
        "account_configured": True,
        "enabled": enabled,
        "connected": bool(enabled and worker["healthy"]),
        "room_count": len(rooms),
        "allowed_user_count": len(users),
        "worker": worker,
        "ready_for_activation": False,
        "setup_required": not enabled,
        "issues": issues,
        "warnings": warnings,
        "limitations": ["Workerbeobachtung ist keine Zustellbestaetigung fuer einzelne Nachrichten."],
    }


def managed_doctor(connection: dict[str, Any], *, live: bool, timeout: float) -> dict[str, Any]:
    report = managed_status(connection)
    live_report: dict[str, Any] = {"requested": live, "ok": None, "worker_health_proven": False}
    if not live:
        report["live"] = live_report
        return report
    try:
        if not math.isfinite(timeout) or not 0 < timeout <= 10:
            raise OnboardingError("talk-doctor-timeout-invalid")
        transport = SafeNextcloudTransport(
            connection["server_url"],
            allow_private=True,
            expected_ips=connection["expected_ips"],
            timeout_seconds=timeout,
            max_response_bytes=262_144,
        )
        credentials = NextcloudCredentials(
            server_url=connection["server_url"],
            login_name=connection["login_name"],
            app_password=connection["app_password"],
            user_id=connection["user_id"],
        )
        response = transport.request(
            "GET",
            "ocs/v2.php/apps/spreed/api/v4/room?noStatusUpdate=1",
            headers=credential_headers(credentials),
        )
        if response.status != 200:
            raise OnboardingError("talk-api-unavailable")
        payload = response.json()
        if not isinstance(payload, dict) or not isinstance(payload.get("ocs"), dict):
            raise OnboardingError("talk-api-response-invalid")
        envelope = payload["ocs"]
        meta = envelope.get("meta", {})
        rooms = envelope.get("data")
        if (
            not isinstance(meta, dict)
            or meta.get("status") != "ok"
            or str(meta.get("statuscode")) not in {"100", "200"}
            or not isinstance(rooms, list)
            or len(rooms) > 1000
        ):
            raise OnboardingError("talk-api-response-invalid")
        writable: set[str] = set()
        tokens: set[str] = set()
        for room in rooms:
            token = room.get("token") if isinstance(room, dict) else None
            if (
                not isinstance(token, str)
                or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", token)
                or token in tokens
            ):
                raise OnboardingError("talk-api-response-invalid")
            tokens.add(token)
            permissions = room.get("permissions")
            if type(permissions) is int and permissions & 128 and room.get("readOnly") == 0:
                writable.add(token)
        selected = set(connection.get("talk", {}).get("room_tokens", []))
        selected_ok = selected <= writable
        live_report = {
            "requested": True,
            "ok": selected_ok,
            "worker_health_proven": False,
            "api_reachable": True,
            "authenticated": True,
            "talk_app": True,
            "room_count": len(tokens),
            "selected_room_count": len(selected),
            "selected_rooms_writable": selected_ok,
            "tls_verified": True,
            "dns_binding_verified": True,
        }
        if not selected_ok:
            live_report["code"] = "talk-selected-room-not-writable"
    except OnboardingError as exc:
        live_report = {"requested": True, "ok": False, "worker_health_proven": False, "code": exc.code}
    except (OSError, ValueError, TypeError, KeyError):
        # Never echo exception text: transports can include URLs or credentials.
        live_report = {
            "requested": True,
            "ok": False,
            "worker_health_proven": False,
            "code": "talk-api-check-failed",
        }
    report["live"] = live_report
    report["ok"] = bool(report["ok"] and live_report["ok"])
    if not live_report["ok"]:
        report["issues"].append(live_report["code"])
        report["connected"] = False
        if report["enabled"]:
            report["state"] = "failed"
    return report
