"""Readiness check for the private resident ClamAV daemon."""

from __future__ import annotations

import json
import os
import stat
import time
from pathlib import Path

from .clamav_health import signature_age_seconds
from .clamav_vendor import vendor_readiness
from .clamd_client import ClamdClientError, ClamdUnixClient

RECEIPT = "openclaw-resident-status.json"
HEARTBEAT_TTL = 20


def socket_identity(path: Path) -> list[int]:
    info = path.lstat()
    if not stat.S_ISSOCK(info.st_mode) or path.parent.is_symlink():
        raise ValueError("socket-invalid")
    return [info.st_dev, info.st_ino, info.st_ctime_ns]


def resident_readiness(socket_path: str | Path, *, database_dir: Path, version: str) -> dict:
    path = Path(socket_path)
    vendor = vendor_readiness(database_dir)
    reasons = list(vendor.get("reasons", []))
    receipt = {}
    try:
        fd = os.open(path.with_name(RECEIPT), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as stream:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                raise ValueError("resident-unverified")
            content = stream.read(4097)
        if len(content) > 4096:
            raise ValueError("resident-unverified")
        receipt = json.loads(content)
        if not isinstance(receipt, dict):
            raise ValueError("resident-unverified")
        valid = (
            receipt.get("schema") == 1
            and receipt["socket"] == socket_identity(path)
            and receipt["version"] == version
            and isinstance(receipt["instance"], str) and len(receipt["instance"]) == 32
            and 0 <= time.time() - receipt["checked_at"] <= HEARTBEAT_TTL
            and receipt["generation"] == vendor.get("generation")
            and receipt["versions"] == vendor.get("versions")
            and version.split("/")[1] == str(receipt["versions"]["daily"])
        )
        if not valid:
            reasons.append("resident-evidence-mismatch")
    except (IndexError, KeyError, TypeError, OSError, ValueError, RecursionError):
        receipt = {}
        reasons.append("resident-unverified")
    ok = bool(vendor["ok"] and not reasons)
    return {"ok": ok, "reasons": reasons, "vendor": vendor, "resident": receipt,
            "generation": vendor.get("generation") if ok else None,
            "instance": receipt.get("instance") if ok else None}


def check_clamd(
    socket_path: str | Path,
    *,
    database_dir: Path,
    max_age_seconds: int | None = None,
    timeout_seconds: float,
    max_stream_bytes: int,
) -> dict[str, object]:
    client = ClamdUnixClient(
        socket_path,
        timeout_seconds=timeout_seconds,
        max_stream_bytes=max_stream_bytes,
    )
    started = time.monotonic()
    deadline = started + min(timeout_seconds, 5)
    client.timeout_seconds = client._remaining_timeout(deadline)
    before = socket_identity(client.socket_path)
    client.ping()
    client.timeout_seconds = client._remaining_timeout(deadline)
    version = client.version()
    evidence = resident_readiness(socket_path, database_dir=database_dir, version=version)
    if before != socket_identity(client.socket_path):
        raise ClamdClientError("socket-changed", "ClamAV-Socket wurde ersetzt")
    client._remaining_timeout(deadline)
    return {
        "ok": evidence["ok"],
        "daemon_ready": True,
        "transport": "daemon-stream",
        "version": version,
        "latency_ms": round((time.monotonic() - started) * 1000, 3),
        "signatures_fresh": evidence["ok"],
        "signature_age_seconds": signature_age_seconds(version),
        "database": evidence["vendor"],
        "currentness": evidence,
    }


def main() -> int:
    try:
        report = check_clamd(
            os.environ.get("OPENCLAW_CLAMD_SOCKET", "/run/clamav/clamd.sock"),
            database_dir=Path(os.environ.get("CLAMAV_DATABASE_DIR", "/var/lib/clamav")),
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
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
