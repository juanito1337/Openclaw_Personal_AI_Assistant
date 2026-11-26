"""Bounded CalDAV agenda queries and validation of their expanded results."""

from __future__ import annotations

import re
from calendar import monthrange
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from urllib.parse import unquote, urlsplit
from xml.etree import ElementTree as ET
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .connectors.nextcloud.xmlutil import CALDAV, DAV, q
from .ical_edit import _component_blocks, _direct_property_indices, property_name, unescape_ical, unfold_ical

_UTC_STAMP = "%Y%m%dT%H%M%SZ"
_LOCAL_STAMP = "%Y%m%dT%H%M%S"
_RECURRENCE_PROPERTIES = {"EXDATE", "EXRULE", "RDATE", "RRULE"}
_UTC_NAMES = {"UTC", "Etc/UTC", "Etc/GMT", "GMT", "UCT", "Etc/UCT", "Zulu", "Etc/Zulu"}
_DAV_SUCCESS = re.compile(r"HTTP/\d+(?:\.\d+)? 200(?: [^\r\n]*)?")
_DAV_STATUS = re.compile(r"HTTP/\d+(?:\.\d+)? ([1-5][0-9]{2})(?: [^\r\n]*)?")
_REPORT_MARGIN = timedelta(days=1)
TIMEZONE_DIAGNOSTIC_REASONS = frozenset({
    "http-status", "transport-error", "dav-envelope-invalid", "dav-scope-mismatch", "response-url-mismatch",
    "property-missing", "property-empty", "property-http-error", "timezone-definition-invalid",
    "timezone-id-invalid", "observance-missing", "observance-invalid", "observance-ambiguous",
    "rrule-invalid", "rrule-unsupported", "rrule-field-syntax", "rrule-temporal-overflow",
    "rrule-bymonth-list", "rrule-bymonth-invalid", "rrule-bymonth-range", "rrule-bymonthday-invalid",
    "rrule-until-local", "rrule-until-date", "rrule-until-invalid",
})
CALENDAR_TIMEZONE_PROPFIND = b"""<d:propfind xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">
 <d:prop><c:calendar-timezone/></d:prop>
</d:propfind>"""
AGENDA_RAW_REPORT = b"""<c:calendar-query xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">
 <d:prop><d:getetag/><c:calendar-data/></d:prop>
 <c:filter><c:comp-filter name="VCALENDAR"><c:comp-filter name="VEVENT"/></c:comp-filter></c:filter>
</c:calendar-query>"""
AGENDA_RESPONSE_MAX_BYTES = 4 * 1024 * 1024
AGENDA_RAW_MAX_OBJECTS = 1000
_PROVENANCE_INVALID = "calendar-fallback-provenance-invalid"
_SERIES_OMITTED = "calendar-fallback-series-omitted"
_RRULE_FREQUENCIES = frozenset({"SECONDLY", "MINUTELY", "HOURLY", "DAILY", "WEEKLY", "MONTHLY", "YEARLY"})
_SOURCE_PARSE_REASONS = frozenset({
    "invalid-event-properties", "invalid-event-time", "invalid-event-duration", "invalid-calendar-data",
    "invalid-recurrence-id", "ambiguous-duration-timezone", "event-outside-agenda", "floating-event-time",
    "invalid-temporal-value", "temporal-overflow", "unknown-tzid", "event-duration-zero-length",
    "event-duration-end-before-start", "event-duration-type-mismatch",
})
_DURATION = re.compile(
    r"P(?:(?P<weeks>[0-9]+)W|(?:(?P<days>[0-9]+)D)?"
    r"(?:T(?:(?P<hours>[0-9]+)H)?(?:(?P<minutes>[0-9]+)M)?(?:(?P<seconds>[0-9]+)S)?)?)"
)


def agenda_query(start: datetime, end: datetime, timezone_name: str) -> tuple[bytes, ZoneInfo]:
    try:
        zone = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        raise ValueError("Unbekannte Kalenderzeitzone") from None
    if start.utcoffset() is None or end.utcoffset() is None:
        raise ValueError("Kalendergrenzen benoetigen eine Zeitzone")
    day_start, day_end = start.astimezone(zone), end.astimezone(zone)
    start, end = start.astimezone(UTC), end.astimezone(UTC)
    if (
        day_start.time() != time.min
        or day_end.time() != time.min
        or day_end.date() - day_start.date() != timedelta(days=1)
        or not timedelta(hours=23) <= end - start <= timedelta(hours=25)
    ):
        raise ValueError("Kalenderabfrage benoetigt genau einen lokalen Tag")

    # Nextcloud's Sabre filter interprets floating/date values in the query's
    # UTC zone before expansion uses the measured calendar timezone.
    report_start, report_end = start - _REPORT_MARGIN, end + _REPORT_MARGIN
    bounds = f'start="{report_start.strftime(_UTC_STAMP)}" end="{report_end.strftime(_UTC_STAMP)}"'
    timezone = _timezone_definition(zone, report_start, report_end)
    request = f"""<c:calendar-query xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">
 <d:prop><d:getetag/><c:calendar-data><c:expand {bounds}/></c:calendar-data></d:prop>
 <c:filter><c:comp-filter name="VCALENDAR"><c:comp-filter name="VEVENT">
  <c:time-range {bounds}/>
 </c:comp-filter></c:comp-filter></c:filter>
 <c:timezone>{timezone}</c:timezone>
</c:calendar-query>"""
    return request.encode(), zone


def _offset_text(offset: timedelta) -> str:
    seconds = int(offset.total_seconds())
    sign = "+" if seconds >= 0 else "-"
    hours, remainder = divmod(abs(seconds), 3600)
    minutes, seconds = divmod(remainder, 60)
    suffix = f"{seconds:02d}" if seconds else ""
    return f"{sign}{hours:02d}{minutes:02d}{suffix}"


