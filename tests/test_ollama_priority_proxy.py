from __future__ import annotations

import json
import os
import socket
import threading
import time
import unittest
import urllib.request
from argparse import Namespace
from contextlib import redirect_stdout
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import StringIO
from types import SimpleNamespace
from unittest.mock import patch

from mail_agent.classifier import OllamaClassifier
from personal_assistant.cli import _run_mail_tool
from personal_assistant.ollama_priority_proxy import (
    PriorityGate,
    PriorityProxyServer,
    ProxyConfig,
    QueueFullError,
    QueueTimeoutError,
)
from personal_assistant.ollama_priority_proxy import (
    main as proxy_main,
)


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class _UpstreamHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    seen_headers: dict[str, str] = {}
    seen_body: bytes = b""
    request_order: list[str] = []
    active_started = threading.Event()
    release_active = threading.Event()
    parallel_started = threading.Event()
    release_parallel = threading.Event()
    parallel_active = 0
    parallel_max_active = 0
    order_lock = threading.Lock()

    def log_message(self, fmt: str, *args: object) -> None:
        del fmt, args

    def do_GET(self) -> None:  # noqa: N802
        payload = {"version": "test"} if self.path == "/api/version" else {"models": []}
        data = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", "0"))
        type(self).seen_body = self.rfile.read(length)
        type(self).seen_headers = {key.lower(): value for key, value in self.headers.items()}
        try:
            request_id = str(json.loads(type(self).seen_body.decode()).get("id") or "")
        except (UnicodeDecodeError, json.JSONDecodeError):
            request_id = ""
        if request_id:
            with type(self).order_lock:
                type(self).request_order.append(request_id)
        if request_id == "active":
            type(self).active_started.set()
            type(self).release_active.wait(2)
        parallel_request = request_id.startswith("parallel-")
        if parallel_request:
            with type(self).order_lock:
                type(self).parallel_active += 1
                type(self).parallel_max_active = max(
                    type(self).parallel_max_active, type(self).parallel_active
                )
                if type(self).parallel_active >= 2:
                    type(self).parallel_started.set()
            type(self).release_parallel.wait(2)
        chunks = [
            b'{"message":{"content":"one"},"done":false}\n',
            b'{"message":{"content":"two"},"done":true}\n',
        ]
        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson")
        self.send_header("Connection", "close")
        self.end_headers()
        for chunk in chunks:
            self.wfile.write(chunk)
            self.wfile.flush()
            time.sleep(0.01)
        self.close_connection = True
        if parallel_request:
            with type(self).order_lock:
                type(self).parallel_active -= 1


class PriorityGateTests(unittest.TestCase):
    def test_interactive_overtakes_queued_background(self) -> None:
        gate = PriorityGate(max_pending=10, starvation_seconds=60, max_concurrency=1)
        active = gate.acquire("normal", timeout=1)
        order: list[str] = []

        def worker(name: str, priority: str) -> None:
            ticket = gate.acquire(priority, source=name, timeout=2)
            order.append(name)
            gate.release(ticket)

        background = threading.Thread(target=worker, args=("background", "background"))
        interactive = threading.Thread(target=worker, args=("interactive", "interactive"))
        background.start()
        time.sleep(0.02)
        interactive.start()
        time.sleep(0.02)
        gate.release(active)
        background.join(2)
        interactive.join(2)
        self.assertEqual(order, ["interactive", "background"])

    def test_queue_limits_and_timeouts_are_bounded(self) -> None:
        gate = PriorityGate(max_pending=1, starvation_seconds=60, max_concurrency=1)
        active = gate.acquire("normal", timeout=1)
        waiter_ready = threading.Event()

        def wait_in_queue() -> None:
            waiter_ready.set()
            ticket = gate.acquire("background", timeout=2)
            gate.release(ticket)

        waiter = threading.Thread(target=wait_in_queue)
        waiter.start()
        waiter_ready.wait(1)
        time.sleep(0.02)
        with self.assertRaises(QueueFullError):
            gate.acquire("interactive", timeout=0.1)
        gate.release(active)
        waiter.join(2)

        active = gate.acquire("normal", timeout=1)
        try:
            with self.assertRaises(QueueTimeoutError):
                gate.acquire("background", timeout=0.03)
        finally:
            gate.release(active)

    def test_starvation_protection_eventually_runs_old_background(self) -> None:
        gate = PriorityGate(max_pending=10, starvation_seconds=0.05, max_concurrency=1)
        active = gate.acquire("normal", timeout=1)
        order: list[str] = []

        def worker(name: str, priority: str) -> None:
            ticket = gate.acquire(priority, source=name, timeout=2)
            order.append(name)
            gate.release(ticket)

        background = threading.Thread(target=worker, args=("background", "background"))
        background.start()
        time.sleep(0.08)
        interactive = threading.Thread(target=worker, args=("interactive", "interactive"))
        interactive.start()
        time.sleep(0.02)
        gate.release(active)
        background.join(2)
        interactive.join(2)
        self.assertEqual(order, ["background", "interactive"])


    def test_two_foreground_requests_can_run_concurrently(self) -> None:
        gate = PriorityGate(max_pending=10, max_concurrency=2, background_burst_idle_seconds=0)
        first = gate.acquire("interactive", timeout=1)
        second = gate.acquire("normal", timeout=1)
        snapshot = gate.snapshot()
        self.assertEqual(snapshot["active_count"], 2)
        self.assertEqual(snapshot["max_concurrency"], 2)
        gate.release(second)
        gate.release(first)

    def test_background_burst_uses_second_slot_only_without_foreground(self) -> None:
        gate = PriorityGate(
            max_pending=10,
            max_concurrency=2,
            background_concurrency=1,
            background_burst_concurrency=2,
            background_burst_idle_seconds=0,
        )
        first = gate.acquire("background", timeout=1, allow_background_burst=True)
        second = gate.acquire("background", timeout=1, allow_background_burst=True)
        self.assertEqual(gate.snapshot()["background_active"], 2)
        gate.release(second)
        gate.release(first)

        foreground = gate.acquire("interactive", timeout=1)
        background = gate.acquire("background", timeout=1, allow_background_burst=True)
        with self.assertRaises(QueueTimeoutError):
            gate.acquire("background", timeout=0.03, allow_background_burst=True)
        gate.release(background)
        gate.release(foreground)


class ProxyIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        _UpstreamHandler.request_order = []
        _UpstreamHandler.active_started = threading.Event()
        _UpstreamHandler.release_active = threading.Event()
        _UpstreamHandler.parallel_started = threading.Event()
        _UpstreamHandler.release_parallel = threading.Event()
        _UpstreamHandler.parallel_active = 0
        _UpstreamHandler.parallel_max_active = 0
        upstream_port = _free_port()
        proxy_port = _free_port()
        self.upstream = ThreadingHTTPServer(("127.0.0.1", upstream_port), _UpstreamHandler)
        self.upstream_thread = threading.Thread(target=self.upstream.serve_forever, daemon=True)
        self.upstream_thread.start()
        config = ProxyConfig(
            upstream_url=f"http://127.0.0.1:{upstream_port}",
            listen_port=proxy_port,
            queue_timeout_seconds=2,
            upstream_timeout_seconds=2,
            starvation_seconds=5,
            max_concurrency=1,
            background_concurrency=1,
            background_burst_concurrency=1,
        )
        config.validate()
        self.proxy = PriorityProxyServer(config)
        self.proxy_thread = threading.Thread(target=self.proxy.serve_forever, daemon=True)
        self.proxy_thread.start()
        self.base_url = f"http://127.0.0.1:{proxy_port}"

    def tearDown(self) -> None:
        self.proxy.shutdown()
        self.proxy.server_close()
        self.upstream.shutdown()
        self.upstream.server_close()
        self.proxy_thread.join(2)
        self.upstream_thread.join(2)

    def test_health_checks_proxy_and_upstream(self) -> None:
        with urllib.request.urlopen(self.base_url + "/healthz", timeout=2) as response:
            payload = json.loads(response.read())
        self.assertTrue(payload["ok"])
        self.assertIn("queue", payload)

    def test_container_client_status_needs_no_proxy_server_configuration(self) -> None:
        output = StringIO()
        environment = {
            "OPENCLAW_RUNTIME": "container",
            "OPENCLAW_ROLE": "supervisor-worker",
        }
        with (
            patch.dict(os.environ, environment, clear=True),
            patch("personal_assistant.ollama_priority_proxy._CONTAINER_PROXY_HOST", "127.0.0.1"),
            patch(
                "personal_assistant.ollama_priority_proxy._CONTAINER_PROXY_PORT",
                self.proxy.server_port,
            ),
            redirect_stdout(output),
        ):
            result = proxy_main(["--status"])
        payload = json.loads(output.getvalue())
        self.assertEqual(result, 0)
        self.assertTrue(payload["ok"])
        self.assertNotIn("OLLAMA_PRIORITY_UPSTREAM", os.environ)

    def test_streaming_passthrough_and_priority_headers(self) -> None:
        request = urllib.request.Request(
            self.base_url + "/api/chat",
            data=b'{"model":"test","stream":true}',
            headers={
                "Content-Type": "application/json",
                "X-OpenClaw-Priority": "background",
                "X-OpenClaw-Source": "test-mail-interface",
            },
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=3) as response:
            payload = response.read()
            wait_header = response.headers.get("X-Ollama-Queue-Wait-Ms")
            priority_header = response.headers.get("X-Ollama-Priority")
        self.assertIn(b'"done":false', payload)
        self.assertIn(b'"done":true', payload)
        self.assertEqual(priority_header, "background")
        self.assertIsNotNone(wait_header)
        self.assertNotIn("x-openclaw-priority", _UpstreamHandler.seen_headers)
        self.assertNotIn("x-openclaw-source", _UpstreamHandler.seen_headers)

    def test_http_proxy_runs_interactive_before_queued_background(self) -> None:
        errors: list[Exception] = []

        def send(request_id: str, priority: str) -> None:
            try:
                request = urllib.request.Request(
                    self.base_url + "/api/chat",
                    data=json.dumps({"model": "test", "id": request_id, "stream": False}).encode(),
                    headers={
                        "Content-Type": "application/json",
                        "X-OpenClaw-Priority": priority,
                    },
                    method="POST",
                )
                with urllib.request.urlopen(request, timeout=5) as response:
                    response.read()
            except Exception as exc:  # pragma: no cover - asserted below
                errors.append(exc)

        active = threading.Thread(target=send, args=("active", "normal"))
        background = threading.Thread(target=send, args=("background", "background"))
        interactive = threading.Thread(target=send, args=("interactive", "interactive"))
        active.start()
        self.assertTrue(_UpstreamHandler.active_started.wait(2))
        background.start()
        time.sleep(0.03)
        interactive.start()
        time.sleep(0.03)
        _UpstreamHandler.release_active.set()
        for thread in (active, background, interactive):
            thread.join(5)
        self.assertEqual(errors, [])
        self.assertEqual(_UpstreamHandler.request_order, ["active", "interactive", "background"])

    def test_http_proxy_forwards_two_requests_concurrently(self) -> None:
        self.proxy.gate.max_concurrency = 2
        errors: list[Exception] = []

        def send(request_id: str) -> None:
            try:
                request = urllib.request.Request(
                    self.base_url + "/api/chat",
                    data=json.dumps({"model": "test", "id": request_id, "stream": False}).encode(),
                    headers={
                        "Content-Type": "application/json",
                        "X-OpenClaw-Priority": "interactive",
                    },
                    method="POST",
                )
                with urllib.request.urlopen(request, timeout=5) as response:
                    response.read()
            except Exception as exc:  # pragma: no cover - asserted below
                errors.append(exc)

        first = threading.Thread(target=send, args=("parallel-1",))
        second = threading.Thread(target=send, args=("parallel-2",))
        first.start()
        second.start()
        self.assertTrue(_UpstreamHandler.parallel_started.wait(2))
        _UpstreamHandler.release_parallel.set()
        first.join(5)
        second.join(5)
        self.assertEqual(errors, [])
        self.assertEqual(_UpstreamHandler.parallel_max_active, 2)

    def test_non_generation_endpoint_bypasses_queue(self) -> None:
        active = self.proxy.gate.acquire("interactive", timeout=1)
        try:
            with urllib.request.urlopen(self.base_url + "/api/tags", timeout=2) as response:
                payload = json.loads(response.read())
            self.assertEqual(payload, {"models": []})
        finally:
            self.proxy.gate.release(active)

    def test_unmarked_generation_defaults_to_interactive(self) -> None:
        request = urllib.request.Request(
            self.base_url + "/api/chat",
            data=b'{"model":"test","stream":false}',
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=3) as response:
            response.read()
            self.assertEqual(response.headers.get("X-Ollama-Priority"), "interactive")

    def test_proxy_refuses_non_loopback_bind(self) -> None:
        config = ProxyConfig(upstream_url="http://127.0.0.1:11434", listen_host="0.0.0.0")
        with self.assertRaises(ValueError):
            config.validate()
        container_config = ProxyConfig(
            upstream_url="http://127.0.0.1:11434",
            listen_host="192.0.2.10",
            container_network_bind=True,
        )
        with self.assertRaises(ValueError):
            container_config.validate()
        with self.assertRaises(ValueError):
            ProxyConfig(upstream_url="http://127.0.0.1:11434/v1").validate()

    def test_proxy_accepts_internal_wildcard_only_for_container_proxy_role(self) -> None:
        base = {
            "OLLAMA_PRIORITY_UPSTREAM": "http://host.docker.internal:11434",
            "OLLAMA_PRIORITY_LISTEN_HOST": "0.0.0.0",
        }
        with patch.dict(
            os.environ,
            {**base, "OPENCLAW_RUNTIME": "container", "OPENCLAW_ROLE": "ollama-proxy"},
            clear=True,
        ):
            config = ProxyConfig.from_env()
        self.assertTrue(config.container_network_bind)
        self.assertEqual(config.listen_host, "0.0.0.0")

        for incomplete_context in (
            base,
            {**base, "OPENCLAW_RUNTIME": "container"},
            {**base, "OPENCLAW_ROLE": "ollama-proxy"},
            {**base, "OPENCLAW_RUNTIME": "container", "OPENCLAW_ROLE": "gateway"},
        ):
            with (
                self.subTest(environment=incomplete_context),
                patch.dict(os.environ, incomplete_context, clear=True),
                self.assertRaisesRegex(ValueError, "nur an Loopback"),
            ):
                ProxyConfig.from_env()


