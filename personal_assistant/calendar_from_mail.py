from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

PREVIEW_SCHEMA_VERSION = 1

_SECTION_RE = re.compile(
    r"(?im)^\s*(?P<label>hinflug|r(?:ue|ü)ckflug|outbound|return|ida|vuelta)\s*[:\-]?\s*$"
)
_DATE_RE = re.compile(
    r"(?<!\d)(?P<day>0?[1-9]|[12]\d|3[01])[.\-/]"
    r"(?P<month>0?[1-9]|1[0-2])[.\-/](?P<year>20\d{2})(?!\d)"
)
_ISO_DATE_RE = re.compile(
    r"(?<!\d)(?P<year>20\d{2})-(?P<month>0[1-9]|1[0-2])-"
    r"(?P<day>0[1-9]|[12]\d|3[01])(?!\d)"
)
_TIME_RE = re.compile(r"(?<!\d)(?P<hour>[01]?\d|2[0-3]):(?P<minute>[0-5]\d)(?!\d)")
_FLIGHT_RE = re.compile(
    r"(?i)\b(?:flug(?:nummer)?|flight|vuelo)?\s*[:#]?\s*"
    r"(?P<flight>[A-Z][A-Z0-9]{1,2})[ -]?(?P<number>\d{2,4}[A-Z]?)\b"
)
_ROUTE_RE = re.compile(
    r"(?i)(?:\b|\()(?P<origin>[A-Z]{3})(?:\)|\b)\s*"
    r"(?:→|->|–|—|\bnach\b|\bto\b|\ba\b)\s*"
    r"(?:\b|\()(?P<destination>[A-Z]{3})(?:\)|\b)"
)

_AIRPORT_TIMEZONES = {
    "BER": "Europe/Berlin",
    "BRE": "Europe/Berlin",
    "CGN": "Europe/Berlin",
    "DRS": "Europe/Berlin",
    "DUS": "Europe/Berlin",
    "FRA": "Europe/Berlin",
    "HAM": "Europe/Berlin",
    "HAJ": "Europe/Berlin",
    "LEJ": "Europe/Berlin",
    "MUC": "Europe/Berlin",
    "NUE": "Europe/Berlin",
    "STR": "Europe/Berlin",
    "SPC": "Atlantic/Canary",
    "LPA": "Atlantic/Canary",
    "TFS": "Atlantic/Canary",
    "TFN": "Atlantic/Canary",
    "ACE": "Atlantic/Canary",
    "FUE": "Atlantic/Canary",
    "MAD": "Europe/Madrid",
    "BCN": "Europe/Madrid",
    "PMI": "Europe/Madrid",
    "LHR": "Europe/London",
    "LGW": "Europe/London",
    "STN": "Europe/London",
    "LCY": "Europe/London",
    "CDG": "Europe/Paris",
    "ORY": "Europe/Paris",
    "AMS": "Europe/Amsterdam",
    "VIE": "Europe/Vienna",
    "ZRH": "Europe/Zurich",
    "JFK": "America/New_York",
    "EWR": "America/New_York",
    "LGA": "America/New_York",
    "SFO": "America/Los_Angeles",
    "LAX": "America/Los_Angeles",
}


@dataclass(frozen=True, slots=True)
class CalendarMailCandidate:
    candidate_id: str
    digest: str
    uid: str
    direction: str
    title: str
    start: str
    end: str
    timezone_start: str
    timezone_end: str
    location: str
    description: str
    flight_number: str
    origin: str
    destination: str
    complete: bool
    missing_fields: tuple[str, ...]
    conflicts: tuple[str, ...]
    field_provenance: dict[str, str]
    duplicate: bool = False

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["missing_fields"] = list(self.missing_fields)
        payload["conflicts"] = list(self.conflicts)
        return payload


@dataclass(frozen=True, slots=True)
class _IcsEvent:
    uid: str
    title: str
    start: str
    end: str
    timezone: str
    location: str
    notes: str


def _unfold_ics(text: str) -> list[str]:
    lines: list[str] = []
    for raw in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if raw.startswith((" ", "\t")) and lines:
            lines[-1] += raw[1:]
        else:
            lines.append(raw)
    return lines


