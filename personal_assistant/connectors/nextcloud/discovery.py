from __future__ import annotations

import hashlib
import urllib.parse
from dataclasses import dataclass
from xml.etree import ElementTree

from .client import NextcloudClient
from .xmlutil import CALDAV, CARDDAV, DAV, parse_multistatus, q


@dataclass(slots=True, frozen=True)
class DiscoveredCollection:
    kind: str
    href: str
    name: str
    resource_id: str
    description: str = ""
    components: tuple[str, ...] = ()
    privileges: tuple[str, ...] = ()
    can_read: bool = False
    can_create: bool = False
    can_update: bool = False

    def supports(self, component: str) -> bool:
        return str(component or "").upper() in self.components


class NextcloudDiscovery:
    def __init__(self, client: NextcloudClient) -> None:
        self.client = client

    def root_health(self) -> dict[str, object]:
        status = self.client.status()
        response = self.client.request(
            "PROPFIND",
            "/remote.php/dav/",
            headers={"Depth": "0", "Content-Type": "application/xml; charset=utf-8"},
            data=b"<?xml version='1.0'?><d:propfind xmlns:d='DAV:'><d:prop><d:current-user-principal/></d:prop></d:propfind>",
            expected={207},
        )
        return {
            "ok": True,
            "server": status,
            "dav_status": response.status,
            "username": self.client.username,
        }

    def calendar_collections(self) -> list[DiscoveredCollection]:
        """Discover all CalDAV collections and their advertised capabilities.

        Nextcloud stores event calendars and task lists below the same CalDAV
        home. The supported component set is therefore authoritative: VEVENT
        means calendar, VTODO means task list, and a collection may support
        both. Discovery is read-only and never changes local configuration.
        """
        path = f"/remote.php/dav/calendars/{urllib.parse.quote(self.client.username, safe='')}/"
        body = b"""<?xml version='1.0' encoding='utf-8'?>
<d:propfind xmlns:d='DAV:' xmlns:cs='http://calendarserver.org/ns/' xmlns:c='urn:ietf:params:xml:ns:caldav'>
 <d:prop>
  <d:displayname/>
  <d:resourcetype/>
  <c:supported-calendar-component-set/>
  <d:current-user-privilege-set/>
  <cs:getctag/>
  <d:description/>
 </d:prop>
</d:propfind>"""
        response = self.client.request(
            "PROPFIND",
            path,
            data=body,
            headers={"Depth": "1", "Content-Type": "application/xml; charset=utf-8"},
            expected={207},
        )
        result: list[DiscoveredCollection] = []
        for item in parse_multistatus(response.data):
            resource_type = item.raw_properties.get(q(DAV, "resourcetype"))
            if resource_type is None or resource_type.find(q(CALDAV, "calendar")) is None:
                continue
            components = self._components(
                item.raw_properties.get(q(CALDAV, "supported-calendar-component-set"))
            )
            privileges = self._privileges(
                item.raw_properties.get(q(DAV, "current-user-privilege-set"))
            )
            name = item.properties.get(q(DAV, "displayname")) or urllib.parse.unquote(
                item.href.rstrip("/").split("/")[-1]
            )
            result.append(
                DiscoveredCollection(
                    kind="calendar",
                    href=item.href,
                    name=name,
                    resource_id="nextcloud-calendar-" + self._slug(item.href),
                    description=item.properties.get(q(DAV, "description"), ""),
                    components=components,
                    privileges=privileges,
                    can_read=self._can_read(privileges),
                    can_create=self._can_create(privileges),
                    can_update=self._can_update(privileges),
                )
            )
        return sorted(result, key=lambda value: (value.name.casefold(), value.href))

    def calendars(self) -> list[DiscoveredCollection]:
        """Return collections that advertise VEVENT support."""
        return [item for item in self.calendar_collections() if not item.components or item.supports("VEVENT")]

    def task_lists(self) -> list[DiscoveredCollection]:
        """Return collections that advertise VTODO support."""
        return [item for item in self.calendar_collections() if item.supports("VTODO")]

    def addressbooks(self) -> list[DiscoveredCollection]:
        path = f"/remote.php/dav/addressbooks/users/{urllib.parse.quote(self.client.username, safe='')}/"
        body = b"""<?xml version='1.0' encoding='utf-8'?>
<d:propfind xmlns:d='DAV:' xmlns:card='urn:ietf:params:xml:ns:carddav'>
 <d:prop><d:displayname/><d:resourcetype/><d:current-user-privilege-set/><d:description/></d:prop>
</d:propfind>"""
        response = self.client.request(
            "PROPFIND",
            path,
            data=body,
            headers={"Depth": "1", "Content-Type": "application/xml; charset=utf-8"},
            expected={207},
        )
        result: list[DiscoveredCollection] = []
        for item in parse_multistatus(response.data):
            raw = item.raw_properties.get(q(DAV, "resourcetype"))
            if raw is None or raw.find(q(CARDDAV, "addressbook")) is None:
                continue
            privileges = self._privileges(
                item.raw_properties.get(q(DAV, "current-user-privilege-set"))
            )
            name = item.properties.get(q(DAV, "displayname")) or urllib.parse.unquote(
                item.href.rstrip("/").split("/")[-1]
            )
            result.append(
                DiscoveredCollection(
                    kind="addressbook",
                    href=item.href,
                    name=name,
                    resource_id="nextcloud-addressbook-" + self._slug(item.href),
                    description=item.properties.get(q(DAV, "description"), ""),
                    privileges=privileges,
                    can_read=self._can_read(privileges),
                    can_create=self._can_create(privileges),
                    can_update=self._can_update(privileges),
                )
            )
        return sorted(result, key=lambda value: (value.name.casefold(), value.href))

    @staticmethod
    def _components(element: ElementTree.Element | None) -> tuple[str, ...]:
        if element is None:
            return ()
        values = {
            str(comp.attrib.get("name") or "").upper()
            for comp in element.iter(q(CALDAV, "comp"))
            if str(comp.attrib.get("name") or "").strip()
        }
        return tuple(sorted(values))

    @staticmethod
    def _privileges(element: ElementTree.Element | None) -> tuple[str, ...]:
        if element is None:
            return ()
        values: set[str] = set()
        for privilege in element.iter(q(DAV, "privilege")):
            for child in list(privilege):
                if child.tag.startswith("{") and "}" in child.tag:
                    namespace, local = child.tag[1:].split("}", 1)
                    values.add(f"{{{namespace}}}{local}")
                else:
                    values.add(child.tag)
        return tuple(sorted(values))

    @staticmethod
    def _local_privileges(privileges: tuple[str, ...]) -> set[str]:
        result: set[str] = set()
        for value in privileges:
            local = value.rsplit("}", 1)[-1] if "}" in value else value
            result.add(local.casefold())
        return result

    @classmethod
    def _can_read(cls, privileges: tuple[str, ...]) -> bool:
        values = cls._local_privileges(privileges)
        # Some servers omit current-user-privilege-set even though a successful
        # depth-1 PROPFIND proves collection visibility. In that case we report
        # read access conservatively as available.
        return not values or bool(values & {"all", "read"})

    @classmethod
    def _can_create(cls, privileges: tuple[str, ...]) -> bool:
        values = cls._local_privileges(privileges)
        return bool(values & {"all", "write", "bind"})

    @classmethod
    def _can_update(cls, privileges: tuple[str, ...]) -> bool:
        values = cls._local_privileges(privileges)
        return bool(values & {"all", "write", "write-content"})

    @staticmethod
    def _slug(value: str) -> str:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]
