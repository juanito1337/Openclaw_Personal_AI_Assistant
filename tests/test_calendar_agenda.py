"""Calendar agendas exercise REPORT boundaries and fail closed on partial data."""

from datetime import UTC, datetime, timedelta
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape
from zoneinfo import ZoneInfo

import pytest
from test_nextcloud_foreground_access import account  # noqa: F401

from personal_assistant.config import AssistantConfig
from personal_assistant.connectors.nextcloud.calendar import NextcloudCalendar
from personal_assistant.connectors.nextcloud.client import DavResponse
from personal_assistant.connectors.nextcloud.discovery import DiscoveredCollection
from personal_assistant.connectors.nextcloud.xmlutil import CALDAV, q
from personal_assistant.nextcloud_access_runtime import _report_shape, component_request
from personal_assistant.nextcloud_onboarding import HttpResponse

ZONE = "Europe/Berlin"
HREF = "/nc/remote.php/dav/calendars/users/assistant/main/"
COLLECTION = DiscoveredCollection(kind="calendar", href=HREF, name="Main", resource_id="main")


def day(month, date, timezone_name=ZONE):
    start = datetime(2026, month, date, tzinfo=ZoneInfo(timezone_name))
    return {"start": start, "end": start + timedelta(days=1), "timezone_name": timezone_name}


def resource(*events):
    return "\r\n".join(("BEGIN:VCALENDAR", "VERSION:2.0", *events, "END:VCALENDAR", ""))


def event(*lines, uid="series"):
    return "\r\n".join((
        "BEGIN:VEVENT", f"UID:{uid}", "DTSTAMP:20260930T000000Z", "SUMMARY:Synthetic", *lines, "END:VEVENT",
    ))


def row(ical, *, name="one.ics", status="HTTP/1.1 200 OK"):
    calendar_data = f"<c:calendar-data>{escape(ical)}</c:calendar-data>" if ical is not None else ""
    return (
        f"<d:response><d:href>{HREF + name}</d:href><d:propstat><d:status>{status}</d:status>"
        f'<d:prop><d:getetag>"one"</d:getetag>{calendar_data}</d:prop></d:propstat></d:response>'
    )


def multistatus(*rows):
    return (
        '<d:multistatus xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">'
        + "".join(rows) + "</d:multistatus>"
    ).encode()


def timezone_response(name="UTC", offset="+0000", *, href=HREF):
    timezone = "\r\n".join((
        "BEGIN:VCALENDAR", "VERSION:2.0", "BEGIN:VTIMEZONE", f"TZID:{name}", "BEGIN:STANDARD",
        "DTSTART:19700101T000000", f"TZOFFSETFROM:{offset}", f"TZOFFSETTO:{offset}",
        "END:STANDARD", "END:VTIMEZONE", "END:VCALENDAR",
    ))
    return multistatus(
        f"<d:response><d:href>{href}</d:href><d:propstat><d:status>HTTP/1.1 200 OK</d:status>"
        f"<d:prop><c:calendar-timezone>{timezone}</c:calendar-timezone></d:prop></d:propstat></d:response>"
    )


class LegacyTransport:
    def __init__(self, body, timezone_body=None):
        self.body = body
        self.timezone_body = timezone_body if timezone_body is not None else timezone_response()
        self.calls = []

    def request(self, method, href, **kwargs):
        self.calls.append((method, href, kwargs))
        body = self.timezone_body if method == "PROPFIND" else self.body
        return DavResponse(207, "Multi-Status", {}, body, HREF)


@pytest.mark.parametrize(
    "month,date,expected_start,expected_end,transition",
    [
        (3, 29, "20260327T230000Z", "20260330T220000Z", "TZOFFSETFROM:+0100\nTZOFFSETTO:+0200"),
        (10, 25, "20261023T220000Z", "20261026T230000Z", "TZOFFSETFROM:+0200\nTZOFFSETTO:+0100"),
    ],
)
def test_empty_agenda_requests_the_exact_dst_day(month, date, expected_start, expected_end, transition):
    transport = LegacyTransport(multistatus())
    calendar = NextcloudCalendar(AssistantConfig(), transport)
    result = calendar.list_agenda(COLLECTION, **day(month, date))
    assert result["events"] == []
    assert not result["complete"]
    assert result["errors"] == [{"code": "calendar-timezone-mismatch"}]

    (method, href, arguments), (probe, probe_href, options) = transport.calls
    assert (probe, probe_href, options["headers"]["Depth"]) == ("PROPFIND", HREF, "0")
    assert (method, href, arguments["expected"]) == ("REPORT", HREF, {207})
    request = ET.fromstring(arguments["data"])
    range_ = {"start": expected_start, "end": expected_end}
    assert request.find(".//" + q(CALDAV, "time-range")).attrib == range_
    assert request.find(".//" + q(CALDAV, "expand")).attrib == range_
    definition = request.findtext(q(CALDAV, "timezone"))
    assert "TZID:Europe/Berlin" in definition
    assert transition in definition


