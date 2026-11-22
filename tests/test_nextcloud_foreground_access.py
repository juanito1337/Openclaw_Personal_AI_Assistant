"""Foreground access through synthetic managed account and pinned transport."""

from types import SimpleNamespace

import pytest
from test_nextcloud_access import grant, managed, measured, scope, update_policy

from personal_assistant import nextcloud_connection as nc
from personal_assistant.config import AssistantConfig
from personal_assistant.connectors.nextcloud.calendar import NextcloudCalendar
from personal_assistant.connectors.nextcloud.client import NextcloudClient
from personal_assistant.connectors.nextcloud.discovery import NextcloudDiscovery
from personal_assistant.connectors.nextcloud.tasks import NextcloudTasks
from personal_assistant.nextcloud_onboarding import HttpResponse
from personal_assistant.registry import ResourceRegistry
from personal_assistant.service import PersonalAssistant
from personal_assistant.tool_settings import ToolSettings

READ = ["list", "read", "search"]


def ics(component):
    return "\r\n".join(
        (
            "BEGIN:VCALENDAR",
            "VERSION:2.0",
            f"BEGIN:{component}",
            "UID:one",
            "SUMMARY:Synthetic",
            f"END:{component}",
            "END:VCALENDAR",
            "",
        )
    )


def report(href, component):
    return f"""<d:multistatus xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">
    <d:response><d:href>{href}</d:href><d:propstat><d:status>HTTP/1.1 200 OK</d:status>
    <d:prop><d:getetag>"one"</d:getetag><c:calendar-data>{ics(component)}</c:calendar-data>
    </d:prop></d:propstat></d:response></d:multistatus>""".encode()


@pytest.fixture
def account(tmp_path, monkeypatch):
    store = managed(tmp_path)
    conn = measured()
    row = conn["discovery_snapshot"]["calendars"][0]
    conn["discovery_snapshot"]["addressbooks"] = [
        {**row, "href": "/nc/remote.php/dav/addressbooks/users/assistant/main/", "components": ["VCARD"]}
    ]
    store.save(conn, expected_revision="")
    ev = scope(conn)
    book = {
        **ev,
        "component": "VCARD",
        "resource_id": nc.collection_resource_id(conn, conn["discovery_snapshot"]["addressbooks"][0]["href"]),
    }
    grants = [
        grant(ev, READ),
        grant({**ev, "component": "VTODO"}, READ + ["create", "update"]),
        grant(book, READ + ["create"]),
    ]
    snap = update_policy(
        store, sorted(grants, key=lambda g: (g["scope"]["resource_id"], g["scope"]["component"]))
    )
    monkeypatch.setenv("OPENCLAW_NEXTCLOUD_CONNECTION_DIR", str(store.directory))
    href = row["href"]
    calls = []
    replies = {}

    class Transport:
        def __init__(self, *args, **kwargs):
            pass

        def request(self, method, path, **kwargs):
            calls.append((method, path, kwargs))
            assert method in replies, f"Unexpected transport call {method}"
            return replies[method]

        def resolve_url(self, path):
            return "https://cloud.example" + path

    monkeypatch.setattr("personal_assistant.nextcloud_onboarding.SafeNextcloudTransport", Transport)
    pa = PersonalAssistant.__new__(PersonalAssistant)
    pa.config = AssistantConfig()
    pa.registry = ResourceRegistry(tmp_path / "resources.toml")
    pa.tool_settings = nc.apply_tool_connection(ToolSettings(path=tmp_path / "tools.toml"), snap.connection)
    pa.nextcloud_client = NextcloudClient(pa.config)
    pa.nextcloud_calendar = NextcloudCalendar(pa.config, pa.nextcloud_client)
    pa.nextcloud_tasks = NextcloudTasks(pa.nextcloud_client)
    pa.nextcloud_discovery = NextcloudDiscovery(pa.nextcloud_client)
    pa.policy = SimpleNamespace(decide=lambda *a: SimpleNamespace(allowed=True))
    return SimpleNamespace(pa=pa, calls=calls, replies=replies, store=store, id=ev["resource_id"], href=href)


