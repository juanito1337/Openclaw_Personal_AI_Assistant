from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from ...config import AssistantConfig
from ...extractors import chunks
from ...ical_edit import component_properties, first_value, unescape_ical
from ...storage import AssistantStorage
from .client import NextcloudClient, NextcloudError
from .discovery import DiscoveredCollection
from .xmlutil import CALDAV, DAV, parse_multistatus, q


@dataclass(slots=True, frozen=True)
class CalendarObject:
    uid: str
    summary: str
    starts_at: str
    ends_at: str
    description: str
    location: str
    status: str
    recurring: bool
    all_day: bool
    raw_ics: str
    href: str
    etag: str


class NextcloudCalendar:
    def __init__(self, config: AssistantConfig, client: NextcloudClient) -> None:
        self.config = config
        self.client = client

    def list_events(self, calendar: DiscoveredCollection) -> list[CalendarObject]:
        now = datetime.now(UTC)
        start = now - timedelta(days=self.config.nextcloud.calendar_horizon_days_back)
        end = now + timedelta(days=self.config.nextcloud.calendar_horizon_days_forward)
        body = f"""<?xml version='1.0' encoding='utf-8'?>
<c:calendar-query xmlns:d='DAV:' xmlns:c='urn:ietf:params:xml:ns:caldav'>
 <d:prop><d:getetag/><c:calendar-data/></d:prop>
 <c:filter><c:comp-filter name='VCALENDAR'><c:comp-filter name='VEVENT'>
 <c:time-range start='{start.strftime('%Y%m%dT%H%M%SZ')}' end='{end.strftime('%Y%m%dT%H%M%SZ')}'/>
 </c:comp-filter></c:comp-filter></c:filter>
</c:calendar-query>""".encode()
        response = self.client.request(
            "REPORT", calendar.href, data=body,
            headers={"Depth": "1", "Content-Type": "application/xml; charset=utf-8"},
            expected={207},
        )
        result: list[CalendarObject] = []
        for item in parse_multistatus(response.data):
            raw = item.properties.get(q(CALDAV, "calendar-data"), "")
            if not raw:
                continue
            result.append(self._parse_ics(raw, item.href, item.properties.get(q(DAV, "getetag"), "")))
        return result

    def find_events_by_uid(self, calendar: DiscoveredCollection, uid: str) -> list[CalendarObject]:
        clean_uid = str(uid or "").strip()
        if not clean_uid:
            raise ValueError("Kalender-UID fehlt")
        escaped = (clean_uid.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))
        body = f"""<?xml version='1.0' encoding='utf-8'?>
<c:calendar-query xmlns:d='DAV:' xmlns:c='urn:ietf:params:xml:ns:caldav'>
 <d:prop><d:getetag/><c:calendar-data/></d:prop>
 <c:filter><c:comp-filter name='VCALENDAR'><c:comp-filter name='VEVENT'>
  <c:prop-filter name='UID'><c:text-match collation='i;octet'>{escaped}</c:text-match></c:prop-filter>
 </c:comp-filter></c:comp-filter></c:filter>
</c:calendar-query>""".encode()
        response = self.client.request(
            "REPORT", calendar.href, data=body,
            headers={"Depth": "1", "Content-Type": "application/xml; charset=utf-8"},
            expected={207},
        )
        result: list[CalendarObject] = []
        for item in parse_multistatus(response.data):
            raw = item.properties.get(q(CALDAV, "calendar-data"), "")
            if not raw:
                continue
            parsed = self._parse_ics(raw, item.href, item.properties.get(q(DAV, "getetag"), ""))
            if parsed.uid == clean_uid:
                result.append(parsed)
        return result

    def read_event(self, href: str, *, fallback_uid: str = "") -> CalendarObject:
        response = self.client.request("GET", href, expected={200})
        return self._parse_ics(
            response.data.decode("utf-8", errors="replace"),
            href,
            self._header(response.headers, "ETag"),
            fallback_uid=fallback_uid,
        )

    @staticmethod
    def safe_uid(uid: str, ics: str = "") -> str:
        return re.sub(r"[^A-Za-z0-9_.@-]+", "-", uid).strip("-") or hashlib.sha256(ics.encode()).hexdigest()

    def event_href(self, calendar: DiscoveredCollection, uid: str, ics: str = "") -> str:
        safe_uid = self.safe_uid(uid, ics)
        return calendar.href.rstrip("/") + "/" + safe_uid + ".ics"

    def event_exists(self, calendar: DiscoveredCollection, uid: str) -> bool:
        response = self.client.request("GET", self.event_href(calendar, uid), expected={200, 404})
        return response.status == 200

    def create_event(self, calendar: DiscoveredCollection, ics: str, uid: str) -> str:
        href = self.event_href(calendar, uid, ics)
        response = self.client.request(
            "PUT",
            href,
            data=ics.encode("utf-8"),
            headers={"Content-Type": "text/calendar; charset=utf-8", "If-None-Match": "*"},
            expected={200, 201, 204, 412},
        )
        if response.status in {200, 201, 204}:
            return href
        if response.status == 412:
            raise NextcloudError("Kalendereintrag existiert bereits")
        raise NextcloudError(f"Kalendereintrag konnte nicht erstellt werden: HTTP {response.status}")

    def update_event(
        self,
        calendar: DiscoveredCollection,
        *,
        href: str,
        uid: str,
        ics: str,
        etag: str,
    ) -> CalendarObject:
        safe_href = self._validated_event_href(calendar, href)
        current_etag = str(etag or "").strip()
        if not current_etag:
            current_etag = self.read_event(safe_href, fallback_uid=uid).etag
        if not current_etag:
            raise RuntimeError("CalDAV-Server lieferte keinen ETag; sicheres Kalender-Update abgebrochen")
        response = self.client.request(
            "PUT",
            safe_href,
            data=ics.encode("utf-8"),
            headers={
                "Content-Type": "text/calendar; charset=utf-8",
                "If-Match": current_etag,
            },
            expected={200, 201, 204, 412},
        )
        if response.status == 412:
            raise RuntimeError(
                "Kalendereintrag wurde zwischenzeitlich geaendert; bitte erneut suchen und die Aenderung wiederholen"
            )
        verified = self.read_event(safe_href, fallback_uid=uid)
        if verified.uid.strip() != uid.strip():
            raise RuntimeError("Kalendereintrag wurde aktualisiert, UID-Verifikation ist fehlgeschlagen")
        return verified

    @staticmethod
    def _validated_event_href(calendar: DiscoveredCollection, href: str) -> str:
        clean = str(href or "").strip()
        root = calendar.href.rstrip("/") + "/"
        if not clean or not clean.startswith(root) or clean == root:
            raise PermissionError("Kalenderobjekt liegt ausserhalb des konfigurierten Kalenders")
        return clean

    def sync_index(self, storage: AssistantStorage, calendars: list[DiscoveredCollection]) -> dict[str, int]:
        stats = {"calendars": 0, "events": 0, "indexed": 0, "errors": 0}
        for calendar in calendars:
            try:
                events = self.list_events(calendar)
                stats["calendars"] += 1
            except Exception as exc:
                stats["errors"] += 1
                storage.audit("nextcloud.calendar.sync_failed", {"calendar": calendar.name, "error": str(exc)}, resource_id=calendar.resource_id)
                continue
            for event in events:
                text = "\n".join([
                    event.summary,
                    f"Beginn: {event.starts_at}",
                    f"Ende: {event.ends_at}",
                    f"Ort: {event.location}",
                    event.description,
                ])
                storage.index_document(
                    source_type="calendar-event",
                    resource_id=calendar.resource_id,
                    source_id=event.uid,
                    uri=event.href,
                    title=event.summary or event.uid,
                    mime_type="text/calendar",
                    etag=event.etag,
                    metadata={
                        "calendar": calendar.name,
                        "starts_at": event.starts_at,
                        "ends_at": event.ends_at,
                        "location": event.location,
                        "status": event.status,
                        "recurring": event.recurring,
                    },
                    chunks=chunks(text, size=self.config.search.chunk_chars, overlap=self.config.search.chunk_overlap_chars),
                )
                stats["events"] += 1
                stats["indexed"] += 1
            storage.set_sync_state(calendar.resource_id, "calendar", status="ok", detail=f"{len(events)} Termine")
        return stats

    @staticmethod
    def _header(headers, name: str) -> str:
        target = name.casefold()
        for key, value in dict(headers or {}).items():
            if str(key).casefold() == target:
                return str(value or "").strip()
        return ""

    @staticmethod
    def _parse_ics(raw: str, href: str, etag: str, *, fallback_uid: str = "") -> CalendarObject:
        props = component_properties(raw, "VEVENT")
        uid = unescape_ical(first_value(props, "UID", fallback_uid)).strip()
        if not uid:
            uid = hashlib.sha256(raw.encode()).hexdigest()
        start = first_value(props, "DTSTART")
        end = first_value(props, "DTEND")
        return CalendarObject(
            uid=uid,
            summary=unescape_ical(first_value(props, "SUMMARY")),
            starts_at=start,
            ends_at=end,
            description=unescape_ical(first_value(props, "DESCRIPTION")),
            location=unescape_ical(first_value(props, "LOCATION")),
            status=first_value(props, "STATUS", "CONFIRMED").upper(),
            recurring=bool(props.get("RRULE") or props.get("RECURRENCE-ID")),
            all_day=bool(start and len(start) == 8 and start.isdigit()),
            raw_ics=raw,
            href=href,
            etag=str(etag or "").strip(),
        )
