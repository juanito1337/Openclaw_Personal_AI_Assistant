from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any
from xml.etree import ElementTree

from ...ical_edit import component_properties, first_value, unescape_ical
from .client import NextcloudClient, NextcloudError
from .discovery import DiscoveredCollection
from .xmlutil import CALDAV, DAV, parse_multistatus, q


@dataclass(slots=True, frozen=True)
class TaskObject:
    uid: str
    title: str
    description: str
    due: str
    start: str
    status: str
    priority: int
    percent_complete: int
    completed: str
    categories: tuple[str, ...]
    recurring: bool
    raw_ics: str
    href: str
    etag: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "uid": self.uid,
            "title": self.title,
            "description": self.description,
            "due": self.due,
            "start": self.start,
            "status": self.status,
            "priority": self.priority,
            "percent_complete": self.percent_complete,
            "completed": self.completed,
            "categories": list(self.categories),
            "recurring": self.recurring,
            "href": self.href,
            "etag": self.etag,
        }


class NextcloudTasks:
    """Restricted CalDAV VTODO client with ETag-guarded updates."""

    def __init__(self, client: NextcloudClient) -> None:
        self.client = client

    @staticmethod
    def _safe_uid(uid: str, fallback: str = "") -> str:
        value = re.sub(r"[^A-Za-z0-9_.@-]+", "-", uid).strip("-")
        return value or hashlib.sha256(fallback.encode("utf-8")).hexdigest()

    def task_href(self, task_list: DiscoveredCollection, uid: str) -> str:
        return task_list.href.rstrip("/") + "/" + self._safe_uid(uid) + ".ics"

    def supports_vtodo(self, task_list: DiscoveredCollection) -> bool:
        body = b"""<?xml version='1.0' encoding='utf-8'?>
<d:propfind xmlns:d='DAV:' xmlns:c='urn:ietf:params:xml:ns:caldav'>
 <d:prop><c:supported-calendar-component-set/></d:prop>
</d:propfind>"""
        response = self.client.request(
            "PROPFIND",
            task_list.href,
            data=body,
            headers={"Depth": "0", "Content-Type": "application/xml; charset=utf-8"},
            expected={207},
        )
        root = ElementTree.fromstring(response.data)
        for component_set in root.iter(q(CALDAV, "supported-calendar-component-set")):
            for comp in component_set.iter(q(CALDAV, "comp")):
                if str(comp.attrib.get("name") or "").upper() == "VTODO":
                    return True
        return False

    def task_exists(self, task_list: DiscoveredCollection, uid: str) -> bool:
        response = self.client.request(
            "PROPFIND",
            self.task_href(task_list, uid),
            headers={"Depth": "0", "Content-Type": "application/xml; charset=utf-8"},
            data=b"<?xml version='1.0'?><d:propfind xmlns:d='DAV:'><d:prop><d:getetag/></d:prop></d:propfind>",
            expected={207, 404},
        )
        return response.status == 207

    def create_task(self, task_list: DiscoveredCollection, ics: str, uid: str) -> str:
        if "BEGIN:VTODO" not in ics or "END:VTODO" not in ics:
            raise NextcloudError("Ungueltige VTODO-Daten")
        href = task_list.href.rstrip("/") + "/" + self._safe_uid(uid, ics) + ".ics"
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
            raise NextcloudError("Aufgabe existiert bereits")
        raise NextcloudError(f"Aufgabe konnte nicht erstellt werden: HTTP {response.status}")

    def find_tasks_by_uid(self, task_list: DiscoveredCollection, uid: str) -> list[TaskObject]:
        clean_uid = str(uid or "").strip()
        if not clean_uid:
            raise ValueError("Aufgaben-UID fehlt")
        escaped = clean_uid.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        body = f"""<?xml version='1.0' encoding='utf-8'?>
<c:calendar-query xmlns:d='DAV:' xmlns:c='urn:ietf:params:xml:ns:caldav'>
 <d:prop><d:getetag/><c:calendar-data/></d:prop>
 <c:filter><c:comp-filter name='VCALENDAR'><c:comp-filter name='VTODO'>
  <c:prop-filter name='UID'><c:text-match collation='i;octet'>{escaped}</c:text-match></c:prop-filter>
 </c:comp-filter></c:comp-filter></c:filter>
</c:calendar-query>""".encode("utf-8")
        response = self.client.request(
            "REPORT", task_list.href, data=body,
            headers={"Depth": "1", "Content-Type": "application/xml; charset=utf-8"},
            expected={207},
        )
        result: list[TaskObject] = []
        for item in parse_multistatus(response.data):
            ics = item.properties.get(q(CALDAV, "calendar-data"), "")
            if not ics:
                continue
            parsed = self._parse_task_object(
                ics, item.href, item.properties.get(q(DAV, "getetag"), "")
            )
            if parsed.uid == clean_uid:
                result.append(parsed)
        return result

    def list_task_objects(
        self,
        task_list: DiscoveredCollection,
        *,
        include_completed: bool = False,
        limit: int = 100,
    ) -> list[TaskObject]:
        body = b"""<?xml version='1.0' encoding='utf-8'?>
<c:calendar-query xmlns:d='DAV:' xmlns:c='urn:ietf:params:xml:ns:caldav'>
 <d:prop><d:getetag/><c:calendar-data/></d:prop>
 <c:filter><c:comp-filter name='VCALENDAR'><c:comp-filter name='VTODO'/></c:comp-filter></c:filter>
</c:calendar-query>"""
        response = self.client.request(
            "REPORT",
            task_list.href,
            data=body,
            headers={"Depth": "1", "Content-Type": "application/xml; charset=utf-8"},
            expected={207},
        )
        result: list[TaskObject] = []
        for item in parse_multistatus(response.data):
            ics = item.properties.get(q(CALDAV, "calendar-data"), "")
            if "BEGIN:VTODO" not in ics:
                continue
            parsed = self._parse_task_object(
                ics,
                item.href,
                item.properties.get(q(DAV, "getetag"), ""),
            )
            if not include_completed and parsed.status == "COMPLETED":
                continue
            result.append(parsed)

        def sort_key(value: TaskObject) -> tuple[str, int, str]:
            return (
                value.due or "9999",
                99 if value.priority == 0 else value.priority,
                value.title,
            )

        result.sort(key=sort_key)
        return result[: max(1, min(int(limit), 500))]

    def list_tasks(
        self,
        task_list: DiscoveredCollection,
        *,
        include_completed: bool = False,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        return [
            task.to_dict()
            for task in self.list_task_objects(
                task_list,
                include_completed=include_completed,
                limit=limit,
            )
        ]

    def read_task(self, href: str, *, fallback_uid: str = "") -> TaskObject:
        response = self.client.request("GET", href, expected={200})
        task = self._parse_task_object(
            response.data.decode("utf-8", errors="replace"),
            href,
            self._header(response.headers, "ETag"),
        )
        if not task.uid and fallback_uid:
            return TaskObject(
                uid=fallback_uid,
                title=task.title,
                description=task.description,
                due=task.due,
                start=task.start,
                status=task.status,
                priority=task.priority,
                percent_complete=task.percent_complete,
                completed=task.completed,
                categories=task.categories,
                recurring=task.recurring,
                raw_ics=task.raw_ics,
                href=task.href,
                etag=task.etag,
            )
        return task

    def update_task(
        self,
        task_list: DiscoveredCollection,
        *,
        href: str,
        uid: str,
        ics: str,
        etag: str,
    ) -> TaskObject:
        safe_href = self._validated_task_href(task_list, href)
        current_etag = str(etag or "").strip()
        if not current_etag:
            current_etag = self.read_task(safe_href, fallback_uid=uid).etag
        if not current_etag:
            raise RuntimeError("CalDAV-Server lieferte keinen ETag; sicheres Aufgaben-Update abgebrochen")
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
                "Aufgabe wurde zwischenzeitlich geaendert; bitte erneut auflisten und die Aenderung wiederholen"
            )
        verified = self.read_task(safe_href, fallback_uid=uid)
        if verified.uid.strip() != uid.strip():
            raise RuntimeError("Aufgabe wurde aktualisiert, UID-Verifikation ist fehlgeschlagen")
        return verified

    @staticmethod
    def _validated_task_href(task_list: DiscoveredCollection, href: str) -> str:
        clean = str(href or "").strip()
        root = task_list.href.rstrip("/") + "/"
        if not clean or not clean.startswith(root) or clean == root:
            raise PermissionError("Aufgabenobjekt liegt ausserhalb der konfigurierten Aufgabenliste")
        return clean

    @classmethod
    def _parse_task_object(cls, ics: str, href: str, etag: str) -> TaskObject:
        values = cls._parse_vtodo(ics)
        props = component_properties(ics, "VTODO", str(values.get("uid") or ""))
        return TaskObject(
            uid=str(values.get("uid") or ""),
            title=str(values.get("title") or ""),
            description=str(values.get("description") or ""),
            due=str(values.get("due") or ""),
            start=str(values.get("start") or ""),
            status=str(values.get("status") or "NEEDS-ACTION").upper(),
            priority=int(values.get("priority") or 0),
            percent_complete=int(values.get("percent_complete") or 0),
            completed=str(values.get("completed") or ""),
            categories=tuple(str(value) for value in values.get("categories") or []),
            recurring=bool(props.get("RRULE") or props.get("RECURRENCE-ID")),
            raw_ics=ics,
            href=href,
            etag=str(etag or "").strip(),
        )

    @classmethod
    def _parse_vtodo(cls, ics: str) -> dict[str, Any]:
        props = component_properties(ics, "VTODO")
        priority = 0
        percent = 0
        try:
            priority = int(first_value(props, "PRIORITY", "0") or 0)
        except ValueError:
            pass
        try:
            percent = int(first_value(props, "PERCENT-COMPLETE", "0") or 0)
        except ValueError:
            pass
        categories = first_value(props, "CATEGORIES")
        return {
            "uid": unescape_ical(first_value(props, "UID")),
            "title": unescape_ical(first_value(props, "SUMMARY")),
            "description": unescape_ical(first_value(props, "DESCRIPTION")),
            "due": first_value(props, "DUE"),
            "start": first_value(props, "DTSTART"),
            "status": first_value(props, "STATUS", "NEEDS-ACTION"),
            "priority": priority,
            "percent_complete": percent,
            "completed": first_value(props, "COMPLETED"),
            "categories": [unescape_ical(v) for v in categories.split(",") if v],
        }

    @staticmethod
    def _header(headers, name: str) -> str:
        target = name.casefold()
        for key, value in dict(headers or {}).items():
            if str(key).casefold() == target:
                return str(value or "").strip()
        return ""

    @staticmethod
    def _unfold(ics: str) -> list[str]:
        result: list[str] = []
        for raw in ics.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
            if raw.startswith((" ", "\t")) and result:
                result[-1] += raw[1:]
            else:
                result.append(raw)
        return result

    @staticmethod
    def _unescape(value: str) -> str:
        return unescape_ical(value)
