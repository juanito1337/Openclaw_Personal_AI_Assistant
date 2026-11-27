"""Fail-closed ClamAV signature freshness and scanner identity check."""

from __future__ import annotations

import json
import math
import os
import re
import subprocess
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from .clamav_vendor import vendor_readiness


def signature_build_time(version: str) -> datetime | None:
    """Parse the build timestamp as wall time in the VERSION producer's timezone."""
    match = re.fullmatch(r"ClamAV [^/\s]+/[0-9]+/([^/\n]+)", version)
    if not match:
        return None
    try:
        return datetime.strptime(match[1], "%a %b %d %H:%M:%S %Y")
    except ValueError:
        return None


def signature_age_seconds(version: str, *, now: float | None = None) -> int | None:
    """Compute age only for a VERSION identity produced in UTC."""
    stamp = signature_build_time(version)
    if stamp is None:
        return None
    elapsed = (time.time() if now is None else now) - stamp.replace(tzinfo=UTC).timestamp()
    return math.ceil(elapsed) if elapsed >= 0 else None


def database_identity(
    *, database_dir: Path | None = None, timeout: float, run: Callable[..., subprocess.CompletedProcess[str]],
) -> str:
    command = ["clamscan"]
    if database_dir is not None:
        command.append(f"--database={database_dir}")
    completed = run(
        [*command, "--version"], env={**os.environ, "TZ": "UTC"},
        capture_output=True, text=True, errors="replace", timeout=timeout, check=False,
    )
    return (completed.stdout or completed.stderr).strip() if completed.returncode == 0 else ""


def inspect_database(
    database_dir: Path,
    *,
    max_age_seconds: int | None = None,
    now: float | None = None,
    run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> dict[str, object]:
    evidence = vendor_readiness(database_dir, now=now)
    identity = database_identity(database_dir=database_dir, timeout=5, run=run) if evidence["ok"] else ""
    reasons = list(evidence.get("reasons", []))
    if evidence["ok"] and (signature_build_time(identity) is None
                           or identity.split("/")[1] != str(evidence["versions"]["daily"])):
        reasons.append("scanner-identity-mismatch")
    return {
        **evidence,
        "ok": evidence["ok"] and not reasons,
        "reasons": reasons,
        "scanner_identity": identity,
        "daily_age_seconds": signature_age_seconds(identity, now=now),
    }


def main() -> int:
    database_dir = Path(os.environ.get("CLAMAV_DATABASE_DIR", "/var/lib/clamav"))
    try:
        report = inspect_database(database_dir)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        print(f"ClamAV nicht bereit: {exc}")
        return 1
    print(json.dumps(report, sort_keys=True))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
