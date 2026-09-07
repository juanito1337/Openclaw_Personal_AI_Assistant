from __future__ import annotations

import socket
import struct
import tempfile
import threading
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from pathlib import Path

import pytest

from personal_assistant.antivirus import HostAntivirus
from personal_assistant.clamd_client import ClamdClientError, ClamdUnixClient
from personal_assistant.tool_settings import AntivirusToolSettings


class FakeClamd:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.stop = threading.Event()
        self.payloads: list[bytes] = []
        self.version = "ClamAV 1.4.3/99999/Fri Jan  1 00:00:00 2099"
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
    try:
        yield daemon
    finally:
        daemon.close()


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
    ("reply", "delay", "category"),
    [
        (b"", 0.0, "protocol-error"),
        (b"not-a-clamd-reply\0", 0.0, "protocol-error"),
        (b"PONG\0", 0.2, "timeout"),
    ],
)
def test_client_types_empty_corrupt_and_timeout_responses(
    reply: bytes,
    delay: float,
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
                if reply:
                    with suppress(OSError):
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
                antivirus.scanner_identity(refresh=True)
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


def test_missing_daemon_may_scan_standalone_but_never_enables_index() -> None:
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
