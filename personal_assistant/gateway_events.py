"""Bounded one-way event delivery from workers to the local OpenClaw gateway."""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import signal
import stat
import subprocess
import sys
import time
import uuid
from collections.abc import Callable, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

EVENT_SCHEMA = 1
MAX_EVENT_TEXT = 1800
MAX_EVENT_BYTES = 8192
MAX_PENDING_EVENTS = 256
MAX_FAILED_EVENTS = 64
MAX_DELIVERY_ATTEMPTS = 10
RELAY_STATUS_MAX_AGE = timedelta(seconds=60)


def _now() -> datetime:
    return datetime.now(UTC)


def _iso(value: datetime | None = None) -> str:
    return (value or _now()).astimezone(UTC).isoformat(timespec="seconds")


def _queue_root() -> Path | None:
    raw = os.environ.get("OPENCLAW_EVENT_QUEUE_DIR", "").strip()
    return Path(raw).expanduser().resolve() if raw else None


def event_command(text: str) -> list[str]:
    """Build the registered local delivery command without exposing credentials."""
    value = str(text)[:MAX_EVENT_TEXT]
    if _queue_root() is not None:
        return [
            "python3",
            "-P",
            "-m",
            "personal_assistant.gateway_events",
            "enqueue",
            "--text",
            value,
        ]
    command = ["openclaw", "system", "event", "--text", value, "--mode", "now"]
    gateway_environment_url = os.environ.get("OPENCLAW_GATEWAY_URL", "").strip()
    gateway_url = os.environ.get("OPENCLAW_GATEWAY_WS_URL", "").strip()
    if gateway_url and not gateway_environment_url:
        command.extend(["--url", gateway_url])
    return command


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    data = json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n"
    if len(data.encode("utf-8")) > MAX_EVENT_BYTES:
        raise ValueError("Gateway-Ereignis ueberschreitet das Groessenlimit")
    with temporary.open("x", encoding="utf-8") as handle:
        os.chmod(temporary, 0o600)
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(path)


def _pending_files(root: Path) -> list[Path]:
    directory = root / "pending"
    try:
        values = list(directory.iterdir())
    except FileNotFoundError:
        return []
    return sorted(path for path in values if path.name.endswith(".json"))


def recover_processing(root: Path) -> int:
    """Return events left claimed by a terminated relay to the pending queue."""
    pending = root / "pending"
    processing = root / "processing"
    pending.mkdir(mode=0o700, parents=True, exist_ok=True)
    processing.mkdir(mode=0o700, parents=True, exist_ok=True)
    recovered = 0
    for source in sorted(processing.glob("*.json")):
        destination = pending / source.name
        if destination.exists():
            # The pending copy is authoritative; retaining two entries would
            # make the same event visible twice after a relay crash.
            source.unlink(missing_ok=True)
            continue
        source.replace(destination)
        recovered += 1
    return recovered


def enqueue_event(text: str, *, source: str | None = None, root: Path | None = None) -> dict[str, Any]:
    queue_root = (root or _queue_root())
    if queue_root is None:
        raise ValueError("OPENCLAW_EVENT_QUEUE_DIR ist nicht konfiguriert")
    value = str(text).strip()
    if not value:
        raise ValueError("Gateway-Ereignis darf nicht leer sein")
    if len(value) > MAX_EVENT_TEXT:
        raise ValueError(f"Gateway-Ereignis darf hoechstens {MAX_EVENT_TEXT} Zeichen enthalten")
    pending = queue_root / "pending"
    pending.mkdir(mode=0o700, parents=True, exist_ok=True)
    lock_path = queue_root / "enqueue.lock"
    with lock_path.open("a+", encoding="utf-8") as lock:
        os.chmod(lock_path, 0o600)
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        processing = queue_root / "processing"
        active = len(_pending_files(queue_root))
        active += len(list(processing.glob("*.json"))) if processing.is_dir() else 0
        if active >= MAX_PENDING_EVENTS:
            raise RuntimeError("Gateway-Ereigniswarteschlange ist voll")
        event_id = uuid.uuid4().hex
        payload = {
            "schema": EVENT_SCHEMA,
            "id": event_id,
            "created_at": _iso(),
            "source": str(source or os.environ.get("OPENCLAW_ROLE") or "unknown")[:80],
            "text": value,
            "attempts": 0,
        }
        _atomic_json(pending / f"{event_id}.json", payload)
    return {"ok": True, "queued": True, "event_id": event_id}


