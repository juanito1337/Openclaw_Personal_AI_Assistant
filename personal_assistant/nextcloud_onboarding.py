"""Operator-owned Nextcloud login and read-only account discovery.

No environment mutation, configuration writes or remote resource changes happen
here. The owner binds each LoginFlow to its authenticated operator session and
persists take_credentials() separately. Never serialize credential dataclasses.
"""

from __future__ import annotations

import base64
import hashlib
import http.client
import ipaddress
import json
import math
import re
import socket
import ssl
import threading
import time
import urllib.parse
from collections.abc import Callable, Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import Any
from xml.etree import ElementTree as ET

from .nextcloud_access import MAX_RIGHTS_AGE_SECONDS

DAV = "DAV:"
CALDAV = "urn:ietf:params:xml:ns:caldav"
CARDDAV = "urn:ietf:params:xml:ns:carddav"
OC = "http://owncloud.org/ns"
MAX_RESPONSE_BYTES = 4 * 1024 * 1024
LOGIN_LIFETIME_SECONDS = 20 * 60
MAX_COLLECTION_READ_CHECKS = 32
COLLECTION_READ_BUDGET_SECONDS = 15.0
COLLECTION_READ_TIMEOUT_SECONDS = 3.0
_DAV_SUCCESS = re.compile(r"HTTP/\d(?:\.\d)? 200(?: .*)?")
_PRIVATE_NETWORKS = tuple(
    ipaddress.ip_network(value) for value in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "fc00::/7")
)


class OnboardingError(RuntimeError):
    """Safe code and fixed message: never include an upstream body or token."""

    def __init__(self, code: str, message: str = "Nextcloud-Anbindung konnte nicht abgeschlossen werden"):
        super().__init__(message)
        self.code = code
        self.diagnostics: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class NextcloudCredentials:
    server_url: str
    login_name: str
    app_password: str = field(repr=False)
    user_id: str = ""


@dataclass(frozen=True, slots=True)
class HttpResponse:
    status: int
    headers: Mapping[str, str]
    body: bytes = field(repr=False)

    def json(self) -> Any:
        try:
            return json.loads(self.body.decode("utf-8"))
        except (UnicodeError, ValueError, RecursionError):
            raise OnboardingError("invalid-json", "Nextcloud lieferte ungueltiges JSON") from None


def _valid_path(path: str) -> str:
    if re.search(r"[\x00-\x20\x7f\\]", path) or re.search(r"%(?![0-9a-fA-F]{2})", path):
        raise OnboardingError("invalid-url", "Ungueltige Nextcloud-Adresse")
    # Reject traversal even when a proxy decodes a second time. Encoded path
    # separators cannot turn a selected collection into another collection.
    decoded = path
    for _ in range(4):
        if re.search(r"%(?:2f|5c|0[0-9a-f]|1[0-9a-f]|7f)", decoded, re.IGNORECASE):
            raise OnboardingError("invalid-url", "Ungueltiger Nextcloud-Pfad")
        decoded = urllib.parse.unquote(decoded, errors="strict")
        if "\\" in decoded or any(part in {".", ".."} for part in decoded.split("/")):
            raise OnboardingError("invalid-url", "Ungueltiger Nextcloud-Pfad")
    if re.search(r"%[0-9a-fA-F]{2}", decoded):
        raise OnboardingError("invalid-url", "Mehrdeutige Pfadkodierung")
    return urllib.parse.quote(path, safe="/%:@!$&'()*+,;=-._~")


def normalize_server_url(value: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 4096:
        raise OnboardingError("invalid-url", "Eine HTTPS-Nextcloud-Adresse ist erforderlich")
    if re.search(r"[\x00-\x20\x7f\\]", value):
        raise OnboardingError("invalid-url", "Ungueltige Nextcloud-Adresse")
    try:
        parsed = urllib.parse.urlsplit(value)
        if parsed.scheme != "https" or not parsed.hostname:
            raise ValueError
        if parsed.username is not None or parsed.password is not None or parsed.query or parsed.fragment:
            raise ValueError
        host = parsed.hostname.encode("idna").decode("ascii").lower().rstrip(".")
        if "%" in host or not host:
            raise ValueError
        port = parsed.port
        if port is not None and not 1 <= port <= 65535:
            raise ValueError
        authority = f"[{host}]" if ":" in host else host
        if port not in {None, 443}:
            authority += f":{port}"
        path = _valid_path(parsed.path).rstrip("/")
        return urllib.parse.urlunsplit(("https", authority, path, "", ""))
    except (ValueError, UnicodeError):
        raise OnboardingError("invalid-url", "Ungueltige HTTPS-Nextcloud-Adresse") from None


def _resolve_addresses(host: str, port: int) -> list[str]:
    return sorted({str(item[4][0]) for item in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)})


def _checked_addresses(values: Sequence[str], *, allow_private: bool) -> list[str]:
    addresses: set[str] = set()
    for value in values:
        try:
            address = ipaddress.ip_address(value)
        except ValueError:
            raise OnboardingError("unsafe-address", "Ungueltige Nextcloud-Zieladresse") from None
        if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
            address = address.ipv4_mapped
        if str(address) in {"fd00:ec2::254", "168.63.129.16"} or getattr(address, "scope_id", None):
            raise OnboardingError("unsafe-address", "Nextcloud-Zieladresse ist nicht erlaubt")
        private = any(
            address.version == network.version and address in network for network in _PRIVATE_NETWORKS
        )
        if address.is_loopback or address.is_link_local or address.is_multicast or address.is_unspecified:
            raise OnboardingError("unsafe-address", "Nextcloud-Zieladresse ist nicht erlaubt")
        if not address.is_global and not (allow_private and private):
            raise OnboardingError("unsafe-address", "Nextcloud-Zieladresse ist nicht erlaubt")
        addresses.add(str(address))
    if not addresses:
        raise OnboardingError("dns-failed", "Nextcloud-Hostname konnte nicht aufgeloest werden")
    return sorted(addresses)


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, host: str, port: int, address: str, timeout: float, context: ssl.SSLContext):
        super().__init__(host, port, timeout=timeout, context=context)
        self._address = address
        self._tls_context = context

    def connect(self) -> None:
        # Connect to the validated address, retaining the hostname for SNI and
        # certificate validation. No second DNS lookup or environment proxy.
        raw = socket.create_connection((self._address, self.port), self.timeout)
        try:
            self.sock = self._tls_context.wrap_socket(raw, server_hostname=self.host)
        except BaseException:
            raw.close()
            raise


def _expire_request(connection: Any, expired: threading.Event) -> None:
    """Interrupt slow header/body streams, rather than resetting a recv timeout."""
    expired.set()
    sock = getattr(connection, "sock", None)
    if sock is not None:
        with suppress(OSError):
            sock.shutdown(socket.SHUT_RDWR)


