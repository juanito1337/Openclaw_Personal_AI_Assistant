from __future__ import annotations

import base64
import hashlib
import http.client
import json
import socket
import ssl
import threading
import time
from dataclasses import replace
from datetime import datetime
from typing import Any
from xml.etree import ElementTree as ET

import pytest

from personal_assistant import nextcloud_onboarding
from personal_assistant.nextcloud_onboarding import (
    CALDAV,
    CARDDAV,
    DAV,
    OC,
    HttpResponse,
    LoginFlow,
    NextcloudCredentials,
    OnboardingError,
    SafeNextcloudTransport,
    _PinnedHTTPSConnection,
    credential_headers,
    discover_account,
    discover_folders,
    normalize_server_url,
)

BASE = "https://cloud.example/nc"
LOGIN = "owner@example.test"
PASSWORD = "fixture-only-app-secret"
POLL_TOKEN = "fixture-only-poll-token"
UID = "actual-user"
ROOT = "/nc/remote.php/dav/"
PRINCIPAL = ROOT + "principals/users/actual-user/"
CALENDARS = "/nc/custom/calendars/"
BOOKS = "/nc/custom/books/"
FILES = ROOT + "files/actual-user/"


class FakeIncoming:
    def __init__(self, response: HttpResponse):
        self.status = response.status
        self.response = response

    def getheaders(self):
        return list(self.response.headers.items())

    def read(self, maximum: int):
        return self.response.body[:maximum]


class FakeConnection:
    def __init__(self, network: FakeNetwork):
        self.network = network
        self.key = ("", "")

    def request(self, method, path, *, body, headers):
        self.key = (method, path)
        self.network.calls.append((method, path, body, headers))

    def getresponse(self):
        assert self.key in self.network.responses, self.key
        values = self.network.responses[self.key]
        assert values, self.key
        value = values.pop(0)
        if isinstance(value, Exception):
            raise value
        return FakeIncoming(value)

    def close(self):
        self.network.closed += 1


class FakeNetwork:
    def __init__(self):
        self.responses: dict[tuple[str, str], list[Any]] = {}
        self.calls: list[Any] = []
        self.connections: list[Any] = []
        self.closed = 0

    def add(self, method: str, path: str, payload: Any = None, *, status: int = 200, headers=None):
        body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        self.responses.setdefault((method, path), []).append(HttpResponse(status, headers or {}, body))

    def factory(self, host, port, address, timeout, context):
        self.connections.append((host, port, address, timeout, context))
        return FakeConnection(self)

    def transport(self, **kwargs):
        return SafeNextcloudTransport(
            BASE,
            resolver=kwargs.pop("resolver", lambda host, port: ["192.168.1.20"]),
            connection_factory=self.factory,
            **kwargs,
        )


def credentials(**kwargs):
    return NextcloudCredentials(BASE, LOGIN, PASSWORD, **kwargs)


def error_code(code: str):
    return pytest.raises(OnboardingError, match=".*", check=lambda exc: exc.code == code)


@pytest.mark.parametrize(
    "url",
    [
        "http://cloud.example",
        "https://user:password@cloud.example",
        "https://cloud.example/?token=x",
        "https://cloud.example/#x",
        "https://cloud.example/../admin",
        "https://cloud.example/%2e%2e/admin",
        "https://cloud.example/%252e%252e/admin",
        "https://cloud.example/%2fadmin",
        "https://cloud.example/\\admin",
        "https://cloud.example/\nadmin",
        "https://cloud.example:0",
        "https://cloud.example:99999",
        "https://cloud.example/%wrong",
        "https://[fe80::1%25eth0]/",
    ],
)
def test_rejects_ambiguous_or_unsafe_base_urls(url):
    with pytest.raises(OnboardingError):
        normalize_server_url(url)


def test_subpaths_dav_hrefs_and_query_preserved_without_duplication():
    transport = FakeNetwork().transport()
    assert normalize_server_url("https://CLOUD.example:443/nc/") == BASE
    assert (
        transport.resolve_url("ocs/v2.php/room?noStatusUpdate=1")
        == BASE + "/ocs/v2.php/room?noStatusUpdate=1"
    )
    assert transport.resolve_url(FILES) == "https://cloud.example" + FILES
    assert transport.resolve_url(BASE + "/remote.php/dav/") == BASE + "/remote.php/dav/"
    with error_code("foreign-path"):
        transport.resolve_url("/ocs/v2.php/room")
    with error_code("foreign-origin"):
        transport.resolve_url("https://other.example/nc/endpoint")
    with pytest.raises(OnboardingError):
        transport.resolve_url("//other.example/nc/endpoint")


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",
        "::1",
        "0.0.0.0",
        "::",
        "169.254.169.254",
        "fe80::1",
        "224.0.0.1",
        "ff02::1",
        "100.64.0.1",
        "192.0.2.1",
        "::ffff:127.0.0.1",
        "fd00:ec2::254",
        "168.63.129.16",
        "fc00::1%eth0",
    ],
)
def test_private_opt_in_does_not_allow_loopback_metadata_or_reserved(address):
    with error_code("unsafe-address"):
        FakeNetwork().transport(resolver=lambda host, port: [address])


