from email.message import Message
from io import BytesIO
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.request import HTTPSHandler, build_opener
from urllib.response import addinfourl

import pytest

from personal_assistant.config import AssistantConfig
from personal_assistant.connectors.nextcloud.client import NextcloudClient, NextcloudError


@pytest.mark.parametrize("status", [207, 500])
@pytest.mark.parametrize("size,accepted", [(32, True), (33, False)])
def test_legacy_calendar_read_stops_at_requested_response_bound(monkeypatch, status, size, accepted):
    # Exercise the actual HTTP body read, including HTTP error bodies: no large
    # calendar payload may first be downloaded and only then rejected by XML code.
    client = NextcloudClient.__new__(NextcloudClient)
    client.config = AssistantConfig()
    client.connection = None
    monkeypatch.setattr(client, "missing_environment", lambda: [])
    monkeypatch.setattr(client, "credentials", lambda: ("https://calendar.example", "fixture", "fixture"))
    monkeypatch.setattr(client, "validate_url", lambda: "https://calendar.example")
    reads = []

    class Body(BytesIO):
        status = 207
        reason = "OK"
        headers = {}
        url = "https://calendar.example/calendar/"

        def read(self, count=-1):
            reads.append(count)
            assert count == 33
            return super().read(count)

    def open_response(*args, **kwargs):
        body = Body(b"x" * size)
        if status == 500:
            raise HTTPError(body.url, 500, "Error", {}, body)
        return body

    monkeypatch.setattr("urllib.request.urlopen", open_response)
    monkeypatch.setattr(
        "urllib.request.build_opener",
        lambda *_: SimpleNamespace(open=open_response),
    )

    def request():
        return client._legacy_request(
            "REPORT", "/calendar/", {}, b"fixture", None, None,
            follow_redirects=True, max_response_bytes=32,
        )

    if accepted:
        response = request()
        assert response.status == status and response.data == b"x" * size
    else:
        with pytest.raises(NextcloudError, match="^response-limit$"):
            request()
    assert reads == [33]


def test_managed_calendar_read_uses_the_stricter_transport_bound(monkeypatch):
    client = NextcloudClient.__new__(NextcloudClient)
    client.config = AssistantConfig()
    client.connection = {"expected_ips": ["192.0.2.1"]}
    monkeypatch.setattr(client, "credentials", lambda: ("https://calendar.example", "fixture", "fixture"))
    limits = []

    class Transport:
        def __init__(self, base_url, **kwargs):
            limits.append(kwargs["max_response_bytes"])

        def request(self, *args, **kwargs):
            return SimpleNamespace(status=207, headers={}, body=b"fixture")

        def resolve_url(self, path):
            return "https://calendar.example" + path

    monkeypatch.setattr("personal_assistant.nextcloud_onboarding.SafeNextcloudTransport", Transport)
    for requested in (1024, 128 * 1024 * 1024):
        client._managed_request(
            "REPORT", "/calendar/", {}, b"fixture", None,
            follow_redirects=False, max_response_bytes=requested,
        )
    assert limits == [1024, max(client.config.search.max_file_bytes, 4 * 1024 * 1024)]


@pytest.mark.parametrize("limit", [0, -1, True, "1024"])
def test_invalid_response_bound_is_rejected_before_transport(monkeypatch, limit):
    client = NextcloudClient.__new__(NextcloudClient)
    with pytest.raises(ValueError, match="response-bound-invalid"):
        client.request("REPORT", "/calendar/", max_response_bytes=limit)


@pytest.mark.parametrize("managed,path,expected", [
    (False, "/remote.php/dav/calendars/a/", "https://calendar.example/nc/remote.php/dav/calendars/a/"),
    (True, "/remote.php/dav/calendars/a/", "https://calendar.example/nc/remote.php/dav/calendars/a/"),
    (True, "/nc/remote.php/dav/calendars/a/", "https://calendar.example/nc/remote.php/dav/calendars/a/"),
])
def test_calendar_expected_request_url_preserves_instance_subpath(monkeypatch, managed, path, expected):
    client = NextcloudClient.__new__(NextcloudClient)
    client.connection = {"server_url": "https://calendar.example/nc"} if managed else None
    monkeypatch.setattr(client, "validate_url", lambda: "https://calendar.example/nc")
    assert client.dav_request_url(path) == expected


def test_bounded_legacy_request_rejects_redirect_before_draining_it(monkeypatch):
    client = NextcloudClient.__new__(NextcloudClient)
    client.config = AssistantConfig()
    client.connection = None
    monkeypatch.setattr(client, "missing_environment", lambda: [])
    monkeypatch.setattr(client, "credentials", lambda: ("https://calendar.example", "fixture", "fixture"))
    monkeypatch.setattr(client, "validate_url", lambda: "https://calendar.example")
    reads = []

    class Body(BytesIO):
        def read(self, count=-1):
            reads.append(count)
            return super().read(count)

    class Redirect(HTTPSHandler):
        def https_open(self, request):
            headers = Message()
            headers["Location"] = "/elsewhere/"
            body = addinfourl(Body(b"fixture" * 10), headers, request.full_url, code=302)
            body.msg = "Found"
            return body

    unbounded_opener = build_opener(Redirect())
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda request, timeout, context: unbounded_opener.open(request, timeout=timeout),
    )
    monkeypatch.setattr(
        "urllib.request.build_opener",
        lambda *handlers: build_opener(Redirect(), handlers[-1]),
    )

    with pytest.raises(NextcloudError, match="^redirect-blocked$"):
        client._legacy_request(
            "GET", "/calendar/", {}, None, None, None, follow_redirects=True, max_response_bytes=16,
        )

    assert reads == []


def test_calendar_expected_request_url_matches_transport_for_canonical_origin_alias():
    from personal_assistant.nextcloud_onboarding import SafeNextcloudTransport

    client = NextcloudClient.__new__(NextcloudClient)
    base = "https://calendar.example/nc"
    client.connection = {"server_url": base}
    transport = SafeNextcloudTransport(base, resolver=lambda *_: ["192.168.2.3"])
    href = "https://CALENDAR.EXAMPLE:443/nc/remote.php/dav/calendars/a/"
    assert client.dav_request_url(href) == transport.resolve_url(href)
