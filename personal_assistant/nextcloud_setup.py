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

from .nextcloud_connection import (
    ConnectionError,
    ConnectionStore,
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
)
from .setup_operator_proof import OperatorProofVerifier

MAX_BODY = 64 * 1024
MAX_FLOWS = 8
FLOW_LIFETIME = 20 * 60
ROOM_TOKEN = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")


@dataclass(slots=True)
class SetupFlow:
    owner: str
    flow: LoginFlow = field(repr=False)
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
        return self.flow.cancel()


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
    )
    return {key: snapshot[key] for key in fields if key in snapshot}


class NextcloudSetupService:
    def __init__(
        self,
        store: ConnectionStore,
        *,
        transport_factory: Callable[..., Any] = SafeNextcloudTransport,
        login_factory: Callable[..., Any] = LoginFlow,
        discovery: Callable[..., Any] = discover_account,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.store = store
        self.transport_factory = transport_factory
        self.login_factory = login_factory
        self.discovery = discovery
        self.clock = clock
        self.flows: dict[str, SetupFlow] = {}
        self.lock = threading.RLock()

    def _prune(self) -> None:
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

    def status(self) -> dict[str, Any]:
        connection = self.store.read()
        result = public_connection(connection)
        if connection:
            result["preview"] = safe_preview(connection["discovery_snapshot"])
            result["last_verified_at"] = connection.get("last_verified_at", "")
            result["status_source"] = "last-verified-setup"
        return result

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
                return self.status()
            if operation == "login.start":
                for identifier, previous in list(self.flows.items()):
                    if previous.owner == actor:
                        previous.cancel()
                        del self.flows[identifier]
                if len(self.flows) >= MAX_FLOWS:
                    raise OnboardingError("too-many-login-sessions", "Zu viele laufende Anmeldungen")
                transport = self.transport_factory(parameters.get("server_url", ""), allow_private=True)
                flow = self.login_factory(transport, clock=self.clock)
                view = flow.start()
                identifier = uuid.uuid4().hex
                current = self.store.read()
                self.flows[identifier] = SetupFlow(
                    actor,
                    flow,
                    transport,
                    self.clock() + FLOW_LIFETIME,
                    expected_revision=str((current or {}).get("revision", "")),
                )
                return {**view, "flow_id": identifier}
            if operation in {"login.poll", "login.cancel", "connection.apply"}:
                identifier, pending = self._pending(actor, parameters)
                if operation == "login.cancel":
                    view = pending.cancel()
                    del self.flows[identifier]
                    return view
                if operation == "connection.apply":
                    return self._apply(identifier, pending, parameters)
                if pending.snapshot is None:
                    view = pending.flow.poll()
                    if view.get("state") != "connected":
                        return {**view, "flow_id": identifier}
                    if pending.credentials is None:
                        pending.credentials = pending.flow.take_credentials()
                    # A failed discovery may be retried without polling the
                    # already consumed server token or losing the app password.
                    pending.snapshot = self.discovery(pending.transport, pending.credentials)
                    preview = safe_preview(pending.snapshot)
                    pending.preview_digest = hashlib.sha256(
                        json.dumps(preview, sort_keys=True).encode()
                    ).hexdigest()
                return {
                    "state": "ready",
                    "flow_id": identifier,
                    "preview_digest": pending.preview_digest,
                    "expected_revision": pending.expected_revision,
                    "preview": safe_preview(pending.snapshot),
                }
            if operation == "connection.refresh":
                current = self._connection()
                revision = self._revision(parameters, current)
                snapshot = self.discovery(self._transport(current), self._credentials(current))
                updated = {
                    **current,
                    "discovery_snapshot": snapshot,
                    "last_verified_at": datetime.now(UTC).isoformat(),
                }
                self.store.save(updated, expected_revision=revision)
                return self.status()
            if operation == "folders.list":
                current = self._connection()
                return discover_folders(
                    self._transport(current), self._credentials(current), parameters.get("path", "")
                )
            if operation == "talk.participants":
                current = self._connection()
                return self._participants(current, parameters.get("room_token", ""))
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
            raise OnboardingError(
                "confirmation-required", "Den Zugriff auf die angezeigten Kontoressourcen bestaetigen"
            )
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
        self.store.save(value, expected_revision=pending.expected_revision)
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
        snapshot = self.discovery(self._transport(current), self._credentials(current))
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
        self.store.save(updated, expected_revision=revision)
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
