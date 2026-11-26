"""A missing expanded row can hide a real instance on the requested civil day."""

import pytest
from test_calendar_agenda import COLLECTION, MissingTimezoneTransport, day, event, multistatus, resource, row

from personal_assistant.config import AssistantConfig
from personal_assistant.connectors.nextcloud.calendar import NextcloudCalendar


def omitted_resource(*components, month=11, date=26):
    raw = multistatus(row(resource(*components)))
    transport = MissingTimezoneTransport(multistatus(), raw)
    return NextcloudCalendar(AssistantConfig(), transport).list_agenda(COLLECTION, **day(month, date))


def test_annual_tzid_series_with_no_possible_start_near_day():
    result = omitted_resource(event("DTSTART;TZID=America/New_York:20250228T190000", "RRULE:FREQ=YEARLY"))

    assert result == {"complete": True, "events": [], "errors": []}


@pytest.mark.parametrize("start,rule", [
    ("20250102T100000", "BYDAY=TH"),
    ("20250126T100000", "BYMONTHDAY=26"),
    ("20250127T100000", "BYMONTHDAY=-5"),
    ("20250101T100000", "BYYEARDAY=1,330"),
    ("20250109T100000", "BYWEEKNO=2,48;BYDAY=TH"),
    ("20250109T100000", "BYWEEKNO=2,48"),
])
def test_yearly_day_selector_without_bymonth_can_match_november(start, rule):
    result = omitted_resource(event(f"DTSTART;TZID=Europe/Berlin:{start}", f"RRULE:FREQ=YEARLY;{rule}"))

    assert not result["complete"] and result["errors"]


def test_annual_start_on_previous_source_date_can_land_on_berlin_dst_day():
    result = omitted_resource(
        event("DTSTART;TZID=America/New_York:20250328T201500", "RRULE:FREQ=YEARLY"), month=3, date=29,
    )

    assert not result["complete"]


def test_annual_multiday_duration_overlaps_day_of_autumn_clock_change():
    result = omitted_resource(
        event("DTSTART;TZID=Europe/Berlin:20251023T233000", "DURATION:P3D", "RRULE:FREQ=YEARLY"),
        month=10, date=25,
    )

    assert not result["complete"]


@pytest.mark.parametrize("extra_components", [
    (event("DTSTART;TZID=Europe/Berlin:20250504T100000", "RRULE:FREQ=YEARLY",
           "RDATE;TZID=Pacific/Kiritimati:20261127T003000"),),
    (event("DTSTART;TZID=Europe/Berlin:20250504T100000", "RRULE:FREQ=YEARLY"),
     event("RECURRENCE-ID;TZID=Europe/Berlin:20260504T100000", "DTSTART;TZID=Europe/Berlin:20261126T130000")),
])
def test_added_or_rescheduled_occurrence_can_cross_annual_date_bound(extra_components):
    result = omitted_resource(*extra_components)

    assert not result["complete"]


@pytest.mark.parametrize("override_duration", [(), ("DURATION:PT1H",)])
def test_moved_annual_instance_without_dtend_can_retain_server_master_duration(override_duration):
    result = omitted_resource(
        event("DTSTART:20250504T100000Z", "DTEND:20250511T100000Z", "RRULE:FREQ=YEARLY"),
        event("RECURRENCE-ID:20260504T100000Z", "DTSTART:20261122T100000Z", *override_duration),
    )

    assert not result["complete"]


def test_returned_override_with_no_explicit_end_keeps_its_possible_day_overlap():
    master = event("DTSTART:20250504T100000Z", "DTEND:20250511T100000Z", "RRULE:FREQ=YEARLY")
    override = event("RECURRENCE-ID:20260504T100000Z", "DTSTART:20261125T100000Z")
    transport = MissingTimezoneTransport(
        multistatus(row(resource(override))), multistatus(row(resource(master, override))),
    )
    result = NextcloudCalendar(AssistantConfig(), transport).list_agenda(COLLECTION, **day(11, 26))

    assert result["events"] or not result["complete"]


def test_returned_rdate_does_not_certify_that_nextcloud_expanded_the_rrule_too():
    master = event("DTSTART:20261101T100000Z", "RRULE:FREQ=DAILY", "RDATE:20261125T110000Z")
    rdate = event("DTSTART:20261125T110000Z", "RECURRENCE-ID:20261125T110000Z")
    transport = MissingTimezoneTransport(
        multistatus(row(resource(rdate))), multistatus(row(resource(master))),
    )
    result = NextcloudCalendar(AssistantConfig(), transport).list_agenda(COLLECTION, **day(11, 26))

    assert result["events"] or not result["complete"]


UNVERIFIED_ZONE = "\r\n".join((
    "BEGIN:VTIMEZONE", "TZID:Europe/Berlin", "BEGIN:STANDARD", "DTSTART:19700101T000000",
    "TZOFFSETFROM:+0100", "TZOFFSETTO:+0100", "RRULE:FREQ=YEARLY;BYMONTH=99", "END:STANDARD", "END:VTIMEZONE",
))


@pytest.mark.parametrize("wall_time,complete", [
    ("20261001T120000", True),
    ("20261125T003000", False),  # An offset near -24h moves the point into the requested day.
    ("20261127T210000", False),  # An offset near +24h moves it back into the requested day.
    ("20261126T120000", False),
])
def test_unverified_timezone_singleton_requires_disjoint_utc_envelope(wall_time, complete):
    result = omitted_resource(UNVERIFIED_ZONE, event(f"DTSTART;TZID=Europe/Berlin:{wall_time}"))

    assert result["complete"] is complete
    assert result["events"] == []


@pytest.mark.parametrize("replacement", [
    "TZOFFSETTO:+9900", "TZOFFSETTO:unknown", "", "TZOFFSETTO:+0100\r\nTZOFFSETTO:+9900",
])
def test_invalid_embedded_offset_cannot_prove_timezone_independent_nonoverlap(replacement):
    definition = UNVERIFIED_ZONE.replace("TZOFFSETTO:+0100", replacement)
    result = omitted_resource(definition, event("DTSTART;TZID=Europe/Berlin:20261130T150000"))

    assert not result["complete"]
    assert result["events"] == []


def test_on_day_invalid_explicit_end_does_not_authorize_empty_result():
    result = omitted_resource(event("DTSTART:20261126T120000Z", "DTEND:20261126T120000Z"))

    assert not result["complete"]
    assert {error["code"] for error in result["errors"]} == {"calendar-fallback-provenance-invalid"}


def test_old_zero_length_explicit_interval_cannot_overlap_current_day():
    result = omitted_resource(event("DTSTART:20260102T120000Z", "DTEND:20260102T120000Z"))

    assert result == {"complete": True, "events": [], "errors": []}


def test_old_start_with_unreadable_duration_can_still_span_current_day():
    result = omitted_resource(event("DTSTART;TZID=Europe/Berlin:20260102T120000", "DURATION:unknown"))

    assert not result["complete"]
