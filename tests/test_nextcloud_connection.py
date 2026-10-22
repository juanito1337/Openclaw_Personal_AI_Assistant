from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from personal_assistant.models import Resource
from personal_assistant.nextcloud_connection import (
    ConnectionError,
    ConnectionStore,
    account_namespace,
    apply_registry_connection,
    apply_tool_connection,
    connection_resources,
    load_connection,
    public_connection,
)
from personal_assistant.registry import ResourceRegistry
from personal_assistant.service import PersonalAssistant
from personal_assistant.tool_settings import ToolSettings


def connection(user: str = "assistant") -> dict:
    first = {
        "href": f"/nc/remote.php/dav/calendars/{user}/a/",
        "name": "Personal",
        "can_read": True,
        "can_create": True,
        "can_update": True,
        "components": ["VEVENT", "VTODO"],
    }
    second = {
        "href": f"/nc/remote.php/dav/calendars/{user}/b/",
        "name": "Personal",
        "can_read": True,
        "can_create": False,
        "can_update": False,
        "components": ["VEVENT", "VTODO"],
    }
    return {
        "schema_version": 1,
        "revision": "fixture",
        "server_url": "https://cloud.example/nc",
        "login_name": "assistant@example.invalid",
        "app_password": "fixture-password-do-not-return",
        "user_id": user,
        "expected_ips": ["192.168.2.3"],
        "discovery_snapshot": {
            "user_id": user,
            "complete": True,
            "calendar_homes": [f"https://cloud.example/nc/remote.php/dav/calendars/{user}/"],
            "addressbook_homes": [f"https://cloud.example/nc/remote.php/dav/addressbooks/users/{user}/"],
            "calendars": [first, second],
            "task_lists": [first, second],
            "file_roots": [
                {"href": f"/nc/remote.php/dav/files/{user}/", "can_read": True, "can_create": True}
            ],
        },
        "talk": {"enabled": False, "room_tokens": [], "allowed_user_ids": []},
    }


def test_atomic_store_guard_redaction_and_previous_snapshot(tmp_path: Path) -> None:
    store = ConnectionStore(tmp_path / "connection")
    first = store.save(connection(), expected_revision="")
    assert first["connected"] is True
    assert "fixture-password" not in json.dumps(first)
    assert "app_password" not in json.dumps(first)
    assert (store.directory / "connection.json").stat().st_mode & 0o777 == 0o600
    with pytest.raises(ConnectionError, match="conflict"):
        store.save(connection("other"), expected_revision="")
    second = store.save(connection("other"), expected_revision=first["revision"])
    assert second["revision"] != first["revision"]
    assert store.read()["user_id"] == "other"
    assert json.loads((store.directory / "previous.json").read_text())["user_id"] == "assistant"