def test_private_server_binding_requires_current_address_set_and_no_mixed_bad_answers():
    network = FakeNetwork()
    transport = network.transport(expected_ips=["192.168.1.20"])
    assert transport.expected_ips == ["192.168.1.20"]
    transport.expected_ips.append("127.0.0.1")
    assert transport.expected_ips == ["192.168.1.20"]
    with error_code("unsafe-address"):
        network.transport(allow_private=False)
    with error_code("dns-changed"):
        network.transport(expected_ips=["192.168.1.21"])
    with error_code("unsafe-address"):
        network.transport(resolver=lambda host, port: ["93.184.216.34", "127.0.0.1"])


def test_dns_rebinding_is_checked_before_each_request_and_connection_uses_bound_ip():
    addresses = ["192.168.1.20"]
    network = FakeNetwork()
    network.add("GET", "/nc/status.php", {})
    transport = network.transport(resolver=lambda host, port: list(addresses))
    transport.request("GET", "status.php")
    host, port, address, _, context = network.connections[0]
    assert (host, port, address) == ("cloud.example", 443, "192.168.1.20")
    assert context.check_hostname and context.verify_mode == ssl.CERT_REQUIRED
    addresses[:] = ["192.168.1.21"]
    with error_code("dns-changed"):
        transport.request("GET", "status.php")
    assert len(network.connections) == 1


def test_pinned_socket_keeps_original_hostname_for_tls(monkeypatch):
    calls = []
    raw = object()
    context = ssl.create_default_context()
    connection = _PinnedHTTPSConnection("cloud.example", 443, "192.168.1.20", 10, context)
    monkeypatch.setattr(
        "socket.create_connection", lambda address, timeout: calls.append((address, timeout)) or raw
    )

    class FakeContext:
        @staticmethod
        def wrap_socket(sock, *, server_hostname):
            calls.append((sock, server_hostname))
            return "tls-socket"

    connection._tls_context = FakeContext()
    connection.connect()
    assert calls == [(("192.168.1.20", 443), 10), (raw, "cloud.example")]
    assert connection.sock == "tls-socket"


def test_redirect_revalidation_prevents_credential_forwarding_to_other_origin():
    network = FakeNetwork()
    network.add("GET", "/nc/first", b"", status=302, headers={"location": "https://evil.example/steal"})
    with error_code("foreign-origin"):
        network.transport().request("GET", "first", headers=credential_headers(credentials()))
    assert len(network.calls) == 1
    assert network.closed == 1


def test_same_origin_redirects_keep_subpath_and_preserve_post_only_for_307_308():
    network = FakeNetwork()
    network.add("POST", "/nc/start", b"", status=307, headers={"location": "next"})
    network.add("POST", "/nc/next", {"ok": True})
    assert network.transport().request("POST", "start", data=b"hello").json() == {"ok": True}
    assert network.calls[1][2] == b"hello"
    network.add("POST", "/nc/start", b"", status=303, headers={"location": "next"})
    with error_code("redirect-method-change"):
        network.transport().request("POST", "start", data=b"hello")


@pytest.mark.parametrize(
    ("body", "headers", "code"),
    [
        (b"x" * 1025, {}, "response-limit"),
        (b"", {"content-length": "1025"}, "response-limit"),
        (b"", {"content-length": "-1"}, "response-limit"),
        (b"gzip", {"content-encoding": "gzip"}, "unsupported-encoding"),
    ],
)
def test_response_limits_apply_before_json_parsing(body, headers, code):
    network = FakeNetwork()
    network.add("GET", "/nc/test", body, headers=headers)
    with error_code(code):
        network.transport(max_response_bytes=1024).request("GET", "test")
    assert network.closed == 1


def test_tls_and_transport_errors_never_echo_upstream_secret_text():
    network = FakeNetwork()
    network.responses[("GET", "/nc/test")] = [ssl.SSLError(PASSWORD), OSError(POLL_TOKEN)]
    transport = network.transport()
    for code in ("tls-error", "transport-error"):
        with pytest.raises(OnboardingError) as raised:
            transport.request("GET", "test")
        assert raised.value.code == code
        assert PASSWORD not in str(raised.value) and POLL_TOKEN not in str(raised.value)


def setup_login(network, *, clock=lambda: 0):
    network.add(
        "POST",
        "/nc/index.php/login/v2",
        {
            "poll": {"token": POLL_TOKEN, "endpoint": BASE + "/login/v2/poll"},
            "login": BASE + "/login/v2/flow/browser-session",
        },
    )
    return LoginFlow(network.transport(), clock=clock)