class PriorityPropagationTests(unittest.TestCase):
    def test_mail_classifier_sends_priority_and_records_queue_wait(self) -> None:
        classifier = OllamaClassifier.__new__(OllamaClassifier)
        classifier.config = SimpleNamespace(
            ollama=SimpleNamespace(
                temperature=0.0,
                num_ctx=0,
                model="gemma4:31b",
                think=False,
                keep_alive="10m",
                base_url="http://127.0.0.1:11435",
                batch_enabled=True,
                batch_size=5,
                background_burst=True,
            )
        )
        classifier.telemetry = None
        classifier.log = __import__("logging").getLogger(__name__)
        classifier.reset_metrics()
        response_payload = {
            "message": {"content": '{"category":"spam"}'},
            "total_duration": 1_000_000,
        }

        class Response:
            headers = {"X-Ollama-Queue-Wait-Ms": "12.5"}

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, traceback):
                return False

            def read(self):
                return json.dumps(response_payload).encode()

        captured: dict[str, str | float | None] = {}

        def fake_urlopen(request, timeout):
            captured["client_timeout"] = timeout
            captured["priority"] = request.get_header("X-openclaw-priority")
            captured["source"] = request.get_header("X-openclaw-source")
            captured["queue_timeout"] = request.get_header("X-openclaw-queue-timeout-seconds")
            captured["upstream_timeout"] = request.get_header("X-openclaw-upstream-timeout-seconds")
            captured["background_burst"] = request.get_header("X-openclaw-background-burst")
            return Response()

        with patch.dict(os.environ, {
            "OPENCLAW_OLLAMA_PRIORITY": "background",
            "OPENCLAW_OLLAMA_SOURCE": "mail-interface-test",
        }, clear=False), patch("urllib.request.urlopen", side_effect=fake_urlopen):
            classifier._request_json(
                system_prompt="system",
                user_prompt="user",
                schema={"type": "object"},
                num_predict=100,
                timeout_seconds=5,
            )
        self.assertEqual(captured["priority"], "background")
        self.assertEqual(captured["source"], "mail-interface-test")
        self.assertEqual(captured["queue_timeout"], "600")
        self.assertEqual(captured["upstream_timeout"], "5")
        self.assertEqual(captured["background_burst"], "true")
        self.assertEqual(captured["client_timeout"], 635)
        metrics = classifier.metrics_snapshot()
        self.assertEqual(metrics["ollama_queue_wait_ms"], 12.5)
        self.assertEqual(metrics["ollama_queue_wait_max_ms"], 12.5)

    def test_openclaw_mail_tool_marks_direct_run_interactive(self) -> None:
        args = Namespace(mail_command="run", limit=1, drain=False)
        with patch("personal_assistant.cli.subprocess.run") as run:
            run.return_value.returncode = 0
            result = _run_mail_tool(args)
        self.assertEqual(result, 0)
        environment = run.call_args.kwargs["env"]
        self.assertEqual(environment["OPENCLAW_OLLAMA_PRIORITY"], "interactive")
        self.assertEqual(environment["OPENCLAW_OLLAMA_SOURCE"], "openclaw-mail-tool")


if __name__ == "__main__":
    unittest.main()