def test_mixed_collection_routes_service_and_connector(account):
    pa = account.pa
    with pa.select_nextcloud_resource("calendar", account.id):
        assert not pa.direct_calendar_status()["update_allowed"]
        account.replies["REPORT"] = HttpResponse(207, {}, report(account.href + "one.ics", "VEVENT"))
        assert [event["uid"] for event in pa.calendar_list()["events"]] == ["one"]
    with pa.select_nextcloud_resource("tasks", account.id):
        assert pa.direct_tasks_status(live=False)["update_allowed"]
    col = pa._calendar_collection(pa.registry.get(account.id))
    previous = len(account.calls)
    with pytest.raises(PermissionError):
        pa.nextcloud_calendar.create_event(col, ics("VEVENT"), "one")
    assert len(account.calls) == previous
    assert [item.resource_id for item in pa.nextcloud_discovery.calendars()] == [account.id]
    account.replies["PUT"] = HttpResponse(201, {}, b"")
    account.replies["REPORT"] = HttpResponse(207, {}, report(account.href + "one.ics", "VTODO"))
    assert pa.nextcloud_tasks.create_task(col, ics("VTODO"), "one").endswith("/one.ics")
    assert account.calls[-2][2]["headers"]["If-None-Match"] == "*"
    assert account.calls[-1][0] == "REPORT"


@pytest.mark.parametrize("component,suffix", [("VTODO", "one.ics"), ("VEVENT", "../b/one.ics")])
def test_calendar_report_cannot_return_other_scope(account, component, suffix):
    pa = account.pa
    account.replies["REPORT"] = HttpResponse(207, {}, report(account.href + suffix, component))
    with pytest.raises(PermissionError), pa.select_nextcloud_resource("calendar", account.id):
        pa.calendar_list()


def test_active_client_requires_typed_context_before_remote_request(account):
    with pytest.raises(PermissionError):
        account.pa.nextcloud_client.request("GET", account.href + "one.ics")
    assert not account.calls


def test_domain_policy_decisions_and_exact_selection_match_status(account, tmp_path):
    from personal_assistant.policy import PolicyEngine

    pa = account.pa
    policy = PolicyEngine(tmp_path / "policy", pa.registry)
    assert not policy.decide(account.id, "calendar.update").allowed
    assert policy.decide(account.id, "tasks.update").allowed
    assert not policy.decide(account.id, "unknown.action").allowed
    update_policy(account.store, [])
    with (
        pytest.raises(PermissionError, match="policy-changed"),
        pa.select_nextcloud_resource("tasks", account.id),
    ):
        pass
    assert not account.calls


def test_unconfigured_role_cannot_recover_environment_configuration(tmp_path, monkeypatch):
    from personal_assistant.config import load_config
    from personal_assistant.tool_settings import load_tool_settings

    monkeypatch.setenv("OPENCLAW_RUNTIME", "container")
    monkeypatch.setenv("OPENCLAW_ROLE", "portfolio-worker")
    monkeypatch.delenv("OPENCLAW_NEXTCLOUD_CONNECTION_DIR", raising=False)
    config_path = tmp_path / "config.toml"
    config_path.write_text("[nextcloud]\nenabled = true\n")
    config = load_config(config_path)
    assert not config.nextcloud.enabled
    assert not load_tool_settings(tmp_path / "tools.toml").nextcloud.workspace.enabled
    client = NextcloudClient(config)
    with pytest.raises(PermissionError, match="scope-unbound"):
        client.credentials()


def test_existing_task_is_checked_before_conditional_put(account):
    pa = account.pa
    coll = pa._task_collection(pa.registry.get(account.id))
    href = account.href + "one.ics"
    account.replies["REPORT"] = HttpResponse(207, {}, report(href, "VTODO"))
    account.replies["PUT"] = HttpResponse(204, {}, b"")
    row = pa.nextcloud_tasks.update_task(coll, href=href, uid="one", ics=ics("VTODO"), etag='"one"')
    assert row.uid == "one"
    assert [method for method, _, _ in account.calls] == ["REPORT", "PUT", "REPORT"]
    assert account.calls[1][2]["headers"]["If-Match"] == '"one"'
    assert all(not args["follow_redirects"] for _, _, args in account.calls)
    account.replies["REPORT"] = HttpResponse(207, {}, report(href, "VEVENT"))
    account.calls.clear()
    with pytest.raises(PermissionError):
        pa.nextcloud_tasks.update_task(coll, href=href, uid="one", ics=ics("VTODO"), etag='"one"')
    assert [method for method, _, _ in account.calls] == ["REPORT"]