def test_login_pending_connected_public_views_and_single_use_credentials():
    network = FakeNetwork()
    now = [0.0]
    flow = setup_login(network, clock=lambda: now[0])
    network.add("POST", "/nc/login/v2/poll", b"", status=404)
    network.add("POST", "/nc/login/v2/poll", {"server": BASE, "loginName": LOGIN, "appPassword": PASSWORD})
    start = flow.start()
    assert start == {
        "state": "pending",
        "expires_in": 1200,
        "login_url": BASE + "/login/v2/flow/browser-session",
    }
    assert flow.poll()["state"] == "pending"
    assert flow.poll()["state"] == "pending"  # Polling is throttled, not sent twice.
    now[0] = 1
    completed = flow.poll()
    assert completed == {"state": "connected"}
    assert flow.poll() == completed
    assert PASSWORD not in json.dumps([start, completed]) and POLL_TOKEN not in json.dumps([start, completed])
    result = flow.take_credentials()
    assert result.login_name == LOGIN and result.app_password == PASSWORD
    assert PASSWORD not in repr(result)
    with error_code("credentials-unavailable"):
        flow.take_credentials()
    assert len(network.calls) == 3
    assert network.calls[1][2] == ("token=" + POLL_TOKEN).encode()


def test_login_expiry_and_cancel_stop_polling_and_clear_credentials():
    now = [0.0]
    network = FakeNetwork()
    flow = setup_login(network, clock=lambda: now[0])
    flow.start()
    now[0] = 1200
    assert flow.poll() == {"state": "expired"}
    assert len(network.calls) == 1
    assert flow.cancel() == {"state": "cancelled"}
    assert flow.poll() == {"state": "cancelled"}
    with error_code("credentials-unavailable"):
        flow.take_credentials()


@pytest.mark.parametrize(
    "payload",
    [
        {"server": "https://other.example/nc", "loginName": LOGIN, "appPassword": PASSWORD},
        {"server": BASE, "loginName": LOGIN},
        {"server": BASE, "loginName": "bad:login", "appPassword": PASSWORD},
        {"server": BASE, "loginName": LOGIN, "appPassword": ""},
    ],
)
def test_consumed_poll_with_invalid_credentials_is_terminal(payload):
    network = FakeNetwork()
    flow = setup_login(network)
    flow.start()
    network.add("POST", "/nc/login/v2/poll", payload)
    with pytest.raises(OnboardingError):
        flow.poll()
    assert flow.poll() == {"state": "failed"}
    with error_code("credentials-unavailable"):
        flow.take_credentials()
    assert PASSWORD not in repr(flow)


def q(namespace, name):
    return "{" + namespace + "}" + name


def multistatus(resources):
    root = ET.Element(q(DAV, "multistatus"))
    for href, props in resources:
        response = ET.SubElement(root, q(DAV, "response"))
        ET.SubElement(response, q(DAV, "href")).text = href
        propstat = ET.SubElement(response, q(DAV, "propstat"))
        ET.SubElement(propstat, q(DAV, "status")).text = "HTTP/1.1 200 OK"
        prop = ET.SubElement(propstat, q(DAV, "prop"))
        for name, value in props.items():
            node = ET.SubElement(prop, name)
            if isinstance(value, str):
                node.text = value
            else:
                node.extend(value)
    return ET.tostring(root)


def href_property(href):
    node = ET.Element(q(DAV, "href"))
    node.text = href
    return [node]


def collection(name, *, components=None, rights=None, file_flags=None, file_id=""):
    kind = (CALDAV, "calendar") if components is not None else (CARDDAV, "addressbook")
    props = {q(DAV, "displayname"): name, q(DAV, "resourcetype"): [ET.Element(q(DAV, "collection"))]}
    if file_flags is None:
        props[q(DAV, "resourcetype")].append(ET.Element(q(*kind)))
    if components is not None:
        props[q(CALDAV, "supported-calendar-component-set")] = [
            ET.Element(q(CALDAV, "comp"), {"name": item}) for item in components
        ]
    if rights is not None:
        privileges = []
        for right in rights:
            privilege = ET.Element(q(DAV, "privilege"))
            ET.SubElement(privilege, q(DAV, right))
            privileges.append(privilege)
        props[q(DAV, "current-user-privilege-set")] = privileges
    if file_flags is not None:
        props[q(OC, "permissions")] = file_flags
        props[q(OC, "fileid")] = file_id
    return props


def ocs(data, *, statuscode=100, status="ok"):
    return {"ocs": {"meta": {"status": status, "statuscode": statuscode}, "data": data}}