class SafeNextcloudTransport:
    """HTTPS bound to one operator-selected origin, installation path and IP set.

    API paths are relative (``ocs/v2.php/...``). Leading slash paths are server
    absolute DAV hrefs and must already include the installation subpath.
    """

    def __init__(
        self,
        base_url: str,
        *,
        allow_private: bool = True,
        expected_ips: Sequence[str] = (),
        timeout_seconds: float = 20,
        max_response_bytes: int = MAX_RESPONSE_BYTES,
        resolver: Callable[[str, int], Sequence[str]] = _resolve_addresses,
        connection_factory: Callable[..., Any] = _PinnedHTTPSConnection,
    ) -> None:
        self.base_url = normalize_server_url(base_url)
        self._base = urllib.parse.urlsplit(self.base_url)
        self._host = str(self._base.hostname)
        self._port = self._base.port or 443
        if not math.isfinite(timeout_seconds) or not 0 < timeout_seconds <= 120:
            raise OnboardingError("invalid-limits", "Ungueltiges Request-Zeitlimit")
        if type(max_response_bytes) is not int or not 1024 <= max_response_bytes <= 1024 * 1024 * 1024:
            raise OnboardingError("invalid-limits", "Ungueltiges Antwortlimit")
        self._timeout = timeout_seconds
        self._maximum = max_response_bytes
        self._resolver = resolver
        self._factory = connection_factory
        self._allow_private = allow_private
        self._context = ssl.create_default_context()
        current = self._resolve()
        supplied = _checked_addresses(expected_ips, allow_private=allow_private) if expected_ips else current
        if supplied != current:
            raise OnboardingError("dns-changed", "Nextcloud-Zieladressen haben sich geaendert")
        self._ips = tuple(supplied)

    @property
    def expected_ips(self) -> list[str]:
        return list(self._ips)

    def _resolve(self) -> list[str]:
        try:
            values = self._resolver(self._host, self._port)
        except OSError:
            raise OnboardingError("dns-failed", "Nextcloud-Hostname konnte nicht aufgeloest werden") from None
        return _checked_addresses(values, allow_private=self._allow_private)

    def resolve_url(self, value: str, *, relative_to: str | None = None) -> str:
        if not isinstance(value, str) or len(value) > 8192 or not value:
            raise OnboardingError("invalid-url", "Ungueltige Nextcloud-Zieladresse")
        if re.search(r"[\x00-\x20\x7f\\]", value) or value.startswith("//"):
            raise OnboardingError("invalid-url", "Ungueltige Nextcloud-Zieladresse")
        try:
            raw = urllib.parse.urlsplit(value)
            _valid_path(raw.path)
            url = urllib.parse.urljoin(relative_to or self.base_url + "/", value)
            parsed = urllib.parse.urlsplit(url)
            if parsed.username is not None or parsed.password is not None or parsed.fragment:
                raise ValueError
            origin = normalize_server_url(urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, "", "", "")))
            if origin != urllib.parse.urlunsplit((self._base.scheme, self._base.netloc, "", "", "")):
                raise OnboardingError("foreign-origin", "Nextcloud verweist auf eine andere Serveradresse")
            path = _valid_path(parsed.path or "/")
            prefix = self._base.path
            if prefix and path != prefix and not path.startswith(prefix + "/"):
                raise OnboardingError("foreign-path", "Nextcloud-Ziel liegt ausserhalb der Installation")
            return urllib.parse.urlunsplit(("https", self._base.netloc, path, parsed.query, ""))
        except (ValueError, UnicodeError):
            raise OnboardingError("invalid-url", "Ungueltige Nextcloud-Zieladresse") from None

    def request(
        self,
        method: str,
        path_or_url: str,
        *,
        headers: Mapping[str, str] | None = None,
        data: bytes | None = None,
        timeout_seconds: float | None = None,
        follow_redirects: bool = True,
    ) -> HttpResponse:
        if method not in {"GET", "HEAD", "POST", "PROPFIND", "REPORT", "PUT", "MKCOL", "MOVE"}:
            raise OnboardingError("method-not-allowed", "Requestmethode ist nicht erlaubt")
        url = self.resolve_url(path_or_url)
        request_headers = {"User-Agent": "OpenClaw-Personal-Assistant/Nextcloud-Setup", **dict(headers or {})}
        for key, value in request_headers.items():
            if not re.fullmatch(r"[A-Za-z0-9-]+", key) or re.search(r"[\r\n\x00]", value):
                raise OnboardingError("invalid-header", "Ungueltige Requestheader")
            if key.lower() in {
                "host",
                "connection",
                "transfer-encoding",
                "content-length",
                "accept-encoding",
            }:
                raise OnboardingError("invalid-header", "Requestheader ist nicht erlaubt")
        if data is not None and len(data) > self._maximum:
            raise OnboardingError("request-limit", "Nextcloud-Anfrage ist zu gross")
        if timeout_seconds is not None and (
            not math.isfinite(timeout_seconds) or timeout_seconds <= 0
        ):
            raise OnboardingError("invalid-limits", "Ungueltiges Request-Zeitlimit")
        deadline = time.monotonic() + timeout_seconds if timeout_seconds is not None else None
        for redirect in range(4):
            if self._resolve() != list(self._ips):
                raise OnboardingError("dns-changed", "Nextcloud-Zieladressen haben sich geaendert")
            parsed = urllib.parse.urlsplit(url)
            target = parsed.path + ("?" + parsed.query if parsed.query else "")
            connection = None
            timer = None
            expired = threading.Event()
            try:
                timeout = self._timeout
                if deadline is not None:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise OnboardingError("request-timeout", "Nextcloud-Pruefzeitlimit erreicht")
                    timeout = min(timeout, remaining)
                connection = self._factory(self._host, self._port, self._ips[0], timeout, self._context)
                if deadline is not None:
                    timer = threading.Timer(timeout, _expire_request, args=(connection, expired))
                    timer.daemon = True
                    timer.start()
                connection.request(method, target, body=data, headers=request_headers)
                if expired.is_set():
                    raise OnboardingError("request-timeout", "Nextcloud-Pruefzeitlimit erreicht")
                incoming = connection.getresponse()
                response_headers = {key.lower(): value for key, value in incoming.getheaders()}
                length = response_headers.get("content-length")
                if length is not None and (not length.isdecimal() or int(length) > self._maximum):
                    raise OnboardingError("response-limit", "Nextcloud-Antwort ist zu gross oder ungueltig")
                body = incoming.read(self._maximum + 1)
                if expired.is_set():
                    raise OnboardingError("request-timeout", "Nextcloud-Pruefzeitlimit erreicht")
                if len(body) > self._maximum:
                    raise OnboardingError("response-limit", "Nextcloud-Antwort ist zu gross")
                if response_headers.get("content-encoding", "identity").lower() != "identity":
                    raise OnboardingError("unsupported-encoding", "Unerwartete Antwortkompression")
                response = HttpResponse(int(incoming.status), response_headers, body)
            except ssl.SSLError:
                if expired.is_set():
                    raise OnboardingError("request-timeout", "Nextcloud-Pruefzeitlimit erreicht") from None
                raise OnboardingError("tls-error", "Nextcloud-TLS-Pruefung fehlgeschlagen") from None
            except (OSError, http.client.HTTPException):
                if expired.is_set():
                    raise OnboardingError("request-timeout", "Nextcloud-Pruefzeitlimit erreicht") from None
                raise OnboardingError("transport-error", "Nextcloud ist momentan nicht erreichbar") from None
            finally:
                if timer is not None:
                    timer.cancel()
                if connection is not None:
                    connection.close()
            if response.status not in {301, 302, 303, 307, 308}:
                return response
            if not follow_redirects:
                raise OnboardingError("redirect-blocked", "Weiterleitung fuer Fachzugriff gesperrt")
            if redirect == 3:
                raise OnboardingError("redirect-limit", "Zu viele Nextcloud-Weiterleitungen")
            if method != "GET" and response.status not in {307, 308}:
                raise OnboardingError(
                    "redirect-method-change", "Nextcloud-Weiterleitung aendert die Requestmethode"
                )
            url = self.resolve_url(response.headers.get("location", ""), relative_to=url)
        raise AssertionError("unreachable")


