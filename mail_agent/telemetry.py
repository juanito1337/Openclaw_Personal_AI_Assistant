from __future__ import annotations

import json
import fcntl
import logging
import os
import time
import threading
import uuid
from collections import defaultdict
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence


_MAX_FILE_BYTES = 20_000_000
_CHECKPOINT_PHASES = {"preflight", "mail_processing", "classification", "digest"}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _round_ms(value: float) -> float:
    return round(max(0.0, value), 3)


def _duration_ms_from_ns(value: object) -> float:
    try:
        return _round_ms(float(value) / 1_000_000.0)
    except (TypeError, ValueError):
        return 0.0


def _boot_id() -> str:
    try:
        return Path("/proc/sys/kernel/random/boot_id").read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _proc_start_ticks(pid: int) -> str:
    if pid <= 0:
        return ""
    try:
        raw = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
        end = raw.rfind(")")
        if end < 0:
            return ""
        fields = raw[end + 2:].split()
        return fields[19] if len(fields) > 19 else ""
    except OSError:
        return ""


def _owner_is_alive(payload: Mapping[str, object]) -> bool:
    try:
        pid = int(payload.get("pid") or 0)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    boot_id = str(payload.get("boot_id") or "")
    if boot_id and boot_id != _boot_id():
        return False
    expected_ticks = str(payload.get("proc_start_ticks") or "")
    actual_ticks = _proc_start_ticks(pid)
    if not actual_ticks:
        return False
    return not expected_ticks or expected_ticks == actual_ticks


@dataclass(slots=True)
class _Aggregate:
    count: int = 0
    total_ms: float = 0.0
    max_ms: float = 0.0
    failures: int = 0
    timeouts: int = 0
    stdout_bytes: int = 0
    stderr_bytes: int = 0

    def add(
        self,
        duration_ms: float,
        *,
        ok: bool = True,
        timeout: bool = False,
        stdout_bytes: int = 0,
        stderr_bytes: int = 0,
    ) -> None:
        duration_ms = max(0.0, float(duration_ms))
        self.count += 1
        self.total_ms += duration_ms
        self.max_ms = max(self.max_ms, duration_ms)
        if not ok:
            self.failures += 1
        if timeout:
            self.timeouts += 1
        self.stdout_bytes += max(0, int(stdout_bytes))
        self.stderr_bytes += max(0, int(stderr_bytes))

    def to_dict(self) -> dict[str, object]:
        return {
            "count": self.count,
            "total_ms": _round_ms(self.total_ms),
            "max_ms": _round_ms(self.max_ms),
            "failures": self.failures,
            "timeouts": self.timeouts,
            "stdout_bytes": self.stdout_bytes,
            "stderr_bytes": self.stderr_bytes,
        }


