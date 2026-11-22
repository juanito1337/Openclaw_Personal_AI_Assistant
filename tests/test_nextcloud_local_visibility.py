"""Synthetic owner databases with real FTS, checkpoints and access-store transitions."""

import json

import pytest
from test_nextcloud_access import grant, managed, measured, scope, update_policy
from test_nextcloud_foreground_access import account as managed_account

from personal_assistant.nextcloud_access import Scope
from personal_assistant.storage import AssistantStorage

account = managed_account


@pytest.fixture
def selected(tmp_path, monkeypatch):
    store = managed(tmp_path)
    conn = measured()
    store.save(conn, expected_revision="")
    folder = scope(conn, "FILES", "Belege", "11")
    off = scope(conn, "FILES", "Belege/Privat", "12")
    snapshot = update_policy(store, [grant(folder, ["list", "read", "search"]), grant(off, [])])
    monkeypatch.setenv("OPENCLAW_NEXTCLOUD_CONNECTION_DIR", str(store.directory))
    storage = AssistantStorage(tmp_path / "knowledge.sqlite")
    yield storage, store, snapshot, Scope(**folder)
    storage.close()


def seed(storage, kind, resource, key, *, proof=None):
    storage.index_document(
        source_type=kind,
        resource_id=resource,
        source_id=key,
        uri="synthetic",
        title=key,
        chunks=["Needle"],
        modified_at=key,
    )
    if proof is not None:
        storage.knowledge_connection.execute(
            "UPDATE documents SET index_generation=? WHERE resource_id=? AND source_id=?",
            (json.dumps(proof), resource, key),
        )
        storage.knowledge_connection.commit()


@pytest.mark.parametrize("fts", [True, False])
def test_visibility_filters_before_limit_and_keeps_other_owner(selected, fts):
    storage, store, snap, folder = selected
    assert storage.fts_enabled
    storage.fts_enabled = fts

    def proof(path):
        return {"binding": snap.binding.to_dict(), "scope": folder.to_dict(), "target_path": path}

    seed(
        storage,
        "nextcloud-file",
        folder.resource_id,
        "Belege/Privat/needle",
        proof=proof("Belege/Privat/needle"),
    )
    seed(storage, "contact", "old-account", "zzz unbound")
    seed(storage, "nextcloud-file", folder.resource_id, "BelegeAlt/foreign", proof=proof("BelegeAlt/foreign"))
    seed(storage, "nextcloud-file", folder.resource_id, "Belege/Report", proof=proof("Belege/Report"))
    seed(storage, "email", "mail-agent", "aaa mail")
    for i in range(4):
        seed(storage, "calendar-event", "previous-account", f"zzz stale {i}", proof=proof("Belege/Report"))
    assert {r.source_id for r in storage.search("Needle", limit=2)} == {"Belege/Report", "aaa mail"}
    assert storage.search("Needle", resource_id="old-account") == []
    update_policy(store, [])
    assert [r.source_id for r in storage.search("Needle")] == ["aaa mail"]


def test_change_between_query_and_snippet_publication(selected, monkeypatch):
    storage, store, _, folder = selected
    seed(storage, "contact", folder.resource_id, "Before")
    storage.fts_enabled = False
    query = storage._search_like

    def restrict_after_fetch(*args):
        rows = query(*args)
        update_policy(store, [])
        return rows

    monkeypatch.setattr(storage, "_search_like", restrict_after_fetch)
    with pytest.raises(PermissionError, match="policy-changed"):
        storage.search("Needle")


