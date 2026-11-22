"""Durable Nextcloud actions across independent CLI invocations."""

import json
from contextlib import contextmanager

import pytest
from test_nextcloud_access import measured, update_policy
from test_nextcloud_foreground_access import account as managed_account
from test_nextcloud_foreground_access import ics, missing_child, report

from personal_assistant import cli
from personal_assistant import nextcloud_connection as nc
from personal_assistant.actions import ActionService
from personal_assistant.nextcloud_native_binding import EXPECTED_ACCESS, NATIVE_CALL
from personal_assistant.nextcloud_onboarding import HttpResponse
from personal_assistant.policy import PolicyEngine
from personal_assistant.storage import AssistantStorage

account = managed_account


@pytest.fixture
def actions(account, tmp_path):
    pa = account.pa
    with_storage = AssistantStorage(tmp_path / "actions.sqlite3")
    service = ActionService(
        with_storage,
        pa.registry,
        PolicyEngine(tmp_path / "policy", pa.registry),
        pa.nextcloud_client,
        None,
        pa.nextcloud_calendar,
        pa.nextcloud_tasks,
    )
    yield service
    with_storage.close()


def prepare(actions, resource):
    return actions.plan("tasks.create", resource, {"uid": "one", "ics": ics("VTODO")}, "same-create")


@pytest.mark.parametrize("change", ["revoke", "account"])
def test_saved_action_loses_authority_and_visibility(account, actions, change):
    storage = actions.storage
    mail = storage.create_action(
        idempotency_key="mail",
        action_type="mail.send",
        resource_id="mail",
        payload={"subject": "Synthetic"},
        requires_approval=True,
    )
    plan = prepare(actions, account.id)
    if change == "revoke":
        update_policy(account.store, [])
    else:
        account.store.save(measured("different"), expected_revision=account.store.read()["revision"])
    for op in (storage.get_action, actions.approve, actions.execute_task_create):
        with pytest.raises(PermissionError, match="policy-changed"):
            op(plan.id)
    assert storage.list_actions(limit=1) == [mail]
    assert storage.get_action(mail.id) == mail
    assert not account.calls


def test_legacy_payload_fields_cannot_bind_historical_plan(account, actions):
    plan = prepare(actions, account.id)
    payload = {
        **plan.payload,
        "binding": account.store.snapshot().binding.to_dict(),
        "scope": {"resource_id": account.id, "component": "VTODO", "path": None, "file_id": None},
    }
    db = actions.storage.connection
    db.execute(
        "UPDATE action_plans SET payload_json=?,status='completed' WHERE id=?", (json.dumps(payload), plan.id)
    )
    db.commit()
    with pytest.raises(PermissionError, match="scope-unbound"):
        actions.execute_task_create(plan.id)
    assert actions.storage.list_actions() == []
    assert not account.calls


def test_idempotency_collision_after_regrant_rejects_same_arguments(account, actions, tmp_path):
    from personal_assistant.registry import ResourceRegistry

    prepare(actions, account.id)
    grants = account.store.snapshot().policy.to_dict()["grants"]
    update_policy(account.store, [])
    update_policy(account.store, grants)
    registry = ResourceRegistry(tmp_path / "new-resources")
    fresh = ActionService(
        actions.storage,
        registry,
        PolicyEngine(tmp_path / "policy", registry),
        type(account.pa.nextcloud_client)(account.pa.config),
        None,
        None,
        None,
    )
    with pytest.raises(PermissionError, match="policy-changed"):
        prepare(fresh, account.id)
    assert not account.calls


def test_accepted_put_uncertain_is_terminal(account, actions):
    plan = prepare(actions, account.id)
    actions.approve(plan.id)
    account.replies["PUT"] = HttpResponse(201, {}, b"")
    account.replies["REPORT"] = HttpResponse(207, {}, report(account.href + "one.ics", "VEVENT"))
    result = actions.execute(plan.id)
    assert result.status == "delivery-uncertain"
    assert actions.approve(plan.id).status == "delivery-uncertain"
    with pytest.raises(PermissionError, match="delivery-uncertain"):
        actions.execute(plan.id)
    assert [m for m, _, _ in account.calls] == ["PUT", "REPORT"]


def test_revocation_after_accepted_put_records_uncertainty_without_replay(account, actions, monkeypatch):
    plan = prepare(actions, account.id)
    actions.approve(plan.id)
    account.replies["PUT"] = HttpResponse(201, {}, b"")
    original = nc.ConnectionStore.admit

    @contextmanager
    def revoke_after_put(store, binding):
        with original(store, binding) as snapshot:
            yield snapshot
        if account.calls and account.calls[-1][0] == "PUT":
            update_policy(account.store, [])

    monkeypatch.setattr(nc.ConnectionStore, "admit", revoke_after_put)
    with pytest.raises(RuntimeError, match="delivery-uncertain"):
        actions.execute(plan.id)
    row = actions.storage.connection.execute(
        "SELECT status FROM action_plans WHERE id=?", (plan.id,)
    ).fetchone()
    assert row[0] == "delivery-uncertain"
    assert [m for m, _, _ in account.calls] == ["PUT"]


