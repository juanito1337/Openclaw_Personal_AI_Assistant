"""Exercise the immutable recovery image on a private copy of the selected public signatures."""

import re
import signal
import subprocess
import sys
import time
import uuid

SEED = """
import os, stat
from pathlib import Path
from personal_assistant.clamav_vendor import database_snapshot, disk_matches
source, target = Path('/source-signatures'), Path('/var/lib/clamav')
snapshot = database_snapshot(source)
def copy_file(name, limit):
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
    with os.fdopen(os.open(source / name, flags), 'rb') as src, (target / name).open('xb') as dst:
        meta = os.fstat(src.fileno())
        if not stat.S_ISREG(meta.st_mode) or not 0 < meta.st_size <= limit:
            raise ValueError('invalid-source')
        remaining = meta.st_size
        while remaining:
            chunk = src.read(min(remaining, 1024 * 1024))
            if not chunk:
                raise ValueError('source-changed')
            dst.write(chunk)
            remaining -= len(chunk)
        if src.read(1) or meta.st_mtime_ns != (source / name).lstat().st_mtime_ns:
            raise ValueError('source-changed')
        os.fchmod(dst.fileno(), 0o640)
for item in snapshot['files'].values():
    copy_file(item['name'], 1024**3)
# Honor the real FreshClam server cooldown even on the private copy.
if os.path.lexists(source / 'freshclam.dat'):
    copy_file('freshclam.dat', 1024**2)
if not disk_matches(source, snapshot) or database_snapshot(target)['generation'] != snapshot['generation']:
    raise ValueError('source-changed')
"""
COMPARE = """
from pathlib import Path
from personal_assistant.clamav_vendor import database_snapshot, vendor_readiness
status = vendor_readiness(Path('/var/lib/clamav'))
live = database_snapshot(Path('/source-signatures'))
raise SystemExit(0 if status['ok'] and status['generation'] == live['generation'] else 1)
"""


def docker(*args: str, timeout: int = 60) -> str:
    try:
        result = subprocess.run(
            ["docker", *args], capture_output=True, timeout=timeout, check=True, text=True
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError("recovery-docker-error") from exc
    return result.stdout.strip()


def check(image: str, source: str) -> None:
    token = "openclaw-clamav-recovery-" + uuid.uuid4().hex
    label = "org.openclaw.clamav-recovery=" + token
    volume = docker("volume", "create", "--label", label)
    if not re.fullmatch(r"[a-f0-9]{64}", volume):
        raise RuntimeError("recovery-volume-invalid")
    private = f"type=volume,src={volume},dst=/var/lib/clamav,volume-nocopy"
    live = f"type=volume,src={source},dst=/source-signatures,readonly,volume-nocopy"
    common = [
        "--read-only",
        "--label",
        label,
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges:true",
        "--pids-limit",
        "128",
        "--memory",
        "2g",
        "--cpus",
        "2",
        "--tmpfs",
        "/tmp:rw,nosuid,nodev,noexec,size=128m,mode=1777",
    ]
    scanner = token + "-scanner"
    stage = "private-volume"
    try:
        # Initialize only the fresh anonymous volume with the narrow CHOWN capability.
        docker(
            "run",
            "--rm",
            *common,
            "--network",
            "none",
            "--user",
            "0:0",
            "--cap-add",
            "CHOWN",
            "--mount",
            private,
            "--entrypoint",
            "chown",
            image,
            "100:101",
            "/var/lib/clamav",
        )
        stage = "signature-copy"
        docker(
            "run",
            "--rm",
            *common,
            "--network",
            "none",
            "--user",
            "100:101",
            "--mount",
            live,
            "--mount",
            private,
            "--entrypoint",
            "python3",
            image,
            "-P",
            "-c",
            SEED,
            timeout=180,
        )
        stage = "signature-bootstrap"
        docker(
            "run",
            "--rm",
            *common,
            "--network",
            "bridge",
            "--user",
            "100:101",
            "--mount",
            private,
            "--tmpfs",
            "/var/log/clamav:rw,nosuid,nodev,noexec,size=16m,uid=100,gid=101,mode=0750",
            image,
            "--once",
            timeout=1300,
        )
        stage = "scanner-start"
        docker(
            "run",
            "-d",
            "--name",
            scanner,
            *common,
            "--network",
            "none",
            "--user",
            "100:101",
            "--mount",
            private + ",readonly",
            "--tmpfs",
            "/run/clamav:rw,nosuid,nodev,noexec,size=4m,uid=100,gid=101,mode=0770",
            "--entrypoint",
            "/sbin/tini",
            image,
            "--",
            "/opt/openclaw-agent/docker/clamd.sh",
        )
        stage = "scanner-readiness"
        deadline = time.monotonic() + 300
        while time.monotonic() < deadline:
            if docker("inspect", "--format", "{{.State.Running}}", scanner) != "true":
                raise RuntimeError("scanner-exited")
            try:
                docker("exec", scanner, "python3", "-P", "-m", "personal_assistant.clamd_health", timeout=12)
            except RuntimeError:
                time.sleep(2)
            else:
                break
        else:
            raise RuntimeError("scanner-timeout")
        stage = "source-generation"
        docker(
            "run",
            "--rm",
            *common,
            "--network",
            "none",
            "--user",
            "100:101",
            "--mount",
            live,
            "--mount",
            private + ",readonly",
            "--entrypoint",
            "python3",
            image,
            "-P",
            "-c",
            COMPARE,
            timeout=180,
        )
    except RuntimeError as exc:
        raise RuntimeError("recovery-" + stage + "-failed") from exc
    finally:
        # A timed-out docker client can leave its one-shot container running.
        containers = docker("ps", "-aq", "--no-trunc", "--filter", "label=" + label).split()
        for identifier in containers:
            if not re.fullmatch(r"[a-f0-9]{64}", identifier):
                raise RuntimeError("recovery-container-invalid")
            docker("rm", "-f", identifier)
        docker("volume", "rm", volume)


def terminate(signum, frame):
    raise KeyboardInterrupt


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, terminate)
    try:
        check(sys.argv[1], sys.argv[2])
    except RuntimeError as error:
        sys.exit(str(error))
    except KeyboardInterrupt:
        sys.exit("recovery-interrupted")
