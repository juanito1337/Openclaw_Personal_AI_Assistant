"""Managed WebDAV file access through the real connector, registry and service."""

from types import SimpleNamespace
from urllib.parse import quote, unquote, urlsplit
from xml.etree import ElementTree as ET

import pytest
from test_nextcloud_access import grant, managed, measured, scope, update_policy

from personal_assistant import nextcloud_connection as nc
from personal_assistant.config import AssistantConfig
from personal_assistant.connectors.nextcloud.client import DavResponse, NextcloudClient
from personal_assistant.connectors.nextcloud.files import NextcloudFiles
from personal_assistant.connectors.nextcloud.xmlutil import DAV, q
from personal_assistant.registry import ResourceRegistry
from personal_assistant.service import PersonalAssistant
from personal_assistant.tool_settings import ToolSettings

PREFIX = "/nc/remote.php/dav/files/assistant/"
OC = "http://owncloud.org/ns"
READ = ["list", "read", "search"]


class FileServer:
    def __init__(self):
        self.nodes = {
            "": ("1", "GCK", None),
            "Belege": ("11", "GCK", None),
            "Belege/Privat": ("12", "GCK", None),
            "BelegeAlt": ("13", "GCK", None),
            "Belege/Nachweis": ("21", "G", b"evidence"),
            "Belege/Ziel": ("22", "GCK", None),
        }
        self.calls = []
        self.extra_href = ""

    def request(self, method, href, headers, data, timeout, *, follow_redirects):
        assert not follow_redirects
        path = unquote(urlsplit(href).path)
        assert path.startswith(PREFIX)
        name = path[len(PREFIX) :].rstrip("/")
        self.calls.append((method, name, headers or {}))
        body = b""
        status = 200
        if method == "PROPFIND":
            if name not in self.nodes:
                status = 404
            else:
                status = 207
                entries = [name]
                if headers["Depth"] == "1":
                    entries += [k for k in self.nodes if k and k.rpartition("/")[0] == name]
                tree = ET.Element(q(DAV, "multistatus"))
                for entry in entries:
                    identifier, flags, content = self.nodes[entry]
                    node = ET.SubElement(tree, q(DAV, "response"))
                    ET.SubElement(node, q(DAV, "href")).text = (
                        PREFIX + quote(entry) + ("/" if content is None and entry else "")
                    )
                    propstat = ET.SubElement(node, q(DAV, "propstat"))
                    ET.SubElement(propstat, q(DAV, "status")).text = "HTTP/1.1 200 OK"
                    props = ET.SubElement(propstat, q(DAV, "prop"))
                    for prop in ET.fromstring(data).find(q(DAV, "prop")):
                        value = ET.SubElement(props, prop.tag)
                        if prop.tag == q(DAV, "resourcetype") and content is None:
                            ET.SubElement(value, q(DAV, "collection"))
                        value.text = {
                            q(OC, "fileid"): identifier,
                            q(OC, "permissions"): flags,
                            q(DAV, "getetag"): '"tag"',
                        }.get(prop.tag)
                if len(entries) > 1 and self.extra_href:
                    tree[-1].find(q(DAV, "href")).text = self.extra_href
                body = ET.tostring(tree)
        elif method == "GET":
            body = self.nodes[name][2]
        elif method in {"MKCOL", "PUT"}:
            assert name not in self.nodes, "unexpected write to an existing resource"
            assert headers["If-None-Match"] == "*"
            self.nodes[name] = ("99", "GCK", data)
            status = 201
        else:
            pytest.fail("Unexpected method " + method)
        return DavResponse(status, "", {}, body, "https://cloud.example" + path)


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    store = managed(tmp_path)
    conn = measured()
    for row in conn["discovery_snapshot"]["folders"]:
        row.update(
            can_mkdir=True,
            privileges_verified=True,
            file_permissions="GCK",
            checked_at=conn["discovery_snapshot"]["checked_at"],
        )
    conn["discovery_snapshot"]["file_roots"][0].update(
        path="",
        file_id="1",
        can_mkdir=True,
        can_create_file=True,
        read_check={"status": "ok"},
        privileges_verified=True,
        file_permissions="GCK",
        checked_at=conn["discovery_snapshot"]["checked_at"],
    )
    store.save(conn, expected_revision="")
    grants = [
        grant(scope(conn, "FILES", path, ident), ops)
        for path, ident, ops in [
            ("Belege", "11", READ + ["mkdir", "create_file"]),
            ("Belege/Privat", "12", []),
            ("BelegeAlt", "13", READ),
        ]
    ]
    update_policy(store, grants)
    monkeypatch.setenv("OPENCLAW_NEXTCLOUD_CONNECTION_DIR", str(store.directory))
    server = FileServer()

    def open_service():
        pa = PersonalAssistant.__new__(PersonalAssistant)
        pa.config = AssistantConfig()
        client = NextcloudClient(pa.config)
        monkeypatch.setattr(client, "_managed_request", server.request)
        pa.nextcloud_files = NextcloudFiles(pa.config, client)
        pa.registry = ResourceRegistry(tmp_path / "resources.toml")
        pa.tool_settings = nc.apply_tool_connection(ToolSettings(path=tmp_path / "tools.toml"), store.read())
        return pa

    return SimpleNamespace(pa=open_service(), open=open_service, server=server, store=store, conn=conn)


