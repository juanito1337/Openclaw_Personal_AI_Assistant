"""Small fail-closed client for the private ClamAV Unix socket.

The client intentionally implements only PING, VERSION and INSTREAM.  It cannot
express file-system scans, reloads, shutdown or another clamd administration
command.
"""

from __future__ import annotations

import socket
import stat
import struct
from dataclasses import dataclass
from pathlib import Path

MAX_RESPONSE_BYTES = 65_536
STREAM_CHUNK_BYTES = 64 * 1024


class ClamdClientError(RuntimeError):
    """A typed connection, protocol or scanner failure."""

    def __init__(self, category: str, detail: str) -> None:
        self.category = category
        super().__init__(detail)


@dataclass(frozen=True, slots=True)
class ClamdScanResponse:
    status: str
    signature: str = ""
    detail: str = ""


class ClamdUnixClient:
    """Bounded clamd INSTREAM client over one absolute Unix socket."""

    def __init__(
        self,
        socket_path: str | Path,
        *,
        timeout_seconds: float,
        max_stream_bytes: int,
    ) -> None:
        path = Path(socket_path).expanduser()
        if not path.is_absolute():
            raise ValueError("ClamAV-Daemonsocket muss ein absoluter Pfad sein")
        if timeout_seconds <= 0:
            raise ValueError("ClamAV-Sockettimeout muss positiv sein")
        if max_stream_bytes < 1:
            raise ValueError("ClamAV-Streamlimit muss positiv sein")
        self.socket_path = path
        self.timeout_seconds = float(timeout_seconds)
        self.max_stream_bytes = int(max_stream_bytes)

    def _validate_socket_path(self) -> None:
        try:
            mode = self.socket_path.lstat().st_mode
        except FileNotFoundError as exc:
            raise ClamdClientError("socket-unavailable", "ClamAV-Daemonsocket fehlt") from exc
        except OSError as exc:
            raise ClamdClientError("socket-unavailable", "ClamAV-Daemonsocket ist nicht lesbar") from exc
        if stat.S_ISLNK(mode) or not stat.S_ISSOCK(mode):
            raise ClamdClientError(
                "socket-invalid",
                "Konfigurierter ClamAV-Daemonpfad ist kein Unix-Socket",
            )

    def _connect(self) -> socket.socket:
        self._validate_socket_path()
        client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        client.settimeout(self.timeout_seconds)
        try:
            client.connect(str(self.socket_path))
        except PermissionError as exc:
            client.close()
            raise ClamdClientError("socket-denied", "Zugriff auf ClamAV-Daemonsocket verweigert") from exc
        except TimeoutError as exc:
            client.close()
            raise ClamdClientError("timeout", "ClamAV-Daemonverbindung hat Zeitlimit erreicht") from exc
        except OSError as exc:
            client.close()
            raise ClamdClientError("socket-unavailable", "ClamAV-Daemon ist nicht erreichbar") from exc
        return client

    @staticmethod
    def _send(client: socket.socket, payload: bytes) -> None:
        try:
            client.sendall(payload)
        except TimeoutError as exc:
            raise ClamdClientError("timeout", "ClamAV-Stream hat Zeitlimit erreicht") from exc
        except (BrokenPipeError, ConnectionResetError, OSError) as exc:
            raise ClamdClientError("daemon-disconnected", "ClamAV-Daemon hat Verbindung beendet") from exc

    @staticmethod
    def _receive(client: socket.socket) -> str:
        chunks: list[bytes] = []
        size = 0
        try:
            while True:
                chunk = client.recv(min(4096, MAX_RESPONSE_BYTES - size + 1))
                if not chunk:
                    break
                marker = chunk.find(b"\0")
                if marker >= 0:
                    chunk = chunk[:marker]
                chunks.append(chunk)
                size += len(chunk)
                if size > MAX_RESPONSE_BYTES:
                    raise ClamdClientError("protocol-error", "ClamAV-Antwort ist zu gross")
                if marker >= 0 or chunk.endswith(b"\n"):
                    break
        except TimeoutError as exc:
            raise ClamdClientError("timeout", "ClamAV-Antwort hat Zeitlimit erreicht") from exc
        except (ConnectionResetError, OSError) as exc:
            raise ClamdClientError("daemon-disconnected", "ClamAV-Daemonantwort ist abgebrochen") from exc
        value = b"".join(chunks).decode("utf-8", errors="replace").strip()
        if not value:
            raise ClamdClientError("protocol-error", "ClamAV-Daemon lieferte keine Antwort")
        return value

    def _command(self, command: bytes) -> str:
        client = self._connect()
        try:
            self._send(client, b"z" + command + b"\0")
            return self._receive(client)
        finally:
            client.close()

    def ping(self) -> bool:
        response = self._command(b"PING")
        if response != "PONG":
            raise ClamdClientError("protocol-error", "ClamAV-PING lieferte keine PONG-Antwort")
        return True

    def version(self) -> str:
        response = self._command(b"VERSION")
        if not response.startswith("ClamAV ") or "/" not in response:
            raise ClamdClientError("protocol-error", "ClamAV-Version ist nicht verifizierbar")
        return response.splitlines()[0][:300]

    def scan(self, data: bytes) -> ClamdScanResponse:
        if len(data) > self.max_stream_bytes:
            raise ClamdClientError(
                "oversize",
                f"ClamAV-Stream ist groesser als {self.max_stream_bytes} Byte",
            )
        client = self._connect()
        try:
            self._send(client, b"zINSTREAM\0")
            view = memoryview(data)
            for offset in range(0, len(view), STREAM_CHUNK_BYTES):
                chunk = bytes(view[offset : offset + STREAM_CHUNK_BYTES])
                self._send(client, struct.pack("!I", len(chunk)))
                self._send(client, chunk)
            self._send(client, struct.pack("!I", 0))
            response = self._receive(client)
        finally:
            client.close()

        _, separator, verdict = response.rpartition(": ")
        value = verdict if separator else response
        if value == "OK":
            return ClamdScanResponse("clean", detail=response[-2000:])
        if value.endswith(" FOUND"):
            return ClamdScanResponse(
                "infected",
                signature=value[: -len(" FOUND")].strip()[:500],
                detail=response[-2000:],
            )
        if value.endswith(" ERROR") or "size limit exceeded" in value.casefold():
            return ClamdScanResponse("error", detail=response[-2000:])
        raise ClamdClientError("protocol-error", "Unbekannte ClamAV-Scanantwort")