def test_contact_create_allowed_and_update_rejected_before_traffic(account):
    from personal_assistant.connectors.nextcloud.contacts import NextcloudContacts

    pa = account.pa
    (book,) = pa.nextcloud_discovery.addressbooks()
    contacts = NextcloudContacts(pa.config, pa.nextcloud_client)
    card = "BEGIN:VCARD\r\nVERSION:3.0\r\nUID:one\r\nFN:Synthetic\r\nEND:VCARD\r\n"
    account.replies["GET"] = HttpResponse(200, {"etag": '"two"'}, card.encode())
    account.replies["PUT"] = HttpResponse(201, {}, b"")
    href = contacts.create_contact(book, card, "one")
    assert href.endswith("/one.vcf") and len(account.calls) == 2
    with pytest.raises(PermissionError, match="operation-not-allowed"):
        contacts.update_contact(book, uid="one", href=href, etag='"two"', vcard=card)
    assert len(account.calls) == 2


def test_active_redirect_stops_before_followup():
    from test_nextcloud_onboarding import ROOT, FakeNetwork, error_code

    fixture = FakeNetwork()
    fixture.add("REPORT", ROOT, status=307, headers={"location": ROOT + "other"})
    with error_code("redirect-blocked"):
        fixture.transport().request("REPORT", ROOT, follow_redirects=False)
    assert len(fixture.calls) == 1


def test_new_policy_blocks_long_lived_client(account):
    pa = account.pa
    col = pa._calendar_collection(pa.registry.get(account.id))
    update_policy(account.store, [])
    with pytest.raises(PermissionError, match="policy-changed"):
        pa.nextcloud_calendar.list_events(col)
    assert not account.calls


def test_inventory_and_tool_reporting_respect_each_component(account):
    from personal_assistant.resource_identity import inventory_report
    from personal_assistant.tool_registry import build_tool_registry

    pa = account.pa
    report = inventory_report(pa.tool_settings, pa.registry)
    rows = {row["domain"]: row for row in report["items"]}
    assert "update" not in rows["calendar"]["resource_permissions"]
    assert "update" in rows["tasks"]["resource_permissions"]
    update_policy(account.store, [])
    with pytest.raises(PermissionError, match="policy-changed"):
        pa.direct_calendar_status()
    with pytest.raises(PermissionError, match="policy-changed"):
        build_tool_registry(pa.tool_settings)


def test_direct_write_checks_exact_uid_and_operation_before_lookup(account):
    pa = account.pa
    col = pa._task_collection(pa.registry.get(account.id))
    with pytest.raises(PermissionError):
        pa.nextcloud_tasks.update_task(
            col, href=account.href + "one.ics", uid="other", ics=ics("VTODO"), etag='"one"'
        )
    with pytest.raises(PermissionError):
        pa.nextcloud_calendar.update_event(
            col, href=account.href + "one.ics", uid="one", ics=ics("VEVENT"), etag=""
        )
    assert not account.calls


def test_ambiguous_component_evidence_is_rejected(account):
    pa = account.pa
    mixed = report(account.href + "one.ics", "VEVENT").replace(
        b"<c:calendar-data>",
        b"<c:calendar-data>" + ics("VTODO").encode() + b"</c:calendar-data><c:calendar-data>",
    )
    account.replies["REPORT"] = HttpResponse(207, {}, mixed)
    with pytest.raises(PermissionError):
        pa.calendar_list()


def test_all_off_denies_lookup_in_legacy_environment_after_transition(tmp_path, monkeypatch):
    from personal_assistant.tool_settings import load_tool_settings

    store = managed(tmp_path)
    conn = measured()
    store.save(conn, expected_revision="")
    monkeypatch.setenv("OPENCLAW_NEXTCLOUD_CONNECTION_DIR", str(store.directory))
    settings = load_tool_settings(tmp_path / "tools.toml")
    assert not settings.nextcloud.tasks.enabled and not settings.nextcloud.workspace.enabled
    assert not ResourceRegistry(tmp_path / "resources.toml").list()
    assert nc.public_connection(store.read())["resources"]


def test_active_discovery_reports_measurement_source(account):
    pa = account.pa
    # Stored effective rights and their measurement time remain distinguishable
    # from a new on-demand server capability measurement.
    (book,) = pa.nextcloud_discovery.addressbooks()
    status = pa.direct_contacts_status()
    assert status["rights_source"] == "saved-measurement"
    assert status["rights_checked_at"]
    assert not status["live_rights_measured"]
    assert not book.can_update


