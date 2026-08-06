"""M4 worker liveness, readiness and business-state contracts."""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def evaluate(mode: str, payload: dict[str, Any], *, current: datetime | None = None) -> dict[str, Any]:
    timestamp = datetime.fromisoformat(str(payload["updated_at"]).replace("Z", "+00:00"))
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=UTC)
    observed = datetime.now(UTC) if current is None else current.astimezone(UTC)
    age = (observed - timestamp.astimezone(UTC)).total_seconds()
    if age < -30 or age > 180:
        raise ValueError(f"stale heartbeat: {age:.0f}s")
    state = str(payload.get("state") or "")
    business = str(payload.get("business_status") or "")
    if not business:
        raise ValueError("business_status fehlt")
    if mode == "":
        if state == "stopped":
            raise ValueError("worker stopped")
    elif mode == "-readiness":
        if state in {"starting", "stopped", "stopping"}:
            raise ValueError(f"worker not ready: {state}")
        if payload.get("scheduler_error"):
            raise ValueError("scheduler readiness failed")
    elif mode == "-business":
        if business not in {"healthy", "disabled"}:
            raise ValueError(f"business unhealthy: {business}")
    else:
        raise ValueError(f"unknown worker health mode: {mode}")
    return {
        "ok": True,
        "mode": "liveness" if mode == "" else mode.removeprefix("-"),
        "state": state,
        "business_status": business,
        "consecutive_failures": int(payload.get("consecutive_failures") or 0),
        "age_seconds": int(age),
    }


def main() -> int:
    if len(sys.argv) != 3:
        print("usage: container_health MODE HEARTBEAT", file=sys.stderr)
        return 2
    try:
        payload = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
        report = evaluate(sys.argv[1], payload)
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        print(f"Worker-Health fehlgeschlagen: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
