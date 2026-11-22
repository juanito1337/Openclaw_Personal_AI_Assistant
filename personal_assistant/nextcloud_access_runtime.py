"""Bind individual DAV operations to the selected component and current account."""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from xml.etree import ElementTree as ET

from .connectors.nextcloud.xmlutil import CALDAV, CARDDAV, DAV, parse_multistatus, q
from .ical_edit import unescape_ical, unfold_ical
from .nextcloud_access import COLLECTIONS, Binding, Scope, _url_path, decide_access
from .nextcloud_connection import collection_resource_id, load_access_snapshot

DAV_PRINCIPAL_PROBE = (
    b"<?xml version='1.0'?><d:propfind xmlns:d='DAV:'><d:prop>"
    b"<d:current-user-principal/></d:prop></d:propfind>"
)


@dataclass(frozen=True, slots=True)
class AccessContext:
    binding: Binding
    scope: Scope
    operation: str
    target_path: str | None = None
    object_uid: str = ""


def delivery_uncertain(error):
    code = str(error)
    if code not in {
        "policy-changed",
        "policy-integrity-error",
        "scope-off",
        "rights-stale",
        "rights-unverified",
        "server-right-missing",
        "folder-identity-changed",
    }:
        code = "postcondition-unverified"
    return RuntimeError("delivery-uncertain: " + code)


_WRITTEN_FIELDS = {
    "VCARD": {"UID", "FN", "N", "EMAIL", "TEL", "ORG", "NOTE"},
    "VEVENT": {
        "UID",
        "RECURRENCE-ID",
        "RRULE",
        "DTSTART",
        "DTEND",
        "DURATION",
        "SUMMARY",
        "DESCRIPTION",
        "LOCATION",
        "STATUS",
    },
    "VTODO": {
        "UID",
        "RECURRENCE-ID",
        "RRULE",
        "DTSTART",
        "DUE",
        "SUMMARY",
        "DESCRIPTION",
        "STATUS",
        "PERCENT-COMPLETE",
        "COMPLETED",
        "PRIORITY",
        "CATEGORIES",
    },
}


def _written_fields(raw, component):
    """Compare direct properties per recurrence instance, independent of folding or property order."""
    stack = []
    objects: list[list[tuple[str, tuple[str, ...], str]]] = []
    for line in unfold_ical(raw):
        key, _, value = line.partition(":")
        if key.upper() == "BEGIN":
            stack.append(value.upper())
            if stack[-1] == component:
                objects.append([])
        elif key.upper() == "END":
            stack.pop()
        elif stack and stack[-1] == component:
            name, *params = key.split(";")
            # Preserve parameter values including date vs date-time and TZID.
            name = name.rsplit(".", 1)[-1].upper()
            if name in _WRITTEN_FIELDS[component]:
                objects[-1].append((name, tuple(sorted(params)), value))
    return sorted(sorted(properties) for properties in objects)


def matches_written_object(result, uid, component, submitted):
    actual = result.raw if component == "VCARD" else result.raw_ics
    return (
        result.uid == uid
        and bool(re.fullmatch(r'"[^"\x00-\x20\x7f]+"', result.etag or ""))
        and _written_fields(submitted, component) == _written_fields(actual, component)
    )


def readback_after_write(client, reader, href, uid, component, submitted):
    initial = getattr(client, "access_snapshot", None)
    if initial is None or initial.mode != "active":
        return reader(href, fallback_uid=uid)
    try:
        result = reader(href, fallback_uid=uid)
        require(matches_written_object(result, uid, component, submitted))
        return result
    except Exception as exc:
        raise delivery_uncertain(exc) from exc


def require(condition, code="scope-invalid"):
    if not condition:
        raise PermissionError(code)


def segments(connection, href):
    try:
        return _url_path(connection, href)
    except (TypeError, ValueError, KeyError):
        raise PermissionError("scope-invalid") from None