def test_multiple_entries_and_explicit_listing(workspace):
    pa, server = workspace.pa, workspace.server
    assert [item["path"] for item in pa.list_nextcloud_files()["items"]] == ["Belege", "BelegeAlt"]
    assert not any(path == "" or headers.get("Depth") == "1" for _, path, headers in server.calls)
    rows = pa.list_nextcloud_files("Belege")["items"]
    assert {r["path"] for r in rows} == {"Belege/Nachweis", "Belege/Ziel"}
    assert not any(path.startswith("Belege/Privat") for _, path, _ in server.calls)
    with pytest.raises(PermissionError):
        pa.list_nextcloud_files("Nachweis")


def test_folder_identity_rights_and_no_overwrite(workspace):
    files, server = workspace.pa.nextcloud_files, workspace.server
    assert files.download("Belege/Nachweis") == b"evidence"
    files.upload_new("Belege/new.txt", b"one")
    with pytest.raises((PermissionError, RuntimeError)):
        files.upload_new("Belege/new.txt", b"other")
    server.nodes["Belege/Ziel"] = ("22", "GC", None)
    files.upload_new("Belege/Ziel/new.txt", b"one")
    with pytest.raises(PermissionError, match="server-right-missing"):
        files.ensure_folder("Belege/Ziel/sub")
    server.nodes["Belege/Ziel"] = ("22", "GK", None)
    files.ensure_folder("Belege/Ziel/sub")
    with pytest.raises(PermissionError, match="server-right-missing"):
        files.upload_new("Belege/Ziel/other.txt", b"two")
    assert [p for m, p, _ in server.calls if m == "MKCOL"] == ["Belege/Ziel/sub"]
    server.nodes["Belege"] = ("33", "GCK", None)
    with pytest.raises(PermissionError, match="folder-identity-changed"):
        files.download("Belege/Nachweis")
    assert [p for m, p, _ in server.calls if m == "GET"] == [
        "Belege/Nachweis",
        "Belege/new.txt",
        "Belege/Ziel/new.txt",
    ]


@pytest.mark.parametrize(
    "bad",
    [
        "https://other.example" + PREFIX + "Belege/f",
        PREFIX + "BelegeAlt/f",
        PREFIX + "Belege/%2e%2e/f",
        PREFIX + "Belege/a%2fb",
        PREFIX + "Belege/Ziel/nested",
    ],
)
def test_reject_unsafe_returned_locator(workspace, bad):
    workspace.server.extra_href = bad
    with pytest.raises(PermissionError):
        workspace.pa.nextcloud_files.list_folder("Belege")


def test_off_and_unsupported_paths_do_not_send(workspace):
    files = workspace.pa.nextcloud_files
    for path in ["Belege/Privat/file", "Belege2/file", "Belege/../Privat/file", "Belege//file"]:
        with pytest.raises((PermissionError, ValueError)):
            files.download(path)
    with pytest.raises(PermissionError):
        files.upload_new("BelegeAlt/new", b"one")
    with pytest.raises(PermissionError):
        files.move_new("Belege/Nachweis", "Belege/new")
    assert not workspace.server.calls
    update_policy(workspace.store, [])
    with pytest.raises(PermissionError, match="policy-changed"):
        files.download("Belege/Nachweis")
    assert not workspace.server.calls