def discovery_network(*, unknown_rights=None):
    network = FakeNetwork()
    network.add("GET", "/nc/ocs/v1.php/cloud/user", ocs({"id": UID, "display-name": "Account owner"}))
    network.add(
        "GET",
        "/nc/ocs/v1.php/cloud/capabilities",
        ocs(
            {
                "capabilities": {
                    "spreed": {"features": ["chat-reference-id", "chat-keep-notifications"]},
                    "theming": {"logo": "https://untrusted.example/private"},
                }
            }
        ),
    )
    network.add(
        "PROPFIND",
        ROOT,
        multistatus([(ROOT, {q(DAV, "current-user-principal"): href_property(PRINCIPAL)})]),
        status=207,
    )
    network.add(
        "PROPFIND",
        PRINCIPAL,
        multistatus(
            [
                (
                    PRINCIPAL,
                    {
                        q(CALDAV, "calendar-home-set"): href_property(CALENDARS),
                        q(CARDDAV, "addressbook-home-set"): href_property(BOOKS),
                    },
                )
            ]
        ),
        status=207,
    )
    network.add(
        "PROPFIND",
        CALENDARS,
        multistatus(
            [
                (CALENDARS, {q(DAV, "resourcetype"): [ET.Element(q(DAV, "collection"))]}),
                (CALENDARS + "events/", collection("Same name", components=["VEVENT"], rights=["read"])),
                (
                    CALENDARS + "tasks/",
                    collection("Tasks", components=["VTODO"], rights=["read", "bind", "write-content"]),
                ),
                (
                    CALENDARS + "both/",
                    collection("Same name", components=["VEVENT", "VTODO"], rights=["all"]),
                ),
                (CALENDARS + "unknown-rights/", collection("Unknown rights", components=["VEVENT"],
                                                          rights=unknown_rights)),
            ]
        ),
        status=207,
    )
    for name, components, rights in (
        ("events", ["VEVENT"], ["read"]),
        ("tasks", ["VTODO"], ["read", "bind", "write-content"]),
        ("both", ["VEVENT", "VTODO"], ["all"]),
        ("unknown-rights", ["VEVENT"], unknown_rights),
    ):
        href = CALENDARS + name + "/"
        network.add("PROPFIND", href,
                    multistatus([(href, collection(name, components=components, rights=rights))]), status=207)
    network.add(
        "PROPFIND",
        BOOKS,
        multistatus([
            (BOOKS, {q(DAV, "resourcetype"): [ET.Element(q(DAV, "collection"))]}),
            (BOOKS + "contacts/", collection("Contacts", rights=["read"])),
        ]),
        status=207,
    )
    network.add(
        "PROPFIND",
        FILES,
        multistatus(
            [
                (FILES, collection("Files", file_flags="RGCK", file_id="1")),
                (FILES + "Shared/", collection("Shared", file_flags="RG", file_id="22")),
            ]
        ),
        status=207,
    )
    network.add(
        "GET",
        "/nc/ocs/v2.php/apps/spreed/api/v4/room?noStatusUpdate=1",
        ocs(
            [
                {
                    "token": "roomA",
                    "displayName": "Room",
                    "permissions": 255,
                    "participantType": 3,
                    "readOnly": 0,
                    "type": 2,
                },
                {
                    "token": "roomB",
                    "displayName": "Room",
                    "permissions": 255,
                    "participantType": 3,
                    "readOnly": 1,
                    "type": 2,
                },
            ],
            statuscode=200,
        ),
    )
    return network


def test_discovery_uses_authenticated_uid_and_server_principal_homes_without_selecting_first():
    network = discovery_network()
    snapshot = discover_account(network.transport(), credentials())
    assert not snapshot["complete"]
    unknown_id = hashlib.sha256((CALENDARS + "unknown-rights/").encode()).hexdigest()[:12]
    assert snapshot["errors"] == [{"domain": "calendars", "code": "privileges-unavailable",
                                    "resource_id": "nextcloud-calendar-" + unknown_id}]
    assert snapshot["user_id"] == UID
    assert snapshot["calendar_homes"] == ["https://cloud.example" + CALENDARS]
    assert snapshot["addressbook_homes"] == ["https://cloud.example" + BOOKS]
    assert len(snapshot["calendars"]) == 3 and len(snapshot["task_lists"]) == 2
    assert len({item["resource_id"] for item in snapshot["caldav_collections"]}) == 4
    events = next(item for item in snapshot["calendars"] if item["href"].endswith("events/"))
    unknown = next(item for item in snapshot["calendars"] if item["href"].endswith("unknown-rights/"))
    assert events["can_read"] and not events["can_update"] and not events["can_create"]
    assert unknown["permissions"] == [] and not unknown["can_read"]
    assert (
        events["resource_id"]
        == "nextcloud-calendar-" + hashlib.sha256(events["href"].encode()).hexdigest()[:12]
    )
    assert snapshot["file_roots"][0]["can_read"] and snapshot["file_roots"][0]["can_mkdir"]
    assert snapshot["folders"][0]["file_id"] == "22" and not snapshot["folders"][0]["can_create"]
    assert [room["can_send"] for room in snapshot["talk_rooms"]] == [True, False]
    assert snapshot["capabilities"]["spreed"]["features"] == ["chat-keep-notifications", "chat-reference-id"]
    serialized = json.dumps(snapshot)
    assert PASSWORD not in serialized and "untrusted.example" not in serialized
    for _, path, _, headers in network.calls:
        assert "owner%40" not in path
        assert (
            headers["Authorization"] == "Basic " + base64.b64encode(f"{LOGIN}:{PASSWORD}".encode()).decode()
        )


