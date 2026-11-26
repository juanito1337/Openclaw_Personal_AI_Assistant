"""Shared limits and watchdog for mail release validation."""

from __future__ import annotations

import os
import re
import signal
import subprocess
from collections.abc import Mapping, Sequence
from contextlib import suppress

RECOVERY_RUNTIME_ARGS = ("--max-runtime", "900", "--shutdown-reserve", "60")
RECOVERY_WATCHDOG_SECONDS = 960
READINESS_TIMEOUT = "mail-recovery-readiness-timeout"
DRY_RUN_TIMEOUT = "mail-recovery-dry-run-timeout"
RECHECK_TIMEOUT = "mail-recovery-recheck-timeout"
FINGERPRINT_UNVERIFIED = "mail-recovery-fingerprint-unverified"


def recovery_fingerprint(gate: object) -> str | None:
    if isinstance(gate, dict):
        fingerprint = gate.get("current_fingerprint")
        if isinstance(fingerprint, str) and re.fullmatch(r"[0-9a-f]{64}", fingerprint):
            return fingerprint
    return None


def start_recovery_capture(
    command: Sequence[str], *, environment: Mapping[str, str] | None = None,
) -> subprocess.Popen[str]:
    return subprocess.Popen(
        list(command),
        env=environment,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )


def finish_recovery_capture(process: subprocess.Popen[str], timeout: int) -> subprocess.CompletedProcess[str]:
    try:
        stdout, stderr = process.communicate(timeout=timeout)
        returncode = process.returncode
        assert returncode is not None
        return subprocess.CompletedProcess(process.args, returncode, stdout, stderr)
    except subprocess.TimeoutExpired:
        # Kill the isolated command group and close our pipe ends. Waiting for
        # pipe EOF can block if a descendant still owns a write descriptor.
        with suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
        with suppress(subprocess.TimeoutExpired):
            process.wait(timeout=5)
        return subprocess.CompletedProcess(process.args, 124, "", "")
    finally:
        if process.stdout is not None:
            process.stdout.close()
        if process.stderr is not None:
            process.stderr.close()
