"""Authorize one WebDAV file request and expose only selected directory entries."""

from __future__ import annotations

import re
from dataclasses import replace
from urllib.parse import quote, urlsplit
from xml.etree import ElementTree as ET

from .connectors.nextcloud.xmlutil import DAV, q
from .nextcloud_access import decide_access, folder_path, path_segments
from .nextcloud_access_runtime import AccessContext, require

OC = "http://owncloud.org/ns"
TYPE = q(DAV, "resourcetype")
ID = q(OC, "fileid")
FLAGS = q(OC, "permissions")
_METADATA = (TYPE, ID, FLAGS)
_LIST = _METADATA + tuple(
    q(DAV, name)
    for name in (
        "displayname",
        "getcontenttype",
        "getcontentlength",
        "getetag",
        "getlastmodified",
    )
)


def _propfind(names):
    root = ET.Element(q(DAV, "propfind"))
    props = ET.SubElement(root, q(DAV, "prop"))
    for name in names:
        ET.SubElement(props, name)
    return ET.tostring(root)


METADATA = _propfind(_METADATA)
LISTING = _propfind(_LIST)


def file_href(connection, path):
    prefix = urlsplit(connection["server_url"]).path.rstrip("/")
    return prefix + "/remote.php/dav/files/" + quote(connection["user_id"], safe="") + "/" + quote(path)


def file_context(snapshot, path, operation):
    if snapshot.mode == "legacy":
        return None
    require(snapshot.mode == "active" and snapshot.policy and snapshot.connection, "scope-unbound")
    segments = path_segments(path)
    selected = None
    for grant in snapshot.policy.grants:
        if grant.scope.component == "FILES":
            prefix = path_segments(grant.scope.path)
            if segments[: len(prefix)] == prefix and (
                selected is None or len(prefix) > len(path_segments(selected.path))
            ):
                selected = grant.scope
    if selected is None:
        raise PermissionError("scope-off")
    decision = decide_access(snapshot, selected, operation, target_path=path)
    # The live guard measures the selected folder again on each request. A
    # stale timestamp alone cannot prevent a new measurement of a local grant.
    require(decision.allowed or decision.code == "rights-stale", decision.code)
    return AccessContext(snapshot.binding, selected, operation, path)


def _path(connection, href):
    try:
        return folder_path(connection, href)
    except (ValueError, TypeError, KeyError):
        raise PermissionError("scope-invalid") from None


def _rows(connection, response, names):
    try:
        root = ET.fromstring(response.data)
    except ET.ParseError:
        raise PermissionError("rights-unverified") from None
    require(root.tag == q(DAV, "multistatus"))
    rows = {}
    for row in root.findall(q(DAV, "response")):
        hrefs = row.findall(q(DAV, "href"))
        require(len(hrefs) == 1 and not len(hrefs[0]))
        path = _path(connection, hrefs[0].text)
        require(path not in rows)
        # A property response uses propstat blocks. A separate whole-resource
        # status alongside them is contradictory and cannot establish rights.
        require(row.find(q(DAV, "status")) is None, "rights-unverified")
        props = {}
        observed = set()
        for stat in row.findall(q(DAV, "propstat")):
            statuses = stat.findall(q(DAV, "status"))
            groups = stat.findall(q(DAV, "prop"))
            require(len(statuses) == 1 and not len(statuses[0]) and len(groups) == 1, "rights-unverified")
            status = re.fullmatch(r"HTTP/\d(?:\.\d)? ([1-5]\d\d)(?: [^\r\n]*)?", statuses[0].text or "")
            if status is None:
                raise PermissionError("rights-unverified")
            for prop in groups[0]:
                if prop.tag in names:
                    require(prop.tag not in observed, "rights-unverified")
                    observed.add(prop.tag)
                    if status[1] == "200":
                        props[prop.tag] = prop
        rows[path] = props
    return rows


def _facts(props):
    require(set(_METADATA).issubset(props), "rights-unverified")
    kind, identifier, flags = (props[name] for name in _METADATA)
    require(
        not len(identifier)
        and not len(flags)
        and re.fullmatch(r"[1-9][0-9]{0,31}", identifier.text or "")
        and re.fullmatch(r"[A-Z]{0,64}", flags.text or ""),
        "rights-unverified",
    )
    require(
        not len(kind) or (len(kind) == 1 and kind[0].tag == q(DAV, "collection") and not len(kind[0])),
        "rights-unverified",
    )
    return bool(len(kind)), identifier.text, flags.text or ""