def _ics_datetime(value: str, params: str, default_timezone: str) -> str:
    raw = value.strip()
    if "VALUE=DATE" in params.upper() or (len(raw) == 8 and "T" not in raw):
        return datetime.strptime(raw[:8], "%Y%m%d").date().isoformat()
    timezone_name = default_timezone
    for part in params.split(";"):
        if part.upper().startswith("TZID="):
            timezone_name = part.split("=", 1)[1].strip().strip('"')
    if raw.endswith("Z"):
        value_without_z = raw[:-1]
        pattern = "%Y%m%dT%H%M%S" if len(value_without_z) >= 15 else "%Y%m%dT%H%M"
        parsed = datetime.strptime(value_without_z, pattern).replace(tzinfo=UTC)
    else:
        trimmed = raw[:15] if len(raw) >= 15 else raw[:13]
        pattern = "%Y%m%dT%H%M%S" if len(trimmed) >= 15 else "%Y%m%dT%H%M"
        parsed = datetime.strptime(trimmed, pattern).replace(tzinfo=ZoneInfo(timezone_name))
    return parsed.isoformat()


def _ics_value(value: str) -> str:
    return value.replace("\\n", "\n").replace("\\,", ",").replace("\\;", ";")


def _event_from_ics(text: str, default_timezone: str) -> _IcsEvent | None:
    in_event = False
    values: dict[str, tuple[str, str]] = {}
    for line in _unfold_ics(text):
        upper = line.upper()
        if upper == "BEGIN:VEVENT":
            in_event = True
            continue
        if upper == "END:VEVENT":
            break
        if not in_event or ":" not in line:
            continue
        left, value = line.split(":", 1)
        name, _, params = left.partition(";")
        if name.upper() in {
            "UID",
            "SUMMARY",
            "DTSTART",
            "DTEND",
            "LOCATION",
            "DESCRIPTION",
        }:
            values[name.upper()] = (value, params)
    if "SUMMARY" not in values or "DTSTART" not in values:
        return None
    try:
        start = _ics_datetime(values["DTSTART"][0], values["DTSTART"][1], default_timezone)
        end = (
            _ics_datetime(values["DTEND"][0], values["DTEND"][1], default_timezone)
            if "DTEND" in values
            else ""
        )
    except (ValueError, KeyError, ZoneInfoNotFoundError):
        return None
    return _IcsEvent(
        uid=_ics_value(values.get("UID", ("", ""))[0]),
        title=_ics_value(values["SUMMARY"][0]),
        start=start,
        end=end,
        timezone=default_timezone,
        location=_ics_value(values.get("LOCATION", ("", ""))[0]),
        notes=_ics_value(values.get("DESCRIPTION", ("", ""))[0]),
    )


def _canonical_digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()


def _mail_source_hash(message: Any) -> str:
    return hashlib.sha256(message.raw).hexdigest()


def _date_from(text: str) -> date | None:
    match = _DATE_RE.search(text) or _ISO_DATE_RE.search(text)
    if match is None:
        return None
    try:
        return date(int(match.group("year")), int(match.group("month")), int(match.group("day")))
    except ValueError:
        return None


def _flight_number(text: str) -> str:
    for match in _FLIGHT_RE.finditer(text):
        prefix = match.group("flight").upper()
        # Avoid interpreting common date/time labels as airline designators.
        if prefix in {"AM", "PM", "UTC", "GMT", "UHR"}:
            continue
        return f"{prefix}{match.group('number').upper()}"
    return ""


def _route(text: str) -> tuple[str, str]:
    match = _ROUTE_RE.search(text)
    if match is None:
        return "", ""
    return match.group("origin").upper(), match.group("destination").upper()


def _direction(label: str, index: int) -> str:
    normalized = label.casefold()
    if normalized in {"hinflug", "outbound", "ida"}:
        return "outbound"
    if normalized in {"rückflug", "rueckflug", "return", "vuelta"}:
        return "return"
    return f"segment-{index + 1}"


def _sections(body: str) -> list[tuple[str, str]]:
    matches = list(_SECTION_RE.finditer(body))
    if not matches:
        return [("segment", body)]
    result: list[tuple[str, str]] = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(body)
        result.append((match.group("label"), body[match.end() : end]))
    return result