def test_scoped_checkpoint_refresh_and_revocation(selected):
    from personal_assistant.nextcloud_index_access import ScopedSyncStorage

    storage, store, snap, folder = selected
    current = ScopedSyncStorage(storage, snap, folder)
    args = dict(
        resource_id=folder.resource_id,
        source_type="nextcloud-file",
        source_id="Belege/one",
        uri="test",
        title="one",
        chunks=["Needle"],
    )
    current.index_document(**args)
    current.upsert_sync_inventory(
        resource_id=folder.resource_id,
        scope="files",
        remote_id="Belege/one",
        source_id="Belege/one",
        source_type="nextcloud-file",
        etag="1",
    )
    current.set_sync_state(folder.resource_id, "files", cursor="cursor-one", status="in-progress")
    assert [r.source_id for r in storage.search("Needle")] == ["Belege/one"]
    assert current.get_sync_state(folder.resource_id, "files")["cursor"] == "cursor-one"

    new = update_policy(store, [grant(folder.to_dict(), ["list", "read", "search"])])
    new_view = ScopedSyncStorage(storage, new, folder)
    assert new_view.get_sync_state(folder.resource_id, "files") is None
    assert new_view.get_sync_inventory(folder.resource_id, "files", "Belege/one") is None
    assert new_view.get_document(folder.resource_id, "Belege/one") is None
    with pytest.raises(PermissionError, match="policy-changed"):
        current.reconcile_sync_inventory(resource_id=folder.resource_id, scope="files", remote_ids=set())
    assert storage.get_document(folder.resource_id, "Belege/one") is not None
    assert storage.search("Needle") == []


@pytest.fixture
def sync_service(selected, monkeypatch, tmp_path):
    from test_nextcloud_file_access import FileServer

    from personal_assistant.config import AssistantConfig
    from personal_assistant.connectors.nextcloud.client import NextcloudClient
    from personal_assistant.service import PersonalAssistant
    from personal_assistant.storage import AssistantStorage

    storage, store, snap, folder = selected
    server = FileServer()
    server.nodes["Belege/one.txt"] = ("55", "G", b"Needle one")
    server.nodes["Belege/two.txt"] = ("56", "G", b"Needle two")
    monkeypatch.setattr(NextcloudClient, "_managed_request", lambda self, *a, **kw: server.request(*a, **kw))
    config = AssistantConfig()
    config.runtime.database = tmp_path / "core.sqlite3"
    config.runtime.resources_file = tmp_path / "resources.toml"
    config.nextcloud.enabled = True
    AssistantStorage(config.runtime.database).close()
    monkeypatch.setenv("OPENCLAW_KNOWLEDGE_DATA_DIR", str(tmp_path / "worker"))
    monkeypatch.setenv("OPENCLAW_ROLE", "sync-worker")
    pa = PersonalAssistant(config)
    yield pa, server, store, snap, folder
    pa.storage.close()


def test_worker_syncs_only_selected_subtree_and_resumes(sync_service, monkeypatch):
    pa, server, store, snap, folder = sync_service
    monkeypatch.setenv("OPENCLAW_SYNC_BATCH_SIZE", "1")
    first = pa.sync_nextcloud()
    second = pa.sync_nextcloud()
    third = pa.sync_nextcloud()
    assert first["resume_required"] and second["resume_required"] and third["complete"]
    assert [p for method, p, _ in server.calls if method == "GET"] == [
        "Belege/Nachweis",
        "Belege/one.txt",
        "Belege/two.txt",
    ]
    assert all(
        p.startswith("Belege") and not p.startswith(("BelegeAlt", "Belege/Privat"))
        for _, p, _ in server.calls
    )
    assert {r.source_id for r in pa.storage.search("Needle")} == {"Belege/one.txt", "Belege/two.txt"}
    state = pa.storage.knowledge_connection.execute("SELECT * FROM sync_state").fetchone()
    assert json.loads(state["detail"])["nextcloud_source"]["binding"] == snap.binding.to_dict()