def test_agenda_retains_each_expanded_instance_including_overlap_and_floating_values():
    expanded = resource(
        event("DTSTART:20261024T215500Z", "DTEND:20261024T223000Z", "RECURRENCE-ID:20261024T212000Z"),
        event("DTSTART:20261025T093000Z", "DTEND:20261025T100000Z", "RECURRENCE-ID:20261025T090000Z"),
    )
    other = [
        row(resource(event("DTSTART;VALUE=DATE:20261025", uid="date")), name="date.ics"),
        row(resource(event("DTSTART:20261025T120000", "DURATION:PT1H", uid="floating")), name="floating.ics"),
    ]
    transport = LegacyTransport(multistatus(row(expanded), *other))
    result = NextcloudCalendar(AssistantConfig(), transport).list_agenda(COLLECTION, **day(10, 25))
    assert not result["complete"]
    assert [
        (item["uid"], item["start"], item["end"], item["recurrence_id"], item["all_day"], item["recurring"])
        for item in result["events"]
    ] == [
        ("series", "2026-10-24T23:55:00+02:00", "2026-10-25T00:30:00+02:00",
         "2026-10-24T23:20:00+02:00", False, True),
        ("date", "2026-10-25", "2026-10-26", "", True, False),
        ("series", "2026-10-25T10:30:00+01:00", "2026-10-25T11:00:00+01:00",
         "2026-10-25T10:00:00+01:00", False, True),
        ("floating", "2026-10-25T12:00:00+01:00", "2026-10-25T13:00:00+01:00", "", False, False),
    ]


@pytest.mark.parametrize(
    "body",
    [
        b"<irrelevant/>",
        b"<d:multistatus xmlns:d='DAV:'><d:response>",
        multistatus(row(None)),
        multistatus(row(None, status="HTTP/1.1 403 Forbidden")),
        multistatus("<d:response><d:href>not-returned.ics</d:href>"
                    "<d:status>HTTP/1.1 404 Not Found</d:status></d:response>"),
        multistatus(row(resource(event("DTSTART:20261025T090000Z"))) * 2),
        multistatus(row(resource(event("DTSTART:20261025T090000Z", "RRULE:FREQ=DAILY")))),
        multistatus(row(resource(event("DTSTART:20261025T090000Z", "RDATE:20261024T090000Z")))),
        multistatus(row(resource(event("DTSTART;TZID=Unknown/Zone:20261025T090000")))),
        multistatus(row(resource(event("DTSTART:20261027T230000Z")))),  # Outside the padded REPORT.
    ],
)
def test_incomplete_reports_cannot_be_a_complete_empty_agenda(body):
    calendar = NextcloudCalendar(AssistantConfig(), LegacyTransport(body))
    result = calendar.list_agenda(COLLECTION, **day(10, 25))
    assert result["complete"] is False
    assert result["errors"]


def test_bad_row_does_not_discard_valid_rows_or_expose_remote_content():
    body = multistatus(
        row(resource(event("DTSTART:20261025T100000Z", uid="meeting"))),
        row(None, name="private.ics", status="HTTP/1.1 403 Private details"),
    )
    calendar = NextcloudCalendar(AssistantConfig(), LegacyTransport(body))
    result = calendar.list_agenda(COLLECTION, **day(10, 25))
    assert result["complete"] is False
    assert [item["uid"] for item in result["events"]] == ["meeting"]
    assert "Private details" not in str(result["errors"])
    assert "private.ics" not in str(result["errors"])


