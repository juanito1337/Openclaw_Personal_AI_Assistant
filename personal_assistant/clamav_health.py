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

SIGNATURE_GROUPS = ("main", "daily", "bytecode")


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
    *, database_dir: Path | None = None, timeout: int, run: Callable[..., subprocess.CompletedProcess[str]],
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
    max_age_seconds: int,
    now: float | None = None,
    run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> dict[str, object]:
    if max_age_seconds < 1:
        raise ValueError("CLAMAV_SIGNATURE_MAX_AGE_SECONDS muss positiv sein")
    selected: dict[str, Path] = {}
    for group in SIGNATURE_GROUPS:
        matches = [database_dir / f"{group}.{suffix}" for suffix in ("cvd", "cld")]
        usable = [path for path in matches if path.is_file() and path.stat().st_size > 0]
        if not usable:
            raise ValueError(f"ClamAV-Signatur fehlt: {group}.cvd/.cld")
        selected[group] = max(usable, key=lambda path: path.stat().st_mtime)
    identity = database_identity(database_dir=database_dir, timeout=10, run=run)
    age = signature_age_seconds(identity, now=now)
    if age is None:
        raise ValueError("ClamAV-Scanneridentitaet oder Signaturzeitstempel ist nicht verifizierbar")
    if age > max_age_seconds:
        raise ValueError(f"ClamAV-daily-Signatur ist zu alt: {age}s > {max_age_seconds}s")
    return {
        "ok": True,
        "scanner_identity": identity,
        "daily_age_seconds": age,
        "max_age_seconds": max_age_seconds,
        "signatures": {name: path.name for name, path in selected.items()},
    }


def main() -> int:
    database_dir = Path(os.environ.get("CLAMAV_DATABASE_DIR", "/var/lib/clamav"))
    max_age = int(os.environ.get("CLAMAV_SIGNATURE_MAX_AGE_SECONDS", "172800"))
    try:
        report = inspect_database(database_dir, max_age_seconds=max_age)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        print(f"ClamAV nicht bereit: {exc}")
        return 1
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
