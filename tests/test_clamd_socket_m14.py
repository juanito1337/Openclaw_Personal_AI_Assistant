from __future__ import annotations

import json
import socket
import struct
import subprocess
import tempfile
import threading
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

from personal_assistant.antivirus import HostAntivirus
from personal_assistant.clamd_client import ClamdClientError, ClamdUnixClient
from personal_assistant.clamd_health import RECEIPT, check_clamd, socket_identity
from personal_assistant.clamd_health import main as clamd_health_main
from personal_assistant.clamd_runner import publish
from personal_assistant.tool_settings import AntivirusToolSettings


class FakeClamd:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.stop = threading.Event()
        self.payloads: list[bytes] = []
        self.version = f"ClamAV 1.4.3/99999/{datetime.now(UTC).ctime()}"
        self.change_version_after_scan = False
        self.server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.server.bind(str(path))
        self.server.listen(8)
        self.server.settimeout(0.1)
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()

    @staticmethod
    def _exact(client: socket.socket, size: int) -> bytes:
        result = bytearray()
        while len(result) < size:
            chunk = client.recv(size - len(result))
            if not chunk:
                raise ConnectionError("unexpected eof")
            result.extend(chunk)
        return bytes(result)

    def _serve(self) -> None:
        while not self.stop.is_set():
            try:
                client, _ = self.server.accept()
            except TimeoutError:
                continue
            with client:
                command = bytearray()
                while not command.endswith(b"\0"):
                    chunk = client.recv(1)
                    if not chunk:
                        break
                    command.extend(chunk)
                if command == b"zPING\0":
                    client.sendall(b"PONG\0")
                elif command == b"zVERSION\0":
                    client.sendall(self.version.encode("ascii") + b"\0")
                elif command == b"zINSTREAM\0":
                    payload = bytearray()
                    while True:
                        size = struct.unpack("!I", self._exact(client, 4))[0]
                        if size == 0:
                            break
                        payload.extend(self._exact(client, size))
                    self.payloads.append(bytes(payload))
                    if b"M14-EICAR" in payload:
                        client.sendall(b"stream: M14-Test-Signature FOUND\0")
                    else:
                        client.sendall(b"stream: OK\0")
                    if self.change_version_after_scan:
                        self.version = "ClamAV 1.4.3/100000/Fri Jan  1 00:00:00 2099"
                else:
                    client.sendall(b"UNKNOWN COMMAND ERROR\0")

    def close(self) -> None:
        self.stop.set()
        self.thread.join(timeout=2)
        self.server.close()


@contextmanager
def fake_clamd(root: Path) -> Iterator[FakeClamd]:
    daemon = FakeClamd(root / "clamd.sock")
    def attest(generation="a" * 64, *, group=None):
        versions = {"daily": int(daemon.version.split("/")[1]), "main": 10, "bytecode": 3}
        if group:
            versions[group] += 1
        disk.update(ok=True, generation=generation, versions=versions)
        publish(root / RECEIPT, {"schema": 1, "instance": uuid.uuid4().hex,
                                 "checked_at": time.time(), "version": daemon.version,
                                 "versions": versions, "generation": generation,
                                 "socket": socket_identity(daemon.path)})
    disk = {}
    daemon.attest = attest
    attest()
    try:
        with patch("personal_assistant.clamd_health.vendor_readiness", return_value=disk):
            yield daemon
    finally:
        daemon.close()


@pytest.mark.parametrize(
    "resident", ["stale", "malformed", "missing", "future", "fresh-but-one-version-behind"]
)
def test_daemon_health_checks_loaded_signature_independently_of_fresh_disk(tmp_path, monkeypatch, resident):
    now = datetime.now(UTC)
    stamps = {
        "stale": (now - timedelta(days=3)).ctime(),
        "malformed": "not-a-date",
        "missing": "",
        "future": (now + timedelta(days=1)).ctime(),
        "fresh-but-one-version-behind": (now - timedelta(days=1)).ctime(),
    }
    with fake_clamd(tmp_path) as daemon:
        daemon.version = f"ClamAV 1.4.3/28194/{stamps[resident]}"
        report = check_clamd(
            daemon.path, database_dir=tmp_path, max_age_seconds=172800, timeout_seconds=1,
            max_stream_bytes=1024,
        )
        settings = AntivirusToolSettings(daemon_socket=str(daemon.path), temp_dir=tmp_path / "scan")
        antivirus = HostAntivirus(settings, database=tmp_path / "antivirus.sqlite3")
        try:
            index = antivirus.index_readiness()
        finally:
            antivirus.close()
    expected = False
    assert report["daemon_ready"] is index["daemon_ready"] is True
    assert report["ok"] is index["index_ready"] is expected
    assert report["signatures_fresh"] is index["signatures_fresh"] is expected
    known_age = resident in ("stale", "fresh-but-one-version-behind")
    assert index["signature_age_source"] == ("daemon-version-utc" if known_age else "")


