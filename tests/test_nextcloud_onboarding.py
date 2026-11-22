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


def add_propstat(response, status, *properties):
    propstat = ET.SubElement(response, q(DAV, "propstat"))
    ET.SubElement(propstat, q(DAV, "status")).text = status
    prop = ET.SubElement(propstat, q(DAV, "prop"))
    for property_tag in properties:
        ET.SubElement(prop, property_tag)


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
    network.add("PROPFIND", BOOKS + "contacts/", multistatus([
        (BOOKS + "contacts/", collection("Contacts", rights=["read"])),
    ]), status=207)
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
    for domain, count in (("calendars", 3), ("task_lists", 2), ("addressbooks", 1)):
        assert diagnostics[domain] == {
            "status": "ok", "inventory_complete": True, "discovered": count, "checked": count,
            "readable": count, "failed": 0, "skipped": 0, "checks_complete": True,
            "probed": count, "retained": 0,
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
    [(method, _, body, headers)] = [call for call in network.calls if call[1] == BOOKS + "contacts/"]
    assert (method, headers["Depth"]) == ("PROPFIND", "0")
    assert {node.tag for node in ET.fromstring(body).find(q(DAV, "prop"))} == {
        q(DAV, "resourcetype"), q(DAV, "current-user-privilege-set"),
    }
    assert snapshot["addressbooks"][0]["read_check"]["permissions"] == ["read"]
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


@pytest.mark.parametrize("properties,code", [
    (collection("Different type", components=["VEVENT"], rights=["read"]), "resource-type-unavailable"),
    (collection("Missing privileges"), "privileges-unavailable"),
    (collection("Missing read right", rights=["bind"]), "read-permission-missing"),
    ({q(CARDDAV, "address-data"): PASSWORD}, "resource-type-unavailable"),
])
def test_addressbook_probe_requires_own_type_and_read_privilege(properties, code):
    network = discovery_network(unknown_rights=["read"])
    href = BOOKS + "contacts/"
    del network.responses[("PROPFIND", href)]
    network.add("PROPFIND", href, multistatus([(href, properties)]), status=207)
    snapshot = discover_account(network.transport(), credentials())
    books = snapshot["diagnostics"]["addressbooks"]
    assert not snapshot["complete"] and books["inventory_complete"] and books["checks_complete"]
    assert (books["status"], books["checked"], books["readable"], books["failed"]) == ("failed", 1, 0, 1)
    assert snapshot["addressbooks"][0]["read_check"]["code"] == code
    assert snapshot["diagnostics"]["calendars"]["status"] == "ok"
    assert PASSWORD not in json.dumps(snapshot)


@pytest.mark.parametrize("change,code", [
    ("wrong-href", "ambiguous-dav"), ("extra-row", "invalid-dav"),
    ("malformed-propstat", "resource-type-unavailable"), ("duplicate-property", "ambiguous-dav"),
    ("duplicate-propstat-status", "ambiguous-dav"), ("duplicate-prop-container", "ambiguous-dav"),
    ("duplicate-response-status", "ambiguous-dav"), ("conflicting-property-status", "ambiguous-dav"),
])
def test_addressbook_probe_requires_unambiguous_successful_dav_metadata(change, code):
    network = discovery_network(unknown_rights=["read"])
    href = BOOKS + "contacts/"
    properties = collection("Contacts", rights=["read"])
    rows = [(BOOKS + "other/" if change == "wrong-href" else href, properties)]
    if change == "extra-row":
        rows.append((BOOKS + "other/", properties))
    tree = ET.fromstring(multistatus(rows))
    response = tree.find(q(DAV, "response"))
    propstat = response.find(q(DAV, "propstat"))
    if change == "malformed-propstat":
        propstat.find(q(DAV, "status")).text = "unexpected 200 status"
    if change == "duplicate-property":
        prop = propstat.find(q(DAV, "prop"))
        prop.append(ET.fromstring(ET.tostring(prop.find(q(DAV, "current-user-privilege-set")))))
    if change == "duplicate-propstat-status":
        ET.SubElement(propstat, q(DAV, "status")).text = "HTTP/1.1 403 Forbidden"
    if change == "duplicate-prop-container":
        ET.SubElement(propstat, q(DAV, "prop"))
    if change == "duplicate-response-status":
        ET.SubElement(response, q(DAV, "status")).text = "HTTP/1.1 200 OK"
        ET.SubElement(response, q(DAV, "status")).text = "HTTP/1.1 403 Forbidden"
    if change == "conflicting-property-status":
        add_propstat(response, "HTTP/1.1 403 Forbidden", q(DAV, "current-user-privilege-set"))
    network.responses[("PROPFIND", href)] = [HttpResponse(207, {}, ET.tostring(tree))]
    snapshot = discover_account(network.transport(), credentials())
    assert snapshot["addressbooks"][0]["read_check"]["code"] == code
    assert snapshot["diagnostics"]["calendars"]["status"] == "ok"


def test_exact_permissions_do_not_inherit_stale_inventory_grants():
    network = discovery_network(unknown_rights=["read"])
    href = CALENDARS + "tasks/"
    del network.responses[("PROPFIND", href)]
    network.add("PROPFIND", href, multistatus([
        (href, collection("Tasks", components=["VTODO"], rights=["read"])),
    ]), status=207)
    tasks = next(item for item in discover_account(network.transport(), credentials())["task_lists"]
                 if item["href"] == href)
    assert tasks["can_create"] and tasks["can_update"]  # Inventory advertisement remains inspectable.
    assert tasks["read_check"]["permissions"] == ["read"]


def test_addressbooks_and_caldav_share_the_thirty_two_request_budget():
    network = discovery_network(unknown_rights=["read"])
    books = [(BOOKS + f"book-{number:02d}/", collection(f"Book {number:02d}", rights=["read"]))
             for number in range(32)]
    del network.responses[("PROPFIND", BOOKS)]
    network.add("PROPFIND", BOOKS, multistatus([
        (BOOKS, {q(DAV, "resourcetype"): [ET.Element(q(DAV, "collection"))]}), *books,
    ]), status=207)
    for href, properties in books:
        network.add("PROPFIND", href, multistatus([(href, properties)]), status=207)
    snapshot = discover_account(network.transport(), credentials())
    probes = [path for _, path, _, headers in network.calls if headers.get("Depth") == "0"
              and path.startswith((CALENDARS, BOOKS))]
    assert len(probes) == 32 and len(set(probes)) == 32
    assert snapshot["diagnostics"]["calendars"]["status"] == "ok"
    result = snapshot["diagnostics"]["addressbooks"]
    assert result["inventory_complete"] and not result["checks_complete"]
    assert (result["status"], result["discovered"], result["readable"], result["skipped"]) == (
        "partial", 32, 28, 4
    )
    assert result["limitations"] == ["read-check-limit"] and not snapshot["results_may_be_truncated"]


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
    assert snapshot["addressbooks"][0]["read_check"]["code"] == "read-check-budget"


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
    for domain in ("calendars", "task_lists", "addressbooks", "files", "talk"):
        assert diagnostics[domain]["status"] == "skipped"
        assert diagnostics[domain]["limitations"] == ["user-unavailable"]
        assert not diagnostics[domain]["inventory_complete"]
    assert len(network.calls) == 1
    assert PASSWORD not in str(error.value) and PASSWORD not in json.dumps(diagnostics)


def test_unreachable_identity_never_claims_server_or_downstream_access():
    network = FakeNetwork()
    network.responses[("GET", "/nc/ocs/v1.php/cloud/user")] = [OSError(PASSWORD)]
    with error_code("transport-error") as error:
        discover_account(network.transport(), credentials())
    report = error.value.diagnostics
    assert report["availability"] == {"status": "failed", "code": "transport-error"}
    assert report["user"]["loaded"] is False
    assert all(report[domain]["status"] == "skipped" for domain in
               ("calendars", "task_lists", "addressbooks", "files", "talk"))
    assert len(network.calls) == 1 and PASSWORD not in json.dumps(report)


def test_folder_browsing_checks_selected_level_only_and_denied_rights_stay_visible():
    network = FakeNetwork()
    href = FILES + "Shared"
    network.add("PROPFIND", href, multistatus([
        (href + "/", collection("Shared", file_flags="G", file_id="22")),
        (href + "/Child/", collection("Child", file_flags="G")),
    ]), status=207)
    result = discover_folders(network.transport(), credentials(user_id=UID), "Shared")
    assert result["complete"] and result["depth"] == 1
    assert [item["read_check"]["status"] for item in result["folders"]] == ["ok", "skipped"]
    assert len(network.calls) == 1
    network.add("PROPFIND", href,
                multistatus([(href + "/", collection("Shared", file_flags="R", file_id="22"))]), status=207)
    result = discover_folders(network.transport(), credentials(user_id=UID), "Shared")
    assert result["inventory_complete"] and not result["complete"]
    assert result["folders"][0]["read_check"]["code"] == "read-permission-missing"


@pytest.mark.parametrize("flags,file_id,read,create_file,mkdir,code", [
    ("GCK", "21", True, True, True, ""),
    ("GC", "21", True, True, False, ""),
    ("GK", "21", True, False, True, ""),
    (None, "21", None, None, None, "privileges-unavailable"),
    ("GCK", None, True, True, True, "file-id-unavailable"),
])
def test_folder_evidence_distinguishes_c_and_k(flags, file_id, read, create_file, mkdir, code):
    network = FakeNetwork()
    href = FILES + "Shared/"
    own = collection("Shared", file_flags="", rights=["all"])
    own.pop(q(OC, "permissions"))
    own.pop(q(OC, "fileid"))
    if flags is not None:
        own[q(OC, "permissions")] = flags
    if file_id is not None:
        own[q(OC, "fileid")] = file_id
    network.add("PROPFIND", href.rstrip("/"), multistatus([
        (href + "Child/", collection("Child", file_flags="GCK", file_id="987")), (href, own),
    ]), status=207)
    result = discover_folders(network.transport(), credentials(user_id=UID), "Shared", max_resources=1)
    assert result["results_may_be_truncated"] and not result["inventory_complete"]
    assert result["discovered"] == 2 and len(result["folders"]) == 1
    assert datetime.fromisoformat(result["checked_at"]).utcoffset().total_seconds() == 0
    assert result["selected_folder"] == {
        "path": "Shared", "file_id": file_id, "href": href,
        "can_read": read, "can_create_file": create_file, "can_mkdir": mkdir,
        "read_check": {"status": "failed" if code else "ok", "code": code,
                       "scope": "first-level", "content_checked": False},
    }


def test_folder_evidence_rejects_child_row_instead_of_requested_folder():
    network = FakeNetwork()
    href = FILES + "Shared/"
    network.add("PROPFIND", href.rstrip("/"), multistatus([
        (href + "Child/", collection("Child", file_flags="GCK", file_id="987")),
    ]), status=207)
    with error_code("ambiguous-dav"):
        discover_folders(network.transport(), credentials(user_id=UID), "Shared")


@pytest.mark.parametrize("failed_group_first", [False, True])
def test_folder_permissions_with_conflicting_propstats_are_ambiguous(failed_group_first):
    network = FakeNetwork()
    href = FILES + "Shared/"
    body = ET.fromstring(multistatus([(href, collection("Shared", file_flags="GCK", file_id="22"))]))
    response = body.find(q(DAV, "response"))
    add_propstat(response, "HTTP/1.1 404 Not Found", q(OC, "permissions"))
    if failed_group_first:
        first = response.find(q(DAV, "propstat"))
        response.remove(first)
        response.append(first)
    network.add("PROPFIND", href.rstrip("/"), ET.tostring(body), status=207)
    with error_code("ambiguous-dav"):
        discover_folders(network.transport(), credentials(user_id=UID), "Shared")


def test_folder_accepts_success_and_not_found_propstats_for_different_properties():
    network = FakeNetwork()
    href = FILES + "Shared/"
    body = ET.fromstring(multistatus([(href, collection("Shared", file_flags="GC", file_id="22"))]))
    response = body.find(q(DAV, "response"))
    add_propstat(response, "HTTP/1.1 404 Not Found", q(DAV, "current-user-privilege-set"))
    network.add("PROPFIND", href.rstrip("/"), ET.tostring(body), status=207)
    result = discover_folders(network.transport(), credentials(user_id=UID), "Shared")
    assert result["complete"] and result["selected_folder"]["read_check"]["status"] == "ok"
    assert result["selected_folder"]["can_create_file"] and not result["selected_folder"]["can_mkdir"]


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



def test_talk_diagnostics_count_confirmed_and_unknown_send_rights_without_content_requests():
    network = discovery_network(unknown_rights=["read"])
    rooms = [
        {"token": "writable", "permissions": 255, "readOnly": 0},
        {"token": "readonly", "permissions": 255, "readOnly": 1},
        {"token": "denied", "permissions": 127, "readOnly": 0},
        {"token": "unknown"},
        {"token": "unknownPermissions", "permissions": "255", "readOnly": 0},
        {"token": "unknownReadOnly", "permissions": 255},
    ]
    path = "/nc/ocs/v2.php/apps/spreed/api/v4/room?noStatusUpdate=1"
    network.responses[("GET", path)] = [HttpResponse(200, {}, json.dumps(ocs(rooms)).encode())]
    snapshot = discover_account(network.transport(), credentials())
    diagnostic = snapshot["diagnostics"].get("talk")
    assert diagnostic == {
        "status": "ok", "app_available": True, "api_reachable": True,
        "reference_id_supported": True, "inventory_complete": True,
        "discovered": 6, "writable": 1, "send_unconfirmed": 3,
        "code": "", "errors": [], "limitations": [],
        "scope": "room-metadata", "content_checked": False,
    }
    assert snapshot["complete"]
    assert [(method, target) for method, target, _, _ in network.calls if "spreed" in target] == [
        ("GET", path),
    ]
    assert all(method in {"GET", "PROPFIND"} for method, _, _, _ in network.calls)
    assert PASSWORD not in json.dumps(diagnostic)


def test_talk_diagnostics_confirm_app_unavailable_only_after_successful_capabilities():
    network = discovery_network(unknown_rights=["read"])
    network.responses[("GET", "/nc/ocs/v1.php/cloud/capabilities")] = [
        HttpResponse(200, {}, json.dumps(ocs({"capabilities": {}})).encode()),
    ]
    snapshot = discover_account(network.transport(), credentials())
    assert snapshot["diagnostics"].get("talk") == {
        "status": "unavailable", "app_available": False, "api_reachable": None,
        "reference_id_supported": None, "inventory_complete": False,
        "discovered": 0, "writable": 0, "send_unconfirmed": 0,
        "code": "talk-app-unavailable", "errors": [], "limitations": [],
        "scope": "room-metadata", "content_checked": False,
    }
    assert snapshot["complete"] and not snapshot["talk_rooms"]
    assert not any("spreed" in path for _, path, _, _ in network.calls)


@pytest.mark.parametrize("response,code", [
    (HttpResponse(403, {}, PASSWORD.encode()), "permission-denied"),
    (HttpResponse(200, {}, b"not JSON " + PASSWORD.encode()), "invalid-json"),
    (HttpResponse(200, {}, json.dumps(ocs({"capabilities": []})).encode()), "invalid-capabilities"),
    (HttpResponse(200, {}, json.dumps(ocs({"capabilities": {"spreed": False}})).encode()),
     "invalid-capabilities"),
    (HttpResponse(200, {}, json.dumps(ocs({"capabilities": {"spreed": {"features": "bad"}}})).encode()),
     "invalid-capabilities"),
])
def test_talk_diagnostics_do_not_treat_failed_capabilities_as_absent(response, code):
    network = discovery_network(unknown_rights=["read"])
    network.responses[("GET", "/nc/ocs/v1.php/cloud/capabilities")] = [response]
    snapshot = discover_account(network.transport(), credentials())
    diagnostic = snapshot["diagnostics"].get("talk")
    assert diagnostic is not None
    assert diagnostic["status"] == "failed"
    assert diagnostic["app_available"] is None and diagnostic["api_reachable"] is None
    assert diagnostic["reference_id_supported"] is None and not diagnostic["inventory_complete"]
    assert (diagnostic["discovered"], diagnostic["writable"], diagnostic["send_unconfirmed"]) == (0, 0, 0)
    assert diagnostic["code"] == code
    assert diagnostic["errors"] == [{"domain": "capabilities", "code": code}]
    assert not snapshot["complete"] and snapshot["diagnostics"]["files"]["status"] == "ok"
    assert not any("spreed" in path for _, path, _, _ in network.calls)
    assert PASSWORD not in json.dumps(diagnostic)


def test_talk_diagnostics_missing_reference_id_does_not_hide_successful_api_access():
    network = discovery_network(unknown_rights=["read"])
    network.responses[("GET", "/nc/ocs/v1.php/cloud/capabilities")] = [
        HttpResponse(200, {}, json.dumps(ocs({"capabilities": {"spreed": {"features": []}}})).encode()),
    ]
    snapshot = discover_account(network.transport(), credentials())
    diagnostic = snapshot["diagnostics"].get("talk")
    assert diagnostic is not None
    assert diagnostic["status"] == "unsupported"
    assert diagnostic["app_available"] is True and diagnostic["api_reachable"] is True
    assert diagnostic["reference_id_supported"] is False and diagnostic["inventory_complete"]
    assert (diagnostic["discovered"], diagnostic["writable"], diagnostic["send_unconfirmed"]) == (2, 1, 0)
    assert diagnostic["code"] == "talk-reference-id-required"
    assert diagnostic["errors"] == []
    assert len(snapshot["talk_rooms"]) == 2


@pytest.mark.parametrize("response,code,reachable", [
    (HttpResponse(403, {}, PASSWORD.encode()), "permission-denied", False),
    (HttpResponse(503, {}, PASSWORD.encode()), "upstream-error", False),
    (HttpResponse(200, {}, b"not JSON " + PASSWORD.encode()), "invalid-json", False),
    (HttpResponse(200, {}, json.dumps(ocs([], status="failure", statuscode=997)).encode()),
     "ocs-error", False),
    (HttpResponse(200, {}, json.dumps(ocs({"private": PASSWORD})).encode()), "invalid-talk-rooms", True),
    (HttpResponse(200, {}, json.dumps(ocs([{}])).encode()), "invalid-talk-rooms", True),
])
def test_talk_diagnostics_failed_room_inventory_is_not_empty_success(response, code, reachable):
    network = discovery_network(unknown_rights=["read"])
    network.responses[("GET", "/nc/ocs/v2.php/apps/spreed/api/v4/room?noStatusUpdate=1")] = [response]
    snapshot = discover_account(network.transport(), credentials())
    diagnostic = snapshot["diagnostics"].get("talk")
    assert diagnostic is not None
    assert diagnostic["status"] == "failed" and diagnostic["app_available"] is True
    assert diagnostic["api_reachable"] is reachable and diagnostic["reference_id_supported"] is True
    assert not diagnostic["inventory_complete"]
    assert (diagnostic["discovered"], diagnostic["writable"], diagnostic["send_unconfirmed"]) == (0, 0, 0)
    assert diagnostic["code"] == code and diagnostic["errors"] == [{"domain": "talk", "code": code}]
    assert not snapshot["complete"] and snapshot["diagnostics"]["files"]["status"] == "ok"
    assert PASSWORD not in json.dumps(snapshot)


def test_talk_diagnostics_preserve_partial_room_inventory_on_invalid_room():
    network = discovery_network(unknown_rights=["read"])
    rooms = [{"token": "valid", "permissions": 255, "readOnly": 0}, {"token": "bad/token"}]
    network.responses[("GET", "/nc/ocs/v2.php/apps/spreed/api/v4/room?noStatusUpdate=1")] = [
        HttpResponse(200, {}, json.dumps(ocs(rooms)).encode()),
    ]
    snapshot = discover_account(network.transport(), credentials())
    diagnostic = snapshot["diagnostics"].get("talk")
    assert diagnostic is not None
    assert diagnostic["status"] == "partial" and diagnostic["api_reachable"] is True
    assert not diagnostic["inventory_complete"]
    assert (diagnostic["discovered"], diagnostic["writable"], diagnostic["send_unconfirmed"]) == (1, 1, 0)
    assert diagnostic["code"] == "invalid-talk-rooms"
    assert diagnostic["errors"] == [{"domain": "talk", "code": "invalid-talk-rooms"}]
    assert [room["token"] for room in snapshot["talk_rooms"]] == ["valid"]


def test_talk_diagnostics_empty_inventory_requires_successful_room_api():
    network = discovery_network(unknown_rights=["read"])
    network.responses[("GET", "/nc/ocs/v2.php/apps/spreed/api/v4/room?noStatusUpdate=1")] = [
        HttpResponse(200, {}, json.dumps(ocs([])).encode()),
    ]
    snapshot = discover_account(network.transport(), credentials())
    diagnostic = snapshot["diagnostics"].get("talk")
    assert diagnostic is not None
    assert diagnostic["status"] == "empty" and diagnostic["inventory_complete"]
    assert diagnostic["app_available"] is True and diagnostic["api_reachable"] is True
    assert (diagnostic["discovered"], diagnostic["writable"], diagnostic["send_unconfirmed"]) == (0, 0, 0)
    assert diagnostic["code"] == "" and diagnostic["errors"] == []
    assert snapshot["complete"]


def test_talk_diagnostics_counts_cover_valid_inventory_before_room_limit():
    network = discovery_network(unknown_rights=["read"])
    rooms = [
        {"token": "alpha", "permissions": 255, "readOnly": 1},
        {"token": "bravo", "permissions": 255, "readOnly": 0},
        {"token": "charlie"},
    ]
    network.responses[("GET", "/nc/ocs/v2.php/apps/spreed/api/v4/room?noStatusUpdate=1")] = [
        HttpResponse(200, {}, json.dumps(ocs(rooms)).encode()),
    ]
    snapshot = discover_account(network.transport(), credentials(), max_resources=1)
    diagnostic = snapshot["diagnostics"].get("talk")
    assert diagnostic is not None
    assert diagnostic["status"] == "partial" and not diagnostic["inventory_complete"]
    assert diagnostic["api_reachable"] is True and diagnostic["reference_id_supported"] is True
    assert (diagnostic["discovered"], diagnostic["writable"], diagnostic["send_unconfirmed"]) == (3, 1, 1)
    assert diagnostic["code"] == "talk_rooms:resource-limit"
    assert diagnostic["errors"] == [] and diagnostic["limitations"] == ["talk_rooms:resource-limit"]
    assert [room["token"] for room in snapshot["talk_rooms"]] == ["alpha"]
    assert not snapshot["complete"] and snapshot["results_may_be_truncated"]


def test_talk_diagnostics_skip_after_failed_identity_without_claiming_app_absent():
    network = FakeNetwork()
    network.add("GET", "/nc/ocs/v1.php/cloud/user", PASSWORD.encode(), status=401)
    with error_code("authentication-failed") as error:
        discover_account(network.transport(), credentials())
    assert error.value.diagnostics.get("talk") == {
        "status": "skipped", "app_available": None, "api_reachable": None,
        "reference_id_supported": None, "inventory_complete": False,
        "discovered": 0, "writable": 0, "send_unconfirmed": 0,
        "code": "user-unavailable", "errors": [], "limitations": ["user-unavailable"],
        "scope": "room-metadata", "content_checked": False,
    }
    assert len(network.calls) == 1


def test_owner_refresh_advances_collection_checks_without_renewing_saved_proofs(tmp_path):
    from test_nextcloud_access import managed
    from test_nextcloud_setup import confirmation

    from personal_assistant.nextcloud_access import Scope, decide_access
    from personal_assistant.nextcloud_setup import NextcloudSetupService

    def inventory(*, insert=False):
        network = discovery_network(unknown_rights=["read"])
        books = [(BOOKS + f"book-{n:02d}/", collection(f"Book {n:02d}", rights=["read"])) for n in range(32)]
        if insert:
            books.insert(0, (BOOKS + "added/", collection("Added", rights=["read"])))
        del network.responses[("PROPFIND", BOOKS)]
        network.add("PROPFIND", BOOKS, multistatus([
            (BOOKS, {q(DAV, "resourcetype"): [ET.Element(q(DAV, "collection"))]}), *books,
        ]), status=207)
        for href, props in books:
            network.add("PROPFIND", href, multistatus([(href, props)]), status=207)
        for href in (CALENDARS + "both/", BOOKS + "book-00/"):
            network.responses[("PROPFIND", href)] = [HttpResponse(403, {}, b"")]
        return network

    first = inventory()
    previous = discover_account(first.transport(), credentials())
    store = managed(tmp_path)
    store.save({
        "server_url": BASE, "user_id": UID, "login_name": LOGIN, "app_password": PASSWORD,
        "expected_ips": list(first.transport().expected_ips), "discovery_snapshot": previous,
        "last_verified_at": previous["checked_at"],
        "talk": {"enabled": False, "room_tokens": [], "allowed_user_ids": []},
    }, expected_revision="")
    second = inventory(insert=True)
    owner = NextcloudSetupService(store, transport_factory=lambda *a, **k: second.transport())

    def book(status, number):
        return next(row for row in status["access"]["rows"] if row["name"] == f"Book {number:02d}")

    def select(status, number):
        row = book(status, number)
        params = {"account_id": status["account_id"], "expected_revision": status["revision"],
                  "expected_policy_revision": status["access"]["binding"]["policy_revision"],
                  "edits": [{"scope": row["scope"], "level": "read"}], "confirm_root": False}
        preview = owner.dispatch("access.preview", params, actor="operator")
        return owner.dispatch("access.apply", confirmation(preview), actor="operator")

    initial = select(owner.status(), 27)
    assert book(initial, 31)["supported_levels"] == ["off"]
    after = owner.dispatch("connection.refresh", {"expected_revision": initial["revision"]}, actor="operator")
    assert "read" in book(after, 31)["supported_levels"] and not book(after, 31)["desired_operations"]
    probes = [p for _, p, _, h in second.calls if h.get("Depth") == "0" and p.startswith((CALENDARS, BOOKS))]
    assert len(probes) == len(set(probes)) == 32
    assert BOOKS + "book-27/" not in probes
    assert book(after, 27)["server"]["checked_at"] == book(initial, 27)["server"]["checked_at"]
    assert book(after, 27)["server"]["checked_at"] != after["preview"]["checked_at"]
    assert "read" in book(after, 27)["effective_operations"]
    assert book(after, 27)["server"]["read_check_source"] == "retained"
    assert book(after, 31)["server"]["read_check_source"] == "probed"
    summary = after["preview"]["diagnostics"]["addressbooks"]
    actual_book_calls = [p for p in probes if p.startswith(BOOKS)]
    assert summary["probed"] == len(actual_book_calls)
    assert summary["retained"] == summary["checked"] - len(actual_book_calls)
    assert summary["retained"] > 0
    assert "read" not in book(after, 0)["supported_levels"]
    saved = select(after, 31)
    assert decide_access(store.snapshot(), Scope(**book(saved, 31)["scope"]), "read").allowed


@pytest.mark.parametrize(
    "invalid", ["expired", "no-stamp", "changed-right", "changed-component", "other-principal"]
)
def test_skipped_collection_never_inherits_invalid_prior_evidence(monkeypatch, invalid):
    from datetime import UTC, timedelta

    from personal_assistant.nextcloud_onboarding import _check_collections

    start = datetime.now(UTC)
    account = {"server_url": BASE, "user_id": UID, "account_id": "hash", "principal_href": PRINCIPAL}
    row = {"kind": "calendar", "href": CALENDARS + "saved/", "resource_id": "saved",
           "components": ["VEVENT"], "permissions": ["read"], "can_read": True, "privileges_verified": True}
    check = {
        "status": "ok", "scope": "collection-metadata", "content_checked": False, "permissions": ["read"]
    }
    old = {**row, "checked_at": (start - timedelta(seconds=30)).isoformat(), "read_check": check}
    previous = {**account, "calendars": [old]}
    if invalid == "expired":
        old["checked_at"] = (start - timedelta(days=1)).isoformat()
    elif invalid == "no-stamp":
        del old["checked_at"]
    elif invalid == "changed-right":
        old["permissions"] = ["read", "create"]
    elif invalid == "changed-component":
        old["components"] = ["VEVENT", "VTODO"]
    else:
        previous["principal_href"] = PRINCIPAL + "other/"
    # All 32 real budget slots are consumed by newly discovered earlier rows.
    prefix = [{**row, "href": CALENDARS + str(i) + "/", "resource_id": str(i)} for i in range(32)]
    current = {**account, "calendars": [*prefix, row], "limitations": []}
    monkeypatch.setattr(nextcloud_onboarding, "_check_collection_read", lambda *a, **kw: check)
    _check_collections(None, None, current, previous, ["calendars"], start)
    assert row["read_check"]["status"] == "skipped" and row["checked_at"] is None
    assert row["read_check_source"] == "skipped"


def test_thirty_two_failing_collection_probes_do_not_starve_the_next_unmeasured_book(monkeypatch):
    from datetime import UTC

    from personal_assistant.nextcloud_onboarding import _check_collections, _read_check

    identity = {"server_url": BASE, "user_id": UID, "account_id": "hash", "principal_href": PRINCIPAL}
    rows = [{"kind": "addressbook", "href": BOOKS + str(i), "resource_id": str(i),
             "can_read": True, "privileges_verified": True, "permissions": ["read"]} for i in range(33)]
    first = {**identity, "addressbooks": [dict(r) for r in rows], "limitations": []}
    second = {**identity, "addressbooks": [dict(r) for r in rows], "limitations": []}
    calls = []

    def probe(_transport, _credentials, row, **options):
        calls.append(row["resource_id"])
        if row["resource_id"] == "32":
            return {**_read_check("ok"), "permissions": ["read"]}
        return _read_check("failed", "permission-denied")

    monkeypatch.setattr(nextcloud_onboarding, "_check_collection_read", probe)
    _check_collections(None, None, first, None, ["addressbooks"], datetime.now(UTC))
    assert calls == [str(i) for i in range(32)]
    calls.clear()
    _check_collections(None, None, second, first, ["addressbooks"], datetime.now(UTC))
    assert calls[0] == "32" and len(calls) == 32
    assert second["addressbooks"][32]["read_check"]["status"] == "ok"
    assert all(row["read_check"]["status"] == "failed" for row in second["addressbooks"][:32])