def _candidate_from_section(
    message: Any,
    *,
    label: str,
    text: str,
    index: int,
) -> CalendarMailCandidate:
    segment_date = _date_from(text)
    times = list(_TIME_RE.finditer(text))
    flight_number = _flight_number(text)
    origin, destination = _route(text)
    timezone_start = _AIRPORT_TIMEZONES.get(origin, "")
    timezone_end = _AIRPORT_TIMEZONES.get(destination, "")
    missing: list[str] = []
    if segment_date is None:
        missing.append("date")
    if len(times) < 2:
        missing.append("start/end-time")
    if not flight_number:
        missing.append("flight-number")
    if not origin:
        missing.append("origin")
    if not destination:
        missing.append("destination")
    if origin and not timezone_start:
        missing.append("origin-timezone")
    if destination and not timezone_end:
        missing.append("destination-timezone")

    start = ""
    end = ""
    conflicts: list[str] = []
    if segment_date and len(times) >= 2 and timezone_start and timezone_end:
        try:
            start_local = datetime(
                segment_date.year,
                segment_date.month,
                segment_date.day,
                int(times[0].group("hour")),
                int(times[0].group("minute")),
                tzinfo=ZoneInfo(timezone_start),
            )
            end_local = datetime(
                segment_date.year,
                segment_date.month,
                segment_date.day,
                int(times[1].group("hour")),
                int(times[1].group("minute")),
                tzinfo=ZoneInfo(timezone_end),
            )
            if end_local.astimezone(ZoneInfo("UTC")) <= start_local.astimezone(ZoneInfo("UTC")):
                end_local += timedelta(days=1)
            start = start_local.isoformat()
            end = end_local.isoformat()
        except (ValueError, ZoneInfoNotFoundError):
            conflicts.append("invalid-local-time")

    direction = _direction(label, index)
    title = f"Flug {flight_number} {origin}–{destination}".strip(" –")
    location = f"{origin} → {destination}".strip(" →")
    description = f"Flugdaten aus der ausgewaehlten E-Mail: {message.subject}".strip()
    source_hash = _mail_source_hash(message)
    identity = {
        "source_hash": source_hash,
        "direction": direction,
        "flight_number": flight_number,
        "origin": origin,
        "destination": destination,
        "start": start,
        "end": end,
    }
    candidate_id = _canonical_digest(identity)[:24]
    digest_payload = {
        **identity,
        "title": title,
        "location": location,
        "description": description,
    }
    digest = _canonical_digest(digest_payload)
    return CalendarMailCandidate(
        candidate_id=candidate_id,
        digest=digest,
        uid=f"assistant-flight-{candidate_id}@local",
        direction=direction,
        title=title,
        start=start,
        end=end,
        timezone_start=timezone_start,
        timezone_end=timezone_end,
        location=location,
        description=description,
        flight_number=flight_number,
        origin=origin,
        destination=destination,
        complete=not missing and not conflicts,
        missing_fields=tuple(missing),
        conflicts=tuple(conflicts),
        field_provenance={
            "date": "mail-body:segment-date" if segment_date else "missing",
            "start": "mail-body:first-time" if len(times) >= 1 else "missing",
            "end": "mail-body:second-time" if len(times) >= 2 else "missing",
            "flight_number": "mail-body:flight-number" if flight_number else "missing",
            "origin": "mail-body:route-origin" if origin else "missing",
            "destination": "mail-body:route-destination" if destination else "missing",
            "timezone_start": "closed-airport-timezone-map" if timezone_start else "missing",
            "timezone_end": "closed-airport-timezone-map" if timezone_end else "missing",
        },
    )


