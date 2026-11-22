"""Bounded operator setup service behind OpenClaw's operator.admin RPC.

No TCP listener and no generic file/command interface. Only the gateway plugin
and this owner receive the private control socket mount. Agent tools are not
registered for these operations. The gateway supplies the authenticated actor.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import hmac
import http.client
import json
import os
import re
import socket
import socketserver
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from typing import Any

from .nextcloud_access import account_failure_observation, path_segments
from .nextcloud_connection import (
    ConnectionError,
    ConnectionStore,
    account_namespace,
    connection_directory,
    public_connection,
)
from .nextcloud_onboarding import (
    LoginFlow,
    NextcloudCredentials,
    OnboardingError,
    SafeNextcloudTransport,
    credential_headers,
    discover_account,
    discover_folders,
    user_failure_diagnostics,
)
from .nextcloud_setup_access import (
    MAX_PROOF_BYTES,
    AccessEditor,
    bound_status,
    integrity_status,
    status_page_params,
    status_view,
    updated_connection,
)
from .setup_operator_proof import OperatorProofVerifier

MAX_BODY = 64 * 1024
MAX_FLOWS = 8
FLOW_LIFETIME = 20 * 60
DAV_EVIDENCE_MAX_AGE = 24 * 60 * 60
ROOM_TOKEN = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")
DIAGNOSTIC_FIELDS = (
    "schema_version",
    "availability",
    "user",
    "calendars",
    "task_lists",
    "addressbooks",
    "files",
    "talk",
)


@dataclass(slots=True)
class SetupFlow:
    owner: str
    flow: LoginFlow | None = field(repr=False)
    transport: SafeNextcloudTransport = field(repr=False)
    expires_at: float
    credentials: NextcloudCredentials | None = field(default=None, repr=False)
    snapshot: dict[str, Any] | None = field(default=None, repr=False)
    preview_digest: str = ""
    expected_revision: str = ""

    def cancel(self) -> dict[str, Any]:
        self.credentials = None
        self.snapshot = None
        self.preview_digest = ""
        return self.flow.cancel() if self.flow else {"state": "cancelled"}


def _redact_secret(value: Any, secret: str, *, field_name: str = "") -> Any:
    """Untrusted display metadata must not reflect an entered app password."""
    # DAV/account identifiers are locally hashed. Talk IDs instead embed the
    # upstream room token and must undergo the same redaction as display text.
    if (
        field_name == "resource_id"
        and isinstance(value, str)
        and re.fullmatch(
            r"nextcloud-(?:(?:calendar|addressbook|file-root|folder)-[a-f0-9]{12}"
            r"|account-[a-f0-9]{16}-(?:[a-f0-9]{16}|files))",
            value,
        )
    ):
        return value
    # Fixed access DTO enums remain usable when a short password happens to
    # occur in a protocol word. An arbitrary value from display metadata still
    # follows the normal redaction path.
    access_words = {"off", "read", "list", "search", "create", "update", "create_file", "mkdir"}
    if field_name in {
        "operations",
        "desired_operations",
        "effective_operations",
        "before_operations",
        "after_operations",
        "supported_levels",
    } and (isinstance(value, list) and all(isinstance(op, str) and op in access_words for op in value)):
        return value
    if (
        (field_name == "mode" and value in ("active", "legacy", "unconfigured", "integrity-error"))
        or (field_name == "component" and value in ("VEVENT", "VTODO", "VCARD", "FILES"))
        or (field_name == "operation" and isinstance(value, str) and value in access_words)
    ):
        return value
    # These are locally generated protocol fields, never upstream display text.
    # Redacting a short password out of a revision or status would break the
    # confirmation contract or turn a failed check into an unknown state.
    if field_name in {
        "state",
        "status",
        "code",
        "revision",
        "expected_revision",
        "preview_digest",
        "flow_id",
        "checked_at",
        "last_verified_at",
        "status_source",
        "preview_id",
        "policy_revision",
        "generation",
        "expected_policy_revision",
        "expires_at",
        "domain",
        "permissions",
        "kind",
        "account_id",
        "limitations",
    }:
        return value
    if isinstance(value, str):
        return value.replace(secret, "[redacted]") if secret else value
    if isinstance(value, list):
        return [_redact_secret(item, secret) for item in value]
    if isinstance(value, dict):
        return {key: _redact_secret(item, secret, field_name=key) for key, item in value.items()}
    return value


def _redact_operator_view(view, secret):
    clean = _redact_secret(view, secret)
    # Restore only locally issued owner handles in their closed operator DTOs.
    # Upstream display metadata can never exempt itself by choosing a field name.
    for group in ("rows", "changes", "access_row"):
        if group == "rows":
            raw_rows = view.get("access", {}).get(group, ())
            safe_rows = clean.get("access", {}).get(group, ())
        elif group == "changes":
            raw_rows, safe_rows = view.get(group, ()), clean.get(group, ())
        else:
            raw_rows = [view[group]] if group in view else []
            safe_rows = [clean[group]] if group in clean else []
        for raw, sanitized in zip(raw_rows, safe_rows, strict=True):
            if "revoke_handle" in raw:
                sanitized["revoke_handle"] = raw["revoke_handle"]
    if "source_revision" in view:
        clean["source_revision"] = view["source_revision"]
    if "source_revision" in view.get("refresh_failure", {}):
        clean["refresh_failure"]["source_revision"] = view["refresh_failure"]["source_revision"]
    return clean


def safe_preview(snapshot: dict[str, Any]) -> dict[str, Any]:
    # The discovery module returns content-free resource metadata. Copy only its
    # published DTO fields; unknown server fields cannot become GUI secrets.
    fields = (
        "server_url",
        "user_id",
        "display_name",
        "calendars",
        "task_lists",
        "addressbooks",
        "folders",
        "file_roots",
        "talk_rooms",
        "capabilities",
        "complete",
        "errors",
        "limitations",
        "files_depth",
        "oldest_collection_check_at",
        "checked_at",
        "results_may_be_truncated",
    )
    result = {key: snapshot[key] for key in fields if key in snapshot}
    diagnostics = snapshot.get("diagnostics")
    if isinstance(diagnostics, dict):
        result["diagnostics"] = {key: diagnostics[key] for key in DIAGNOSTIC_FIELDS if key in diagnostics}
    return result


class NextcloudSetupService:
    def __init__(
        self,
        store: ConnectionStore,
        *,
        transport_factory: Callable[..., Any] = SafeNextcloudTransport,
        login_factory: Callable[..., Any] = LoginFlow,
        discovery: Callable[..., Any] = discover_account,
        clock: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.store = store
        self.transport_factory = transport_factory
        self.login_factory = login_factory
        self.discovery = discovery
        self.clock = clock
        self.wall_clock = wall_clock
        self.refresh_failure: dict[str, Any] | None = None
        self.flows: dict[str, SetupFlow] = {}
        self.lock = threading.RLock()
        self.access = AccessEditor(self)

    def _prune(self) -> None:
        self.access.prune()
        for identifier, pending in list(self.flows.items()):
            if pending.expires_at <= self.clock():
                pending.cancel()
                del self.flows[identifier]

    def _pending(self, actor: str, parameters: dict[str, Any]) -> tuple[str, SetupFlow]:
        identifier = parameters.get("flow_id")
        pending = self.flows.get(identifier) if isinstance(identifier, str) else None
        if pending is None or not hmac.compare_digest(pending.owner, actor):
            raise OnboardingError("login-session-unavailable", "Anmeldevorgang fehlt oder ist abgelaufen")
        return str(identifier), pending

    def _connection(self) -> dict[str, Any]:
        value = self.store.read()
        if value is None:
            raise OnboardingError("not-connected", "Nextcloud ist noch nicht eingerichtet")
        return value

    def _transport(self, connection: dict[str, Any]) -> Any:
        return self.transport_factory(
            connection["server_url"], allow_private=True, expected_ips=tuple(connection["expected_ips"])
        )

    def _rediscover(self, current):
        transport, credentials = self._transport(current), self._credentials(current)
        if self.discovery is discover_account and "setup_account_failure" not in current:
            return discover_account(transport, credentials, previous_snapshot=current["discovery_snapshot"])
        return self.discovery(transport, credentials)

    @staticmethod
    def _credentials(connection: dict[str, Any]) -> NextcloudCredentials:
        return NextcloudCredentials(
            connection["server_url"],
            connection["login_name"],
            connection["app_password"],
            connection["user_id"],
        )

    @staticmethod
    def _revision(parameters: dict[str, Any], connection: dict[str, Any]) -> str:
        revision = parameters.get("expected_revision")
        if not isinstance(revision, str) or revision != connection["revision"]:
            raise ConnectionError("nextcloud-connection-conflict")
        return revision

    def status(self, parameters=None) -> dict[str, Any]:
        expected, offset = status_page_params(parameters or {})
        try:
            snapshot = self.store.snapshot()
            if expected and (snapshot.connection or {}).get("revision") != expected:
                raise ConnectionError("nextcloud-connection-conflict")
            return self._status(snapshot, offset=offset)
        except ConnectionError as exc:
            if str(exc) == "policy-integrity-error":
                return integrity_status()
            raise

    def _status(self, snapshot, *, offset=0) -> dict[str, Any]:
        connection = snapshot.connection
        result = public_connection(connection)
        if snapshot.managed or connection:
            result["access"] = status_view(snapshot)
            if connection:
                self.access.present_rows(snapshot, result["access"]["rows"])
        if connection:
            result["preview"] = safe_preview(connection["discovery_snapshot"])
            result.update(self._diagnostic_freshness(result["preview"]))
            failure = self.account_failure(snapshot)
            if failure:
                result["refresh_failure"] = failure
            result["last_verified_at"] = connection.get("last_verified_at", "")
            result["status_source"] = "last-verified-setup"
            result = bound_status(_redact_operator_view(result, connection["app_password"]), offset)
        return result

    def account_failure(self, snap):
        conn = snap.connection
        if not snap.managed:
            failure = self.refresh_failure
            return failure if failure and failure["revision"] == conn["revision"] else None
        observed = account_failure_observation(snap)
        if observed is None:
            return None
        return {
            "state": "failed",
            "code": observed["code"],
            "checked_at": observed["checked_at"],
            "diagnostics": user_failure_diagnostics(observed["code"]),
            "account_id": observed["account_id"],
            "source_revision": observed["source_revision"],
            "revision": conn["revision"],
        }

    def _record_account_failure(self, snap, exc):
        current = snap.connection
        failure = {
            **self._failed_login(exc, current["app_password"]),
            "account_id": account_namespace(current),
            "revision": current["revision"],
        }
        self.access.candidates.clear()
        if not snap.managed:
            self.refresh_failure = failure
            return failure
        observation = {
            "account_id": failure["account_id"],
            "owner_user_id": current["user_id"],
            "generation": snap.binding.generation if snap.binding else None,
            "evidence_at": current["discovery_snapshot"].get("checked_at"),
            "checked_at": failure["checked_at"],
            "source_revision": current["revision"],
            "code": failure["code"],
        }
        self.store.save(
            {**current, "setup_account_failure": observation}, expected_revision=current["revision"]
        )
        return self.account_failure(self.store.snapshot())

    def _freshness(self, checked_at: Any) -> dict[str, Any]:
        age = None
        if isinstance(checked_at, str) and len(checked_at) <= 64:
            try:
                measured = datetime.fromisoformat(checked_at)
                if measured.utcoffset() is not None:
                    elapsed = (self.wall_clock() - measured).total_seconds()
                    if elapsed >= 0:
                        age = elapsed
            except (ValueError, OverflowError):
                pass  # Unparseable timestamps carry no freshness evidence.
        state = "unknown" if age is None else "fresh" if age < DAV_EVIDENCE_MAX_AGE else "stale"
        return {"state": state, "age_seconds": age, "max_age_seconds": DAV_EVIDENCE_MAX_AGE}

    def _diagnostic_freshness(self, preview):
        """The current account check and each retained collection domain age independently."""
        current = self._freshness(preview.get("checked_at"))
        by_domain = {}
        for domain in ("calendars", "task_lists", "addressbooks"):
            retained = [
                {"checked_at": row.get("checked_at"), **self._freshness(row.get("checked_at"))}
                for row in preview.get(domain, ())
                if row.get("read_check_source") == "retained"
            ]
            if retained:
                by_domain[domain] = max(
                    retained, key=lambda age: (age["age_seconds"] is None, age["age_seconds"] or 0)
                )
        return {"diagnostics_freshness": current, "diagnostics_domain_freshness": by_domain}

    def _begin_login(self, actor: str) -> str:
        self.access.candidates.pop(actor, None)
        # Replacing either entry method invalidates this operator's old token,
        # credentials and approval digest before any new network operation.
        for identifier, previous in list(self.flows.items()):
            if previous.owner == actor:
                previous.cancel()
                del self.flows[identifier]
        if len(self.flows) >= MAX_FLOWS:
            raise OnboardingError("too-many-login-sessions", "Zu viele laufende Anmeldungen")
        return str((self.store.read() or {}).get("revision", ""))

    def _failed_login(self, exc: OnboardingError, secret: str = "") -> dict[str, Any]:
        # No exception message, upstream body, login name or URL enters this DTO.
        code = exc.code if re.fullmatch(r"[a-z][a-z0-9-]{0,127}", exc.code) else "setup-unavailable"
        result: dict[str, Any] = {
            "state": "failed",
            "code": code,
            "checked_at": self.wall_clock().isoformat(),
        }
        report = user_failure_diagnostics(code)
        diagnostics = getattr(exc, "diagnostics", None)
        if isinstance(diagnostics, dict):
            report.update({key: diagnostics[key] for key in DIAGNOSTIC_FIELDS if key in diagnostics})
        result["diagnostics"] = report
        return _redact_secret(result, secret)

    def _ready(self, identifier: str, pending: SetupFlow) -> dict[str, Any]:
        if pending.expires_at <= self.clock():
            pending.cancel()
            self.flows.pop(identifier, None)
            raise OnboardingError("login-session-unavailable", "Anmeldevorgang ist abgelaufen")
        if pending.snapshot is None or pending.credentials is None:
            raise OnboardingError("preview-required")
        uid = pending.snapshot.get("user_id")
        if not isinstance(uid, str) or not 1 <= len(uid) <= 512 or re.search(r"[\x00-\x1f/\\]", uid):
            raise OnboardingError("invalid-user-id", "Nextcloud meldet keine eindeutige Benutzer-ID")
        if pending.snapshot.get("server_url") != pending.credentials.server_url:
            raise OnboardingError("server-changed", "Nextcloud meldet eine andere Basisadresse")
        preview = _redact_secret(safe_preview(pending.snapshot), pending.credentials.app_password)
        pending.preview_digest = hashlib.sha256(json.dumps(preview, sort_keys=True).encode()).hexdigest()
        return {
            "state": "ready",
            "flow_id": identifier,
            "preview_digest": pending.preview_digest,
            "expected_revision": pending.expected_revision,
            "preview": preview,
            **self._diagnostic_freshness(preview),
        }

    def _credential_login(self, actor: str, parameters: dict[str, Any]) -> dict[str, Any]:
        revision = self._begin_login(actor)
        limits = {"server_url": 2048, "login_name": 512, "app_password": 4096}
        if set(parameters) != set(limits) or any(
            not isinstance(parameters[key], str)
            or not 1 <= len(parameters[key]) <= maximum
            or re.search(r"[\x00-\x1f\x7f]", parameters[key])
            for key, maximum in limits.items()
        ):
            raise OnboardingError("invalid-credentials", "Ungueltige Nextcloud-Anmeldedaten")
        pending = None
        try:
            transport = self.transport_factory(parameters["server_url"], allow_private=True)
            credentials = NextcloudCredentials(
                transport.base_url, parameters["login_name"], parameters["app_password"]
            )
            credential_headers(credentials)
            pending = SetupFlow(
                actor,
                None,
                transport,
                self.clock() + FLOW_LIFETIME,
                credentials=credentials,
                expected_revision=revision,
            )
            pending.snapshot = self.discovery(transport, credentials)
            identifier = uuid.uuid4().hex
            result = self._ready(identifier, pending)
            self.flows[identifier] = pending
            return result
        except OnboardingError as exc:
            if pending:
                pending.cancel()
            return self._failed_login(exc, parameters["app_password"])
        except Exception:
            if pending:
                pending.cancel()
            raise OnboardingError(
                "setup-unavailable", "Kontopruefung konnte nicht abgeschlossen werden"
            ) from None

    def dispatch(self, operation: str, parameters: dict[str, Any], *, actor: str) -> dict[str, Any]:
        if not isinstance(actor, str) or not 1 <= len(actor) <= 256 or re.search(r"[\x00-\x1f]", actor):
            raise OnboardingError(
                "operator-session-required", "Eine authentisierte OpenClaw-Operatorsitzung ist erforderlich"
            )
        if not isinstance(parameters, dict):
            raise OnboardingError("invalid-parameters")
        with self.lock:
            self._prune()
            if operation == "status":
                return self.status(parameters)
            if operation == "access.preview":
                snapshot = self.store.snapshot()
                view = self.access.preview(actor, parameters, snapshot)
                assert snapshot.connection is not None
                return _redact_operator_view(view, snapshot.connection["app_password"])
            if operation == "access.apply":
                return self.access.apply(actor, parameters)
            if operation == "login.start":
                revision = self._begin_login(actor)
                transport = self.transport_factory(parameters.get("server_url", ""), allow_private=True)
                flow = self.login_factory(transport, clock=self.clock)
                try:
                    view = flow.start()
                except Exception:
                    flow.cancel()
                    raise
                identifier = uuid.uuid4().hex
                self.flows[identifier] = SetupFlow(
                    actor,
                    flow,
                    transport,
                    self.clock() + FLOW_LIFETIME,
                    expected_revision=revision,
                )
                return {**view, "flow_id": identifier}
            if operation == "login.credentials":
                return self._credential_login(actor, parameters)
            if operation in {"login.poll", "login.cancel", "connection.apply"}:
                identifier, pending = self._pending(actor, parameters)
                if operation == "login.cancel":
                    view = pending.cancel()
                    del self.flows[identifier]
                    return view
                if operation == "connection.apply":
                    return self._apply(identifier, pending, parameters)
                if pending.snapshot is None:
                    try:
                        if pending.flow is None:
                            raise OnboardingError("login-session-unavailable")
                        view = pending.flow.poll()
                        if view.get("state") != "connected":
                            if view.get("state") != "pending":
                                pending.cancel()
                                del self.flows[identifier]
                                return view
                            return {**view, "flow_id": identifier}
                        pending.credentials = pending.flow.take_credentials()
                        pending.snapshot = self.discovery(pending.transport, pending.credentials)
                    except OnboardingError as exc:
                        result = self._failed_login(
                            exc, pending.credentials.app_password if pending.credentials else ""
                        )
                        pending.cancel()
                        del self.flows[identifier]
                        return result
                    except Exception:
                        pending.cancel()
                        del self.flows[identifier]
                        raise OnboardingError(
                            "setup-unavailable", "Kontopruefung konnte nicht abgeschlossen werden"
                        ) from None
                try:
                    return self._ready(identifier, pending)
                except OnboardingError as exc:
                    result = self._failed_login(
                        exc, pending.credentials.app_password if pending.credentials else ""
                    )
                    pending.cancel()
                    self.flows.pop(identifier, None)
                    return result
            if operation == "connection.refresh":
                before = self.store.snapshot()
                if before.connection is None:
                    raise OnboardingError("not-connected")
                current = before.connection
                revision = self._revision(parameters, current)
                try:
                    snapshot = self._rediscover(current)
                except OnboardingError as exc:
                    return self._record_account_failure(before, exc)
                updated = {
                    **current,
                    "discovery_snapshot": snapshot,
                    "last_verified_at": datetime.now(UTC).isoformat(),
                }
                self.store.save(
                    updated_connection(self.store.snapshot(), updated), expected_revision=revision
                )
                self.refresh_failure = None
                return self.status()
            if operation == "folders.list":
                snapshot = self.store.snapshot()
                if snapshot.connection is None:
                    raise OnboardingError("not-connected")
                current = snapshot.connection
                path = parameters.get("path", "")
                if not isinstance(path, str) or len(path.encode()) > 2048:
                    raise OnboardingError("invalid-parameters")
                path = path.strip("/")
                try:
                    path_segments(path)
                except ValueError:
                    raise OnboardingError("invalid-parameters") from None
                self.access.opening(current, path)
                try:
                    folders = discover_folders(self._transport(current), self._credentials(current), path)
                    row = self.access.opened(snapshot, path, folders)
                finally:
                    established = self.access.observe_folder(snapshot, path)
                if row:
                    self.access.present_rows(established, [row])
                    folders["access_row"] = row
                folders.update(
                    account_id=account_namespace(current),
                    source_revision=current["revision"],
                    revision=established.connection["revision"],
                    diagnostics_freshness=self._freshness(folders.get("checked_at")),
                )
                return _redact_operator_view(folders, current["app_password"])
            if operation == "talk.participants":
                current = self._connection()
                return _redact_secret(
                    self._participants(current, parameters.get("room_token", "")), current["app_password"]
                )
            if operation == "talk.configure":
                return self._configure_talk(parameters)
            raise OnboardingError("unknown-operation", "Unbekannte Einrichtungsoperation")

    def _apply(self, identifier: str, pending: SetupFlow, parameters: dict[str, Any]) -> dict[str, Any]:
        if pending.snapshot is None or pending.credentials is None or not pending.preview_digest:
            raise OnboardingError("preview-required", "Zuerst die erkannten Ressourcen pruefen")
        supplied = parameters.get("preview_digest")
        if not isinstance(supplied, str) or not hmac.compare_digest(pending.preview_digest, supplied):
            raise OnboardingError("preview-changed", "Die Einrichtungsvorschau hat sich geaendert")
        if parameters.get("confirm_account_access") is not True:
            raise OnboardingError("confirmation-required", "Die geprüfte Nextcloud-Kontoidentität bestätigen")
        if parameters.get("expected_revision") != pending.expected_revision:
            raise ConnectionError("nextcloud-connection-conflict")
        credentials = pending.credentials
        value = {
            "server_url": credentials.server_url,
            "login_name": credentials.login_name,
            "app_password": credentials.app_password,
            "user_id": pending.snapshot["user_id"],
            "expected_ips": list(pending.transport.expected_ips),
            "discovery_snapshot": pending.snapshot,
            "last_verified_at": datetime.now(UTC).isoformat(),
            "talk": {"enabled": False, "room_tokens": [], "allowed_user_ids": []},
        }
        try:
            value = updated_connection(self.store.snapshot(), value)
            self.store.save(value, expected_revision=pending.expected_revision)
        finally:
            # An uncertain/conflicted publication is terminal. Status/read-back
            # must establish the result before another explicit setup attempt.
            pending.cancel()
            del self.flows[identifier]
        return self.status()

    def _participants(self, current: dict[str, Any], room_token: str) -> dict[str, Any]:
        if not isinstance(room_token, str) or not ROOM_TOKEN.fullmatch(room_token):
            raise OnboardingError("invalid-talk-room")
        rooms = current["discovery_snapshot"].get("talk_rooms", [])
        if not any(item.get("token") == room_token for item in rooms):
            raise OnboardingError("talk-room-not-discovered", "Talk-Raum ist fuer dieses Konto nicht erfasst")
        response = self._transport(current).request(
            "GET",
            f"ocs/v2.php/apps/spreed/api/v4/room/{room_token}/participants?includeStatus=false",
            headers=credential_headers(self._credentials(current)),
        )
        if response.status != 200:
            raise OnboardingError(
                "talk-participants-unavailable", "Talk-Teilnehmer konnten nicht gelesen werden"
            )
        payload = response.json()
        try:
            envelope = payload["ocs"]
            if envelope["meta"]["status"] != "ok" or int(envelope["meta"]["statuscode"]) not in {100, 200}:
                raise ValueError
            items = envelope["data"]
            if not isinstance(items, list) or len(items) > 1000:
                raise ValueError
            users = []
            for item in items:
                if item.get("actorType") != "users":
                    continue
                actor_id = item.get("actorId")
                if not isinstance(actor_id, str) or not actor_id or len(actor_id) > 512:
                    raise ValueError
                users.append({"user_id": actor_id, "name": str(item.get("displayName") or actor_id)})
        except (KeyError, TypeError, ValueError, AttributeError):
            raise OnboardingError("invalid-talk-participants") from None
        return {"room_token": room_token, "participants": users}

    def _configure_talk(self, parameters: dict[str, Any]) -> dict[str, Any]:
        current = self._connection()
        revision = self._revision(parameters, current)
        if type(parameters.get("enabled")) is not bool:
            raise OnboardingError("invalid-talk-selection")
        if not parameters["enabled"]:
            updated = {**current, "talk": {**current.get("talk", {}), "enabled": False}}
            self.store.save(updated, expected_revision=revision)
            return self.status()
        rooms, users = parameters.get("room_tokens"), parameters.get("allowed_user_ids")
        if (
            not isinstance(rooms, list)
            or not 1 <= len(rooms) <= 16
            or not all(isinstance(room, str) and ROOM_TOKEN.fullmatch(room) for room in rooms)
        ):
            raise OnboardingError("invalid-talk-selection", "Einen oder mehrere konkrete Talk-Raeume waehlen")
        if (
            not isinstance(users, list)
            or not 1 <= len(users) <= 32
            or not all(isinstance(user, str) and 1 <= len(user) <= 512 for user in users)
        ):
            raise OnboardingError("invalid-talk-selection", "Die zugelassenen Benutzer auswaehlen")
        snapshot = self._rediscover(current)
        features = snapshot.get("capabilities", {}).get("spreed", {}).get("features", [])
        if "chat-reference-id" not in features:
            raise OnboardingError(
                "talk-reference-id-required",
                "Diese Talk-Version bietet keine sichere Erkennung eigener Antworten",
            )
        room_map = {item["token"]: item for item in snapshot.get("talk_rooms", [])}
        updated = {**current, "discovery_snapshot": snapshot}
        known_users: set[str] = set()
        for room in rooms:
            if room not in room_map or room_map[room].get("can_send") is not True:
                raise OnboardingError(
                    "talk-room-not-writable", "Ausgewaehlter Talk-Raum ist nicht beschreibbar"
                )
            participants = self._participants(updated, room)["participants"]
            members = {item["user_id"] for item in participants}
            if not members.intersection(users):
                raise OnboardingError(
                    "talk-sender-not-in-room", "Ein Raum enthaelt keinen ausgewaehlten Benutzer"
                )
            known_users.update(members)
        if set(users) - known_users:
            raise OnboardingError(
                "talk-sender-not-in-room", "Ausgewaehlter Benutzer ist kein bestaetigter Teilnehmer"
            )
        updated["talk"] = {
            "enabled": True,
            "room_tokens": sorted(set(rooms)),
            "allowed_user_ids": sorted(set(users)),
        }
        updated["last_verified_at"] = datetime.now(UTC).isoformat()
        self.store.save(updated_connection(self.store.snapshot(), updated), expected_revision=revision)
        return self.status()


class SetupServer(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True
    request_queue_size = 4

    def __init__(self, path: Path, service: NextcloudSetupService, *, proof_verifier: Any = None):
        self.service = service
        self.proof_verifier = proof_verifier or OperatorProofVerifier()
        self.slots = threading.BoundedSemaphore(4)
        super().__init__(str(path), SetupHandler)

    def process_request(
        self, request: socket.socket | tuple[bytes, socket.socket], client_address: Any
    ) -> None:
        if not self.slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        super().process_request(request, client_address)

    def process_request_thread(
        self, request: socket.socket | tuple[bytes, socket.socket], client_address: Any
    ) -> None:
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.slots.release()

    def service_actions(self) -> None:
        # Expired app passwords must also clear when the browser closes and no
        # later RPC arrives. Do not block the accept loop during discovery.
        if self.service.lock.acquire(blocking=False):
            try:
                self.service._prune()
            finally:
                self.service.lock.release()


class SetupHandler(BaseHTTPRequestHandler):
    server: SetupServer

    def setup(self) -> None:
        self.request.settimeout(10)
        super().setup()

    def log_message(self, format: str, *args: Any) -> None:
        # Never log HTTP payloads, credentials, flow IDs or resource names.
        pass

    def _reply(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        self._reply(
            200 if self.path == "/health" else 404,
            {"ok": self.path == "/health", "service": "nextcloud-setup"},
        )

    def do_POST(self) -> None:
        if self.path != "/rpc" or self.headers.get("Transfer-Encoding"):
            self._reply(400, {"ok": False, "error": {"code": "invalid-request"}})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if (
                not 1 <= length <= MAX_BODY
                or self.headers.get("Content-Type", "").split(";", 1)[0] != "application/json"
            ):
                raise ValueError
            payload = json.loads(self.rfile.read(length))
            if not isinstance(payload, dict) or set(payload) != {"operation", "parameters", "actor", "proof"}:
                raise ValueError
            if payload["operation"] in ("access.preview", "access.apply"):
                proof = payload["proof"]
                if (
                    isinstance(proof, dict)
                    and isinstance(proof.get("payload"), str)
                    and len(proof["payload"].encode()) > MAX_PROOF_BYTES
                ):
                    raise OnboardingError("invalid-access-parameters")
            self.server.proof_verifier.verify(payload)
            result = self.server.service.dispatch(
                payload["operation"], payload["parameters"], actor=payload["actor"]
            )
            self._reply(200, {"ok": True, "result": result})
        except OnboardingError as exc:
            self._reply(200, {"ok": False, "error": {"code": exc.code, "message": str(exc)}})
        except ConnectionError as exc:
            self._reply(
                200,
                {
                    "ok": False,
                    "error": {
                        "code": str(exc),
                        "message": "Einrichtung geaendert oder Verbindungsspeicher nicht bereit",
                    },
                },
            )
        except (ValueError, TypeError, KeyError, TimeoutError):
            self._reply(400, {"ok": False, "error": {"code": "invalid-request"}})
        except Exception:
            self._reply(
                500,
                {
                    "ok": False,
                    "error": {"code": "setup-unavailable", "message": "Einrichtungsdienst nicht bereit"},
                },
            )


def check_socket(path: Path) -> bool:
    connection = http.client.HTTPConnection("localhost", timeout=3)
    connection.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    connection.sock.settimeout(3)
    try:
        connection.sock.connect(str(path))
        connection.request("GET", "/health")
        response = connection.getresponse()
        return response.status == 200 and json.loads(response.read(1024)).get("ok") is True
    except (OSError, ValueError, http.client.HTTPException):
        return False
    finally:
        connection.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="OpenClaw operator-only Nextcloud setup owner")
    parser.add_argument(
        "--socket",
        type=Path,
        default=Path(os.environ.get("OPENCLAW_SETUP_SOCKET", "/run/openclaw-setup/control.sock")),
    )
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    if args.check:
        return 0 if check_socket(args.socket) else 1
    directory = connection_directory()
    if directory is None:
        raise SystemExit("OPENCLAW_NEXTCLOUD_CONNECTION_DIR is required")
    args.socket.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    lock_fd = os.open(args.socket.parent / ".owner.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(lock_fd)
        raise SystemExit("setup-owner-already-running") from None
    # Refuse overlapping owners; remove only a stale socket from the private mount.
    if args.socket.exists():
        if check_socket(args.socket):
            raise SystemExit("setup-owner-already-running")
        if not args.socket.is_socket():
            raise SystemExit("invalid-setup-socket")
        args.socket.unlink()
    server = SetupServer(args.socket, NextcloudSetupService(ConnectionStore(directory)))
    os.chmod(args.socket, 0o600)
    try:
        server.serve_forever(poll_interval=0.5)
    finally:
        server.server_close()
        args.socket.unlink(missing_ok=True)
        os.close(lock_fd)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