def component_request(client, component, operation, collection, method, href, *, object_uid="", **kwargs):
    snapshot = getattr(client, "access_snapshot", None)
    if snapshot is None or snapshot.mode == "legacy":
        return client.request(method, href, **kwargs)
    require(snapshot.mode == "active", "scope-unbound")
    if collection is None:
        target = segments(snapshot.connection, href)
        rows = snapshot.connection["discovery_snapshot"].get(COLLECTIONS[component], ())
        parents = [row for row in rows if segments(snapshot.connection, row["href"]) == target[:-1]]
        require(len(parents) == 1)
        resource_id = collection_resource_id(snapshot.connection, parents[0]["href"])
    else:
        resource_id = collection.resource_id
    access = AccessContext(snapshot.binding, Scope(resource_id, component), operation, object_uid=object_uid)
    return client.request(method, href, access=access, **kwargs)


def _object_uid(raw, component):
    """One resource can carry recurrence exceptions of the same type and UID."""
    lines = unfold_ical(raw)
    stack: list[str] = []
    root = "VCARD" if component == "VCARD" else "VCALENDAR"
    uids = []
    count = 0
    closed = False
    for line in lines:
        key, sep, value = line.partition(":")
        require(bool(sep) and not closed)
        prop = key.upper()
        name = value.upper()
        if prop == "BEGIN":
            if not stack:
                require(name == root)
            elif len(stack) == 1:
                require(root != "VCARD" and name in {component, "VTIMEZONE"})
            else:
                require(
                    (stack[-1], name)
                    in {(component, "VALARM"), ("VTIMEZONE", "DAYLIGHT"), ("VTIMEZONE", "STANDARD")}
                )
            stack.append(name)
            if name == component:
                count += 1
        elif prop == "END":
            require(stack and stack.pop() == name)
            closed = not stack
        else:
            require(bool(stack))
            if prop.split(";", 1)[0] == "UID" and stack[-1] == component:
                uids.append(value if component == "VCARD" else unescape_ical(value))
    require(closed and count > 0 and len(uids) == count and len(set(uids)) == 1 and uids[0].strip())
    return uids[0]


def check_component_write(client, collection, component, operation, uid, raw):
    initial = getattr(client, "access_snapshot", None)
    if initial is None or initial.mode == "legacy":
        return
    snapshot = load_access_snapshot()
    decision = decide_access(
        snapshot, Scope(collection.resource_id, component), operation, expected_binding=initial.binding
    )
    require(decision.allowed, decision.code)
    require(uid and _object_uid(raw, component) == uid)


def _leaf(element):
    return element is not None and len(element) == 0 and not element.attrib


