#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import signal
import socket
import sqlite3
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from personal_assistant.work_scheduler import AdaptiveWorkScheduler

STOP = False


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


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


def config(
    job: str,
    workspace: Path,
    image_root: Path,
) -> tuple[list[str], int, int, bool, dict[str, str]]:
    assistant = str(image_root / "scripts/assistant.sh")
    mail_agent = str(image_root / "scripts/mail-agent.sh")
    if job == "mail":
        productive = [
            mail_agent, "run", "--drain",
            "--batch-size", os.environ.get("MAIL_DRAIN_BATCH_SIZE", "20"),
            "--max-messages", os.environ.get("MAIL_MAX_MESSAGES", "500"),
            "--max-runtime", os.environ.get("MAIL_MAX_RUNTIME", "2400"),
            "--shutdown-reserve", os.environ.get("MAIL_SHUTDOWN_RESERVE", "180"),
            "--max-batches", os.environ.get("MAIL_MAX_BATCHES", "100"),
            "--no-digest",
        ]
        return (
            [
                "python3", "-P", "-m", "personal_assistant.mail_worker", "--", *productive,
            ],
            int(os.environ.get("MAIL_INTERVAL_SECONDS", "1200")),
            int(os.environ.get("MAIL_INITIAL_DELAY_SECONDS", "120")),
            True,
            {
                "OPENCLAW_ROLE": "mail-worker",
                "OPENCLAW_OLLAMA_PRIORITY": "background",
                "OPENCLAW_OLLAMA_SOURCE": "mail-container-worker",
                "OPENCLAW_SCHEDULER_SOURCE": "background-worker",
            },
        )
    if job == "sync":
        return (
            [assistant, "index", "all"],
            int(os.environ.get("SYNC_INTERVAL_SECONDS", "900")),
            int(os.environ.get("SYNC_INITIAL_DELAY_SECONDS", "300")),
            False,
            {
                "OPENCLAW_ROLE": "sync-worker",
                "OPENCLAW_SCHEDULER_SOURCE": "background-worker",
            },
        )
    if job == "supervisor":
        return (
            [assistant, "jobs", "check", "--target", "all"],
            int(os.environ.get("SUPERVISOR_INTERVAL_SECONDS", "300")),
            int(os.environ.get("SUPERVISOR_INITIAL_DELAY_SECONDS", "180")),
            True,
            {"OPENCLAW_ROLE": "supervisor-worker"},
        )
    if job == "portfolio":
        return (
            [assistant, "portfolio", "quotes", "refresh"],
            int(os.environ.get("PORTFOLIO_INTERVAL_SECONDS", "900")),
            int(os.environ.get("PORTFOLIO_INITIAL_DELAY_SECONDS", "240")),
            False,
            {
                "OPENCLAW_ROLE": "portfolio-worker",
                "OPENCLAW_SCHEDULER_SOURCE": "background-worker",
            },
        )
    if job == "monitor":
        return (
            [assistant, "monitor", "record", "--days", "7", "--live"],
            int(os.environ.get("MONITOR_INTERVAL_SECONDS", "3600")),
            int(os.environ.get("MONITOR_INITIAL_DELAY_SECONDS", "420")),
            True,
            {
                "OPENCLAW_ROLE": "monitor-worker",
                "OPENCLAW_SCHEDULER_SOURCE": "background-worker",
            },
        )
    raise ValueError(job)


