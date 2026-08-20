#!/usr/bin/env python3
"""Content-contained protocol fixtures for the hermetic M11 acceptance stack."""

from __future__ import annotations

import base64
import hashlib
import json
import socketserver
import struct
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse


def _mail(message_id: str, subject: str, body: str) -> bytes:
    return (
        "From: Fixture Sender <sender@example.invalid>\r\n"
        "To: Fixture User <user@example.invalid>\r\n"
        f"Subject: {subject}\r\n"
        f"Message-ID: <{message_id}@example.invalid>\r\n"
        "Date: Thu, 20 Aug 2026 08:00:00 +0000\r\n"
        "\r\n"
        f"{body}\r\n"
    ).encode()


class FixtureState:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.reset()

    def reset(self) -> None:
        self.folders: dict[str, dict[str, Any]] = {
            "folder-inbox": {"name": "INBOX", "uidvalidity": "10"},
            "folder-archive": {"name": "Archive", "uidvalidity": "20"},
            "folder-copies": {"name": "Copies", "uidvalidity": "30"},
            "folder-spam": {"name": "Spamverdacht", "uidvalidity": "40"},
        }
        self.messages: dict[str, dict[str, Any]] = {
            "alpha": {
                "folder_id": "folder-inbox",
                "uid": "1",
                "raw": _mail(
                    "alpha",
                    "Projekt Aurora",
                    "Polarstern bestaetigt das hermetische Dachprojekt.",
                ),
                "move_from": None,
            },
            "beta": {
                "folder_id": "folder-archive",
                "uid": "2",
                "raw": _mail("beta", "Rechnung ZX-2048", "Belegter Rechnungsbetrag 42 EUR."),
                "move_from": None,
            },
        }
        self.partial = False
        self.embedding_error = False
        self.generation = 1
        self.metrics = {"raw_fetches": 0, "clamav_calls": 0, "embedding_calls": 0}

    def inventory(self) -> dict[str, Any]:
        folders = []
        for folder_id, folder in sorted(self.folders.items()):
            rows = []
            for key, message in sorted(self.messages.items()):
                if message["folder_id"] != folder_id:
                    continue
                raw = bytes(message["raw"])
                rows.append(
                    {
                        "key": key,
                        "mailbox_id": str(message["uid"]),
                        "uid": str(message["uid"]),
                        "subject": raw.split(b"Subject: ", 1)[1].split(b"\r\n", 1)[0].decode(),
                        "date": "2026-08-20T08:00:00+00:00",
                        "raw_sha256": hashlib.sha256(raw).hexdigest(),
                        "move_from": message.get("move_from"),
                    }
                )
            folders.append(
                {
                    "folder_id": folder_id,
                    "name": folder["name"],
                    "uidvalidity": folder["uidvalidity"],
                    "messages": rows,
                }
            )
        return {
            "generation": self.generation,
            "complete": not self.partial,
            "authoritative": not self.partial,
            "folders": folders,
        }

    def action(self, name: str) -> None:
        if name == "reset":
            self.reset()
            return
        if name == "add":
            self.messages["gamma"] = {
                "folder_id": "folder-inbox",
                "uid": "3",
                "raw": _mail("gamma", "Neue Nachricht", "Inkrementeller Suchbegriff Morgenrot."),
                "move_from": None,
            }
        elif name == "move-alpha":
            message = self.messages["alpha"]
            message["move_from"] = self._locator(message)
            message["folder_id"] = "folder-archive"
            message["uid"] = "9"
        elif name == "copy-alpha":
            source = self.messages["alpha"]
            self.messages["alpha-copy"] = {
                "folder_id": "folder-copies",
                "uid": "11",
                "raw": source["raw"],
                "move_from": None,
            }
        elif name == "delete-beta":
            self.messages.pop("beta", None)
        elif name == "quarantine-copy":
            message = self.messages["alpha-copy"]
            message["move_from"] = self._locator(message)
            message["folder_id"] = "folder-spam"
            message["uid"] = "12"
        elif name == "rename-archive":
            self.folders["folder-archive"]["name"] = "Archiv/2026"
        elif name == "uidvalidity-reset":
            self.folders["folder-archive"]["uidvalidity"] = "21"
            self.messages["alpha"]["uid"] = "1"
        elif name == "partial-on":
            self.partial = True
        elif name == "partial-off":
            self.partial = False
        elif name == "embedding-error-on":
            self.embedding_error = True
        elif name == "embedding-error-off":
            self.embedding_error = False
        else:
            raise ValueError(f"unknown fixture action: {name}")
        self.generation += 1

    def _locator(self, message: dict[str, Any]) -> dict[str, str]:
        folder = self.folders[str(message["folder_id"])]
        return {
            "folder_id": str(message["folder_id"]),
            "folder_name": str(folder["name"]),
            "mailbox_id": str(message["uid"]),
            "uidvalidity": str(folder["uidvalidity"]),
            "uid": str(message["uid"]),
        }


STATE = FixtureState()


class ReusableTCPServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