def _report_shape(data, component, operation):
    root = ET.fromstring(data or b"")
    is_card = component == "VCARD"
    namespace = CARDDAV if is_card else CALDAV
    require(root.tag == q(namespace, "addressbook-query" if is_card else "calendar-query"))
    children = list(root)
    prop = root.find(q(DAV, "prop"))
    if prop is None:
        raise PermissionError("scope-invalid")
    require(not prop.attrib and children and children[0] is prop)
    payload_tag = q(namespace, "address-data" if is_card else "calendar-data")
    require(len(prop) in (1, 2) and prop[0].tag == q(DAV, "getetag"))
    require(_leaf(prop[0]))
    if not is_card:
        require(len(prop) == 2, "rights-unverified")
    if len(prop) == 2:
        payload = prop[1]
        require(payload.tag == payload_tag and not payload.attrib)
        if not _leaf(payload):
            require(component == "VEVENT" and len(payload) == 1)
            envelope = payload[0]
            require(
                envelope.tag == q(CALDAV, "comp")
                and envelope.attrib == {"name": "VCALENDAR"}
                and len(envelope) == 1
            )
            event = envelope[0]
            require(
                event.tag == q(CALDAV, "comp") and event.attrib == {"name": component} and len(event) == 1
            )
            field = event[0]
            require(field.tag == q(CALDAV, "prop") and field.attrib == {"name": "UID"} and not len(field))
    if is_card:
        require(len(children) == 1)
    else:
        require(len(children) == 2 and children[1].tag == q(CALDAV, "filter"))
        node = children[1]
        for name in ("VCALENDAR", component):
            require(
                len(node) == 1
                and node[0].tag == q(CALDAV, "comp-filter")
                and node[0].attrib == {"name": name}
            )
            node = node[0]
        require(len(node) <= 1)
        if len(node):
            term = node[0]
            if term.tag == q(CALDAV, "time-range"):
                require(
                    len(term) == 0
                    and set(term.attrib) == {"start", "end"}
                    and all(re.fullmatch(r"\d{8}T\d{6}Z", v) for v in term.attrib.values())
                )
            else:
                require(
                    term.tag == q(CALDAV, "prop-filter")
                    and term.attrib == {"name": "UID"}
                    and len(term) == 1
                    and term[0].tag == q(CALDAV, "text-match")
                    and term[0].attrib == {"collation": "i;octet"}
                    and len(term[0]) == 0
                )
    if operation != "list":
        uid_filter = root.find(".//" + q(CALDAV, "prop-filter"))
        require(not is_card and uid_filter is not None and bool(uid_filter[0].text), "operation-not-allowed")
    return {item.tag for item in prop}


def _verified_multistatus(data, allowed_props):
    root = ET.fromstring(data)
    require(root.tag == q(DAV, "multistatus") and not root.attrib and len(root) <= 10000)
    for response in root:
        require(response.tag == q(DAV, "response") and not response.attrib)
        hrefs = response.findall(q(DAV, "href"))
        propstats = response.findall(q(DAV, "propstat"))
        require(len(hrefs) == 1 and _leaf(hrefs[0]) and propstats and len(response) == 1 + len(propstats))
        seen = set()
        for entry in propstats:
            status = entry.find(q(DAV, "status"))
            props = entry.find(q(DAV, "prop"))
            if status is None or props is None:
                raise PermissionError("scope-invalid")
            require(len(entry) == 2 and _leaf(status))
            require(re.fullmatch(r"HTTP/\d+(?:\.\d+)? 200(?: [^\r\n]*)?", status.text or ""))
            for prop in props:
                require(prop.tag in allowed_props and prop.tag not in seen)
                seen.add(prop.tag)
                if prop.tag == q(CALDAV, "supported-calendar-component-set"):
                    require(
                        all(
                            c.tag == q(CALDAV, "comp") and len(c) == 0 and set(c.attrib) == {"name"}
                            for c in prop
                        )
                    )
                else:
                    require(_leaf(prop))
    return parse_multistatus(data)


def _response_items(response, snapshot, scope, parent, allowed_props):
    rows = _verified_multistatus(response.data, allowed_props)
    payload_tag = q(CARDDAV, "address-data") if scope.component == "VCARD" else q(CALDAV, "calendar-data")
    seen = set()
    for row in rows:
        target = segments(snapshot.connection, row.href)
        require(target[:-1] == parent and target not in seen)
        seen.add(target)
        require(set(row.properties) == allowed_props, "rights-unverified")
        if payload_tag in row.properties:
            _object_uid(row.properties[payload_tag], scope.component)
    return rows


def _component_query(component, uid):
    require(isinstance(uid, str) and uid.strip(), "scope-invalid")
    root = ET.Element(q(CALDAV, "calendar-query"))
    prop = ET.SubElement(root, q(DAV, "prop"))
    ET.SubElement(prop, q(DAV, "getetag"))
    ET.SubElement(prop, q(CALDAV, "calendar-data"))
    filt = ET.SubElement(root, q(CALDAV, "filter"))
    outer = ET.SubElement(filt, q(CALDAV, "comp-filter"), {"name": "VCALENDAR"})
    inner = ET.SubElement(outer, q(CALDAV, "comp-filter"), {"name": component})
    filt = ET.SubElement(inner, q(CALDAV, "prop-filter"), {"name": "UID"})
    ET.SubElement(filt, q(CALDAV, "text-match"), {"collation": "i;octet"}).text = uid
    return ET.tostring(root, encoding="utf-8")


