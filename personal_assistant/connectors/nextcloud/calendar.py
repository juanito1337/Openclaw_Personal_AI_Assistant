from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from ...calendar_agenda import (
    AGENDA_RAW_REPORT,
    AGENDA_RESPONSE_MAX_BYTES,
    CALENDAR_TIMEZONE_PROPFIND,
    AgendaFallbackError,
    agenda_query,
    agenda_timezone_error,
    calendar_timezone_absence,
    calendar_timezone_property_evidence,
    parse_agenda,
    parse_agenda_provenance,
    project_agenda_fallback,
)
from ...config import AssistantConfig
from ...extractors import chunks
from ...ical_edit import component_properties, first_value, unescape_ical
from ...incremental_sync import RemoteObject, StageTelemetry, plan_batch
from ...nextcloud_access_runtime import check_component_write, component_request, readback_after_write
from ...nextcloud_index_access import require_scoped_sync
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


@dataclass(slots=True, frozen=True)
class CalendarMetadata:
    href: str
    etag: str
    uid: str = ""


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
 <c:time-range start='{start.strftime("%Y%m%dT%H%M%SZ")}' end='{end.strftime("%Y%m%dT%H%M%SZ")}'/>
 </c:comp-filter></c:comp-filter></c:filter>
</c:calendar-query>""".encode()
        response = component_request(
            self.client,
            "VEVENT",
            "list",
            calendar,
            "REPORT",
            calendar.href,
            data=body,
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

    def list_agenda(
        self, calendar: DiscoveredCollection, *, start: datetime, end: datetime, timezone_name: str,
    ) -> dict[str, Any]:
        body, zone = agenda_query(start, end, timezone_name)
        request_url = self.client.dav_request_url(calendar.href)
        response = component_request(
            self.client, "VEVENT", "list", calendar, "REPORT", calendar.href, data=body,
            headers={"Depth": "1", "Content-Type": "application/xml; charset=utf-8"}, expected={207},
            max_response_bytes=AGENDA_RESPONSE_MAX_BYTES,
        )
        if response.url != request_url:
            return {"complete": False, "events": [], "errors": [{"code": "invalid-calendar-response"}]}
        result = parse_agenda(response.data, request_url, start=start, end=end, zone=zone)
        issue, missing = self._agenda_timezone_issue(calendar, request_url, zone, start, end)
        if missing:
            return self._agenda_without_collection_timezone(calendar, request_url, body, zone, start, end)
        if issue and issue.get("reason") == "response-url-mismatch":
            return {"complete": False, "events": [], "errors": [issue]}
        if issue and result["complete"]:
            result.update(complete=False, errors=[issue])
        return result

    def _agenda_timezone_issue(
        self, calendar: DiscoveredCollection, request_url: str, zone: ZoneInfo,
        start: datetime, end: datetime,
    ) -> tuple[dict[str, str | int] | None, bool]:
        unverified = "calendar-timezone-unverified"
        try:
            measured = component_request(
                self.client, "VEVENT", "list", calendar, "PROPFIND", calendar.href,
                data=CALENDAR_TIMEZONE_PROPFIND,
                headers={"Depth": "0", "Content-Type": "application/xml; charset=utf-8"},
                max_response_bytes=AGENDA_RESPONSE_MAX_BYTES,
            )
        except PermissionError as exc:
            code = str(exc)
            if code not in {"server-right-missing", "rights-unverified"}:
                raise
            return {"code": code}, False
        except NextcloudError as exc:
            if str(exc).startswith("nextcloud-connection-changed"):
                raise
            return {"code": unverified, "reason": "transport-error"}, False
        if measured.status != 207:
            return {"code": unverified, "reason": "http-status", "http_status": measured.status}, False
        if measured.url != request_url:
            return {"code": unverified, "reason": "response-url-mismatch"}, False
        if calendar_timezone_absence(measured.data, request_url):
            return None, True
        definition, reason, property_status = calendar_timezone_property_evidence(measured.data, request_url)
        code = unverified
        if definition:
            code, reason = agenda_timezone_error(definition, zone, start, end)
        if not code:
            return None, False
        issue: dict[str, str | int] = {"code": code}
        if reason:
            issue["reason"] = reason
            if property_status is not None:
                issue["property_status"] = property_status
        return issue, False

    def _agenda_without_collection_timezone(
        self, calendar: DiscoveredCollection, url: str, expanded_query: bytes,
        zone: ZoneInfo, start: datetime, end: datetime,
    ) -> dict[str, Any]:
        def scoped_report(query: bytes, *, expanded: bool = False):
            response = component_request(
                self.client, "VEVENT", "list", calendar, "REPORT", calendar.href, data=query,
                headers={"Depth": "1", "Content-Type": "application/xml; charset=utf-8"},
                max_response_bytes=AGENDA_RESPONSE_MAX_BYTES,
            )
            if response.status != 207:
                raise AgendaFallbackError("calendar-fallback-http-error")
            if response.url != url:
                failure_code = ("calendar-fallback-expansion-unbound" if expanded
                                else "calendar-fallback-provenance-invalid")
                raise AgendaFallbackError(failure_code)
            return parse_agenda_provenance(response.data, url, expanded=expanded)

        try:
            before = scoped_report(AGENDA_RAW_REPORT)
            expanded = scoped_report(expanded_query, expanded=True)
            after = scoped_report(AGENDA_RAW_REPORT)
            return project_agenda_fallback(before, expanded, after, zone=zone, start=start, end=end)
        except AgendaFallbackError as exc:
            code = str(exc)
        except PermissionError as exc:
            code = str(exc)
            if code not in {"server-right-missing", "rights-unverified"}:
                raise
        except NextcloudError as exc:
            if str(exc).startswith("nextcloud-connection-changed"):
                raise
            code = ("calendar-fallback-provenance-limit" if str(exc) == "response-limit"
                    else "calendar-fallback-transport-error")
        return {"complete": False, "events": [], "errors": [{"code": code}]}

    def list_event_metadata(self, calendar: DiscoveredCollection) -> list[CalendarMetadata]:
        now = datetime.now(UTC)
        start = now - timedelta(days=self.config.nextcloud.calendar_horizon_days_back)
        end = now + timedelta(days=self.config.nextcloud.calendar_horizon_days_forward)
        snapshot = getattr(self.client, "access_snapshot", None)
        proof = ""
        if snapshot is not None and snapshot.mode == "active":
            proof = (
                "<c:calendar-data><c:comp name='VCALENDAR'><c:comp name='VEVENT'>"
                "<c:prop name='UID'/></c:comp></c:comp></c:calendar-data>"
            )
        body = f"""<?xml version='1.0' encoding='utf-8'?>