def test_worker_revocation_after_download_never_commits_or_fetches_next(sync_service, monkeypatch):
    pa, server, store, _, _ = sync_service
    initial_download = pa.nextcloud_files.download

    def download_and_revoke(*args, **kwargs):
        data = initial_download(*args, **kwargs)
        update_policy(store, [])
        return data

    monkeypatch.setattr(pa.nextcloud_files, "download", download_and_revoke)
    result = pa.sync_nextcloud()
    assert not result["complete"] and result["partial"] and "policy-changed" in str(result)
    assert len([p for m, p, _ in server.calls if m == "GET"]) == 1
    for table in ("documents", "sync_inventory", "sync_state"):
        assert pa.storage.knowledge_connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0


def test_cli_search_cannot_emit_results_after_revocation(selected, monkeypatch):
    from argparse import Namespace
    from types import SimpleNamespace

    from personal_assistant.cli_handlers.core import handle

    storage, store, snapshot, folder = selected
    row = {"binding": snapshot.binding.to_dict(), "scope": folder.to_dict(), "target_path": "Belege/one"}
    seed(storage, "nextcloud-file", folder.resource_id, "Belege/one", proof=row)
    search = storage.search

    def revoke_before_emit(*args, **kwargs):
        rows = search(*args, **kwargs)
        assert rows
        update_policy(store, [])
        return rows

    monkeypatch.setattr(storage, "search", revoke_before_emit)
    emitted = []
    with pytest.raises(PermissionError, match="policy-changed"):
        handle(
            Namespace(command="search", limit=1, query="Needle", source_type="", resource=""),
            SimpleNamespace(storage=storage),
            emitted.append,
        )
    assert emitted == []


def test_hidden_child_withdraws_projection_without_claiming_remote_deletion(sync_service):
    pa, server, _, _, folder = sync_service
    assert pa.sync_nextcloud()["complete"]
    assert len(pa.storage.search("Needle")) == 2
    identifier, _, body = server.nodes["Belege/one.txt"]
    server.nodes["Belege/one.txt"] = (identifier, "", body)
    result = pa.sync_nextcloud()
    assert result["files"]["sources"][0]["removed"] == 0
    assert [r.source_id for r in pa.storage.search("Needle")] == ["Belege/two.txt"]
    assert pa.storage.get_document(folder.resource_id, "Belege/one.txt") is not None


def test_replaced_folder_with_same_path_withholds_index_until_verified_again(sync_service):
    pa, server, _, _, folder = sync_service
    assert pa.sync_nextcloud()["complete"]
    assert len(pa.storage.search("Needle")) == 2
    server.nodes["Belege"] = ("999", "GCK", None)
    changed = pa.sync_nextcloud()
    assert changed["partial"] and "folder-identity-changed" in str(changed)
    assert pa.storage.search("Needle") == []
    assert pa.storage.get_document(folder.resource_id, "Belege/one.txt") is not None
    server.nodes["Belege"] = ("11", "GCK", None)
    assert pa.sync_nextcloud()["complete"]
    assert len(pa.storage.search("Needle")) == 2


def test_monitor_reports_current_source_only_without_copying_paths(sync_service):
    from datetime import UTC, datetime
    from types import SimpleNamespace

    from personal_assistant.monitoring import PerformanceMonitor

    pa, _, store, _, _ = sync_service
    assert pa.sync_nextcloud()["complete"]
    monitor = PerformanceMonitor.__new__(PerformanceMonitor)
    monitor.storage = pa.storage
    monitor.registry = SimpleNamespace(resources={}, duplicate_ids=[])

    def states():
        return monitor._assistant_metrics("", now=datetime.now(UTC))["sync_state"]

    before = states()
    assert len(before) == 1 and "Belege" not in str(before)
    update_policy(store, [])
    assert states() == []


