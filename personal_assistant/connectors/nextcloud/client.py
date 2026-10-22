from __future__ import annotations

import base64
import ipaddress
import os
import ssl
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass

from ...config import AssistantConfig
from ...nextcloud_connection import load_connection


@dataclass(slots=True, frozen=True)
class DavResponse:
    status: int
    reason: str
    headers: Mapping[str, str]
    data: bytes
    url: str


class NextcloudError(RuntimeError):
    pass


class NextcloudClient:
    def __init__(self, config: AssistantConfig) -> None:
        self.config = config
        self.connection = load_connection()

    def credentials(self) -> tuple[str, str, str]:
        if self.connection:
            return (self.connection["server_url"], self.connection["login_name"],
                    self.connection["app_password"])
        cfg = self.config.nextcloud
        return (
            os.environ.get(cfg.base_url_env, "").strip().rstrip("/"),
            os.environ.get(cfg.username_env, "").strip(),
            os.environ.get(cfg.token_env, "").strip(),
        )

    def missing_environment(self) -> list[str]:
        if self.connection:
            return []
        cfg = self.config.nextcloud
        return [
            name
            for name in (cfg.base_url_env, cfg.username_env, cfg.token_env)
            if not os.environ.get(name, "").strip()
        ]

    def validate_url(self) -> str:
        base_url, _, _ = self.credentials()
        if not base_url:
            raise NextcloudError("NEXTCLOUD_URL fehlt")
        parsed = urllib.parse.urlparse(base_url)
        if parsed.scheme == "https":
            return base_url
        if parsed.scheme == "http" and os.environ.get("PERSONAL_ASSISTANT_ALLOW_HTTP") == "1":
            host = parsed.hostname or ""
            try:
                private = ipaddress.ip_address(host).is_private
            except ValueError:
                private = host in {"localhost"} or host.endswith(".local") or host.endswith(".home.arpa")
            if private:
                return base_url
        raise NextcloudError("Nextcloud muss HTTPS verwenden; HTTP ist nur mit expliziter LAN-Ausnahme erlaubt")

    def request(
        self,
        method: str,
        path: str,
        *,
        data: bytes | None = None,
        headers: Mapping[str, str] | None = None,
        timeout: int | None = None,
        expected: set[int] | None = None,
    ) -> DavResponse:
        current = load_connection()
        if (current or {}).get("revision", "") != (self.connection or {}).get("revision", ""):
            raise NextcloudError("nextcloud-connection-changed: Status und Auswahl erneut pruefen")
        missing = self.missing_environment()
        if missing:
            raise NextcloudError("Fehlende Umgebungsvariablen: " + ", ".join(missing))
        base_url = self.validate_url()
        _, username, token = self.credentials()
        url = base_url + "/" + path.lstrip("/")
        auth = base64.b64encode(f"{username}:{token}".encode()).decode("ascii")
        request_headers = {
            "Authorization": "Basic " + auth,
            "User-Agent": "Personal-Assistant/3.4.0",
            **dict(headers or {}),
        }
        if self.connection:
            from ...nextcloud_onboarding import OnboardingError, SafeNextcloudTransport

            try:
                transport = SafeNextcloudTransport(
                    base_url, allow_private=True,
                    expected_ips=tuple(self.connection["expected_ips"]),
                    timeout_seconds=timeout or self.config.nextcloud.request_timeout_seconds,
                    max_response_bytes=max(self.config.search.max_file_bytes, 4 * 1024 * 1024),
                )
                # Legacy connectors use installation-relative /remote.php and
                # /status.php. Verified DAV hrefs already carry the installation
                # prefix (or full origin) and must not receive it twice.
                managed_path = path
                if path == "/status.php" or path.startswith(("/remote.php/", "/ocs/")):
                    managed_path = path.lstrip("/")
                response = transport.request(method, managed_path, headers=request_headers, data=data)
                result = DavResponse(response.status, "", response.headers, response.body,
                                     transport.resolve_url(managed_path))
            except OnboardingError as exc:
                raise NextcloudError(exc.code) from exc
            if expected and result.status not in expected:
                raise NextcloudError(f"nextcloud-http-{result.status}")
            return result
        request = urllib.request.Request(url, data=data, headers=request_headers, method=method)
        try:
            with urllib.request.urlopen(
                request,
                timeout=timeout or self.config.nextcloud.request_timeout_seconds,
                context=ssl.create_default_context(),
            ) as response:
                result = DavResponse(
                    status=int(response.status),
                    reason=str(response.reason or ""),
                    headers={str(k): str(v) for k, v in response.headers.items()},
                    data=response.read(),
                    url=str(response.url),
                )
        except urllib.error.HTTPError as exc:
            result = DavResponse(
                status=int(exc.code),
                reason=str(exc.reason or "HTTP-Fehler"),
                headers={str(k): str(v) for k, v in exc.headers.items()},
                data=exc.read(),
                url=url,
            )
        except (urllib.error.URLError, TimeoutError, ssl.SSLError) as exc:
            raise NextcloudError(str(exc)) from exc
        if expected and result.status not in expected:
            detail = result.data.decode("utf-8", errors="replace")[:500]
            raise NextcloudError(
                f"Nextcloud {method} {path}: HTTP {result.status} {result.reason}: {detail}"
            )
        return result

    def status(self) -> dict[str, object]:
        response = self.request("GET", "/status.php", expected={200})
        import json
        try:
            payload = json.loads(response.data.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise NextcloudError("status.php lieferte kein gueltiges JSON") from exc
        if not isinstance(payload, dict):
            raise NextcloudError("status.php lieferte ein unerwartetes Format")
        return payload

    @property
    def username(self) -> str:
        if self.connection:
            return str(self.connection["user_id"])
        return self.credentials()[1]