def credential_headers(credentials: NextcloudCredentials) -> dict[str, str]:
    """Backend-only headers. Never include them in diagnostics or UI payloads."""
    if (
        not credentials.login_name
        or not credentials.app_password
        or ":" in credentials.login_name
        or any(ord(char) < 32 for char in credentials.login_name + credentials.app_password)
    ):
        raise OnboardingError("invalid-credentials", "Ungueltige Nextcloud-Anmeldedaten")
    token = base64.b64encode(f"{credentials.login_name}:{credentials.app_password}".encode()).decode("ascii")
    return {"Authorization": "Basic " + token, "OCS-APIRequest": "true", "Accept": "application/json"}


def _http_ok(response: HttpResponse, expected: int = 200) -> None:
    if response.status == expected:
        return
    code = {401: "authentication-failed", 403: "permission-denied", 404: "not-found", 429: "rate-limited"}
    raise OnboardingError(code.get(response.status, "upstream-error"), "Nextcloud-Anfrage wurde abgewiesen")


class LoginFlow:
    """One ephemeral login. Its owning HTTP service must authenticate each call."""

    def __init__(self, transport: SafeNextcloudTransport, *, clock: Callable[[], float] = time.monotonic):
        self.transport = transport
        self._clock = clock
        self._lock = threading.RLock()
        self._state = "new"
        self._expires = 0.0
        self._last_poll = -math.inf
        self._login_url = ""
        self._poll_url = ""
        self._token = ""
        self._credentials: NextcloudCredentials | None = None

    def _clear(self) -> None:
        self._token = ""
        self._poll_url = ""
        self._login_url = ""

    def _view(self, *, include_login: bool = False) -> dict[str, Any]:
        result: dict[str, Any] = {"state": self._state}
        if self._state == "pending":
            result["expires_in"] = max(0, int(self._expires - self._clock()))
            if include_login:
                result["login_url"] = self._login_url
        return result

    def start(self) -> dict[str, Any]:
        with self._lock:
            if self._state != "new":
                raise OnboardingError("login-already-started", "Anmeldevorgang wurde bereits gestartet")
            response = self.transport.request("POST", "index.php/login/v2", data=b"")
            _http_ok(response)
            payload = response.json()
            try:
                poll = payload["poll"]
                token = poll["token"]
                if not isinstance(token, str) or not 16 <= len(token) <= 4096:
                    raise ValueError
                login_url = self.transport.resolve_url(payload["login"])
                poll_url = self.transport.resolve_url(poll["endpoint"])
            except (KeyError, TypeError, ValueError):
                raise OnboardingError(
                    "invalid-login-response", "Nextcloud lieferte keinen Anmeldevorgang"
                ) from None
            self._token, self._login_url, self._poll_url = token, login_url, poll_url
            self._state = "pending"
            self._expires = self._clock() + LOGIN_LIFETIME_SECONDS
            return self._view(include_login=True)

    def poll(self) -> dict[str, Any]:
        with self._lock:
            if self._state != "pending":
                return self._view()
            now = self._clock()
            if now >= self._expires:
                self._state = "expired"
                self._clear()
                return self._view()
            if now - self._last_poll < 1:
                return self._view()
            self._last_poll = now
            response = self.transport.request(
                "POST",
                self._poll_url,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                data=urllib.parse.urlencode({"token": self._token}).encode(),
            )
            if response.status == 404:
                return self._view()
            if response.status != 200:
                if response.status in {400, 401, 403, 410}:
                    self._state = "failed"
                    self._clear()
                _http_ok(response)
            # A successful poll consumes its server token. Malformed credentials
            # are terminal; retrying cannot retrieve that one-time response.
            try:
                payload = response.json()
                server = normalize_server_url(payload["server"])
                if server != self.transport.base_url:
                    raise OnboardingError("server-changed", "Nextcloud meldet eine andere Basisadresse")
                login, password = payload["loginName"], payload["appPassword"]
                if not isinstance(login, str) or not isinstance(password, str) or len(password) > 4096:
                    raise ValueError
                credentials = NextcloudCredentials(server, login, password)
                credential_headers(credentials)
            except (KeyError, TypeError, ValueError, OnboardingError) as exc:
                self._state = "failed"
                self._clear()
                if isinstance(exc, OnboardingError):
                    raise
                raise OnboardingError(
                    "invalid-login-response", "Nextcloud lieferte keine Anmeldedaten"
                ) from None
            self._credentials = credentials
            self._state = "connected"
            self._clear()
            return self._view()

    def take_credentials(self) -> NextcloudCredentials:
        with self._lock:
            if self._state != "connected" or self._credentials is None:
                raise OnboardingError("credentials-unavailable", "Keine neuen Anmeldedaten vorhanden")
            result, self._credentials = self._credentials, None
            return result

    def cancel(self) -> dict[str, Any]:
        with self._lock:
            self._clear()
            self._credentials = None
            self._state = "cancelled"
            return self._view()


def _auth_request(
    transport: SafeNextcloudTransport,
    credentials: NextcloudCredentials,
    method: str,
    path: str,
    *,
    headers: Mapping[str, str] | None = None,
    data: bytes | None = None,
    timeout_seconds: float | None = None,
) -> HttpResponse:
    if normalize_server_url(credentials.server_url) != transport.base_url:
        raise OnboardingError("account-mismatch", "Zugangsdaten gehoeren zu einer anderen Nextcloud")
    options: dict[str, Any] = {}
    if timeout_seconds is not None:
        options["timeout_seconds"] = timeout_seconds
    return transport.request(method, path, headers={**credential_headers(credentials), **dict(headers or {})},
                             data=data, **options)


