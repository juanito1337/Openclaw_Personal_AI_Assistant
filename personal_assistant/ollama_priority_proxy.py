from __future__ import annotations

import argparse
import http.client
import ipaddress
import json
import logging
import os
import signal
import socket
import ssl
import threading
import time
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Mapping
from urllib.parse import urlsplit


LOG = logging.getLogger("ollama-priority-proxy")

_PRIORITY_VALUES = {
    "interactive": 0,
    "normal": 10,
    "maintenance": 20,
    "background": 30,
}
_PRIORITY_NAMES = {value: key for key, value in _PRIORITY_VALUES.items()}
_HOP_BY_HOP_HEADERS = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
}
_GPU_PATHS = {
    "/api/chat",
    "/api/generate",
    "/api/embed",
    "/api/embeddings",
    "/v1/chat/completions",
    "/v1/completions",
    "/v1/embeddings",
    "/v1/responses",
}


def _safe_float(value: object, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _safe_int(value: object, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _is_loopback_host(host: str) -> bool:
    value = host.strip().lower()
    if value in {"localhost", "ip6-localhost"}:
        return True
    try:
        return ipaddress.ip_address(value).is_loopback
    except ValueError:
        return False


def _normalize_priority(value: str | None, *, default: str = "interactive") -> tuple[int, str]:
    name = str(value or default).strip().lower()
    if name not in _PRIORITY_VALUES:
        name = default
    return _PRIORITY_VALUES[name], name


@dataclass(slots=True)
class ProxyConfig:
    upstream_url: str
    listen_host: str = "127.0.0.1"
    listen_port: int = 11435
    queue_timeout_seconds: float = 900.0
    upstream_timeout_seconds: float = 600.0
    max_pending: int = 128
    starvation_seconds: float = 600.0
    max_concurrency: int = 2
    background_concurrency: int = 1
    background_burst_concurrency: int = 2
    background_burst_idle_seconds: float = 5.0
    buffer_bytes: int = 16_384
    connect_timeout_seconds: float = 10.0

    @classmethod
    def from_env(cls) -> "ProxyConfig":
        upstream = os.environ.get("OLLAMA_PRIORITY_UPSTREAM", "").strip()
        if not upstream:
            raise ValueError("OLLAMA_PRIORITY_UPSTREAM ist nicht gesetzt")
        config = cls(
            upstream_url=upstream,
            listen_host=os.environ.get("OLLAMA_PRIORITY_LISTEN_HOST", "127.0.0.1").strip(),
            listen_port=_safe_int(os.environ.get("OLLAMA_PRIORITY_LISTEN_PORT"), 11435),
            queue_timeout_seconds=_safe_float(os.environ.get("OLLAMA_PRIORITY_QUEUE_TIMEOUT"), 900.0),
            upstream_timeout_seconds=_safe_float(os.environ.get("OLLAMA_PRIORITY_UPSTREAM_TIMEOUT"), 600.0),
            max_pending=_safe_int(os.environ.get("OLLAMA_PRIORITY_MAX_PENDING"), 128),
            starvation_seconds=_safe_float(os.environ.get("OLLAMA_PRIORITY_STARVATION_SECONDS"), 600.0),
            max_concurrency=_safe_int(os.environ.get("OLLAMA_PRIORITY_MAX_CONCURRENCY"), 2),
            background_concurrency=_safe_int(os.environ.get("OLLAMA_PRIORITY_BACKGROUND_CONCURRENCY"), 1),
            background_burst_concurrency=_safe_int(
                os.environ.get("OLLAMA_PRIORITY_BACKGROUND_BURST_CONCURRENCY"), 2
            ),
            background_burst_idle_seconds=_safe_float(
                os.environ.get("OLLAMA_PRIORITY_BACKGROUND_BURST_IDLE_SECONDS"), 5.0
            ),
            buffer_bytes=_safe_int(os.environ.get("OLLAMA_PRIORITY_BUFFER_BYTES"), 16_384),
            connect_timeout_seconds=_safe_float(os.environ.get("OLLAMA_PRIORITY_CONNECT_TIMEOUT"), 10.0),
        )
        config.validate()
        return config

    @property
    def listen_url(self) -> str:
        host = "[::1]" if self.listen_host == "::1" else self.listen_host
        return f"http://{host}:{self.listen_port}"

    def validate(self) -> None:
        parsed = urlsplit(self.upstream_url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("OLLAMA_PRIORITY_UPSTREAM muss eine vollstaendige HTTP(S)-URL sein")
        if parsed.username or parsed.password:
            raise ValueError("OLLAMA_PRIORITY_UPSTREAM darf keine Zugangsdaten in der URL enthalten")
        if parsed.query or parsed.fragment or parsed.path not in {"", "/"}:
            raise ValueError("OLLAMA_PRIORITY_UPSTREAM muss auf die Ollama-Basis-URL ohne Unterpfad zeigen")
        if not _is_loopback_host(self.listen_host):
            raise ValueError("Der Prioritaetsproxy darf aus Sicherheitsgruenden nur an Loopback binden")
        if not 1 <= self.listen_port <= 65535:
            raise ValueError("OLLAMA_PRIORITY_LISTEN_PORT ist ungueltig")
        if self.max_pending < 1:
            raise ValueError("OLLAMA_PRIORITY_MAX_PENDING muss mindestens 1 sein")
        if self.max_concurrency < 1:
            raise ValueError("OLLAMA_PRIORITY_MAX_CONCURRENCY muss mindestens 1 sein")
        if not 1 <= self.background_concurrency <= self.max_concurrency:
            raise ValueError("OLLAMA_PRIORITY_BACKGROUND_CONCURRENCY ist ausserhalb der Gesamtparallelitaet")
        if not self.background_concurrency <= self.background_burst_concurrency <= self.max_concurrency:
            raise ValueError("OLLAMA_PRIORITY_BACKGROUND_BURST_CONCURRENCY ist ungueltig")
        if self.background_burst_idle_seconds < 0:
            raise ValueError("OLLAMA_PRIORITY_BACKGROUND_BURST_IDLE_SECONDS darf nicht negativ sein")
        if self.queue_timeout_seconds <= 0 or self.upstream_timeout_seconds <= 0:
            raise ValueError("Zeitlimits muessen groesser als 0 sein")
        if self.buffer_bytes < 1024:
            raise ValueError("OLLAMA_PRIORITY_BUFFER_BYTES muss mindestens 1024 sein")
        upstream_port = parsed.port or (443 if parsed.scheme == "https" else 80)
        if _is_loopback_host(parsed.hostname) and upstream_port == self.listen_port:
            raise ValueError("Upstream und Proxy duerfen nicht dieselbe Loopback-Adresse verwenden")


@dataclass(slots=True)
class QueueTicket:
    priority: int
    priority_name: str
    sequence: int
    source: str
    enqueued_at: float = field(default_factory=time.monotonic)
    granted_at: float = 0.0
    cancelled: bool = False
    allow_background_burst: bool = False

    @property
    def queue_wait_ms(self) -> float:
        end = self.granted_at or time.monotonic()
        return max(0.0, (end - self.enqueued_at) * 1000.0)


class QueueFullError(RuntimeError):
    pass


class QueueTimeoutError(TimeoutError):
    pass


class PriorityGate:
    """Two-slot, non-preemptive priority scheduler with a protected foreground lane.

    Interactive, normal and maintenance requests may use every free slot. Background
    work normally receives only ``background_concurrency`` slots. A request explicitly
    marked for catch-up may temporarily use the second slot, but only while no
    foreground request is active or waiting and the foreground idle grace elapsed.
    """

    def __init__(
        self,
        *,
        max_pending: int = 128,
        starvation_seconds: float = 600.0,
        max_concurrency: int = 2,
        background_concurrency: int = 1,
        background_burst_concurrency: int = 2,
        background_burst_idle_seconds: float = 5.0,
    ) -> None:
        self.max_pending = max(1, int(max_pending))
        self.starvation_seconds = max(0.01, float(starvation_seconds))
        self.max_concurrency = max(1, int(max_concurrency))
        self.background_concurrency = max(1, min(int(background_concurrency), self.max_concurrency))
        self.background_burst_concurrency = max(
            self.background_concurrency,
            min(int(background_burst_concurrency), self.max_concurrency),
        )
        self.background_burst_idle_seconds = max(0.0, float(background_burst_idle_seconds))
        self._condition = threading.Condition()
        self._pending: list[QueueTicket] = []
        self._active: list[QueueTicket] = []
        self._sequence = 0
        self._last_foreground_activity = time.monotonic()

    def acquire(
        self,
        priority_name: str,
        *,
        source: str = "",
        timeout: float = 900.0,
        allow_background_burst: bool = False,
    ) -> QueueTicket:
        priority, normalized = _normalize_priority(priority_name)
        deadline = time.monotonic() + max(0.001, float(timeout))
        with self._condition:
            if len(self._pending) >= self.max_pending:
                raise QueueFullError("Prioritaetswarteschlange ist voll")
            self._sequence += 1
            ticket = QueueTicket(
                priority,
                normalized,
                self._sequence,
                source[:80],
                allow_background_burst=bool(allow_background_burst),
            )
            self._pending.append(ticket)
            self._condition.notify_all()
            while True:
                if ticket.cancelled:
                    raise QueueTimeoutError("Anfrage wurde aus der Warteschlange entfernt")
                if self._select_next_locked() is ticket:
                    self._pending.remove(ticket)
                    ticket.granted_at = time.monotonic()
                    self._active.append(ticket)
                    if ticket.priority_name != "background":
                        self._last_foreground_activity = ticket.granted_at
                    self._condition.notify_all()
                    return ticket
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    if ticket in self._pending:
                        self._pending.remove(ticket)
                    ticket.cancelled = True
                    self._condition.notify_all()
                    raise QueueTimeoutError("Zeitlimit in der Prioritaetswarteschlange ueberschritten")
                self._condition.wait(timeout=min(remaining, 0.5))

    def release(self, ticket: QueueTicket) -> None:
        with self._condition:
            if ticket in self._active:
                self._active.remove(ticket)
                if ticket.priority_name != "background":
                    self._last_foreground_activity = time.monotonic()
                self._condition.notify_all()

    def _foreground_present_locked(self) -> bool:
        return any(item.priority_name != "background" for item in self._active) or any(
            item.priority_name != "background" for item in self._pending
        )

    def _can_run_locked(self, ticket: QueueTicket) -> bool:
        if len(self._active) >= self.max_concurrency:
            return False
        if ticket.priority_name != "background":
            return True
        background_active = sum(1 for item in self._active if item.priority_name == "background")
        if background_active < self.background_concurrency:
            return True
        if not ticket.allow_background_burst:
            return False
        if background_active >= self.background_burst_concurrency:
            return False
        if self._foreground_present_locked():
            return False
        idle_seconds = time.monotonic() - self._last_foreground_activity
        return idle_seconds >= self.background_burst_idle_seconds

    def _select_next_locked(self) -> QueueTicket | None:
        eligible = [item for item in self._pending if self._can_run_locked(item)]
        if not eligible:
            return None
        now = time.monotonic()
        starved = [item for item in eligible if now - item.enqueued_at >= self.starvation_seconds]
        if starved:
            return min(starved, key=lambda item: (item.enqueued_at, item.sequence))
        return min(eligible, key=lambda item: (item.priority, item.sequence))

    def snapshot(self) -> dict[str, object]:
        with self._condition:
            counts = {name: 0 for name in _PRIORITY_VALUES}
            for item in self._pending:
                counts[item.priority_name] = counts.get(item.priority_name, 0) + 1
            active_items = [
                {
                    "priority": item.priority_name,
                    "source": item.source,
                    "running_ms": round((time.monotonic() - item.granted_at) * 1000.0, 3),
                    "background_burst": bool(item.allow_background_burst),
                }
                for item in sorted(self._active, key=lambda value: value.sequence)
            ]
            background_active = sum(1 for item in self._active if item.priority_name == "background")
            return {
                # Compatibility for older status consumers that expected one active object.
                "active": active_items[0] if active_items else None,
                "active_requests": active_items,
                "active_count": len(active_items),
                "background_active": background_active,
                "pending": len(self._pending),
                "pending_by_priority": counts,
                "max_pending": self.max_pending,
                "max_concurrency": self.max_concurrency,
                "background_concurrency": self.background_concurrency,
                "background_burst_concurrency": self.background_burst_concurrency,
                "background_burst_idle_seconds": self.background_burst_idle_seconds,
                "starvation_seconds": self.starvation_seconds,
            }


@dataclass(slots=True)
class ProxyStats:
    started_at: float = field(default_factory=time.time)
    total_requests: int = 0
    scheduled_requests: int = 0
    completed_requests: int = 0
    failed_requests: int = 0
    queue_timeouts: int = 0
    queue_full: int = 0
    upstream_timeouts: int = 0
    client_disconnects: int = 0
    queue_wait_total_ms: float = 0.0
    queue_wait_max_ms: float = 0.0
    by_priority: dict[str, int] = field(default_factory=dict)
    concurrency_max_observed: int = 0
    background_concurrency_max_observed: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def record_start(self, *, scheduled: bool, priority: str) -> None:
        with self._lock:
            self.total_requests += 1
            if scheduled:
                self.scheduled_requests += 1
                self.by_priority[priority] = self.by_priority.get(priority, 0) + 1

    def record_grant(self, wait_ms: float, *, active_count: int, background_active: int) -> None:
        with self._lock:
            self.queue_wait_total_ms += max(0.0, wait_ms)
            self.queue_wait_max_ms = max(self.queue_wait_max_ms, max(0.0, wait_ms))
            self.concurrency_max_observed = max(self.concurrency_max_observed, int(active_count))
            self.background_concurrency_max_observed = max(
                self.background_concurrency_max_observed, int(background_active)
            )

    def record_finish(self, *, ok: bool) -> None:
        with self._lock:
            if ok:
                self.completed_requests += 1
            else:
                self.failed_requests += 1

    def record_queue_timeout(self) -> None:
        with self._lock:
            self.queue_timeouts += 1
            self.failed_requests += 1

    def record_queue_full(self) -> None:
        with self._lock:
            self.queue_full += 1
            self.failed_requests += 1

    def record_upstream_timeout(self) -> None:
        with self._lock:
            self.upstream_timeouts += 1

    def record_client_disconnect(self) -> None:
        with self._lock:
            self.client_disconnects += 1

    def snapshot(self) -> dict[str, object]:
        with self._lock:
            average = self.queue_wait_total_ms / self.scheduled_requests if self.scheduled_requests else 0.0
            return {
                "uptime_seconds": round(max(0.0, time.time() - self.started_at), 3),
                "total_requests": self.total_requests,
                "scheduled_requests": self.scheduled_requests,
                "completed_requests": self.completed_requests,
                "failed_requests": self.failed_requests,
                "queue_timeouts": self.queue_timeouts,
                "queue_full": self.queue_full,
                "upstream_timeouts": self.upstream_timeouts,
                "client_disconnects": self.client_disconnects,
                "queue_wait_average_ms": round(average, 3),
                "queue_wait_max_ms": round(self.queue_wait_max_ms, 3),
                "concurrency_max_observed": self.concurrency_max_observed,
                "background_concurrency_max_observed": self.background_concurrency_max_observed,
                "by_priority": dict(sorted(self.by_priority.items())),
            }


class PriorityProxyServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, config: ProxyConfig) -> None:
        self.config = config
        self.gate = PriorityGate(
            max_pending=config.max_pending,
            starvation_seconds=config.starvation_seconds,
            max_concurrency=config.max_concurrency,
            background_concurrency=config.background_concurrency,
            background_burst_concurrency=config.background_burst_concurrency,
            background_burst_idle_seconds=config.background_burst_idle_seconds,
        )
        self.stats = ProxyStats()
        super().__init__((config.listen_host, config.listen_port), PriorityProxyHandler)


class PriorityProxyHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "OpenClawOllamaPriority/25"

    def setup(self) -> None:
        super().setup()
        self._response_started = False

    @property
    def priority_server(self) -> PriorityProxyServer:
        return self.server  # type: ignore[return-value]

    def log_message(self, fmt: str, *args: object) -> None:
        LOG.info("%s - %s", self.address_string(), fmt % args)

    def do_GET(self) -> None:  # noqa: N802
        if self.path.split("?", 1)[0] == "/healthz":
            self._health()
            return
        if self.path.split("?", 1)[0] == "/priority/status":
            self._status()
            return
        self._proxy_request()

    def do_POST(self) -> None:  # noqa: N802
        self._proxy_request()

    def do_DELETE(self) -> None:  # noqa: N802
        self._proxy_request()

    def do_PUT(self) -> None:  # noqa: N802
        self._proxy_request()

    def do_PATCH(self) -> None:  # noqa: N802
        self._proxy_request()

    def _json_response(self, status: int, payload: Mapping[str, object], **headers: str) -> None:
        data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self._response_started = True
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Connection", "close")
        for key, value in headers.items():
            self.send_header(key.replace("_", "-"), value)
        self.end_headers()
        self.wfile.write(data)
        self.close_connection = True

    def _status(self) -> None:
        self._json_response(
            200,
            {
                "ok": True,
                "listen": self.priority_server.config.listen_url,
                "upstream": self.priority_server.config.upstream_url,
                "queue": self.priority_server.gate.snapshot(),
                "stats": self.priority_server.stats.snapshot(),
            },
        )

    def _health(self) -> None:
        ok, detail = probe_upstream(self.priority_server.config, timeout=3.0)
        self._json_response(
            200 if ok else 503,
            {
                "ok": ok,
                "detail": detail,
                "queue": self.priority_server.gate.snapshot(),
                "stats": self.priority_server.stats.snapshot(),
            },
        )

    def _request_body(self) -> bytes:
        length_text = self.headers.get("Content-Length", "0")
        try:
            length = int(length_text)
        except ValueError as exc:
            raise ValueError("Ungueltiger Content-Length Header") from exc
        if length < 0 or length > 128 * 1024 * 1024:
            raise ValueError("Request-Body ist zu gross")
        return self.rfile.read(length) if length else b""

    def _priority(self) -> tuple[int, str]:
        return _normalize_priority(self.headers.get("X-OpenClaw-Priority"))

    def _scheduled(self) -> bool:
        return self.command in {"POST", "PUT"} and self.path.split("?", 1)[0] in _GPU_PATHS

    def _background_burst_requested(self, priority_name: str) -> bool:
        if priority_name != "background":
            return False
        value = str(self.headers.get("X-OpenClaw-Background-Burst") or "").strip().casefold()
        return value in {"1", "true", "yes", "on"}

    def _request_timeout(self, header: str, configured_max: float) -> float:
        requested = _safe_float(self.headers.get(header), configured_max)
        return max(1.0, min(float(configured_max), requested))

    def _proxy_request(self) -> None:
        scheduled = self._scheduled()
        _, priority_name = self._priority()
        source = str(self.headers.get("X-OpenClaw-Source") or self.headers.get("User-Agent") or "")[:80]
        self.priority_server.stats.record_start(scheduled=scheduled, priority=priority_name)
        try:
            body = self._request_body()
        except ValueError as exc:
            self.priority_server.stats.record_finish(ok=False)
            self._json_response(400, {"error": str(exc)})
            return

        ticket: QueueTicket | None = None
        queue_wait_ms = 0.0
        queue_timeout_seconds = self._request_timeout(
            "X-OpenClaw-Queue-Timeout-Seconds",
            self.priority_server.config.queue_timeout_seconds,
        )
        upstream_timeout_seconds = self._request_timeout(
            "X-OpenClaw-Upstream-Timeout-Seconds",
            self.priority_server.config.upstream_timeout_seconds,
        )
        if scheduled:
            queued_at = time.monotonic()
            try:
                ticket = self.priority_server.gate.acquire(
                    priority_name,
                    source=source,
                    timeout=queue_timeout_seconds,
                    allow_background_burst=self._background_burst_requested(priority_name),
                )
                queue_wait_ms = ticket.queue_wait_ms
                gate_snapshot = self.priority_server.gate.snapshot()
                self.priority_server.stats.record_grant(
                    queue_wait_ms,
                    active_count=int(gate_snapshot.get("active_count") or 0),
                    background_active=int(gate_snapshot.get("background_active") or 0),
                )
            except QueueFullError as exc:
                self.priority_server.stats.record_queue_full()
                self._json_response(
                    503,
                    {"error": str(exc), "error_type": "queue_full", "retry_after": 30},
                    Retry_After="30",
                )
                return
            except QueueTimeoutError as exc:
                queue_wait_ms = max(0.0, (time.monotonic() - queued_at) * 1000.0)
                self.priority_server.stats.record_queue_timeout()
                self._json_response(
                    503,
                    {"error": str(exc), "error_type": "queue_timeout", "retry_after": 30},
                    Retry_After="30",
                    X_Ollama_Queue_Wait_Ms=f"{queue_wait_ms:.3f}",
                )
                return
            except Exception as exc:  # Defensive fail-open: preserve existing inference path.
                LOG.exception("Prioritaetssteuerung ausgefallen; Anfrage wird direkt weitergeleitet: %s", exc)
                ticket = None

        ok = False
        try:
            self._forward(
                body=body,
                queue_wait_ms=queue_wait_ms,
                priority_name=priority_name,
                upstream_timeout_seconds=upstream_timeout_seconds,
            )
            ok = True
        except (BrokenPipeError, ConnectionResetError):
            self.priority_server.stats.record_client_disconnect()
            LOG.info("Client hat die Verbindung vorzeitig beendet")
        except (socket.timeout, TimeoutError) as exc:
            self.priority_server.stats.record_upstream_timeout()
            LOG.warning("Ollama-Upstream-Zeitlimit nach %.1fs: %s", upstream_timeout_seconds, exc)
            if not self._response_started and not self.wfile.closed:
                try:
                    self._json_response(
                        504,
                        {"error": "Ollama-Modelllauf hat das Zeitlimit ueberschritten", "error_type": "upstream_timeout"},
                        X_Ollama_Queue_Wait_Ms=f"{queue_wait_ms:.3f}",
                    )
                except (BrokenPipeError, ConnectionResetError):
                    pass
        except Exception as exc:
            LOG.exception("Ollama-Proxyfehler: %s", exc)
            if not self._response_started and not self.wfile.closed:
                try:
                    self._json_response(
                        502,
                        {"error": "Ollama-Upstream nicht erreichbar", "error_type": "upstream_error", "detail": str(exc)[:300]},
                        X_Ollama_Queue_Wait_Ms=f"{queue_wait_ms:.3f}",
                    )
                except (BrokenPipeError, ConnectionResetError):
                    pass
        finally:
            if ticket is not None:
                self.priority_server.gate.release(ticket)
            self.priority_server.stats.record_finish(ok=ok)

    def _forward(
        self,
        *,
        body: bytes,
        queue_wait_ms: float,
        priority_name: str,
        upstream_timeout_seconds: float,
    ) -> None:
        config = self.priority_server.config
        parsed = urlsplit(config.upstream_url)
        scheme = parsed.scheme
        port = parsed.port or (443 if scheme == "https" else 80)
        connection_cls = http.client.HTTPSConnection if scheme == "https" else http.client.HTTPConnection
        kwargs: dict[str, Any] = {"timeout": upstream_timeout_seconds}
        if scheme == "https":
            kwargs["context"] = ssl.create_default_context()
        connection = connection_cls(parsed.hostname, port, **kwargs)
        prefix = parsed.path.rstrip("/")
        target = prefix + self.path
        headers: dict[str, str] = {}
        for key, value in self.headers.items():
            lower = key.lower()
            if lower in _HOP_BY_HOP_HEADERS or lower in {
                "host", "content-length", "x-openclaw-priority", "x-openclaw-source",
                "x-openclaw-queue-timeout-seconds", "x-openclaw-upstream-timeout-seconds",
                "x-openclaw-background-burst",
            }:
                continue
            headers[key] = value
        headers["Host"] = parsed.netloc
        headers["Content-Length"] = str(len(body))
        headers["Connection"] = "close"

        try:
            connection.request(self.command, target, body=body, headers=headers)
            response = connection.getresponse()
            self._response_started = True
            self.send_response(response.status, response.reason)
            upstream_length = response.getheader("Content-Length")
            for key, value in response.getheaders():
                lower = key.lower()
                if lower in _HOP_BY_HOP_HEADERS or lower in {"server", "date", "content-length"}:
                    continue
                self.send_header(key, value)
            if upstream_length:
                self.send_header("Content-Length", upstream_length)
            self.send_header("X-Ollama-Queue-Wait-Ms", f"{queue_wait_ms:.3f}")
            self.send_header("X-Ollama-Priority", priority_name)
            self.send_header("X-Ollama-Upstream-Timeout-Seconds", f"{upstream_timeout_seconds:.3f}")
            gate_snapshot = self.priority_server.gate.snapshot()
            self.send_header("X-Ollama-Active-Slots", str(gate_snapshot.get("active_count") or 0))
            self.send_header("X-Ollama-Max-Slots", str(gate_snapshot.get("max_concurrency") or 1))
            self.send_header("Connection", "close")
            self.end_headers()
            while True:
                chunk = response.read1(config.buffer_bytes)
                if not chunk:
                    break
                self.wfile.write(chunk)
                self.wfile.flush()
            self.close_connection = True
        finally:
            connection.close()


def probe_upstream(config: ProxyConfig, *, timeout: float = 5.0) -> tuple[bool, str]:
    parsed = urlsplit(config.upstream_url)
    scheme = parsed.scheme
    port = parsed.port or (443 if scheme == "https" else 80)
    connection_cls = http.client.HTTPSConnection if scheme == "https" else http.client.HTTPConnection
    kwargs: dict[str, Any] = {"timeout": min(timeout, config.connect_timeout_seconds)}
    if scheme == "https":
        kwargs["context"] = ssl.create_default_context()
    connection = connection_cls(parsed.hostname, port, **kwargs)
    try:
        target = parsed.path.rstrip("/") + "/api/version"
        connection.request("GET", target, headers={"Connection": "close"})
        response = connection.getresponse()
        data = response.read(4096)
        if response.status != 200:
            return False, f"Upstream HTTP {response.status}"
        try:
            payload = json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            payload = {}
        version = str(payload.get("version") or "erreichbar") if isinstance(payload, dict) else "erreichbar"
        return True, f"Ollama {version}"
    except (OSError, http.client.HTTPException, ssl.SSLError) as exc:
        return False, str(exc)
    finally:
        connection.close()



def query_local_status(config: ProxyConfig, *, timeout: float = 5.0) -> tuple[bool, dict[str, object]]:
    connection = http.client.HTTPConnection(
        config.listen_host, config.listen_port, timeout=min(timeout, config.connect_timeout_seconds)
    )
    try:
        connection.request("GET", "/healthz", headers={"Connection": "close"})
        response = connection.getresponse()
        data = response.read(1_000_000)
        try:
            payload = json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            payload = {"ok": False, "detail": "Proxyantwort war kein JSON"}
        if not isinstance(payload, dict):
            payload = {"ok": False, "detail": "Proxyantwort war kein JSON-Objekt"}
        return response.status == 200 and bool(payload.get("ok")), payload
    except (OSError, http.client.HTTPException) as exc:
        return False, {"ok": False, "detail": str(exc)}
    finally:
        connection.close()

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Lokaler Prioritaetsproxy fuer OpenClaw/Ollama")
    parser.add_argument("--check", action="store_true", help="Upstream pruefen und beenden")
    parser.add_argument("--status", action="store_true", help="Lokalen Proxyzustand pruefen und beenden")
    parser.add_argument("--print-config", action="store_true", help="Effektive nicht-geheime Konfiguration ausgeben")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=os.environ.get("OLLAMA_PRIORITY_LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    try:
        config = ProxyConfig.from_env()
    except ValueError as exc:
        LOG.error("Konfigurationsfehler: %s", exc)
        return 2

    if args.status:
        ok, payload = query_local_status(config)
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0 if ok else 1
    if args.print_config:
        print(json.dumps({
            "upstream_url": config.upstream_url,
            "listen_url": config.listen_url,
            "queue_timeout_seconds": config.queue_timeout_seconds,
            "upstream_timeout_seconds": config.upstream_timeout_seconds,
            "max_pending": config.max_pending,
            "max_concurrency": config.max_concurrency,
            "background_concurrency": config.background_concurrency,
            "background_burst_concurrency": config.background_burst_concurrency,
            "background_burst_idle_seconds": config.background_burst_idle_seconds,
            "starvation_seconds": config.starvation_seconds,
        }, indent=2, ensure_ascii=False))
        return 0
    if args.check:
        ok, detail = probe_upstream(config)
        print(json.dumps({"ok": ok, "detail": detail}, ensure_ascii=False))
        return 0 if ok else 1

    server = PriorityProxyServer(config)
    stop_event = threading.Event()

    def stop_handler(signum: int, frame: object) -> None:
        del signum, frame
        if stop_event.is_set():
            return
        stop_event.set()
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, stop_handler)
    signal.signal(signal.SIGINT, stop_handler)
    LOG.info(
        "Ollama-Prioritaetsproxy gestartet: %s -> %s (slots=%s, background=%s/%s, starvation=%ss)",
        config.listen_url,
        config.upstream_url,
        config.max_concurrency,
        config.background_concurrency,
        config.background_burst_concurrency,
        config.starvation_seconds,
    )
    try:
        server.serve_forever(poll_interval=0.25)
    finally:
        server.server_close()
        LOG.info("Ollama-Prioritaetsproxy beendet")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
