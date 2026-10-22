"""Account-authenticated Talk transport and durable channel handoff.

This is an internal, fixed-purpose channel worker, not an agent tool. The
gateway owns its database. Only the Python side reads account credentials;
the JSON-lines protocol carries bounded text and content-free status codes.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import sqlite3
import sys
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlencode

ROOM = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")
MAX_TEXT = 16_000
MAX_BATCH = 100
MAX_LINE = 128_000


class TalkAccountError(RuntimeError):
    """Public error code only; remote bodies and exception strings are private."""


def account_identity(connection: dict[str, Any]) -> str:
    value = [connection["server_url"], connection["user_id"]]
    return hashlib.sha256(json.dumps(value).encode()).hexdigest()


class TalkInbox:
    """Single-owner persistent inbox/outbox; interrupted work is never replayed."""

    def __init__(self, path: Path):
        self.status_path = path.with_suffix(".status.json")
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.lock = path.with_suffix(".lock").open("a+")
        try:
            fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.lock.close()
            raise TalkAccountError("talk-writer-already-active") from None
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        os.chmod(path, 0o600)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        version = self.db.execute("PRAGMA user_version").fetchone()[0]
        if version not in (0, 1):
            self.close()
            raise TalkAccountError("talk-state-schema-incompatible")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS cursors (
              account TEXT NOT NULL, room TEXT NOT NULL, message_id INTEGER NOT NULL,
              PRIMARY KEY(account,room));
            CREATE TABLE IF NOT EXISTS inbox (
              account TEXT NOT NULL, room TEXT NOT NULL, message_id INTEGER NOT NULL,
              sender TEXT NOT NULL, body TEXT NOT NULL, timestamp INTEGER NOT NULL,
              state TEXT NOT NULL, accepted REAL NOT NULL,
              PRIMARY KEY(account,room,message_id));
            CREATE TABLE IF NOT EXISTS outbox (
              reference_id TEXT PRIMARY KEY, account TEXT NOT NULL, room TEXT NOT NULL,
              inbound_id INTEGER NOT NULL, sequence INTEGER NOT NULL, body_digest TEXT NOT NULL,
              message_id INTEGER, state TEXT NOT NULL,
              UNIQUE(account,room,inbound_id,sequence));
            PRAGMA user_version=1;
        """)
        with self.db:
            self.db.execute("UPDATE inbox SET state='uncertain' WHERE state='processing'")
            self.db.execute("UPDATE outbox SET state='uncertain' WHERE state='sending'")

    def close(self) -> None:
        self.db.close()
        self.lock.close()

    def record_health(self, account: str, status: dict[str, Any]) -> None:
        projection: dict[str, Any] = {
            key: status[key]
            for key in ("enabled", "connected", "code", "room_count", "counts", "connection_revision")
            if key in status
        }
        payload: dict[str, Any] = {
            "schema_version": 1, "account": account, "observed_at": time.time(), **projection,
        }
        temporary = self.status_path.with_suffix(".tmp")
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "w") as handle:
            json.dump(payload, handle)
        os.replace(temporary, self.status_path)

    def cursor(self, account: str, room: str) -> int | None:
        row = self.db.execute(
            "SELECT message_id FROM cursors WHERE account=? AND room=?", (account, room)
        ).fetchone()
        return int(row[0]) if row else None

    def initialize(self, account: str, room: str, message_id: int) -> None:
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO cursors VALUES(?,?,?)", (account, room, message_id))

    def is_own(self, account: str, room: str, message: dict[str, Any]) -> bool:
        reference = str(message.get("referenceId", ""))
        row = self.db.execute(
            "SELECT 1 FROM outbox WHERE account=? AND room=? AND (reference_id=? OR message_id=?)",
            (account, room, reference, message.get("id")),
        ).fetchone()
        return row is not None

    def accept(self, account: str, room: str, cursor: int, messages: list[dict[str, Any]]) -> None:
        # Acceptance and cursor publication share one durable transaction.
        with self.db:
            for message in sorted(messages, key=lambda item: item["id"]):
                self.db.execute(
                    "INSERT OR IGNORE INTO inbox VALUES(?,?,?,?,?,?,'accepted',?)",
                    (
                        account,
                        room,
                        message["id"],
                        message["actorId"],
                        message["message"],
                        message.get("timestamp", 0),
                        time.time(),
                    ),
                )
            self.db.execute(
                "UPDATE cursors SET message_id=MAX(message_id,?) WHERE account=? AND room=?",
                (cursor, account, room),
            )

    def claim(self, account: str, rooms: list[str], senders: list[str]) -> dict[str, Any] | None:
        with self.db:
            rows = self.db.execute(
                "SELECT * FROM inbox WHERE account=? AND state='accepted' ORDER BY accepted,message_id",
                (account,),
            ).fetchall()
            for row in rows:
                if row["room"] not in rooms or row["sender"] not in senders:
                    self.db.execute(
                        "UPDATE inbox SET state='revoked',body='' "
                        "WHERE account=? AND room=? AND message_id=?",
                        (account, row["room"], row["message_id"]),
                    )
                    continue
                self.db.execute(
                    "UPDATE inbox SET state='processing' WHERE account=? AND room=? AND message_id=?",
                    (account, row["room"], row["message_id"]),
                )
                return {
                    "account": account,
                    "room": row["room"],
                    "id": row["message_id"],
                    "sender_id": row["sender"],
                    "text": row["body"],
                    "timestamp": row["timestamp"],
                }
        return None

    def finish(self, account: str, room: str, message_id: int, *, success: bool) -> None:
        with self.db:
            self.db.execute(
                "UPDATE inbox SET state=?,body='' WHERE account=? AND room=? "
                "AND message_id=? AND state='processing'",
                ("completed" if success else "uncertain", account, room, message_id),
            )

    def reserve_send(self, account: str, room: str, inbound_id: int, sequence: int, text: str) -> str:
        digest = hashlib.sha256(text.encode()).hexdigest()
        with self.db:
            inbound = self.db.execute(
                "SELECT state FROM inbox WHERE account=? AND room=? AND message_id=?",
                (account, room, inbound_id),
            ).fetchone()
            if not inbound or inbound[0] != "processing":
                raise TalkAccountError("talk-response-without-active-message")
            prior = self.db.execute(
                "SELECT * FROM outbox WHERE account=? AND room=? AND inbound_id=? AND sequence=?",
                (account, room, inbound_id, sequence),
            ).fetchone()
            if prior:
                raise TalkAccountError("talk-send-already-attempted")
            reference = hashlib.sha256(uuid.uuid4().bytes).hexdigest()
            self.db.execute(
                "INSERT INTO outbox VALUES(?,?,?,?,?,?,NULL,'sending')",
                (reference, account, room, inbound_id, sequence, digest),
            )
        return reference

    def sent(self, reference: str, message_id: int | None) -> None:
        with self.db:
            self.db.execute(
                "UPDATE outbox SET state=?,message_id=? WHERE reference_id=?",
                ("sent" if message_id is not None else "uncertain", message_id, reference),
            )

    def counts(self, account: str) -> dict[str, int]:
        result = {"accepted": 0, "processing": 0, "uncertain": 0, "completed": 0}
        for row in self.db.execute(
            "SELECT state,COUNT(*) FROM inbox WHERE account=? GROUP BY state", (account,)
        ):
            result[row[0]] = row[1]
        result["delivery_uncertain"] = self.db.execute(
            "SELECT COUNT(*) FROM outbox WHERE account=? AND state='uncertain'", (account,)
        ).fetchone()[0]
        return result