@dataclass(slots=True)
class PerformanceTelemetry:
    """Low-overhead, fail-open runtime telemetry for the productive mail agent.

    The recorder deliberately stores no subjects, addresses, message bodies, paths
    from mail content, command arguments, or model output. Telemetry failures are
    logged and never allowed to change mail-agent behaviour.
    """

    path: Path
    enabled: bool = True
    operation: str = "mail-agent"
    log: logging.Logger = field(default_factory=lambda: logging.getLogger(__name__))
    run_id: str = field(init=False, default="")
    started_at: str = field(init=False, default="")
    _started_monotonic: float = field(init=False, default=0.0)
    _phases: dict[str, _Aggregate] = field(init=False, default_factory=dict)
    _commands: dict[str, _Aggregate] = field(init=False, default_factory=dict)
    _ollama_attempts: list[dict[str, object]] = field(init=False, default_factory=list)
    _finished: bool = field(init=False, default=False)
    _write_error_reported: bool = field(init=False, default=False)
    _last_phase: str = field(init=False, default="starting")
    _progress_processed: int = field(init=False, default=0)
    _progress_skipped: int = field(init=False, default=0)
    _progress_error_count: int = field(init=False, default=0)
    _progress_classifier: dict[str, object] = field(init=False, default_factory=dict)
    _ollama_attempt_sequence: int = field(init=False, default=0)
    _checkpoint_enabled: bool = field(init=False, default=True)
    _lock: threading.RLock = field(init=False, default_factory=threading.RLock, repr=False)

    @classmethod
    def for_database(cls, database: Path, *, operation: str = "mail-agent") -> "PerformanceTelemetry":
        disabled = os.environ.get("MAIL_AGENT_TELEMETRY", "1").strip().casefold() in {
            "0", "false", "no", "off",
        }
        return cls(
            path=database.parent / "performance.jsonl",
            enabled=not disabled,
            operation=operation,
        )

    @property
    def inflight_path(self) -> Path:
        return self.path.with_name("performance-inflight.json")

    def reset(self, operation: str) -> None:
        live_owner_present = False
        if self.enabled:
            live_owner_present = self._recover_stale_inflight()
        self.operation = operation
        self.run_id = uuid.uuid4().hex
        self.started_at = _utc_now()
        self._started_monotonic = time.perf_counter()
        self._phases = defaultdict(_Aggregate)
        self._commands = defaultdict(_Aggregate)
        self._ollama_attempts = []
        self._finished = False
        self._last_phase = "starting"
        self._progress_processed = 0
        self._progress_skipped = 0
        self._progress_error_count = 0
        self._progress_classifier = {}
        self._ollama_attempt_sequence = 0
        self._checkpoint_enabled = not live_owner_present
        if self.enabled and self._checkpoint_enabled:
            self.checkpoint("starting")

    def ensure_started(self) -> None:
        if not self.run_id:
            self.reset(self.operation)

    @contextmanager
    def phase(self, name: str) -> Iterator[None]:
        if not self.enabled:
            yield
            return
        self.ensure_started()
        if name in _CHECKPOINT_PHASES:
            self.checkpoint(name)
        started = time.perf_counter()
        ok = True
        try:
            yield
        except BaseException:
            ok = False
            raise
        finally:
            self.record_phase(name, (time.perf_counter() - started) * 1000.0, ok=ok)

    def checkpoint(self, phase: str) -> None:
        with self._lock:
            if not self.enabled or not self._checkpoint_enabled:
                return
            self.ensure_started()
            self._last_phase = str(phase)[:120]
            payload = {
                "schema_version": 2,
                "record_type": "inflight",
                "run_id": self.run_id,
                "operation": self.operation,
                "started_at": self.started_at,
                "updated_at": _utc_now(),
                "last_phase": self._last_phase,
                "pid": os.getpid(),
                "boot_id": _boot_id(),
                "proc_start_ticks": _proc_start_ticks(os.getpid()),
                "processed": self._progress_processed,
                "skipped": self._progress_skipped,
                "error_count": self._progress_error_count,
                "phases": {name: value.to_dict() for name, value in sorted(self._phases.items())},
                "external_commands": {
                    name: value.to_dict() for name, value in sorted(self._commands.items())
                },
                "ollama": {
                    "attempts": [dict(item) for item in self._ollama_attempts],
                    "summary": self._ollama_summary(),
                },
                "classifier": dict(self._progress_classifier),
            }
            self._write_json_atomic(self.inflight_path, payload)

    def update_progress(
        self,
        *,
        processed: int,
        skipped: int,
        errors: Sequence[str] | int,
        classifier: Mapping[str, object] | None = None,
        phase: str = "progress",
    ) -> None:
        with self._lock:
            if not self.enabled:
                return
            self._progress_processed = max(0, int(processed))
            self._progress_skipped = max(0, int(skipped))
            self._progress_error_count = max(0, int(errors if isinstance(errors, int) else len(errors)))
            self._progress_classifier = dict(classifier or {})
            self.checkpoint(phase)

    def begin_ollama_attempt(
        self,
        *,
        format_mode: str,
        payload_bytes: int,
        prompt_chars: int,
        queue_timeout_seconds: int,
        upstream_timeout_seconds: int,
    ) -> str:
        with self._lock:
            if not self.enabled:
                return ""
            self.ensure_started()
            self._ollama_attempt_sequence += 1
            attempt_id = f"{self.run_id}-{self._ollama_attempt_sequence}"
            self._ollama_attempts.append({
                "attempt_id": attempt_id,
                "format": format_mode,
                "state": "running",
                "started_at": _utc_now(),
                "payload_bytes": max(0, int(payload_bytes)),
                "prompt_chars": max(0, int(prompt_chars)),
                "queue_timeout_seconds": max(0, int(queue_timeout_seconds)),
                "upstream_timeout_seconds": max(0, int(upstream_timeout_seconds)),
                "ok": False,
                "timeout": False,
                "error_type": "",
                "queue_wait_ms": 0.0,
            })
            self.checkpoint(f"ollama.{format_mode}.waiting")
            return attempt_id

    def record_phase(self, name: str, duration_ms: float, *, ok: bool = True) -> None:
        if not self.enabled:
            return
        self.ensure_started()
        try:
            aggregate = self._phases.setdefault(str(name), _Aggregate())
            aggregate.add(duration_ms, ok=ok)
        except Exception as exc:  # pragma: no cover - defensive fail-open guard
            self.log.debug("Performance-Telemetrie konnte Phase nicht erfassen: %s", exc)

    def observe_command(self, event: Mapping[str, object]) -> None:
        if not self.enabled:
            return
        self.ensure_started()
        try:
            key = str(event.get("category") or "external.unknown")
            aggregate = self._commands.setdefault(key, _Aggregate())
            aggregate.add(
                float(event.get("duration_ms") or 0.0),
                ok=bool(event.get("ok", False)),
                timeout=bool(event.get("timeout", False)),
                stdout_bytes=int(event.get("stdout_bytes") or 0),
                stderr_bytes=int(event.get("stderr_bytes") or 0),
            )
        except Exception as exc:  # pragma: no cover - defensive fail-open guard
            self.log.debug("Performance-Telemetrie konnte Kommando nicht erfassen: %s", exc)

    def record_antivirus(self, *, duration_ms: float, status: str, source_type: str) -> None:
        if not self.enabled:
            return
        key = "antivirus." + (source_type or "unknown").replace(" ", "-")
        self.record_phase(key, duration_ms, ok=status in {"clean", "disabled"})

    def record_ollama_attempt(
        self,
        *,
        format_mode: str,
        client_duration_ms: float,
        payload_bytes: int,
        prompt_chars: int,
        response: Mapping[str, object] | None = None,
        error: str = "",
        timeout: bool = False,
        queue_wait_ms: float = 0.0,
        attempt_id: str = "",
    ) -> None:
        with self._lock:
            if not self.enabled:
                return
            self.ensure_started()
            response = response or {}
            item: dict[str, object] = {
                "attempt_id": attempt_id,
                "format": format_mode,
                "state": "finished",
                "finished_at": _utc_now(),
                "client_duration_ms": _round_ms(client_duration_ms),
                "payload_bytes": max(0, int(payload_bytes)),
                "prompt_chars": max(0, int(prompt_chars)),
                "ok": not bool(error),
                "timeout": bool(timeout),
                "error_type": error[:80],
                "total_duration_ms": _duration_ms_from_ns(response.get("total_duration")),
                "load_duration_ms": _duration_ms_from_ns(response.get("load_duration")),
                "prompt_eval_count": int(response.get("prompt_eval_count") or 0),
                "prompt_eval_duration_ms": _duration_ms_from_ns(response.get("prompt_eval_duration")),
                "eval_count": int(response.get("eval_count") or 0),
                "eval_duration_ms": _duration_ms_from_ns(response.get("eval_duration")),
                "done_reason": str(response.get("done_reason") or "")[:80],
                "queue_wait_ms": _round_ms(queue_wait_ms),
            }
            server_ms = float(item["total_duration_ms"] or 0.0)
            item["client_overhead_ms"] = _round_ms(max(0.0, client_duration_ms - server_ms))
            replaced = False
            if attempt_id:
                for index, current in enumerate(self._ollama_attempts):
                    if current.get("attempt_id") == attempt_id:
                        merged = dict(current)
                        merged.update(item)
                        self._ollama_attempts[index] = merged
                        replaced = True
                        break
            if not replaced:
                self._ollama_attempts.append(item)
            self.record_phase("ollama.client", client_duration_ms, ok=not bool(error))
            self.checkpoint(f"ollama.{format_mode}.finished")

    def compact_snapshot(self) -> dict[str, object]:
        self.ensure_started()
        total_ms = (time.perf_counter() - self._started_monotonic) * 1000.0
        ollama_client = sum(float(item.get("client_duration_ms") or 0.0) for item in self._ollama_attempts)
        ollama_server = sum(float(item.get("total_duration_ms") or 0.0) for item in self._ollama_attempts)
        ollama_queue = sum(float(item.get("queue_wait_ms") or 0.0) for item in self._ollama_attempts)
        return {
            "run_id": self.run_id,
            "operation": self.operation,
            "started_at": self.started_at,
            "total_ms": _round_ms(total_ms),
            "ollama_attempts": len(self._ollama_attempts),
            "ollama_client_ms": _round_ms(ollama_client),
            "ollama_server_ms": _round_ms(ollama_server),
            "ollama_queue_wait_ms": _round_ms(ollama_queue),
            "file": str(self.path),
            "enabled": self.enabled,
        }

    def finish(
        self,
        *,
        processed: int,
        skipped: int,
        errors: Sequence[str],
        classifier: Mapping[str, object] | None = None,
        drain: Mapping[str, object] | None = None,
    ) -> dict[str, object]:
        with self._lock:
            if not self.enabled:
                return {"enabled": False}
            self.ensure_started()
            if self._finished:
                return self.compact_snapshot()
            self._finished = True
            self._progress_processed = max(0, int(processed))
            self._progress_skipped = max(0, int(skipped))
            self._progress_error_count = len(errors)
            self._progress_classifier = dict(classifier or {})
            total_ms = (time.perf_counter() - self._started_monotonic) * 1000.0
            record: dict[str, object] = {
                "schema_version": 2,
                "record_type": "run",
                "run_id": self.run_id,
                "operation": self.operation,
                "started_at": self.started_at,
                "finished_at": _utc_now(),
                "total_ms": _round_ms(total_ms),
                "outcome": "ok" if not errors else "error",
                "processed": max(0, int(processed)),
                "skipped": max(0, int(skipped)),
                "error_count": len(errors),
                "phases": {name: value.to_dict() for name, value in sorted(self._phases.items())},
                "external_commands": {
                    name: value.to_dict() for name, value in sorted(self._commands.items())
                },
                "ollama": {
                    "attempts": self._ollama_attempts,
                    "summary": self._ollama_summary(),
                },
                "classifier": dict(classifier or {}),
            }
            if drain:
                record["drain"] = dict(drain)
            self._append_record(record)
            self._remove_inflight()
            return self.compact_snapshot()

    def _recover_stale_inflight(self) -> bool:
        path = self.inflight_path
        if not path.is_file():
            return False
        lock_path = path.with_suffix(path.suffix + ".lock")
        try:
            lock_path.parent.mkdir(parents=True, exist_ok=True)
            with lock_path.open("a+", encoding="utf-8") as lock_file:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
                if not path.is_file():
                    return False
                try:
                    stale = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    path.unlink(missing_ok=True)
                    return False
                if not isinstance(stale, Mapping) or not stale.get("run_id"):
                    path.unlink(missing_ok=True)
                    return False
                if _owner_is_alive(stale):
                    return True
                run_id = str(stale.get("run_id"))
                if self._run_id_already_recorded(run_id):
                    path.unlink(missing_ok=True)
                    return False
                started_at = str(stale.get("started_at") or "")
                total_ms = 0.0
                try:
                    started = datetime.fromisoformat(started_at)
                    total_ms = (datetime.now(timezone.utc) - started.astimezone(timezone.utc)).total_seconds() * 1000.0
                except (TypeError, ValueError):
                    pass
                ollama = stale.get("ollama") if isinstance(stale.get("ollama"), Mapping) else {}
                interrupted = {
                    "schema_version": 2,
                    "record_type": "run",
                    "run_id": run_id,
                    "operation": str(stale.get("operation") or "mail-agent"),
                    "started_at": started_at,
                    "finished_at": _utc_now(),
                    "total_ms": _round_ms(total_ms),
                    "outcome": "interrupted",
                    "interrupt_reason": "owner-process-not-alive",
                    "processed": max(0, int(stale.get("processed") or 0)),
                    "skipped": max(0, int(stale.get("skipped") or 0)),
                    "error_count": max(1, int(stale.get("error_count") or 0)),
                    "last_phase": str(stale.get("last_phase") or "unknown")[:120],
                    "phases": dict(stale.get("phases") or {}) if isinstance(stale.get("phases"), Mapping) else {},
                    "external_commands": dict(stale.get("external_commands") or {}) if isinstance(stale.get("external_commands"), Mapping) else {},
                    "ollama": dict(ollama),
                    "classifier": dict(stale.get("classifier") or {}) if isinstance(stale.get("classifier"), Mapping) else {},
                }
                if not interrupted["ollama"]:
                    interrupted["ollama"] = {"attempts": [], "summary": self._empty_ollama_summary()}
                self._append_record(interrupted)
                try:
                    current = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    current = {}
                if isinstance(current, Mapping) and str(current.get("run_id") or "") == run_id:
                    path.unlink(missing_ok=True)
                return False
        except OSError as exc:
            self.log.debug("Performance-Telemetrie konnte Inflight nicht sperren: %s", exc)
            return False

    def _run_id_already_recorded(self, run_id: str) -> bool:
        if not run_id or not self.path.is_file():
            return False
        try:
            lines = self.path.read_text(encoding="utf-8", errors="replace").splitlines()[-2000:]
        except OSError:
            return False
        for line in reversed(lines):
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(record, Mapping) and str(record.get("run_id") or "") == run_id:
                return True
        return False

    @staticmethod
    def _empty_ollama_summary() -> dict[str, object]:
        return {
            "attempt_count": 0,
            "successful_attempts": 0,
            "running_attempts": 0,
            "timeouts": 0,
            "client_duration_ms": 0.0,
            "server_total_duration_ms": 0.0,
            "load_duration_ms": 0.0,
            "prompt_eval_count": 0,
            "prompt_eval_duration_ms": 0.0,
            "eval_count": 0,
            "eval_duration_ms": 0.0,
            "client_overhead_ms": 0.0,
            "queue_wait_ms": 0.0,
            "queue_wait_max_ms": 0.0,
        }

    def _remove_inflight(self) -> None:
        try:
            path = self.inflight_path
            if not path.is_file():
                return
            current = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(current, Mapping) and current.get("run_id") == self.run_id:
                path.unlink(missing_ok=True)
        except (OSError, json.JSONDecodeError):
            return

    def _ollama_summary(self) -> dict[str, object]:
        attempts = self._ollama_attempts
        return {
            "attempt_count": len(attempts),
            "successful_attempts": sum(1 for item in attempts if item.get("ok")),
            "running_attempts": sum(1 for item in attempts if item.get("state") == "running"),
            "timeouts": sum(1 for item in attempts if item.get("timeout")),
            "client_duration_ms": _round_ms(
                sum(float(item.get("client_duration_ms") or 0.0) for item in attempts)
            ),
            "server_total_duration_ms": _round_ms(
                sum(float(item.get("total_duration_ms") or 0.0) for item in attempts)
            ),
            "load_duration_ms": _round_ms(
                sum(float(item.get("load_duration_ms") or 0.0) for item in attempts)
            ),
            "prompt_eval_count": sum(int(item.get("prompt_eval_count") or 0) for item in attempts),
            "prompt_eval_duration_ms": _round_ms(
                sum(float(item.get("prompt_eval_duration_ms") or 0.0) for item in attempts)
            ),
            "eval_count": sum(int(item.get("eval_count") or 0) for item in attempts),
            "eval_duration_ms": _round_ms(
                sum(float(item.get("eval_duration_ms") or 0.0) for item in attempts)
            ),
            "client_overhead_ms": _round_ms(
                sum(float(item.get("client_overhead_ms") or 0.0) for item in attempts)
            ),
            "queue_wait_ms": _round_ms(
                sum(float(item.get("queue_wait_ms") or 0.0) for item in attempts)
            ),
            "queue_wait_max_ms": _round_ms(
                max((float(item.get("queue_wait_ms") or 0.0) for item in attempts), default=0.0)
            ),
        }

    def _write_json_atomic(self, path: Path, payload: Mapping[str, object]) -> None:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
            data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            try:
                os.write(fd, data)
            finally:
                os.close(fd)
            os.replace(temporary, path)
        except Exception as exc:
            if not self._write_error_reported:
                self.log.warning("Performance-Telemetrie konnte Checkpoint nicht schreiben: %s", exc)
                self._write_error_reported = True

    def _append_record(self, record: Mapping[str, object]) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._rotate_if_needed()
            line = json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
            fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            try:
                os.write(fd, line.encode("utf-8"))
            finally:
                os.close(fd)
        except Exception as exc:  # Telemetry must never stop productive processing.
            if not self._write_error_reported:
                self.log.warning("Performance-Telemetrie konnte nicht geschrieben werden: %s", exc)
                self._write_error_reported = True

    def _rotate_if_needed(self) -> None:
        try:
            if not self.path.exists() or self.path.stat().st_size < _MAX_FILE_BYTES:
                return
            rotated = self.path.with_suffix(self.path.suffix + ".1")
            rotated.unlink(missing_ok=True)
            self.path.replace(rotated)
        except OSError as exc:
            self.log.debug("Performance-Telemetrie konnte nicht rotiert werden: %s", exc)