def test_active_tool_inventory_hides_unavailable_file_and_setup_operations(account):
    from personal_assistant.tool_registry import build_tool_registry

    ids = {tool.id for tool in build_tool_registry(account.pa.tool_settings)}
    assert "nextcloud.calendar.update" not in ids and "nextcloud.tasks.update" in ids
    assert "nextcloud.list" not in ids
    assert "nextcloud.calendar.configure" not in ids


def test_typed_label_cannot_authorize_another_http_action(account):
    from personal_assistant.nextcloud_access import Scope
    from personal_assistant.nextcloud_access_runtime import AccessContext

    pa = account.pa
    access = AccessContext(account.store.snapshot().binding, Scope(account.id, "VTODO"), "create")
    with pytest.raises(PermissionError):
        pa.nextcloud_client.request(
            "PUT",
            account.href + "one.ics",
            access=access,
            data=ics("VTODO").encode(),
            headers={"Content-Type": "text/calendar", "If-Match": '"one"'},
        )
    with pytest.raises(PermissionError):
        pa.nextcloud_client.request("DELETE", account.href + "one.ics", access=access)
    assert not account.calls


def test_missing_object_content_is_incomplete_not_an_empty_calendar(account):
    pa = account.pa
    payload = report(account.href + "one.ics", "VEVENT")
    start, rest = payload.split(b"<c:calendar-data>")
    _, end = rest.split(b"</c:calendar-data>")
    account.replies["REPORT"] = HttpResponse(207, {}, start + end)
    with pytest.raises(PermissionError, match="rights-unverified"):
        pa.calendar_list()


def missing_child(href):
    return (f'<d:multistatus xmlns:d="DAV:"><d:response><d:href>{href}</d:href>'
            '<d:status>HTTP/1.1 404 Not Found</d:status></d:response></d:multistatus>').encode()


def test_task_existence_is_component_scoped(account):
    pa = account.pa
    col = pa._task_collection(pa.registry.get(account.id))
    account.replies["REPORT"] = HttpResponse(207, {}, b'<d:multistatus xmlns:d="DAV:"/>')
    with pytest.raises(PermissionError, match="rights-unverified"):
        pa.nextcloud_tasks.task_exists(col, "one")
    assert [method for method, _, _ in account.calls] == ["REPORT", "REPORT"]
    assert b"calendar-multiget" in account.calls[1][2]["data"]


def test_restriction_after_put_blocks_readback(account, monkeypatch):
    from contextlib import contextmanager

    pa = account.pa
    col = pa._task_collection(pa.registry.get(account.id))
    href = account.href + "one.ics"
    account.replies["REPORT"] = HttpResponse(207, {}, report(href, "VTODO"))
    account.replies["PUT"] = HttpResponse(204, {}, b"")
    original = nc.ConnectionStore.admit

    @contextmanager
    def admit_then_restrict(store, binding):
        with original(store, binding) as snapshot:
            yield snapshot
        if account.calls and account.calls[-1][0] == "PUT":
            update_policy(account.store, [])

    monkeypatch.setattr(nc.ConnectionStore, "admit", admit_then_restrict)
    with pytest.raises(RuntimeError, match="delivery-uncertain: policy-changed"):
        pa.nextcloud_tasks.update_task(col, href=href, uid="one", ics=ics("VTODO"), etag='"one"')
    assert [method for method, _, _ in account.calls] == ["REPORT", "PUT"]


def test_runtime_projection_rejects_unverified_active_dictionary(account, tmp_path, monkeypatch):
    value = account.store.read()
    value["access_policy"]["grants"] = []
    with pytest.raises(nc.ConnectionError, match="policy-integrity-error"):
        nc.apply_tool_connection(ToolSettings(path=tmp_path / "tools.toml"), value)
    monkeypatch.delenv("OPENCLAW_NEXTCLOUD_CONNECTION_DIR")
    with pytest.raises(nc.ConnectionError, match="policy-integrity-error"):
        nc.apply_tool_connection(ToolSettings(path=tmp_path / "tools.toml"), account.store.read())


