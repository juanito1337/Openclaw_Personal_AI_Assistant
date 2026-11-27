"""Bounded official ClamAV version observations and offline generation evidence.

DNS announces versions; only FreshClam downloads and verifies signed databases.
Signature build age is diagnostic, never a substitute for vendor equality.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, TypeGuard

GROUPS = ("main", "daily", "bytecode")
SOURCE = "current.cvd.clamav.net"
STATUS_NAME = "openclaw-vendor-status.json"
POLL_SECONDS = 300
PROOF_TTL_SECONDS = 660
RECORD_MAX_AGE_SECONDS = 43200
MAX_EVIDENCE_BYTES = 16384
MAX_DATABASE_BYTES = 1024 * 1024 * 1024
ERROR_CODES = frozenset({
    "vendor-probe-timeout", "vendor-probe-failed", "vendor-response-invalid", "vendor-version-regression",
    "local-version-ahead", "database-version-mismatch", "database-missing", "database-ambiguous",
    "database-invalid", "database-changed", "freshclam-timeout", "freshclam-failed", "updater-io-error",
    "database-signature-invalid", "database-verification-timeout", "database-verifier-unavailable",
})


class VendorError(ValueError):
    """Safe technical failure; never contains tool output or paths."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _number(value: Any) -> TypeGuard[int | float]:
    try:
        return type(value) in (int, float) and math.isfinite(value) and value > 0
    except OverflowError:
        return False


def _versions(value: Any) -> bool:
    return isinstance(value, dict) and set(value) == set(GROUPS) and all(
        type(x) is int and 0 < x < 2**31 for x in value.values()
    )


def vendor_observation_valid(proof: dict, now: float, *, check_ttl: bool = True) -> bool:
    """Validate one complete observation; historical callers supply its original check time."""
    checked, stamp = proof.get("checked_at"), proof.get("record_timestamp")
    return bool(
        proof.get("source") == SOURCE and _versions(proof.get("versions"))
        and _number(checked) and _number(stamp) and _number(now)
        and stamp <= checked <= now
        and now - stamp <= RECORD_MAX_AGE_SECONDS
        and (not check_ttl or now - checked <= PROOF_TTL_SECONDS)
    )


