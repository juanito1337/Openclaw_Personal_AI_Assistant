"""Bounded CalDAV agenda queries and validation of their expanded results."""

from __future__ import annotations

import re
from calendar import monthrange
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
    "http-status", "transport-error", "dav-envelope-invalid", "dav-scope-mismatch",
    "property-missing", "property-empty", "property-http-error", "timezone-definition-invalid",
    "timezone-id-invalid", "observance-missing", "observance-invalid", "observance-ambiguous",
    "rrule-invalid", "rrule-unsupported",
})
CALENDAR_TIMEZONE_PROPFIND = b"""<d:propfind xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">
 <d:prop><c:calendar-timezone/></d:prop>
</d:propfind>"""
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
            or (path.netloc and (path.scheme, path.netloc) != (requested.scheme, requested.netloc))
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
    parts = dict(entry.split("=", 1) for entry in value.upper().split(";"))
    if (
        parts.pop("FREQ", "") != "YEARLY" or parts.pop("INTERVAL", "1") != "1"
        or set(parts) - {"BYMONTH", "BYMONTHDAY", "BYDAY", "UNTIL"}
    ):
        raise _TimezoneParseIssue("rrule-unsupported")
    month = int(parts.get("BYMONTH", str(first.month)))
    days = [int(item) for item in parts["BYMONTHDAY"].split(",")] if "BYMONTHDAY" in parts else []
    weekday_rule = re.fullmatch(r"([+-]?[1-5])?(MO|TU|WE|TH|FR|SA|SU)", parts.get("BYDAY", ""))
    if "BYDAY" in parts and weekday_rule is None:
        raise _TimezoneParseIssue("rrule-unsupported")
    until = datetime.strptime(parts["UNTIL"], _UTC_STAMP).replace(tzinfo=UTC) if "UNTIL" in parts else None
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
            except (ValueError, OverflowError):
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


def _event_time(line: str, zone: ZoneInfo) -> date | datetime:
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
        return datetime.strptime(value, "%Y%m%d").date()
    if declared_type not in {"", "DATE-TIME"} or not re.fullmatch(r"[0-9]{8}T[0-9]{6}Z?", value):
        raise ValueError("invalid-event-time")
    if value.endswith("Z"):
        if zone_name:
            raise ValueError("invalid-event-time")
        return datetime.strptime(value, _UTC_STAMP).replace(tzinfo=UTC)
    target = ZoneInfo(zone_name) if zone_name else zone
    local = datetime.strptime(value, _LOCAL_STAMP)
    return local.replace(tzinfo=target, fold=0)


def _end_from_duration(start: date | datetime, value: str) -> date | datetime:
    match = _DURATION.fullmatch(value)
    if not match or value.endswith("T"):
        raise ValueError("invalid-event-duration")
    units = {key: int(amount or "0") for key, amount in match.groupdict().items()}
    calendar_days = timedelta(weeks=units["weeks"], days=units["days"])
    elapsed = timedelta(hours=units["hours"], minutes=units["minutes"], seconds=units["seconds"])
    if not (calendar_days or elapsed) or (elapsed and not isinstance(start, datetime)):
        raise ValueError("invalid-event-duration")
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


def _agenda_event(lines: list[str], zone: ZoneInfo, start: datetime, end: datetime) -> dict[str, Any]:
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
        raise ValueError("invalid-event-duration")
    first, last = _instant(begins, zone), _instant(finishes, zone)
    if last < first or (last == first and "DTEND" in fields):
        raise ValueError("invalid-event-duration")
    if not _overlaps(first, last, start, end):
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


def _expanded_events(raw: str, zone: ZoneInfo, start: datetime, end: datetime) -> list[dict[str, Any]]:
    lines = unfold_ical(raw)
    if _component_blocks(lines, "VCALENDAR") != [(0, len(lines) - 1)]:
        raise ValueError("invalid-calendar-data")
    events = []
    cursor = 1
    for first, last in _component_blocks(lines, "VEVENT"):
        if any(line.upper().startswith(("BEGIN:", "END:")) for line in lines[cursor:first]):
            raise ValueError("invalid-calendar-data")
        if any(line.upper().startswith("BEGIN:") and line.upper() != "BEGIN:VALARM"
               for line in lines[first + 1:last]):
            raise ValueError("invalid-calendar-data")
        properties = [lines[index] for index in _direct_property_indices(lines, first, last)]
        events.append(_agenda_event(properties, zone, start, end))
        cursor = last + 1
    if not events or any(line.upper().startswith(("BEGIN:", "END:")) for line in lines[cursor:-1]):
        raise ValueError("invalid-calendar-data")
    recurrence_ids = {event["recurrence_id"] for event in events}
    if len({event["uid"] for event in events}) != 1 or len(recurrence_ids) != len(events):
        raise ValueError("invalid-recurrence-id")
    if any(recurrence_ids):
        for event in events:
            event["recurring"] = True
    return events


def _row_calendar_data(row: ET.Element, request_url: str) -> tuple[str, str]:
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
        or (href.netloc and (href.scheme, href.netloc) != (parent.scheme, parent.netloc))
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
    return path, fields[payload]


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
            path, raw = _row_calendar_data(row, request_url)
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
