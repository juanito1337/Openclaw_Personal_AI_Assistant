"""Bounded, private Talk webhook transport; never a general-purpose HTTP proxy.

Caddy owns TLS and the direct-peer ACL. This process binds only loopback, keeps
the signed body unchanged and leaves HMAC/backend validation to the pinned Talk
plugin. It never owns a bot secret and never logs requests, headers or bodies.
"""

from __future__ import annotations

import http.client
import signal
import socket
import threading
import time
from contextlib import suppress
from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import ThreadingMixIn
from typing import Any, BinaryIO

WEBHOOK_PATH = "/nextcloud-talk-webhook"
LISTEN_ADDRESS = ("127.0.0.1", 9080)
UPSTREAM_ADDRESS = ("gateway", 8788)
MAX_BODY_BYTES = 1_048_576
MAX_RESPONSE_BYTES = 65_536
MAX_HEADER_BYTES = 16_384
MAX_CONNECTIONS = 4
REQUEST_TIMEOUT_SECONDS = 10.0
UPSTREAM_TIMEOUT_SECONDS = 15.0
# Nextcloud Talk 25.0.5 BotService::sendAsyncRequest and the pinned OpenClaw
# nextcloud-talk@2026.7.1 extractNextcloudTalkHeaders contract.
FORWARDED_HEADERS = (
    "Content-Type",
    "X-Nextcloud-Talk-Random",
    "X-Nextcloud-Talk-Signature",
    "X-Nextcloud-Talk-Backend",
    "OCS-APIRequest",
)