def _read_event(path: Path) -> dict[str, Any]:
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_EVENT_BYTES:
        raise ValueError("Ereigniseintrag ist nicht regulaer oder zu gross")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("schema") != EVENT_SCHEMA:
        raise ValueError("Ereigniseintrag hat ein unbekanntes Schema")
    event_id = str(payload.get("id") or "")
    text = str(payload.get("text") or "")
    if not event_id or path.name != f"{event_id}.json":
        raise ValueError("Ereignis-ID stimmt nicht mit dem Dateinamen ueberein")
    if not text or len(text) > MAX_EVENT_TEXT:
        raise ValueError("Ereignistext ist leer oder zu lang")
    attempts = payload.get("attempts")
    if not isinstance(attempts, int) or attempts < 0:
        raise ValueError("Ereignisversuch ist ungueltig")
    return payload


RelayRunner = Callable[[Sequence[str], dict[str, str]], int]


def _move_failed(source: Path, failed: Path) -> None:
    failed.mkdir(mode=0o700, parents=True, exist_ok=True)
    existing = sorted(failed.glob("*.json"), key=lambda path: (path.stat().st_mtime_ns, path.name))
    for expired in existing[: max(0, len(existing) - MAX_FAILED_EVENTS + 1)]:
        expired.unlink(missing_ok=True)
    source.replace(failed / source.name)


