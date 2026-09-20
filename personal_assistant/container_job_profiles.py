from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path


def config(
    job: str,
    workspace: Path,
    image_root: Path,
    *,
    environ: Mapping[str, str] | None = None,
) -> tuple[list[str], int, int, bool, dict[str, str]]:
    """Return the fixed command and scheduling profile for one container job."""

    env = os.environ if environ is None else environ
    assistant = str(image_root / "scripts/assistant.sh")
    mail_agent = str(image_root / "scripts/mail-agent.sh")
    if job == "mail":
        productive = [
            mail_agent,
            "run",
            "--drain",
            "--batch-size",
            env.get("MAIL_DRAIN_BATCH_SIZE", "20"),
            "--max-messages",
            env.get("MAIL_MAX_MESSAGES", "500"),
            "--max-runtime",
            env.get("MAIL_MAX_RUNTIME", "2400"),
            "--shutdown-reserve",
            env.get("MAIL_SHUTDOWN_RESERVE", "180"),
            "--max-batches",
            env.get("MAIL_MAX_BATCHES", "100"),
            "--no-digest",
        ]
        return (
            [
                "python3",
                "-P",
                "-m",
                "personal_assistant.mail_owner_cycle",
                "--image-root",
                str(image_root),
                "--",
                "python3",
                "-P",
                "-m",
                "personal_assistant.mail_worker",
                "--",
                *productive,
            ],
            int(env.get("MAIL_INTERVAL_SECONDS", "1200")),
            int(env.get("MAIL_INITIAL_DELAY_SECONDS", "120")),
            True,
            {
                "OPENCLAW_ROLE": "mail-worker",
                "OPENCLAW_OLLAMA_PRIORITY": "background",
                "OPENCLAW_OLLAMA_SOURCE": "mail-container-worker",
                "OPENCLAW_SCHEDULER_SOURCE": "background-worker",
            },
        )
    profiles = {
        "sync": (
            [assistant, "index", "all"],
            int(env.get("SYNC_INTERVAL_SECONDS", "900")),
            int(env.get("SYNC_INITIAL_DELAY_SECONDS", "300")),
            False,
            {
                "OPENCLAW_ROLE": "sync-worker",
                "OPENCLAW_SCHEDULER_SOURCE": "background-worker",
                "OPENCLAW_BATCHED_JOB": "1",
            },
        ),
        "supervisor": (
            [assistant, "jobs", "check", "--target", "all"],
            int(env.get("SUPERVISOR_INTERVAL_SECONDS", "300")),
            int(env.get("SUPERVISOR_INITIAL_DELAY_SECONDS", "180")),
            True,
            {"OPENCLAW_ROLE": "supervisor-worker"},
        ),
        "portfolio": (
            [assistant, "portfolio", "quotes", "refresh"],
            int(env.get("PORTFOLIO_INTERVAL_SECONDS", "900")),
            int(env.get("PORTFOLIO_INITIAL_DELAY_SECONDS", "240")),
            False,
            {
                "OPENCLAW_ROLE": "portfolio-worker",
                "OPENCLAW_SCHEDULER_SOURCE": "background-worker",
            },
        ),
        "monitor": (
            [assistant, "monitor", "record", "--days", "7", "--live"],
            int(env.get("MONITOR_INTERVAL_SECONDS", "3600")),
            int(env.get("MONITOR_INITIAL_DELAY_SECONDS", "420")),
            True,
            {
                "OPENCLAW_ROLE": "monitor-worker",
                "OPENCLAW_SCHEDULER_SOURCE": "background-worker",
            },
        ),
    }
    try:
        return profiles[job]
    except KeyError as exc:
        raise ValueError(job) from exc