def query_vendor(*, now: float | None = None) -> dict:
    """Run DNS-only lookup in the existing maintenance image, with a hard deadline."""
    try:
        result = subprocess.run(
            ["/bin/busybox", "nslookup", "-type=TXT", SOURCE],
            capture_output=True, text=True, errors="replace", timeout=10, check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise VendorError("vendor-probe-timeout") from exc
    except (OSError, subprocess.SubprocessError) as exc:
        raise VendorError("vendor-probe-failed") from exc
    if result.returncode != 0:
        raise VendorError("vendor-probe-failed")
    output = result.stdout
    if not isinstance(output, str) or len(output.encode()) > MAX_EVIDENCE_BYTES:
        raise VendorError("vendor-response-invalid")
    matches = re.findall(r'^current\.cvd\.clamav\.net\s+text\s*=\s*"([^"\r\n]+)"\s*$', output, re.M)
    if len(matches) != 1:
        raise VendorError("vendor-response-invalid")
    fields = matches[0].split(":")
    if len(fields) != 8 or not all(re.fullmatch(r"[0-9]{1,12}", fields[i]) for i in (1, 2, 3, 7)):
        raise VendorError("vendor-response-invalid")
    checked = time.time() if now is None else now
    proof = {
        "source": SOURCE, "checked_at": checked, "record_timestamp": int(fields[3]),
        "versions": {"main": int(fields[1]), "daily": int(fields[2]), "bytecode": int(fields[7])},
    }
    if not vendor_observation_valid(proof, checked):
        raise VendorError("vendor-response-invalid")
    return proof


def _open_regular(path: Path, *, limit: int) -> int:
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError as exc:
        raise VendorError("evidence-unreadable") from exc
    info = os.fstat(fd)
    if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= limit:
        os.close(fd)
        raise VendorError("evidence-invalid")
    return fd


def _metadata(info: os.stat_result) -> dict:
    return {"size": info.st_size, "mtime_ns": info.st_mtime_ns, "ctime_ns": info.st_ctime_ns,
            "inode": info.st_ino, "device": info.st_dev}


def _header_version(header: bytes) -> int:
    try:
        fields = header.decode("ascii").strip().split(":")
        version = int(fields[2])
        if len(header) != 512 or fields[0] != "ClamAV-VDB" or len(fields) < 9 or not 0 < version < 2**31:
            raise ValueError
        return version
    except (ValueError, IndexError, UnicodeError) as exc:
        raise VendorError("database-invalid") from exc


def _selected(database_dir: Path, group: str, *, allow_missing: bool = False) -> Path | None:
    if database_dir.is_symlink() or not database_dir.is_dir():
        raise VendorError("database-invalid")
    files = [database_dir / f"{group}.{ext}" for ext in ("cvd", "cld")]
    found = [p for p in files if p.exists() or p.is_symlink()]
    if not found:
        if allow_missing:
            return None
        raise VendorError("database-missing")
    if len(found) != 1:
        raise VendorError("database-ambiguous")
    return found[0]


def _generation(versions: dict, files: dict) -> str:
    payload = {name: {"version": versions[name], "sha256": files[name]["sha256"]} for name in versions}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def database_snapshot(database_dir: Path, *, allow_missing: bool = False) -> dict:
    versions: dict[str, int] = {}
    files: dict[str, dict] = {}
    for group in GROUPS:
        path = _selected(database_dir, group, allow_missing=allow_missing)
        if path is None:
            continue
        try:
            fd = _open_regular(path, limit=MAX_DATABASE_BYTES)
            with os.fdopen(fd, "rb") as handle:
                before = os.fstat(handle.fileno())
                version = _header_version(handle.read(512))
                handle.seek(0)
                digest = hashlib.file_digest(handle, "sha256").hexdigest()
                after = os.fstat(handle.fileno())
                if _metadata(before) != _metadata(after) or _metadata(after) != _metadata(path.lstat()):
                    raise VendorError("database-changed")
        except (OSError, VendorError) as exc:
            if isinstance(exc, VendorError) and exc.code == "database-changed":
                raise
            raise VendorError("database-invalid") from exc
        versions[group] = version
        files[group] = {"name": path.name, **_metadata(after), "sha256": digest}
    return {"versions": versions, "files": files, "generation": _generation(versions, files)}


def disk_matches(database_dir: Path, snapshot: dict) -> bool:
    """Check trusted publisher's bound header/stat identities without repeated full hashing."""
    try:
        versions, files = snapshot["versions"], snapshot["files"]
        if not _versions(versions) or set(files) != set(GROUPS):
            return False
        for group in GROUPS:
            item = files[group]
            if not isinstance(item, dict) or not re.fullmatch(r"[0-9a-f]{64}", item.get("sha256", "")):
                return False
            path = _selected(database_dir, group)
            if path is None or path.name != item.get("name"):
                return False
            with os.fdopen(_open_regular(path, limit=MAX_DATABASE_BYTES), "rb") as handle:
                meta = _metadata(os.fstat(handle.fileno()))
                if any(type(item.get(k)) is not int or item[k] != v for k, v in meta.items()):
                    return False
                if _header_version(handle.read(512)) != versions[group] or meta != _metadata(path.lstat()):
                    return False
        return snapshot.get("generation") == _generation(versions, files)
    except (KeyError, TypeError, ValueError, OSError):
        return False


def verify_snapshot(database_dir: Path, snapshot: dict) -> None:
    """Authenticate CVD/CLD with the pinned engine in a bounded isolated child."""
    if not disk_matches(database_dir, snapshot):
        raise VendorError("database-changed")
    try:
        result = subprocess.run(
            [sys.executable, "-P", "-m", "personal_assistant.clamav_verify", str(database_dir)],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            timeout=120, check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise VendorError("database-verification-timeout") from exc
    except OSError as exc:
        raise VendorError("database-verifier-unavailable") from exc
    if result.returncode:
        raise VendorError("database-signature-invalid")
    if not disk_matches(database_dir, snapshot):
        raise VendorError("database-changed")


def publish_status(database_dir: Path, status: dict) -> None:
    if database_dir.is_symlink() or not database_dir.is_dir():
        raise VendorError("database-invalid")
    data = json.dumps({**status, "schema": 1}, sort_keys=True, allow_nan=False).encode() + b"\n"
    if len(data) > MAX_EVIDENCE_BYTES:
        raise VendorError("vendor-proof-oversized")
    fd, temporary = tempfile.mkstemp(prefix=".openclaw-vendor-", dir=database_dir)
    try:
        with os.fdopen(fd, "wb") as handle:
            os.fchmod(handle.fileno(), 0o640)
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, database_dir / STATUS_NAME)
        directory = os.open(database_dir, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        Path(temporary).unlink(missing_ok=True)


def read_status(database_dir: Path) -> dict:
    try:
        if database_dir.is_symlink():
            raise VendorError("vendor-proof-invalid")
        with os.fdopen(_open_regular(database_dir / STATUS_NAME, limit=MAX_EVIDENCE_BYTES), "rb") as handle:
            payload = handle.read(MAX_EVIDENCE_BYTES + 1)
        if len(payload) > MAX_EVIDENCE_BYTES:
            raise VendorError("vendor-proof-invalid")
        result = json.loads(payload)
        if not isinstance(result, dict) or type(result.get("schema")) is not int or result["schema"] != 1:
            raise VendorError("vendor-proof-invalid")
        if result.get("state") not in {"current", "failed", "updating", "update-required"}:
            raise VendorError("vendor-proof-invalid")
        return result
    except (OSError, ValueError, TypeError, RecursionError) as exc:
        raise VendorError("vendor-proof-unavailable") from exc


def vendor_readiness(database_dir: Path, *, now: float | None = None) -> dict:
    checked_now = time.time() if now is None else now
    try:
        proof = read_status(database_dir)
        report = {key: proof[key] for key in (
            "state", "source", "checked_at", "record_timestamp", "versions", "database",
        ) if key in proof}
        error = proof.get("error")
        if isinstance(error, str) and error in ERROR_CODES:
            report["error"] = error
        reason = ""
        if not vendor_observation_valid(proof, checked_now):
            reason = "vendor-proof-stale-or-invalid"
        elif proof["state"] != "current":
            reason = "vendor-" + proof["state"]
        elif not isinstance(proof.get("database"), dict) or not disk_matches(database_dir, proof["database"]):
            reason = "vendor-database-changed"
        elif proof["versions"] != proof["database"].get("versions"):
            reason = "vendor-version-mismatch"
        elif read_status(database_dir) != proof:
            reason = "vendor-proof-changed"
        report.update(ok=not reason, reasons=[reason] if reason else [])
        report["generation"] = proof.get("database", {}).get("generation", "") if not reason else ""
        report["proof_max_age_seconds"] = PROOF_TTL_SECONDS
        return report
    except (VendorError, TypeError, AttributeError):
        return {"ok": False, "state": "unknown", "reasons": ["vendor-proof-unavailable"], "generation": ""}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", required=True)
    parser.parse_args(argv)
    try:
        observation = query_vendor()
        database = database_snapshot(Path(os.environ.get("CLAMAV_DATABASE_DIR", "/var/lib/clamav")))
        if not disk_matches(Path(os.environ.get("CLAMAV_DATABASE_DIR", "/var/lib/clamav")), database):
            raise VendorError("database-changed")
        verify_snapshot(Path(os.environ.get("CLAMAV_DATABASE_DIR", "/var/lib/clamav")), database)
        if not vendor_observation_valid(observation, time.time()):
            raise VendorError("vendor-proof-stale-or-invalid")
        equal = database["versions"] == observation["versions"]
        report = {**observation, "database": database, "generation": database["generation"],
                  "ok": equal, "read_only": True, "state": "current" if equal else "update-required"}
    except (OSError, VendorError) as exc:
        report = {"ok": False, "read_only": True, "state": "unknown",
                  "error": exc.code if isinstance(exc, VendorError) else "vendor-check-failed"}
    print(json.dumps(report, sort_keys=True))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