@pytest.mark.parametrize(
    "envelope,code",
    [(None, "scope-unbound"), ("{", "scope-invalid"), ('{"mode":"legacy","binding":null}', "policy-changed")],
)
def test_native_envelope_before_cli_selection_and_physical_request(
    account, monkeypatch, capsys, envelope, code
):
    monkeypatch.setenv(NATIVE_CALL, "1")
    if envelope is None:
        monkeypatch.delenv(EXPECTED_ACCESS, raising=False)
    else:
        monkeypatch.setenv(EXPECTED_ACCESS, envelope)
    assert cli.main(["--config", "/synthetic/missing.toml", "tasks", "list"]) != 0
    assert code in capsys.readouterr().err
    account.replies["GET"] = HttpResponse(200, {}, b"{}")
    with pytest.raises(nc.ConnectionError, match=code):
        account.pa.nextcloud_client.request("GET", "/status.php")
    assert not account.calls


def test_completed_task_with_changed_content_is_conflict(account, actions):
    plan = prepare(actions, account.id)
    actions.approve(plan.id)
    account.replies["PUT"] = HttpResponse(201, {}, b"")
    verified = report(account.href + "one.ics", "VTODO")
    account.replies["REPORT"] = HttpResponse(207, {}, verified)
    assert actions.execute(plan.id).status == "completed"
    account.replies["REPORT"] = HttpResponse(207, {}, verified.replace(b"Synthetic", b"Different"))
    conflict, duplicate = actions.execute_task_create(plan.id)
    assert not duplicate and conflict.status != "completed"
    assert [m for m, _, _ in account.calls].count("PUT") == 1


def test_missing_completed_target_requires_renewed_create_request(account, actions, monkeypatch):
    plan = prepare(actions, account.id)
    actions.approve(plan.id)
    account.replies["PUT"] = HttpResponse(201, {}, b"")
    verified = HttpResponse(207, {}, report(account.href + "one.ics", "VTODO"))
    account.replies["REPORT"] = verified
    assert actions.execute(plan.id).status == "completed"
    missing = HttpResponse(207, {}, b'<d:multistatus xmlns:d="DAV:"/>')
    original = actions.client._managed_request
    absent = True

    def serve(method, path, headers, data, timeout, **kwargs):
        nonlocal absent
        if method == "REPORT" and absent:
            account.replies[method] = (
                HttpResponse(207, {}, missing_child(account.href + "one.ics"))
                if b"calendar-multiget" in data else missing
            )
        response = original(method, path, headers, data, timeout, **kwargs)
        if method == "PUT":
            absent = False
            account.replies["REPORT"] = verified
        return response

    monkeypatch.setattr(actions.client, "_managed_request", serve)
    with pytest.raises(PermissionError, match="approval-required"):
        actions.execute_task_create(plan.id)
    assert [m for m, _, _ in account.calls].count("PUT") == 1
    assert actions.storage.get_action(plan.id).status == "completed"
    # A new bounded create call undergoes its normal selection and authorization.
    renewed, duplicate = actions.execute_task_create(plan.id, recreate_missing=True)
    assert not duplicate and renewed.status == "completed"
    assert [m for m, _, _ in account.calls].count("PUT") == 2


def test_local_result_release_checks_original_native_binding(account, monkeypatch, capsys):
    from personal_assistant.nextcloud_native_binding import native_access_projection

    initial = account.store.snapshot()
    monkeypatch.setenv(NATIVE_CALL, "1")
    monkeypatch.setenv(EXPECTED_ACCESS, json.dumps(native_access_projection(initial)))
    update_policy(account.store, [])
    with pytest.raises(nc.ConnectionError, match="policy-changed"):
        cli._print({"synthetic": "revoked"})
    assert not capsys.readouterr().out


def test_same_key_for_another_uid_cannot_resume_original_action(account, actions):
    prepare(actions, account.id)
    other = {"uid": "two", "ics": ics("VTODO").replace("UID:one", "UID:two")}
    with pytest.raises(PermissionError, match="idempotency-conflict"):
        actions.plan("tasks.create", account.id, other, "same-create")
    assert not account.calls


