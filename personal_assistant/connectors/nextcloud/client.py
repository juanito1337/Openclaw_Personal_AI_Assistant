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
from ...nextcloud_access import AccessSnapshot, account_failure_observation, decide_access
from ...nextcloud_access_runtime import (
    DAV_PRINCIPAL_PROBE,
    AccessContext,
    delivery_uncertain,
    dispatch_component,
    require,
)
from ...nextcloud_connection import (
    ConnectionStore,
    admit_projection,
    connection_directory,
    load_access_snapshot,
)
from ...nextcloud_native_binding import verify_native_access


@dataclass(slots=True, frozen=True)
class DavResponse:
    status: int
    reason: str
    headers: Mapping[str, str]
    data: bytes
    url: str


class NextcloudError(RuntimeError):
    pass


class _BlockRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise NextcloudError("redirect-blocked")


class NextcloudClient:
    def __init__(self, config: AssistantConfig) -> None:
        self.config = config
        self.access_snapshot = load_access_snapshot()
        verify_native_access(self.access_snapshot)
        self.connection = self.access_snapshot.connection

    def credentials(self) -> tuple[str, str, str]:
        if self.connection:
            return (self.connection["server_url"], self.connection["login_name"],
                    self.connection["app_password"])
        require(self.access_snapshot.mode == "legacy", "scope-unbound")
        cfg = self.config.nextcloud
        return (
            os.environ.get(cfg.base_url_env, "").strip().rstrip("/"),
            os.environ.get(cfg.username_env, "").strip(),
            os.environ.get(cfg.token_env, "").strip(),
        )

    def missing_environment(self) -> list[str]:
        if self.connection:
            return []
        require(self.access_snapshot.mode == "legacy", "scope-unbound")
        cfg = self.config.nextcloud
        return [
            name
            for name in (cfg.base_url_env, cfg.username_env, cfg.token_env)
            if not os.environ.get(name, "").strip()
        ]

    def _check_managed_revision(self, snapshot: AccessSnapshot) -> None:
        if self.connection is None or snapshot.connection is None:
            raise PermissionError("policy-changed")
        require(snapshot.connection["revision"] == self.connection["revision"], "policy-changed")

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
        access: AccessContext | None = None,
    ) -> DavResponse:
        current_snapshot = load_access_snapshot()
        verify_native_access(current_snapshot)
        require(current_snapshot.mode != "unconfigured", "scope-unbound")
        if current_snapshot.mode == "active" or self.access_snapshot.mode == "active":
            require(
                current_snapshot.binding == self.access_snapshot.binding and current_snapshot.binding,
                "policy-changed",
            )
            self._check_managed_revision(current_snapshot)
            directory = connection_directory()
            if directory is None:
                raise PermissionError("policy-integrity-error")
            store = ConnectionStore(directory)

            def send(verb, target, *, headers=None, data=None):
                with store.admit(current_snapshot.binding) as admitted:
                    verify_native_access(admitted)
                    self._check_managed_revision(admitted)
                    if access is not None and access.scope.component != "FILES":
                        decision = decide_access(
                            admitted, access.scope, access.operation, expected_binding=access.binding
                        )
                        require(decision.allowed, decision.code)
                    return self._managed_request(verb, target, headers, data, timeout, follow_redirects=False)

            if access is None:
                # Only two release-owned health probes have no domain scope.
                require(self._health_request(method, path, headers, data), "scope-unbound")
                result = send(method, path, headers=headers, data=data)
            else:
                require(
                    type(access) is AccessContext and access.binding == current_snapshot.binding,
                    "policy-changed",
                )
                if access.scope.component == "FILES":
                    from ...nextcloud_file_access import filter_file_response, guard_file_request

                    guard_file_request(current_snapshot, access, method, path, headers, data, send)
                    result = filter_file_response(
                        current_snapshot,
                        access,
                        method,
                        send(method, path, headers=headers, data=data),
                        headers=headers,
                    )
                else:
                    result = dispatch_component(current_snapshot, access, method, path, headers, data, send)
            try:
                with store.admit(current_snapshot.binding) as admitted:
                    verify_native_access(admitted)
                    self._check_managed_revision(admitted)
                    if access is not None and result.status in {401, 403}:
                        code = "server-right-missing" if result.status == 403 else "rights-unverified"
                        raise PermissionError(code)
                    if expected and result.status not in expected:
                        raise NextcloudError(f"nextcloud-http-{result.status}")
                    if result.status >= 300:
                        return DavResponse(result.status, "", {}, b"", result.url)
                    return result
            except (PermissionError, RuntimeError) as exc:
                if method in {"PUT", "MKCOL"} and 200 <= result.status < 300:
                    raise delivery_uncertain(exc) from exc
                raise
        current = current_snapshot.connection
        if (current or {}).get("revision", "") != (self.connection or {}).get("revision", ""):
            raise NextcloudError("nextcloud-connection-changed: Status und Auswahl erneut pruefen")
        if self.access_snapshot.managed:
            with admit_projection(self.access_snapshot) as admitted:
                if account_failure_observation(admitted):
                    require(self._health_request(method, path, headers, data), "rights-unverified")
                result = self._legacy_request(
                    method, path, headers, data, timeout, expected, follow_redirects=False
                )
            try:
                with admit_projection(self.access_snapshot):
                    return result
            except (PermissionError, RuntimeError) as exc:
                if method in {"PUT", "MKCOL", "MOVE", "POST"} and 200 <= result.status < 300:
                    raise delivery_uncertain(exc) from exc
                raise
        return self._legacy_request(method, path, headers, data, timeout, expected, follow_redirects=True)

    @staticmethod
    def _health_request(method, path, headers, data):
        return (method == "GET" and path == "/status.php" and not headers and data is None) or (
            method == "PROPFIND"
            and path == "/remote.php/dav/"
            and data == DAV_PRINCIPAL_PROBE
            and headers == {"Depth": "0", "Content-Type": "application/xml; charset=utf-8"}
        )

    def _legacy_request(self, method, path, headers, data, timeout, expected, *, follow_redirects):
        missing = self.missing_environment()
        if missing:
            raise NextcloudError("Fehlende Umgebungsvariablen: " + ", ".join(missing))
        if self.connection:
            result = self._managed_request(
                method, path, headers, data, timeout, follow_redirects=follow_redirects
            )
            if expected and result.status not in expected:
                raise NextcloudError(f"nextcloud-http-{result.status}")
            return result
        base_url = self.validate_url()
        _, username, token = self.credentials()
        url = base_url + "/" + path.lstrip("/")
        auth = base64.b64encode(f"{username}:{token}".encode()).decode("ascii")
        request_headers = {
            "Authorization": "Basic " + auth,
            "User-Agent": "Personal-Assistant/3.4.0",
            **dict(headers or {}),
        }
        request = urllib.request.Request(url, data=data, headers=request_headers, method=method)
        try:
            limit = timeout or self.config.nextcloud.request_timeout_seconds
            context = ssl.create_default_context()
            if follow_redirects:
                incoming = urllib.request.urlopen(request, timeout=limit, context=context)
            else:
                opener = urllib.request.build_opener(
                    urllib.request.HTTPSHandler(context=context), _BlockRedirect()
                )
                incoming = opener.open(request, timeout=limit)
            with incoming as response:
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

    def _managed_request(self, method, path, headers, data, timeout, *, follow_redirects):
        from ...nextcloud_onboarding import OnboardingError, SafeNextcloudTransport

        if self.connection is None:
            raise PermissionError("scope-unbound")
        base_url, username, token = self.credentials()
        auth = base64.b64encode(f"{username}:{token}".encode()).decode("ascii")
        # The pinned transport computes the content length. It rejects an
        # independently supplied wire length.
        request_headers = {k: v for k, v in (headers or {}).items() if k.lower() != "content-length"}
        request_headers["Authorization"] = "Basic " + auth
        try:
            transport = SafeNextcloudTransport(
                base_url,
                allow_private=True,
                expected_ips=tuple(self.connection["expected_ips"]),
                timeout_seconds=timeout or self.config.nextcloud.request_timeout_seconds,
                max_response_bytes=max(self.config.search.max_file_bytes, 4 * 1024 * 1024),
            )
            target = (
                path.lstrip("/")
                if path == "/status.php" or path.startswith(("/remote.php/", "/ocs/"))
                else path
            )
            response = transport.request(
                method, target, headers=request_headers, data=data, follow_redirects=follow_redirects
            )
            return DavResponse(
                response.status, "", response.headers, response.body, transport.resolve_url(target)
            )
        except OnboardingError as exc:
            if method in {"PUT", "MKCOL", "MOVE", "POST"} and exc.code in {
                "request-timeout", "transport-error", "tls-error", "response-limit", "unsupported-encoding",
            }:
                raise delivery_uncertain(exc) from exc
            raise NextcloudError(exc.code) from exc

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