def test_daemon_healthcheck_exits_unhealthy_with_disk_problem_but_preserves_socket_evidence(
    tmp_path, monkeypatch, capsys,
):
    with fake_clamd(tmp_path) as daemon:
        monkeypatch.setenv("OPENCLAW_CLAMD_SOCKET", str(daemon.path))
        monkeypatch.setenv("CLAMAV_DATABASE_DIR", str(tmp_path))
        with patch("personal_assistant.clamd_health.vendor_readiness", return_value={"ok": False}):
            assert clamd_health_main() == 1
    report = json.loads(capsys.readouterr().out)
    assert report["ok"] is report["database"]["ok"] is False
    assert report["daemon_ready"] is True
    assert report["signatures_fresh"] is False


@pytest.mark.parametrize(("age", "disk_case", "expected_age"), [
    (172800, "same", 172800),
    (172801, "same", 172801),
    (172801, "newer", None),
    (172801, "engine-only", None),
    (172801, "no-resident-signature", None),
    (172801, "bad-resident-date", None),
    (172801, "bad-disk-date", None),
    (172801, "unavailable", None),
])
def test_legacy_age_requires_matching_utc_database_identity(
    tmp_path, monkeypatch, age, disk_case, expected_age,
):
    now = datetime(2026, 11, 27, 12, tzinfo=UTC)
    built = now - timedelta(seconds=age)
    # A daemon running in Europe/Berlin renders this November build in CET.
    resident = f"ClamAV 1.4.3/28194/{(built + timedelta(hours=1)).ctime()}"
    if disk_case == "no-resident-signature":
        resident = "ClamAV 1.4.3"
    if disk_case == "bad-resident-date":
        resident = "ClamAV 1.4.3/28194/no-date"
    disk = f"ClamAV 1.4.3/{28195 if disk_case == 'newer' else 28194}/{built.ctime()}"
    if disk_case == "engine-only":
        disk = "ClamAV 1.4.3"
    if disk_case == "bad-disk-date":
        disk = "ClamAV 1.4.3/28194/no-date"

    def version_probe(args, **kwargs):
        assert args in (["clamdscan", "--version"], ["clamscan", "--version"])
        assert 0 < kwargs["timeout"] <= 5
        if args[0] == "clamscan":
            assert kwargs["env"]["TZ"] == "UTC"
            if disk_case == "unavailable":
                raise OSError("no scanner")
        output = resident if args[0] == "clamdscan" else disk
        if kwargs.get("text"):
            return subprocess.CompletedProcess(args, 0, output, "")
        return subprocess.CompletedProcess(args, 0, output.encode("ascii"), b"")

    monkeypatch.setenv("TZ", "Europe/Berlin")
    monkeypatch.setattr(subprocess, "run", version_probe)
    monkeypatch.setattr(time, "time", lambda: now.timestamp())
    monkeypatch.setattr(HostAntivirus, "_systemd_unit", lambda self, name: {"ActiveState": "active"})
    settings = AntivirusToolSettings(daemon_socket="", allow_standalone_fallback=False)
    antivirus = HostAntivirus(settings, database=tmp_path / "antivirus.sqlite3")
    try:
        daemon = antivirus._daemon_status()
        readiness = antivirus.index_readiness()
    finally:
        antivirus.close()
    assert readiness["daemon_ready"] is True
    assert readiness["signature_age_seconds"] == expected_age
    assert daemon["version"] == resident
    assert readiness["signature_age_source"] == ("matching-local-database-utc" if expected_age else "")
    assert readiness["signature_timestamp"] == (built.ctime() if expected_age else "")
    assert readiness["index_ready"] is False
    assert "legacy-resident-unverified" in readiness["reasons"]


