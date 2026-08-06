from __future__ import annotations

import hashlib
import os
import shutil
import sqlite3
import subprocess
import tempfile
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from .config import WORKSPACE_ROOT
from .tool_settings import AntivirusToolSettings

DEFAULT_ANTIVIRUS_DB = WORKSPACE_ROOT / "personal_assistant/data/antivirus.sqlite3"


def _default_antivirus_database() -> Path:
    root = os.environ.get("OPENCLAW_SECURITY_DATA_DIR")
    return Path(root).expanduser().resolve() / "antivirus.sqlite3" if root else DEFAULT_ANTIVIRUS_DB


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _parse_time(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC)
    except (TypeError, ValueError):
        return None


@dataclass(slots=True, frozen=True)
class AntivirusResult:
    status: str
    sha256: str
    size_bytes: int
    source_type: str
    name: str
    scanner: str
    scanner_identity: str
    signature: str = ""
    detail: str = ""
    duration_ms: float = 0.0
    cached: bool = False

    @property
    def clean(self) -> bool:
        return self.status == "clean"

    @property
    def infected(self) -> bool:
        return self.status == "infected"

    @property
    def error(self) -> bool:
        return self.status == "error"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class AntivirusStore:
    def __init__(self, path: Path = DEFAULT_ANTIVIRUS_DB) -> None:
        self.path = path.expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA busy_timeout=5000")
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS scans (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sha256 TEXT NOT NULL,
                scanner_identity TEXT NOT NULL,
                status TEXT NOT NULL,
                signature TEXT,
                detail TEXT,
                size_bytes INTEGER NOT NULL,
                source_type TEXT,
                name TEXT,
                duration_ms REAL NOT NULL DEFAULT 0,
                scanned_at TEXT NOT NULL,
                UNIQUE(sha256, scanner_identity)
            );
            CREATE INDEX IF NOT EXISTS idx_antivirus_scans_time ON scans(scanned_at);
            CREATE INDEX IF NOT EXISTS idx_antivirus_scans_status ON scans(status);
            """
        )
        self.connection.commit()

    def close(self) -> None:
        self.connection.close()

    def get(self, sha256: str, scanner_identity: str) -> sqlite3.Row | None:
        return self.connection.execute(
            "SELECT * FROM scans WHERE sha256=? AND scanner_identity=?",
            (sha256, scanner_identity),
        ).fetchone()

    def put(self, result: AntivirusResult) -> None:
        self.connection.execute(
            """
            INSERT INTO scans(
                sha256,scanner_identity,status,signature,detail,size_bytes,
                source_type,name,duration_ms,scanned_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(sha256,scanner_identity) DO UPDATE SET
                status=excluded.status,signature=excluded.signature,detail=excluded.detail,
                size_bytes=excluded.size_bytes,source_type=excluded.source_type,
                name=excluded.name,duration_ms=excluded.duration_ms,scanned_at=excluded.scanned_at
            """,
            (
                result.sha256,
                result.scanner_identity,
                result.status,
                result.signature,
                result.detail[:4000],
                result.size_bytes,
                result.source_type,
                result.name[:500],
                float(result.duration_ms),
                _now_iso(),
            ),
        )
        self.connection.commit()

    def summary(self, *, days: int = 7) -> dict[str, Any]:
        since = (datetime.now(UTC) - timedelta(days=max(1, int(days)))).isoformat()
        rows = self.connection.execute(
            "SELECT status,COUNT(*) AS count FROM scans WHERE scanned_at>=? GROUP BY status",
            (since,),
        ).fetchall()
        latest = self.connection.execute(
            "SELECT scanned_at,status,signature,name FROM scans ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return {
            "database": str(self.path),
            "counts": {str(row["status"]): int(row["count"]) for row in rows},
            "latest": dict(latest) if latest else None,
        }


class HostAntivirus:
    """Fail-closed host antivirus adapter.

    clamdscan is preferred because the daemon keeps signatures resident in memory.
    Each request is still initiated by the Personal Assistant. A standalone
    clamscan fallback is optional and is used only when the daemon client fails.
    """

    def __init__(
        self,
        settings: AntivirusToolSettings,
        *,
        database: Path | None = None,
        runner: Any | None = None,
    ) -> None:
        self.settings = settings
        self.store = AntivirusStore(database or _default_antivirus_database())
        self._runner = runner
        self._identity: str | None = None

    def close(self) -> None:
        self.store.close()

    @staticmethod
    def _systemd_unit(unit: str) -> dict[str, Any]:
        if shutil.which("systemctl") is None:
            return {"available": False, "unit": unit}
        try:
            result = subprocess.run(
                [
                    "systemctl", "show", unit, "--no-pager",
                    "--property=LoadState,ActiveState,SubState,UnitFileState,Result",
                ],
                capture_output=True,
                text=True,
                timeout=8,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return {"available": False, "unit": unit, "error": str(exc)}
        values: dict[str, Any] = {"available": result.returncode == 0, "unit": unit}
        for line in result.stdout.splitlines():
            if "=" in line:
                key, value = line.split("=", 1)
                values[key] = value
        if result.stderr.strip():
            values["error"] = result.stderr.strip()[:500]
        return values

    def _run(self, args: list[str], *, input_bytes: bytes | None = None, timeout: int | None = None) -> subprocess.CompletedProcess[bytes]:
        if self._runner is not None:
            return self._runner(args, input_bytes=input_bytes, timeout=timeout)
        return subprocess.run(
            args,
            input=input_bytes,
            capture_output=True,
            text=False,
            timeout=timeout or self.settings.timeout_seconds,
            check=False,
        )

    def scanner_identity(self, *, refresh: bool = False) -> str:
        if self._identity and not refresh:
            return self._identity
        candidates = [self.settings.binary]
        if self.settings.allow_standalone_fallback:
            candidates.append(self.settings.fallback_binary)
        parts: list[str] = []
        for binary in dict.fromkeys(candidates):
            if not binary:
                continue
            try:
                result = self._run([binary, "--version"], timeout=15)
                output = (result.stdout + b"\n" + result.stderr).decode("utf-8", errors="replace").strip()
                if result.returncode == 0 and output:
                    parts.append(f"{binary}:{output.splitlines()[0][:300]}")
            except (OSError, subprocess.TimeoutExpired):
                continue
        self._identity = " | ".join(parts) or "clamav:unavailable"
        return self._identity

    def doctor(self, *, live_scan: bool = True) -> dict[str, Any]:
        identity = self.scanner_identity(refresh=True)
        daemon = self._systemd_unit(self.settings.daemon_service)
        freshclam = self._systemd_unit(self.settings.freshclam_service)
        binary = shutil.which(self.settings.binary)
        fallback = shutil.which(self.settings.fallback_binary) if self.settings.allow_standalone_fallback else None
        result: dict[str, Any] = {
            "ok": False,
            "enabled": self.settings.enabled,
            "fail_closed": self.settings.fail_closed,
            "scan_raw_mail": self.settings.scan_raw_mail,
            "scan_attachments": self.settings.scan_attachments,
            "binary": binary or "",
            "fallback_binary": fallback or "",
            "scanner_identity": identity,
            "daemon": daemon,
            "freshclam": freshclam,
            "cache": self.store.summary(days=7),
        }
        if not self.settings.enabled:
            result.update({"ok": True, "detail": "Virenscanner ist deaktiviert"})
            return result
        if not binary and not fallback:
            result["detail"] = "Weder clamdscan noch clamscan ist installiert"
            return result
        daemon_ok = daemon.get("ActiveState") == "active"
        if binary and not daemon_ok and not fallback:
            result["detail"] = "clamdscan ist vorhanden, aber clamav-daemon ist nicht aktiv"
            return result
        if live_scan:
            scan = self.scan_bytes(b"Personal Assistant antivirus health check\n", name="health-check.txt", source_type="health", use_cache=False)
            result["live_scan"] = scan.to_dict()
            result["ok"] = scan.clean
            result["detail"] = "Scan erfolgreich" if scan.clean else scan.detail or scan.status
        else:
            result["ok"] = bool(binary and daemon_ok) or bool(fallback)
            result["detail"] = "Scanner verfuegbar" if result["ok"] else "Scanner nicht verfuegbar"
        return result

    def _cached(self, sha256: str, identity: str, *, name: str, source_type: str, size: int) -> AntivirusResult | None:
        row = self.store.get(sha256, identity)
        if row is None:
            return None
        scanned = _parse_time(str(row["scanned_at"] or ""))
        if scanned is None:
            return None
        age = datetime.now(UTC) - scanned
        if age.total_seconds() > self.settings.cache_hours * 3600:
            return None
        return AntivirusResult(
            status=str(row["status"]),
            sha256=sha256,
            size_bytes=size,
            source_type=source_type,
            name=name,
            scanner="cache",
            scanner_identity=identity,
            signature=str(row["signature"] or ""),
            detail=str(row["detail"] or ""),
            duration_ms=0.0,
            cached=True,
        )

    @staticmethod
    def _parse_signature(output: str) -> str:
        for line in output.splitlines():
            if line.rstrip().endswith(" FOUND"):
                value = line.rsplit(":", 1)[-1].strip()
                return value[:-6].strip() if value.endswith(" FOUND") else value
        return ""

    def _invoke(self, path: Path) -> tuple[str, str, str, float]:
        attempts: list[tuple[str, list[str]]] = []
        if shutil.which(self.settings.binary):
            attempts.append(("clamdscan-fdpass", [self.settings.binary, "--fdpass", "--no-summary", "--stdout", str(path)]))
            attempts.append(("clamdscan-stream", [self.settings.binary, "--stream", "--no-summary", "--stdout", str(path)]))
        if self.settings.allow_standalone_fallback and shutil.which(self.settings.fallback_binary):
            attempts.append(("clamscan", [self.settings.fallback_binary, "--no-summary", "--stdout", str(path)]))
        if not attempts:
            return "error", "", "Kein ClamAV-Scanner installiert", 0.0

        errors: list[str] = []
        for backend, command in attempts:
            started = time.monotonic()
            try:
                result = self._run(command, timeout=self.settings.timeout_seconds)
            except subprocess.TimeoutExpired:
                errors.append(f"{backend}: Timeout")
                continue
            except OSError as exc:
                errors.append(f"{backend}: {exc}")
                continue
            duration = (time.monotonic() - started) * 1000.0
            output = (result.stdout + b"\n" + result.stderr).decode("utf-8", errors="replace").strip()
            if result.returncode == 0:
                return "clean", "", output[-2000:], duration
            if result.returncode == 1:
                return "infected", self._parse_signature(output), output[-2000:], duration
            errors.append(f"{backend}: rc={result.returncode}: {output[-1000:]}")
        return "error", "", " | ".join(errors)[-4000:], 0.0

    def scan_bytes(
        self,
        data: bytes,
        *,
        name: str,
        source_type: str,
        use_cache: bool = True,
    ) -> AntivirusResult:
        digest = hashlib.sha256(data).hexdigest()
        identity = self.scanner_identity()
        if not self.settings.enabled:
            return AntivirusResult(
                status="disabled",
                sha256=digest,
                size_bytes=len(data),
                source_type=source_type,
                name=name,
                scanner="disabled",
                scanner_identity=identity,
                detail="Virenscanner ist deaktiviert",
            )
        if len(data) > self.settings.max_scan_bytes:
            result = AntivirusResult(
                status="error",
                sha256=digest,
                size_bytes=len(data),
                source_type=source_type,
                name=name,
                scanner="limit",
                scanner_identity=identity,
                detail=f"Scanobjekt ist groesser als {self.settings.max_scan_bytes} Byte",
            )
            self.store.put(result)
            return result
        if use_cache:
            cached = self._cached(digest, identity, name=name, source_type=source_type, size=len(data))
            if cached is not None:
                return cached

        temp_root = self.settings.temp_dir.expanduser().resolve()
        temp_root.mkdir(parents=True, exist_ok=True)
        os.chmod(temp_root, 0o700)
        safe_suffix = Path(name).suffix[:16] if Path(name).suffix else ".bin"
        with tempfile.TemporaryDirectory(prefix="scan-", dir=temp_root) as folder:
            path = Path(folder) / ("payload" + safe_suffix)
            path.write_bytes(data)
            os.chmod(path, 0o600)
            status, signature, detail, duration = self._invoke(path)
        result = AntivirusResult(
            status=status,
            sha256=digest,
            size_bytes=len(data),
            source_type=source_type,
            name=name,
            scanner="clamav",
            scanner_identity=identity,
            signature=signature,
            detail=detail,
            duration_ms=round(duration, 2),
        )
        self.store.put(result)
        return result

    def self_test(self) -> dict[str, Any]:
        # Standard harmless EICAR antivirus test string, assembled at runtime so
        # the update archive itself is not flagged by simplistic file scanners.
        payload = (
            b"X5O!P%@AP[4\\PZX54(P^)7CC)7}$"
            + b"EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*"
        )
        result = self.scan_bytes(
            payload, name="eicar.com.txt", source_type="antivirus-self-test", use_cache=False
        )
        return {
            "ok": result.infected,
            "expected": "infected",
            "result": result.to_dict(),
        }

    def scan_path(self, path: str | Path, *, source_type: str = "file", use_cache: bool = True) -> AntivirusResult:
        file_path = Path(path).expanduser().resolve()
        if not file_path.is_file():
            raise FileNotFoundError(file_path)
        return self.scan_bytes(file_path.read_bytes(), name=file_path.name, source_type=source_type, use_cache=use_cache)