def test_stale_selected_folder_retains_only_recheck_route(account, tmp_path):
    from datetime import UTC, datetime, timedelta

    conn = account.store.read()
    selected = scope(conn, "FILES", "Belege", "11")
    update_policy(account.store, [grant(selected, READ + ["create_file"])])
    current = account.store.read()
    current["discovery_snapshot"]["folders"][0]["checked_at"] = (
        datetime.now(UTC) - timedelta(days=2)
    ).isoformat()
    account.store.save(current, expected_revision=current["revision"])
    snapshot = account.store.snapshot()
    settings = nc.apply_tool_connection(ToolSettings(path=tmp_path / "tools"), snapshot)
    assert settings.nextcloud.workspace.enabled and not settings.nextcloud.workspace.allow_upload
    resource = ResourceRegistry(tmp_path / "resources").get(selected["resource_id"])
    assert not resource.permissions and not resource.metadata["folder_operations"]
    assert resource.metadata["folder_recheck_operations"] == {"Belege": ["list"]}
    from personal_assistant.tool_registry import build_tool_registry

    tools = {tool.id: tool for tool in build_tool_registry(settings)}
    assert "erneut" in tools["nextcloud.list"].description and "nextcloud.sync" not in tools


@pytest.mark.parametrize("aggregate", ["list", "status"])
@pytest.mark.parametrize("change", ["policy", "inventory"])
def test_aggregate_cannot_release_rows_measured_before_change(account, aggregate, change):
    pa = account.pa
    current = account.store.read()
    grants = [
        grant({**scope(current), "resource_id": nc.collection_resource_id(current, row["href"])}, READ)
        for row in current["discovery_snapshot"]["calendars"]
    ]
    fresh = update_policy(account.store, sorted(grants, key=lambda g: g["scope"]["resource_id"]))
    nc.apply_registry_connection(pa.registry, fresh)
    pa.tool_settings = nc.apply_tool_connection(pa.tool_settings, fresh)

    def select_then_change(*args, **kwargs):
        if change == "policy":
            update_policy(account.store, [])
        else:
            original = account.store.read()
            account.store.save(original, expected_revision=original["revision"])
        return {"ok": True, "events": [{"uid": "private"}], "complete": True}

    if aggregate == "list":
        pa.calendar_list = select_then_change
    else:
        pa.direct_calendar_status = select_then_change
    with pytest.raises((PermissionError, nc.ConnectionError), match="policy-changed"):
        if aggregate == "list":
            pa._account_domain_list("calendar", limit=10)
        else:
            pa._account_domain_status("calendar")


@pytest.mark.parametrize(
    "new_content, etag", [("different", '"one"'), ("Synthetic", 'W/"one"'), ("Synthetic", '""')]
)
def test_task_create_requires_requested_fields_and_strong_etag(account, new_content, etag):
    pa = account.pa
    col = pa._task_collection(pa.registry.get(account.id))
    account.replies["PUT"] = HttpResponse(201, {}, b"")
    response = (
        report(account.href + "one.ics", "VTODO")
        .replace(b"Synthetic", new_content.encode())
        .replace(b'"one"', etag.encode())
    )
    account.replies["REPORT"] = HttpResponse(207, {}, response)
    with pytest.raises(RuntimeError, match="delivery-uncertain"):
        pa.nextcloud_tasks.create_task(col, ics("VTODO"), "one")
    assert [method for method, _, _ in account.calls] == ["PUT", "REPORT"]


def test_task_update_does_not_confirm_unchanged_status(account):
    pa = account.pa
    col = pa._task_collection(pa.registry.get(account.id))
    href = account.href + "one.ics"
    account.replies["REPORT"] = HttpResponse(207, {}, report(href, "VTODO"))
    account.replies["PUT"] = HttpResponse(204, {}, b"")
    proposed = ics("VTODO").replace("END:VTODO", "STATUS:COMPLETED\r\nPERCENT-COMPLETE:100\r\nEND:VTODO")
    with pytest.raises(RuntimeError, match="delivery-uncertain"):
        pa.nextcloud_tasks.update_task(col, href=href, uid="one", ics=proposed, etag='"one"')
    assert [method for method, _, _ in account.calls] == ["REPORT", "PUT", "REPORT"]


