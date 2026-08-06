#!/usr/bin/env python3
"""Hermetic protocol fixtures for M8. Contains no production credentials or data."""

from __future__ import annotations

import hashlib
import json
import socketserver
import struct
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import ClassVar
from urllib.parse import urlparse

FIXTURE_MAIL = (
    b"From: fixture-sender@example.invalid\r\n"
    b"To: fixture-recipient@example.invalid\r\n"
    b"Subject: M8 fixture\r\n\r\nHermetic integration payload.\r\n"
)
FIXTURE_EVENT = (
    b"BEGIN:VCALENDAR\r\nVERSION:2.0\r\nBEGIN:VEVENT\r\n"
    b"UID:m8-event\r\nDTSTART:20260806T100000Z\r\nDTEND:20260806T110000Z\r\n"
    b"SUMMARY:M8 Fixture\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n"
)
FIXTURE_CONTACT = (
    b"BEGIN:VCARD\r\nVERSION:3.0\r\nUID:m8-contact\r\n"
    b"FN:M8 Fixture\r\nEMAIL:fixture@example.invalid\r\nEND:VCARD\r\n"
)


class FixtureState:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.mail = [FIXTURE_MAIL]
        self.sent: list[bytes] = []
        self.objects: dict[str, tuple[bytes, str]] = {
            "/dav/calendars/m8-event.ics": (FIXTURE_EVENT, '"event-v1"'),
            "/dav/addressbooks/m8-contact.vcf": (FIXTURE_CONTACT, '"contact-v1"'),
            "/dav/files/fixture.txt": (b"fixture file\n", '"file-v1"'),
        }

    def put(self, path: str, data: bytes, if_match: str, if_none_match: str) -> tuple[int, str]:
        with self.lock:
            current = self.objects.get(path)
            if if_none_match == "*" and current is not None:
                return 412, current[1]
            if if_match and (current is None or if_match != current[1]):
                return 412, current[1] if current else ""
            etag = '"' + hashlib.sha256(data).hexdigest()[:16] + '"'
            self.objects[path] = (data, etag)
            return (204 if current else 201), etag


STATE = FixtureState()


class ReusableTCPServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


class IMAPHandler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        self.wfile.write(b"* OK M8 hermetic IMAP ready\r\n")
        while raw := self.rfile.readline(65536):
            parts = raw.decode("utf-8", errors="replace").strip().split(maxsplit=2)
            if len(parts) < 2:
                continue
            tag, command = parts[0], parts[1].upper()
            if command in {"CAPABILITY"}:
                self.wfile.write(b"* CAPABILITY IMAP4rev1\r\n")
            elif command == "LOGIN":
                self.wfile.write(f"{tag} OK LOGIN completed\r\n".encode())
                continue
            elif command == "LIST":
                self.wfile.write(b'* LIST (\\HasNoChildren) "/" "INBOX"\r\n')
            elif command in {"SELECT", "EXAMINE"}:
                self.wfile.write(f"* {len(STATE.mail)} EXISTS\r\n".encode())
            elif command == "SEARCH":
                ids = b" ".join(str(index + 1).encode() for index in range(len(STATE.mail)))
                self.wfile.write(b"* SEARCH " + ids + b"\r\n")
            elif command == "FETCH":
                payload = STATE.mail[0]
                self.wfile.write(
                    b"* 1 FETCH (RFC822 {" + str(len(payload)).encode() + b"}\r\n" + payload + b")\r\n"
                )
            elif command == "NOOP":
                pass
            elif command == "LOGOUT":
                self.wfile.write(b"* BYE fixture logout\r\n")
                self.wfile.write(f"{tag} OK LOGOUT completed\r\n".encode())
                return
            else:
                self.wfile.write(f"{tag} BAD unsupported fixture command\r\n".encode())
                continue
            self.wfile.write(f"{tag} OK {command} completed\r\n".encode())


class SMTPHandler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        self.wfile.write(b"220 m8.invalid ESMTP fixture\r\n")
        data_mode = False
        payload = bytearray()
        while raw := self.rfile.readline(65536):
            if data_mode:
                if raw == b".\r\n":
                    with STATE.lock:
                        STATE.sent.append(bytes(payload))
                    self.wfile.write(b"250 2.0.0 fixture queued\r\n")
                    data_mode = False
                    payload.clear()
                else:
                    payload.extend(raw[1:] if raw.startswith(b"..") else raw)
                continue
            command = raw.decode("utf-8", errors="replace").strip().upper()
            if command.startswith(("EHLO", "HELO")):
                self.wfile.write(b"250-m8.invalid\r\n250 8BITMIME\r\n")
            elif command.startswith(("MAIL FROM:", "RCPT TO:", "RSET")):
                self.wfile.write(b"250 2.1.0 OK\r\n")
            elif command == "DATA":
                data_mode = True
                self.wfile.write(b"354 End data with <CR><LF>.<CR><LF>\r\n")
            elif command == "QUIT":
                self.wfile.write(b"221 2.0.0 bye\r\n")
                return
            else:
                self.wfile.write(b"502 5.5.1 unsupported fixture command\r\n")