def test_managed_report_permits_only_matching_bounded_vevent_expansion(account):  # noqa: F811
    pa = account.pa
    collection = pa._calendar_collection(pa.registry.get(account.id))
    account.replies["REPORT"] = HttpResponse(207, {}, multistatus())
    account.replies["PROPFIND"] = HttpResponse(207, {}, timezone_response(href=account.href))
    assert pa.nextcloud_calendar.list_agenda(collection, **day(3, 29, "UTC"))["complete"]
    (method, href, arguments), (probe, probe_href, options) = account.calls
    assert (probe, probe_href, options["headers"]["Depth"]) == ("PROPFIND", account.href, "0")
    assert (method, href) == ("REPORT", account.href)
    report = arguments["data"]
    assert _report_shape(report, "VEVENT", "list")
    with pytest.raises(PermissionError):
        _report_shape(report.replace(b"20260331T000000Z", b"20260331T220000Z", 1), "VEVENT", "list")
    with pytest.raises(PermissionError):
        _report_shape(report, "VEVENT", "read")
    with pytest.raises(PermissionError):
        _report_shape(report.replace(b"20260331T000000Z", b"20260401T220000Z"), "VEVENT", "list")
    with pytest.raises(PermissionError):
        _report_shape(report.replace(b"TZOFFSETTO:+0000", b"TZOFFSETTO:+0300"), "VEVENT", "list")


@pytest.mark.parametrize(
    "zone, timezone_body, errors",
    [
        ("UTC", timezone_response(), []),
        ("UTC", timezone_response(name="Etc/UTC"), []),
        ("UTC", timezone_response(offset="+0100"), [{"code": "calendar-timezone-mismatch"}]),
        ("UTC", multistatus(), [{"code": "calendar-timezone-unverified", "reason": "dav-envelope-invalid"}]),
        (
            "UTC", timezone_response(href=HREF + "other/"),
            [{"code": "calendar-timezone-unverified", "reason": "dav-scope-mismatch"}],
        ),
        (ZONE, timezone_response(name=ZONE, offset="+0200"), [{"code": "calendar-timezone-mismatch"}]),
        (ZONE, timezone_response(name=ZONE, offset="+0100"), []),
    ],
)
def test_complete_result_requires_compatible_proved_timezone(zone, timezone_body, errors):
    transport = LegacyTransport(multistatus(), timezone_body)
    calendar = NextcloudCalendar(AssistantConfig(), transport)
    assert calendar.list_agenda(COLLECTION, **day(11, 23, zone)) == {
        "events": [], "complete": not errors, "errors": errors,
    }


def test_non_local_midnight_is_rejected_before_transport():
    transport = LegacyTransport(multistatus())
    calendar = NextcloudCalendar(AssistantConfig(), transport)
    with pytest.raises(ValueError):
        calendar.list_agenda(
            COLLECTION, start=datetime(2026, 10, 25, tzinfo=UTC), end=datetime(2026, 10, 26, tzinfo=UTC),
            timezone_name=ZONE,
        )
    assert transport.calls == []


def test_nextcloud_padding_drops_other_dates_and_keeps_a_floating_boundary_event():
    transport = LegacyTransport(
        multistatus(
            # Sabre interprets DTSTART at 23:30 as UTC for candidate selection,
            # but expansion uses the measured Berlin timezone and yields 22:30Z.
            row(resource(event("DTSTART:20261123T223000Z", uid="today"))),
            row(resource(event("DTSTART:20261123T230000Z", uid="tomorrow")), name="tomorrow.ics"),
        ),
        timezone_response(name=ZONE, offset="+0100"),
    )
    result = NextcloudCalendar(AssistantConfig(), transport).list_agenda(COLLECTION, **day(11, 23))
    assert result["complete"] is True
    assert [(entry["uid"], entry["start"]) for entry in result["events"]] == [
        ("today", "2026-11-23T23:30:00+01:00"),
    ]


def test_managed_agenda_preserves_verified_positive_rows_on_a_failed_sibling(account):  # noqa: F811
    pa = account.pa
    collection = pa._calendar_collection(pa.registry.get(account.id))
    valid = row(resource(event("DTSTART:20261123T120000Z", uid="observed"))).replace(HREF, account.href)
    failed = row(None, name="private.ics", status="HTTP/1.1 403 Confidential detail")
    failed = failed.replace(HREF, account.href)
    account.replies["REPORT"] = HttpResponse(207, {}, multistatus(valid, failed))
    result = pa.nextcloud_calendar.list_agenda(collection, **day(11, 23))
    assert [entry["uid"] for entry in result["events"]] == ["observed"]
    assert result["complete"] is False
    assert "Confidential detail" not in str(result["errors"])