def test_explicit_account_root_and_narrower_selection(workspace):
    store, conn = workspace.store, workspace.conn
    previous = store.snapshot()
    root = grant(scope(conn, "FILES", "", "1"), READ)
    private = grant(scope(conn, "FILES", "Belege/Privat", "12"), [])
    payload = {
        **previous.connection,
        "access_policy": {**previous.policy.to_dict(), "grants": [root, private]},
    }
    params = dict(
        expected_revision=previous.connection["revision"],
        expected_policy_revision=previous.binding.policy_revision,
    )
    with pytest.raises(nc.ConnectionError, match="account-root-confirmation-required"):
        store.save(payload, **params)
    store.save(payload, **params, confirm_account_root=True)
    pa = workspace.open()
    assert {row["path"] for row in pa.list_nextcloud_files(max_depth=0)["items"]} == {"Belege", "BelegeAlt"}
    with pytest.raises(PermissionError, match="scope-off"):
        pa.nextcloud_files.download("Belege/Privat/note")


def test_remeasure_expired_evidence_without_saving_or_widening(workspace):
    conn = workspace.store.read()
    for row in conn["discovery_snapshot"]["folders"]:
        row["checked_at"] = "2001-01-01T00:00:00+00:00"
    workspace.store.save(conn, expected_revision=conn["revision"])
    files = workspace.open().nextcloud_files
    before = workspace.store.read()
    assert files.download("Belege/Nachweis") == b"evidence"
    with pytest.raises(PermissionError, match="operation-not-allowed"):
        files.upload_new("BelegeAlt/foo", b"abc")
    assert workspace.store.read() == before


def test_parent_read_revoked_blocks_child_content(workspace):
    server = workspace.server
    server.nodes["Belege/Ziel"] = ("22", "CK", None)
    server.nodes["Belege/Ziel/note"] = ("24", "G", b"contents")
    with pytest.raises(PermissionError, match="server-right-missing"):
        workspace.pa.nextcloud_files.download("Belege/Ziel/note")
    assert not any(method == "GET" for method, _, _ in server.calls)


def test_nested_grant_under_off_does_not_enumerate_parent(workspace):
    conn = workspace.store.read()
    folders = conn["discovery_snapshot"]["folders"]
    folders.append({**folders[0], "path": "Belege/Ziel", "href": PREFIX + "Belege/Ziel/", "file_id": "22"})
    workspace.store.save(conn, expected_revision=conn["revision"])
    update_policy(
        workspace.store,
        [
            grant(scope(conn, "FILES", "Belege", "11"), []),
            grant(scope(conn, "FILES", "Belege/Ziel", "22"), READ + ["mkdir"]),
        ],
    )
    files = workspace.open().nextcloud_files
    files.ensure_folder("Belege/Ziel/sub")
    assert not any(path in {"", "Belege"} for _, path, _ in workspace.server.calls)
    with pytest.raises(PermissionError, match="scope-off"):
        files.list_folder("Belege")


def test_file_action_creates_only_its_exact_target(workspace, tmp_path):
    from dataclasses import replace

    from personal_assistant.actions import ActionService
    from personal_assistant.policy import PolicyEngine
    from personal_assistant.storage import AssistantStorage

    pa = workspace.pa
    tools = pa.tool_settings
    pa.tool_settings = replace(
        tools,
        nextcloud=replace(
            tools.nextcloud, workspace=replace(tools.nextcloud.workspace, outbox=tmp_path / "outbox")
        ),
    )
    storage = AssistantStorage(tmp_path / "work.sqlite")
    try:
        pa.policy = PolicyEngine(tmp_path / "policy.toml", pa.registry)
        pa.actions = ActionService(
            storage, pa.registry, pa.policy, pa.nextcloud_files.client, pa.nextcloud_files, None, None
        )
        assert pa.workspace_write_text("Belege/note.txt", "one")["ok"]
        assert workspace.server.nodes["Belege/note.txt"][2] == b"one"
        assert pa.workspace_write_text("Belege/note.txt", "one")["duplicate"]
        assert not pa.workspace_write_text("Belege/note.txt", "other")["ok"]
        assert not pa.workspace_write_text("Belege/missing/note.txt", "one")["ok"]
        assert not any(m == "MKCOL" for m, _, _ in workspace.server.calls)
        assert pa.workspace_mkdir("Belege/missing")["ok"]
        assert [p for m, p, _ in workspace.server.calls if m == "MKCOL"] == ["Belege/missing"]
        workspace.server.nodes["Belege/missing"] = ("99", "G", b"changed into file")
        conflict = pa.workspace_mkdir("Belege/missing")
        assert not conflict["ok"] and not conflict["duplicate"]
    finally:
        storage.close()