def command_category(args: Sequence[str]) -> str:
    """Return a privacy-safe command category without retaining arguments."""

    if not args:
        return "external.unknown"
    binary = Path(str(args[0])).name.casefold()
    tokens = [str(item).casefold() for item in args[1:]]
    if "himalaya" in binary:
        for noun in ("folder", "envelope", "message", "template"):
            if noun in tokens:
                index = tokens.index(noun)
                verb = tokens[index + 1] if index + 1 < len(tokens) else "unknown"
                if verb in {"list", "export", "move", "send", "write", "read", "delete", "add"}:
                    return f"himalaya.{noun}.{verb}"
                return f"himalaya.{noun}.other"
        return "himalaya.other"
    if binary in {"clamdscan", "clamscan"}:
        return "antivirus.command"
    if binary in {"node", "nodejs"}:
        return "node.command"
    if binary == "openclaw":
        return "openclaw.command"
    if binary == "systemctl":
        return "systemd.command"
    return "external." + (binary or "unknown")[:80]


def read_recent_performance(path: Path, limit: int = 20) -> list[dict[str, Any]]:
    """Read newest valid final records, de-duplicated by run_id."""

    limit = max(1, min(int(limit), 500))
    if not path.is_file():
        return []
    records: list[dict[str, Any]] = []
    seen_run_ids: set[str] = set()
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    for line in reversed(lines):
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(data, dict):
            continue
        run_id = str(data.get("run_id") or "")
        if run_id and run_id in seen_run_ids:
            continue
        if run_id:
            seen_run_ids.add(run_id)
        records.append(data)
        if len(records) >= limit:
            break
    records.reverse()
    return records