def test_metadata_cannot_release_untyped_calendar_locators(account):
    pa = account.pa
    col = pa._calendar_collection(pa.registry.get(account.id))
    start, rest = report(account.href + "one.ics", "VEVENT").split(b"<c:calendar-data>")
    _, end = rest.split(b"</c:calendar-data>")
    account.replies["REPORT"] = HttpResponse(207, {}, start + end)
    with pytest.raises(PermissionError):
        pa.nextcloud_calendar.list_event_metadata(col)


def test_managed_legacy_views_stop_after_activation(tmp_path, monkeypatch):
    from personal_assistant.tool_registry import build_tool_registry
    from personal_assistant.tool_settings import load_tool_settings

    store = nc.ConnectionStore(tmp_path / "mount")
    store.save(measured(), expected_revision="")
    state = tmp_path / "state"
    state.mkdir()
    nc.initialize_access_store(state, store.directory, transition="verified-pre-policy")
    monkeypatch.setenv("OPENCLAW_NEXTCLOUD_CONNECTION_DIR", str(store.directory))
    settings = load_tool_settings(tmp_path / "tools")
    registry = ResourceRegistry(tmp_path / "resources")
    assert registry.list() and build_tool_registry(settings)
    legacy = store.snapshot()
    policy = nc.access.new_policy(legacy.connection)
    store.save(
        {**legacy.connection, "access_policy": policy.to_dict()},
        expected_revision=legacy.connection["revision"],
        expected_policy_revision="",
    )
    for read in (registry.list, lambda: build_tool_registry(settings)):
        with pytest.raises((PermissionError, nc.ConnectionError), match="policy-changed"):
            read()


@pytest.mark.parametrize("transition", ["before", "after", "redirect"])
def test_managed_legacy_hop_admission(tmp_path, monkeypatch, transition):
    from contextlib import contextmanager

    from personal_assistant.connectors.nextcloud import client as client_module

    store = nc.ConnectionStore(tmp_path / "mount")
    store.save(measured(), expected_revision="")
    state = tmp_path / "state"
    state.mkdir()
    nc.initialize_access_store(state, store.directory, transition="verified-pre-policy")
    monkeypatch.setenv("OPENCLAW_NEXTCLOUD_CONNECTION_DIR", str(store.directory))
    client = NextcloudClient(AssistantConfig())
    calls = []

    def activate(snapshot):
        policy = nc.access.new_policy(snapshot.connection)
        store.save(
            {**snapshot.connection, "access_policy": policy.to_dict()},
            expected_revision=snapshot.connection["revision"],
            expected_policy_revision="",
        )

    def send(*args, **kwargs):
        calls.append(kwargs["follow_redirects"])
        return client_module.DavResponse(200, "", {}, b"unused", "https://cloud.example/nc")

    monkeypatch.setattr(client, "_managed_request", send)
    if transition == "before":

        def snapshot_then_activate():
            original = store.snapshot()
            activate(original)
            return original

        monkeypatch.setattr(client_module, "load_access_snapshot", snapshot_then_activate)
    elif transition == "after":

        @contextmanager
        def activate_after_hop(old):
            with nc.admit_projection(old) as current:
                yield current
            if calls:
                activate(old)

        monkeypatch.setattr(client_module, "admit_projection", activate_after_hop, raising=False)
    if transition == "redirect":
        client.request("GET", "/status.php")
        assert calls == [False]
    else:
        with pytest.raises((PermissionError, nc.ConnectionError), match="policy-changed"):
            client.request("GET", "/status.php")
        assert calls == ([] if transition == "before" else [False])


def test_task_service_returns_uncertain_without_presenting_proposal_as_remote_task(account, tmp_path):
    from personal_assistant.actions import ActionService
    from personal_assistant.policy import PolicyEngine
    from personal_assistant.storage import AssistantStorage

    pa = account.pa
    policy = PolicyEngine(tmp_path / "policies.toml", pa.registry)
    pa.actions = ActionService(
        AssistantStorage(tmp_path / "actions.sqlite3"),
        pa.registry,
        policy,
        pa.nextcloud_client,
        None,
        pa.nextcloud_calendar,
        pa.nextcloud_tasks,
    )
    account.replies["PUT"] = HttpResponse(204, {}, b"")
    account.replies["REPORT"] = HttpResponse(207, {}, report(account.href + "one.ics", "VTODO"))
    result = pa.task_create(title="Requested field", uid="one")
    assert not result["ok"] and result["delivery_uncertain"]
    assert "task" not in result and [c[0] for c in account.calls].count("PUT") == 1