class ClamHandler(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        command = self.request.recv(64)
        if b"PING" in command:
            self.request.sendall(b"PONG\n")
            return
        if b"INSTREAM" not in command:
            self.request.sendall(b"UNKNOWN COMMAND ERROR\0")
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
        if b"EICAR-STANDARD-ANTIVIRUS-TEST-FILE" in data:
            self.request.sendall(b"stream: Eicar-Signature FOUND\0")
        else:
            self.request.sendall(b"stream: OK\0")


class HTTPHandler(BaseHTTPRequestHandler):
    server_version = "M8Fixture/1"
    protocol_version = "HTTP/1.1"
    DAV_XML: ClassVar[bytes] = (
        b"<?xml version='1.0' encoding='utf-8'?>"
        b"<d:multistatus xmlns:d='DAV:' xmlns:c='urn:ietf:params:xml:ns:caldav' "
        b"xmlns:card='urn:ietf:params:xml:ns:carddav'>"
        b"<d:response><d:href>/dav/</d:href><d:propstat><d:prop>"
        b"<d:displayname>M8 fixture</d:displayname></d:prop>"
        b"<d:status>HTTP/1.1 200 OK</d:status></d:propstat></d:response>"
        b"</d:multistatus>"
    )

    def log_message(self, _format: str, *_args: object) -> None:
        return

    def _reply(self, status: int, data: bytes = b"", **headers: str) -> None:
        self.send_response(status)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Connection", "close")
        for key, value in headers.items():
            self.send_header(key.replace("_", "-"), value)
        self.end_headers()
        if data:
            self.wfile.write(data)

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path == "/health":
            self._reply(200, b'{"ok":true}\n', Content_Type="application/json")
            return
        if path == "/status.php":
            self._reply(200, b'{"installed":true,"version":"m8-fixture"}\n', Content_Type="application/json")
            return
        if path == "/market/real-time/M8.XETRA":
            payload = json.dumps({"code": "M8.XETRA", "close": 123.45, "currency": "EUR"}).encode()
            self._reply(200, payload, Content_Type="application/json")
            return
        if path == "/__state":
            with STATE.lock:
                payload = json.dumps(
                    {"smtp_messages": len(STATE.sent), "objects": sorted(STATE.objects)}
                ).encode()
            self._reply(200, payload, Content_Type="application/json")
            return
        with STATE.lock:
            current = STATE.objects.get(path)
        if current is None:
            self._reply(404)
            return
        self._reply(200, current[0], ETag=current[1])

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        if urlparse(self.path).path != "/api/generate":
            self._reply(404)
            return
        request = json.loads(body or b"{}")
        response = json.dumps(
            {"model": request.get("model", "fixture"), "response": "m8-hermetic", "done": True}
        ).encode()
        self._reply(200, response, Content_Type="application/json")

    def do_OPTIONS(self) -> None:  # noqa: N802
        self._reply(204, DAV="1, 2, calendar-access, addressbook")

    def do_PROPFIND(self) -> None:  # noqa: N802
        self._reply(207, self.DAV_XML, Content_Type="application/xml")

    def do_REPORT(self) -> None:  # noqa: N802
        path = urlparse(self.path).path.rstrip("/")
        entries: list[bytes] = []
        with STATE.lock:
            items = list(STATE.objects.items())
        for href, (data, etag) in items:
            if not href.startswith(path + "/"):
                continue
            property_name = b"card:address-data" if href.endswith(".vcf") else b"c:calendar-data"
            entries.append(
                b"<d:response><d:href>" + href.encode() + b"</d:href><d:propstat><d:prop><d:getetag>"
                + etag.encode() + b"</d:getetag><" + property_name + b"><![CDATA[" + data
                + b"]]></" + property_name
                + b"></d:prop><d:status>HTTP/1.1 200 OK</d:status>"
                + b"</d:propstat></d:response>"
            )
        xml = (
            b"<?xml version='1.0'?><d:multistatus xmlns:d='DAV:' xmlns:c='urn:ietf:params:xml:ns:caldav' "
            b"xmlns:card='urn:ietf:params:xml:ns:carddav'>" + b"".join(entries) + b"</d:multistatus>"
        )
        self._reply(207, xml, Content_Type="application/xml")

    def do_PUT(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        length = int(self.headers.get("Content-Length", "0"))
        data = self.rfile.read(length)
        status, etag = STATE.put(
            path,
            data,
            self.headers.get("If-Match", ""),
            self.headers.get("If-None-Match", ""),
        )
        self._reply(status, ETag=etag)


def main() -> None:
    servers = [
        ReusableTCPServer(("0.0.0.0", 1143), IMAPHandler),
        ReusableTCPServer(("0.0.0.0", 1025), SMTPHandler),
        ReusableTCPServer(("0.0.0.0", 3310), ClamHandler),
        ThreadingHTTPServer(("0.0.0.0", 8080), HTTPHandler),
    ]
    for server in servers:
        threading.Thread(target=server.serve_forever, daemon=True).start()
    threading.Event().wait()


if __name__ == "__main__":
    main()