def test_partial_discovery_failure_keeps_other_resources_and_safe_error():
    network = discovery_network()
    network.responses[("PROPFIND", BOOKS)] = [
        HttpResponse(403, {}, (PASSWORD + " upstream private body").encode())
    ]
    snapshot = discover_account(network.transport(), credentials())
    assert not snapshot["complete"]
    assert {"domain": "contacts", "code": "permission-denied"} in snapshot["errors"]
    assert len(snapshot["calendars"]) == 3 and len(snapshot["talk_rooms"]) == 2
    assert PASSWORD not in json.dumps(snapshot)


def test_unknown_server_properties_never_grant_file_read_and_limits_are_explicit():
    network = discovery_network()
    props = {q(DAV, "resourcetype"): [ET.Element(q(DAV, "collection"))]}
    network.responses[("PROPFIND", FILES)] = [HttpResponse(207, {}, multistatus([(FILES, props)]))]
    snapshot = discover_account(network.transport(), credentials(), max_resources=1)
    assert not snapshot["file_roots"][0]["can_read"]
    assert not snapshot["complete"] and snapshot["results_may_be_truncated"]
    assert "calendars:resource-limit" in snapshot["limitations"]


def test_existing_credential_uid_cannot_silently_switch_accounts():
    network = discovery_network()
    with error_code("account-changed"):
        discover_account(network.transport(), credentials(user_id="previous-account"))
    assert len(network.calls) == 1


def test_credentials_cannot_be_sent_to_different_server():
    network = discovery_network()
    with error_code("account-mismatch"):
        discover_account(network.transport(), replace(credentials(), server_url="https://other.example"))
    assert not network.calls


def test_foreign_dav_hrefs_are_partial_failure_and_never_requested():
    network = discovery_network()
    network.responses[("PROPFIND", CALENDARS)] = [
        HttpResponse(
            207,
            {},
            multistatus(
                [
                    (
                        "https://other.example/calendar/",
                        collection("Foreign", components=["VEVENT"], rights=["all"]),
                    ),
                ]
            ),
        )
    ]
    snapshot = discover_account(network.transport(), credentials())
    assert {"domain": "caldav", "code": "foreign-origin"} in snapshot["errors"]
    assert not snapshot["calendars"]
    assert snapshot["file_roots"]
    assert all("other.example" not in call[1] for call in network.calls)


def test_directory_discovery_rejects_sibling_prefix_escape_and_traversal():
    network = FakeNetwork()
    network.add(
        "PROPFIND",
        FILES + "Safe",
        multistatus(
            [
                (FILES + "Safe/", collection("Safe", file_flags="G")),
                (FILES + "SafeOther/", collection("Outside", file_flags="G")),
            ]
        ),
        status=207,
    )
    with error_code("foreign-path"):
        discover_folders(network.transport(), credentials(user_id=UID), "Safe")
    for path in ("../Other", "%2e%2e/Other", "foo\\bar"):
        with pytest.raises(OnboardingError):
            discover_folders(network.transport(), credentials(user_id=UID), path)


def test_ocs_http_success_with_error_meta_is_not_success():
    network = FakeNetwork()
    network.add("GET", "/nc/ocs/v1.php/cloud/user", ocs({"id": UID}, status="failure", statuscode=997))
    with error_code("ocs-error"):
        discover_account(network.transport(), credentials())


def test_xml_entity_definitions_are_not_expanded():
    network = discovery_network()
    network.responses[("PROPFIND", BOOKS)] = [
        HttpResponse(207, {}, b'<!DOCTYPE x [<!ENTITY e "bad">]><x>&e;</x>')
    ]
    snapshot = discover_account(network.transport(), credentials())
    assert {"domain": "contacts", "code": "invalid-dav"} in snapshot["errors"]


def test_readable_file_flag_is_g_not_reshare_flag_r():
    network = FakeNetwork()
    network.add(
        "PROPFIND",
        FILES,
        multistatus(
            [
                (FILES, collection("Root", file_flags="R")),
                (FILES + "100%25/", collection("100%", file_flags="G")),
            ]
        ),
        status=207,
    )
    folders = discover_folders(network.transport(), credentials(user_id=UID))["folders"]
    assert folders[0]["can_read"] is False
    assert folders[1]["can_read"] is True and folders[1]["path"] == "100%"