def test_private_client_ping_version_clean_and_infected() -> None:
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        with fake_clamd(root) as daemon:
            client = ClamdUnixClient(
                daemon.path,
                timeout_seconds=2,
                max_stream_bytes=1024,
            )
            assert client.ping()
            assert client.version().startswith("ClamAV 1.4.3/99999/")
            clean = client.scan(b"harmless")
            infected = client.scan(b"M14-EICAR")
            assert clean.status == "clean"
            assert infected.status == "infected"
            assert infected.signature == "M14-Test-Signature"
            assert daemon.payloads == [b"harmless", b"M14-EICAR"]


def test_client_rejects_oversize_and_non_socket_before_scan() -> None:
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        regular = root / "not-a-socket"
        regular.write_text("fixture", encoding="utf-8")
        client = ClamdUnixClient(regular, timeout_seconds=1, max_stream_bytes=4)
        with pytest.raises(ClamdClientError, match="groesser") as oversize:
            client.scan(b"12345")
        assert oversize.value.category == "oversize"
        with pytest.raises(ClamdClientError, match="kein Unix-Socket") as invalid:
            client.ping()
        assert invalid.value.category == "socket-invalid"


@pytest.mark.parametrize(
    ("reply", "delay", "byte_delay", "category"),
    [
        (b"", 0.0, 0.0, "protocol-error"),
        (b"not-a-clamd-reply\0", 0.0, 0.0, "protocol-error"),
        (b"PONG\0", 0.2, 0.0, "timeout"),
        (b"PONG\0", 0.0, 0.025, "timeout"),
    ],
)
def test_client_types_empty_corrupt_and_timeout_responses(
    reply: bytes,
    delay: float,
    byte_delay: float,
    category: str,
) -> None:
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "clamd.sock"
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server.bind(str(path))
        server.listen(1)

        def serve_once() -> None:
            client, _ = server.accept()
            with client:
                client.recv(1024)
                if delay:
                    threading.Event().wait(delay)
                with suppress(OSError):
                    if byte_delay:
                        for byte in reply:
                            client.sendall(bytes([byte]))
                            time.sleep(byte_delay)
                    else:
                        client.sendall(reply)

        thread = threading.Thread(target=serve_once, daemon=True)
        thread.start()
        client = ClamdUnixClient(path, timeout_seconds=0.05, max_stream_bytes=1024)
        with pytest.raises(ClamdClientError) as failure:
            client.ping()
        assert failure.value.category == category
        thread.join(timeout=1)
        server.close()


def test_client_types_missing_socket() -> None:
    with tempfile.TemporaryDirectory() as folder:
        client = ClamdUnixClient(
            Path(folder) / "missing.sock",
            timeout_seconds=1,
            max_stream_bytes=1024,
        )
        with pytest.raises(ClamdClientError) as failure:
            client.ping()
        assert failure.value.category == "socket-unavailable"


def test_index_readiness_times_out_promptly_on_silent_socket(tmp_path, monkeypatch):
    monkeypatch.setattr("personal_assistant.antivirus.CLAMD_DIAGNOSTIC_TIMEOUT_SECONDS", 0.05)
    path = tmp_path / "silent.sock"
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as silent:
        silent.bind(str(path))
        silent.listen(1)
        settings = AntivirusToolSettings(daemon_socket=str(path), timeout_seconds=2)
        antivirus = HostAntivirus(settings, database=tmp_path / "av.sqlite3")
        try:
            started = time.monotonic()
            readiness = antivirus.index_readiness()
            elapsed = time.monotonic() - started
        finally:
            antivirus.close()
    assert readiness["index_ready"] is readiness["daemon_ready"] is False
    assert "clamd-not-ready" in readiness["reasons"]
    assert elapsed < 1


def test_stream_scan_uses_configured_timeout(tmp_path, monkeypatch):
    scan_timeouts = []
    original_scan = ClamdUnixClient.scan

    def record_scan(client, data):
        scan_timeouts.append(client.timeout_seconds)
        return original_scan(client, data)

    monkeypatch.setattr(ClamdUnixClient, "scan", record_scan)
    with fake_clamd(tmp_path) as daemon:
        settings = AntivirusToolSettings(
            daemon_socket=str(daemon.path), timeout_seconds=127, temp_dir=tmp_path,
        )
        antivirus = HostAntivirus(settings, database=tmp_path / "av.sqlite3")
        try:
            result = antivirus.scan_bytes(b"mail", name="test", source_type="test")
        finally:
            antivirus.close()
    assert result.clean and scan_timeouts == [127]