def _candidates_from_ics(message: Any, default_timezone: str) -> list[CalendarMailCandidate]:
    result: list[CalendarMailCandidate] = []
    source_hash = _mail_source_hash(message)
    for index, raw in enumerate(message.calendar_invites[:MAX_INVITES]):
        event = _event_from_ics(raw, default_timezone)
        if event is None:
            continue
        end = str(event.end or "")
        missing = tuple(
            name
            for name, value in (("title", event.title), ("start", event.start), ("end", end))
            if not value
        )
        identity = {
            "source_hash": source_hash,
            "ics_index": index,
            "source_uid": event.uid,
            "start": event.start,
            "end": end,
        }
        candidate_id = _canonical_digest(identity)[:24]
        digest = _canonical_digest(
            {
                **identity,
                "title": event.title,
                "location": event.location,
                "description": event.notes,
            }
        )
        result.append(
            CalendarMailCandidate(
                candidate_id=candidate_id,
                digest=digest,
                uid=f"assistant-mail-event-{candidate_id}@local",
                direction=f"ics-{index + 1}",
                title=event.title,
                start=event.start,
                end=end,
                timezone_start=event.timezone or default_timezone,
                timezone_end=event.timezone or default_timezone,
                location=event.location,
                description=event.notes,
                flight_number="",
                origin="",
                destination="",
                complete=not missing,
                missing_fields=missing,
                conflicts=(),
                field_provenance={
                    "title": "mail-attachment:text/calendar:SUMMARY",
                    "start": "mail-attachment:text/calendar:DTSTART",
                    "end": "mail-attachment:text/calendar:DTEND" if end else "missing",
                    "location": "mail-attachment:text/calendar:LOCATION",
                },
            )
        )
    return result


MAX_INVITES = 4


def build_calendar_mail_preview(
    message: Any,
    *,
    resource_id: str,
    default_timezone: str,
    duplicate_uids: set[str] | None = None,
) -> dict[str, Any]:
    """Build a deterministic read-only preview from one exact, scanned mail."""

    duplicate_uids = duplicate_uids or set()
    candidates = _candidates_from_ics(message, default_timezone)
    if not candidates:
        candidates = [
            _candidate_from_section(message, label=label, text=text, index=index)
            for index, (label, text) in enumerate(_sections(message.body_text)[:4])
        ]
    candidates = [
        CalendarMailCandidate(
            **{
                **candidate.to_dict(),
                "missing_fields": tuple(candidate.missing_fields),
                "conflicts": tuple(candidate.conflicts),
                "duplicate": candidate.uid in duplicate_uids,
            }
        )
        for candidate in candidates
    ]
    source = {
        "folder": message.source_folder,
        "mailbox_id": message.mailbox_id,
        "expected_subject": message.subject,
        "raw_sha256": _mail_source_hash(message),
    }
    preview_core = {
        "schema_version": PREVIEW_SCHEMA_VERSION,
        "resource_id": resource_id,
        "source": source,
        "candidates": [candidate.to_dict() for candidate in candidates],
    }
    preview_digest = _canonical_digest(preview_core)
    ready = bool(candidates) and all(candidate.complete for candidate in candidates)
    return {
        "ok": True,
        "complete": ready,
        "decision": "ready" if ready else "information-required",
        "read_only": True,
        "writes_external_data": False,
        "schema_version": PREVIEW_SCHEMA_VERSION,
        "resource_id": resource_id,
        "source": source,
        "preview_digest": preview_digest,
        "candidate_count": len(candidates),
        "candidates": [candidate.to_dict() for candidate in candidates],
        "approval_required_for_create": True,
        "bulk_write": False,
    }


def select_unchanged_candidate(
    preview: dict[str, Any],
    *,
    expected_preview_digest: str,
    candidate_id: str,
) -> dict[str, Any]:
    if str(preview.get("preview_digest") or "") != str(expected_preview_digest or ""):
        raise PermissionError("Mail-zu-Kalender-Vorschau hat sich geaendert")
    matches = [
        item
        for item in preview.get("candidates") or []
        if isinstance(item, dict) and str(item.get("candidate_id") or "") == candidate_id
    ]
    if len(matches) != 1:
        raise ValueError("Kalenderkandidat wurde nicht eindeutig in der Vorschau gefunden")
    candidate = dict(matches[0])
    if not candidate.get("complete"):
        raise ValueError("Kalenderkandidat ist unvollstaendig oder widerspruechlich")
    return candidate


__all__ = [
    "CalendarMailCandidate",
    "PREVIEW_SCHEMA_VERSION",
    "build_calendar_mail_preview",
    "select_unchanged_candidate",
]