<c:calendar-query xmlns:d='DAV:' xmlns:c='urn:ietf:params:xml:ns:caldav'>
 <d:prop><d:getetag/>{proof}</d:prop>
 <c:filter><c:comp-filter name='VCALENDAR'><c:comp-filter name='VEVENT'>
 <c:time-range start='{start.strftime("%Y%m%dT%H%M%SZ")}' end='{end.strftime("%Y%m%dT%H%M%SZ")}'/>
 </c:comp-filter></c:comp-filter></c:filter>
</c:calendar-query>""".encode()
        response = component_request(
            self.client,
            "VEVENT",
            "list",
            calendar,
            "REPORT",
            calendar.href,
            data=body,
            headers={"Depth": "1", "Content-Type": "application/xml; charset=utf-8"},
            expected={207},
        )
        return [
            CalendarMetadata(
                href=item.href,
                etag=item.properties.get(q(DAV, "getetag"), ""),
                uid=unescape_ical(first_value(
                    component_properties(item.properties[q(CALDAV, "calendar-data")], "VEVENT"), "UID"
                )) if proof else "",
            )
            for item in parse_multistatus(response.data)
            if item.href and item.href.rstrip("/") != calendar.href.rstrip("/")
        ]

    def find_events_by_uid(self, calendar: DiscoveredCollection, uid: str) -> list[CalendarObject]:
        clean_uid = str(uid or "").strip()
        if not clean_uid:
            raise ValueError("Kalender-UID fehlt")
        escaped = clean_uid.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        body = f"""<?xml version='1.0' encoding='utf-8'?>
<c:calendar-query xmlns:d='DAV:' xmlns:c='urn:ietf:params:xml:ns:caldav'>
 <d:prop><d:getetag/><c:calendar-data/></d:prop>
 <c:filter><c:comp-filter name='VCALENDAR'><c:comp-filter name='VEVENT'>
  <c:prop-filter name='UID'><c:text-match collation='i;octet'>{escaped}</c:text-match></c:prop-filter>
 </c:comp-filter></c:comp-filter></c:filter>
