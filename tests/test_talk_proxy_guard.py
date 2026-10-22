from __future__ import annotations

import http.client
import io
import socket
import threading
import time
import unittest
from contextlib import contextmanager, redirect_stderr
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

from personal_assistant.talk_proxy_guard import (
    FORWARDED_HEADERS,
    LISTEN_ADDRESS,
    MAX_BODY_BYTES,
    MAX_CONNECTIONS,
    MAX_RESPONSE_BYTES,
    REQUEST_TIMEOUT_SECONDS,
    UPSTREAM_ADDRESS,
    UPSTREAM_TIMEOUT_SECONDS,
    WEBHOOK_PATH,
    TalkGuardServer,
    TokenBucket,
)


class TalkProxyGuardTests(unittest.TestCase):
    def setUp(self) -> None:
        self.seen = []
        self.status = 202
        self.response = b'{"ok":true}'
        self.response_delay = 0.0
        self.header_drip = False
        self.body_drip = False
        self.chunked = False
        self.declared_length = None
        self.extra_headers = []
        test = self

        class Backend(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *_: object) -> None:
                pass

            def do_POST(self) -> None:  # noqa: N802
                test.seen.append(
                    (self.path, self.headers, self.rfile.read(int(self.headers["Content-Length"])))
                )
                try:
                    time.sleep(test.response_delay)
                    if test.header_drip:
                        for byte in b"HTTP/1.1 200 OK\r\nX-Test: never-end":
                            self.connection.sendall(bytes([byte]))
                            time.sleep(0.03)
                        return
                    self.send_response(test.status)
                    if test.chunked:
                        self.send_header("Transfer-Encoding", "chunked")
                    else:
                        self.send_header(
                            "Content-Length",
                            str(len(test.response) if test.declared_length is None else test.declared_length),
                        )
                    self.send_header("Connection", "close")
                    self.send_header("Set-Cookie", "private-cookie")
                    self.send_header("Location", "https://must-not-follow.invalid/")
                    for name, value in test.extra_headers:
                        self.send_header(name, value)
                    self.end_headers()
                    if test.body_drip:
                        for byte in test.response:
                            self.wfile.write(bytes([byte]))
                            self.wfile.flush()
                            time.sleep(0.03)
                    elif test.chunked:
                        self.wfile.write(f"{len(test.response):x}\r\n".encode())
                        self.wfile.write(test.response + b"\r\n0\r\n\r\n")
                    else:
                        self.wfile.write(test.response)
                    self.close_connection = True
                except OSError:
                    pass

        self.backend = ThreadingHTTPServer(("127.0.0.1", 0), Backend)
        self.backend_thread = threading.Thread(
            target=self.backend.serve_forever, kwargs={"poll_interval": 0.01}
        )
        self.backend_thread.start()
        self.guard = TalkGuardServer(
            ("127.0.0.1", 0),
            upstream_address=self.backend.server_address,
            request_timeout=0.4,
            upstream_timeout=0.35,
        )
        self.guard_thread = threading.Thread(target=self.guard.serve_forever, kwargs={"poll_interval": 0.01})
        self.guard_thread.start()

    def tearDown(self) -> None:
        self.guard.shutdown()
        self.guard.server_close()
        self.guard_thread.join(2)
        self.backend.shutdown()
        self.backend.server_close()
        self.backend_thread.join(2)

    def request(self, method="POST", path=WEBHOOK_PATH, body=b"{}", headers=None):
        connection = http.client.HTTPConnection(*self.guard.server_address, timeout=2)
        try:
            connection.request(method, path, body, headers or {})
            response = connection.getresponse()
            return response.status, response.read(), dict(response.getheaders())
        finally:
            connection.close()

    def raw(self, head: bytes, body: bytes = b"") -> tuple[int, bytes]:
        with socket.create_connection(self.guard.server_address, timeout=2) as sock:
            sock.sendall(head + body)
            response = http.client.HTTPResponse(sock)
            response.begin()
            return response.status, response.read()

    @contextmanager
    def incomplete_connections(self, count):
        sockets = []
        try:
            for _ in range(count):
                sock = socket.create_connection(self.guard.server_address, timeout=2)
                sock.sendall(
                    b"POST /nextcloud-talk-webhook HTTP/1.1\r\nHost: test\r\nContent-Length: 2\r\n\r\n"
                )
                sockets.append(sock)
            deadline = time.monotonic() + 0.2
            while self.guard._slots._value != MAX_CONNECTIONS - count and time.monotonic() < deadline:
                time.sleep(0.002)
            yield sockets
        finally:
            for sock in sockets:
                sock.close()

    def test_preserves_raw_signed_bytes_and_only_contract_headers(self) -> None:
        body = b'{"text": "a\\u00e4", "spaces":   [1, 2]}\n'
        headers = {name: f"test-{number}" for number, name in enumerate(FORWARDED_HEADERS)}
        headers.update(
            {
                "Authorization": "must-not-forward",
                "Cookie": "private",
                "X-Forwarded-For": "192.168.2.3",
                "Forwarded": "for=192.168.2.3",
                "Proxy-Authorization": "secret",
                "X-Unrelated": "private",
            }
        )
        status, data, response_headers = self.request(body=body, headers=headers)
        self.assertEqual((status, data), (202, self.response))
        path, received, actual_body = self.seen[0]
        self.assertEqual((path, actual_body), (WEBHOOK_PATH, body))
        self.assertEqual(
            set(key.lower() for key in received),
            {
                *(name.lower() for name in FORWARDED_HEADERS),
                "host",
                "content-length",
                "connection",
            },
        )
        for name in FORWARDED_HEADERS:
            self.assertEqual(received[name], headers[name])
        self.assertNotIn("Set-Cookie", response_headers)
        self.assertNotIn("Location", response_headers)

    def test_exact_raw_route_no_normalization_no_query_no_proxy(self) -> None:
        for path in [
            "/",
            WEBHOOK_PATH + "?a=1",
            WEBHOOK_PATH + "/",
            "/%6eextcloud-talk-webhook",
            "//nextcloud-talk-webhook",
            "http://example.invalid" + WEBHOOK_PATH,
        ]:
            with self.subTest(path=path):
                self.assertEqual(self.request(path=path)[0], 404)
        self.assertEqual(self.seen, [])

    def test_only_post_and_private_health(self) -> None:
        for method in ["GET", "HEAD", "PUT", "DELETE", "OPTIONS", "CONNECT", "PATCH"]:
            with self.subTest(method=method):
                self.assertEqual(self.request(method=method)[0], 405)
        status, body, _ = self.request(method="GET", path="/healthz", body=None)
        self.assertEqual(status, 200)
        self.assertIn(b"guard-transport-only", body)
        self.assertEqual(self.request(path="/healthz")[0], 404)
        self.assertEqual(self.seen, [])

    def test_missing_duplicate_and_invalid_lengths(self) -> None:
        cases = [
            (b"", 411),
            (b"Content-Length: 0\r\n", 400),
            (b"Content-Length: -1\r\n", 400),
            (b"Content-Length: 2, 2\r\n", 400),
            (b"Content-Length: 2\r\nContent-Length: 2\r\n", 400),
            (b"Content-Length: 999999999999999999999999\r\n", 400),
            (f"Content-Length: {MAX_BODY_BYTES + 1}\r\n".encode(), 413),
            (b"Content-Length: 2\r\nTransfer-Encoding: chunked\r\n", 400),
        ]
        for framing, status in cases:
            with self.subTest(framing=framing):
                self.assertEqual(
                    self.raw(b"POST /nextcloud-talk-webhook HTTP/1.1\r\nHost: test\r\n" + framing + b"\r\n")[
                        0
                    ],
                    status,
                )
        self.assertEqual(self.seen, [])

    def test_duplicate_and_folded_signature_or_hop_header_rejected(self) -> None:
        for headers in [
            b"X-Nextcloud-Talk-Signature: one\r\nX-Nextcloud-Talk-Signature: two\r\n",
            b"X-Nextcloud-Talk-Signature: one\r\n two\r\n",
            b"Connection: X-Nextcloud-Talk-Signature\r\nX-Nextcloud-Talk-Signature: one\r\n",
        ]:
            self.assertEqual(
                self.raw(
                    b"POST /nextcloud-talk-webhook HTTP/1.1\r\nHost: test\r\nContent-Length: 2\r\n"
                    + headers
                    + b"\r\n",
                    b"{}",
                )[0],
                400,
            )
        self.assertEqual(self.seen, [])

    def test_body_limit_boundary_is_allowed(self) -> None:
        body = b"x" * MAX_BODY_BYTES
        self.assertEqual(self.request(body=body)[0], 202)
        self.assertEqual(self.seen[0][2], body)

    def test_headers_are_bounded_and_expect_is_rejected(self) -> None:
        self.assertEqual(
            self.raw(
                b"POST /nextcloud-talk-webhook HTTP/1.1\r\nHost: test\r\nX-Large: "
                + b"x" * 16384
                + b"\r\nContent-Length: 2\r\n\r\n",
                b"{}",
            )[0],
            431,
        )
        self.assertEqual(self.request(headers={"Expect": "100-continue"})[0], 417)

    def test_one_request_per_connection_no_pipelined_delivery(self) -> None:
        packet = b"POST /nextcloud-talk-webhook HTTP/1.1\r\nHost: test\r\nContent-Length: 2\r\n\r\n{}"
        self.assertEqual(self.raw(packet + packet)[0], 202)
        self.assertEqual(len(self.seen), 1)

    def test_token_bucket_is_shared_and_health_does_not_consume_it(self) -> None:
        with patch("personal_assistant.talk_proxy_guard.time.monotonic", return_value=100.0):
            self.guard.bucket = TokenBucket()
            for _ in range(10):
                self.assertEqual(self.request()[0], 202)
            self.assertEqual(self.request()[0], 429)
            self.assertEqual(self.request(method="GET", path="/healthz", body=None)[0], 200)
        self.assertEqual(len(self.seen), 10)

    def test_token_refill_is_one_per_second_and_caps_at_ten(self) -> None:
        with patch("personal_assistant.talk_proxy_guard.time.monotonic") as clock:
            clock.return_value = 100.0
            bucket = TokenBucket()
            self.assertTrue(all(bucket.take() for _ in range(10)))
            self.assertFalse(bucket.take())
            clock.return_value = 100.9
            self.assertFalse(bucket.take())
            clock.return_value = 101.0
            self.assertTrue(bucket.take())
            self.assertFalse(bucket.take())
            clock.return_value = 500.0
            self.assertTrue(all(bucket.take() for _ in range(10)))
            self.assertFalse(bucket.take())

    def test_slots_are_acquired_before_spawning_and_released(self) -> None:
        with self.incomplete_connections(4):
            self.assertEqual(self.guard._slots._value, 0)
            with patch.object(
                self.guard, "process_request_thread", wraps=self.guard.process_request_thread
            ) as run:
                with socket.create_connection(self.guard.server_address, timeout=2) as sock:
                    response = http.client.HTTPResponse(sock)
                    response.begin()
                    self.assertEqual(response.status, 503)
                self.assertFalse(run.called)
        deadline = time.monotonic() + 1
        while self.guard._slots._value != 4 and time.monotonic() < deadline:
            time.sleep(0.005)
        self.assertEqual(self.guard._slots._value, 4)
        self.assertEqual(self.request()[0], 202)

    def test_slow_body_has_hard_deadline_not_idle_deadline(self) -> None:
        with socket.create_connection(self.guard.server_address, timeout=2) as sock:
            sock.sendall(
                b"POST /nextcloud-talk-webhook HTTP/1.1\r\nHost: test\r\nContent-Length: 100\r\n\r\n"
            )
            start = time.monotonic()
            for _ in range(7):
                sock.sendall(b"x")
                time.sleep(0.07)
            response = http.client.HTTPResponse(sock)
            response.begin()
            self.assertEqual(response.status, 408)
            self.assertLess(time.monotonic() - start, 1)
        self.assertEqual(self.seen, [])

    def test_slow_headers_release_slot_within_deadline(self) -> None:
        with socket.create_connection(self.guard.server_address, timeout=2) as sock:
            sock.sendall(b"POST /nextcloud-talk-webhook HTTP/1.1\r\nX-Partial: ")
            time.sleep(0.55)
            self.assertEqual(self.guard._slots._value, 4)
        self.assertEqual(self.seen, [])

    def test_redirect_never_followed_and_no_retry(self) -> None:
        for status in [301, 302, 307, 308]:
            self.status = status
            self.assertEqual(self.request()[0], 502)
        self.assertEqual(len(self.seen), 4)

    def test_2xx_4xx_5xx_preserved(self) -> None:
        for status in [200, 202, 400, 401, 403, 404, 429, 500, 503]:
            self.status = status
            self.assertEqual(self.request()[0], status)

    def test_oversized_upstream_body_and_headers_rejected(self) -> None:
        self.response = b"x" * (MAX_RESPONSE_BYTES + 1)
        self.assertEqual(self.request()[0], 502)
        self.chunked = True
        self.assertEqual(self.request()[0], 502)
        self.chunked = False
        self.response = b"{}"
        self.extra_headers = [("X-Oversized", "x" * 16384)]
        self.assertEqual(self.request()[0], 502)

    def test_short_response_body_is_not_success(self) -> None:
        self.declared_length = 20
        self.assertEqual(self.request()[0], 502)

    def test_chunked_response_is_bounded_and_supported(self) -> None:
        self.chunked = True
        self.assertEqual(self.request()[:2], (202, self.response))

    def test_slow_response_headers_and_body_have_hard_total_deadline(self) -> None:
        for mode in ["delay", "headers", "body"]:
            self.response_delay = 0.5 if mode == "delay" else 0
            self.header_drip = mode == "headers"
            self.body_drip = mode == "body"
            self.response = b"x" * 100
            start = time.monotonic()
            self.assertEqual(self.request()[0], 502)
            self.assertLess(time.monotonic() - start, 0.9)
        self.assertEqual(len(self.seen), 3)

    def test_failures_do_not_log_request_data(self) -> None:
        captured = io.StringIO()
        with redirect_stderr(captured):
            self.request(path="/private-token-and-mail-content")
            self.request(headers={"Content-Length": "invalid-secret-value"})
            self.status = 302
            self.request(body=b"private-mail-body")
        self.assertEqual(captured.getvalue(), "")

    def test_runtime_defaults_are_fixed_and_limits_cannot_be_expanded(self) -> None:
        self.assertEqual(LISTEN_ADDRESS, ("127.0.0.1", 9080))
        self.assertEqual(UPSTREAM_ADDRESS, ("gateway", 8788))
        self.assertEqual(MAX_CONNECTIONS, 4)
        for kwargs in [
            {"server_address": ("0.0.0.0", 0)},
            {"request_timeout": REQUEST_TIMEOUT_SECONDS + 1},
            {"upstream_timeout": UPSTREAM_TIMEOUT_SECONDS + 1},
        ]:
            with self.assertRaises(ValueError):
                TalkGuardServer(**kwargs)


if __name__ == "__main__":
    unittest.main()