def _calendar_locator(snapshot, scope, collection, href, send, *, with_uid):
    """Only a resource-level 404 on the exact href proves that this child is absent."""
    target = segments(snapshot.connection, href)
    etag = q(DAV, "getetag")
    properties = {etag}
    root = ET.Element(q(CALDAV, "calendar-multiget"))
    prop = ET.SubElement(root, q(DAV, "prop"))
    ET.SubElement(prop, etag)
    if with_uid:
        payload = q(CALDAV, "calendar-data")
        properties.add(payload)
        data = ET.SubElement(prop, payload)
        envelope = ET.SubElement(data, q(CALDAV, "comp"), {"name": "VCALENDAR"})
        component = ET.SubElement(envelope, q(CALDAV, "comp"), {"name": scope.component})
        ET.SubElement(component, q(CALDAV, "prop"), {"name": "UID"})
    ET.SubElement(root, q(DAV, "href")).text = href
    response = send(
        "REPORT", collection, headers={"Depth": "0", "Content-Type": "application/xml"},
        data=ET.tostring(root, encoding="utf-8"),
    )
    require(response.status == 207, "server-right-missing" if response.status == 403 else "rights-unverified")
    reply = ET.fromstring(response.data)
    require(reply.tag == q(DAV, "multistatus") and not reply.attrib and len(reply) == 1, "rights-unverified")
    item = reply[0]
    require(item.tag == q(DAV, "response") and not item.attrib)
    hrefs = item.findall(q(DAV, "href"))
    require(len(hrefs) == 1 and _leaf(hrefs[0]) and segments(snapshot.connection, hrefs[0].text) == target)
    status = item.find(q(DAV, "status"))
    if status is not None:
        require(
            len(item) == 2 and _leaf(status)
            and re.fullmatch(r"HTTP/\d+(?:\.\d+)? 404(?: [^\r\n]*)?", status.text or ""),
            "rights-unverified",
        )
        return replace(response, status=404, data=b"", headers={}), None
    rows = _response_items(response, snapshot, scope, target[:-1], properties)
    require(len(rows) == 1 and rows[0].properties.get(etag), "rights-unverified")
    return response, rows[0]


def _read_calendar_object(snapshot, scope, collection, href, send, uid=""):
    target = segments(snapshot.connection, href)
    parent = target[:-1]
    payload = q(CALDAV, "calendar-data")
    etag = q(DAV, "getetag")
    headers = {"Depth": "1", "Content-Type": "application/xml"}
    prior_etag = None
    if not uid:
        response, row = _calendar_locator(snapshot, scope, collection, href, send, with_uid=True)
        if row is None:
            return response
        uid = _object_uid(row.properties[payload], scope.component)
        prior_etag = row.properties[etag]
    response = send("REPORT", collection, headers=headers, data=_component_query(scope.component, uid))
    require(response.status == 207, "server-right-missing" if response.status == 403 else "rights-unverified")
    rows = _response_items(response, snapshot, scope, parent, {etag, payload})
    matches = [r for r in rows if _object_uid(r.properties[payload], scope.component) == uid]
    if not matches:
        require(not rows, "rights-unverified")
        # An empty UID search says nothing about the object occupying this href.
        response, row = _calendar_locator(snapshot, scope, collection, href, send, with_uid=False)
        require(row is None, "target-mismatch")
        return response
    require(len(matches) == 1 and segments(snapshot.connection, matches[0].href) == target)
    row = matches[0]
    require(row.properties[etag], "rights-unverified")
    require(prior_etag is None or prior_etag == row.properties[etag], "object-changed")
    return replace(
        response, status=200, headers={"ETag": row.properties[etag]}, data=row.properties[payload].encode()
    )