def _probe(connection, path, send):
    response = send("PROPFIND", file_href(connection, path), headers={"Depth": "0"}, data=METADATA)
    if response.status == 404:
        return None
    require(response.status == 207, "rights-unverified")
    rows = _rows(connection, response, _METADATA)
    require(set(rows) == {path}, "rights-unverified")
    return _facts(rows[path])


def guard_file_request(snapshot, access, method, href, headers, data, send):
    path, operation = access.target_path, access.operation
    conn = snapshot.connection
    require(_path(conn, href) == path)
    require(file_context(snapshot, path, operation) == access)
    headers = dict(headers or {})
    listing = method == "PROPFIND" and headers == {"Depth": "1"} and data == LISTING and operation == "list"
    metadata = (
        method == "PROPFIND"
        and headers == {"Depth": "0"}
        and data == METADATA
        and operation in {"list", "read"}
    )
    reading = method == "GET" and operation == "read" and data is None and set(headers) <= {"If-Match"}
    upload = (
        method == "PUT"
        and operation == "create_file"
        and isinstance(data, bytes)
        and set(headers) <= {"If-None-Match", "Content-Type", "Content-Length"}
        and headers.get("If-None-Match") == "*"
        and headers.get("Content-Length", str(len(data))) == str(len(data))
    )
    mkdir = method == "MKCOL" and operation == "mkdir" and headers == {"If-None-Match": "*"} and data is None
    require(listing or metadata or reading or upload or mkdir, "operation-not-allowed")
    scope_path = access.scope.path
    selected = _probe(conn, scope_path, send)
    require(selected and selected[0] and selected[1] == access.scope.file_id, "folder-identity-changed")
    required_flag = "C" if upload else "K" if mkdir else "G"
    require("G" in selected[2] and required_flag in selected[2], "server-right-missing")
    if path != scope_path:
        parent = path.rpartition("/")[0]
        evidence = selected if parent == scope_path else _probe(conn, parent, send)
        require(evidence and evidence[0], "rights-unverified")
        require("G" in evidence[2] and required_flag in evidence[2], "server-right-missing")
    if upload or mkdir:
        require(scope_path != path, "folder-identity-changed")
        require(_probe(conn, path, send) is None, "target-exists")
    elif listing or reading:
        target = selected if path == scope_path else _probe(conn, path, send)
        require(target and target[0] == listing, "rights-unverified")
        require("G" in target[2], "server-right-missing")


def filter_file_response(snapshot, access, method, response, *, headers):
    if method == "GET" and response.status == 200:
        return response
    if method != "PROPFIND" or response.status != 207:
        return replace(response, data=b"")
    target = access.target_path
    rows = _rows(snapshot.connection, response, _METADATA if headers["Depth"] == "0" else _LIST)
    require(target in rows, "rights-unverified")
    if headers["Depth"] == "0":
        require(set(rows) == {target})
    tree = ET.Element(q(DAV, "multistatus"))
    for path, props in rows.items():
        if path != target:
            require(access.operation == "list" and path and path.rpartition("/")[0] == target)
            try:
                ctx = file_context(snapshot, path, "list")
            except PermissionError:
                continue
        else:
            ctx = access
        folder, identifier, flags = _facts(props)
        if "G" not in flags:
            require(path != target, "server-right-missing")
            continue
        if path == ctx.scope.path:
            require(folder and identifier == ctx.scope.file_id, "folder-identity-changed")
        # Rebuild the projection so unrequested properties and extra response
        # elements cannot disclose data through the direct client entry point.
        row = ET.SubElement(tree, q(DAV, "response"))
        ET.SubElement(row, q(DAV, "href")).text = file_href(snapshot.connection, path)
        stat = ET.SubElement(row, q(DAV, "propstat"))
        ET.SubElement(stat, q(DAV, "status")).text = "HTTP/1.1 200 OK"
        clean = ET.SubElement(stat, q(DAV, "prop"))
        for tag, source in props.items():
            dest = ET.SubElement(clean, tag)
            if tag == TYPE:
                if folder:
                    ET.SubElement(dest, q(DAV, "collection"))
            else:
                require(not len(source))
                dest.text = source.text
    return replace(response, data=ET.tostring(tree))