def _ocs(transport: SafeNextcloudTransport, credentials: NextcloudCredentials, path: str) -> Any:
    response = _auth_request(transport, credentials, "GET", path)
    _http_ok(response)
    payload = response.json()
    try:
        ocs = payload["ocs"]
        meta = ocs["meta"]
        if meta["status"] != "ok" or int(meta["statuscode"]) not in {100, 200}:
            raise ValueError
        return ocs["data"]
    except (KeyError, TypeError, ValueError):
        raise OnboardingError("ocs-error", "Nextcloud meldet einen OCS-Fehler") from None


def _q(namespace: str, name: str) -> str:
    return "{" + namespace + "}" + name


@dataclass(slots=True)
class _DavItem:
    href: str
    properties: dict[str, ET.Element]
    status: str


def _propfind(
    transport: SafeNextcloudTransport,
    credentials: NextcloudCredentials,
    path: str,
    properties: Sequence[str],
    *,
    depth: int,
    timeout_seconds: float | None = None,
) -> list[_DavItem]:
    root = ET.Element(_q(DAV, "propfind"))
    prop = ET.SubElement(root, _q(DAV, "prop"))
    for name in properties:
        ET.SubElement(prop, name)
    response = _auth_request(
        transport,
        credentials,
        "PROPFIND",
        path,
        headers={"Depth": str(depth), "Content-Type": "application/xml; charset=utf-8"},
        data=ET.tostring(root, encoding="utf-8", xml_declaration=True),
        timeout_seconds=timeout_seconds,
    )
    _http_ok(response, 207)
    if b"<!DOCTYPE" in response.body.upper() or b"<!ENTITY" in response.body.upper():
        raise OnboardingError("invalid-dav", "Unerlaubte DAV-XML-Deklaration")
    try:
        multistatus = ET.fromstring(response.body)
    except (ET.ParseError, ValueError):
        raise OnboardingError("invalid-dav", "Nextcloud lieferte ungueltiges DAV-XML") from None
    if multistatus.tag != _q(DAV, "multistatus"):
        raise OnboardingError("invalid-dav", "Nextcloud lieferte keinen DAV-Multistatus")
    items: list[_DavItem] = []
    for child in multistatus.findall(_q(DAV, "response")):
        hrefs = child.findall(_q(DAV, "href"))
        if len(hrefs) != 1 or not hrefs[0].text:
            raise OnboardingError("ambiguous-dav", "DAV-Identitaet ist nicht eindeutig")
        raw_href = hrefs[0].text
        absolute = transport.resolve_url(raw_href)
        parsed = urllib.parse.urlsplit(absolute)
        if parsed.query:
            raise OnboardingError("invalid-dav", "DAV-Ressource enthaelt einen unerwarteten Query")
        response_statuses = child.findall(_q(DAV, "status"))
        if len(response_statuses) > 1:
            raise OnboardingError("ambiguous-dav", "Nextcloud lieferte mehrere DAV-Ressourcenstatus")
        status = ""
        if response_statuses:
            status_node = response_statuses[0]
            if len(status_node) or not status_node.text:
                raise OnboardingError(
                    "invalid-dav", "Nextcloud lieferte einen ungueltigen DAV-Ressourcenstatus"
                )
            status = status_node.text
        values: dict[str, ET.Element] = {}
        seen_properties: set[str] = set()
        for propstat in child.findall(_q(DAV, "propstat")):
            statuses = propstat.findall(_q(DAV, "status"))
            prop_nodes = propstat.findall(_q(DAV, "prop"))
            if len(statuses) > 1 or len(prop_nodes) > 1:
                raise OnboardingError(
                    "ambiguous-dav", "Nextcloud lieferte einen mehrdeutigen DAV-Eigenschaftsstatus"
                )
            if not statuses or not prop_nodes or len(statuses[0]):
                raise OnboardingError(
                    "invalid-dav", "Nextcloud lieferte einen ungueltigen DAV-Eigenschaftsstatus"
                )
            success = _DAV_SUCCESS.fullmatch(statuses[0].text or "") is not None
            for item in prop_nodes[0]:
                if item.tag in seen_properties:
                    raise OnboardingError("ambiguous-dav", "Nextcloud lieferte doppelte DAV-Eigenschaften")
                seen_properties.add(item.tag)
                if success:
                    values[item.tag] = item
        items.append(_DavItem(parsed.path, values, status))
    if len({item.href.rstrip("/") for item in items}) != len(items):
        raise OnboardingError("ambiguous-dav", "Nextcloud lieferte doppelte DAV-Ressourcen")
    return items


def _text(item: _DavItem, namespace: str, name: str) -> str:
    element = item.properties.get(_q(namespace, name))
    return "".join(element.itertext()).strip() if element is not None else ""


def _privileges(item: _DavItem) -> dict[str, Any]:
    element = item.properties.get(_q(DAV, "current-user-privilege-set"))
    names = set()
    if element is not None:
        names = {child.tag for node in element.findall(_q(DAV, "privilege")) for child in node}
    values = {name.removeprefix("{DAV:}") for name in names if name.startswith("{DAV:}")}
    flags = {
        "can_read": bool(values & {"read", "all"}),
        "can_create": bool(values & {"bind", "write", "all"}),
        "can_update": bool(values & {"write-content", "write", "all"}),
    }
    return {
        **flags,
        "privileges": sorted(names),
        "privileges_verified": element is not None,
        "permissions": [
            permission for permission in ("read", "create", "update") if flags["can_" + permission]
        ],
    }


def _resource(item: _DavItem, kind: str) -> dict[str, Any]:
    return {
        "resource_id": "nextcloud-" + kind + "-" + hashlib.sha256(item.href.encode()).hexdigest()[:12],
        "kind": kind,
        "href": item.href,
        "remote_id": item.href,
        "name": _text(item, DAV, "displayname")
        or urllib.parse.unquote(item.href.rstrip("/").rsplit("/", 1)[-1]),
        **_privileges(item),
    }


def _home_hrefs(transport: SafeNextcloudTransport, item: _DavItem, namespace: str, name: str) -> list[str]:
    element = item.properties.get(_q(namespace, name))
    if element is None:
        raise OnboardingError("dav-home-missing", "Nextcloud meldet keine DAV-Home-Adresse")
    hrefs = {transport.resolve_url(node.text or "") for node in element.findall(_q(DAV, "href"))}
    if not hrefs:
        raise OnboardingError("dav-home-missing", "Nextcloud meldet keine DAV-Home-Adresse")
    return sorted(hrefs)