def test_a_timezone_definition_without_any_observance_is_unverified():
    bad = timezone_response().replace(b"BEGIN:STANDARD\r\nDTSTART:19700101T000000\r\n", b"")
    bad = bad.replace(b"END:STANDARD\r\n", b"")
    calendar = NextcloudCalendar(AssistantConfig(), LegacyTransport(multistatus(), bad))
    assert calendar.list_agenda(COLLECTION, **day(11, 23, "UTC"))["errors"] == [
        {"code": "calendar-timezone-unverified", "reason": "observance-missing", "property_status": 200},
    ]


def test_effective_observance_takes_precedence_over_historical_offsets():
    bad = timezone_response(name=ZONE, offset="+0100").replace(
        b"END:VTIMEZONE",
        b"BEGIN:STANDARD\r\nDTSTART:20000101T000000\r\n"
        b"TZOFFSETFROM:+0100\r\nTZOFFSETTO:+0200\r\nEND:STANDARD\r\nEND:VTIMEZONE",
    )
    calendar = NextcloudCalendar(AssistantConfig(), LegacyTransport(multistatus(), bad))
    assert calendar.list_agenda(COLLECTION, **day(11, 23))["errors"] == [
        {"code": "calendar-timezone-mismatch"},
    ]


def test_expanded_timed_nominal_duration_with_lost_timezone_is_incomplete():
    transport = LegacyTransport(multistatus(row(resource(
        event("DTSTART:20261024T220000Z", "DURATION:P1D"),
    ))), timezone_response(name=ZONE, offset="+0200"))
    calendar = NextcloudCalendar(AssistantConfig(), transport)
    result = calendar.list_agenda(COLLECTION, **day(10, 25))
    assert result["events"] == []
    assert result["errors"] == [{"code": "incomplete-calendar-response"}]


@pytest.mark.parametrize("status,code", [(401, "rights-unverified"), (403, "server-right-missing")])
def test_managed_timezone_denial_preserves_positive_agenda_rows(account, status, code):  # noqa: F811
    pa = account.pa
    collection = pa._calendar_collection(pa.registry.get(account.id))
    valid = row(resource(event("DTSTART:20261123T120000Z", uid="observed"))).replace(HREF, account.href)
    account.replies["REPORT"] = HttpResponse(207, {}, multistatus(valid))
    account.replies["PROPFIND"] = HttpResponse(status, {}, b"private error body")
    result = pa.nextcloud_calendar.list_agenda(collection, **day(11, 23))
    assert [entry["uid"] for entry in result["events"]] == ["observed"]
    assert result["complete"] is False
    assert result["errors"] == [{"code": code}]


def test_managed_legacy_report_comment_cannot_opt_into_partial_validation(account):  # noqa: F811
    pa = account.pa
    collection = pa._calendar_collection(pa.registry.get(account.id))
    failed = row(None, status="HTTP/1.1 403 Confidential detail").replace(HREF, account.href)
    account.replies["REPORT"] = HttpResponse(207, {}, multistatus(failed))
    query = b"""<c:calendar-query xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">
      <!-- <c:expand marker -->
      <d:prop><d:getetag/><c:calendar-data/></d:prop>
      <c:filter><c:comp-filter name="VCALENDAR"><c:comp-filter name="VEVENT"/></c:comp-filter></c:filter>
    </c:calendar-query>"""
    with pytest.raises(PermissionError):
        component_request(
            pa.nextcloud_client, "VEVENT", "list", collection, "REPORT", account.href,
            data=query, headers={"Depth": "1", "Content-Type": "application/xml"}, expected={207},
        )


def test_an_annual_standard_and_daylight_definition_proves_both_dst_transition_days():
    property_body = timezone_response(name=ZONE, offset="+0100").replace(
        b"DTSTART:19700101T000000\r\nTZOFFSETFROM:+0100",
        b"DTSTART:19961027T030000\r\nRRULE:FREQ=YEARLY;BYMONTH=10;BYDAY=-1SU\r\nTZOFFSETFROM:+0200",
    ).replace(
        b"END:VTIMEZONE",
        b"BEGIN:DAYLIGHT\r\nDTSTART:19810329T020000\r\nRRULE:FREQ=YEARLY;BYMONTH=3;BYDAY=-1SU\r\n"
        b"TZOFFSETFROM:+0100\r\nTZOFFSETTO:+0200\r\nEND:DAYLIGHT\r\nEND:VTIMEZONE",
    )
    calendar = NextcloudCalendar(AssistantConfig(), LegacyTransport(multistatus(), property_body))
    assert calendar.list_agenda(COLLECTION, **day(3, 29))["complete"]
    assert calendar.list_agenda(COLLECTION, **day(10, 25))["complete"]
