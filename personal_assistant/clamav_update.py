"""Single writer for signed ClamAV databases and vendor currentness evidence."""

import argparse
import fcntl
import os
import stat
import subprocess
import time
from contextlib import ExitStack
from pathlib import Path

from .clamav_vendor import (
    GROUPS,
    POLL_SECONDS,
    VendorError,
    database_snapshot,
    disk_matches,
    publish_status,
    query_vendor,
    read_status,
    vendor_observation_valid,
    verify_snapshot,
)

UPDATER_UID = 100
READER_GID = 101
LEGACY_DATABASE_GID = 102
DEFAULT_MAINTENANCE_INTERVAL = 14400
OBSERVATION_FIELDS = ("source", "checked_at", "record_timestamp", "versions")


def download(database: Path, lock_fd: int) -> None:
    # FreshClam owns signature verification and its persisted CDN cooldown.
    try:
        result = subprocess.run(
            ["freshclam", f"--datadir={database}"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=600,
            check=False,
            pass_fds=(lock_fd,),
        )
    except subprocess.TimeoutExpired as exc:
        raise VendorError("freshclam-timeout") from exc
    if result.returncode:
        raise VendorError("freshclam-failed")


def _regressed(versions: dict, previous: dict) -> bool:
    return any(versions[group] < value for group, value in previous.items())


def _previous_status(database: Path) -> dict:
    try:
        prior = read_status(database)
    except VendorError:
        return {}
    return prior if vendor_observation_valid(prior, prior.get("checked_at", 0.0)) else {}


def ensure_reader_access(database: Path, snapshot: dict) -> bool:
    """Grant the scanner reader group access only to the three authenticated, inode-bound files."""
    with ExitStack() as stack:
        changes = []
        for group in GROUPS:
            item = snapshot["files"][group]
            if item["name"] not in (f"{group}.cld", f"{group}.cvd"):
                raise VendorError("database-invalid")
            fd = os.open(database / item["name"], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            stack.callback(os.close, fd)
            info = os.fstat(fd)
            if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != UPDATER_UID
                    or info.st_gid not in (READER_GID, LEGACY_DATABASE_GID)):
                raise VendorError("database-invalid")
            expected = (item["device"], item["inode"], item["ctime_ns"])
            if (info.st_dev, info.st_ino, info.st_ctime_ns) != expected:
                raise VendorError("database-changed")
            if info.st_gid == READER_GID and info.st_mode & stat.S_IRGRP:
                continue
            if info.st_gid == LEGACY_DATABASE_GID and info.st_mode & stat.S_IROTH:
                # Already readable by the consumer, even with the original package group.
                continue
            changes.append((fd, info))
        for fd, info in changes:
            if info.st_gid != READER_GID:
                # Never transfer old group write/execute privileges to scan consumers.
                os.fchmod(fd, stat.S_IMODE(info.st_mode) & ~stat.S_IRWXG)
                os.fchown(fd, -1, READER_GID)
            os.fchmod(fd, stat.S_IMODE(os.fstat(fd).st_mode) | stat.S_IRGRP)
        return bool(changes)


def _update(database: Path, maintenance: bool, lock_fd: int) -> bool:
    prior = _previous_status(database)
    previous_observation = {key: prior[key] for key in OBSERVATION_FIELDS if key in prior}
    retry = prior.get("state") in {"updating", "update-required"} or prior.get("retry_required") is True
    observation = {}
    try:
        observation = query_vendor()
        if prior.get("versions") and _regressed(observation["versions"], prior["versions"]):
            observation = previous_observation
            raise VendorError("vendor-version-regression")
        revoked = prior.get("state") == "current" and prior["versions"] != observation["versions"]
        if revoked:
            publish_status(database, {**observation, "state": "update-required"})
        previous_disk = prior.get("database", {})
        verified = prior.get("state") == "current" and isinstance(previous_disk, dict)
        verified = verified and disk_matches(database, previous_disk)
        disk = previous_disk
        if maintenance or not verified:
            disk = database_snapshot(database, allow_missing=True)
            verified = verified and disk["generation"] == previous_disk["generation"]
        versions = disk["versions"]
        if _regressed(observation["versions"], versions):
            raise VendorError("local-version-ahead")
        if not revoked and versions != observation["versions"]:
            publish_status(database, {**observation, "state": "update-required"})
        if versions != observation["versions"] or not verified or retry:
            retry = True
            for attempt in range(2):
                requested = observation["versions"]
                publish_status(database, {**observation, "state": "updating"})
                download(database, lock_fd)
                disk = database_snapshot(database)
                verify_snapshot(database, disk)
                latest = query_vendor()
                if _regressed(latest["versions"], requested):
                    raise VendorError("vendor-version-regression")
                observation = latest
                if disk["versions"] == observation["versions"]:
                    break
                if _regressed(observation["versions"], disk["versions"]):
                    raise VendorError("local-version-ahead")
                if requested == observation["versions"] or attempt == 1:
                    raise VendorError("database-version-mismatch")
                publish_status(database, {**observation, "state": "update-required"})
        if ensure_reader_access(database, disk):
            previous_generation = disk["generation"]
            disk = database_snapshot(database)
            if disk["generation"] != previous_generation:
                raise VendorError("database-changed")
            verify_snapshot(database, disk)
        publish_status(database, {**observation, "state": "current", "database": disk})
        return True
    except (OSError, VendorError) as exc:
        # Keep the last observed version floor across failed probes.
        code = exc.code if isinstance(exc, VendorError) else "updater-io-error"
        publish_status(
            database,
            {
                **(observation or previous_observation),
                "state": "failed",
                "error": code,
                "retry_required": retry,
            },
        )
        return False


def run_once(database: Path, *, maintenance: bool = False) -> bool:
    directory = os.open(database, os.O_DIRECTORY | os.O_RDONLY | os.O_NOFOLLOW)
    try:
        fd = os.open("openclaw-updater.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600, dir_fd=directory)
    finally:
        os.close(directory)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise VendorError("updater-lock-invalid")
        fcntl.flock(fd, fcntl.LOCK_EX)
        return _update(database, maintenance, fd)
    finally:
        os.close(fd)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    database = Path(os.environ.get("CLAMAV_DATABASE_DIR", "/var/lib/clamav"))
    try:
        interval = int(os.environ.get("CLAMAV_UPDATE_INTERVAL_SECONDS", DEFAULT_MAINTENANCE_INTERVAL))
        if interval < POLL_SECONDS:
            raise ValueError()
    except ValueError:
        print("updater-interval-invalid", flush=True)
        return 2
    due = time.monotonic() + interval
    while True:
        started = time.monotonic()
        periodic = started >= due
        try:
            ok = run_once(database, maintenance=periodic)
        except (OSError, VendorError):
            ok = False
        if not ok:
            print("clamav-updater-pass-failed", flush=True)
        if args.once:
            return int(not ok)
        if periodic:
            due = time.monotonic() + interval
        time.sleep(max(0, started + POLL_SECONDS - time.monotonic()))


if __name__ == "__main__":
    raise SystemExit(main())