def test_mkdir_does_not_create_ancestors(workspace):
    with pytest.raises(PermissionError):
        workspace.pa.nextcloud_files.ensure_folder("Belege/missing/deeper")
    assert not any(m == "MKCOL" for m, _, _ in workspace.server.calls)


def test_file_and_folder_creation_require_matching_readback(workspace, monkeypatch):
    files, server = workspace.pa.nextcloud_files, workspace.server
    original = server.request

    def changed(method, href, headers, data, timeout, **kwargs):
        response = original(method, href, headers, data, timeout, **kwargs)
        if method in {"MKCOL", "PUT"}:
            path = urlsplit(href).path[len(PREFIX) :].rstrip("/")
            server.nodes[path] = ("99", "GCK", b"unexpected")
        return response

    monkeypatch.setattr(files.client, "_managed_request", changed)
    for action in (
        lambda: files.upload_new("Belege/neue.txt", b"approved"),
        lambda: files.ensure_folder("Belege/Ordner"),
    ):
        with pytest.raises(RuntimeError, match="delivery-uncertain"):
            action()
    assert sum(m == "PUT" for m, _, _ in server.calls) == 1
    assert sum(m == "MKCOL" for m, _, _ in server.calls) == 1


def test_action_refuses_modified_approved_upload(workspace, tmp_path):
    import hashlib

    from personal_assistant.actions import ActionService
    from personal_assistant.policy import PolicyEngine
    from personal_assistant.storage import AssistantStorage

    pa = workspace.pa
    storage = AssistantStorage(tmp_path / "plan.sqlite")
    try:
        actions = ActionService(
            storage,
            pa.registry,
            PolicyEngine(tmp_path / "policy.toml", pa.registry),
            pa.nextcloud_files.client,
            pa.nextcloud_files,
            None,
            None,
        )
        local = tmp_path / "outbox-payload"
        local.write_bytes(b"changed after approval")
        plan = actions.plan(
            "files.create",
            pa.tool_settings.nextcloud.workspace.resource_id,
            {
                "path": "Belege/receipt",
                "local_path": str(local),
                "overwrite": False,
                "sha256": hashlib.sha256(b"approved").hexdigest(),
                "workspace_tool": True,
            },
        )
        result = actions.execute(plan.id)
        assert result.status == "failed" and "payload-changed" in result.error
        assert not workspace.server.calls
    finally:
        storage.close()


def test_revocation_before_readback_sends_no_old_get(workspace, monkeypatch):
    files = workspace.pa.nextcloud_files
    download = files.download

    def revoked(path, **kwargs):
        update_policy(workspace.store, [])
        return download(path, **kwargs)

    monkeypatch.setattr(files, "download", revoked)
    with pytest.raises(RuntimeError, match="delivery-uncertain: policy-changed"):
        files.upload_new("Belege/new.txt", b"approved")
    assert workspace.server.nodes["Belege/new.txt"][2] == b"approved"
    assert workspace.server.calls[-1][0] == "PUT"


def test_typed_request_cannot_relabel_a_method_or_locator(workspace):
    from personal_assistant.nextcloud_file_access import METADATA, file_context

    files = workspace.pa.nextcloud_files
    client = files.client
    path = "Belege/new"
    context = file_context(client.access_snapshot, path, "create_file")
    for method, target, kwargs in (
        ("PUT", path, {"headers": {"If-Match": '"older"'}, "data": b"unapproved"}),
        ("MKCOL", path, {"headers": {"If-None-Match": "*"}}),
        ("PUT", "Belege/Privat/file", {"headers": {"If-None-Match": "*"}, "data": b"unapproved"}),
        ("PROPFIND", path, {"headers": {"Depth": "infinity"}, "data": METADATA}),
        ("MOVE", path, {}),
    ):
        with pytest.raises(PermissionError):
            client.request(method, PREFIX + target, access=context, **kwargs)
    assert not workspace.server.calls