</c:calendar-query>""".encode()
        response = component_request(
            self.client,
            "VEVENT",
            "read",
            calendar,
            "REPORT",
            calendar.href,
            data=body,
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
        response = component_request(
            self.client, "VEVENT", "read", None, "GET", href, object_uid=fallback_uid, expected={200}
        )
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
        response = component_request(
            self.client,
            "VEVENT",
            "read",
            calendar,
            "GET",
            self.event_href(calendar, uid),
            object_uid=uid,
            expected={200, 404},
        )
        return response.status == 200

    def create_event(self, calendar: DiscoveredCollection, ics: str, uid: str) -> str:
        check_component_write(self.client, calendar, "VEVENT", "create", uid, ics)
        href = self.event_href(calendar, uid, ics)
        response = component_request(
            self.client,
            "VEVENT",
            "create",
            calendar,
            "PUT",
            href,
            data=ics.encode("utf-8"),
            headers={"Content-Type": "text/calendar; charset=utf-8", "If-None-Match": "*"},
            expected={200, 201, 204, 412},
        )
        if response.status in {200, 201, 204}:
            snapshot = getattr(self.client, "access_snapshot", None)
            if snapshot is not None and snapshot.mode == "active":
                readback_after_write(self.client, self.read_event, href, uid, "VEVENT", ics)
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
        check_component_write(self.client, calendar, "VEVENT", "update", uid, ics)
        safe_href = self._validated_event_href(calendar, href)
        current_etag = str(etag or "").strip()
        if not current_etag:
            current_etag = self.read_event(safe_href, fallback_uid=uid).etag
        if not current_etag:
            raise RuntimeError("CalDAV-Server lieferte keinen ETag; sicheres Kalender-Update abgebrochen")
        response = component_request(
            self.client,
            "VEVENT",
            "update",
            calendar,
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
                "Kalendereintrag wurde zwischenzeitlich geaendert; bitte erneut suchen "
                "und die Aenderung wiederholen"
            )
        verified = readback_after_write(self.client, self.read_event, safe_href, uid, "VEVENT", ics)
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

    def sync_index(
        self,
        storage: AssistantStorage,
        calendars: list[DiscoveredCollection],
        *,
        batch_size: int = 100,
    ) -> dict[str, Any]:
        source = require_scoped_sync(self.client, storage, "VEVENT")
        if source is not None and [item.resource_id for item in calendars] != [source.scope.resource_id]:
            raise PermissionError("scope-unbound")
        stats: dict[str, Any] = {
            "calendars": 0,
            "events": 0,
            "indexed": 0,
            "unchanged": 0,
            "moved": 0,
            "removed": 0,
            "errors": 0,
            "resume_required": False,
            "cursor_resets": 0,
        }
        telemetry = StageTelemetry()
        for calendar in calendars:
            telemetry.start("discovery")
            try:
                metadata = self.list_event_metadata(calendar)
                stats["calendars"] = int(stats["calendars"]) + 1
            except Exception as exc:
                if source is not None and isinstance(exc, PermissionError):
                    raise
                stats["errors"] = int(stats["errors"]) + 1
                if not storage.core_read_only:
                    storage.audit(
                        "nextcloud.calendar.sync_failed",
                        {"calendar": calendar.name, "error": str(exc)},
                        resource_id=calendar.resource_id,
                    )
                telemetry.stop("discovery")
                continue
            telemetry.stop("discovery")
            state = storage.get_sync_state(calendar.resource_id, "calendar")
            plan = plan_batch(
                [RemoteObject(item.href, item.etag) for item in metadata],
                cursor=str(state["cursor"] or "") if state is not None else "",
                batch_size=batch_size,
            )
            stats["resume_required"] = bool(stats["resume_required"]) or plan.resume_required
            stats["cursor_resets"] = int(stats["cursor_resets"]) + int(plan.cursor_reset)
            by_href = {item.href: item for item in metadata}
            batch_errors = 0
            for remote in plan.objects:
                item = by_href[remote.remote_id]
                telemetry.start("metadata_compare")
                inventory = storage.get_sync_inventory(calendar.resource_id, "calendar", item.href)
                if inventory is not None and item.etag and str(inventory["etag"] or "") == item.etag:
                    stats["unchanged"] = int(stats["unchanged"]) + 1
                    telemetry.add("metadata_hits")
                    telemetry.stop("metadata_compare")
                    continue
                moved = storage.find_sync_inventory_by_etag(calendar.resource_id, "calendar", item.etag)
                if moved is not None and str(moved["remote_id"]) != item.href:
                    source_id = str(moved["source_id"])
                    storage.update_document_locator(
                        resource_id=calendar.resource_id,
                        source_id=source_id,
                        uri=item.href,
                        etag=item.etag,
                    )
                    storage.move_sync_inventory(
                        resource_id=calendar.resource_id,
                        scope="calendar",
                        previous_remote_id=str(moved["remote_id"]),
                        remote_id=item.href,
                        source_id=source_id,
                        source_type="calendar-event",
                        etag=item.etag,
                    )
                    stats["moved"] = int(stats["moved"]) + 1
                    telemetry.add("moves")
                    telemetry.stop("metadata_compare")
                    continue
                telemetry.add("metadata_misses")
                telemetry.stop("metadata_compare")
                try:
                    telemetry.start("download")
                    event = self.read_event(
                        item.href,
                        fallback_uid=item.uid or (str(inventory["source_id"] or "") if inventory else ""),
                    )
                    if item.uid and (event.uid != item.uid or not item.etag or event.etag != item.etag):
                        raise PermissionError("object-changed")
                    telemetry.stop("download")
                    telemetry.start("parse")
                    text = "\n".join(
                        [
                            event.summary,
                            f"Beginn: {event.starts_at}",
                            f"Ende: {event.ends_at}",
                            f"Ort: {event.location}",
                            event.description,
                        ]
                    )
                    event_chunks = chunks(
                        text,
                        size=self.config.search.chunk_chars,
                        overlap=self.config.search.chunk_overlap_chars,
                    )
                    telemetry.stop("parse")
                    telemetry.start("index_update")
                    storage.index_document(
                        source_type="calendar-event",
                        resource_id=calendar.resource_id,
                        source_id=event.uid,
                        uri=event.href,
                        title=event.summary or event.uid,
                        mime_type="text/calendar",
                        etag=event.etag or item.etag,
                        metadata={
                            "calendar": calendar.name,
                            "starts_at": event.starts_at,
                            "ends_at": event.ends_at,
                            "location": event.location,
                            "status": event.status,
                            "recurring": event.recurring,
                        },
                        chunks=event_chunks,
                    )
                    telemetry.stop("index_update")
                    telemetry.start("commit")
                    storage.upsert_sync_inventory(
                        resource_id=calendar.resource_id,
                        scope="calendar",
                        remote_id=item.href,
                        source_id=event.uid,
                        source_type="calendar-event",
                        etag=event.etag or item.etag,
                    )
                    telemetry.stop("commit")
                    stats["events"] = int(stats["events"]) + 1
                    stats["indexed"] = int(stats["indexed"]) + 1
                except Exception as exc:
                    if source is not None and isinstance(exc, PermissionError):
                        raise
                    telemetry.stop_running()
                    batch_errors += 1
                    stats["errors"] = int(stats["errors"]) + 1
                    if not storage.core_read_only:
                        storage.audit(
                            "nextcloud.calendar.index_failed",
                            {"href": item.href, "error": str(exc)},
                            resource_id=calendar.resource_id,
                        )
            completed = plan.complete and batch_errors == 0
            if completed:
                telemetry.start("commit")
                removed = storage.reconcile_sync_inventory(
                    resource_id=calendar.resource_id,
                    scope="calendar",
                    remote_ids={item.href for item in metadata},
                )
                telemetry.stop("commit")
                stats["removed"] = int(stats["removed"]) + removed
            storage.set_sync_state(
                calendar.resource_id,
                "calendar",
                cursor=plan.cursor()
                if batch_errors == 0
                else (str(state["cursor"] or "") if state is not None else ""),
                status="ok" if completed else ("partial" if batch_errors else "in-progress"),
                detail=json.dumps({key: value for key, value in stats.items() if key != "telemetry"}),
                data_changed=bool(int(stats["indexed"]) or int(stats["moved"]) or int(stats["removed"])),
            )
        stats["telemetry"] = telemetry.to_dict()
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