def handler(signum: int, frame: object) -> None:
    del signum, frame
    global STOP
    STOP = True


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("job", choices=("mail", "sync", "supervisor", "portfolio", "monitor"))
    args = parser.parse_args()

    signal.signal(signal.SIGTERM, handler)
    signal.signal(signal.SIGINT, handler)

    workspace = Path(os.environ.get("OPENCLAW_WORKSPACE", "/home/node/.openclaw/workspace")).resolve()
    image_root = Path(os.environ.get("OPENCLAW_IMAGE_ROOT", "/opt/openclaw-agent")).resolve()
    if not Path(__file__).resolve().is_relative_to(image_root):
        raise SystemExit(f"Worker-Loop liegt nicht im Imagepfad: {Path(__file__).resolve()}")
    status_dir = Path(
        os.environ.get(
            "OPENCLAW_JOB_STATUS_DIR",
            workspace / "personal_assistant/data/container_jobs",
        )
    ).resolve()
    log_dir = Path(
        os.environ.get(
            "OPENCLAW_LOG_DIR",
            workspace / "personal_assistant/data/container_logs",
        )
    ).resolve()
    coordination = Path(
        os.environ.get(
            "OPENCLAW_COORDINATION_DATA_DIR",
            workspace / "personal_assistant/data",
        )
    ).resolve()
    state_path = coordination / "job_control.json"
    heartbeat = status_dir / f"{args.job}.json"
    wake = status_dir / f"{args.job}.wake"
    log_path = log_dir / f"{args.job}.log"
    status_dir.mkdir(parents=True, exist_ok=True)
    log_dir.mkdir(parents=True, exist_ok=True)

    command, interval, initial_delay, default_on, extra_env = config(args.job, workspace, image_root)
    scheduler = None if args.job == "supervisor" else AdaptiveWorkScheduler(
        coordination / "work_scheduler.sqlite3"
    )
    scheduler_owner = f"container:{args.job}:{socket.gethostname()}:{os.getpid()}"
    next_run = time.monotonic() + max(0, initial_delay)
    status: dict[str, Any] = {
        "job": args.job,
        "state": "starting",
        "updated_at": now(),
        "result": "unknown",
        "business_status": "starting",
        "last_exit_code": None,
        "consecutive_failures": 0,
    }
    atomic_json(heartbeat, status)

    while not STOP:
        is_desired = desired(state_path, args.job, default_on)
        if not is_desired:
            status.update(state="disabled", updated_at=now(), business_status="disabled")
            atomic_json(heartbeat, status)
            time.sleep(10)
            next_run = time.monotonic() + interval
            continue

        if wake.exists():
            wake.unlink(missing_ok=True)
            next_run = time.monotonic()

        if time.monotonic() < next_run:
            status.update(
                state="waiting",
                updated_at=now(),
                next_run_in_seconds=max(0, int(next_run - time.monotonic())),
            )
            atomic_json(heartbeat, status)
            time.sleep(min(15, max(1, next_run - time.monotonic())))
            continue

        claim = None
        ticket_id = ""
        if scheduler is not None:
            ticket_id = scheduler.enqueue(
                args.job,
                owner=scheduler_owner,
                metadata={"runtime": "container", "interval_seconds": interval},
            )
            queue_aborted = False
            while not STOP:
                if not desired(state_path, args.job, default_on):
                    scheduler.cancel_pending(
                        ticket_id,
                        detail="Job wurde waehrend der Wartezeit ausgeschaltet",
                    )
                    status.update(
                        state="disabled",
                        updated_at=now(),
                        business_status="disabled",
                        scheduler_ticket=ticket_id,
                    )
                    atomic_json(heartbeat, status)
                    next_run = time.monotonic() + interval
                    queue_aborted = True
                    break
                claim = scheduler.claim(ticket_id, owner=scheduler_owner)
                if claim.granted:
                    break
                status.update(
                    state="queued",
                    updated_at=now(),
                    scheduler_ticket=ticket_id,
                    queue_reason=claim.reason,
                    queue_position=claim.position,
                    queue_score=claim.score,
                )
                atomic_json(heartbeat, status)
                time.sleep(5)
            if STOP:
                scheduler.cancel_pending(ticket_id, detail="Worker wird beendet")
                break
            if queue_aborted:
                continue
            if claim is None or not claim.granted:
                continue

        started = now()
        status.pop("scheduler_error", None)
        status.update(
            state="running",
            updated_at=started,
            last_started_at=started,
            last_exit_code=None,
            result="running",
            command=command,
            scheduler_ticket=ticket_id,
            queue_reason="granted" if claim is not None else "bypass",
            queue_position=1 if claim is not None else None,
            queue_score=claim.score if claim is not None else None,
        )
        atomic_json(heartbeat, status)
        env = os.environ.copy()
        env.update(extra_env)
        lease_failures = 0
        lease_lost = False
        with log_path.open("ab", buffering=0) as log:
            header = f"\n[{started}] START {' '.join(command)}\n".encode()
            log.write(header)
            process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT, env=env, cwd=workspace)
            while process.poll() is None and not STOP:
                status.update(state="running", updated_at=now(), pid=process.pid)
                atomic_json(heartbeat, status)
                if scheduler is not None and claim is not None:
                    try:
                        renewed = scheduler.renew(claim.lease_token, owner=scheduler_owner)
                    except sqlite3.Error as exc:
                        renewed = False
                        log.write(f"[{now()}] SCHEDULER lease renewal error: {exc}\n".encode())
                    lease_failures = 0 if renewed else lease_failures + 1
                    if lease_failures >= 3:
                        lease_lost = True
                        log.write(f"[{now()}] SCHEDULER lease lost; stopping child safely\n".encode())
                        process.terminate()
                        break
                time.sleep(10)
            if (STOP or lease_lost) and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=120)
                except subprocess.TimeoutExpired:
                    process.kill()
            code = int(process.wait())
            if lease_lost:
                code = 125
            finished = now()
            log.write(f"[{finished}] END exit={code}\n".encode())

        result_name = (
            "interrupted" if STOP or lease_lost
            else ("completed" if code == 0 else ("degraded" if code == 1 else "failed"))
        )
        if scheduler is not None and claim is not None:
            recorded = scheduler.finish(
                claim.lease_token,
                owner=scheduler_owner,
                result=result_name,
                exit_code=code,
                error_code="lease-lost" if lease_lost else "",
                detail="Worker beendet" if STOP else (
                    "Scheduler-Lease konnte nicht erneuert werden" if lease_lost else ""
                ),
            )
            if not recorded:
                lease_lost = True
                code = 125
                status["scheduler_error"] = "Laufergebnis konnte keiner aktiven Lease zugeordnet werden"
            else:
                scheduler.prune(keep_days=180)
        previous_failures = int(status.get("consecutive_failures") or 0)
        if STOP or lease_lost:
            business_status = "interrupted"
            consecutive_failures = previous_failures + 1
        elif code == 0:
            business_status = "healthy"
            consecutive_failures = 0
        elif code == 1:
            business_status = "degraded"
            consecutive_failures = previous_failures + 1
        else:
            business_status = "failed"
            consecutive_failures = previous_failures + 1
        status.update(
            state="waiting" if not STOP else "stopping",
            updated_at=now(),
            last_finished_at=finished,
            last_exit_code=code,
            result="success" if code == 0 else ("degraded" if code == 1 else "failed"),
            business_status=business_status,
            consecutive_failures=consecutive_failures,
            pid=None,
        )
        if code == 0:
            status["last_success_at"] = finished
        atomic_json(heartbeat, status)
        next_run = time.monotonic() + interval

    status.update(state="stopped", updated_at=now())
    atomic_json(heartbeat, status)
    if scheduler is not None:
        scheduler.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