def test_host_antivirus_reports_daemon_transport_and_cache() -> None:
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        with fake_clamd(root) as daemon:
            settings = AntivirusToolSettings(
                daemon_socket=str(daemon.path),
                fallback_binary="",
                allow_standalone_fallback=False,
                temp_dir=root / "tmp",
                signature_max_age_seconds=172800,
            )
            antivirus = HostAntivirus(settings, database=root / "antivirus.sqlite3")
            try:
                readiness = antivirus.index_readiness()
                assert readiness["index_ready"] is True
                assert readiness["daemon_ready"] is True
                assert readiness["signatures_fresh"] is True
                assert readiness["engine_version"] == "1.4.3"
                assert readiness["signature_version"] == "99999"
                first = antivirus.scan_bytes(b"mail", name="mail.eml", source_type="test")
                second = antivirus.scan_bytes(b"mail", name="mail.eml", source_type="test")
                assert first.clean and first.transport == "daemon-stream"
                assert first.fallback_used is False
                assert second.clean and second.cached and second.transport == "cache"
                doctor = antivirus.doctor(live_scan=False)
                assert doctor["daemon_ready"] is True
                assert doctor["transport"] == "daemon-stream"
                assert doctor["index_readiness"]["index_ready"] is True
                assert doctor["daemon"]["engine_version"] == "1.4.3"
                assert doctor["daemon"]["signature_version"] == "99999"
            finally:
                antivirus.close()


def test_signature_identity_change_invalidates_clean_cache() -> None:
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        with fake_clamd(root) as daemon:
            settings = AntivirusToolSettings(
                daemon_socket=str(daemon.path),
                fallback_binary="",
                allow_standalone_fallback=False,
                temp_dir=root / "tmp",
            )
            antivirus = HostAntivirus(settings, database=root / "antivirus.sqlite3")
            try:
                first = antivirus.scan_bytes(b"same-mail", name="mail.eml", source_type="test")
                assert first.clean and not first.cached
                assert len(daemon.payloads) == 1
                daemon.version = "ClamAV 1.4.3/100000/Fri Jan  1 00:00:00 2099"
                daemon.attest("b" * 64)
                second = antivirus.scan_bytes(b"same-mail", name="mail.eml", source_type="test")
                assert second.clean and not second.cached
                assert second.scanner_identity != first.scanner_identity
                assert len(daemon.payloads) == 2
            finally:
                antivirus.close()


def test_signature_change_during_scan_fails_closed_without_clean_cache() -> None:
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        with fake_clamd(root) as daemon:
            daemon.change_version_after_scan = True
            settings = AntivirusToolSettings(
                daemon_socket=str(daemon.path),
                fallback_binary="",
                allow_standalone_fallback=False,
                temp_dir=root / "tmp",
            )
            antivirus = HostAntivirus(settings, database=root / "antivirus.sqlite3")
            try:
                result = antivirus.scan_bytes(
                    b"mail-during-reload",
                    name="mail.eml",
                    source_type="test",
                    use_cache=False,
                )
                assert result.error
                assert result.fallback_reason == "signature-changed-during-scan"
                assert antivirus.store.summary()["counts"].get("clean", 0) == 0
                daemon.change_version_after_scan = False
                daemon.attest("b" * 64)
                recovered = antivirus.scan_bytes(
                    b"mail-during-reload",
                    name="mail.eml",
                    source_type="test",
                )
                assert recovered.clean
                assert not recovered.cached
                assert len(daemon.payloads) == 2
            finally:
                antivirus.close()


def test_index_preflight_rejects_missing_or_unready_contract() -> None:
    from mail_agent.search_backfill import require_index_antivirus_ready

    class MissingReadiness:
        pass

    class Unready:
        @staticmethod
        def index_readiness() -> dict[str, object]:
            return {"ok": False, "index_ready": False, "reasons": ["clamd-not-ready"]}

    with pytest.raises(RuntimeError, match="Preflight fehlt"):
        require_index_antivirus_ready(MissingReadiness())  # type: ignore[arg-type]
    with pytest.raises(RuntimeError, match="clamd-not-ready"):
        require_index_antivirus_ready(Unready())  # type: ignore[arg-type]