def test_login_start_rejects_foreign_poll_endpoint_before_opening_browser():
    network = FakeNetwork()
    network.add(
        "POST",
        "/nc/index.php/login/v2",
        {
            "poll": {"token": POLL_TOKEN, "endpoint": "https://evil.example/collect"},
            "login": BASE + "/login/flow",
        },
    )
    flow = LoginFlow(network.transport())
    with error_code("foreign-origin"):
        flow.start()
    assert flow.poll() == {"state": "new"}
    assert len(network.calls) == 1


def test_cancel_after_success_discards_uncollected_credentials():
    network = FakeNetwork()
    flow = setup_login(network)
    flow.start()
    network.add("POST", "/nc/login/v2/poll", {"server": BASE, "loginName": LOGIN, "appPassword": PASSWORD})
    flow.poll()
    flow.cancel()
    with error_code("credentials-unavailable"):
        flow.take_credentials()


def test_capabilities_without_talk_do_not_claim_rooms_absent_due_to_request_error():
    network = discovery_network()
    network.responses[("GET", "/nc/ocs/v1.php/cloud/capabilities")] = [
        HttpResponse(200, {}, json.dumps(ocs({"capabilities": {}})).encode())
    ]
    snapshot = discover_account(network.transport(), credentials())
    assert snapshot["talk_rooms"] == []
    assert not any(item["domain"] in {"capabilities", "talk"} for item in snapshot["errors"])
    assert "spreed" not in snapshot["capabilities"]
    assert not any("spreed" in call[1] for call in network.calls)


def test_diagnostics_check_each_unique_collection_metadata_without_loading_content():
    network = discovery_network(unknown_rights=["read"])
    snapshot = discover_account(network.transport(), credentials())
    assert snapshot["complete"]
    assert datetime.fromisoformat(snapshot["checked_at"]).utcoffset().total_seconds() == 0
    diagnostics = snapshot["diagnostics"]
    assert diagnostics["user"] == {"status": "ok", "loaded": True, "code": ""}
    for domain, count in (("calendars", 3), ("task_lists", 2)):
        assert diagnostics[domain] == {
            "status": "ok", "inventory_complete": True, "discovered": count, "checked": count,
            "readable": count, "failed": 0, "skipped": 0, "checks_complete": True,
            "content_checked": False, "scope": "collection-metadata", "errors": [], "limitations": [],
        }
    probes = [(call, connection) for call, connection in zip(network.calls, network.connections, strict=True)
              if call[1].startswith(CALENDARS) and call[1] != CALENDARS]
    assert len(probes) == 4  # The mixed VEVENT/VTODO collection is requested only once.
    for (method, _, body, headers), connection in probes:
        assert method == "PROPFIND" and headers["Depth"] == "0"
        assert 0 < connection[3] <= 3
        prop = ET.fromstring(body).find(q(DAV, "prop"))
        assert {node.tag for node in prop} == {
            q(DAV, "resourcetype"), q(DAV, "current-user-privilege-set"),
            q(CALDAV, "supported-calendar-component-set"),
        }
        assert b"calendar-data" not in body and b"address-data" not in body
    assert diagnostics["files"]["root_readable"] and diagnostics["files"]["scope"] == "first-level"
    assert diagnostics["files"]["depth"] == 1 and not diagnostics["files"]["recursive"]
    assert snapshot["file_roots"][0]["read_check"]["status"] == "ok"
    assert snapshot["folders"][0]["read_check"]["status"] == "skipped"
    assert snapshot["folders"][0]["read_check"]["code"] == "folder-not-opened"
    assert PASSWORD not in json.dumps(diagnostics) and POLL_TOKEN not in json.dumps(diagnostics)


def test_collection_read_denial_is_distinct_from_successful_inventory_and_other_domains():
    network = discovery_network(unknown_rights=["read"])
    network.responses[("PROPFIND", CALENDARS + "tasks/")] = [HttpResponse(403, {}, PASSWORD.encode())]
    snapshot = discover_account(network.transport(), credentials())
    tasks = snapshot["diagnostics"]["task_lists"]
    assert not snapshot["complete"] and not snapshot["results_may_be_truncated"]
    assert tasks["inventory_complete"] and tasks["checks_complete"]
    assert (tasks["status"], tasks["checked"], tasks["readable"], tasks["failed"]) == ("partial", 2, 1, 1)
    assert tasks["errors"][0]["code"] == "permission-denied"
    assert snapshot["diagnostics"]["calendars"]["status"] == "ok"
    assert snapshot["diagnostics"]["files"]["status"] == "ok"
    assert PASSWORD not in json.dumps(snapshot)