def test_worker_syncs_one_component_in_mixed_collection(selected, tmp_path, monkeypatch):
    from test_nextcloud_foreground_access import report

    from personal_assistant.config import AssistantConfig
    from personal_assistant.connectors.nextcloud.client import DavResponse, NextcloudClient
    from personal_assistant.service import PersonalAssistant

    _, store, _, _ = selected
    conn = store.snapshot().connection
    ev = scope(conn)
    selected_event = update_policy(store, [grant(ev, ["list", "read", "search"])])
    href = conn["discovery_snapshot"]["calendars"][0]["href"]
    calls = []

    def request(client, method, target, headers, data, timeout, *, follow_redirects):
        assert target == href and method == "REPORT" and b"VEVENT" in data and b"VTODO" not in data
        if calls:
            from xml.etree import ElementTree as ET

            assert ET.fromstring(data).findtext(".//{urn:ietf:params:xml:ns:caldav}text-match") == "one"
        calls.append(method)
        return DavResponse(207, "", {}, report(href + "one.ics", "VEVENT"), "https://cloud.example" + href)

    monkeypatch.setattr(NextcloudClient, "_managed_request", request)
    config = AssistantConfig()
    config.runtime.database = tmp_path / "core.sqlite3"
    config.runtime.resources_file = tmp_path / "resources.toml"
    AssistantStorage(config.runtime.database).close()
    monkeypatch.setenv("OPENCLAW_KNOWLEDGE_DATA_DIR", str(tmp_path / "worker"))
    monkeypatch.setenv("OPENCLAW_ROLE", "sync-worker")
    pa = PersonalAssistant(config)
    try:
        result = pa.sync_nextcloud()
        assert result["complete"] and calls == ["REPORT", "REPORT"]
        assert [r.source_id for r in pa.storage.search("Synthetic")] == ["one"]
        evidence = result["calendar"]["sources"][0]
        assert (
            evidence["scope"]["component"] == "VEVENT"
            and evidence["binding"] == selected_event.binding.to_dict()
        )
    finally:
        pa.storage.close()


def test_same_account_reconnected_cannot_restore_old_index(selected):
    from personal_assistant.nextcloud_index_access import ScopedSyncStorage

    storage, store, snapshot, folder = selected
    scoped = ScopedSyncStorage(storage, snapshot, folder)
    scoped.index_document(
        source_type="nextcloud-file",
        resource_id=folder.resource_id,
        source_id="Belege/one",
        uri="test",
        title="one",
        chunks=["Needle"],
    )
    for user in ("other", "assistant"):
        before = store.snapshot()
        store.save(measured(user), expected_revision=before.connection["revision"])
        assert storage.search("Needle") == []
    update_policy(store, [grant(folder.to_dict(), ["list", "read", "search"])])
    assert storage.search("Needle", resource_id=folder.resource_id) == []
    with pytest.raises(PermissionError, match="policy-changed"):
        storage.search("Needle", visibility=snapshot)


def test_generic_index_generation_and_metadata_cannot_assign_nextcloud_provenance(selected):
    storage, _, snapshot, folder = selected
    proof = {"binding": snapshot.binding.to_dict(), "scope": folder.to_dict(), "target_path": "Belege/one"}
    storage.index_document(
        resource_id=folder.resource_id,
        source_type="nextcloud-file",
        source_id="Belege/one",
        uri="test",
        title="one",
        chunks=["Needle"],
        index_generation=json.dumps(proof),
        metadata=proof,
    )
    assert storage.search("Needle") == []


def test_incomplete_or_ambiguous_provenance_cannot_publish_component_rows(selected):
    storage, store, _, _ = selected
    event = scope(store.read())
    snapshot = update_policy(store, [grant(event, ["list", "read", "search"])])
    only_two_fields = {"component": "VEVENT", "resource_id": event["resource_id"]}
    proof = {"binding": snapshot.binding.to_dict(), "scope": only_two_fields, "target_path": None}
    seed(storage, "calendar-event", event["resource_id"], "event", proof=proof)
    assert storage.search("Needle") == []
    full = json.dumps({**proof, "scope": event})
    duplicate = '{"target_path": "forged", ' + full[1:]
    storage.knowledge_connection.execute("UPDATE documents SET index_generation=?", (duplicate,))
    storage.knowledge_connection.commit()
    assert storage.search("Needle") == []