def test_missing_daemon_may_scan_standalone_but_never_enables_index(monkeypatch) -> None:
    monkeypatch.setattr("personal_assistant.antivirus.vendor_readiness",
                        lambda _: {"ok": True, "generation": "a" * 64, "versions": {"daily": 99999}})
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        fallback = root / "clamscan"
        fallback.write_text(
            "#!/bin/sh\n"
            "case \" $* \" in *\" --version \"*) echo 'ClamAV 1.4.3/99999/Test'; exit 0;; esac\n"
            "echo \"$3: OK\"\n",
            encoding="utf-8",
        )
        fallback.chmod(0o755)
        settings = AntivirusToolSettings(
            daemon_socket=str(root / "missing.sock"),
            binary="",
            fallback_binary=str(fallback),
            allow_standalone_fallback=True,
            temp_dir=root / "tmp",
        )
        antivirus = HostAntivirus(settings, database=root / "antivirus.sqlite3")
        try:
            result = antivirus.scan_bytes(b"mail", name="mail.eml", source_type="test")
            assert result.clean
            assert result.transport == "clamscan"
            assert result.fallback_used is True
            readiness = antivirus.index_readiness()
            assert readiness["index_ready"] is False
            assert readiness["daemon_ready"] is False
            assert readiness["fallback_allowed_for_index"] is False
            assert "clamd-not-ready" in readiness["reasons"]
        finally:
            antivirus.close()


def test_old_but_attested_scanner_is_ready_and_main_only_change_invalidates_cache(tmp_path):
    with fake_clamd(tmp_path) as daemon:
        daemon.version = "ClamAV 1.4.3/99999/Mon May  1 00:00:00 2023"
        daemon.attest()
        av = HostAntivirus(AntivirusToolSettings(daemon_socket=str(daemon.path), temp_dir=tmp_path),
                           database=tmp_path / "av.sqlite3")
        try:
            assert av.index_readiness()["index_ready"]
            a = av.scan_bytes(b"same", name="mail", source_type="test")
            daemon.attest("b" * 64)
            b = av.scan_bytes(b"same", name="mail", source_type="test")
            assert a.clean and b.clean and not b.cached
            assert a.scanner_identity != b.scanner_identity
        finally:
            av.close()


@pytest.mark.parametrize("group", ["main", "bytecode"])
def test_non_daily_change_during_stream_cannot_publish_clean(tmp_path, monkeypatch, group):
    with fake_clamd(tmp_path) as daemon:
        av = HostAntivirus(AntivirusToolSettings(daemon_socket=str(daemon.path), temp_dir=tmp_path),
                           database=tmp_path / "av.sqlite3")
        scan = ClamdUnixClient.scan

        def update_during_scan(client, data):
            response = scan(client, data)
            daemon.attest("b" * 64, group=group)
            return response

        monkeypatch.setattr(ClamdUnixClient, "scan", update_during_scan)
        try:
            result = av.scan_bytes(b"mail", name="test", source_type="test")
            assert result.error
            assert result.fallback_reason == "signature-changed-during-scan"
            assert av.store.summary()["counts"].get("clean", 0) == 0
        finally:
            av.close()


@pytest.mark.parametrize("change", ["expired", "socket-reused", "missing"])
def test_invalid_resident_proof_blocks_even_with_responding_daemon(tmp_path, change):
    with fake_clamd(tmp_path) as daemon:
        receipt = tmp_path / RECEIPT
        proof = json.loads(receipt.read_bytes())
        if change == "expired":
            proof["checked_at"] -= 21
            publish(receipt, proof)
        elif change == "socket-reused":
            proof["socket"][1] += 1
            publish(receipt, proof)
        else:
            receipt.unlink()
        report = check_clamd(daemon.path, database_dir=tmp_path, max_age_seconds=172800,
                             timeout_seconds=1, max_stream_bytes=1024)
        assert report["daemon_ready"] is True
        assert report["ok"] is False
        assert report["currentness"]["reasons"]


def test_stream_trickling_verdict_uses_one_total_deadline(tmp_path):
    path = tmp_path / "trickle.sock"
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
        server.bind(str(path))
        server.listen(1)

        def serve():
            client, _ = server.accept()
            with client, suppress(OSError):
                # Read the complete INSTREAM command, one-byte object and terminator.
                FakeClamd._exact(client, len(b"zINSTREAM\0") + 9)
                for byte in b"stream: OK\0":
                    client.sendall(bytes([byte]))
                    time.sleep(0.025)

        thread = threading.Thread(target=serve, daemon=True)
        thread.start()
        try:
            client = ClamdUnixClient(path, timeout_seconds=0.1, max_stream_bytes=1024)
            with pytest.raises(ClamdClientError) as failed:
                client.scan(b"x")
            assert failed.value.category == "timeout"
        finally:
            thread.join(timeout=1)
