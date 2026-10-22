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
from dataclasses import dataclass, field, replace
from typing import Any
from xml.etree import ElementTree as ET

DAV = "DAV:"
CALDAV = "urn:ietf:params:xml:ns:caldav"
CARDDAV = "urn:ietf:params:xml:ns:carddav"
OC = "http://owncloud.org/ns"
MAX_RESPONSE_BYTES = 4 * 1024 * 1024
LOGIN_LIFETIME_SECONDS = 20 * 60
_PRIVATE_NETWORKS = tuple(
    ipaddress.ip_network(value) for value in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "fc00::/7")
)


class OnboardingError(RuntimeError):
    """Safe code and fixed message: never include an upstream body or token."""

    def __init__(self, code: str, message: str = "Nextcloud-Anbindung konnte nicht abgeschlossen werden"):
        super().__init__(message)
        self.code = code


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
        for redirect in range(4):
            if self._resolve() != list(self._ips):
                raise OnboardingError("dns-changed", "Nextcloud-Zieladressen haben sich geaendert")
            parsed = urllib.parse.urlsplit(url)
            target = parsed.path + ("?" + parsed.query if parsed.query else "")
            connection = None
            try:
                connection = self._factory(self._host, self._port, self._ips[0], self._timeout, self._context)
                connection.request(method, target, body=data, headers=request_headers)
                incoming = connection.getresponse()
                response_headers = {key.lower(): value for key, value in incoming.getheaders()}
                length = response_headers.get("content-length")
                if length is not None and (not length.isdecimal() or int(length) > self._maximum):
                    raise OnboardingError("response-limit", "Nextcloud-Antwort ist zu gross oder ungueltig")
                body = incoming.read(self._maximum + 1)
                if len(body) > self._maximum:
                    raise OnboardingError("response-limit", "Nextcloud-Antwort ist zu gross")
                if response_headers.get("content-encoding", "identity").lower() != "identity":
                    raise OnboardingError("unsupported-encoding", "Unerwartete Antwortkompression")
                response = HttpResponse(int(incoming.status), response_headers, body)
            except ssl.SSLError:
                raise OnboardingError("tls-error", "Nextcloud-TLS-Pruefung fehlgeschlagen") from None
            except (OSError, http.client.HTTPException):
                raise OnboardingError("transport-error", "Nextcloud ist momentan nicht erreichbar") from None
            finally:
                if connection is not None:
                    connection.close()
            if response.status not in {301, 302, 303, 307, 308}:
                return response
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
) -> HttpResponse:
    if normalize_server_url(credentials.server_url) != transport.base_url:
        raise OnboardingError("account-mismatch", "Zugangsdaten gehoeren zu einer anderen Nextcloud")
    return transport.request(
        method, path, headers={**credential_headers(credentials), **dict(headers or {})}, data=data
    )


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
        raw_href = child.findtext(_q(DAV, "href")) or ""
        absolute = transport.resolve_url(raw_href)
        parsed = urllib.parse.urlsplit(absolute)
        if parsed.query:
            raise OnboardingError("invalid-dav", "DAV-Ressource enthaelt einen unerwarteten Query")
        values: dict[str, ET.Element] = {}
        for propstat in child.findall(_q(DAV, "propstat")):
            if " 200 " not in (propstat.findtext(_q(DAV, "status")) or ""):
                continue
            property_node = propstat.find(_q(DAV, "prop"))
            if property_node is not None:
                values.update({item.tag: item for item in property_node})
        items.append(_DavItem(parsed.path, values, child.findtext(_q(DAV, "status")) or ""))
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
        if resource_type is None:
            errors.append({"domain": "files", "code": "properties-unavailable"})
            continue
        if resource_type.find(_q(DAV, "collection")) is None:
            continue
        folder = _resource(item, "file-root" if item is own and not clean else "folder")
        permission_element = item.properties.get(_q(OC, "permissions"))
        if permission_element is not None:
            flags = _text(item, OC, "permissions")
            folder.update(
                {
                    "can_read": "G" in flags,
                    "can_create": "C" in flags or "K" in flags,
                    "can_create_file": "C" in flags,
                    "can_mkdir": "K" in flags,
                    "can_update": "W" in flags,
                    "privileges_verified": True,
                    "file_permissions": flags,
                    "permissions": [
                        permission
                        for permission, allowed in (
                            ("read", "G" in flags),
                            ("create", bool(set(flags) & {"C", "K"})),
                            ("update", "W" in flags),
                        )
                        if allowed
                    ],
                }
            )
        folder["file_id"] = _text(item, OC, "fileid")
        folder["path"] = urllib.parse.unquote(item.href[len(root_path) :].strip("/"))
        folders.append(folder)
    if not any(folder["href"] == own.href for folder in folders):
        raise OnboardingError("not-a-folder", "Ausgewaehlte DAV-Ressource ist kein Ordner")
    folders.sort(key=lambda item: (item["href"] != own.href, item["name"].casefold(), item["href"]))
    truncated = len(folders) > max_resources
    return {
        "path": clean,
        "folders": folders[:max_resources],
        "depth": 1,
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
) -> dict[str, Any]:
    """Discover all account collections and a bounded first folder level.

    No resource is implicitly selected or enabled. Missing advertised privileges
    never become writable/readable rights merely because PROPFIND succeeded.
    """
    _resource_limit(max_resources)
    user = _ocs(transport, credentials, "ocs/v1.php/cloud/user")
    uid = user.get("id") if isinstance(user, dict) else None
    if not isinstance(uid, str) or not uid or len(uid) > 512 or re.search(r"[\x00-\x1f/\\]", uid):
        raise OnboardingError("invalid-user-id", "Nextcloud meldet keine eindeutige Benutzer-ID")
    if credentials.user_id and credentials.user_id != uid:
        raise OnboardingError("account-changed", "Nextcloud-Konto hat sich geaendert")
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
    }

    def failed(domain: str, exc: OnboardingError) -> None:
        snapshot["errors"].append({"domain": domain, "code": exc.code})

    try:
        raw = _ocs(transport, credentials, "ocs/v1.php/cloud/capabilities")
        capabilities = raw.get("capabilities") if isinstance(raw, dict) else None
        if not isinstance(capabilities, dict):
            raise OnboardingError("invalid-capabilities")
        spreed = capabilities.get("spreed")
        if isinstance(spreed, dict):
            features = spreed.get("features", [])
            if not isinstance(features, list) or not all(isinstance(value, str) for value in features):
                raise OnboardingError("invalid-capabilities")
            snapshot["capabilities"]["spreed"] = {"features": sorted(set(features))}
    except OnboardingError as exc:
        failed("capabilities", exc)

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
                    for item in items:
                        home_path = urllib.parse.urlsplit(home).path.rstrip("/")
                        if item.href.rstrip("/") != home_path and not item.href.startswith(home_path + "/"):
                            raise OnboardingError(
                                "foreign-path", "DAV-Ressource liegt ausserhalb ihres Homes"
                            )
                        if item.href in seen:
                            continue
                        seen.add(item.href)
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
    except OnboardingError as exc:
        failed("files", exc)
    if "spreed" in snapshot["capabilities"]:
        try:
            rooms = _ocs(transport, credentials, "ocs/v2.php/apps/spreed/api/v4/room?noStatusUpdate=1")
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
        except OnboardingError as exc:
            failed("talk", exc)
    for key in ("caldav_collections", "calendars", "task_lists", "addressbooks", "folders", "talk_rooms"):
        snapshot[key].sort(key=lambda item: (item["name"].casefold(), item["resource_id"]))
        if len(snapshot[key]) > max_resources:
            snapshot[key] = snapshot[key][:max_resources]
            snapshot["limitations"].append(key + ":resource-limit")
    snapshot["limitations"] = sorted(set(snapshot["limitations"]))
    snapshot["complete"] = not snapshot["errors"] and not snapshot["limitations"]
    snapshot["results_may_be_truncated"] = bool(snapshot["limitations"])
    return snapshot
