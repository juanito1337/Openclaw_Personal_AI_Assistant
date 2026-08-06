from __future__ import annotations

import logging
import subprocess
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

from .telemetry import command_category


@dataclass(slots=True)
class CommandResult:
    args: list[str]
    returncode: int
    stdout: str = ""
    stderr: str = ""

    @property
    def ok(self) -> bool:
        return self.returncode == 0

    @property
    def combined(self) -> str:
        return (self.stdout + "\n" + self.stderr).strip()


@dataclass(slots=True)
class BinaryCommandResult:
    args: list[str]
    returncode: int
    stdout: bytes = b""
    stderr: bytes = b""

    @property
    def ok(self) -> bool:
        return self.returncode == 0

    @property
    def error_text(self) -> str:
        return self.stderr.decode("utf-8", errors="replace")


class CommandRunner:
    def __init__(
        self,
        timeout_seconds: int = 90,
        observer: Callable[[Mapping[str, object]], None] | None = None,
    ) -> None:
        self.timeout_seconds = timeout_seconds
        self.observer = observer
        self.log = logging.getLogger(__name__)

    def _observe(
        self,
        command: list[str],
        *,
        started: float,
        returncode: int,
        stdout_size: int,
        stderr_size: int,
    ) -> None:
        if self.observer is None:
            return
        event = {
            "category": command_category(command),
            "duration_ms": round((time.perf_counter() - started) * 1000.0, 3),
            "returncode": returncode,
            "ok": returncode == 0,
            "timeout": returncode == 124,
            "stdout_bytes": max(0, stdout_size),
            "stderr_bytes": max(0, stderr_size),
        }
        try:
            self.observer(event)
        except Exception as exc:  # Observability must not change command behaviour.
            self.log.debug("Kommando-Telemetrie fehlgeschlagen: %s", exc)

    def run(
        self,
        args: Iterable[str],
        *,
        input_text: str | None = None,
        env: dict[str, str] | None = None,
        timeout: int | None = None,
        cwd: str | Path | None = None,
    ) -> CommandResult:
        command = [str(item) for item in args]
        self.log.debug("Kommando: %s", command)
        started = time.perf_counter()
        try:
            completed = subprocess.run(
                command,
                input=input_text,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=env,
                cwd=str(cwd) if cwd is not None else None,
                timeout=timeout or self.timeout_seconds,
                check=False,
            )
            self._observe(
                command,
                started=started,
                returncode=completed.returncode,
                stdout_size=len(completed.stdout.encode("utf-8", errors="replace")),
                stderr_size=len(completed.stderr.encode("utf-8", errors="replace")),
            )
            return CommandResult(command, completed.returncode, completed.stdout, completed.stderr)
        except FileNotFoundError as exc:
            detail = str(exc)
            self._observe(command, started=started, returncode=127, stdout_size=0, stderr_size=len(detail.encode()))
            return CommandResult(command, 127, "", detail)
        except subprocess.TimeoutExpired as exc:
            stdout = exc.stdout if isinstance(exc.stdout, str) else ""
            stderr = exc.stderr if isinstance(exc.stderr, str) else ""
            stderr = stderr or "Kommando-Timeout"
            self._observe(
                command,
                started=started,
                returncode=124,
                stdout_size=len(stdout.encode("utf-8", errors="replace")),
                stderr_size=len(stderr.encode("utf-8", errors="replace")),
            )
            return CommandResult(command, 124, stdout, stderr)

    def run_bytes(
        self,
        args: Iterable[str],
        *,
        timeout: int | None = None,
        cwd: str | Path | None = None,
    ) -> BinaryCommandResult:
        command = [str(item) for item in args]
        self.log.debug("Binaerkommando: %s", command)
        started = time.perf_counter()
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                text=False,
                cwd=str(cwd) if cwd is not None else None,
                timeout=timeout or self.timeout_seconds,
                check=False,
            )
            self._observe(
                command,
                started=started,
                returncode=completed.returncode,
                stdout_size=len(completed.stdout),
                stderr_size=len(completed.stderr),
            )
            return BinaryCommandResult(command, completed.returncode, completed.stdout, completed.stderr)
        except FileNotFoundError as exc:
            detail = str(exc).encode()
            self._observe(command, started=started, returncode=127, stdout_size=0, stderr_size=len(detail))
            return BinaryCommandResult(command, 127, b"", detail)
        except subprocess.TimeoutExpired as exc:
            stdout = exc.stdout if isinstance(exc.stdout, bytes) else b""
            stderr = exc.stderr if isinstance(exc.stderr, bytes) else b""
            stderr = stderr or b"Kommando-Timeout"
            self._observe(
                command,
                started=started,
                returncode=124,
                stdout_size=len(stdout),
                stderr_size=len(stderr),
            )
            return BinaryCommandResult(command, 124, stdout, stderr)
