"""Single-writer mail cycle with the one allowlisted production-gate recovery."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
import uuid
from collections.abc import Sequence
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from .mail_recovery import (
    DRY_RUN_TIMEOUT,
    FINGERPRINT_UNVERIFIED,
    READINESS_TIMEOUT,
    RECHECK_TIMEOUT,
    RECOVERY_RUNTIME_ARGS,
    RECOVERY_WATCHDOG_SECONDS,
    finish_recovery_capture,
    recovery_fingerprint,
    start_recovery_capture,
)

RECOVERY_COOLDOWN = timedelta(minutes=30)
CHILD: subprocess.Popen[Any] | None = None
CAPTURE_GROUP: int | None = None


def _now() -> datetime:
    return datetime.now(UTC)


def _iso() -> str:
    return _now().isoformat(timespec="seconds")


def _state_path() -> Path:
    coordination = Path(
        os.environ.get("OPENCLAW_COORDINATION_DATA_DIR", "/var/lib/openclaw/coordination")
    ).expanduser().resolve()
    return coordination / "mail_worker_recovery.json"


def _load_state(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _write_state(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    with temporary.open("x", encoding="utf-8") as handle:
        os.chmod(temporary, 0o600)
        json.dump(payload, handle, ensure_ascii=False, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(path)


def _payload(stdout: str) -> dict[str, Any] | None:
    try:
        value = json.loads(stdout)
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def _in_cooldown(state: dict[str, Any], signature: str) -> bool:
    if state.get("signature") != signature or state.get("ok"):
        return False
    try:
        attempted = datetime.fromisoformat(str(state.get("attempted_at") or "").replace("Z", "+00:00"))
    except ValueError:
        return False
    if attempted.tzinfo is None:
        attempted = attempted.replace(tzinfo=UTC)
    return _now() - attempted.astimezone(UTC) < RECOVERY_COOLDOWN


def _run_capture(
    command: Sequence[str], *, environment: dict[str, str], timeout: int = 300,
) -> subprocess.CompletedProcess[str]:
    global CHILD, CAPTURE_GROUP
    process = start_recovery_capture(command, environment=environment)
    CHILD = process
    CAPTURE_GROUP = process.pid
    try:
        result = finish_recovery_capture(process, timeout)
    finally:
        CAPTURE_GROUP = None
        CHILD = None
    if result.stdout:
        print(result.stdout, end="" if result.stdout.endswith("\n") else "\n")
    if result.stderr:
        print(result.stderr, end="" if result.stderr.endswith("\n") else "\n", file=sys.stderr)
    return result


def _report_timeout(code: str) -> None:
    print(f"Mail-Produktionsfreigabe ueberschritt das Zeitlimit ({code})", file=sys.stderr)


def _run_productive(command: Sequence[str], *, environment: dict[str, str]) -> int:
    global CHILD
    CHILD = subprocess.Popen(list(command), env=environment)
    returncode = int(CHILD.wait())
    CHILD = None
    return returncode


def run_cycle(
    productive_command: Sequence[str],
    *,
    mail_agent: str,
    environment: dict[str, str] | None = None,
    state_path: Path | None = None,
) -> int:
    """Run preflight, bounded recovery when allowed, then the productive command."""
    env = dict(environment or os.environ)
    path = state_path or _state_path()
    check_command = [mail_agent, "production-check"]
    check = _run_capture(check_command, environment=env)
    if check.returncode == 124:
        _report_timeout(READINESS_TIMEOUT)
        return 124
    check_payload = _payload(check.stdout)
    if check.returncode == 0 and check_payload is not None and check_payload.get("ok"):
        return _run_productive(productive_command, environment=env)
    if (
        check.returncode != 4
        or not isinstance(check_payload, dict)
        or not check_payload.get("auto_recoverable")
    ):
        return int(check.returncode or 4)

    signature = recovery_fingerprint(check_payload.get("gate"))
    if signature is None:
        print(f"Mail-Produktionsfreigabe bleibt blockiert ({FINGERPRINT_UNVERIFIED})", file=sys.stderr)
        return 4
    state = _load_state(path)
    if _in_cooldown(state, signature):
        print(
            "Mail-Produktionsfreigabe bleibt blockiert; derselbe fehlgeschlagene Dry-Run "
            "liegt innerhalb des 30-Minuten-Cooldowns.",
            file=sys.stderr,
        )
        return 4

    recovery_environment = env.copy()
    recovery_environment.update(
        {
            "OPENCLAW_OLLAMA_PRIORITY": "maintenance",
            "OPENCLAW_OLLAMA_SOURCE": "mail-worker-recovery",
        }
    )
    dry_run: subprocess.CompletedProcess[str] | None = None
    for attempt in range(1, 4):
        dry_run = _run_capture(
            [mail_agent, "run", "--dry-run", "--no-digest", "--limit", "5", *RECOVERY_RUNTIME_ARGS],
            environment=recovery_environment,
            timeout=RECOVERY_WATCHDOG_SECONDS,
        )
        if dry_run.returncode != 3 or attempt == 3:
            break
        time.sleep(2)
    assert dry_run is not None
    dry_payload = _payload(dry_run.stdout)
    errors = list(dry_payload.get("errors") or []) if isinstance(dry_payload, dict) else ["invalid-json"]
    actions = list(dry_payload.get("actions") or []) if isinstance(dry_payload, dict) else []
    actions_ok = all(bool(item.get("ok")) for item in actions if isinstance(item, dict))
    dry_ok = dry_run.returncode == 0 and isinstance(dry_payload, dict) and not errors and actions_ok
    if not dry_ok:
        reason = "dry-run-failed"
        if dry_run.returncode == 124:
            reason = DRY_RUN_TIMEOUT
            _report_timeout(reason)
        # A process-lock collision is transient and must not create a cooldown.
        if dry_run.returncode != 3:
            _write_state(
                path,
                {
                    "schema": 1,
                    "signature": signature,
                    "attempted_at": _iso(),
                    "ok": False,
                    "reason": reason,
                    "returncode": dry_run.returncode,
                },
            )
        return int(dry_run.returncode or 1)

    after = _run_capture(check_command, environment=env)
    after_payload = _payload(after.stdout)
    ok = after.returncode == 0 and isinstance(after_payload, dict) and bool(after_payload.get("ok"))
    reason = "production-gate-verified" if ok else "production-gate-still-blocked"
    if after.returncode == 124:
        reason = RECHECK_TIMEOUT
        _report_timeout(reason)
    _write_state(
        path,
        {
            "schema": 1,
            "signature": signature,
            "attempted_at": _iso(),
            "ok": ok,
            "reason": reason,
            "returncode": after.returncode,
        },
    )
    if not ok:
        return int(after.returncode or 4)
    return _run_productive(productive_command, environment=env)


def _forward_signal(signum: int, _frame: object) -> None:
    if CAPTURE_GROUP is not None:
        with suppress(ProcessLookupError):
            os.killpg(CAPTURE_GROUP, signum)
    elif CHILD is not None and CHILD.poll() is None:
        CHILD.send_signal(signum)


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments[:1] == ["--"]:
        arguments = arguments[1:]
    if not arguments:
        print("Produktives Mail-Kommando fehlt", file=sys.stderr)
        return 2
    signal.signal(signal.SIGTERM, _forward_signal)
    signal.signal(signal.SIGINT, _forward_signal)
    image_root = Path(os.environ.get("OPENCLAW_IMAGE_ROOT", "/opt/openclaw-agent")).resolve()
    mail_agent = str(image_root / "scripts/mail-agent.sh")
    try:
        return run_cycle(arguments, mail_agent=mail_agent)
    except OSError as exc:
        print(str(exc), file=sys.stderr)
        return 127


if __name__ == "__main__":
    raise SystemExit(main())