def _one_item(items: list[_DavItem], transport: SafeNextcloudTransport, path: str) -> _DavItem:
    target = urllib.parse.urlsplit(transport.resolve_url(path)).path.rstrip("/")
    selected = [item for item in items if item.href.rstrip("/") == target]
    if len(selected) != 1:
        raise OnboardingError("ambiguous-dav", "DAV-Identitaet ist nicht eindeutig")
    return selected[0]


def _resource_limit(value: int) -> int:
    if type(value) is not int or not 1 <= value <= 10000:
        raise OnboardingError("invalid-limits", "Ungueltiges Discoverylimit")
    return value


def _read_check(status: str, code: str = "", *, scope: str = "collection-metadata") -> dict[str, Any]:
    return {"status": status, "code": code, "scope": scope, "content_checked": False}


def _check_item_status(item: _DavItem) -> None:
    if item.status and not _DAV_SUCCESS.fullmatch(item.status):
        raise OnboardingError("resource-unavailable", "DAV-Ressource konnte nicht gelesen werden")


def _check_collection_read(
    transport: SafeNextcloudTransport, credentials: NextcloudCredentials,
    resource: dict[str, Any], *, timeout_seconds: float,
) -> dict[str, Any]:
    """Read this collection's metadata, without enumerating or downloading objects."""
    is_calendar = resource["kind"] == "calendar"
    collection_type = _q(CALDAV, "calendar") if is_calendar else _q(CARDDAV, "addressbook")
    components_property = _q(CALDAV, "supported-calendar-component-set")
    properties = [_q(DAV, "resourcetype"), _q(DAV, "current-user-privilege-set")]
    if is_calendar:
        properties.append(components_property)
    try:
        items = _propfind(
            transport, credentials, resource["href"], properties,
            depth=0, timeout_seconds=timeout_seconds,
        )
        item = _one_item(items, transport, resource["href"])
        if len(items) != 1:
            raise OnboardingError("invalid-dav")
        _check_item_status(item)
        resource_type = item.properties.get(_q(DAV, "resourcetype"))
        if (resource_type is None or resource_type.find(_q(DAV, "collection")) is None
                or resource_type.find(collection_type) is None):
            raise OnboardingError("resource-type-unavailable")
        if is_calendar:
            components = item.properties.get(components_property)
            names = {node.get("name", "").upper() for node in components
                     if node.tag == _q(CALDAV, "comp")} if components is not None else set()
            if not names or not set(resource["components"]) <= names:
                raise OnboardingError("components-unavailable")
        rights = _privileges(item)
        if not resource["privileges_verified"] or not rights["privileges_verified"]:
            raise OnboardingError("privileges-unavailable")
        if not resource["can_read"] or not rights["can_read"]:
            raise OnboardingError("read-permission-missing")
    except OnboardingError as exc:
        return _read_check("failed", exc.code)
    return {
        **_read_check("ok"),
        "permissions": [
            permission for permission in rights["permissions"] if permission in resource["permissions"]
        ],
    }


def _previous_checks(snapshot, previous, domains):
    identity = ("server_url", "user_id", "account_id", "principal_href")
    if not previous or any(not snapshot.get(k) or snapshot[k] != previous.get(k) for k in identity):
        return {}
    rows: dict[tuple[str, str], dict | None] = {}
    for domain in domains:
        for row in previous.get(domain, ()):
            if not isinstance(row, dict) or not all(isinstance(row.get(k), str) for k in ("kind", "href")):
                continue
            key = (row["kind"], row["href"])
            # Shared calendar/task listings must describe the very same observation.
            if key not in rows:
                rows[key] = row
            elif rows[key] != row:
                rows[key] = None
    return rows


def _check_collections(transport, credentials, snapshot, previous, domains, started):
    prior = _previous_checks(snapshot, previous, domains)
    current: dict[tuple[str, str], list[dict]] = {}
    for domain in domains:
        for row in snapshot[domain]:
            current.setdefault((row["kind"], row["href"]), []).append(row)

    reusable = {}
    evidence_fields = ("kind", "href", "resource_id", "remote_id", "components", "privileges_verified",
                       "privileges", "permissions", "can_read", "can_create", "can_update")
    for key, listings in current.items():
        old = prior.get(key)
        if not old or any(old.get(k) != listings[0].get(k) for k in evidence_fields):
            continue
        check = old.get("read_check")
        if (
            not isinstance(check, dict)
            or check.get("status") not in {"ok", "failed"}
            or check.get("scope") != "collection-metadata"
            or check.get("content_checked") is not False
        ):
            continue
        if check["status"] == "ok":
            rights = check.get("permissions")
            if (
                old.get("privileges_verified") is not True or old.get("can_read") is not True
                or not isinstance(rights, list) or "read" not in rights
                or any(right not in old.get("permissions", ()) for right in rights)
            ):
                continue
        try:
            measured = datetime.fromisoformat(old["checked_at"])
            age = (started - measured).total_seconds()
        except (KeyError, ValueError, TypeError, OverflowError):
            continue
        if 0 <= age < MAX_RIGHTS_AGE_SECONDS:
            reusable[key] = (measured, old["checked_at"], check)

    # Unmeasured, changed and expired rows first. Each failed own probe also
    # receives an attempt time so a failing prefix cannot monopolize the budget.
    never_checked = datetime.min.replace(tzinfo=UTC)
    ordered = sorted(current, key=lambda key: reusable[key][0] if key in reusable else never_checked)
    deadline = time.monotonic() + COLLECTION_READ_BUDGET_SECONDS
    oldest = started
    for index, key in enumerate(ordered):
        remaining = deadline - time.monotonic()
        if index < MAX_COLLECTION_READ_CHECKS and remaining > 0:
            source = "probed"
            stamp = datetime.now(UTC).isoformat()
            check = _check_collection_read(
                transport, credentials, current[key][0],
                timeout_seconds=min(COLLECTION_READ_TIMEOUT_SECONDS, remaining),
            )
        elif (
            key in reusable
            and (datetime.now(UTC) - reusable[key][0]).total_seconds() < MAX_RIGHTS_AGE_SECONDS
        ):
            source = "retained"
            measured, stamp, check = reusable[key]
            oldest = min(oldest, measured)
        else:
            code = "read-check-limit" if index >= MAX_COLLECTION_READ_CHECKS else "read-check-budget"
            snapshot["limitations"].append(code)
            source, stamp, check = "skipped", None, _read_check("skipped", code)
        for row in current[key]:
            row.update(checked_at=stamp, read_check=check, read_check_source=source)
    if oldest < started:
        snapshot["oldest_collection_check_at"] = oldest.isoformat()


