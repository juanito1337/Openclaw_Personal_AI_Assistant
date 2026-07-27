from __future__ import annotations

from dataclasses import dataclass
from xml.etree import ElementTree


DAV = "DAV:"
CALDAV = "urn:ietf:params:xml:ns:caldav"
CARDDAV = "urn:ietf:params:xml:ns:carddav"
NC = "http://nextcloud.org/ns"
CS = "http://calendarserver.org/ns/"


@dataclass(slots=True, frozen=True)
class MultiStatusItem:
    href: str
    properties: dict[str, str]
    raw_properties: dict[str, ElementTree.Element]


def parse_multistatus(data: bytes) -> list[MultiStatusItem]:
    root = ElementTree.fromstring(data)
    result: list[MultiStatusItem] = []
    for response in root.findall(f"{{{DAV}}}response"):
        href = response.findtext(f"{{{DAV}}}href") or ""
        properties: dict[str, str] = {}
        raw: dict[str, ElementTree.Element] = {}
        for propstat in response.findall(f"{{{DAV}}}propstat"):
            status = propstat.findtext(f"{{{DAV}}}status") or ""
            if " 200 " not in status:
                continue
            prop = propstat.find(f"{{{DAV}}}prop")
            if prop is None:
                continue
            for child in list(prop):
                key = child.tag
                properties[key] = "".join(child.itertext()).strip()
                raw[key] = child
        result.append(MultiStatusItem(href=href, properties=properties, raw_properties=raw))
    return result


def q(namespace: str, name: str) -> str:
    return f"{{{namespace}}}{name}"