def test_active_create_retries_accept_generated_calendar_stamp_only(account, actions):
    def payload(stamp):
        return {
            "ics": ics("VTODO").replace("END:VTODO", "DTSTAMP:" + stamp + "\r\nEND:VTODO"),
            "direct_tasks_tool": True,
            "uid": "one",
        }

    first = actions.plan("tasks.create", account.id, payload("20260101T000000Z"), "same-create")
    retry = actions.plan("tasks.create", account.id, payload("20260103T000000Z"), "same-create")
    assert retry.id == first.id and retry.payload == first.payload


@pytest.mark.parametrize("code", ["request-timeout", "tls-error"])
def test_ambiguous_mutation_transport_is_not_replayed(account, actions, monkeypatch, code):
    from personal_assistant.nextcloud_onboarding import OnboardingError, SafeNextcloudTransport

    plan = prepare(actions, account.id)
    actions.approve(plan.id)
    sent = []
    accepted = {}

    def timed_out(self, method, path, **kwargs):
        sent.append(method)
        accepted[path] = kwargs["data"]
        raise OnboardingError(code)

    monkeypatch.setattr(SafeNextcloudTransport, "request", timed_out)
    assert actions.execute(plan.id).status == "delivery-uncertain"
    assert actions.approve(plan.id).status == "delivery-uncertain"
    with pytest.raises(PermissionError, match="delivery-uncertain"):
        actions.execute(plan.id)
    assert sent == ["PUT"]
    assert list(accepted.values()) == [plan.payload["ics"].encode()]


def test_history_requires_read_grant_even_if_create_is_granted(account, actions):
    from test_nextcloud_access import grant

    snap = account.store.snapshot()
    selected = next(g.scope for g in snap.policy.grants if g.scope.component == "VTODO")
    update_policy(account.store, [grant(selected.to_dict(), ["create"])])
    from personal_assistant.registry import ResourceRegistry

    registry = ResourceRegistry(account.pa.registry.path)
    fresh = ActionService(
        actions.storage,
        registry,
        PolicyEngine(actions.policy.path, registry),
        type(actions.client)(account.pa.config),
        None,
        None,
        None,
    )
    prepare(fresh, account.id)
    assert actions.storage.list_actions() == []


def test_unrecognized_historical_owner_cannot_release_foreign_payload(account, actions):
    plan = prepare(actions, account.id)
    db = actions.storage.connection
    db.execute(
        "UPDATE action_plans SET action_type='legacy.unknown',payload_json='{}' WHERE id=?", (plan.id,)
    )
    db.commit()
    with pytest.raises(PermissionError, match="scope-unbound"):
        actions.storage.get_action(plan.id)
    assert not actions.storage.list_actions()


def test_mail_duplicate_history_is_independent_of_connection_store(account, actions, monkeypatch):
    storage = actions.storage
    plan = storage.create_action(
        idempotency_key="mail",
        action_type="mail.move",
        resource_id="mail",
        payload={"subject": "Synthetic"},
        requires_approval=True,
    )
    monkeypatch.setenv("OPENCLAW_NEXTCLOUD_CONNECTION_DIR", str(account.store.directory / "missing"))
    monkeypatch.setenv("OPENCLAW_RUNTIME", "container")
    assert storage.list_actions(action_type="mail.move") == [plan]


def test_native_cli_release_keeps_original_connection_evidence(account, monkeypatch, capsys):
    from personal_assistant.nextcloud_native_binding import native_access_projection

    initial = account.store.snapshot()
    monkeypatch.setenv(NATIVE_CALL, "1")
    monkeypatch.setenv(EXPECTED_ACCESS, json.dumps(native_access_projection(initial)))

    def refreshed(args):
        current = account.store.read()
        account.store.save(current, expected_revision=current["revision"])
        cli._print({"private": "synthetic"})
        return 0

    monkeypatch.setattr(cli, "_handle_jobs", refreshed)
    monkeypatch.setattr(cli, "_load_container_role_environment", lambda: False)
    assert cli.main(["jobs", "check", "--target", "all", "--deep"]) != 0
    output = capsys.readouterr()
    assert "policy-changed" in output.err and "synthetic" not in output.out


