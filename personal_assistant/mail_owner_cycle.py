from __future__ import annotations

import argparse
import json
import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.chmod(temporary, 0o600)
    temporary.replace(path)


def _desired(state_path: Path, name: str) -> bool:
    try:
        payload = json.loads(state_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return False
    desired = payload.get("desired") if isinstance(payload, dict) else {}
    return bool(desired.get(name, False)) if isinstance(desired, dict) else False


def _heartbeat(
    status_path: Path,
    *,
    state: str,
    result: str,
    exit_code: int | None,
    detail: str = "",
    started_at: str = "",
) -> None:
    timestamp = _now()
    payload: dict[str, Any] = {
        "job": "mail-index",
        "state": state,
        "updated_at": timestamp,
        "result": result,
        "business_status": (
            "healthy" if exit_code == 0 else "disabled" if state == "disabled" else "degraded"
        ),
        "last_exit_code": exit_code,
        "detail": detail,
    }
    if started_at:
        payload["last_started_at"] = started_at
    if state in {"waiting", "disabled"}:
        payload["last_finished_at"] = timestamp
    if exit_code == 0:
        payload["last_success_at"] = timestamp
    _atomic_json(status_path, payload)


def main() -> int:
    parser = argparse.ArgumentParser(description="Einzelner Mail-Owner-Zyklus")
    parser.add_argument("--state-path", type=Path)
    parser.add_argument("--status-dir", type=Path)
    parser.add_argument("--image-root", type=Path, required=True)
    parser.add_argument("mail_command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    mail_command = list(args.mail_command)
    if mail_command[:1] == ["--"]:
        mail_command = mail_command[1:]
    if not mail_command:
        raise SystemExit("Mail-Kommando fehlt")

    workspace = Path(
        os.environ.get("OPENCLAW_WORKSPACE", "/home/node/.openclaw/workspace")
    )
    coordination = Path(
        os.environ.get(
            "OPENCLAW_COORDINATION_DATA_DIR",
            workspace / "personal_assistant/data",
        )
    )
    state_path = args.state_path or coordination / "job_control.json"
    status_dir = args.status_dir or Path(
        os.environ.get(
            "OPENCLAW_JOB_STATUS_DIR",
            workspace / "personal_assistant/data/container_jobs",
        )
    )

    mail_result = subprocess.run(mail_command, check=False)
    status_path = status_dir / "mail-index.json"
    if mail_result.returncode == 3:
        index_enabled = _desired(state_path, "mail-index")
        _heartbeat(
            status_path,
            state="waiting" if index_enabled else "disabled",
            result="deferred",
            exit_code=None,
            detail=(
                "Mail-Owner-Lauf wegen belegter Single-Writer-Sperre kontrolliert vertagt"
            ),
        )
        return 0
    if mail_result.returncode != 0:
        _heartbeat(
            status_path,
            state="waiting",
            result="degraded",
            exit_code=1,
            detail="Reconcile nach eingeschraenktem Mail-Lauf nicht gestartet",
        )
        return int(mail_result.returncode)
    if not _desired(state_path, "mail-index"):
        _heartbeat(
            status_path,
            state="disabled",
            result="success",
            exit_code=None,
            detail="Persistenter Sollzustand ist OFF",
        )
        return 0

    started = _now()
    _heartbeat(
        status_path,
        state="running",
        result="running",
        exit_code=None,
        detail="Read-only IMAP-Reconciliation laeuft beim Mail-Owner",
        started_at=started,
    )
    assistant = args.image_root / "scripts/assistant.sh"
    reconcile = [
        str(assistant),
        "mail",
        "index",
        "reconcile",
        "--max-folders",
        os.environ.get("MAIL_INDEX_MAX_FOLDERS", "500"),
        "--max-messages",
        os.environ.get("MAIL_INDEX_MAX_MESSAGES", "100000"),
        "--max-bytes",
        os.environ.get("MAIL_INDEX_MAX_BYTES", "2000000000"),
        "--max-message-bytes",
        os.environ.get("MAIL_INDEX_MAX_MESSAGE_BYTES", "100000000"),
        "--max-runtime",
        os.environ.get("MAIL_INDEX_MAX_RUNTIME", "600"),
        "--request-interval",
        os.environ.get("MAIL_INDEX_REQUEST_INTERVAL", "0.05"),
        "--retention-generations",
        os.environ.get("MAIL_INDEX_RETENTION_GENERATIONS", "2"),
        "--yes",
    ]
    result = subprocess.run(reconcile, check=False)
    code = int(result.returncode)
    _heartbeat(
        status_path,
        state="waiting",
        result="success" if code == 0 else "degraded" if code == 1 else "failed",
        exit_code=code,
        detail=(
            "Autoritativer Reconcile abgeschlossen"
            if code == 0
            else "Autoritativer Reconcile meldet einen begrenzten Fehler"
        ),
        started_at=started,
    )
    return code


if __name__ == "__main__":
    raise SystemExit(main())