def _default_relay_runner(command: Sequence[str], environment: dict[str, str]) -> int:
    try:
        result = subprocess.run(
            list(command),
            env=environment,
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return 124
    return int(result.returncode)


def _relay_environment() -> dict[str, str]:
    environment = os.environ.copy()
    port = environment.get("OPENCLAW_GATEWAY_PORT", "18789")
    environment["OPENCLAW_GATEWAY_URL"] = f"ws://127.0.0.1:{port}"
    environment.pop("OPENCLAW_GATEWAY_WS_URL", None)
    environment.pop("OPENCLAW_ALLOW_INSECURE_PRIVATE_WS", None)
    return environment


def _write_relay_status(
    root: Path,
    *,
    state: str,
    last_delivery_at: str = "",
    last_error_code: int | None = None,
) -> dict[str, Any]:
    failed = root / "failed"
    failed_count = len(list(failed.glob("*.json"))) if failed.is_dir() else 0
    retrying = 0
    for path in _pending_files(root):
        try:
            if int(_read_event(path).get("attempts") or 0) > 0:
                retrying += 1
        except (OSError, ValueError, json.JSONDecodeError):
            retrying += 1
    effective_state = "degraded" if state == "degraded" or failed_count or retrying else state
    payload = {
        "schema": EVENT_SCHEMA,
        "ok": effective_state == "running",
        "state": effective_state,
        "updated_at": _iso(),
        "last_delivery_at": last_delivery_at,
        "last_error_code": last_error_code,
        "pending": len(_pending_files(root)),
        "retrying": retrying,
        "failed": failed_count,
    }
    _atomic_json(root / "relay-status.json", payload)
    return payload


def relay_once(root: Path, *, runner: RelayRunner = _default_relay_runner) -> dict[str, Any]:
    root = root.expanduser().resolve()
    pending = root / "pending"
    processing = root / "processing"
    failed = root / "failed"
    for directory in (pending, processing, failed):
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    delivered = 0
    delivery_errors = 0
    invalid = 0
    last_delivery_at = ""
    last_error_code: int | None = None
    for source in _pending_files(root):
        claimed = processing / source.name
        try:
            source.replace(claimed)
        except FileNotFoundError:
            continue
        try:
            payload = _read_event(claimed)
        except (OSError, ValueError, json.JSONDecodeError):
            invalid += 1
            _move_failed(claimed, failed)
            continue
        last_attempt = str(payload.get("last_attempt_at") or "")
        if payload["attempts"] and last_attempt:
            try:
                attempted = datetime.fromisoformat(last_attempt.replace("Z", "+00:00"))
                if attempted.tzinfo is None:
                    attempted = attempted.replace(tzinfo=UTC)
                retry_delay = min(300, 2 ** min(int(payload["attempts"]), 8))
                if (_now() - attempted.astimezone(UTC)).total_seconds() < retry_delay:
                    claimed.replace(pending / claimed.name)
                    continue
            except ValueError:
                _move_failed(claimed, failed)
                invalid += 1
                continue
        command = [
            "openclaw",
            "system",
            "event",
            "--text",
            str(payload["text"]),
            "--mode",
            "now",
        ]
        returncode = runner(command, _relay_environment())
        if returncode == 0:
            claimed.unlink()
            delivered += 1
            last_delivery_at = _iso()
            continue
        delivery_errors += 1
        last_error_code = returncode
        payload["attempts"] = int(payload["attempts"]) + 1
        payload["last_attempt_at"] = _iso()
        if payload["attempts"] >= MAX_DELIVERY_ATTEMPTS:
            _atomic_json(claimed, payload)
            _move_failed(claimed, failed)
        else:
            _atomic_json(pending / claimed.name, payload)
            claimed.unlink()
        break
    state = "degraded" if invalid or delivery_errors else "running"
    status = _write_relay_status(
        root,
        state=state,
        last_delivery_at=last_delivery_at,
        last_error_code=last_error_code,
    )
    return {
        "ok": not invalid and not delivery_errors,
        "delivered": delivered,
        "delivery_errors": delivery_errors,
        "invalid": invalid,
        "status": status,
    }


def relay_status(
    root: Path | None = None,
    *,
    maximum_age: timedelta = RELAY_STATUS_MAX_AGE,
) -> dict[str, Any]:
    queue_root = (root or _queue_root())
    if queue_root is None:
        return {"ok": False, "detail": "Gateway-Ereigniswarteschlange ist nicht konfiguriert"}
    path = queue_root / "relay-status.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        updated = datetime.fromisoformat(str(payload.get("updated_at") or "").replace("Z", "+00:00"))
        if updated.tzinfo is None:
            updated = updated.replace(tzinfo=UTC)
    except (OSError, ValueError, json.JSONDecodeError, AttributeError) as exc:
        return {"ok": False, "path": str(path), "detail": f"Relay-Status ist nicht lesbar: {exc}"}
    age = max(0.0, (_now() - updated.astimezone(UTC)).total_seconds())
    ok = bool(payload.get("ok") and payload.get("state") == "running" and age <= maximum_age.total_seconds())
    return {
        "ok": ok,
        "path": str(path),
        "state": str(payload.get("state") or "unknown"),
        "age_seconds": round(age, 3),
        "pending": int(payload.get("pending") or 0),
        "retrying": int(payload.get("retrying") or 0),
        "failed": int(payload.get("failed") or 0),
        "last_delivery_at": str(payload.get("last_delivery_at") or ""),
        "last_error_code": payload.get("last_error_code"),
        "detail": (
            "Gateway-Ereignisrelay ist aktuell"
            if ok
            else "Gateway-Ereignisrelay ist nicht zustellbereit"
        ),
    }


def serve_gateway(command: Sequence[str], *, root: Path) -> int:
    if not command:
        raise ValueError("Gateway-Kommando fehlt")
    stop = False

    def handle_stop(_signum: int, _frame: object) -> None:
        nonlocal stop
        stop = True

    signal.signal(signal.SIGTERM, handle_stop)
    signal.signal(signal.SIGINT, handle_stop)
    recover_processing(root)
    process = subprocess.Popen(list(command), env=os.environ.copy())
    _write_relay_status(root, state="running")
    last_relay = 0.0
    while process.poll() is None and not stop:
        current = time.monotonic()
        if current - last_relay >= 2.0:
            relay_once(root)
            last_relay = current
        time.sleep(0.25)
    if stop and process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=90)
        except subprocess.TimeoutExpired:
            process.kill()
    returncode = int(process.wait())
    _write_relay_status(root, state="stopped", last_error_code=returncode if returncode else None)
    return returncode


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Lokale, begrenzte OpenClaw-Gateway-Ereignisqueue")
    subparsers = parser.add_subparsers(dest="command", required=True)
    enqueue = subparsers.add_parser("enqueue")
    enqueue.add_argument("--text", required=True)
    subparsers.add_parser("status")
    serve = subparsers.add_parser("serve")
    serve.add_argument("gateway_command", nargs=argparse.REMAINDER)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = _queue_root()
    if root is None:
        print("OPENCLAW_EVENT_QUEUE_DIR ist nicht konfiguriert", file=sys.stderr)
        return 2
    if args.command == "enqueue":
        try:
            result = enqueue_event(args.text, root=root)
        except (OSError, RuntimeError, ValueError) as exc:
            print(str(exc), file=sys.stderr)
            return 1
        print(json.dumps(result, ensure_ascii=False))
        return 0
    if args.command == "status":
        result = relay_status(root)
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result["ok"] else 1
    command = list(args.gateway_command)
    if command[:1] == ["--"]:
        command = command[1:]
    try:
        return serve_gateway(command, root=root)
    except (OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 127


if __name__ == "__main__":
    raise SystemExit(main())