class TalkAccountWorker:
    def __init__(
        self,
        inbox: TalkInbox,
        load: Callable[[], dict[str, Any] | None],
        transport_factory: Callable[..., Any] | None = None,
    ):
        self.inbox = inbox
        self.load = load
        self.transport_factory = transport_factory
        self.connection: dict[str, Any] | None = None
        self.transport: Any = None
        self.headers: dict[str, str] = {}
        self.account = ""
        self.room_offset = 0

    def refresh(self) -> dict[str, Any]:
        connection = self.load()
        talk = connection.get("talk", {}) if connection else {}
        rooms, senders = talk.get("room_tokens", []), talk.get("allowed_user_ids", [])
        if not connection or talk.get("enabled") is not True:
            self.connection = None
            return {
                "enabled": False, "connected": False, "code": "talk-disabled",
                "connection_revision": connection["revision"] if connection else "",
            }
        if (
            not isinstance(rooms, list)
            or not 1 <= len(rooms) <= 32
            or any(not isinstance(r, str) or not ROOM.fullmatch(r) for r in rooms)
            or not isinstance(senders, list)
            or not 1 <= len(senders) <= 32
            or any(not isinstance(s, str) or not s or len(s) > 512 for s in senders)
        ):
            raise TalkAccountError("talk-pairing-required")
        if not connection.get("expected_ips") or not connection.get("user_id"):
            raise TalkAccountError("talk-connection-incomplete")
        features = (
            connection.get("discovery_snapshot", {})
            .get("capabilities", {})
            .get("spreed", {})
            .get("features", [])
        )
        if "chat-reference-id" not in features:
            raise TalkAccountError("talk-reference-id-required")
        if self.connection != connection:
            from personal_assistant.nextcloud_onboarding import (
                NextcloudCredentials,
                SafeNextcloudTransport,
                credential_headers,
            )

            self.connection = connection
            factory = self.transport_factory or SafeNextcloudTransport
            self.transport = factory(
                connection["server_url"],
                allow_private=True,
                expected_ips=connection["expected_ips"],
                timeout_seconds=10,
                max_response_bytes=1_048_576,
            )
            self.headers = credential_headers(
                NextcloudCredentials(
                    server_url=connection["server_url"],
                    login_name=connection["login_name"],
                    app_password=connection["app_password"],
                    user_id=connection["user_id"],
                )
            )
            self.account = account_identity(connection)
        return {
            "enabled": True,
            "connected": False,
            "room_count": len(rooms),
            "code": "talk-configured",
            "connection_revision": connection["revision"],
            "counts": self.inbox.counts(self.account),
        }

    def _chat(self, room: str, parameters: dict[str, Any]) -> tuple[list[dict[str, Any]], int]:
        features = (
            (self.connection or {})
            .get("discovery_snapshot", {})
            .get("capabilities", {})
            .get("spreed", {})
            .get("features", [])
        )
        if "chat-keep-notifications" in features:
            parameters = {**parameters, "markNotificationsAsRead": 0}
        path = "ocs/v2.php/apps/spreed/api/v1/chat/" + quote(room, safe="")
        response = self.transport.request(
            "GET",
            path + "?" + urlencode(parameters),
            headers=self.headers,
        )
        if response.status == 304:
            return [], int(parameters.get("lastKnownMessageId", 0))
        if response.status not in (200, 204):
            raise TalkAccountError("talk-poll-http-error")
        if response.status == 204:
            data: Any = []
        else:
            payload = response.json()
            ocs = payload.get("ocs", {}) if isinstance(payload, dict) else {}
            if int(ocs.get("meta", {}).get("statuscode", 0)) not in (100, 200):
                raise TalkAccountError("talk-poll-ocs-error")
            data = ocs.get("data")
        if not isinstance(data, list) or len(data) > MAX_BATCH:
            raise TalkAccountError("talk-poll-response-invalid")
        ids: list[int] = []
        for message in data:
            if not isinstance(message, dict) or type(message.get("id")) is not int or message["id"] < 1:
                raise TalkAccountError("talk-message-id-invalid")
            ids.append(message["id"])
        cursor = max(ids, default=int(parameters.get("lastKnownMessageId", 0)))
        # Do not advance beyond messages actually observed. Header-only cursor
        # advancement could otherwise skip an unaccepted request.
        return data, cursor

    def poll(self) -> dict[str, Any]:
        status = self.refresh()
        if not self.connection:
            return {"status": status, "message": None}
        talk = self.connection["talk"]
        rooms, senders = talk["room_tokens"], talk["allowed_user_ids"]
        pending = self.inbox.claim(self.account, rooms, senders)
        if pending:
            return {"status": status, "message": pending}
        room = rooms[self.room_offset % len(rooms)]
        self.room_offset += 1
        cursor = self.inbox.cursor(self.account, room)
        if cursor is None:
            _, latest = self._chat(
                room,
                {"lookIntoFuture": 0, "limit": 1, "setReadMarker": 0, "noStatusUpdate": 1, "format": "json"},
            )
            self.inbox.initialize(self.account, room, latest)
            status.update(connected=True, code="talk-paired-no-history-replay")
            return {"status": status, "message": None}
        messages, newest = self._chat(
            room,
            {
                "lookIntoFuture": 1,
                "lastKnownMessageId": cursor,
                "includeLastKnown": 0,
                "limit": MAX_BATCH,
                "timeout": 5,
                "setReadMarker": 0,
                "noStatusUpdate": 1,
                "format": "json",
            },
        )
        accepted = []
        for message in messages:
            text = message.get("message")
            if (
                message["id"] <= cursor
                or self.inbox.is_own(self.account, room, message)
                or message.get("actorType") != "users"
                or message.get("actorId") not in senders
                or message.get("messageType") not in (None, "comment")
                or message.get("systemMessage")
                or message.get("messageParameters")
                or not isinstance(text, str)
                or not text.strip()
                or len(text.encode("utf-8")) > MAX_TEXT
                or "\x00" in text
            ):
                continue
            accepted.append(message)
        self.inbox.accept(self.account, room, newest, accepted)
        status.update(connected=True, code="talk-poll-ok")
        return {"status": status, "message": self.inbox.claim(self.account, rooms, senders)}

    def send(self, request: dict[str, Any]) -> dict[str, Any]:
        self.refresh()
        room, text = request.get("room"), request.get("text")
        inbound_id, sequence = request.get("inbound_id"), request.get("sequence")
        if (
            not self.connection
            or request.get("account") != self.account
            or not isinstance(room, str)
            or room not in self.connection["talk"]["room_tokens"]
            or not isinstance(text, str)
            or not text.strip()
            or len(text.encode("utf-8")) > MAX_TEXT
            or "\x00" in text
            or type(inbound_id) is not int
            or inbound_id < 1
            or type(sequence) is not int
            or not 0 <= sequence < 128
        ):
            raise TalkAccountError("talk-send-invalid")
        inbound = self.inbox.db.execute(
            "SELECT sender FROM inbox WHERE account=? AND room=? AND message_id=?",
            (self.account, room, inbound_id),
        ).fetchone()
        if inbound and inbound[0] not in self.connection["talk"]["allowed_user_ids"]:
            raise TalkAccountError("talk-sender-revoked")
        reference = self.inbox.reserve_send(self.account, room, inbound_id, sequence, text)
        try:
            response = self.transport.request(
                "POST",
                "ocs/v2.php/apps/spreed/api/v1/chat/" + quote(room, safe=""),
                headers={**self.headers, "Content-Type": "application/x-www-form-urlencoded"},
                data=urlencode({"message": text, "referenceId": reference, "format": "json"}).encode(),
            )
            payload = response.json()
            ocs = payload.get("ocs", {}) if isinstance(payload, dict) else {}
            data = ocs.get("data", {})
            message_id = data.get("id") if isinstance(data, dict) else None
            if (
                response.status not in (200, 201)
                or int(ocs.get("meta", {}).get("statuscode", 0)) not in (100, 200, 201)
                or type(message_id) is not int
                or message_id < 1
                or data.get("referenceId") != reference
            ):
                raise TalkAccountError("talk-delivery-uncertain")
            self.inbox.sent(reference, message_id)
            return {"sent": True, "message_id": message_id}
        except Exception:
            self.inbox.sent(reference, None)
            raise TalkAccountError("talk-delivery-uncertain") from None

    def command(self, request: dict[str, Any]) -> dict[str, Any]:
        operation = request.get("operation")
        if operation == "poll":
            try:
                result = self.poll()
            except Exception:
                self.inbox.record_health(
                    self.account,
                    {"enabled": bool(self.connection), "connected": False, "code": "talk-poll-failed",
                     "connection_revision": (self.connection or {}).get("revision", "")},
                )
                raise
            self.inbox.record_health(self.account, result["status"])
            return result
        if operation == "status":
            return self.refresh()
        if operation == "send":
            return self.send(request)
        if operation == "finish":
            # Bind completion to the exact account identity carried by claim.
            account = request.get("account", "")
            if not isinstance(account, str) or not re.fullmatch("[a-f0-9]{64}", account):
                raise TalkAccountError("talk-account-invalid")
            self.inbox.finish(
                account,
                str(request.get("room", "")),
                int(request.get("inbound_id", 0)),
                success=request.get("success") is True,
            )
            return {"completed": request.get("success") is True}
        raise TalkAccountError("talk-operation-unknown")


def main() -> int:
    if sys.argv[1:] != ["worker"]:
        return 2
    from personal_assistant.nextcloud_connection import load_connection

    path = Path(os.environ.get("OPENCLAW_CORE_DATA_DIR", "/var/lib/openclaw/core")) / "nextcloud-talk.sqlite3"
    inbox = TalkInbox(path)
    worker = TalkAccountWorker(inbox, load_connection)
    try:
        while True:
            line = sys.stdin.buffer.readline(MAX_LINE + 1)
            if not line:
                break
            if len(line) > MAX_LINE:
                return 2
            request_id = None
            try:
                request = json.loads(line)
                if not isinstance(request, dict):
                    raise TalkAccountError("talk-request-invalid")
                request_id = request.get("request_id")
                if not isinstance(request_id, int):
                    raise TalkAccountError("talk-request-id-invalid")
                result = {"request_id": request_id, "ok": True, "result": worker.command(request)}
            except TalkAccountError as exc:
                result = {"request_id": request_id, "ok": False, "code": str(exc)}
            except Exception:
                result = {"request_id": request_id, "ok": False, "code": "talk-transport-or-state-error"}
            print(json.dumps(result, ensure_ascii=True), flush=True)
    finally:
        inbox.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
