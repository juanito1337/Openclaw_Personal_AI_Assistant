from __future__ import annotations

import hashlib
import hmac
import json
import os
import socket
import struct
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

RPC_SCHEMA_VERSION = 1
MAX_REQUEST_BYTES = 64 * 1024
DEFAULT_MAX_RESPONSE_BYTES = 128 * 1024


def _canonical(value: Mapping[str, Any]) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def _digest(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


@dataclass(frozen=True, slots=True)
class GatewaySandboxProfile:
    domain_secrets: tuple[str, ...] = ()
    read_write_domain_mounts: tuple[str, ...] = ()
    rpc_socket: str = "/run/openclaw-tool-executor/rpc.sock"

    def validate(self) -> None:
        if self.domain_secrets or self.read_write_domain_mounts:
            raise ValueError("Gateway-Prototyp darf keine Fach-Secrets oder RW-Domainmounts besitzen")
        if not self.rpc_socket.startswith("/run/") or not self.rpc_socket.endswith(".sock"):
            raise ValueError("Gateway-Prototyp benoetigt einen expliziten Runtime-Unix-Socket")


@dataclass(frozen=True, slots=True)
class ExecutorRoleProfile:
    role: str
    allowed_tool_ids: tuple[str, ...]
    domain_secrets: tuple[str, ...]
    read_write_mounts: tuple[str, ...]

    def validate(self, registered_tool_ids: set[str]) -> None:
        if not self.role or not self.allowed_tool_ids:
            raise ValueError("Executorrolle benoetigt Rolle und mindestens ein Tool")
        if not set(self.allowed_tool_ids) <= registered_tool_ids:
            raise ValueError("Executorrolle referenziert ein nicht registriertes Tool")
        if any(not path.startswith("/var/lib/openclaw/") for path in self.read_write_mounts):
            raise ValueError("Executor-RW-Mount liegt ausserhalb des Domainstate")


@dataclass(frozen=True, slots=True)
class RpcRequest:
    request_id: str
    tool_id: str
    tool_schema_version: int
    arguments: dict[str, Any]
    approval: str
    approval_binding: str
    evidence: dict[str, Any]
    deadline_epoch_ms: int
    idempotency_key: str
    nonce: str
    signature: str = ""

    def unsigned_payload(self) -> dict[str, Any]:
        payload = asdict(self)
        payload.pop("signature")
        return payload

    def to_payload(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> RpcRequest:
        required = {item.name for item in cls.__dataclass_fields__.values()}
        if set(payload) != required:
            raise ValueError("RPC-Request besitzt unbekannte oder fehlende Felder")
        arguments = payload.get("arguments")
        evidence = payload.get("evidence")
        if not isinstance(arguments, dict) or not isinstance(evidence, dict):
            raise ValueError("RPC-Argumente und Evidenz muessen Objekte sein")
        return cls(
            request_id=str(payload["request_id"]),
            tool_id=str(payload["tool_id"]),
            tool_schema_version=int(payload["tool_schema_version"]),
            arguments=dict(arguments),
            approval=str(payload["approval"]),
            approval_binding=str(payload["approval_binding"]),
            evidence=dict(evidence),
            deadline_epoch_ms=int(payload["deadline_epoch_ms"]),
            idempotency_key=str(payload["idempotency_key"]),
            nonce=str(payload["nonce"]),
            signature=str(payload["signature"]),
        )


@dataclass(frozen=True, slots=True)
class RpcResponse:
    request_id: str
    ok: bool
    status: str
    result: dict[str, Any] = field(default_factory=dict)
    blocker: dict[str, Any] = field(default_factory=dict)
    postcondition_verified: bool = False

    def to_payload(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> RpcResponse:
        return cls(
            request_id=str(payload.get("request_id") or ""),
            ok=bool(payload.get("ok")),
            status=str(payload.get("status") or "invalid-response"),
            result=dict(payload.get("result") or {}),
            blocker=dict(payload.get("blocker") or {}),
            postcondition_verified=bool(payload.get("postcondition_verified")),
        )


@dataclass(frozen=True, slots=True)
class PrototypeToolSpec:
    tool_id: str
    schema_version: int
    argument_types: dict[str, type]
    approval: str
    handler: Callable[[dict[str, Any]], dict[str, Any]]
    max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES

    def validate_arguments(self, arguments: Mapping[str, Any]) -> None:
        if set(arguments) != set(self.argument_types):
            raise ValueError("argument-schema-mismatch")
        for name, expected_type in self.argument_types.items():
            value = arguments[name]
            if expected_type is int and isinstance(value, bool):
                raise ValueError("argument-schema-mismatch")
            if not isinstance(value, expected_type):
                raise ValueError("argument-schema-mismatch")


def approval_binding(
    *,
    tool_id: str,
    tool_schema_version: int,
    arguments: Mapping[str, Any],
    idempotency_key: str,
) -> str:
    return _digest(
        {
            "tool_id": tool_id,
            "tool_schema_version": tool_schema_version,
            "arguments": dict(arguments),
            "idempotency_key": idempotency_key,
        }
    )


def sign_request(request: RpcRequest, rpc_auth_key: bytes) -> RpcRequest:
    if len(rpc_auth_key) < 32:
        raise ValueError("RPC-Authentisierungsschluessel ist zu kurz")
    signature = hmac.new(rpc_auth_key, _canonical(request.unsigned_payload()), hashlib.sha256).hexdigest()
    return RpcRequest(**{**request.unsigned_payload(), "signature": signature})


def build_gateway_request(
    *,
    tool_id: str,
    tool_schema_version: int,
    arguments: dict[str, Any],
    approval: str,
    evidence: dict[str, Any],
    deadline_epoch_ms: int,
    idempotency_key: str,
    request_id: str,
    nonce: str,
    rpc_auth_key: bytes,
) -> RpcRequest:
    request = RpcRequest(
        request_id=request_id,
        tool_id=tool_id,
        tool_schema_version=tool_schema_version,
        arguments=arguments,
        approval=approval,
        approval_binding=approval_binding(
            tool_id=tool_id,
            tool_schema_version=tool_schema_version,
            arguments=arguments,
            idempotency_key=idempotency_key,
        ),
        evidence=evidence,
        deadline_epoch_ms=deadline_epoch_ms,
        idempotency_key=idempotency_key,
        nonce=nonce,
    )
    return sign_request(request, rpc_auth_key)


class PrototypeToolExecutor:
    """Fail-closed RPC router. It is intentionally absent from the live catalog."""

    def __init__(
        self,
        specs: list[PrototypeToolSpec],
        *,
        rpc_auth_key: bytes,
        max_inflight: int = 1,
    ) -> None:
        if len(rpc_auth_key) < 32:
            raise ValueError("RPC-Authentisierungsschluessel ist zu kurz")
        if max_inflight < 1:
            raise ValueError("Executor benoetigt mindestens einen Ausfuehrungsslot")
        self._specs = {spec.tool_id: spec for spec in specs}
        if len(self._specs) != len(specs):
            raise ValueError("Doppelte Tool-ID im Executor-Prototyp")
        self._rpc_auth_key = rpc_auth_key
        self._max_inflight = max_inflight
        self._inflight = 0
        self._lock = threading.Lock()
        self._nonces: set[str] = set()
        self._idempotency: dict[str, str] = {}
        self.audit: list[dict[str, Any]] = []

    @property
    def registered_tool_ids(self) -> set[str]:
        return set(self._specs)

    @staticmethod
    def _blocked(request_id: str, code: str, detail: str) -> RpcResponse:
        return RpcResponse(
            request_id=request_id,
            ok=False,
            status="blocked",
            blocker={"code": code, "detail": detail, "shell_fallback": False},
        )

    def _record(self, request: RpcRequest, response: RpcResponse) -> None:
        self.audit.append(
            {
                "request_id": request.request_id,
                "tool_id": request.tool_id,
                "request_digest": _digest(request.unsigned_payload()),
                "idempotency_digest": hashlib.sha256(request.idempotency_key.encode()).hexdigest(),
                "status": response.status,
                "blocker_code": str(response.blocker.get("code") or ""),
                "argument_names": sorted(request.arguments),
                "content_recorded": False,
            }
        )

    def _authenticated(self, request: RpcRequest) -> bool:
        expected = hmac.new(
            self._rpc_auth_key,
            _canonical(request.unsigned_payload()),
            hashlib.sha256,
        ).hexdigest()
        return hmac.compare_digest(expected, request.signature)

    def handle(self, request: RpcRequest, *, now_epoch_ms: int | None = None) -> RpcResponse:
        now = int(time.time() * 1000) if now_epoch_ms is None else now_epoch_ms
        if not self._authenticated(request):
            response = self._blocked(request.request_id, "authentication-failed", "RPC-Signatur ungueltig")
            self._record(request, response)
            return response
        if request.deadline_epoch_ms < now:
            response = self._blocked(request.request_id, "deadline-exceeded", "RPC-Deadline ist abgelaufen")
            self._record(request, response)
            return response
        with self._lock:
            if request.nonce in self._nonces:
                response = self._blocked(
                    request.request_id,
                    "replay-detected",
                    "RPC-Nonce wurde bereits verwendet",
                )
                self._record(request, response)
                return response
            self._nonces.add(request.nonce)
            request_digest = _digest(request.unsigned_payload())
            previous = self._idempotency.get(request.idempotency_key)
            if previous is not None:
                code = "duplicate-request" if previous == request_digest else "idempotency-conflict"
                response = self._blocked(
                    request.request_id,
                    code,
                    "Idempotenzschluessel wurde bereits verwendet",
                )
                self._record(request, response)
                return response
            if self._inflight >= self._max_inflight:
                response = self._blocked(request.request_id, "backpressure", "Executor-Kapazitaet ist belegt")
                self._record(request, response)
                return response
            self._inflight += 1
            self._idempotency[request.idempotency_key] = request_digest
        try:
            spec = self._specs.get(request.tool_id)
            if spec is None:
                response = self._blocked(request.request_id, "unknown-tool", "Tool-ID ist nicht registriert")
            elif request.tool_schema_version != spec.schema_version:
                response = self._blocked(
                    request.request_id,
                    "schema-version-mismatch",
                    "Tool-Schema ist fremd",
                )
            else:
                try:
                    spec.validate_arguments(request.arguments)
                except ValueError as exc:
                    response = self._blocked(
                        request.request_id,
                        str(exc),
                        "Argumente verletzen das geschlossene Schema",
                    )
                else:
                    expected_binding = approval_binding(
                        tool_id=request.tool_id,
                        tool_schema_version=request.tool_schema_version,
                        arguments=request.arguments,
                        idempotency_key=request.idempotency_key,
                    )
                    if request.approval != spec.approval or not hmac.compare_digest(
                        request.approval_binding, expected_binding
                    ):
                        response = self._blocked(
                            request.request_id,
                            "approval-mismatch",
                            "Approval ist nicht exakt gebunden",
                        )
                    else:
                        try:
                            result = spec.handler(dict(request.arguments))
                            if not isinstance(result, dict):
                                raise TypeError("Toolresultat ist kein Objekt")
                            response = RpcResponse(
                                request_id=request.request_id,
                                ok=True,
                                status="completed",
                                result=result,
                                postcondition_verified=bool(result.get("postcondition_verified")),
                            )
                            if len(_canonical(response.to_payload())) > spec.max_response_bytes:
                                response = self._blocked(
                                    request.request_id,
                                    "response-too-large",
                                    "Toolantwort ueberschreitet das Groessenlimit",
                                )
                        except Exception as exc:  # noqa: BLE001 - prototype must map every crash
                            response = self._blocked(
                                request.request_id,
                                "executor-unavailable",
                                f"Executorfehler: {type(exc).__name__}",
                            )
        finally:
            with self._lock:
                self._inflight -= 1
        self._record(request, response)
        return response


def encode_frame(payload: Mapping[str, Any], *, max_bytes: int) -> bytes:
    body = _canonical(payload)
    if len(body) > max_bytes:
        raise ValueError("rpc-frame-too-large")
    return struct.pack("!I", len(body)) + body


def _read_exact(channel: socket.socket, size: int) -> bytes:
    chunks: list[bytes] = []
    remaining = size
    while remaining:
        chunk = channel.recv(remaining)
        if not chunk:
            raise ValueError("rpc-frame-truncated")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def receive_frame(channel: socket.socket, *, max_bytes: int) -> dict[str, Any]:
    size = struct.unpack("!I", _read_exact(channel, 4))[0]
    if size > max_bytes:
        raise ValueError("rpc-frame-too-large")
    payload = json.loads(_read_exact(channel, size))
    if not isinstance(payload, dict):
        raise ValueError("rpc-frame-not-object")
    return payload


class OneShotUnixExecutorServer:
    """One-request AF_UNIX harness proving the proposed local trust boundary."""

    def __init__(self, path: Path, executor: PrototypeToolExecutor) -> None:
        self.path = path
        self.executor = executor

    def serve(self, ready: threading.Event, *, timeout_seconds: float = 2.0) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.unlink(missing_ok=True)
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
            server.bind(str(self.path))
            os.chmod(self.path, 0o600)
            server.listen(1)
            server.settimeout(timeout_seconds)
            ready.set()
            channel, _ = server.accept()
            with channel:
                try:
                    request = RpcRequest.from_payload(
                        receive_frame(channel, max_bytes=MAX_REQUEST_BYTES)
                    )
                    response = self.executor.handle(request)
                except Exception as exc:  # noqa: BLE001 - typed transport blocker
                    response = RpcResponse(
                        request_id="",
                        ok=False,
                        status="blocked",
                        blocker={
                            "code": "invalid-rpc-frame",
                            "detail": type(exc).__name__,
                            "shell_fallback": False,
                        },
                    )
                channel.sendall(
                    encode_frame(response.to_payload(), max_bytes=DEFAULT_MAX_RESPONSE_BYTES)
                )
        self.path.unlink(missing_ok=True)


def unix_rpc_call(path: Path, request: RpcRequest, *, timeout_seconds: float = 2.0) -> RpcResponse:
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as channel:
        channel.settimeout(timeout_seconds)
        channel.connect(str(path))
        channel.sendall(encode_frame(request.to_payload(), max_bytes=MAX_REQUEST_BYTES))
        payload = receive_frame(channel, max_bytes=DEFAULT_MAX_RESPONSE_BYTES)
    return RpcResponse.from_payload(payload)