def test_index_records_canonical_installation_uri(workspace, tmp_path):
    from personal_assistant.knowledge import KnowledgeIndexer
    from personal_assistant.nextcloud_index_access import ScopedSyncStorage
    from personal_assistant.storage import AssistantStorage

    pa = workspace.pa
    workspace.server.nodes["Belege/beleg.txt"] = ("19", "G", b"text")
    storage = AssistantStorage(tmp_path / "index.sqlite")
    try:
        resource = pa.tool_settings.nextcloud.workspace.resource_id
        snapshot = pa.nextcloud_files.client.access_snapshot
        (selected,) = (
            g.scope
            for g in snapshot.policy.grants
            if g.scope.resource_id == resource and g.scope.path == "Belege"
        )
        source = ScopedSyncStorage(storage, snapshot, selected)
        result = pa.nextcloud_files.sync_index(
            source,
            KnowledgeIndexer(pa.config, source),
            resource_id=resource,
            roots=("Belege",),
            max_items=10,
            max_depth=2,
        )
        assert result["complete"]
        assert storage.get_document(resource, "Belege/beleg.txt")["uri"] == (
            "https://cloud.example" + PREFIX + "Belege/beleg.txt"
        )
    finally:
        storage.close()


@pytest.mark.parametrize(
    "anomaly", ["double-status", "self-status", "mixed-permissions", "mixed-id", "mixed-type"]
)
@pytest.mark.parametrize("operation", ["get", "put"])
def test_conflicting_propstat_cannot_authorize_content_or_write(workspace, monkeypatch, anomaly, operation):
    from dataclasses import replace

    files = workspace.pa.nextcloud_files
    original = workspace.server.request

    def ambiguous(method, href, headers, data, timeout, **kwargs):
        response = original(method, href, headers, data, timeout, **kwargs)
        if method != "PROPFIND" or response.status != 207:
            return response
        tree = ET.fromstring(response.data)
        row = tree.find(q(DAV, "response"))
        if anomaly in {"double-status", "self-status"}:
            parent = row if anomaly == "self-status" else row.find(q(DAV, "propstat"))
        else:
            parent = ET.SubElement(row, q(DAV, "propstat"))
            props = ET.SubElement(parent, q(DAV, "prop"))
            ET.SubElement(
                props,
                {
                    "mixed-permissions": q(OC, "permissions"),
                    "mixed-id": q(OC, "fileid"),
                    "mixed-type": q(DAV, "resourcetype"),
                }[anomaly],
            )
        ET.SubElement(parent, q(DAV, "status")).text = "HTTP/1.1 403 Forbidden"
        return replace(response, data=ET.tostring(tree))

    monkeypatch.setattr(files.client, "_managed_request", ambiguous)
    with pytest.raises(PermissionError, match="rights-unverified"):
        if operation == "get":
            files.download("Belege/Nachweis")
        else:
            files.upload_new("Belege/new", b"approved")
    assert [method for method, _, _ in workspace.server.calls] == ["PROPFIND"]


def test_missing_optional_dav_property_preserves_witnessed_rights(workspace, monkeypatch):
    from dataclasses import replace

    files = workspace.pa.nextcloud_files
    original = workspace.server.request

    def without_displayname(method, href, headers, data, timeout, **kwargs):
        response = original(method, href, headers, data, timeout, **kwargs)
        if method != "PROPFIND" or headers["Depth"] != "1":
            return response
        tree = ET.fromstring(response.data)
        for row in tree.findall(q(DAV, "response")):
            props = row.find(q(DAV, "propstat")).find(q(DAV, "prop"))
            props.remove(props.find(q(DAV, "displayname")))
            stat = ET.SubElement(row, q(DAV, "propstat"))
            ET.SubElement(stat, q(DAV, "status")).text = "HTTP/1.1 404 Not Found"
            ET.SubElement(ET.SubElement(stat, q(DAV, "prop")), q(DAV, "displayname"))
        return replace(response, data=ET.tostring(tree))

    monkeypatch.setattr(files.client, "_managed_request", without_displayname)
    assert {r.name for r in files.list_folder("Belege")} == {"Nachweis", "Ziel"}