class TokenBucket:
    """One shared bucket: ten initial requests, one new token per second."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._tokens = 10.0
        self._updated = time.monotonic()

    def take(self) -> bool:
        with self._lock:
            now = time.monotonic()
            self._tokens = min(10.0, self._tokens + max(0.0, now - self._updated))
            self._updated = now
            if self._tokens < 1.0:
                return False
            self._tokens -= 1.0
            return True


class _HeaderReader:
    """Bound total headers, not just each individual header line."""

    def __init__(self, stream: BinaryIO, remaining: int) -> None:
        self.stream = stream
        self.remaining = remaining

    def readline(self, limit: int = -1) -> bytes:
        if self.remaining < 0:
            raise http.client.LineTooLong("headers")
        maximum = self.remaining + 1
        if limit >= 0:
            maximum = min(maximum, limit)
        line = self.stream.readline(maximum)
        self.remaining -= len(line)
        if self.remaining < 0:
            raise http.client.LineTooLong("headers")
        return line

    def close(self) -> None:
        self.stream.close()


def _shutdown_read(sock: socket.socket, expired: threading.Event) -> None:
    expired.set()
    with suppress(OSError):
        sock.shutdown(socket.SHUT_RD)


class _BoundedResponse(http.client.HTTPResponse):
    # HTTPResponse accepts file-like readers at runtime; its stub narrows this
    # to BufferedReader even though only readline/close are needed during begin.
    fp: Any

    def __init__(self, sock: socket.socket) -> None:
        super().__init__(sock, method="POST")
        self.fp = _HeaderReader(self.fp, MAX_HEADER_BYTES)

    def begin(self) -> None:
        bounded = self.fp
        try:
            super().begin()
        finally:
            # The budget reader is only for the status line and headers. Body
            # reads have their own independent bounded buffer and hard deadline.
            if self.fp is bounded:
                self.fp = bounded.stream


class TalkGuardServer(ThreadingMixIn, HTTPServer):
    daemon_threads = False
    block_on_close = True
    request_queue_size = MAX_CONNECTIONS
    allow_reuse_address = True

    def __init__(
        self,
        server_address: tuple[str, int] = LISTEN_ADDRESS,
        *,
        upstream_address: tuple[str, int] = UPSTREAM_ADDRESS,
        request_timeout: float = REQUEST_TIMEOUT_SECONDS,
        upstream_timeout: float = UPSTREAM_TIMEOUT_SECONDS,
    ) -> None:
        if server_address[0] != "127.0.0.1":
            raise ValueError("talk-guard-loopback-required")
        if not 0 < request_timeout <= REQUEST_TIMEOUT_SECONDS:
            raise ValueError("talk-guard-invalid-request-timeout")
        if not 0 < upstream_timeout <= UPSTREAM_TIMEOUT_SECONDS:
            raise ValueError("talk-guard-invalid-upstream-timeout")
        self.request_timeout = request_timeout
        self.upstream_timeout = upstream_timeout
        self._slots = threading.BoundedSemaphore(MAX_CONNECTIONS)
        self.bucket = TokenBucket()
        # Resolve the fixed service before becoming ready. Each request connects
        # to a numeric address, so DNS cannot evade its hard transport deadline.
        self._upstream = socket.getaddrinfo(
            upstream_address[0], upstream_address[1], socket.AF_INET, socket.SOCK_STREAM
        )[0][4]
        super().__init__(server_address, TalkGuardHandler)

    def process_request(
        self, request: socket.socket | tuple[bytes, socket.socket], client_address: tuple[str, int]
    ) -> None:
        if not isinstance(request, socket.socket):
            raise TypeError("talk-guard-tcp-required")
        # Acquire BEFORE ThreadingMixIn creates a thread. There is no unbounded
        # thread-per-connection queue and no waiting handler holding a slot.
        if not self._slots.acquire(blocking=False):
            request.settimeout(0.1)
            with suppress(OSError):
                request.sendall(
                    b"HTTP/1.1 503 Service Unavailable\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"
                )
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self._slots.release()
            raise

    def process_request_thread(
        self, request: socket.socket | tuple[bytes, socket.socket], client_address: tuple[str, int]
    ) -> None:
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._slots.release()

    def handle_error(
        self, request: socket.socket | tuple[bytes, socket.socket], client_address: tuple[str, int]
    ) -> None:
        # socketserver's default traceback could contain request-derived values.
        del request, client_address

    def forward(self, body: bytes, headers: dict[str, str]) -> tuple[int, bytes]:
        expired = threading.Event()
        # Numeric connect plus socket timeout is bounded; one timer then covers
        # send, slow response headers and slow response body together.
        deadline = time.monotonic() + self.upstream_timeout
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(self.upstream_timeout)
            sock.connect(self._upstream)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("talk-upstream-timeout")
            timer = threading.Timer(remaining, _shutdown_read, args=(sock, expired))
            timer.daemon = True
            timer.start()
            try:
                head = (
                    f"POST {WEBHOOK_PATH} HTTP/1.1\r\nHost: gateway:8788\r\n"
                    f"Content-Length: {len(body)}\r\nConnection: close\r\n"
                )
                head += "".join(f"{key}: {value}\r\n" for key, value in headers.items())
                sock.settimeout(max(0.001, deadline - time.monotonic()))
                sock.sendall(head.encode("latin-1") + b"\r\n" + body)
                response = _BoundedResponse(sock)
                try:
                    response.begin()
                    if not (200 <= response.status < 300 or 400 <= response.status < 600):
                        raise ValueError("talk-upstream-status-rejected")
                    lengths = response.headers.get_all("Content-Length", [])
                    encodings = response.headers.get_all("Transfer-Encoding", [])
                    if len(lengths) > 1 or (lengths and encodings):
                        raise ValueError("talk-upstream-ambiguous-framing")
                    if lengths and (
                        not lengths[0].isascii()
                        or not lengths[0].isdigit()
                        or len(lengths[0]) > 8
                        or int(lengths[0]) > MAX_RESPONSE_BYTES
                    ):
                        raise ValueError("talk-upstream-response-size")
                    if encodings and (len(encodings) != 1 or encodings[0].lower() != "chunked"):
                        raise ValueError("talk-upstream-encoding-rejected")
                    data = bytearray()
                    while True:
                        remaining = deadline - time.monotonic()
                        if remaining <= 0 or expired.is_set():
                            raise TimeoutError("talk-upstream-timeout")
                        sock.settimeout(remaining)
                        chunk = response.read1(min(8192, MAX_RESPONSE_BYTES + 1 - len(data)))
                        if not chunk:
                            break
                        data.extend(chunk)
                        if len(data) > MAX_RESPONSE_BYTES:
                            raise ValueError("talk-upstream-response-size")
                    if expired.is_set() or time.monotonic() >= deadline:
                        raise TimeoutError("talk-upstream-timeout")
                    if lengths and response.status not in {204, 205} and len(data) != int(lengths[0]):
                        raise ValueError("talk-upstream-incomplete-response")
                    return response.status, bytes(data)
                finally:
                    response.close()
            finally:
                timer.cancel()
                timer.join()


class TalkGuardHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server: TalkGuardServer
    rfile: Any
    raw_requestline: bytes

    def setup(self) -> None:
        self.request.settimeout(self.server.request_timeout)
        super().setup()
        self._expired = threading.Event()
        self._receive_timer = threading.Timer(
            self.server.request_timeout, _shutdown_read, args=(self.connection, self._expired)
        )
        self._receive_timer.daemon = True
        self._receive_timer.start()

    def finish(self) -> None:
        self._receive_timer.cancel()
        self._receive_timer.join()
        with suppress(OSError):
            super().finish()

    def log_message(self, format: str, *args: object) -> None:
        del format, args

    def send_error(self, code: int, message: str | None = None, explain: str | None = None) -> None:
        del message, explain
        self._reply(405 if code == 501 else code)

    def handle_expect_100(self) -> bool:
        self._reply(417)
        return False

    def parse_request(self) -> bool:
        original = self.rfile
        self.rfile = _HeaderReader(original, MAX_HEADER_BYTES - len(self.raw_requestline))
        try:
            parsed = super().parse_request()
        finally:
            self.rfile = original
        if not parsed:
            return False
        # BaseHTTPRequestHandler normalizes // prefixes. Authorize the literal
        # request-target instead, not the normalized/decoded variant.
        parts = self.raw_requestline.split()
        if len(parts) != 3 or parts[1] not in {WEBHOOK_PATH.encode(), b"/healthz"}:
            self._reply(404)
            return False
        if self.headers.defects or any(
            any(ord(char) < 32 or ord(char) == 127 for char in value) for value in self.headers.values()
        ):
            self._reply(400)
            return False
        return True

    def _reply(self, status: int, body: bytes = b"") -> None:
        self.close_connection = True
        with suppress(OSError):
            self.connection.settimeout(1.0)
            self.send_response_only(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/healthz" and self.client_address[0] == "127.0.0.1":
            self._reply(200, b'{"ok":true,"scope":"guard-transport-only"}\n')
        else:
            self._reply(405)

    def do_POST(self) -> None:  # noqa: N802
        if self.path != WEBHOOK_PATH:
            self._reply(404)
            return
        if not self.server.bucket.take():
            self._reply(429)
            return
        lengths = self.headers.get_all("Content-Length", [])
        if self.headers.get_all("Transfer-Encoding") or len(lengths) > 1:
            self._reply(400)
            return
        if not lengths:
            self._reply(411)
            return
        value = lengths[0]
        if not value.isascii() or not value.isdigit() or len(value) > 8:
            self._reply(400)
            return
        size = int(value)
        if not 0 < size <= MAX_BODY_BYTES:
            self._reply(413 if size > MAX_BODY_BYTES else 400)
            return
        headers = {}
        connection_tokens = {
            token.strip().lower()
            for value in self.headers.get_all("Connection", [])
            for token in value.split(",")
        }
        if connection_tokens.intersection(name.lower() for name in FORWARDED_HEADERS):
            self._reply(400)
            return
        for name in FORWARDED_HEADERS:
            values = self.headers.get_all(name, [])
            if len(values) > 1:
                self._reply(400)
                return
            if values:
                headers[name] = values[0]
        try:
            body = self.rfile.read(size)
        except (OSError, TimeoutError):
            self._reply(408)
            return
        self._receive_timer.cancel()
        self._receive_timer.join()
        if self._expired.is_set():
            self._reply(408)
            return
        if len(body) != size:
            self._reply(400)
            return
        try:
            status, response_body = self.server.forward(body, headers)
        except (OSError, ValueError, http.client.HTTPException):
            self._reply(502)
            return
        self._reply(status, response_body)


def main() -> int:
    # No environment-controlled address, upstream, limit or arbitrary route.
    try:
        server = TalkGuardServer()
    except (OSError, ValueError):
        return 1
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    server.timeout = 0.25
    try:
        while not stop.is_set():
            server.handle_request()
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