@pytest.mark.parametrize("domain", ["calendar", "contacts"])
def test_other_components_check_post_write_fields(account, domain):
    from personal_assistant.connectors.nextcloud.contacts import NextcloudContacts

    pa = account.pa
    snapshot = account.store.snapshot()
    grants = snapshot.policy.to_dict()["grants"]
    component = "VEVENT" if domain == "calendar" else "VCARD"
    selected = next(g for g in grants if g["scope"]["component"] == component)
    selected["operations"] = sorted(READ + ["create", "update"])
    fresh = update_policy(account.store, grants)
    nc.apply_registry_connection(pa.registry, fresh)
    client = NextcloudClient(pa.config)
    account.replies["PUT"] = HttpResponse(204, {}, b"")
    href = account.href + "one.ics"
    if domain == "calendar":
        calendar = NextcloudCalendar(pa.config, client)
        col = pa._calendar_collection(pa.registry.get(account.id))
        account.replies["REPORT"] = HttpResponse(207, {}, report(href, "VEVENT"))

        def update():
            calendar.update_event(
                col,
                href=href,
                uid="one",
                etag='"one"',
                ics=ics("VEVENT").replace("END:VEVENT", "LOCATION:Requested\r\nEND:VEVENT"),
            )
    else:
        contacts = NextcloudContacts(pa.config, client)
        col = pa._contact_collection(pa.registry.get(selected["scope"]["resource_id"]))
        href = col.href.rstrip("/") + "/one.vcf"
        original = "BEGIN:VCARD\r\nVERSION:3.0\r\nUID:one\r\nFN:Synthetic\r\nEND:VCARD\r\n"
        account.replies["GET"] = HttpResponse(200, {"ETag": '"one"'}, original.encode())

        def update():
            contacts.update_contact(
                col,
                href=href,
                uid="one",
                etag='"one"',
                vcard=original.replace("END:VCARD", "TEL;TYPE=VOICE:0000000\r\nEND:VCARD"),
            )

    with pytest.raises(RuntimeError, match="delivery-uncertain"):
        update()
    assert [c[0] for c in account.calls].count("PUT") == 1


@pytest.mark.parametrize("component", ["VEVENT", "VTODO"])
def test_calendar_metadata_requests_only_component_and_uid(account, component):
    from xml.etree import ElementTree as ET

    from personal_assistant.connectors.nextcloud.xmlutil import CALDAV, q

    pa = account.pa
    col = pa._calendar_collection(pa.registry.get(account.id))
    href = account.href + "one.ics"
    account.replies["REPORT"] = HttpResponse(207, {}, report(href, component))
    if component == "VEVENT":
        entries = pa.nextcloud_calendar.list_event_metadata(col)
        assert [(entry.href, entry.etag, entry.uid) for entry in entries] == [(href, '"one"', "one")]
    else:
        with pytest.raises(PermissionError, match="scope-invalid"):
            pa.nextcloud_calendar.list_event_metadata(col)
    request = ET.fromstring(account.calls[0][2]["data"])
    proof = request.find(".//" + q(CALDAV, "calendar-data"))
    assert proof is not None and [node.get("name") for node in proof.iter() if node.get("name")] == [
        "VCALENDAR",
        "VEVENT",
        "UID",
    ]


