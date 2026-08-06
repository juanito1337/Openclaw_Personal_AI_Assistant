"""Fail-closed ClamAV signature freshness and scanner identity check."""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
from collections.abc import Callable
from pathlib import Path

SIGNATURE_GROUPS = ("main", "daily", "bytecode")


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
    current = time.time() if now is None else now
    age = max(0, int(current - selected["daily"].stat().st_mtime))
    if age > max_age_seconds:
        raise ValueError(f"ClamAV-daily-Signatur ist zu alt: {age}s > {max_age_seconds}s")
    completed = run(
        ["clamscan", "--version"],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    identity = (completed.stdout or completed.stderr).strip()
    if completed.returncode != 0 or not re.search(r"\bClamAV\s+[^/\s]+/\d+", identity):
        raise ValueError("ClamAV-Scanneridentitaet oder Signaturversion ist nicht verifizierbar")
    return {
        "ok": True,
        "scanner_identity": identity.splitlines()[0],
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
