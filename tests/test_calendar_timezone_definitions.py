from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from personal_assistant.calendar_agenda import agenda_timezone_error, project_fallback_diagnostic_counts


def _timezone_definition(rule: str) -> str:
    return "\r\n".join((
        "BEGIN:VCALENDAR", "VERSION:2.0", "BEGIN:VTIMEZONE", "TZID:Europe/Berlin",
        "BEGIN:STANDARD", "DTSTART:20241027T030000", "TZOFFSETFROM:+0200", "TZOFFSETTO:+0100",
        f"RRULE:FREQ=YEARLY;{rule}", "END:STANDARD", "END:VTIMEZONE", "END:VCALENDAR",
    ))


def _evaluate(rule: str) -> tuple[str, str]:
    zone = ZoneInfo("Europe/Berlin")
    return agenda_timezone_error(
        _timezone_definition(rule), zone,
        datetime(2026, 11, 26, tzinfo=zone), datetime(2026, 11, 27, tzinfo=zone),
    )


@pytest.mark.parametrize(("rule", "reason"), [
    ("BYMONTH=10;", "rrule-field-syntax"),
    ("BYMONTH=10;PRIVATE-FIXTURE", "rrule-field-syntax"),
    ("BYMONTH=10,11", "rrule-bymonth-list"),
    ("BYMONTH=private-fixture", "rrule-bymonth-invalid"),
    ("BYMONTH=13", "rrule-bymonth-range"),
    ("BYMONTHDAY=25,private-fixture", "rrule-bymonthday-invalid"),
    ("BYMONTH=10;UNTIL=20281029T020000", "rrule-until-local"),
    ("BYMONTH=10;UNTIL=20281029", "rrule-until-date"),
    ("BYMONTH=10;UNTIL=private-fixture", "rrule-until-invalid"),
    ("BYMONTH=10;UNTIL=20281329T010000Z", "rrule-until-invalid"),
])
def test_yearly_observance_rejections_report_only_the_structural_reason(rule, reason):
    assert _evaluate(rule) == ("calendar-timezone-unverified", reason)
    key = f"embedded-timezone/{reason}"
    assert project_fallback_diagnostic_counts(
        "calendar-fallback-provenance-invalid", {key: 2, f"embedded-timezone/{rule}": 1},
    ) == {key: 2}


@pytest.mark.parametrize("rule", [
    "BYMONTH=10;BYDAY=-1SU",
    "BYMONTH=10;BYDAY=SU;BYMONTHDAY=25,26,27,28,29,30,31;UNTIL=20281029T010000Z",
])
def test_valid_yearly_observances_remain_verifiable(rule):
    assert _evaluate(rule) == ("", "")