class IMAPHandler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        selected = "INBOX"
        self.wfile.write(b"* OK M11 hermetic IMAP ready\r\n")
        while raw := self.rfile.readline(65536):
            parts = raw.decode(errors="replace").strip().split(maxsplit=2)
            if len(parts) < 2:
                continue
            tag, command = parts[0], parts[1].upper()
            if command == "CAPABILITY":
                self.wfile.write(b"* CAPABILITY IMAP4rev1 UIDPLUS\r\n")
            elif command == "LOGIN":
                pass
            elif command == "LIST":
                with STATE.lock:
                    names = [str(item["name"]) for item in STATE.folders.values()]
                for name in sorted(names):
                    self.wfile.write(f'* LIST (\\HasNoChildren) "/" "{name}"\r\n'.encode())
            elif command in {"SELECT", "EXAMINE"}:
                selected = parts[2].strip('"') if len(parts) > 2 else "INBOX"
                with STATE.lock:
                    count = sum(
                        STATE.folders[str(item["folder_id"])]["name"] == selected
                        for item in STATE.messages.values()
                    )
                self.wfile.write(f"* {count} EXISTS\r\n".encode())
            elif command == "SEARCH":
                with STATE.lock:
                    ids = [
                        str(item["uid"])
                        for item in STATE.messages.values()
                        if STATE.folders[str(item["folder_id"])]["name"] == selected
                    ]
                self.wfile.write(("* SEARCH " + " ".join(ids) + "\r\n").encode())
            elif command == "FETCH":
                requested = parts[2].split()[0] if len(parts) > 2 else ""
                with STATE.lock:
                    payload = next(
                        bytes(item["raw"])
                        for item in STATE.messages.values()
                        if str(item["uid"]) == requested
                        and STATE.folders[str(item["folder_id"])]["name"] == selected
                    )
                self.wfile.write(
                    b"* 1 FETCH (RFC822 {" + str(len(payload)).encode() + b"}\r\n" + payload + b")\r\n"
                )
            elif command == "LOGOUT":
                self.wfile.write(b"* BYE fixture logout\r\n")
                self.wfile.write(f"{tag} OK LOGOUT completed\r\n".encode())
                return
            else:
                self.wfile.write(f"{tag} BAD unsupported fixture command\r\n".encode())
                continue
            self.wfile.write(f"{tag} OK {command} completed\r\n".encode())


class ClamHandler(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        command = self.request.recv(64)
        if b"PING" in command:
            self.request.sendall(b"PONG\n")
            return
        data = bytearray()
        buffered = command.split(b"\0", 1)[1] if b"\0" in command else b""
        while True:
            while len(buffered) < 4:
                chunk = self.request.recv(65536)
                if not chunk:
                    return
                buffered += chunk
            size = struct.unpack("!I", buffered[:4])[0]
            buffered = buffered[4:]
            if size == 0:
                break
            while len(buffered) < size:
                buffered += self.request.recv(65536)
            data.extend(buffered[:size])
            buffered = buffered[size:]
        with STATE.lock:
            STATE.metrics["clamav_calls"] += 1
        if b"M11-SCANNER-ERROR" in data:
            self.request.sendall(b"stream: fixture scanner ERROR\0")
        elif b"EICAR-STANDARD-ANTIVIRUS-TEST-FILE" in data:
            self.request.sendall(b"stream: Eicar-Signature FOUND\0")
        else:
            self.request.sendall(b"stream: OK\0")


class HTTPHandler(BaseHTTPRequestHandler):
    server_version = "M11Fixture/1"
    protocol_version = "HTTP/1.1"

    def log_message(self, _format: str, *_args: object) -> None:
        return

    def reply(self, status: int, payload: Any) -> None:
        data = json.dumps(payload, sort_keys=True).encode() + b"\n"
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/health":
            self.reply(200, {"ok": True})
            return
        if parsed.path == "/mail/inventory":
            with STATE.lock:
                self.reply(200, STATE.inventory())
            return
        if parsed.path == "/mail/raw":
            key = parse_qs(parsed.query).get("key", [""])[0]
            with STATE.lock:
                message = STATE.messages.get(key)
                if message is None:
                    self.reply(404, {"ok": False})
                    return
                STATE.metrics["raw_fetches"] += 1
                payload = base64.b64encode(bytes(message["raw"])).decode()
            self.reply(200, {"ok": True, "raw_base64": payload})
            return
        if parsed.path == "/metrics":
            with STATE.lock:
                self.reply(200, {"ok": True, **STATE.metrics})
            return
        self.reply(404, {"ok": False})

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(length) or b"{}")
        if self.path == "/control":
            try:
                with STATE.lock:
                    STATE.action(str(payload.get("action") or ""))
                    generation = STATE.generation
            except ValueError as exc:
                self.reply(400, {"ok": False, "error": str(exc)})
                return
            self.reply(200, {"ok": True, "generation": generation})
            return
        if self.path == "/api/embed":
            with STATE.lock:
                STATE.metrics["embedding_calls"] += 1
                fail = STATE.embedding_error
            if fail:
                self.reply(503, {"ok": False, "error": "fixture embedding unavailable"})
                return
            texts = payload.get("texts") if isinstance(payload, dict) else []
            vectors = []
            for value in texts if isinstance(texts, list) else []:
                folded = str(value).casefold()
                vectors.append(
                    [
                        4.0 if any(word in folded for word in ("dach", "roof", "polarstern")) else 0.2,
                        4.0 if any(word in folded for word in ("rechnung", "invoice")) else 0.2,
                        4.0 if any(word in folded for word in ("morgenrot", "incremental")) else 0.2,
                        1.0,
                    ]
                )
            self.reply(200, {"ok": True, "vectors": vectors})
            return
        self.reply(404, {"ok": False})


def main() -> None:
    servers = [
        ReusableTCPServer(("0.0.0.0", 1143), IMAPHandler),
        ReusableTCPServer(("0.0.0.0", 3310), ClamHandler),
        ThreadingHTTPServer(("0.0.0.0", 8080), HTTPHandler),
    ]
    for server in servers:
        threading.Thread(target=server.serve_forever, daemon=True).start()
    threading.Event().wait()


if __name__ == "__main__":
    main()