@pytest.mark.parametrize("component", ["VEVENT", "VTODO"])
def test_single_caldav_object_uses_bounded_report_on_pinned_transport(tmp_path, monkeypatch, component):
    from xml.etree import ElementTree as ET

    from test_nextcloud_onboarding import FakeConnection, FakeNetwork, multistatus

    from personal_assistant.connectors.nextcloud.xmlutil import CALDAV, DAV, q
    from personal_assistant.nextcloud_onboarding import SafeNextcloudTransport

    conn = measured()
    store = managed(tmp_path)
    store.save({**conn, "expected_ips": ["192.168.1.20"]}, expected_revision="")
    update_policy(store, [grant(scope(conn, component), READ + ["create"])])
    monkeypatch.setenv("OPENCLAW_NEXTCLOUD_CONNECTION_DIR", str(store.directory))
    collection = NextcloudDiscovery(NextcloudClient(AssistantConfig()))
    selected = (collection.calendars() if component == "VEVENT" else collection.task_lists())[0]
    href = selected.href.rstrip("/") + "/one.ics"
    network = FakeNetwork()
    config = AssistantConfig()
    config.search.max_file_bytes = 1024 * 1024
    raw = ics(component)
    large = raw.replace("UID:one", "UID:unrelated").replace("Synthetic", "x" * (4 * 1024 * 1024))
    row = {q(DAV, "getetag"): '"one"', q(CALDAV, "calendar-data"): raw}
    created = False
    requests = []

    class CalendarConnection(FakeConnection):
        def request(self, method, path, *, body, headers):
            nonlocal created
            if method == "PUT":
                assert path == href and headers["If-None-Match"] == "*"
                created = True
                network.add(method, path, b"", status=201)
            else:
                assert method == "REPORT" and path == selected.href
                query = ET.fromstring(body)
                uid = query.findtext(".//" + q(CALDAV, "text-match"))
                requested_href = query.findtext(q(DAV, "href"))
                requests.append((uid, requested_href))
                rows = [(href, row)] if created else []
                if requested_href:
                    assert requested_href == href and headers["Depth"] == "0"
                    if not created:
                        assert query.find(".//" + q(CALDAV, "calendar-data")) is None
                        network.add(method, path, missing_child(href), status=207)
                        return super().request(method, path, body=body, headers=headers)
                    projected = raw.replace("SUMMARY:Synthetic\r\n", "")
                    rows = [(href, {**row, q(CALDAV, "calendar-data"): projected})]
                    assert [n.get("name") for n in query.iter() if n.get("name")] == [
                        "VCALENDAR", component, "UID"
                    ]
                elif uid is None:
                    rows.append((selected.href + "large.ics", {**row, q(CALDAV, "calendar-data"): large}))
                else:
                    assert uid == "one"
                network.add(method, path, multistatus(rows), status=207)
            super().request(method, path, body=body, headers=headers)

    def pinned(base_url, **kwargs):
        assert kwargs["max_response_bytes"] == 4 * 1024 * 1024
        return SafeNextcloudTransport(
            base_url, resolver=lambda *a: ["192.168.1.20"],
            connection_factory=lambda *a: CalendarConnection(network), **kwargs,
        )

    monkeypatch.setattr("personal_assistant.nextcloud_onboarding.SafeNextcloudTransport", pinned)
    client = NextcloudClient(config)
    if component == "VEVENT":
        calendar = NextcloudCalendar(config, client)
        exists, create, read = calendar.event_exists, calendar.create_event, calendar.read_event
    else:
        tasks = NextcloudTasks(client)
        exists, create, read = tasks.task_exists, tasks.create_task, tasks.read_task
    assert not exists(selected, "one")
    assert create(selected, raw, "one") == href
    assert exists(selected, "one") and read(href).uid == "one"
    assert requests == [
        ("one", None), (None, href), ("one", None), ("one", None), (None, href), ("one", None)
    ]


@pytest.mark.parametrize("mismatch", ["locator", "component", "etag", "duplicate-uid"])
def test_locator_only_read_rejects_unbound_or_changed_object(account, monkeypatch, mismatch):
    from personal_assistant.connectors.nextcloud.client import DavResponse

    calendar = account.pa.nextcloud_calendar
    href = account.href + "one.ics"
    calls = []

    def respond(method, target, headers, data, timeout, *, follow_redirects):
        calls.append(data)
        if len(calls) == 1:
            assert b"calendar-multiget" in data and headers["Depth"] == "0"
            reply = report(
                account.href + "other.ics" if mismatch == "locator" else href,
                "VTODO" if mismatch == "component" else "VEVENT",
            )
        else:
            assert b"text-match" in data
            reply = report(href, "VEVENT")
            if mismatch == "etag":
                reply = reply.replace(b'"one"', b'"two"')
            if mismatch == "duplicate-uid":
                from xml.etree import ElementTree as ET

                root = ET.fromstring(reply)
                root.append(ET.fromstring(report(account.href + "other.ics", "VEVENT"))[0])
                reply = ET.tostring(root)
        return DavResponse(207, "", {}, reply, "https://cloud.example" + target)

    monkeypatch.setattr(calendar.client, "_managed_request", respond)
    with pytest.raises(PermissionError):
        calendar.read_event(href)
    assert len(calls) == (1 if mismatch in {"locator", "component"} else 2)
