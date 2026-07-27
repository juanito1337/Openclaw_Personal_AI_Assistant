from __future__ import annotations

import errno
import os
from pathlib import Path
from types import TracebackType
from typing import IO, Any


class ProcessLockError(RuntimeError):
    pass


def _read_pid(handle: IO[str]) -> int | None:
    try:
        handle.seek(0)
        value = handle.read().strip()
        return int(value) if value else None
    except (OSError, ValueError):
        return None


def inspect_process_lock(path: Path) -> dict[str, Any]:
    """Inspect the real advisory lock without deleting or modifying its file."""

    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        handle = path.open("a+", encoding="utf-8")
    except OSError as exc:
        return {
            "ok": False,
            "locked": True,
            "pid": None,
            "process_alive": None,
            "path": str(path),
            "detail": f"Prozesssperre konnte nicht geoeffnet werden: {exc}",
        }

    with handle:
        pid = _read_pid(handle)
        try:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except ImportError:
            return {
                "ok": False,
                "locked": True,
                "pid": pid,
                "process_alive": None,
                "path": str(path),
                "detail": "Prozesssperren werden auf diesem Betriebssystem nicht unterstuetzt",
            }
        except OSError as exc:
            if exc.errno not in {errno.EACCES, errno.EAGAIN}:
                return {
                    "ok": False,
                    "locked": True,
                    "pid": pid,
                    "process_alive": None,
                    "path": str(path),
                    "detail": f"Prozesssperre konnte nicht geprueft werden: {exc}",
                }
            alive = bool(pid and Path(f"/proc/{pid}").exists())
            return {
                "ok": True,
                "locked": True,
                "pid": pid,
                "process_alive": alive,
                "path": str(path),
                "detail": (
                    f"Prozesssperre wird von PID {pid} gehalten"
                    if pid
                    else "Prozesssperre wird von einem anderen Prozess gehalten"
                ),
            }
        else:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            finally:
                pass
            return {
                "ok": True,
                "locked": False,
                "pid": pid,
                "process_alive": bool(pid and Path(f"/proc/{pid}").exists()),
                "path": str(path),
                "detail": "Keine aktive Prozesssperre",
            }


class ProcessLock:
    """Non-blocking process lock to prevent duplicate external mail actions."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.handle: IO[str] | None = None

    def __enter__(self) -> ProcessLock:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = self.path.open("a+", encoding="utf-8")
        try:
            import fcntl

            fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except ImportError as exc:
            self.handle.close()
            self.handle = None
            raise ProcessLockError("Prozesssperren werden auf diesem Betriebssystem nicht unterstuetzt") from exc
        except OSError as exc:
            self.handle.close()
            self.handle = None
            if exc.errno in {errno.EACCES, errno.EAGAIN}:
                raise ProcessLockError(f"Ein anderer Mail-Interface-Lauf ist bereits aktiv: {self.path}") from exc
            raise ProcessLockError(f"Prozesssperre konnte nicht gesetzt werden: {exc}") from exc
        self.handle.seek(0)
        self.handle.truncate()
        self.handle.write(str(os.getpid()))
        self.handle.flush()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if self.handle is None:
            return
        try:
            import fcntl

            fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
        finally:
            self.handle.close()
            self.handle = None