@pytest.fixture
def collection_worker(tmp_path, monkeypatch, account):
    from test_nextcloud_foreground_access import report

    from personal_assistant.connectors.nextcloud.client import DavResponse
    from personal_assistant.connectors.nextcloud.contacts import NextcloudContacts

    pa = account.pa
    pa.role = "sync-worker"
    pa.storage = AssistantStorage(tmp_path / "worker.sqlite")
    pa.nextcloud_contacts = NextcloudContacts(pa.config, pa.nextcloud_client)
    book_id = pa.tool_settings.nextcloud.contacts.resource_id
    book = pa.registry.get(book_id).remote_id
    state = {"denied": "", "locator": "first.ics", "present": True}

    def transport(method, path, headers, data, timeout, *, follow_redirects):
        component = "VCARD" if path.startswith(book) else "VEVENT"
        if state["denied"] == component:
            assert method == "REPORT"
            return DavResponse(403, "", {}, b"", path)
        if component == "VEVENT":
            payload = (
                report(account.href + state["locator"], component)
                if state["present"]
                else b'<d:multistatus xmlns:d="DAV:"/>'
            )
        elif method == "REPORT":
            payload = f"""<d:multistatus xmlns:d="DAV:"><d:response><d:href>{book}one.vcf</d:href>
                <d:propstat><d:status>HTTP/1.1 200 OK</d:status><d:prop><d:getetag>"one"</d:getetag>
                </d:prop></d:propstat></d:response></d:multistatus>""".encode()
        else:
            payload = b"BEGIN:VCARD\nVERSION:3.0\nUID:card\nFN:Synthetic\nEND:VCARD"
        return DavResponse(207 if method == "REPORT" else 200, "", {"ETag": '"one"'}, payload, path)

    monkeypatch.setattr(pa.nextcloud_client, "_managed_request", transport)
    try:
        assert pa.sync_nextcloud()["complete"]
        yield pa, state
    finally:
        pa.storage.close()


@pytest.mark.parametrize(
    "component,denied_uid,visible_uid,domain",
    [("VEVENT", "one", "card", "calendar"), ("VCARD", "card", "one", "contacts")],
)
def test_worker_withholds_only_scope_denied_by_report(
    collection_worker, component, denied_uid, visible_uid, domain
):
    pa, state = collection_worker
    assert {r.source_id for r in pa.storage.search("Synthetic")} == {denied_uid, visible_uid}
    state["denied"] = component
    result = pa.sync_nextcloud()
    assert not result["complete"]
    assert result[domain]["sources"][0].get("reason") == "server-right-missing"
    assert {r.source_id for r in pa.storage.search("Synthetic")} == {visible_uid}
    state["denied"] = ""
    assert pa.sync_nextcloud()[domain]["sources"][0]["indexed"] == 1
    assert {r.source_id for r in pa.storage.search("Synthetic")} == {denied_uid, visible_uid}


def test_worker_rename_preserves_current_uid_but_historical_locator_cannot_retain_it(collection_worker):
    pa, state = collection_worker
    state["locator"] = "renamed.ics"
    assert pa.sync_nextcloud()["complete"]
    assert pa.sync_nextcloud()["calendar"]["sources"][0]["unchanged"] == 1
    assert {r.source_id for r in pa.storage.search("Synthetic")} == {"one", "card"}
    resource_id = pa.tool_settings.nextcloud.calendar.resource_id
    pa.storage.upsert_sync_inventory(
        resource_id=resource_id,
        scope="older-binding",
        remote_id="obsolete.ics",
        source_id="one",
        source_type="calendar-event",
        etag="old",
    )
    state["present"] = False
    assert pa.sync_nextcloud()["complete"]
    assert {r.source_id for r in pa.storage.search("Synthetic")} == {"card"}