@pytest.mark.parametrize("change,code", [
    ("privileges", "privileges-unavailable"),
    ("no-read", "read-permission-missing"),
    ("components", "components-unavailable"),
    ("type", "resource-type-unavailable"),
    ("status", "resource-unavailable"),
    ("missing", "ambiguous-dav"),
    ("malformed", "invalid-dav"),
])
def test_malformed_or_missing_collection_evidence_never_claims_readable(change, code):
    network = discovery_network(unknown_rights=["read"])
    href = CALENDARS + "both/"
    properties = collection("Mixed", components=["VEVENT", "VTODO"], rights=["read"])
    if change == "privileges":
        del properties[q(DAV, "current-user-privilege-set")]
    elif change == "no-read":
        properties[q(DAV, "current-user-privilege-set")] = []
    elif change == "components":
        del properties[q(CALDAV, "supported-calendar-component-set")]
    elif change == "type":
        del properties[q(DAV, "resourcetype")]
    body = multistatus([] if change == "missing" else [(href, properties)])
    if change == "status":
        tree = ET.fromstring(body)
        ET.SubElement(tree.find(q(DAV, "response")), q(DAV, "status")).text = "HTTP/1.1 403 Forbidden"
        body = ET.tostring(tree)
    elif change == "malformed":
        body = b"not XML " + PASSWORD.encode()
    network.responses[("PROPFIND", href)] = [HttpResponse(207, {}, body)]
    snapshot = discover_account(network.transport(), credentials())
    assert not snapshot["complete"]
    for domain in ("calendars", "task_lists"):
        result = snapshot["diagnostics"][domain]
        assert result["inventory_complete"] and result["failed"] == 1
        assert result["errors"][0]["code"] == code
    assert PASSWORD not in json.dumps(snapshot)


def test_failed_collection_inventory_is_not_an_empty_success():
    network = discovery_network(unknown_rights=["read"])
    network.responses[("PROPFIND", CALENDARS)] = [HttpResponse(403, {}, b"")]
    snapshot = discover_account(network.transport(), credentials())
    for domain in ("calendars", "task_lists"):
        result = snapshot["diagnostics"][domain]
        assert result["status"] == "failed" and result["discovered"] == 0
        assert not result["inventory_complete"] and not result["checks_complete"]
        assert result["errors"] == [{"domain": "caldav", "code": "permission-denied"}]


def test_empty_complete_inventory_is_distinct_from_failed_discovery():
    network = discovery_network(unknown_rights=["read"])
    network.responses[("PROPFIND", CALENDARS)] = [HttpResponse(207, {}, multistatus([
        (CALENDARS, {q(DAV, "resourcetype"): [ET.Element(q(DAV, "collection"))]}),
    ]))]
    snapshot = discover_account(network.transport(), credentials())
    for domain in ("calendars", "task_lists"):
        result = snapshot["diagnostics"][domain]
        assert result["status"] == "empty" and result["discovered"] == 0
        assert result["inventory_complete"] and result["checks_complete"]


@pytest.mark.parametrize("resources", [[], [(CALENDARS, {})]])
def test_missing_home_identity_or_properties_never_claims_empty_inventory(resources):
    network = discovery_network(unknown_rights=["read"])
    network.responses[("PROPFIND", CALENDARS)] = [HttpResponse(207, {}, multistatus(resources))]
    snapshot = discover_account(network.transport(), credentials())
    for domain in ("calendars", "task_lists"):
        assert snapshot["diagnostics"][domain]["status"] == "failed"
        assert not snapshot["diagnostics"][domain]["inventory_complete"]


def test_read_check_limit_is_skipped_work_not_truncated_inventory(monkeypatch):
    monkeypatch.setattr(nextcloud_onboarding, "MAX_COLLECTION_READ_CHECKS", 1)
    network = discovery_network(unknown_rights=["read"])
    snapshot = discover_account(network.transport(), credentials())
    assert not snapshot["complete"] and not snapshot["results_may_be_truncated"]
    assert "read-check-limit" in snapshot["limitations"]
    assert sum(path.startswith(CALENDARS) and path != CALENDARS for _, path, _, _ in network.calls) == 1
    for domain in ("calendars", "task_lists"):
        result = snapshot["diagnostics"][domain]
        assert result["inventory_complete"] and not result["checks_complete"]
        assert result["skipped"] > 0 and result["failed"] == 0


def test_added_read_checks_stop_at_budget_and_report_unchecked_collections(monkeypatch):
    current = [0.0]
    monkeypatch.setattr(nextcloud_onboarding.time, "monotonic", lambda: current[0])
    original = nextcloud_onboarding._check_collection_read

    def check(*args, **kwargs):
        result = original(*args, **kwargs)
        current[0] = 16.0
        return result

    monkeypatch.setattr(nextcloud_onboarding, "_check_collection_read", check)
    network = discovery_network(unknown_rights=["read"])
    snapshot = discover_account(network.transport(), credentials())
    assert "read-check-budget" in snapshot["limitations"]
    assert sum(path.startswith(CALENDARS) and path != CALENDARS for _, path, _, _ in network.calls) == 1
    assert not snapshot["results_may_be_truncated"]
    assert any(item["read_check"]["code"] == "read-check-budget" for item in snapshot["calendars"])


