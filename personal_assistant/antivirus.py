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
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any

from .clamd_client import ClamdClientError, ClamdUnixClient
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
    transport: str = ""
    fallback_used: bool = False
    fallback_reason: str = ""

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
        self._identity_transport = ""

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

    def _run(
        self,
        args: list[str],
        *,
        input_bytes: bytes | None = None,
        timeout: int | None = None,
    ) -> subprocess.CompletedProcess[bytes]:
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
        daemon = self._daemon_status()
        if daemon["ready"]:
            if daemon.get("version"):
                self._identity = f"clamd:{daemon['version']}"
            else:
                self._identity = self._binary_identity(self.settings.binary)
            self._identity_transport = str(daemon.get("transport") or "clamdscan-systemd")
            return self._identity
        fallback = self.settings.fallback_binary if self.settings.allow_standalone_fallback else ""
        self._identity = self._binary_identity(fallback)
        self._identity_transport = "standalone" if self._identity != "clamav:unavailable" else "unavailable"
        return self._identity

    def _binary_identity(self, binary: str) -> str:
        if not binary:
            return "clamav:unavailable"
        try:
            result = self._run([binary, "--version"], timeout=15)
            output = (result.stdout + b"\n" + result.stderr).decode(
                "utf-8", errors="replace"
            ).strip()
        except (OSError, subprocess.TimeoutExpired):
            return "clamav:unavailable"
        if result.returncode != 0 or not output:
            return "clamav:unavailable"
        return f"{binary}:{output.splitlines()[0][:300]}"

    def _daemon_client(self) -> ClamdUnixClient | None:
        if not self.settings.daemon_socket:
            return None
        return ClamdUnixClient(
            self.settings.daemon_socket,
            timeout_seconds=self.settings.timeout_seconds,
            max_stream_bytes=self.settings.max_scan_bytes,
        )

    @staticmethod
    def _signature_age(version: str) -> int | None:
        parts = version.split("/", 2)
        if len(parts) != 3:
            return None
        try:
            stamp = parsedate_to_datetime(parts[2])
        except (TypeError, ValueError, OverflowError):
            return None
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=UTC)
        return max(0, int((datetime.now(UTC) - stamp.astimezone(UTC)).total_seconds()))

    @staticmethod
    def _version_fields(version: str) -> dict[str, str]:
        """Split a verified clamd VERSION response without guessing fields."""

        parts = version.split("/", 2)
        engine = parts[0].removeprefix("ClamAV ").strip() if parts else ""
        signature = parts[1].strip() if len(parts) >= 2 else ""
        timestamp = parts[2].strip() if len(parts) >= 3 else ""
        return {
            "engine_version": engine,
            "signature_version": signature,
            "signature_timestamp": timestamp,
        }

    def _daemon_status(self) -> dict[str, Any]:
        client = self._daemon_client()
        if client is None:
            unit = self._systemd_unit(self.settings.daemon_service)
            ready = unit.get("ActiveState") == "active"
            identity = self._binary_identity(self.settings.binary) if ready else ""
            version = identity.split(":", 1)[1] if ":" in identity else ""
            return {
                **unit,
                "ready": ready,
                "transport": "clamdscan-systemd" if ready else "",
                "socket": "",
                "version": version,
                "signature_age_seconds": self._signature_age(version),
                "error_category": "" if ready else "daemon-unavailable",
                **self._version_fields(version),
            }
        try:
            client.ping()
            version = client.version()
        except (ClamdClientError, OSError, ValueError) as exc:
            return {
                "available": False,
                "ready": False,
                "transport": "daemon-stream",
                "socket": str(client.socket_path),
                "version": "",
                "signature_age_seconds": None,
                "error_category": getattr(exc, "category", "daemon-unavailable"),
                "error": str(exc),
                **self._version_fields(""),
            }
        age = self._signature_age(version)
        return {
            "available": True,
            "ready": True,
            "transport": "daemon-stream",
            "socket": str(client.socket_path),
            "version": version,
            "signature_age_seconds": age,
            "error_category": "",
            **self._version_fields(version),
        }

    def index_readiness(self) -> dict[str, Any]:
        daemon = self._daemon_status()
        age = daemon.get("signature_age_seconds")
        signatures_fresh = bool(
            daemon.get("ready")
            and age is not None
            and int(age) <= self.settings.signature_max_age_seconds
        )
        daemon_required = bool(self.settings.require_daemon_for_index)
        ready = bool(
            self.settings.enabled
            and self.settings.fail_closed
            and self.settings.scan_raw_mail
            and self.settings.scan_attachments
            and (not daemon_required or daemon.get("ready"))
            and (not daemon_required or signatures_fresh)
        )
        reasons: list[str] = []
        if not self.settings.enabled:
            reasons.append("antivirus-disabled")
        if not self.settings.fail_closed:
            reasons.append("antivirus-not-fail-closed")
        if not self.settings.scan_raw_mail:
            reasons.append("raw-mail-scan-disabled")
        if not self.settings.scan_attachments:
            reasons.append("attachment-scan-disabled")
        if daemon_required and not daemon.get("ready"):
            reasons.append("clamd-not-ready")
        elif daemon_required and not signatures_fresh:
            reasons.append("clamd-signatures-not-fresh")
        return {
            "ok": ready,
            "index_ready": ready,
            "daemon_required": daemon_required,
            "daemon_ready": bool(daemon.get("ready")),
            "signatures_fresh": signatures_fresh,
            "signature_age_seconds": age,
            "signature_max_age_seconds": self.settings.signature_max_age_seconds,
            "transport": daemon.get("transport") or "",
            "engine_version": daemon.get("engine_version") or "",
            "signature_version": daemon.get("signature_version") or "",
            "signature_timestamp": daemon.get("signature_timestamp") or "",
            "fallback_allowed_for_index": False,
            "reasons": reasons,
        }

    def doctor(self, *, live_scan: bool = True) -> dict[str, Any]:
        daemon = self._daemon_status()
        identity = self.scanner_identity(refresh=True)
        freshclam = self._systemd_unit(self.settings.freshclam_service)
        binary = shutil.which(self.settings.binary)
        fallback = (
            shutil.which(self.settings.fallback_binary)
            if self.settings.allow_standalone_fallback
            else None
        )
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
            "daemon_ready": bool(daemon.get("ready")),
            "transport": daemon.get("transport") or self._identity_transport,
            "signature_age_seconds": daemon.get("signature_age_seconds"),
            "signature_max_age_seconds": self.settings.signature_max_age_seconds,
            "index_readiness": self.index_readiness(),
        }
        if not self.settings.enabled:
            result.update({"ok": True, "detail": "Virenscanner ist deaktiviert"})
            return result
        if not binary and not fallback:
            result["detail"] = "Weder clamdscan noch clamscan ist installiert"
            return result
        daemon_ok = bool(daemon.get("ready"))
        if binary and not daemon_ok and not fallback:
            result["detail"] = "clamdscan ist vorhanden, aber clamav-daemon ist nicht aktiv"
            return result
        if live_scan:
            scan = self.scan_bytes(
                b"Personal Assistant antivirus health check\n",
                name="health-check.txt",
                source_type="health",
                use_cache=False,
            )
            result["live_scan"] = scan.to_dict()
            result["ok"] = scan.clean
            result["scanner_works"] = scan.clean
            result["fallback_used"] = scan.fallback_used
            result["detail"] = "Scan erfolgreich" if scan.clean else scan.detail or scan.status
        else:
            result["ok"] = bool(binary and daemon_ok) or bool(fallback)
            result["detail"] = "Scanner verfuegbar" if result["ok"] else "Scanner nicht verfuegbar"
            result["scanner_works"] = result["ok"]
        return result

    def _cached(
        self,
        sha256: str,
        identity: str,
        *,
        name: str,
        source_type: str,
        size: int,
    ) -> AntivirusResult | None:
        row = self.store.get(sha256, identity)
        if row is None:
            return None
        # Transient daemon, protocol and policy failures remain useful audit
        # rows, but must never suppress a later real scan after recovery.
        if str(row["status"]) not in {"clean", "infected"}:
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
            transport="cache",
        )

    @staticmethod
    def _parse_signature(output: str) -> str:
        for line in output.splitlines():
            if line.rstrip().endswith(" FOUND"):
                value = line.rsplit(":", 1)[-1].strip()
                return value[:-6].strip() if value.endswith(" FOUND") else value
        return ""

    def _invoke_standalone(self, path: Path) -> tuple[str, str, str, float, str]:
        attempts: list[tuple[str, list[str]]] = []
        if shutil.which(self.settings.binary):
            attempts.append(
                (
                    "clamdscan-fdpass",
                    [self.settings.binary, "--fdpass", "--no-summary", "--stdout", str(path)],
                )
            )
            attempts.append(
                (
                    "clamdscan-stream",
                    [self.settings.binary, "--stream", "--no-summary", "--stdout", str(path)],
                )
            )
        if self.settings.allow_standalone_fallback and shutil.which(self.settings.fallback_binary):
            attempts.append(
                (
                    "clamscan",
                    [self.settings.fallback_binary, "--no-summary", "--stdout", str(path)],
                )
            )
        if not attempts:
            return "error", "", "Kein ClamAV-Scanner installiert", 0.0, "unavailable"

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
            output = (
                (result.stdout + b"\n" + result.stderr)
                .decode("utf-8", errors="replace")
                .strip()
                .replace(str(path), "<scan-object>")
            )
            if result.returncode == 0:
                return "clean", "", output[-2000:], duration, backend
            if result.returncode == 1:
                return "infected", self._parse_signature(output), output[-2000:], duration, backend
            errors.append(f"{backend}: rc={result.returncode}: {output[-1000:]}")
        return "error", "", " | ".join(errors)[-4000:], 0.0, "standalone"

    def _invoke(
        self,
        data: bytes,
        path: Path,
    ) -> tuple[str, str, str, float, str, bool, str, str]:
        client = self._daemon_client()
        daemon_error = ""
        daemon_category = ""
        if client is not None:
            started = time.monotonic()
            try:
                version_before = client.version()
                response = client.scan(data)
                version_after = client.version()
                if version_after != version_before:
                    raise ClamdClientError(
                        "signature-changed-during-scan",
                        "ClamAV-Identitaet wechselte waehrend des Scans",
                    )
                duration = (time.monotonic() - started) * 1000.0
                return (
                    response.status,
                    response.signature,
                    response.detail,
                    duration,
                    "daemon-stream",
                    False,
                    "",
                    f"clamd:{version_after}",
                )
            except (ClamdClientError, OSError, ValueError) as exc:
                daemon_category = getattr(exc, "category", "daemon-unavailable")
                daemon_error = str(exc)
                if not self.settings.allow_standalone_fallback:
                    return (
                        "error", "", daemon_error, 0.0, "daemon-stream", False,
                        daemon_category, self.scanner_identity(refresh=True),
                    )
        status, signature, detail, duration, transport = self._invoke_standalone(path)
        if transport == "daemon-stream":
            identity = self.scanner_identity(refresh=True)
        elif transport.startswith("clamdscan"):
            identity = self._binary_identity(self.settings.binary)
        else:
            identity = self._binary_identity(self.settings.fallback_binary)
        self._identity = identity
        self._identity_transport = transport
        fallback_used = client is not None
        if daemon_error:
            detail = f"{daemon_category}: {daemon_error} | {detail}"[-4000:]
        return (
            status,
            signature,
            detail,
            duration,
            transport,
            fallback_used,
            daemon_category,
            identity,
        )

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
            (
                status,
                signature,
                detail,
                duration,
                transport,
                fallback_used,
                fallback_reason,
                result_identity,
            ) = self._invoke(data, path)
        result = AntivirusResult(
            status=status,
            sha256=digest,
            size_bytes=len(data),
            source_type=source_type,
            name=name,
            scanner="clamav",
            scanner_identity=result_identity,
            signature=signature,
            detail=detail,
            duration_ms=round(duration, 2),
            transport=transport,
            fallback_used=fallback_used,
            fallback_reason=fallback_reason,
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

    def scan_path(
        self,
        path: str | Path,
        *,
        source_type: str = "file",
        use_cache: bool = True,
    ) -> AntivirusResult:
        file_path = Path(path).expanduser().resolve()
        if not file_path.is_file():
            raise FileNotFoundError(file_path)
        return self.scan_bytes(
            file_path.read_bytes(),
            name=file_path.name,
            source_type=source_type,
            use_cache=use_cache,
        )