def _collection_diagnostic(
    resources: list[dict[str, Any]], *, inventory_complete: bool, discovered: int,
    errors: list[dict[str, Any]], limitations: list[str],
) -> dict[str, Any]:
    checked = sum(item["read_check"]["status"] != "skipped" for item in resources)
    readable = sum(item["read_check"]["status"] == "ok" for item in resources)
    failed = checked - readable
    skipped = discovered - checked
    if not inventory_complete or skipped or failed:
        status = "partial" if readable or skipped else "failed"
    else:
        status = "ok" if discovered else "empty"
    return {
        "status": status, "inventory_complete": inventory_complete, "discovered": discovered,
        "checked": checked, "readable": readable, "failed": failed, "skipped": skipped,
        "probed": sum(item.get("read_check_source") == "probed" for item in resources),
        "retained": sum(item.get("read_check_source") == "retained" for item in resources),
        "checks_complete": inventory_complete and skipped == 0,
        "content_checked": False, "scope": "collection-metadata", "errors": errors,
        "limitations": sorted(set(limitations)),
    }


def _talk_diagnostic(status: str, code: str = "") -> dict[str, Any]:
    """Room metadata evidence only; send permissions never prove message delivery."""
    return {
        "status": status, "app_available": None, "api_reachable": None,
        "reference_id_supported": None, "inventory_complete": False,
        "discovered": 0, "writable": 0, "send_unconfirmed": 0,
        "code": code, "errors": [], "limitations": [],
        "scope": "room-metadata", "content_checked": False,
    }


def user_failure_diagnostics(code: str) -> dict[str, Any]:
    downstream = _collection_diagnostic(
        [], inventory_complete=False, discovered=0, errors=[], limitations=["user-unavailable"]
    )
    downstream["status"] = "skipped"
    talk = _talk_diagnostic("skipped", "user-unavailable")
    talk["limitations"] = ["user-unavailable"]
    if code in {
        "authentication-failed",
        "permission-denied",
        "not-found",
        "rate-limited",
        "upstream-error",
        "invalid-json",
        "ocs-error",
        "invalid-user-id",
        "account-changed",
        "response-limit",
        "unsupported-encoding",
    }:
        availability = {"status": "ok", "code": ""}
    elif code in {"transport-error", "request-timeout", "dns-failed", "dns-changed", "tls-error"}:
        availability = {"status": "failed", "code": code}
    else:
        availability = {"status": "skipped", "code": code}
    return {
        "schema_version": 1, "availability": availability,
        "user": {"status": "failed", "loaded": False, "code": code},
        "calendars": dict(downstream),
        "task_lists": dict(downstream),
        "addressbooks": dict(downstream),
        "talk": talk,
        "files": {"status": "skipped", "inventory_complete": False, "scope": "first-level",
                  "depth": 1, "root_readable": False, "discovered": 0, "content_checked": False,
                  "recursive": False, "errors": [], "limitations": ["user-unavailable"]},
    }


def discover_folders(
    transport: SafeNextcloudTransport,
    credentials: NextcloudCredentials,
    path: str = "",
    *,
    max_resources: int = 1000,
) -> dict[str, Any]:
    """List one directory level, including its own capabilities; never crawl."""
    _resource_limit(max_resources)
    uid = credentials.user_id
    if not uid or any(char in uid for char in "/\\"):
        raise OnboardingError("user-id-missing", "Bestaetigte Nextcloud-Benutzer-ID fehlt")
    clean = path.strip("/")
    encoded = "/".join(urllib.parse.quote(part, safe="") for part in clean.split("/"))
    _valid_path(encoded)
    target = "remote.php/dav/files/" + urllib.parse.quote(uid, safe="") + "/" + encoded
    items = _propfind(
        transport,
        credentials,
        target,
        [
            _q(DAV, "displayname"),
            _q(DAV, "resourcetype"),
            _q(DAV, "current-user-privilege-set"),
            _q(OC, "fileid"),
            _q(OC, "permissions"),
        ],
        depth=1,
    )
    own = _one_item(items, transport, target)
    _check_item_status(own)
    root_path = urllib.parse.urlsplit(
        transport.resolve_url("remote.php/dav/files/" + urllib.parse.quote(uid, safe="") + "/")
    ).path
    folders = []
    errors = []
    for item in items:
        resource_type = item.properties.get(_q(DAV, "resourcetype"))
        own_path = own.href.rstrip("/")
        if not item.href.startswith(root_path) or not (
            item.href.rstrip("/") == own_path or item.href.startswith(own_path + "/")
        ):
            raise OnboardingError("foreign-path", "Dateiressource liegt ausserhalb des ausgewaehlten Ordners")
        if item is not own and "/" in item.href[len(own_path) + 1:].strip("/"):
            raise OnboardingError("invalid-dav", "Nextcloud lieferte mehr als eine Ordnerebene")
        try:
            _check_item_status(item)
        except OnboardingError as exc:
            errors.append({"domain": "files", "code": exc.code})
            continue
        if resource_type is None:
            errors.append({"domain": "files", "code": "properties-unavailable"})
            continue
        if resource_type.find(_q(DAV, "collection")) is None:
            continue
        folder = _resource(item, "file-root" if item is own and not clean else "folder")
        permission_element = item.properties.get(_q(OC, "permissions"))
        flags = _text(item, OC, "permissions")
        verified = (
            permission_element is not None
            and len(permission_element) == 0
            and re.fullmatch(r"[A-Z]{0,64}", flags) is not None
        )
        rights = set(flags) if verified else set()
        can_read, can_create_file, can_mkdir, can_update = (flag in rights for flag in "GCKW")
        folder.update(
            can_read=can_read, can_create=can_create_file or can_mkdir, can_update=can_update,
            can_create_file=can_create_file if verified else None, can_mkdir=can_mkdir if verified else None,
            privileges_verified=verified, file_permissions=flags if verified else None,
            permissions=[permission for permission, allowed in (
                ("read", can_read), ("create", can_create_file or can_mkdir), ("update", can_update),
            ) if allowed],
        )
        fileid_element = item.properties.get(_q(OC, "fileid"))
        file_id = _text(item, OC, "fileid")
        folder["file_id"] = (file_id if fileid_element is not None and len(fileid_element) == 0
                             and re.fullmatch(r"[1-9][0-9]{0,31}", file_id) else "")
        folder["path"] = urllib.parse.unquote(item.href[len(root_path) :].strip("/"))
        folder["read_check"] = _read_check("skipped", "folder-not-opened", scope="first-level")
        folders.append(folder)
    if not any(folder["href"] == own.href for folder in folders):
        raise OnboardingError("not-a-folder", "Ausgewaehlte DAV-Ressource ist kein Ordner")
    folders.sort(key=lambda item: (item["href"] != own.href, item["name"].casefold(), item["href"]))
    truncated = len(folders) > max_resources
    inventory_complete = not truncated and not errors
    selected = folders[0]
    code = ("privileges-unavailable" if not selected["privileges_verified"] else
            "read-permission-missing" if not selected["can_read"] else
            "file-id-unavailable" if not selected["file_id"] else "")
    selected["read_check"] = _read_check("failed" if code else "ok", code, scope="first-level")
    if code:
        errors.append({"domain": "files", "code": code, "resource_id": selected["resource_id"]})
    return {
        "path": clean,
        "folders": folders[:max_resources],
        "checked_at": datetime.now(UTC).isoformat(),
        "selected_folder": {
            "path": selected["path"], "file_id": selected["file_id"] or None, "href": selected["href"],
            "can_read": selected["can_read"] if selected["privileges_verified"] else None,
            "can_create_file": selected["can_create_file"], "can_mkdir": selected["can_mkdir"],
            "read_check": selected["read_check"],
        },
        "depth": 1,
        "inventory_complete": inventory_complete,
        "discovered": len(folders),
        "complete": not truncated and not errors,
        "errors": errors,
        "limitations": ["resource-limit"] if truncated else [],
        "results_may_be_truncated": truncated,
    }


