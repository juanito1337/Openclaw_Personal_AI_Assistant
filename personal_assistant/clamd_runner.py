"""Supervise the foreground scanner and attest one stable database load."""
from __future__ import annotations

import json
import os
import signal
import socket
import stat
import struct
import subprocess
import tempfile
import threading
import time
import uuid
from pathlib import Path

from .clamav_vendor import vendor_readiness
from .clamd_client import ClamdClientError, ClamdUnixClient
from .clamd_health import RECEIPT, socket_identity


def publish(path: Path, receipt: dict) -> None:
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as output:
        temp = Path(output.name)
        try:
            output.write(json.dumps(receipt).encode())
            output.flush()
            os.fchmod(output.fileno(), 0o640)
            os.replace(temp, path)
        finally:
            temp.unlink(missing_ok=True)


def verify_child(path: Path, pid: int) -> None:
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as peer:
        peer.settimeout(2)
        peer.connect(str(path))
        credentials = peer.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
        if struct.unpack("3i", credentials)[0] != pid:
            raise ValueError("resident-child-mismatch")


def supervise(database: Path, path: Path, stop: threading.Event, scan_timeout: int) -> int:
    receipt = path.with_name(RECEIPT)
    client = ClamdUnixClient(path, timeout_seconds=2, max_stream_bytes=1024)
    while not stop.is_set():
        receipt.unlink(missing_ok=True)
        initial = vendor_readiness(database)
        if not initial["ok"]:
            stop.wait(2)
            continue
        if path.exists() or path.is_symlink():
            if not stat.S_ISSOCK(path.lstat().st_mode):
                raise ValueError("socket-invalid")
            path.unlink()
        child = subprocess.Popen(["/usr/sbin/clamd", "--config-file=/tmp/clamd.conf"])
        instance = uuid.uuid4().hex
        startup_deadline = time.monotonic() + 300
        loaded = False
        try:
            while not stop.is_set():
                if child.poll() is not None:
                    return 1
                latest = vendor_readiness(database)
                if not latest["ok"] or latest["database"] != initial["database"]:
                    break
                try:
                    sock = socket_identity(path)
                    verify_child(path, child.pid)
                    client.ping()
                    version = client.version()
                    latest = vendor_readiness(database)
                    if (not latest["ok"] or latest["database"] != initial["database"]
                            or child.poll() is not None or sock != socket_identity(path)
                            or version.split("/")[1] != str(initial["versions"]["daily"])):
                        break
                    publish(receipt, {"schema": 1, "socket": sock, "instance": instance,
                                      "version": version, "versions": initial["versions"],
                                      "generation": initial["generation"], "checked_at": time.time()})
                    loaded = True
                except (OSError, ValueError, ClamdClientError):
                    receipt.unlink(missing_ok=True)
                    if loaded or time.monotonic() >= startup_deadline:
                        break
                stop.wait(2)
        finally:
            receipt.unlink(missing_ok=True)
            if child.poll() is None:
                child.terminate()
                try:
                    child.wait(timeout=scan_timeout + 5)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait(timeout=5)
    return 0


def main() -> int:
    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())
    return supervise(Path("/var/lib/clamav"), Path("/run/clamav/clamd.sock"), stop,
                     int(os.environ.get("CLAMAV_SCAN_TIMEOUT_SECONDS", "120")))


if __name__ == "__main__":
    raise SystemExit(main())