def dispatch_component(snapshot, access, method, href, headers, data, send):
    scope = access.scope
    operation = access.operation
    decision = decide_access(
        snapshot, scope, operation, target_path=access.target_path, expected_binding=access.binding
    )
    require(decision.allowed, decision.code)
    rows = snapshot.connection["discovery_snapshot"][COLLECTIONS[scope.component]]
    collection = next(
        row["href"]
        for row in rows
        if collection_resource_id(snapshot.connection, row["href"]) == scope.resource_id
    )
    parent = segments(snapshot.connection, collection)
    target = segments(snapshot.connection, href)
    child = target[:-1] == parent
    headers = dict(headers or {})
    allowed_headers = {"Depth", "Content-Type", "If-Match", "If-None-Match"}
    require(not (set(headers) - allowed_headers))
    if method == "REPORT":
        require(target == parent and operation in {"list", "read", "search"} and headers.get("Depth") == "1")
        props = _report_shape(data, scope.component, operation)
        response = send(method, href, headers=headers, data=data)
        require(response.status >= 300 or response.status == 207, "rights-unverified")
        if response.status == 207:
            _response_items(response, snapshot, scope, parent, props)
        return response
    if method == "PROPFIND":
        require(operation == "read" and headers.get("Depth") == "0")
        root = ET.fromstring(data or b"")
        require(
            root.tag == q(DAV, "propfind")
            and len(root) == 1
            and root[0].tag == q(DAV, "prop")
            and len(root[0]) == 1
            and _leaf(root[0][0])
        )
        tag = root[0][0].tag
        require(
            scope.component == "VTODO"
            and tag == q(CALDAV, "supported-calendar-component-set")
            and target == parent
        )
        response = send(method, href, headers=headers, data=data)
        require(response.status >= 300 or response.status == 207, "rights-unverified")
        if response.status == 207:
            items = _verified_multistatus(response.data, {tag})
            require(len(items) == 1 and segments(snapshot.connection, items[0].href) == target)
            require(set(items[0].properties) == {tag}, "rights-unverified")
            root = ET.fromstring(response.data)
            for advertised in root.iter(tag):
                for entry in list(advertised):
                    if entry.get("name") != scope.component:
                        advertised.remove(entry)
            response = replace(response, data=ET.tostring(root, encoding="utf-8"))
        return response
    require(child)
    if method == "GET":
        require(operation == "read" and data is None)
        if scope.component != "VCARD":
            return _read_calendar_object(snapshot, scope, collection, href, send, access.object_uid)
        response = send(method, href, headers=headers)
        require(response.status >= 300 or response.status == 200, "rights-unverified")
        if response.status == 200:
            _object_uid(response.data.decode("utf-8"), scope.component)
        return response
    require(method == "PUT" and operation in {"create", "update"}, "operation-not-allowed")
    mime = headers.get("Content-Type", "").split(";", 1)[0].lower()
    require(mime == ("text/vcard" if scope.component == "VCARD" else "text/calendar"))
    uid = _object_uid((data or b"").decode("utf-8"), scope.component)
    if operation == "create":
        require(headers.get("If-None-Match") == "*" and "If-Match" not in headers)
    else:
        etag = headers.get("If-Match", "")
        require(re.fullmatch(r'"[^"\x00-\x20\x7f]*"', etag) and "If-None-Match" not in headers)
        if scope.component == "VCARD":
            current = send("GET", href)
            require(
                current.status == 200 and _object_uid(current.data.decode("utf-8"), scope.component) == uid
            )
        else:
            current = _read_calendar_object(snapshot, scope, collection, href, send, uid)
        require(
            current.status == 200
            and any(k.lower() == "etag" and v == etag for k, v in current.headers.items()),
            "object-changed",
        )
    return replace(send(method, href, headers=headers, data=data), data=b"")