def discover_account(
    transport: SafeNextcloudTransport,
    credentials: NextcloudCredentials,
    *,
    max_resources: int = 1000,
    previous_snapshot: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Discover all account collections and a bounded first folder level.

    No resource is implicitly selected or enabled. Missing advertised privileges
    never become writable/readable rights merely because PROPFIND succeeded.
    """
    _resource_limit(max_resources)
    checked_at = datetime.now(UTC).isoformat()
    try:
        user = _ocs(transport, credentials, "ocs/v1.php/cloud/user")
        uid = user.get("id") if isinstance(user, dict) else None
        if not isinstance(uid, str) or not uid or len(uid) > 512 or re.search(r"[\x00-\x1f/\\]", uid):
            raise OnboardingError("invalid-user-id", "Nextcloud meldet keine eindeutige Benutzer-ID")
        if credentials.user_id and credentials.user_id != uid:
            raise OnboardingError("account-changed", "Nextcloud-Konto hat sich geaendert")
    except OnboardingError as exc:
        exc.diagnostics = user_failure_diagnostics(exc.code)
        raise
    credentials = replace(credentials, user_id=uid)
    snapshot: dict[str, Any] = {
        "schema_version": 1,
        "server_url": transport.base_url,
        "user_id": uid,
        "account_id": hashlib.sha256((transport.base_url + "\0" + uid).encode()).hexdigest(),
        "display_name": str(user.get("display-name") or user.get("displayname") or uid),
        "expected_ips": transport.expected_ips,
        "calendars": [],
        "task_lists": [],
        "caldav_collections": [],
        "addressbooks": [],
        "file_roots": [],
        "folders": [],
        "talk_rooms": [],
        "capabilities": {},
        "calendar_homes": [],
        "addressbook_homes": [],
        "complete": True,
        "errors": [],
        "limitations": [],
        "diagnostics": {"schema_version": 1, "availability": {"status": "ok", "code": ""},
                        "user": {"status": "ok", "loaded": True, "code": ""}},
    }

    def failed(domain: str, exc: OnboardingError) -> None:
        snapshot["errors"].append({"domain": domain, "code": exc.code})

    talk = _talk_diagnostic("failed")
    snapshot["diagnostics"]["talk"] = talk
    try:
        raw = _ocs(transport, credentials, "ocs/v1.php/cloud/capabilities")
        capabilities = raw.get("capabilities") if isinstance(raw, dict) else None
        if not isinstance(capabilities, dict):
            raise OnboardingError("invalid-capabilities")
        if "spreed" not in capabilities:
            talk.update(status="unavailable", app_available=False, code="talk-app-unavailable")
        else:
            spreed = capabilities["spreed"]
            if not isinstance(spreed, dict):
                raise OnboardingError("invalid-capabilities")
            features = spreed.get("features", [])
            if not isinstance(features, list) or not all(isinstance(value, str) for value in features):
                raise OnboardingError("invalid-capabilities")
            snapshot["capabilities"]["spreed"] = {"features": sorted(set(features))}
            talk.update(app_available=True, reference_id_supported="chat-reference-id" in features)
    except OnboardingError as exc:
        failed("capabilities", exc)
        talk.update(code=exc.code, errors=[{"domain": "capabilities", "code": exc.code}])

    principal_item = None
    try:
        root = "remote.php/dav/"
        root_item = _one_item(
            _propfind(transport, credentials, root, [_q(DAV, "current-user-principal")], depth=0),
            transport,
            root,
        )
        principal_hrefs = _home_hrefs(transport, root_item, DAV, "current-user-principal")
        if len(principal_hrefs) != 1:
            raise OnboardingError("ambiguous-principal")
        principal = principal_hrefs[0]
        principal_item = _one_item(
            _propfind(
                transport,
                credentials,
                principal,
                [
                    _q(CALDAV, "calendar-home-set"),
                    _q(CARDDAV, "addressbook-home-set"),
                ],
                depth=0,
            ),
            transport,
            principal,
        )
        snapshot["principal_href"] = principal_item.href
    except OnboardingError as exc:
        failed("dav", exc)

    common = [_q(DAV, "displayname"), _q(DAV, "resourcetype"), _q(DAV, "current-user-privilege-set")]
    if principal_item is not None:
        for domain, namespace, home_name, kind in (
            ("caldav", CALDAV, "calendar-home-set", "calendar"),
            ("contacts", CARDDAV, "addressbook-home-set", "addressbook"),
        ):
            try:
                homes = _home_hrefs(transport, principal_item, namespace, home_name)
            except OnboardingError as exc:
                failed(domain, exc)
                continue
            if len(homes) > min(max_resources, 32):
                homes = homes[: min(max_resources, 32)]
                snapshot["limitations"].append(domain + ":home-limit")
            snapshot["calendar_homes" if kind == "calendar" else "addressbook_homes"] = homes
            seen = set()
            for home in homes:
                try:
                    properties = common + (
                        [_q(CALDAV, "supported-calendar-component-set")] if kind == "calendar" else []
                    )
                    items = _propfind(transport, credentials, home, properties, depth=1)
                    home_item = _one_item(items, transport, home)
                    _check_item_status(home_item)
                    home_type = home_item.properties.get(_q(DAV, "resourcetype"))
                    if home_type is None or home_type.find(_q(DAV, "collection")) is None:
                        raise OnboardingError("properties-unavailable")
                    for item in items:
                        home_path = urllib.parse.urlsplit(home).path.rstrip("/")
                        if item.href.rstrip("/") != home_path and not item.href.startswith(home_path + "/"):
                            raise OnboardingError(
                                "foreign-path", "DAV-Ressource liegt ausserhalb ihres Homes"
                            )
                        if item.href in seen:
                            continue
                        seen.add(item.href)
                        try:
                            _check_item_status(item)
                        except OnboardingError as exc:
                            failed(domain, exc)
                            continue
                        resource_type = item.properties.get(_q(DAV, "resourcetype"))
                        if resource_type is None:
                            failed(domain, OnboardingError("properties-unavailable"))
                            continue
                        if resource_type.find(_q(namespace, kind)) is None:
                            continue
                        resource = _resource(item, kind)
                        if kind == "calendar":
                            components = item.properties.get(_q(CALDAV, "supported-calendar-component-set"))
                            resource["components"] = (
                                sorted(
                                    {
                                        str(component.get("name", "")).upper()
                                        for component in components
                                        if component.tag == _q(CALDAV, "comp") and component.get("name")
                                    }
                                )
                                if components is not None
                                else []
                            )
                            snapshot["caldav_collections"].append(resource)
                            if "VEVENT" in resource["components"]:
                                snapshot["calendars"].append(resource)
                            if "VTODO" in resource["components"]:
                                snapshot["task_lists"].append(resource)
                            if not resource["components"]:
                                failed(domain, OnboardingError("components-unavailable"))
                        else:
                            snapshot["addressbooks"].append(resource)
                except OnboardingError as exc:
                    failed(domain, exc)
    try:
        files = discover_folders(transport, credentials, max_resources=max_resources)
        snapshot["file_roots"] = [item for item in files["folders"] if item["kind"] == "file-root"]
        snapshot["folders"] = [item for item in files["folders"] if item["kind"] != "file-root"]
        snapshot["files_depth"] = 1
        snapshot["errors"].extend(files["errors"])
        snapshot["limitations"].extend(files["limitations"])
        root_readable = any(item["read_check"]["status"] == "ok" for item in snapshot["file_roots"])
        snapshot["diagnostics"]["files"] = {
            "status": ("ok" if root_readable and files["complete"] else
                       "partial" if root_readable else "failed"),
            "inventory_complete": files["inventory_complete"], "scope": "first-level", "depth": 1,
            "root_readable": root_readable, "discovered": files["discovered"],
            "content_checked": False, "recursive": False,
            "errors": files["errors"], "limitations": files["limitations"],
        }
    except OnboardingError as exc:
        failed("files", exc)
        snapshot["diagnostics"]["files"] = {
            "status": "failed", "inventory_complete": False, "scope": "first-level", "depth": 1,
            "root_readable": False, "discovered": 0, "content_checked": False, "recursive": False,
            "errors": [{"code": exc.code}], "limitations": [],
        }
    if "spreed" in snapshot["capabilities"]:
        talk["api_reachable"] = False
        try:
            rooms = _ocs(transport, credentials, "ocs/v2.php/apps/spreed/api/v4/room?noStatusUpdate=1")
            talk["api_reachable"] = True
            if not isinstance(rooms, list):
                raise OnboardingError("invalid-talk-rooms")
            tokens = set()
            for room in rooms:
                token = room.get("token") if isinstance(room, dict) else None
                if (
                    not isinstance(token, str)
                    or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", token)
                    or token in tokens
                ):
                    raise OnboardingError("invalid-talk-rooms")
                tokens.add(token)
                permissions = room.get("permissions")
                can_send = type(permissions) is int and bool(permissions & 128) and room.get("readOnly") == 0
                snapshot["talk_rooms"].append(
                    {
                        "resource_id": "nextcloud-talk-" + token,
                        "token": token,
                        "name": str(room.get("displayName") or room.get("name") or token),
                        "kind": "talk-room",
                        "can_read": True,
                        "can_send": can_send,
                        "permissions": permissions if type(permissions) is int else None,
                        "participant_type": room.get("participantType"),
                        "read_only": room.get("readOnly"),
                        "type": room.get("type"),
                    }
                )
            talk["inventory_complete"] = True
        except OnboardingError as exc:
            failed("talk", exc)
            talk.update(code=exc.code, errors=[{"domain": "talk", "code": exc.code}])
        # Count all valid observed rooms before the bounded snapshot is sliced.
        talk["discovered"] = len(snapshot["talk_rooms"])
        talk["writable"] = sum(room["can_send"] for room in snapshot["talk_rooms"])
        talk["send_unconfirmed"] = sum(
            not room["can_send"]
            and not (type(room["permissions"]) is int and not room["permissions"] & 128)
            and room["read_only"] != 1
            for room in snapshot["talk_rooms"]
        )
        if talk["errors"]:
            talk["status"] = "partial" if talk["discovered"] else "failed"
        elif not talk["reference_id_supported"]:
            talk.update(status="unsupported", code="talk-reference-id-required")
        else:
            talk["status"] = "ok" if talk["discovered"] else "empty"
    collection_domains = {"calendars": "caldav", "task_lists": "caldav", "addressbooks": "contacts"}
    discovered_counts = {key: len(snapshot[key]) for key in collection_domains}
    for key in ("caldav_collections", "calendars", "task_lists", "addressbooks", "folders", "talk_rooms"):
        snapshot[key].sort(key=lambda item: (item["name"].casefold(), item["resource_id"]))
        if len(snapshot[key]) > max_resources:
            snapshot[key] = snapshot[key][:max_resources]
            snapshot["limitations"].append(key + ":resource-limit")
            if key == "talk_rooms":
                talk.update(status="partial", inventory_complete=False)
                talk["limitations"].append("talk_rooms:resource-limit")
                if not talk["errors"]:
                    talk["code"] = "talk_rooms:resource-limit"
    inventory_limitations = list(snapshot["limitations"])
    inventory_errors = list(snapshot["errors"])
    _check_collections(
        transport, credentials, snapshot, previous_snapshot, collection_domains,
        datetime.fromisoformat(checked_at),
    )
    for key, domain in collection_domains.items():
        domain_errors = [dict(error) for error in inventory_errors if error["domain"] in {"dav", domain}]
        domain_limits = [
            value for value in inventory_limitations if value.startswith((domain + ":", key + ":"))
        ]
        inventory_complete = not domain_errors and not domain_limits
        for resource in snapshot[key]:
            check = resource["read_check"]
            if check["status"] == "failed":
                error = {"domain": key, "resource_id": resource["resource_id"], "code": check["code"]}
                snapshot["errors"].append(error)
                domain_errors.append(error)
            elif check["status"] == "skipped":
                domain_limits.append(check["code"])
        snapshot["diagnostics"][key] = _collection_diagnostic(
            snapshot[key], inventory_complete=inventory_complete, discovered=discovered_counts[key],
            errors=domain_errors, limitations=domain_limits,
        )
    snapshot["limitations"] = sorted(set(snapshot["limitations"]))
    snapshot["complete"] = not snapshot["errors"] and not snapshot["limitations"]
    snapshot["results_may_be_truncated"] = bool(inventory_limitations)
    # The window starts with the oldest check in this measurement.
    snapshot["checked_at"] = checked_at
    return snapshot