def test_store_rejects_symlink_corruption_and_insecure_permissions(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir(mode=0o700)
    (target / "connection.json").symlink_to(tmp_path / "missing")
    with pytest.raises(ConnectionError, match="unreadable"):
        load_connection(target)
    (target / "connection.json").unlink()
    (target / "connection.json").write_text("{}")
    (target / "connection.json").chmod(0o644)
    with pytest.raises(ConnectionError, match="permissions"):
        load_connection(target)
    (target / "connection.json").chmod(0o600)
    with pytest.raises(ConnectionError, match="schema"):
        load_connection(target)


def test_no_secret_search_without_explicit_store(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENCLAW_NEXTCLOUD_CONNECTION_DIR", raising=False)
    assert load_connection() is None
    assert public_connection(None) == {"connected": False, "revision": ""}


def test_account_scope_preserves_multiple_resources_and_server_rights() -> None:
    value = connection()
    resources = connection_resources(value)
    assert len(resources) == 3  # VEVENT/VTODO share collection identity.
    settings = apply_tool_connection(ToolSettings(path=Path("/unused")), value)
    assert len(settings.nextcloud.calendar.resource_ids) == 2
    assert not settings.nextcloud.calendar.resource_id
    assert settings.nextcloud.calendar.resource_ids == settings.nextcloud.tasks.resource_ids
    assert settings.nextcloud.workspace.root == ""
    assert settings.nextcloud.workspace.allow_move is False
    readonly = next(item for item in resources if item.remote_id.endswith("/b/"))
    assert readonly.permissions == ("read",)
    assert account_namespace(value) != account_namespace(connection("other"))
    assert account_namespace(value) != account_namespace({**value, "server_url": "https://other.example/nc"})


@pytest.mark.parametrize("base", ["https://cloud.example/nc", "https://cloud.example:443/nc"])
def test_dav_default_https_port_preserves_collection_identity_without_widening_origin(base: str) -> None:
    from personal_assistant.nextcloud_connection import collection_resource_id

    value = {**connection(), "server_url": base}
    path = "/nc/remote.php/dav/calendars/assistant/a/"
    expected = collection_resource_id(value, path)
    assert collection_resource_id(value, "https://cloud.example" + path) == expected
    assert collection_resource_id(value, "https://cloud.example:443" + path) == expected
    for origin in ("https://cloud.example:8443", "https://cloud.example:0", "https://other.example", "http://cloud.example:443"):
        with pytest.raises(ConnectionError, match="origin-mismatch"):
            collection_resource_id(value, origin + path)


def test_account_change_cannot_retarget_prior_registry_plan(tmp_path: Path) -> None:
    registry = ResourceRegistry(tmp_path / "resources.toml")
    registry.resources["mail"] = Resource(id="mail", kind="email-service", connector="mail")
    apply_registry_connection(registry, connection())
    old_ids = {item.id for item in registry.list() if item.connector == "nextcloud"}
    apply_registry_connection(registry, connection("other"))
    assert "mail" in registry.resources
    assert old_ids.isdisjoint(registry.resources)


def test_exact_resource_selection_prevents_cross_scope_and_restores_state(tmp_path: Path) -> None:
    assistant = PersonalAssistant.__new__(PersonalAssistant)
    assistant.registry = ResourceRegistry(tmp_path / "resources.toml")
    apply_registry_connection(assistant.registry, connection())
    assistant.tool_settings = apply_tool_connection(ToolSettings(path=tmp_path / "tools.toml"), connection())
    before = assistant.tool_settings
    readonly = next(item.id for item in assistant.registry.list() if item.remote_id.endswith("/b/"))
    with pytest.raises(ValueError, match="selection-required"):
        assistant.require_nextcloud_target("tasks")
    with (
        pytest.raises(PermissionError, match="not-authorized"),
        assistant.select_nextcloud_resource("tasks", "another-account"),
    ):
        pass
    with assistant.select_nextcloud_resource("tasks", readonly):
        assert assistant.tool_settings.nextcloud.tasks.resource_id == readonly
        assert assistant.tool_settings.nextcloud.tasks.allow_list
        assert not assistant.tool_settings.nextcloud.tasks.allow_update
        assistant.require_nextcloud_target("tasks")
    assert assistant.tool_settings is before


def test_multi_resource_listing_preserves_origin_and_reports_partial_failure(tmp_path: Path) -> None:
    assistant = PersonalAssistant.__new__(PersonalAssistant)
    assistant.registry = ResourceRegistry(tmp_path / "resources.toml")
    apply_registry_connection(assistant.registry, connection())
    assistant.tool_settings = apply_tool_connection(ToolSettings(path=tmp_path / "tools.toml"), connection())
    assistant.policy = SimpleNamespace(decide=lambda *args: SimpleNamespace(allowed=True))
    assistant.nextcloud_tasks = SimpleNamespace(
        list_tasks=lambda *args, **kwargs: [{"uid": "same", "title": "Test"}]
    )
    result = assistant.tasks_list(limit=10)
    assert result["complete"]
    assert len({row["resource_id"] for row in result["tasks"]}) == 2

    def fail_one(collection, **kwargs):
        if collection.href.endswith("/b/"):
            raise RuntimeError("upstream-secret-must-not-escape")
        return [{"uid": "same", "title": "Test"}]

    assistant.nextcloud_tasks.list_tasks = fail_one
    result = assistant.tasks_list(limit=10)
    assert not result["ok"] and not result["complete"]
    assert result["results_may_be_truncated"]
    assert len(result["tasks"]) == 1
    assert "upstream-secret" not in json.dumps(result)


def test_account_root_listing_and_writes_remain_scoped(tmp_path: Path) -> None:
    assistant = PersonalAssistant.__new__(PersonalAssistant)
    assistant.registry = ResourceRegistry(tmp_path / "resources.toml")
    apply_registry_connection(assistant.registry, connection())
    assistant.tool_settings = apply_tool_connection(ToolSettings(path=tmp_path / "tools.toml"), connection())
    from personal_assistant.config import AssistantConfig
    from personal_assistant.connectors.nextcloud.files import NextcloudFiles

    assistant.config = AssistantConfig()
    seen = []
    assistant.nextcloud_files = SimpleNamespace(
        clean_path=NextcloudFiles.clean_path, list_folder=lambda path: seen.append(path) or []
    )
    assert assistant.list_nextcloud_files()["root"] == ""
    assert seen == [""]
    assert assistant._workspace_path("Another folder/new.txt") == "Another folder/new.txt"
    with pytest.raises(ValueError):
        assistant._workspace_path("../../escape")
    workspace = assistant.tool_settings.nextcloud.workspace
    assistant.tool_settings.nextcloud.workspace = replace(workspace, root="Assistent")
    with pytest.raises(PermissionError):
        assistant._workspace_path("Another folder/new.txt")


def test_inventory_reports_every_resource_without_inventing_default(tmp_path: Path) -> None:
    from personal_assistant.resource_identity import inventory_report

    registry = ResourceRegistry(tmp_path / "resources.toml")
    apply_registry_connection(registry, connection())
    settings = apply_tool_connection(ToolSettings(path=tmp_path / "tools.toml"), connection())
    report = inventory_report(settings, registry)
    assert report["ok"]
    assert report["calendar_identity"]["direct_resource_id"] == ""
    calendars = [row for row in report["items"] if row["domain"] == "calendar"]
    assert len(calendars) == 2
    assert {tuple(row["resource_permissions"]) for row in calendars} == {
        ("read",),
        ("read", "create", "update"),
    }


def test_registry_preserves_dormant_entries_without_publishing_account_overlay(tmp_path, monkeypatch):
    registry = ResourceRegistry(tmp_path / "resources.toml")
    legacy = Resource(id="legacy-cloud", kind="file-root", connector="nextcloud", remote_id="Assistent")
    registry.write([legacy, Resource(id="mail", kind="email-service", connector="mail")])
    store = ConnectionStore(tmp_path / "connection")
    store.save(connection(), expected_revision="")
    monkeypatch.setenv("OPENCLAW_NEXTCLOUD_CONNECTION_DIR", str(store.directory))
    registry.load()
    registry.upsert(Resource(id="mail", kind="email-service", connector="mail", permissions=("read",)))
    content = registry.path.read_text()
    assert "legacy-cloud" in content
    assert "nextcloud-account-" not in content
    assert registry.get("mail").permissions == ("read",)
    with pytest.raises(PermissionError, match="OpenClaw-Einrichtung"):
        registry.upsert(Resource(id="extra-cloud", kind="file-root", connector="nextcloud"))


def test_absolute_and_relative_hrefs_have_one_account_bound_identity():
    from personal_assistant.nextcloud_connection import collection_resource_id

    value = connection()
    relative = value["discovery_snapshot"]["calendars"][0]["href"]
    assert collection_resource_id(value, relative) == collection_resource_id(
        value, "https://cloud.example" + relative
    )
    with pytest.raises(ConnectionError, match="origin-mismatch"):
        collection_resource_id(value, "https://attacker.example/nc/remote.php/dav/")


@pytest.mark.parametrize("explicit_default_port", [False, True])
def test_live_discovery_uses_verified_home_and_same_resource_id(explicit_default_port):
    from personal_assistant.connectors.nextcloud.client import DavResponse
    from personal_assistant.connectors.nextcloud.discovery import NextcloudDiscovery

    value = connection()
    value["discovery_snapshot"]["calendar_homes"] = ["https://cloud.example/nc/dav/shared-home/"]
    href = value["discovery_snapshot"]["calendars"][0]["href"]
    response_href = "https://cloud.example:443" + href if explicit_default_port else href
    body = f"""<d:multistatus xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">
      <d:response><d:href>{response_href}</d:href><d:propstat><d:status>HTTP/1.1 200 OK</d:status><d:prop>
      <d:resourcetype><d:collection/><c:calendar/></d:resourcetype><d:displayname>Personal</d:displayname>
      <c:supported-calendar-component-set><c:comp name="VEVENT"/></c:supported-calendar-component-set>
      <d:current-user-privilege-set><d:privilege><d:read/></d:privilege></d:current-user-privilege-set>
      </d:prop></d:propstat></d:response></d:multistatus>""".encode()
    seen = []

    def request(method, path, **kwargs):
        seen.append(path)
        return DavResponse(207, "", {}, body, path)

    client = SimpleNamespace(connection=value, username="wrong-login-alias", request=request)
    found = NextcloudDiscovery(client).calendars()
    assert seen == value["discovery_snapshot"]["calendar_homes"]
    assert found[0].resource_id == next(
        item.id for item in connection_resources(value) if item.remote_id == href
    )
    assert not found[0].can_update


def test_managed_client_preserves_subpath_and_stops_on_changed_account(tmp_path, monkeypatch):
    from personal_assistant.config import AssistantConfig
    from personal_assistant.connectors.nextcloud.client import NextcloudClient, NextcloudError
    from personal_assistant.nextcloud_onboarding import HttpResponse

    store = ConnectionStore(tmp_path / "connection")
    stored = store.save(connection(), expected_revision="")
    monkeypatch.setenv("OPENCLAW_NEXTCLOUD_CONNECTION_DIR", str(store.directory))
    seen = []

    class Transport:
        def __init__(self, base_url, **kwargs):
            self.base_url = base_url

        def request(self, method, path, **kwargs):
            seen.append(path)
            return HttpResponse(207, {}, b"fixture")

        def resolve_url(self, path):
            return self.base_url + "/" + path

    monkeypatch.setattr("personal_assistant.nextcloud_onboarding.SafeNextcloudTransport", Transport)
    client = NextcloudClient(AssistantConfig())
    client.request("PROPFIND", "/remote.php/dav/")
    client.request("PROPFIND", "/nc/remote.php/dav/calendars/assistant/a/")
    assert seen == ["remote.php/dav/", "/nc/remote.php/dav/calendars/assistant/a/"]
    store.save(connection("another"), expected_revision=stored["revision"])
    with pytest.raises(NextcloudError, match="connection-changed"):
        client.request("PUT", "/nc/remote.php/dav/calendars/assistant/a/item.ics")
    assert len(seen) == 2


def test_store_audit_is_content_free_and_failure_restores_previous_connection(tmp_path, monkeypatch):
    store = ConnectionStore(tmp_path / "connection")
    first = store.save(connection(), expected_revision="")
    content = (store.directory / "audit.jsonl").read_text()
    events = [json.loads(row) for row in content.splitlines()]
    assert [event["phase"] for event in events] == ["prepared", "committed"]
    assert "cloud.example" not in content and "fixture-password" not in content
    actual_audit = store._audit

    def reject_commit(event):
        if event["phase"] == "committed":
            raise ConnectionError("nextcloud-connection-audit-failed")
        return actual_audit(event)

    monkeypatch.setattr(store, "_audit", reject_commit)
    with pytest.raises(ConnectionError, match="publication-failed"):
        store.save(connection("different"), expected_revision=first["revision"])
    assert store.read()["revision"] == first["revision"]
    assert store.read()["user_id"] == "assistant"


def test_account_sync_uses_account_root_and_reports_failed_optional_scope(tmp_path):
    assistant = PersonalAssistant.__new__(PersonalAssistant)
    assistant.role = "sync-worker"
    assistant.config = SimpleNamespace(
        nextcloud=SimpleNamespace(enabled=True, allowed_file_roots=()),
        search=SimpleNamespace(nextcloud_max_items=10, nextcloud_max_depth=3),
    )
    assistant.registry = ResourceRegistry(tmp_path / "resources.toml")
    value = connection()
    apply_registry_connection(assistant.registry, value)
    assistant.nextcloud_client = SimpleNamespace(connection=value)
    assistant.discover_nextcloud = lambda **kw: {
        "health": {"ok": True},
        "calendars": [],
        "addressbooks": [],
        "resource_errors": [{"scope": "contacts", "code": "NextcloudError"}],
    }
    assistant.storage = object()
    assistant.indexer = object()
    seen = []

    def sync_files(*args, **kwargs):
        seen.append(kwargs)
        return {"errors": 0, "authoritative": True}

    assistant.nextcloud_files = SimpleNamespace(sync_index=sync_files)
    assistant.nextcloud_contacts = SimpleNamespace(sync_index=lambda *a, **kw: {"errors": 0})
    assistant.nextcloud_calendar = SimpleNamespace(sync_index=lambda *a, **kw: {"errors": 0})
    report = assistant.sync_nextcloud()
    assert seen[0]["resource_id"] == account_namespace(value) + "-files"
    assert seen[0]["roots"] == ("",)
    assert not report["complete"] and report["partial_scopes"] == ["contacts"]


def test_managed_legacy_configuration_is_rejected_before_network(tmp_path):
    assistant = PersonalAssistant.__new__(PersonalAssistant)
    assistant.registry = ResourceRegistry(tmp_path / "resources.toml")
    apply_registry_connection(assistant.registry, connection())
    with pytest.raises(PermissionError, match="OpenClaw-Einrichtung"):
        assistant._configure_discovered_caldav(
            component="VEVENT", resource_id="id", allow_create=True, allow_read=True
        )