def test_inventory_limit_reports_discovered_count_separately_from_checked_count():
    network = discovery_network(unknown_rights=["read"])
    snapshot = discover_account(network.transport(), credentials(), max_resources=1)
    calendars = snapshot["diagnostics"]["calendars"]
    assert calendars["discovered"] == 3 and calendars["checked"] == 1 and calendars["skipped"] == 2
    assert not calendars["inventory_complete"] and not calendars["checks_complete"]
    assert snapshot["results_may_be_truncated"]


@pytest.mark.parametrize("response,code", [
    (HttpResponse(401, {}, PASSWORD.encode()), "authentication-failed"),
    (HttpResponse(200, {}, json.dumps(ocs({"id": ""})).encode()), "invalid-user-id"),
    (HttpResponse(200, {}, b"not JSON " + PASSWORD.encode()), "invalid-json"),
])
def test_failed_user_lookup_has_safe_diagnostics_and_never_loads_downstream(response, code):
    network = FakeNetwork()
    network.responses[("GET", "/nc/ocs/v1.php/cloud/user")] = [response]
    with error_code(code) as error:
        discover_account(network.transport(), credentials())
    diagnostics = error.value.diagnostics
    assert diagnostics["user"] == {"status": "failed", "loaded": False, "code": code}
    for domain in ("calendars", "task_lists", "files"):
        assert diagnostics[domain]["status"] == "skipped"
        assert diagnostics[domain]["limitations"] == ["user-unavailable"]
        assert not diagnostics[domain]["inventory_complete"]
    assert len(network.calls) == 1
    assert PASSWORD not in str(error.value) and PASSWORD not in json.dumps(diagnostics)


def test_folder_browsing_checks_selected_level_only_and_denied_rights_stay_visible():
    network = FakeNetwork()
    href = FILES + "Shared"
    network.add("PROPFIND", href, multistatus([
        (href + "/", collection("Shared", file_flags="G")),
        (href + "/Child/", collection("Child", file_flags="G")),
    ]), status=207)
    result = discover_folders(network.transport(), credentials(user_id=UID), "Shared")
    assert result["complete"] and result["depth"] == 1
    assert [item["read_check"]["status"] for item in result["folders"]] == ["ok", "skipped"]
    assert len(network.calls) == 1
    network.add("PROPFIND", href,
                multistatus([(href + "/", collection("Shared", file_flags="R"))]), status=207)
    result = discover_folders(network.transport(), credentials(user_id=UID), "Shared")
    assert result["inventory_complete"] and not result["complete"]
    assert result["folders"][0]["read_check"]["code"] == "read-permission-missing"


def test_folder_depth_one_response_cannot_claim_nested_descendants_as_checked():
    network = FakeNetwork()
    network.add("PROPFIND", FILES, multistatus([
        (FILES, collection("Root", file_flags="G")),
        (FILES + "Child/Grandchild/", collection("Nested", file_flags="G")),
    ]), status=207)
    with error_code("invalid-dav"):
        discover_folders(network.transport(), credentials(user_id=UID))


@pytest.mark.parametrize("phase", ["headers", "body"])
def test_probe_deadline_interrupts_slow_stream_even_when_each_recv_is_timely(phase):
    server, client = socket.socketpair()
    stop = threading.Event()

    class Connection:
        sock = client

        def __init__(self, host, port, address, timeout, context):
            self.sock.settimeout(timeout)

        def request(self, *args, **kwargs):
            pass

        def getresponse(self):
            response = http.client.HTTPResponse(self.sock)
            response.begin()
            return response

        def close(self):
            self.sock.close()

    def trickle():
        try:
            if phase == "body":
                server.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 100\r\n\r\n")
                remaining = b"x" * 100
            else:
                remaining = b"HTTP/1.1 200 OK\r\nContent-Length: 0\r\n\r\n"
            for character in remaining:
                server.sendall(bytes([character]))
                if stop.wait(0.02):
                    break
        except OSError:
            pass

    worker = threading.Thread(target=trickle, daemon=True)
    worker.start()
    started = time.monotonic()
    try:
        transport = SafeNextcloudTransport(
            BASE, resolver=lambda host, port: ["192.168.1.20"], connection_factory=Connection,
        )
        with error_code("request-timeout"):
            transport.request("PROPFIND", "metadata", timeout_seconds=0.15)
        assert time.monotonic() - started < 0.75
    finally:
        stop.set()
        client.close()
        worker.join(timeout=1)
        server.close()
    assert not worker.is_alive()
