from __future__ import annotations

import base64
import os
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import PurePosixPath

from .config import Config
from .models import OperationResult


@dataclass(slots=True, frozen=True)
class WebDAVHealth:
    ok: bool
    detail: str


class NextcloudFilesClient:
    """Small, restricted Nextcloud WebDAV client for invoice PDFs only.

    It supports exactly the operations required by the mail agent: check the files
    endpoint, create directories, and upload immutable files. It has no delete,
    move, share, or overwrite operation.
    """

    def __init__(self, config: Config, *, dry_run: bool = False) -> None:
        self.config = config
        self.dry_run = dry_run

    def credentials(self) -> tuple[str, str, str]:
        cfg = self.config.nextcloud
        return (
            os.environ.get(cfg.base_url_env, "").strip().rstrip("/"),
            os.environ.get(cfg.username_env, "").strip(),
            os.environ.get(cfg.token_env, "").strip(),
        )

    def missing_environment(self) -> list[str]:
        cfg = self.config.nextcloud
        return [
            name
            for name in (cfg.base_url_env, cfg.username_env, cfg.token_env)
            if not os.environ.get(name, "").strip()
        ]

    @staticmethod
    def _clean_remote_path(value: str) -> str:
        path = PurePosixPath("/" + (value or "").replace("\\", "/").lstrip("/"))
        parts = [part for part in path.parts if part not in {"/", "", "."}]
        if any(part == ".." for part in parts):
            raise ValueError("Ungueltiger Nextcloud-Pfad")
        return "/".join(parts)

    @staticmethod
    def _quote_path(value: str) -> str:
        return "/".join(urllib.parse.quote(part, safe="") for part in value.split("/") if part)

    def _files_root(self) -> str:
        base_url, username, _ = self.credentials()
        if not base_url or not username:
            return ""
        return f"{base_url}/remote.php/dav/files/{urllib.parse.quote(username, safe='')}/"

    def _authorization(self) -> str:
        _, username, token = self.credentials()
        encoded = base64.b64encode(f"{username}:{token}".encode("utf-8")).decode("ascii")
        return "Basic " + encoded

    def _request(
        self,
        method: str,
        remote_path: str,
        *,
        data: bytes | None = None,
        headers: dict[str, str] | None = None,
        timeout: int | None = None,
    ) -> tuple[int, str]:
        missing = self.missing_environment()
        if missing:
            return 0, "Fehlende Umgebungsvariablen: " + ", ".join(missing)
        root = self._files_root()
        clean = self._clean_remote_path(remote_path)
        url = root + self._quote_path(clean)
        request_headers = {
            "Authorization": self._authorization(),
            "User-Agent": "Local-Personal-Assistant/3.4.0",
            **(headers or {}),
        }
        request = urllib.request.Request(url, data=data, headers=request_headers, method=method)
        try:
            with urllib.request.urlopen(
                request,
                timeout=timeout or self.config.invoices.upload_timeout_seconds,
            ) as response:
                return int(response.status), str(response.reason or "")
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail = exc.read().decode("utf-8", errors="replace")[:500]
            except Exception:
                detail = ""
            return int(exc.code), detail or str(exc.reason or "HTTP-Fehler")
        except urllib.error.URLError as exc:
            return 0, str(exc)
        except TimeoutError:
            return 0, "Nextcloud-WebDAV-Timeout"

    def health(self, *, live: bool = True) -> WebDAVHealth:
        if not self.config.nextcloud.enabled:
            return WebDAVHealth(False, "Nextcloud ist deaktiviert")
        missing = self.missing_environment()
        if missing:
            return WebDAVHealth(False, "Fehlende Umgebungsvariablen: " + ", ".join(missing))
        if not live:
            return WebDAVHealth(True, "Nextcloud-Dateizugang ist lokal konfiguriert")
        status, detail = self._request("PROPFIND", "", headers={"Depth": "0"}, timeout=20)
        if status in {200, 207}:
            return WebDAVHealth(True, f"Nextcloud WebDAV erreichbar (HTTP {status})")
        return WebDAVHealth(False, f"Nextcloud WebDAV nicht erreichbar (HTTP {status or 'keine Antwort'}): {detail}")

    def ensure_folder(self, remote_folder: str) -> OperationResult:
        clean = self._clean_remote_path(remote_folder)
        if not clean:
            return OperationResult(True, "nextcloud-folder-root")
        if self.dry_run:
            return OperationResult(True, "would-create-nextcloud-folder", destination=clean)
        current: list[str] = []
        for part in clean.split("/"):
            current.append(part)
            path = "/".join(current)
            status, detail = self._request("MKCOL", path)
            if status in {201, 405}:
                continue
            return OperationResult(
                False,
                "nextcloud-folder-failed",
                f"Ordner {path}: HTTP {status or 'keine Antwort'} {detail}".strip(),
                destination=path,
            )
        return OperationResult(True, "nextcloud-folder-ready", destination=clean)

    def upload_pdf(self, remote_path: str, data: bytes) -> OperationResult:
        clean = self._clean_remote_path(remote_path)
        if not clean.casefold().endswith(".pdf"):
            return OperationResult(False, "nextcloud-upload-refused", "Nur PDF-Dateien duerfen hochgeladen werden")
        if self.dry_run:
            return OperationResult(True, "would-upload-invoice", destination=clean, path=clean)
        status, detail = self._request(
            "PUT",
            clean,
            data=data,
            headers={
                "Content-Type": "application/pdf",
                "Content-Length": str(len(data)),
                "If-None-Match": "*",
            },
        )
        if status in {200, 201, 204}:
            return OperationResult(True, "invoice-uploaded", f"Nextcloud HTTP {status}", destination=clean, path=clean)
        if status == 412:
            return OperationResult(True, "invoice-already-exists", "Datei existiert bereits", destination=clean, path=clean)
        return OperationResult(
            False,
            "invoice-upload-failed",
            f"Nextcloud HTTP {status or 'keine Antwort'}: {detail}",
            destination=clean,
            path=clean,
        )