@pytest.mark.parametrize("published", ["binding", "connection-revision"])
def test_virtual_roots_revalidate_before_publication(workspace, monkeypatch, published):
    files = workspace.pa.nextcloud_files
    original = files.verified_entry

    def revoke_after_final_probe(path):
        original(path)
        if path == "BelegeAlt":
            if published == "binding":
                update_policy(workspace.store, [])
            else:
                conn = workspace.store.read()
                workspace.store.save(conn, expected_revision=conn["revision"])

    monkeypatch.setattr(files, "verified_entry", revoke_after_final_probe)
    with pytest.raises((PermissionError, RuntimeError), match="policy-changed") as error:
        workspace.pa.list_nextcloud_files()
    assert "Belege" not in str(error.value)


@pytest.mark.parametrize("rights", ["G", ""])
def test_stale_selected_paths_reachable_only_after_live_list_check(workspace, rights):
    from personal_assistant.tool_registry import build_tool_registry

    conn = workspace.store.read()
    for row in conn["discovery_snapshot"]["folders"]:
        row["checked_at"] = "2001-01-01T00:00:00+00:00"
    workspace.store.save(conn, expected_revision=conn["revision"])
    before = workspace.store.read()
    pa = workspace.open()
    resource = pa.registry.get(nc.account_namespace(conn) + "-files")
    assert not resource.permissions and not resource.metadata["folder_operations"]
    assert not pa.tool_settings.nextcloud.workspace.allow_upload
    visible = {tool.id for tool in build_tool_registry(pa.tool_settings)}
    assert "nextcloud.list" in visible
    assert not {"nextcloud.workspace.upload", "nextcloud.workspace.mkdir"} & visible
    workspace.server.nodes["Belege"] = ("11", rights, None)
    if rights:
        assert {row["path"] for row in pa.list_nextcloud_files("Belege", max_depth=0)["items"]} == {
            "Belege/Nachweis",
            "Belege/Ziel",
        }
    else:
        with pytest.raises(PermissionError, match="server-right-missing"):
            pa.list_nextcloud_files("Belege", max_depth=0)
        assert not any(headers.get("Depth") == "1" for _, _, headers in workspace.server.calls)
    assert workspace.store.read() == before


def test_managed_legacy_listing_discards_rows_when_all_off_activates(tmp_path, monkeypatch):
    store = nc.ConnectionStore(tmp_path / "mount")
    store.save(measured(), expected_revision="")
    state = tmp_path / "state"
    state.mkdir()
    nc.initialize_access_store(state, store.directory, transition="verified-pre-policy")
    monkeypatch.setenv("OPENCLAW_NEXTCLOUD_CONNECTION_DIR", str(store.directory))

    pa = PersonalAssistant.__new__(PersonalAssistant)
    pa.config = AssistantConfig()
    client = NextcloudClient(pa.config)
    snapshot = client.access_snapshot
    assert snapshot.managed and snapshot.mode == "legacy"
    pa.registry = ResourceRegistry(tmp_path / "resources.toml")
    pa.tool_settings = nc.apply_tool_connection(ToolSettings(path=tmp_path / "tools.toml"), snapshot)
    pa.nextcloud_files = NextcloudFiles(pa.config, client)
    server = FileServer()

    def send(method, href, headers, data, timeout, **kwargs):
        return server.request(method, "/nc" + href, headers, data, timeout, **kwargs)

    monkeypatch.setattr(client, "_managed_request", send)
    list_folder = pa.nextcloud_files.list_folder

    def activate_after_last_list(path):
        rows = list_folder(path)
        assert any(row.path == "Belege/Nachweis" for row in rows)
        store.save(
            {**snapshot.connection, "access_policy": nc.access.new_policy(snapshot.connection).to_dict()},
            expected_revision=snapshot.connection["revision"],
            expected_policy_revision="",
        )
        return rows

    monkeypatch.setattr(pa.nextcloud_files, "list_folder", activate_after_last_list)
    with pytest.raises((PermissionError, nc.ConnectionError), match="^policy-changed$"):
        pa.list_nextcloud_files("Belege", max_depth=0)
    assert store.snapshot().mode == "active" and not store.snapshot().policy.grants
    assert [method for method, _, _ in server.calls] == ["PROPFIND"]