@pytest.mark.parametrize("outcome", ["rotated", "unchanged", "oversized"])
def test_forwarded_native_doctor_guards_actual_child_output(account, tmp_path, monkeypatch, capfd, outcome):
    import sys
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from time import monotonic

    from personal_assistant.cli_handlers import mail
    from personal_assistant.config import AssistantConfig
    from personal_assistant.nextcloud_native_binding import native_access_projection

    initial = account.store.snapshot()
    monkeypatch.setenv(NATIVE_CALL, "1")
    monkeypatch.setenv(EXPECTED_ACCESS, json.dumps(native_access_projection(initial)))
    # A synthetic role executable emits to both physical pipes before the caller selects a new revision.
    script = tmp_path / "role-child"
    script.write_text(
        f"#!{sys.executable}\n"
        + """
import pathlib, sys, time
assert sys.argv[1:] == ['-m','mail_agent','doctor']
root=pathlib.Path(__file__).parent
print('fixture-doctor-result', flush=True)
print('fixture-diagnostic', file=sys.stderr, flush=True)
(root/'ready').touch()
deadline=time.monotonic()+5
while not (root/'release').exists() and time.monotonic()<deadline:
    time.sleep(.01)
"""
    )
    if outcome == "oversized":
        monkeypatch.setattr(mail, "MAX_CAPTURE_BYTES", 5)
    script.chmod(0o700)
    config_file = tmp_path / "config.toml"
    config_file.touch()
    monkeypatch.setattr(cli, "_load_secrets", lambda *a: None)
    monkeypatch.setattr(cli, "load_config", lambda *a: AssistantConfig())
    monkeypatch.setattr(cli, "_logging", lambda *a: None)
    monkeypatch.setattr(cli, "_record_interactive_activity", lambda *a: None)
    monkeypatch.setattr(sys, "executable", str(script))
    with ThreadPoolExecutor(max_workers=1) as threads:
        result = threads.submit(cli.main, ["--config", str(config_file), "mail", "doctor"])
        deadline = monotonic() + 5
        while not (tmp_path / "ready").exists() and monotonic() < deadline and not result.done():
            Event().wait(0.01)
        assert (tmp_path / "ready").exists()
        if outcome == "rotated":
            current = account.store.read()
            account.store.save(current, expected_revision=current["revision"])
        (tmp_path / "release").touch()
        rc = result.result(timeout=5)
    out, err = capfd.readouterr()
    if outcome == "rotated":
        assert rc != 0 and "policy-changed" in err
        assert "fixture-" not in out + err
    elif outcome == "oversized":
        assert rc != 0 and "output-limit" in err and "fixture-" not in out + err
    else:
        assert rc == 0 and "fixture-doctor-result" in out and "fixture-diagnostic" in err


@pytest.mark.parametrize("observation", [
    "collection-not-found", "occupied", "property-not-found", "foreign-child-404",
    "lookup-404", "empty-lookup", "unrelated-uid",
])
def test_completed_task_requires_conclusive_exact_child_absence(account, actions, monkeypatch, observation):
    from xml.etree import ElementTree as ET

    from test_nextcloud_onboarding import multistatus, q

    from personal_assistant.nextcloud_onboarding import CALDAV, DAV

    plan = prepare(actions, account.id)
    actions.approve(plan.id)
    href = account.href + "one.ics"
    account.replies["PUT"] = HttpResponse(201, {}, b"")
    account.replies["REPORT"] = HttpResponse(207, {}, report(href, "VTODO"))
    completed = actions.execute(plan.id)
    assert completed.status == "completed"
    original = actions.client._managed_request
    probes = []

    def serve(method, target, headers, data, timeout, **kwargs):
        if method == "REPORT":
            query = ET.fromstring(data)
            if query.tag == q(CALDAV, "calendar-query"):
                assert query.findtext(".//" + q(CALDAV, "text-match")) == "one"
                body = report(account.href + "other.ics", "VTODO").replace(b"UID:one", b"UID:other")
                account.replies[method] = HttpResponse(
                    404 if observation == "collection-not-found" else 207, {},
                    body if observation == "unrelated-uid" else multistatus([]),
                )
            else:
                assert query.tag == q(CALDAV, "calendar-multiget") and headers["Depth"] == "0"
                assert query.findtext(q(DAV, "href")) == href
                assert [node.tag for node in query.find(q(DAV, "prop"))] == [q(DAV, "getetag")]
                probes.append(href)
                body = multistatus([(href, {q(DAV, "getetag"): '"replacement"'})])
                if observation == "foreign-child-404":
                    body = missing_child(account.href + "foreign.ics")
                if observation == "property-not-found":
                    body = body.replace(b"200 OK", b"404 Not Found")
                if observation == "empty-lookup":
                    body = multistatus([])
                account.replies[method] = HttpResponse(404 if observation == "lookup-404" else 207, {}, body)
        return original(method, target, headers, data, timeout, **kwargs)

    monkeypatch.setattr(actions.client, "_managed_request", serve)
    for renew in (False, True):
        with pytest.raises(PermissionError) as exc:
            actions.execute_task_create(plan.id, recreate_missing=renew)
        if observation == "occupied":
            assert "target-mismatch" in str(exc.value)
        else:
            assert str(exc.value) in {"rights-unverified", "scope-invalid"}
        assert actions.storage.get_action(plan.id) == completed
        assert [m for m, _, _ in account.calls].count("PUT") == 1
    assert len(probes) == (0 if observation in {"collection-not-found", "unrelated-uid"} else 2)