def _timezone_definition(zone: ZoneInfo, start: datetime, end: datetime) -> str:
    """Supply the configured offset and nearby transitions for floating and DATE matching."""
    cursor = start - timedelta(days=2)
    offset = cursor.astimezone(zone).utcoffset()
    assert offset is not None
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//OpenClaw//Calendar agenda//EN",
             "BEGIN:VTIMEZONE", f"TZID:{zone.key}"]

    def observance(instant: datetime, before: timedelta, after: timedelta) -> None:
        kind = "DAYLIGHT" if instant.astimezone(zone).dst() else "STANDARD"
        local = (instant + before).strftime(_LOCAL_STAMP)
        lines.extend((f"BEGIN:{kind}", f"DTSTART:{local}", f"TZOFFSETFROM:{_offset_text(before)}",
                      f"TZOFFSETTO:{_offset_text(after)}", f"END:{kind}"))

    observance(cursor, offset, offset)
    while cursor < end + timedelta(days=2):
        probe = cursor + timedelta(hours=6)
        next_offset = probe.astimezone(zone).utcoffset()
        assert next_offset is not None
        if offset != next_offset:
            low, high = cursor, probe
            while high - low > timedelta(seconds=1):
                middle = low + timedelta(seconds=int((high - low).total_seconds()) // 2)
                if middle.astimezone(zone).utcoffset() == offset:
                    low = middle
                else:
                    high = middle
            observance(high, offset, next_offset)
            offset = next_offset
        cursor = probe
    return "\n".join((*lines, "END:VTIMEZONE", "END:VCALENDAR"))


def matches_agenda_query(data: bytes, expansion: ET.Element, timezone: ET.Element | None) -> bool:
    if timezone is None or not timezone.text or set(expansion.attrib) != {"start", "end"}:
        return False
    match = re.search(r"^TZID:([A-Za-z0-9_+./-]+)$", timezone.text, re.MULTILINE)
    if match is None:
        return False
    try:
        start = datetime.strptime(expansion.attrib["start"], _UTC_STAMP).replace(tzinfo=UTC)
        end = datetime.strptime(expansion.attrib["end"], _UTC_STAMP).replace(tzinfo=UTC)
        canonical, _ = agenda_query(start + _REPORT_MARGIN, end - _REPORT_MARGIN, match[1])
    except (ValueError, OverflowError):
        return False
    return data == canonical


def calendar_timezone_property_evidence(data: bytes, url: str) -> tuple[str, str, int | None]:
    """Return the scoped property or a content-free rejection reason and propstat status."""
    invalid = ("", "dav-envelope-invalid", None)
    try:
        root = ET.fromstring(data)
        if root.tag != q(DAV, "multistatus") or root.attrib or len(root) != 1:
            return invalid
        response = root[0]
        hrefs, statuses = response.findall(q(DAV, "href")), response.findall(q(DAV, "propstat"))
        if response.tag != q(DAV, "response") or len(response) != 2 or len(hrefs) != 1 or len(statuses) != 1:
            return invalid
        path, requested = urlsplit(hrefs[0].text or ""), urlsplit(url)
        if (
            path.query or path.fragment or not path.path
            or unquote(path.path).rstrip("/") != unquote(requested.path).rstrip("/")
            or ((path.scheme or path.netloc)
                and (path.scheme, path.netloc) != (requested.scheme, requested.netloc))
        ):
            return "", "dav-scope-mismatch", None
        status = statuses[0]
        props = status.find(q(DAV, "prop"))
        match = _DAV_STATUS.fullmatch(status.findtext(q(DAV, "status")) or "")
        if props is None or len(status) != 2 or len(props) > 1 or match is None:
            return invalid
        property_status = int(match[1])
        if len(props) == 0:
            return "", "property-missing", property_status
        entry = props[0]
        if entry.tag != q(CALDAV, "calendar-timezone") or entry.attrib or len(entry):
            return invalid
        definition = (entry.text or "").strip()
        if property_status == 404 and not definition:
            return "", "property-missing", property_status
        if property_status != 200:
            return "", "property-http-error", property_status
        if not definition:
            return "", "property-empty", property_status
        return definition, "", property_status
    except (ET.ParseError, ValueError):
        return invalid


def calendar_timezone_property(data: bytes, url: str) -> str:
    definition, _, _ = calendar_timezone_property_evidence(data, url)
    return definition


def calendar_timezone_absence(data: bytes, url: str) -> bool:
    """Require an explicit property-level 404 on the exact requested collection."""
    if calendar_timezone_property_evidence(data, url) != ("", "property-missing", 404):
        return False
    root = ET.fromstring(data)
    response = root[0]
    href = response.find(q(DAV, "href"))
    propstat = response.find(q(DAV, "propstat"))
    assert href is not None and propstat is not None
    prop = propstat.find(q(DAV, "prop"))
    status = propstat.find(q(DAV, "status"))
    assert prop is not None and status is not None
    return (
        len(prop) == 1 and len(href) == len(status) == 0
        and not any(element.attrib for element in (response, href, propstat, prop, status))
        and not any((element.text or "").strip() for element in (root, response, propstat, prop))
        and not any((element.tail or "").strip() for element in root.iter())
    )


class _TimezoneParseIssue(ValueError):
    pass


def _offset_value(value: str) -> timedelta:
    if not re.fullmatch(r"[+-][0-9]{4}(?:[0-9]{2})?", value):
        raise ValueError("invalid-timezone-offset")
    hours, minutes, seconds = int(value[1:3]), int(value[3:5]), int(value[5:] or "0")
    if hours > 23 or minutes > 59 or seconds > 59:
        raise ValueError("invalid-timezone-offset")
    offset = timedelta(hours=hours, minutes=minutes, seconds=seconds)
    return offset if value[0] == "+" else -offset


def _yearly_onsets(
    value: str, first: datetime, before: timedelta, window: tuple[datetime, datetime],
) -> list[datetime]:
    try:
        parts = dict(entry.split("=", 1) for entry in value.upper().split(";"))
    except ValueError:
        raise _TimezoneParseIssue("rrule-field-syntax") from None
    if (
        parts.pop("FREQ", "") != "YEARLY" or parts.pop("INTERVAL", "1") != "1"
        or set(parts) - {"BYMONTH", "BYMONTHDAY", "BYDAY", "UNTIL"}
    ):
        raise _TimezoneParseIssue("rrule-unsupported")
    month_value = parts.get("BYMONTH", str(first.month))
    try:
        month = int(month_value)
    except ValueError:
        reason = "rrule-bymonth-list" if "," in month_value else "rrule-bymonth-invalid"
        raise _TimezoneParseIssue(reason) from None
    try:
        days = [int(item) for item in parts["BYMONTHDAY"].split(",")] if "BYMONTHDAY" in parts else []
    except ValueError:
        raise _TimezoneParseIssue("rrule-bymonthday-invalid") from None
    weekday_rule = re.fullmatch(r"([+-]?[1-5])?(MO|TU|WE|TH|FR|SA|SU)", parts.get("BYDAY", ""))
    if "BYDAY" in parts and weekday_rule is None:
        raise _TimezoneParseIssue("rrule-unsupported")
    until = None
    if "UNTIL" in parts:
        value = parts["UNTIL"]
        try:
            until = datetime.strptime(value, _UTC_STAMP).replace(tzinfo=UTC)
        except ValueError:
            if re.fullmatch(r"[0-9]{8}T[0-9]{6}", value):
                raise _TimezoneParseIssue("rrule-until-local") from None
            if re.fullmatch(r"[0-9]{8}", value):
                raise _TimezoneParseIssue("rrule-until-date") from None
            raise _TimezoneParseIssue("rrule-until-invalid") from None
    if not 1 <= month <= 12:
        raise _TimezoneParseIssue("rrule-bymonth-range")
    years = set(range(max(1, window[0].year - 1), window[1].year + 1))
    if until:
        years.update((max(1, until.year - 1), until.year))
    onsets: list[datetime] = []
    for year in sorted(years):
        _, month_days = monthrange(year, month)
        candidates = [first.replace(year=year, month=month, day=index) for index in range(1, month_days + 1)]
        if weekday_rule:
            weekday = ("MO", "TU", "WE", "TH", "FR", "SA", "SU").index(weekday_rule[2])
            candidates = [candidate for candidate in candidates if candidate.weekday() == weekday]
            ordinal = weekday_rule[1]
            if ordinal:
                index = int(ordinal)
                index = index - 1 if index > 0 else index
                candidates = [candidates[index]] if -len(candidates) <= index < len(candidates) else []
        if days:
            candidates = [candidate for candidate in candidates
                          if candidate.day in days or candidate.day - month_days - 1 in days]
        elif not weekday_rule:
            candidates = [candidate for candidate in candidates if candidate.day == first.day]
        onsets.extend(
            candidate for candidate in candidates
            if candidate >= first and (until is None or candidate.replace(tzinfo=UTC) - before <= until)
        )
    return onsets


def _observances(
    lines: list[str], window: tuple[datetime, datetime],
) -> list[tuple[datetime, timedelta, timedelta]]:
    definitions = sorted(_component_blocks(lines, "STANDARD") + _component_blocks(lines, "DAYLIGHT"))
    if not definitions:
        raise _TimezoneParseIssue("observance-missing")
    transitions: set[tuple[datetime, timedelta, timedelta]] = set()
    for first, last in definitions:
        properties: dict[str, list[str]] = {}
        for index in _direct_property_indices(lines, first, last):
            line = lines[index]
            properties.setdefault(property_name(line), []).append(line.partition(":")[2])
        keys = ("DTSTART", "TZOFFSETFROM", "TZOFFSETTO")
        if any(len(properties.get(key, [])) != 1 for key in keys) or len(properties.get("RRULE", [])) > 1:
            raise ValueError("invalid-timezone-observance")
        begins = datetime.strptime(properties["DTSTART"][0], _LOCAL_STAMP)
        before = _offset_value(properties["TZOFFSETFROM"][0])
        after = _offset_value(properties["TZOFFSETTO"][0])
        onsets = [begins]
        for dates in properties.get("RDATE", []):
            onsets.extend(datetime.strptime(value, _LOCAL_STAMP) for value in dates.split(","))
        if properties.get("RRULE"):
            try:
                onsets.extend(_yearly_onsets(properties["RRULE"][0], begins, before, window))
            except _TimezoneParseIssue:
                raise
            except OverflowError:
                raise _TimezoneParseIssue("rrule-temporal-overflow") from None
            except ValueError:
                raise _TimezoneParseIssue("rrule-invalid") from None
        transitions.update((item.replace(tzinfo=UTC) - before, before, after) for item in onsets)
    ordered = sorted(transitions)
    if len({item[0] for item in ordered}) != len(ordered):
        raise _TimezoneParseIssue("observance-ambiguous")
    return ordered


def agenda_timezone_error(value: str, zone: ZoneInfo, start: datetime, end: datetime) -> tuple[str, str]:
    try:
        lines = unfold_ical(value)
        blocks = _component_blocks(lines, "VTIMEZONE")
        if len(blocks) != 1 or _component_blocks(lines, "VCALENDAR") != [(0, len(lines) - 1)]:
            return "calendar-timezone-unverified", "timezone-definition-invalid"
        first, last = blocks[0]
        names = [lines[index][5:] for index in _direct_property_indices(lines, first, last)
                 if lines[index].upper().startswith("TZID:")]
        if len(names) != 1:
            return "calendar-timezone-unverified", "timezone-id-invalid"
        same_zone = names[0] == zone.key or names[0] in _UTC_NAMES and zone.key in _UTC_NAMES
        if not same_zone:
            return "calendar-timezone-mismatch", ""

        low = start.astimezone(UTC) - _REPORT_MARGIN
        high = end.astimezone(UTC) + _REPORT_MARGIN
        transitions = _observances(lines[first:last + 1], (low, high))
        # Sabre resolves known IANA names via PHP DateTimeZone, but also check
        # the advertised definition's effective observances over the padded day.
        probes = {low, high}
        for instant, _, _ in transitions:
            if low < instant <= high:
                probes.update((instant - timedelta(seconds=1), instant))
        instant = low
        while instant < high:
            probes.add(instant)
            instant += timedelta(minutes=15)
        current_offset = transitions[0][1]
        transition_iterator = iter(transitions)
        transition = next(transition_iterator, None)
        for instant in sorted(probes):
            while transition is not None and transition[0] <= instant:
                current_offset = transition[2]
                transition = next(transition_iterator, None)
            if instant.astimezone(zone).utcoffset() != current_offset:
                return "calendar-timezone-mismatch", ""
    except _TimezoneParseIssue as exc:
        return "calendar-timezone-unverified", str(exc)
    except (ValueError, OverflowError):
        return "calendar-timezone-unverified", "observance-invalid"
    return "", ""


def _event_wall_value(line: str) -> tuple[date | datetime, str | None]:
    header, _, value = line.partition(":")
    parameters: dict[str, str] = {}
    for assignment in header.split(";")[1:]:
        name, _, argument = assignment.partition("=")
        name = name.upper()
        if name not in {"VALUE", "TZID"} or name in parameters or not argument:
            raise ValueError("invalid-event-time")
        parameters[name] = argument.strip('"')
    declared_type = parameters.get("VALUE", "").upper()
    zone_name = parameters.get("TZID")
    if re.fullmatch(r"[0-9]{8}", value):
        if zone_name or declared_type not in {"", "DATE"}:
            raise ValueError("invalid-event-time")
        return datetime.strptime(value, "%Y%m%d").date(), None
    if declared_type not in {"", "DATE-TIME"} or not re.fullmatch(r"[0-9]{8}T[0-9]{6}Z?", value):
        raise ValueError("invalid-event-time")
    if value.endswith("Z"):
        if zone_name:
            raise ValueError("invalid-event-time")
        return datetime.strptime(value, _UTC_STAMP).replace(tzinfo=UTC), None
    return datetime.strptime(value, _LOCAL_STAMP), zone_name


def _event_time(line: str, zone: ZoneInfo | None) -> date | datetime:
    wall, zone_name = _event_wall_value(line)
    if not isinstance(wall, datetime) or wall.tzinfo is not None:
        return wall
    target = ZoneInfo(zone_name) if zone_name else zone
    if target is None:
        raise ValueError("floating-event-time")
    return wall.replace(tzinfo=target, fold=0)


def _duration_parts(value: str) -> tuple[timedelta, timedelta]:
    match = _DURATION.fullmatch(value)
    if not match or value.endswith("T"):
        raise ValueError("invalid-event-duration")
    units = {key: int(amount or "0") for key, amount in match.groupdict().items()}
    return (timedelta(weeks=units["weeks"], days=units["days"]),
            timedelta(hours=units["hours"], minutes=units["minutes"], seconds=units["seconds"]))


def _end_from_duration(start: date | datetime, value: str) -> date | datetime:
    calendar_days, elapsed = _duration_parts(value)
    if not (calendar_days or elapsed):
        raise ValueError("event-duration-zero-length")
    if elapsed and not isinstance(start, datetime):
        raise ValueError("event-duration-type-mismatch")
    if calendar_days and isinstance(start, datetime) and start.tzinfo is UTC:
        # Expansion can leave a nominal day/week duration after stripping TZID.
        raise ValueError("ambiguous-duration-timezone")
    end = start + calendar_days
    if isinstance(end, datetime) and elapsed:
        return (end.astimezone(UTC) + elapsed).astimezone(end.tzinfo)
    return end


def _instant(value: date | datetime, zone: ZoneInfo) -> datetime:
    timed = value if isinstance(value, datetime) else datetime.combine(value, time.min, zone)
    return timed.astimezone(UTC)


def _display_time(value: date | datetime, zone: ZoneInfo) -> str:
    return (value.astimezone(zone) if isinstance(value, datetime) else value).isoformat()


def _overlaps(first: datetime, last: datetime, start: datetime, end: datetime) -> bool:
    return start <= first < end if first == last else first < end and start < last


def _agenda_event(
    lines: list[str], zone: ZoneInfo, start: datetime, end: datetime, *, raw_single: bool = False,
) -> dict[str, Any] | None:
    fields: dict[str, str] = {}
    used_fields = {"UID", "DTSTART", "DTEND", "DURATION", "RECURRENCE-ID", "SUMMARY",
                   "DESCRIPTION", "LOCATION", "STATUS"}
    for line in lines:
        name = property_name(line)
        if name in _RECURRENCE_PROPERTIES or (name in used_fields and name in fields):
            raise ValueError("invalid-event-properties")
        if name in used_fields:
            fields[name] = line

    def text(name: str, default: str = "") -> str:
        return unescape_ical(fields[name].partition(":")[2]) if name in fields else default

    uid = text("UID").strip()
    if not uid or "DTSTART" not in fields or {"DTEND", "DURATION"} <= fields.keys():
        raise ValueError("invalid-event-properties")
    begins = _event_time(fields["DTSTART"], zone)
    all_day = not isinstance(begins, datetime)
    if "DTEND" in fields:
        finishes = _event_time(fields["DTEND"], zone)
    elif "DURATION" in fields:
        finishes = _end_from_duration(begins, text("DURATION"))
    else:
        finishes = begins + timedelta(days=1) if all_day else begins
    if isinstance(finishes, datetime) == all_day:
        raise ValueError("event-duration-type-mismatch")
    first, last = _instant(begins, zone), _instant(finishes, zone)
    if last < first:
        raise ValueError("event-duration-end-before-start")
    if last == first and "DTEND" in fields:
        raise ValueError("event-duration-zero-length")
    if not _overlaps(first, last, start, end):
        if raw_single:
            return None
        raise ValueError("event-outside-agenda")

    recurrence_id = ""
    if "RECURRENCE-ID" in fields:
        recurrence = _event_time(fields["RECURRENCE-ID"], zone)
        if isinstance(recurrence, datetime) == all_day:
            raise ValueError("invalid-recurrence-id")
        recurrence_id = _display_time(recurrence, zone)
    return {
        "uid": uid, "title": text("SUMMARY"), "start": _display_time(begins, zone),
        "end": _display_time(finishes, zone), "description": text("DESCRIPTION"),
        "location": text("LOCATION"), "status": text("STATUS", "CONFIRMED").upper(),
        "recurring": bool(recurrence_id), "all_day": all_day, "recurrence_id": recurrence_id,
    }


def _agenda_components(raw: str, *, allow_timezones: bool = False) -> tuple[list[list[str]], bool]:
    lines = unfold_ical(raw)
    if _component_blocks(lines, "VCALENDAR") != [(0, len(lines) - 1)]:
        raise ValueError("invalid-calendar-data")
    events: list[list[str]] = []
    cursor = 1
    event_blocks = _component_blocks(lines, "VEVENT")
    zone_blocks = _component_blocks(lines, "VTIMEZONE") if allow_timezones else []
    for first, last in sorted(event_blocks + zone_blocks):
        if first < cursor or any(line.upper().startswith(("BEGIN:", "END:")) for line in lines[cursor:first]):
            raise ValueError("invalid-calendar-data")
        if lines[first].upper() == "BEGIN:VEVENT":
            if any(line.upper().startswith("BEGIN:") and line.upper() != "BEGIN:VALARM"
                   for line in lines[first + 1:last]):
                raise ValueError("invalid-calendar-data")
            events.append([lines[index] for index in _direct_property_indices(lines, first, last)])
        cursor = last + 1
    if not events or any(line.upper().startswith(("BEGIN:", "END:")) for line in lines[cursor:-1]):
        raise ValueError("invalid-calendar-data")
    return events, bool(zone_blocks)


def _expanded_events(raw: str, zone: ZoneInfo, start: datetime, end: datetime) -> list[dict[str, Any]]:
    components, _ = _agenda_components(raw)
    events = []
    for properties in components:
        event = _agenda_event(properties, zone, start, end)
        assert event is not None
        events.append(event)
    recurrence_ids = {event["recurrence_id"] for event in events}
    if len({event["uid"] for event in events}) != 1 or len(recurrence_ids) != len(events):
        raise ValueError("invalid-recurrence-id")
    if any(recurrence_ids):
        for event in events:
            event["recurring"] = True
    return events


def _row_calendar_data(row: ET.Element, request_url: str) -> tuple[str, str, str]:
    hrefs = row.findall(q(DAV, "href"))
    statuses = row.findall(q(DAV, "propstat"))
    if row.tag != q(DAV, "response") or len(hrefs) != 1 or not statuses or len(row) != len(statuses) + 1:
        raise ValueError("invalid-calendar-response")
    href = urlsplit(hrefs[0].text or "")
    parent = urlsplit(request_url)
    path = unquote(href.path)
    prefix = unquote(parent.path).rstrip("/") + "/"
    name = path.removeprefix(prefix)
    if (
        href.query or href.fragment or not path.startswith(prefix) or not name
        or "/" in name or "\\" in name or name in {".", ".."}
        or ((href.scheme or href.netloc) and (href.scheme, href.netloc) != (parent.scheme, parent.netloc))
    ):
        raise ValueError("invalid-calendar-response")

    fields = {}
    etag, payload = q(DAV, "getetag"), q(CALDAV, "calendar-data")
    for status in statuses:
        props = status.find(q(DAV, "prop"))
        code = status.findtext(q(DAV, "status")) or ""
        if props is None or len(status) != 2 or not _DAV_SUCCESS.fullmatch(code):
            raise ValueError("incomplete-calendar-response")
        for item in props:
            value = item.text or ""
            if item.tag not in {etag, payload} or item.tag in fields or len(item) or not value.strip():
                raise ValueError("invalid-calendar-response")
            fields[item.tag] = value
    if fields.keys() != {etag, payload}:
        raise ValueError("incomplete-calendar-response")
    return path, fields[etag], fields[payload]


class AgendaFallbackError(ValueError):
    """A content-free rejection of a property-404 agenda read."""


@dataclass(frozen=True, slots=True)
class _AgendaObject:
    etag: str
    uid: str
    raw: str
    components: list[list[str]]
    has_timezones: bool


def parse_agenda_provenance(data: bytes, url: str, *, expanded: bool = False) -> dict[str, _AgendaObject]:
    if len(data) > AGENDA_RESPONSE_MAX_BYTES:
        raise AgendaFallbackError("calendar-fallback-provenance-limit")
    invalid = "calendar-fallback-expansion-unbound" if expanded else "calendar-fallback-provenance-invalid"
    try:
        root = ET.fromstring(data)
        if root.tag != q(DAV, "multistatus") or root.attrib or (root.text or "").strip():
            raise ValueError
        if len(root) > AGENDA_RAW_MAX_OBJECTS:
            raise AgendaFallbackError("calendar-fallback-provenance-limit")
        objects: dict[str, _AgendaObject] = {}
        uids: set[str] = set()
        for row in root:
            path, etag, raw = _row_calendar_data(row, url)
            if path in objects or not re.fullmatch(r'"[\x21\x23-\x7e\x80-\xff]+"', etag):
                raise ValueError
            components, has_timezones = _agenda_components(raw, allow_timezones=not expanded)
            event_uids = [
                [unescape_ical(line.partition(":")[2]).strip()
                 for line in part if property_name(line) == "UID"]
                for part in components
            ]
            if any(len(uid) != 1 or not uid[0] for uid in event_uids):
                raise ValueError
            uid = event_uids[0][0]
            if uid in uids or any(item[0] != uid for item in event_uids):
                raise ValueError
            uids.add(uid)
            objects[path] = _AgendaObject(etag, uid, raw, components, has_timezones)
        return objects
    except AgendaFallbackError:
        raise
    except (ET.ParseError, ValueError, OverflowError):
        raise AgendaFallbackError(invalid) from None


def _requires_expansion(source: _AgendaObject) -> bool:
    return len(source.components) != 1 or any(
        property_name(line) in _RECURRENCE_PROPERTIES | {"RECURRENCE-ID"}
        for line in source.components[0]
    )


_TEMPORAL_FIELDS = frozenset({"DTSTART", "DTEND", "RECURRENCE-ID", "RDATE", "EXDATE"})
_RRULE_RANGES = {
    "COUNT": (1, 2**31 - 1), "INTERVAL": (1, 2**31 - 1), "BYSECOND": (0, 59), "BYMINUTE": (0, 59),
    "BYHOUR": (0, 23), "BYMONTHDAY": (-31, 31), "BYYEARDAY": (-366, 366), "BYWEEKNO": (-53, 53),
    "BYMONTH": (1, 12), "BYSETPOS": (-366, 366),
}
_WEEKDAY = r"(?:MO|TU|WE|TH|FR|SA|SU)"


def _object_timezone_issue(source: _AgendaObject, start: datetime, end: datetime) -> str:
    if not source.has_timezones:
        return ""
    names = set()
    for component in source.components:
        for line in component:
            if property_name(line) in _TEMPORAL_FIELDS:
                match = re.search(r';TZID=(?:"([^"]+)"|([^;]+))(?:;|$)', line.partition(":")[0], re.I)
                if match:
                    names.add(match[1] or match[2])
    for name in sorted(names):
        try:
            code, reason = agenda_timezone_error(source.raw, ZoneInfo(name), start, end)
        except ZoneInfoNotFoundError:
            return "unknown-tzid"
        if code:
            return reason or "timezone-mismatch"
    return ""


def _rule_independent(line: str, *, all_day: bool) -> bool:
    header, _, value = line.partition(":")
    tokens = [token.split("=", 1) for token in value.upper().split(";")]
    rule = dict(tokens)
    freq = rule.get("FREQ", "")
    if (header.upper() != "RRULE" or len(rule) != len(tokens) or not all(rule.values())
            or rule.keys() - (_RRULE_RANGES.keys() | {"FREQ", "UNTIL", "WKST", "BYDAY"})
            or freq not in _RRULE_FREQUENCIES
            or {"COUNT", "UNTIL"} <= rule.keys()
            or (all_day and (freq in {"SECONDLY", "MINUTELY", "HOURLY"}
                             or rule.keys() & {"BYSECOND", "BYMINUTE", "BYHOUR"}))
            or (freq != "YEARLY" and "BYWEEKNO" in rule)
            or (freq == "WEEKLY" and "BYMONTHDAY" in rule)
            or (freq in {"DAILY", "WEEKLY", "MONTHLY"} and "BYYEARDAY" in rule)
            or ("BYSETPOS" in rule and not any(name.startswith("BY") and name != "BYSETPOS"
                                              for name in rule))):
        return False
    if "UNTIL" in rule and isinstance(_event_time(f"UNTIL:{rule['UNTIL']}", None), datetime) == all_day:
        return False
    if "WKST" in rule and not re.fullmatch(_WEEKDAY, rule["WKST"]):
        return False
    for weekday in rule.get("BYDAY", "").split(",") if "BYDAY" in rule else ():
        match = re.fullmatch(rf"([+-]?[0-9]{{1,2}})?{_WEEKDAY}", weekday)
        if not match or (match[1] is not None and (
            not 1 <= abs(int(match[1])) <= 53 or freq not in {"MONTHLY", "YEARLY"} or "BYWEEKNO" in rule
        )):
            return False
    for name, (low, high) in _RRULE_RANGES.items():
        if name not in rule:
            continue
        values = rule[name].split(",")
        if name in {"COUNT", "INTERVAL"} and len(values) != 1:
            return False
        for value in values:
            if not re.fullmatch(r"[+-]?[0-9]{1,10}", value):
                return False
            number = int(value)
            if not low <= number <= high or (low < 0 and number == 0):
                return False
    return True


def _expansion_day_type(source: _AgendaObject) -> bool | None:
    masters = 0
    day_type: bool | None = None
    overrides: set[date | datetime] = set()
    try:
        for component in source.components:
            names = [property_name(line) for line in component]
            if names.count("DTSTART") != 1 or any(names.count(name) > 1 for name in (
                "DTEND", "DURATION", "RECURRENCE-ID", "RRULE",
            )) or "EXRULE" in names or {"DTEND", "DURATION"} <= set(names):
                return None
            masters += int("RECURRENCE-ID" not in names)
            start = _event_time(component[names.index("DTSTART")], None)
            all_day = not isinstance(start, datetime)
            if day_type is not None and all_day != day_type:
                return None
            day_type = all_day
            for name, line in zip(names, component, strict=True):
                header, _, value = line.partition(":")
                if name in {"DTEND", "RECURRENCE-ID", "RDATE", "EXDATE"}:
                    for item in value.split(",") if name in {"RDATE", "EXDATE"} else (value,):
                        parsed = _event_time(f"{header}:{item}", None)
                        if isinstance(parsed, datetime) == all_day:
                            return None
                        if name == "DTEND" and parsed <= start:
                            return None
                        if name == "RECURRENCE-ID":
                            key = parsed.astimezone(UTC) if isinstance(parsed, datetime) else parsed
                            if key in overrides:
                                return None
                            overrides.add(key)
                elif name == "DURATION":
                    _end_from_duration(start, value)
                elif name == "RRULE" and (
                    "RECURRENCE-ID" in names or not _rule_independent(line, all_day=all_day)
                ):
                    return None
        return day_type if masters == 1 else None
    except (ValueError, OverflowError, ZoneInfoNotFoundError):
        return None


def _normalized_expansion(source: _AgendaObject, *, all_day: bool) -> bool:
    try:
        for component in source.components:
            for line in component:
                if property_name(line) not in _TEMPORAL_FIELDS:
                    continue
                value = _event_time(line, None)
                if isinstance(value, datetime) == all_day or (not all_day and not line.endswith("Z")):
                    return False
        return True
    except (ValueError, OverflowError, ZoneInfoNotFoundError):
        return False


_MAX_UTC_OFFSET = timedelta(days=1)
_MAX_YEARLY_CANDIDATE_DAYS = 64


def _utc_envelope(wall: date | datetime) -> tuple[datetime, datetime]:
    """Enclose every possible instant of a wall value without using its TZID."""
    if isinstance(wall, datetime):
        if wall.tzinfo is UTC:
            return wall, wall
        neutral = wall.replace(tzinfo=UTC)
    else:
        neutral = datetime.combine(wall, time.min, UTC)
    return neutral - _MAX_UTC_OFFSET, neutral + _MAX_UTC_OFFSET


def _embedded_offsets_bounded(source: _AgendaObject) -> bool:
    if not source.has_timezones:
        return True
    lines = unfold_ical(source.raw)
    try:
        for first, last in _component_blocks(lines, "VTIMEZONE"):
            definition = lines[first:last + 1]
            blocks = _component_blocks(definition, "STANDARD") + _component_blocks(definition, "DAYLIGHT")
            if not blocks or any(
                line.upper().startswith("BEGIN:") and line.upper() not in {"BEGIN:STANDARD", "BEGIN:DAYLIGHT"}
                for line in definition[1:-1]
            ):
                return False
            for first_observance, last_observance in blocks:
                properties = [definition[index] for index in _direct_property_indices(
                    definition, first_observance, last_observance,
                )]
                for name in ("TZOFFSETFROM", "TZOFFSETTO"):
                    offsets = [line.partition(":")[2] for line in properties if property_name(line) == name]
                    if len(offsets) != 1:
                        return False
                    _offset_value(offsets[0])
        return True
    except ValueError:
        return False


def _raw_singleton_outside_day(component: list[str], start: datetime, end: datetime) -> bool:
    fields = {}
    for line in component:
        name = property_name(line)
        if name in {"DTSTART", "DTEND", "DURATION"}:
            if name in fields:
                return False
            fields[name] = line
    if "DTSTART" not in fields or {"DTEND", "DURATION"} <= fields.keys():
        return False
    try:
        begins, _ = _event_wall_value(fields["DTSTART"])
        begin_low, begin_high = _utc_envelope(begins)
        if "DTEND" in fields:
            finishes, _ = _event_wall_value(fields["DTEND"])
            end_low, end_high = _utc_envelope(finishes)
        elif "DURATION" in fields:
            days, elapsed = _duration_parts(fields["DURATION"].partition(":")[2])
            if elapsed and not isinstance(begins, datetime):
                return False
            end_low, end_high = _utc_envelope(begins + days)
            end_low, end_high = end_low + elapsed, end_high + elapsed
        else:
            finishes = begins if isinstance(begins, datetime) else begins + timedelta(days=1)
            end_low, end_high = _utc_envelope(finishes)
        return max(begin_high, end_high) < start or min(begin_low, end_low) >= end
    except (ValueError, OverflowError):
        return False


def _yearly_date_may_match(candidate: date, seed: date, rule: dict[str, str]) -> bool:
    date_selectors = {"BYDAY", "BYMONTHDAY", "BYYEARDAY", "BYWEEKNO"} & rule.keys()
    if "BYMONTH" in rule:
        if candidate.month not in {int(month) for month in rule["BYMONTH"].split(",")}:
            return False
    elif not date_selectors and candidate.month != seed.month:
        return False
    if "BYMONTHDAY" in rule:
        month_days = monthrange(candidate.year, candidate.month)[1]
        possible_days = {candidate.day, candidate.day - month_days - 1}
        if not possible_days & {int(value) for value in rule["BYMONTHDAY"].split(",")}:
            return False
    elif not date_selectors and candidate.day != seed.day:
        return False
    if "BYDAY" in rule:
        weekday = ("MO", "TU", "WE", "TH", "FR", "SA", "SU")[candidate.weekday()]
        if weekday not in {value[-2:] for value in rule["BYDAY"].split(",")}:
            return False
    # Ordinals, year/week selectors, COUNT/UNTIL, and time-of-day selectors
    # can narrow the possible dates further. Deliberately ignore them here.
    return True


def _upper_duration(first: date | datetime, fields: dict[str, str]) -> timedelta:
    first_low, first_high = _utc_envelope(first)
    if "DTEND" in fields:
        first_end, _ = _event_wall_value(fields["DTEND"])
        _, end_high = _utc_envelope(first_end)
        return end_high - first_low
    if "DURATION" in fields:
        days, elapsed = _duration_parts(fields["DURATION"].partition(":")[2])
        uncertainty = 2 * _MAX_UTC_OFFSET if days and first_low != first_high else timedelta()
        return days + elapsed + uncertainty
    return timedelta() if isinstance(first, datetime) else timedelta(days=3)


def _inherited_override_may_overlap(source: _AgendaObject, start: datetime, end: datetime) -> bool:
    parts = [{property_name(line): line for line in component} for component in source.components]
    overrides = [fields for fields in parts if "RECURRENCE-ID" in fields and "DTEND" not in fields]
    if not overrides:
        return False
    master = next(fields for fields in parts if "RECURRENCE-ID" not in fields)
    inherited = _upper_duration(_event_wall_value(master["DTSTART"])[0], master)
    if not inherited:
        return False
    for override in overrides:
        first, _ = _event_wall_value(override["DTSTART"])
        low, high = _utc_envelope(first)
        if low < end and high + inherited >= start:
            return True
    return False


def _has_mixed_recurrence_sources(source: _AgendaObject) -> bool:
    # Nextcloud's Sabre iterator can prefer RDATE over RRULE instead of
    # expanding the union. Returned rows then establish only a subset.
    return any({"RRULE", "RDATE"} <= {property_name(line) for line in part} for part in source.components)


def _yearly_series_outside_day(source: _AgendaObject, start: datetime, end: datetime) -> bool:
    """Reject dates excluded by a superset of possible yearly starts, not generated instances."""
    parts = [{property_name(line): line for line in component} for component in source.components]
    master = next(fields for fields in parts if "RECURRENCE-ID" not in fields)
    master_duration = _upper_duration(_event_wall_value(master["DTSTART"])[0], master)
    yearly = False
    for component, fields in zip(source.components, parts, strict=True):
        first, _ = _event_wall_value(fields["DTSTART"])
        duration = _upper_duration(first, fields)
        if "RECURRENCE-ID" in fields and "DTEND" not in fields:
            # Sabre can retain the master's duration when a moved occurrence
            # does not carry DTEND, including when it carries DURATION.
            duration = max(duration, master_duration)

        starts = [first]
        for line in component:
            if property_name(line) == "RDATE":
                header, _, values = line.partition(":")
                starts.extend(_event_wall_value(f"{header}:{value}")[0] for value in values.split(","))
        for value in starts:
            low, high = _utc_envelope(value)
            if low < end and high + duration >= start:
                return False

        if "RRULE" not in fields:
            continue
        rule = dict(part.split("=", 1) for part in fields["RRULE"].partition(":")[2].upper().split(";"))
        if rule["FREQ"] != "YEARLY":
            return False
        yearly = True
        seed = first.date() if isinstance(first, datetime) else first
        earliest = (start - duration - _MAX_UTC_OFFSET).date()
        latest = (end + _MAX_UTC_OFFSET).date()
        span = (latest - earliest).days + 1
        if span > _MAX_YEARLY_CANDIDATE_DAYS or any(
            _yearly_date_may_match(earliest + timedelta(days=offset), seed, rule) for offset in range(span)
        ):
            return False
    return yearly


def _series_outside_day(source: _AgendaObject, zone: ZoneInfo, start: datetime, end: datetime) -> bool:
    """Prove nonoverlap for simple temporal bounds without generating any recurrence set."""
    for component in source.components:
        fields = {property_name(line): line for line in component}
        first = _event_time(fields["DTSTART"], None)
        dates = [first]
        for line in component:
            if property_name(line) == "RDATE":
                header, _, dates_text = line.partition(":")
                dates.extend(_event_time(f"{header}:{value}", None) for value in dates_text.split(","))
        # Explicit TZID series are usable when the server returns their instances.
        # Keep absent series incomplete when local date arithmetic is not exact.
        if any(isinstance(item, datetime) and item.tzinfo is not UTC for item in dates):
            return False
        if all(_instant(value, zone) >= end for value in dates):
            continue
        if "RECURRENCE-ID" in fields and "DTEND" not in fields:
            return False

        latest = max(dates)
        if "RRULE" in fields:
            rule = dict(part.split("=", 1) for part in fields["RRULE"].partition(":")[2].upper().split(";"))
            if "UNTIL" in rule:
                latest = max(latest, _event_time(f"UNTIL:{rule['UNTIL']}", None))
            elif ("COUNT" in rule and rule["FREQ"] in {"DAILY", "WEEKLY"}
                  and rule.keys() <= {"FREQ", "COUNT", "INTERVAL", "WKST"}):
                days = (int(rule["COUNT"]) - 1) * int(rule.get("INTERVAL", "1"))
                latest = max(latest, first + timedelta(days=days * (7 if rule["FREQ"] == "WEEKLY" else 1)))
            else:
                return False
        if "DTEND" in fields:
            first_end = _event_time(fields["DTEND"], None)
            if isinstance(first_end, datetime) and first_end.tzinfo is not UTC:
                return False
        elif "DURATION" in fields:
            first_end = _end_from_duration(first, fields["DURATION"].partition(":")[2])
        else:
            first_end = first if isinstance(first, datetime) else first + timedelta(days=1)
        if _instant(latest + (first_end - first), zone) >= start:
            return False
    return True


def _omitted_series_diagnostic(source: _AgendaObject) -> str:
    master = next(
        part for part in source.components if all(property_name(line) != "RECURRENCE-ID" for line in part)
    )
    timestamp = _event_time(next(line for line in master if property_name(line) == "DTSTART"), None)
    basis = ("UTC" if timestamp.tzinfo is UTC else "TZID") if isinstance(timestamp, datetime) else "DATE"
    rule = next((line.partition(":")[2].upper() for line in master if property_name(line) == "RRULE"), "")
    fields = dict(part.split("=", 1) for part in rule.split(";") if part)
    bound = "UNTIL" if "UNTIL" in fields else "COUNT" if "COUNT" in fields else "NONE"
    return f"omitted-series/{fields.get('FREQ', 'NONE')}/{basis}/{bound}"


def _source_parse_reason(exc: Exception) -> str:
    if isinstance(exc, ZoneInfoNotFoundError):
        return "unknown-tzid"
    if isinstance(exc, OverflowError):
        return "temporal-overflow"
    reason = str(exc)
    return reason if reason in _SOURCE_PARSE_REASONS else "invalid-temporal-value"


def project_fallback_diagnostic_counts(code: str, counts: object) -> dict[str, int]:
    """Bound and revalidate content-free diagnostics before crossing the tool boundary."""
    def allowed(key: str) -> bool:
        stage, _, tail = key.partition("/")
        if code == _PROVENANCE_INVALID:
            if stage == "embedded-timezone":
                return tail in TIMEZONE_DIAGNOSTIC_REASONS | {"timezone-mismatch", "unknown-tzid"}
            return (stage in {"raw-singleton", "expanded-instance", "series-bound"}
                    and tail in _SOURCE_PARSE_REASONS)
        if code == _SERIES_OMITTED and stage == "omitted-series":
            parts = tail.split("/")
            return (len(parts) == 3 and parts[0] in _RRULE_FREQUENCIES | {"NONE"}
                    and parts[1] in {"UTC", "TZID", "DATE"} and parts[2] in {"UNTIL", "COUNT", "NONE"})
        return False

    if not isinstance(counts, dict):
        return {}
    validated = {
        key: count for key, count in counts.items()
        if isinstance(key, str) and len(key) <= 72 and type(count) is int
        and 1 <= count <= AGENDA_RAW_MAX_OBJECTS and allowed(key)
    }
    return dict(sorted(validated.items(), key=lambda item: (-item[1], item[0]))[:64])


def project_agenda_fallback(
    before: dict[str, _AgendaObject], expanded: dict[str, _AgendaObject], after: dict[str, _AgendaObject],
    *, zone: ZoneInfo, start: datetime, end: datetime,
) -> dict[str, Any]:
    before_keys = {path: (source.etag, source.uid) for path, source in before.items()}
    if before != after:
        raise AgendaFallbackError("calendar-fallback-snapshot-changed")
    if any(before_keys.get(path) != (source.etag, source.uid) for path, source in expanded.items()):
        raise AgendaFallbackError("calendar-fallback-expansion-unbound")

    start, end = start.astimezone(UTC), end.astimezone(UTC)
    events: list[dict[str, Any]] = []
    codes: set[str] = set()
    diagnostics: dict[str, Counter[str]] = {_PROVENANCE_INVALID: Counter(), _SERIES_OMITTED: Counter()}

    def instant(event: dict[str, Any], key: str) -> datetime:
        parse = date.fromisoformat if event["all_day"] else datetime.fromisoformat
        return _instant(parse(event[key]), zone)

    for path, source in before.items():
        stage = "embedded-timezone"
        try:
            requires_expansion = _requires_expansion(source)
            if (not requires_expansion and _raw_singleton_outside_day(source.components[0], start, end)
                    and _embedded_offsets_bounded(source)):
                continue
            timezone_issue = _object_timezone_issue(source, start, end)
            if timezone_issue:
                codes.add(_PROVENANCE_INVALID)
                diagnostics[_PROVENANCE_INVALID][f"{stage}/{timezone_issue}"] += 1
                continue
            if not requires_expansion:
                stage = "raw-singleton"
                event = _agenda_event(source.components[0], zone, start, end, raw_single=True)
                if event is not None:
                    events.append(event)
            else:
                stage = "series-bound"
                day_type = _expansion_day_type(source)
                if day_type is None:
                    codes.add("calendar-timezone-dependent-series")
                elif path in expanded:
                    stage = "expanded-instance"
                    if (not _normalized_expansion(expanded[path], all_day=day_type)
                            or _has_mixed_recurrence_sources(source)
                            or _inherited_override_may_overlap(source, start, end)):
                        codes.add("calendar-fallback-expansion-unbound")
                        continue
                    instances = _expanded_events(
                        expanded[path].raw, zone, start - _REPORT_MARGIN, end + _REPORT_MARGIN,
                    )
                    events.extend(
                        event for event in instances
                        if _overlaps(instant(event, "start"), instant(event, "end"), start, end)
                    )
                elif not (_series_outside_day(source, zone, start, end)
                          or _yearly_series_outside_day(source, start, end)):
                    codes.add(_SERIES_OMITTED)
                    diagnostics[_SERIES_OMITTED][_omitted_series_diagnostic(source)] += 1
        except (ValueError, OverflowError, ZoneInfoNotFoundError) as exc:
            codes.add(_PROVENANCE_INVALID)
            diagnostics[_PROVENANCE_INVALID][f"{stage}/{_source_parse_reason(exc)}"] += 1
    events.sort(key=lambda event: (instant(event, "start"), event["uid"], event["recurrence_id"]))
    errors = []
    for code in sorted(codes):
        counts = project_fallback_diagnostic_counts(code, diagnostics.get(code))
        errors.append({"code": code, **({"diagnostic_counts": counts} if counts else {})})
    return {"complete": not codes, "events": events, "errors": errors}


def parse_agenda(
    data: bytes, request_url: str, *, start: datetime, end: datetime, zone: ZoneInfo,
) -> dict[str, Any]:
    errors: list[dict[str, str]] = []
    events: list[dict[str, Any]] = []
    try:
        response = ET.fromstring(data)
    except ET.ParseError:
        return {"complete": False, "events": [], "errors": [{"code": "invalid-calendar-response"}]}
    if response.tag != q(DAV, "multistatus") or response.attrib or (response.text or "").strip():
        return {"complete": False, "events": [], "errors": [{"code": "invalid-calendar-response"}]}

    start, end = start.astimezone(UTC), end.astimezone(UTC)
    seen: set[str] = set()
    uids: set[str] = set()

    def event_instant(event: dict[str, Any], key: str) -> datetime:
        parse = date.fromisoformat if event["all_day"] else datetime.fromisoformat
        return _instant(parse(event[key]), zone)

    for row in response:
        try:
            path, _, raw = _row_calendar_data(row, request_url)
            if path in seen:
                raise ValueError("duplicate-calendar-response")
            seen.add(path)
            instances = _expanded_events(raw, zone, start - _REPORT_MARGIN, end + _REPORT_MARGIN)
            uid = instances[0]["uid"]
            if uid in uids:
                raise ValueError("duplicate-calendar-uid")
            uids.add(uid)
            events.extend(
                event for event in instances
                if _overlaps(event_instant(event, "start"), event_instant(event, "end"), start, end)
            )
        except (ValueError, OverflowError, ZoneInfoNotFoundError):
            errors.append({"code": "incomplete-calendar-response"})
    events.sort(key=lambda event: (event_instant(event, "start"), event["uid"], event["recurrence_id"]))
    return {"complete": not errors, "events": events, "errors": errors}