def summarize_performance(records: Sequence[Mapping[str, object]]) -> dict[str, object]:
    """Build a compact cross-run report from privacy-safe telemetry records."""

    deduplicated: list[Mapping[str, object]] = []
    seen: set[str] = set()
    for record in reversed(list(records)):
        run_id = str(record.get("run_id") or "")
        if run_id and run_id in seen:
            continue
        if run_id:
            seen.add(run_id)
        deduplicated.append(record)
    records = list(reversed(deduplicated))
    if not records:
        return {
            "ok": True,
            "runs": 0,
            "detail": "Noch keine Performance-Messungen vorhanden",
        }
    phase_totals: dict[str, _Aggregate] = defaultdict(_Aggregate)
    command_totals: dict[str, _Aggregate] = defaultdict(_Aggregate)
    operation_counts: dict[str, int] = defaultdict(int)
    total_ms = 0.0
    processed = 0
    errors = 0
    interrupted_runs = 0
    ollama_attempts = 0
    ollama_client_ms = 0.0
    ollama_server_ms = 0.0
    ollama_queue_wait_ms = 0.0
    ollama_queue_wait_max_ms = 0.0
    prompt_tokens = 0
    eval_tokens = 0

    for record in records:
        operation_counts[str(record.get("operation") or "unknown")] += 1
        total_ms += float(record.get("total_ms") or 0.0)
        processed += int(record.get("processed") or 0)
        errors += int(record.get("error_count") or 0)
        if record.get("outcome") == "interrupted":
            interrupted_runs += 1
        phases = record.get("phases")
        if isinstance(phases, Mapping):
            for name, raw in phases.items():
                if not isinstance(raw, Mapping):
                    continue
                target = phase_totals[str(name)]
                target.count += int(raw.get("count") or 0)
                target.total_ms += float(raw.get("total_ms") or 0.0)
                target.max_ms = max(target.max_ms, float(raw.get("max_ms") or 0.0))
                target.failures += int(raw.get("failures") or 0)
        commands = record.get("external_commands")
        if isinstance(commands, Mapping):
            for name, raw in commands.items():
                if not isinstance(raw, Mapping):
                    continue
                target = command_totals[str(name)]
                target.count += int(raw.get("count") or 0)
                target.total_ms += float(raw.get("total_ms") or 0.0)
                target.max_ms = max(target.max_ms, float(raw.get("max_ms") or 0.0))
                target.failures += int(raw.get("failures") or 0)
                target.timeouts += int(raw.get("timeouts") or 0)
        ollama = record.get("ollama")
        summary = ollama.get("summary") if isinstance(ollama, Mapping) else None
        if isinstance(summary, Mapping):
            ollama_attempts += int(summary.get("attempt_count") or 0)
            ollama_client_ms += float(summary.get("client_duration_ms") or 0.0)
            ollama_server_ms += float(summary.get("server_total_duration_ms") or 0.0)
            ollama_queue_wait_ms += float(summary.get("queue_wait_ms") or 0.0)
            ollama_queue_wait_max_ms = max(
                ollama_queue_wait_max_ms, float(summary.get("queue_wait_max_ms") or 0.0)
            )
            prompt_tokens += int(summary.get("prompt_eval_count") or 0)
            eval_tokens += int(summary.get("eval_count") or 0)

    phase_ranking = sorted(
        (
            {
                "name": name,
                "count": aggregate.count,
                "total_ms": _round_ms(aggregate.total_ms),
                "average_ms": _round_ms(aggregate.total_ms / max(1, aggregate.count)),
                "max_ms": _round_ms(aggregate.max_ms),
                "failures": aggregate.failures,
            }
            for name, aggregate in phase_totals.items()
        ),
        key=lambda item: float(item["total_ms"]),
        reverse=True,
    )
    command_ranking = sorted(
        (
            {
                "name": name,
                "count": aggregate.count,
                "total_ms": _round_ms(aggregate.total_ms),
                "average_ms": _round_ms(aggregate.total_ms / max(1, aggregate.count)),
                "max_ms": _round_ms(aggregate.max_ms),
                "failures": aggregate.failures,
                "timeouts": aggregate.timeouts,
            }
            for name, aggregate in command_totals.items()
        ),
        key=lambda item: float(item["total_ms"]),
        reverse=True,
    )
    run_count = len(records)
    return {
        "ok": True,
        "runs": run_count,
        "operations": dict(sorted(operation_counts.items())),
        "total_runtime_ms": _round_ms(total_ms),
        "average_runtime_ms": _round_ms(total_ms / max(1, run_count)),
        "processed": processed,
        "errors": errors,
        "interrupted_runs": interrupted_runs,
        "average_ms_per_processed_message": (
            _round_ms(total_ms / processed) if processed > 0 else None
        ),
        "ollama": {
            "attempts": ollama_attempts,
            "client_duration_ms": _round_ms(ollama_client_ms),
            "server_duration_ms": _round_ms(ollama_server_ms),
            "client_overhead_ms": _round_ms(max(0.0, ollama_client_ms - ollama_server_ms)),
            "queue_wait_ms": _round_ms(ollama_queue_wait_ms),
            "queue_wait_max_ms": _round_ms(ollama_queue_wait_max_ms),
            "prompt_eval_count": prompt_tokens,
            "eval_count": eval_tokens,
        },
        "slowest_phases": phase_ranking[:20],
        "slowest_external_commands": command_ranking[:20],
        "first_started_at": str(records[0].get("started_at") or ""),
        "last_finished_at": str(records[-1].get("finished_at") or ""),
    }
