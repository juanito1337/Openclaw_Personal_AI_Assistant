"""Readiness check for the private resident ClamAV daemon."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from .clamav_health import inspect_database
from .clamd_client import ClamdClientError, ClamdUnixClient


def check_clamd(
    socket_path: str | Path,
    *,
    database_dir: Path,
    max_age_seconds: int,
    timeout_seconds: float,
    max_stream_bytes: int,
) -> dict[str, object]:
    database = inspect_database(database_dir, max_age_seconds=max_age_seconds)
    client = ClamdUnixClient(
        socket_path,
        timeout_seconds=timeout_seconds,
        max_stream_bytes=max_stream_bytes,
    )
    started = time.monotonic()
    client.ping()
    version = client.version()
    return {
        "ok": True,
        "daemon_ready": True,
        "transport": "daemon-stream",
        "version": version,
        "latency_ms": round((time.monotonic() - started) * 1000.0, 3),
        "database": database,
    }


def main() -> int:
    try:
        report = check_clamd(
            os.environ.get("OPENCLAW_CLAMD_SOCKET", "/run/clamav/clamd.sock"),
            database_dir=Path(os.environ.get("CLAMAV_DATABASE_DIR", "/var/lib/clamav")),
            max_age_seconds=int(os.environ.get("CLAMAV_SIGNATURE_MAX_AGE_SECONDS", "172800")),
            timeout_seconds=float(os.environ.get("CLAMAV_HEALTH_TIMEOUT_SECONDS", "5")),
            max_stream_bytes=int(os.environ.get("CLAMAV_STREAM_MAX_BYTES", "100000000")),
        )
    except (ClamdClientError, OSError, ValueError) as exc:
        print(
            json.dumps(
                {
                    "ok": False,
                    "daemon_ready": False,
                    "error": getattr(exc, "category", "clamd-not-ready"),
                    "detail": str(exc),
                },
                sort_keys=True,
            )
        )
        return 1
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
