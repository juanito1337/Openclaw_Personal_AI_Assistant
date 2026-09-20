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

from personal_assistant.container_job_profiles import config
from personal_assistant.job_runtime import resolve_job_run
from personal_assistant.work_scheduler import AdaptiveWorkScheduler

STOP = False
BATCH_RESUME_EXIT_CODE = 75


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
    scheduler_db = coordination / "work_scheduler.sqlite3"
    scheduler = None if args.job == "supervisor" else AdaptiveWorkScheduler(scheduler_db)
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
    resume_parent_run_id = ""

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
                parent_run_id=resume_parent_run_id,
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
        status.pop("batch_resume_pending", None)
        status.update(
            state="running",
            updated_at=started,
            last_started_at=started,
            last_exit_code=None,
            result="in-progress",
            command=command,
            scheduler_ticket=ticket_id,
            queue_reason="granted" if claim is not None else "bypass",
            queue_position=1 if claim is not None else None,
            queue_score=claim.score if claim is not None else None,
            run_id=claim.run_id if claim is not None else "",
            attempt_id=claim.attempt_id if claim is not None else "",
            parent_run_id=claim.parent_run_id if claim is not None else "",
            job_id=claim.job_id if claim is not None else args.job,
        )
        atomic_json(heartbeat, status)
        env = os.environ.copy()
        env.update(extra_env)
        if claim is not None:
            env.update(
                {
                    "OPENCLAW_RUN_ID": claim.run_id,
                    "OPENCLAW_ATTEMPT_ID": claim.attempt_id,
                    "OPENCLAW_PARENT_RUN_ID": claim.parent_run_id,
                    "OPENCLAW_JOB_ID": claim.job_id,
                }
            )
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

        preliminary = resolve_job_run(
            code,
            stopped=STOP,
            lease_lost=lease_lost,
            previous_failures=int(status.get("consecutive_failures") or 0),
            claim_run_id=claim.run_id if claim is not None else "",
            batch_resume_exit_code=BATCH_RESUME_EXIT_CODE,
        )
        if scheduler is not None and claim is not None:
            recorded = scheduler.finish(
                claim.lease_token,
                owner=scheduler_owner,
                result=preliminary.result,
                exit_code=code,
                error_code="lease-lost" if lease_lost else "",
                detail="Worker beendet"
                if STOP
                else ("Scheduler-Lease konnte nicht erneuert werden" if lease_lost else ""),
            )
            if not recorded:
                lease_lost = True
                code = 125
                status["scheduler_error"] = "Laufergebnis konnte keiner aktiven Lease zugeordnet werden"
            else:
                scheduler.prune(keep_days=180)
        previous_failures = int(status.get("consecutive_failures") or 0)
        outcome = resolve_job_run(
            code,
            stopped=STOP,
            lease_lost=lease_lost,
            previous_failures=previous_failures,
            claim_run_id=claim.run_id if claim is not None else "",
            batch_resume_exit_code=BATCH_RESUME_EXIT_CODE,
        )
        status.update(
            state="waiting" if not STOP else "stopping",
            updated_at=now(),
            last_finished_at=finished,
            last_exit_code=code,
            result=outcome.result,
            business_status=outcome.business_status,
            consecutive_failures=outcome.consecutive_failures,
            pid=None,
        )
        if outcome.last_success:
            status["last_success_at"] = finished
        if outcome.batch_resume:
            status["batch_resume_pending"] = True
        resume_parent_run_id = outcome.resume_parent_run_id
        atomic_json(heartbeat, status)
        next_run = time.monotonic() if outcome.batch_resume else time.monotonic() + interval

    status.update(state="stopped", updated_at=now())
    atomic_json(heartbeat, status)
    if scheduler is not None:
        scheduler.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
