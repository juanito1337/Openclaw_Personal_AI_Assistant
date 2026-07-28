#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

STOP = False


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.chmod(temporary, 0o600)
    temporary.replace(path)


def desired(state_path: Path, job: str, default: bool) -> bool:
    try:
        payload = json.loads(state_path.read_text(encoding="utf-8"))
        values = payload.get("desired") if isinstance(payload, dict) else {}
        return bool(values.get(job, default)) if isinstance(values, dict) else default
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return default


def config(job: str, workspace: Path) -> tuple[list[str], int, int, bool, dict[str, str]]:
    if job == "mail":
        return (
            [
                str(workspace / "scripts/mail-agent.sh"), "run", "--drain",
                "--batch-size", os.environ.get("MAIL_DRAIN_BATCH_SIZE", "20"),
                "--max-messages", os.environ.get("MAIL_MAX_MESSAGES", "500"),
                "--max-runtime", os.environ.get("MAIL_MAX_RUNTIME", "2400"),
                "--shutdown-reserve", os.environ.get("MAIL_SHUTDOWN_RESERVE", "180"),
                "--max-batches", os.environ.get("MAIL_MAX_BATCHES", "100"),
                "--no-digest",
            ],
            int(os.environ.get("MAIL_INTERVAL_SECONDS", "1200")),
            int(os.environ.get("MAIL_INITIAL_DELAY_SECONDS", "120")),
            True,
            {"OPENCLAW_OLLAMA_PRIORITY": "background", "OPENCLAW_OLLAMA_SOURCE": "mail-container-worker"},
        )
    if job == "sync":
        return (
            [str(workspace / "scripts/assistant.sh"), "index", "all"],
            int(os.environ.get("SYNC_INTERVAL_SECONDS", "900")),
            int(os.environ.get("SYNC_INITIAL_DELAY_SECONDS", "300")),
            False,
            {},
        )
    if job == "supervisor":
        return (
            [str(workspace / "scripts/assistant.sh"), "jobs", "check", "--target", "all"],
            int(os.environ.get("SUPERVISOR_INTERVAL_SECONDS", "300")),
            int(os.environ.get("SUPERVISOR_INITIAL_DELAY_SECONDS", "180")),
            True,
            {},
        )
    raise ValueError(job)


def handler(signum: int, frame: object) -> None:
    del signum, frame
    global STOP
    STOP = True


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("job", choices=("mail", "sync", "supervisor"))
    args = parser.parse_args()

    signal.signal(signal.SIGTERM, handler)
    signal.signal(signal.SIGINT, handler)

    workspace = Path(os.environ.get("OPENCLAW_WORKSPACE", "/home/node/.openclaw/workspace")).resolve()
    status_dir = Path(os.environ.get("OPENCLAW_JOB_STATUS_DIR", workspace / "personal_assistant/data/container_jobs")).resolve()
    log_dir = Path(os.environ.get("OPENCLAW_LOG_DIR", workspace / "personal_assistant/data/container_logs")).resolve()
    state_path = workspace / "personal_assistant/data/job_control.json"
    heartbeat = status_dir / f"{args.job}.json"
    wake = status_dir / f"{args.job}.wake"
    log_path = log_dir / f"{args.job}.log"
    status_dir.mkdir(parents=True, exist_ok=True)
    log_dir.mkdir(parents=True, exist_ok=True)

    command, interval, initial_delay, default_on, extra_env = config(args.job, workspace)
    next_run = time.monotonic() + max(0, initial_delay)
    status: dict[str, Any] = {
        "job": args.job,
        "state": "starting",
        "updated_at": now(),
        "result": "success",
        "last_exit_code": 0,
    }
    atomic_json(heartbeat, status)

    while not STOP:
        is_desired = desired(state_path, args.job, default_on)
        if not is_desired:
            status.update(state="disabled", updated_at=now(), result="success")
            atomic_json(heartbeat, status)
            time.sleep(10)
            next_run = time.monotonic() + interval
            continue

        if wake.exists():
            wake.unlink(missing_ok=True)
            next_run = time.monotonic()

        if time.monotonic() < next_run:
            status.update(state="waiting", updated_at=now(), next_run_in_seconds=max(0, int(next_run - time.monotonic())))
            atomic_json(heartbeat, status)
            time.sleep(min(15, max(1, next_run - time.monotonic())))
            continue

        started = now()
        status.update(state="running", updated_at=started, last_started_at=started, command=command)
        atomic_json(heartbeat, status)
        env = os.environ.copy()
        env.update(extra_env)
        with log_path.open("ab", buffering=0) as log:
            header = f"\n[{started}] START {' '.join(command)}\n".encode()
            log.write(header)
            process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT, env=env, cwd=workspace)
            while process.poll() is None and not STOP:
                status.update(state="running", updated_at=now(), pid=process.pid)
                atomic_json(heartbeat, status)
                time.sleep(10)
            if STOP and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=120)
                except subprocess.TimeoutExpired:
                    process.kill()
            code = int(process.wait())
            finished = now()
            log.write(f"[{finished}] END exit={code}\n".encode())

        status.update(
            state="waiting" if not STOP else "stopping",
            updated_at=now(),
            last_finished_at=finished,
            last_exit_code=code,
            result="success" if code in {0, 1} else "failed",
            pid=None,
        )
        atomic_json(heartbeat, status)
        next_run = time.monotonic() + interval

    status.update(state="stopped", updated_at=now())
    atomic_json(heartbeat, status)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
