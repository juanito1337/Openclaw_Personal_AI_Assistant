from __future__ import annotations

import hashlib
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from test_nextcloud_connection import connection

from personal_assistant import nextcloud_connection as nc


def core():
    from personal_assistant import nextcloud_access

    return nextcloud_access


def managed(tmp_path, initial="fresh"):
    directory = tmp_path / "mount"
    directory.mkdir(mode=0o700)
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    store = nc.ConnectionStore(directory, require_mode=True)
    nc.initialize_access_store(state, directory, transition=initial)
    return store


def grant(scope, operations):
    return {"scope": scope, "operations": sorted(operations)}


def scope(conn, component="VEVENT", path=None, file_id=None):
    resource = (
        nc.account_namespace(conn) + "-files"
        if component == "FILES"
        else nc.collection_resource_id(conn, conn["discovery_snapshot"]["calendars"][0]["href"])
    )
    return {"resource_id": resource, "component": component, "path": path, "file_id": file_id}


def measured(user="assistant"):
    conn = connection(user)
    discovery = conn["discovery_snapshot"]
    discovery["checked_at"] = datetime.now(UTC).isoformat()
    discovery["server_url"] = conn["server_url"]
    discovery["account_id"] = hashlib.sha256((conn["server_url"] + "\0" + user).encode()).hexdigest()
    for row in discovery["calendars"]:
        row["privileges_verified"] = True
        row["read_check"] = {"status": "ok", "permissions": ["read", "create", "update"]}
    discovery["folders"] = [
        {
            "path": path,
            "file_id": file_id,
            "checked_at": discovery["checked_at"],
            "privileges_verified": True,
            "file_permissions": "GC",
            "href": f"/nc/remote.php/dav/files/{user}/{path}/",
            "can_read": True,
            "can_create_file": True,
            "can_mkdir": False,
            "read_check": {"status": "ok"},
        }
        for path, file_id in (("Belege", "11"), ("Belege/Privat", "12"), ("BelegeAlt", "13"))
    ]
    return conn


def update_policy(store, grants):
    before = store.snapshot()
    policy = {**before.policy.to_dict(), "grants": grants}
    store.save(
        {**before.connection, "access_policy": policy},
        expected_revision=before.connection["revision"],
        expected_policy_revision=before.binding.policy_revision,
    )
    return store.snapshot()


@pytest.mark.parametrize("loss", ["record", "v1", "mode", "lock", "mount", "damaged-mode", "older-policy"])
def test_active_policy_loss_never_restores_legacy(tmp_path, monkeypatch, loss):
    store = managed(tmp_path)
    conn = measured()
    store.save(conn, expected_revision="")
    first = store.read()
    update_policy(store, [grant(scope(conn), ["list"])])
    record = store.directory / "connection.json"
    if loss in {"record", "mode", "lock"}:
        (store.directory / {"record": "connection.json", "mode": "mode.json", "lock": ".lock"}[loss]).unlink()
    elif loss == "mount":
        store.directory.rename(tmp_path / "unmounted")
    elif loss == "damaged-mode":
        (store.directory / "mode.json").write_text("{}")
    else:
        record.write_text(json.dumps(conn if loss == "v1" else first))
    monkeypatch.setenv("OPENCLAW_ROLE", "setup-service")
    monkeypatch.setenv("OPENCLAW_NEXTCLOUD_CONNECTION_DIR", str(store.directory))
    with pytest.raises(nc.ConnectionError, match="policy-integrity-error"):
        nc.load_connection()
    with pytest.raises(nc.ConnectionError, match="policy-integrity-error"):
        store.save(conn, expected_revision="")
    assert not (loss == "lock" and (store.directory / ".lock").exists())


def test_account_switch_back_requires_new_generation(tmp_path):
    store = managed(tmp_path)
    store.save(measured(), expected_revision="")
    first = update_policy(store, [grant(scope(measured()), ["list"])])
    for user in ("other", "assistant"):
        before = store.snapshot()
        store.save(measured(user), expected_revision=before.connection["revision"])
        assert not store.snapshot().policy.grants
        assert store.read()["talk"]["enabled"] is False
    with pytest.raises(nc.ConnectionError, match="policy-changed"), store.admit(first.binding):
        pass
    assert first.binding.generation != store.snapshot().binding.generation


def test_nested_off_and_replaced_file_id(tmp_path):
    store = managed(tmp_path)
    conn = measured()
    store.save(conn, expected_revision="")
    outer = scope(conn, "FILES", "Belege", "11")
    inner = scope(conn, "FILES", "Belege/Privat", "12")
    snap = update_policy(store, [grant(outer, ["list", "create_file"]), grant(inner, [])])
    decision = core().decide_access
    assert decision(snap, outer, "create_file", target_path="Belege/neue Datei").allowed
    assert decision(snap, outer, "list", target_path="Belege/Privat/test").code == "scope-off"
    assert not decision(snap, outer, "list", target_path="BelegeAlt/test").allowed
    changed = store.read()
    changed["discovery_snapshot"]["folders"][0]["file_id"] = "99"
    store.save(changed, expected_revision=changed["revision"])
    assert decision(store.snapshot(), outer, "list").code == "folder-identity-changed"


@pytest.mark.parametrize(
    "href",
    [
        "https://other.test/nc/remote.php/dav/files/assistant/A/",
        "/nc/remote.php/dav/files/other/A/",
        "/nc/remote.php/dav/files/assistant//A/",
        "/nc/remote.php/dav/files/assistant/Z/../A/",
        "/other/remote.php/dav/files/assistant/A/",
        "/nc/remote.php/dav/files/assistant/%FF/",
        "/nc/remote.php/dav/files/assistant/A%252F/",
        "/nc/remote.php/dav/files/assistant/A%5C/",
        "/nc/remote.php/dav/files/assistant/A/?",
        "/nc/remote.php/dav/files/assistant/A%2f/",
        "/nc/remote.php/dav/files/assistant/A/#x",
    ],
)
def test_folder_href_cannot_alias_another_scope(href):
    conn = measured()
    row = conn["discovery_snapshot"]["folders"][0]
    row["href"] = href
    with pytest.raises(ValueError, match="scope-invalid"):
        core().normalize_scope(conn, scope(conn, "FILES", "Belege", "11"), conn["discovery_snapshot"])


def test_verified_unicode_space_and_comma_round_trip():
    conn = measured()
    row = conn["discovery_snapshot"]["folders"][0]
    row.update(path="Grüße, Juni", href="/nc/remote.php/dav/files/assistant/Gr%C3%BC%C3%9Fe%2C%20Juni/")
    raw = scope(conn, "FILES", row["path"], "11")
    assert core().normalize_scope(conn, raw, conn["discovery_snapshot"]).to_dict() == raw


def test_legacy_preview_intersects_effective_operations(tmp_path):
    store = nc.ConnectionStore(tmp_path / "mount")
    conn = measured()
    store.save(conn, expected_revision="")
    state = tmp_path / "state"
    state.mkdir()
    nc.initialize_access_store(state, store.directory, transition="verified-pre-policy")
    snap = store.snapshot()
    assert snap.mode == "legacy"
    raw = scope(conn)
    proof = {
        "account_id": nc.account_namespace(conn),
        "owner_user_id": conn["user_id"],
        "connection_revision": snap.connection["revision"],
        "grants": [grant(raw, ["list", "read"])],
        "account_root_confirmed": False,
    }
    policy, withheld = core().migration_candidate(snap, proof)
    assert [g.to_dict() for g in policy.grants] == [grant(raw, ["list", "read"])]
    assert not withheld
    assert store.snapshot().mode == "legacy"
    unproven, withheld = core().migration_candidate(snap, {})
    assert not unproven.grants and withheld


def test_publication_crash_and_exact_owner_recovery(tmp_path, monkeypatch):
    store = managed(tmp_path)
    publish = store._publish

    def fail_final(filename, data):
        if filename == "mode.json" and json.loads(data)["mode"] == "active":
            raise OSError("interrupted")
        publish(filename, data)

    monkeypatch.setattr(store, "_publish", fail_final)
    with pytest.raises(nc.ConnectionError):
        store.save(measured(), expected_revision="")
    with pytest.raises(nc.ConnectionError, match="policy-integrity-error"):
        store.snapshot()
    monkeypatch.setattr(store, "_publish", publish)
    store.recover()
    assert store.snapshot().mode == "active"


def test_shared_admission_serializes_publish_then_one_cas_wins(tmp_path):
    store = managed(tmp_path)
    store.save(measured(), expected_revision="")
    first = store.snapshot()
    started = threading.Event()

    def publish():
        started.set()
        return store.save(
            first.connection,
            expected_revision=first.connection["revision"],
            expected_policy_revision=first.binding.policy_revision,
        )

    with ThreadPoolExecutor(2) as pool:
        with store.admit(first.binding):
            both = [pool.submit(publish) for _ in range(2)]
            assert started.wait(2)
            assert not any(f.done() for f in both)
        outcomes = [f.exception(timeout=4) for f in both]
    assert sum(error is None for error in outcomes) == 1
    assert any("conflict" in str(error) for error in outcomes)


def test_effective_rights_are_component_specific_and_expire(tmp_path):
    store = managed(tmp_path)
    conn = measured()
    store.save(conn, expected_revision="")
    raw = scope(conn)
    snap = update_policy(store, [grant(raw, ["list", "read", "update"])])
    assert core().decide_access(snap, raw, "update").allowed
    assert core().decide_access(snap, {**raw, "component": "VTODO"}, "update").code == "scope-off"
    changed = store.read()
    changed["discovery_snapshot"]["calendars"][0]["read_check"]["permissions"] = ["read"]
    store.save(changed, expected_revision=changed["revision"])
    assert core().decide_access(store.snapshot(), raw, "update").code == "server-right-missing"
    changed = store.read()
    changed["discovery_snapshot"]["checked_at"] = (datetime.now(UTC) - timedelta(days=4)).isoformat()
    store.save(changed, expected_revision=changed["revision"])
    assert core().decide_access(store.snapshot(), raw, "read").code == "rights-stale"


def test_redeploy_cannot_reinitialize_lost_files(tmp_path):
    store = managed(tmp_path)
    state = tmp_path / "state"
    receipt = state / ".layout-migrations/nextcloud-access-v1.json"
    initial = json.loads(receipt.read_text())
    assert initial["status"] == "ready"
    nc.initialize_access_store(state, store.directory, transition="verify")
    (store.directory / "mode.json").unlink()
    with pytest.raises(nc.ConnectionError, match="policy-integrity-error"):
        nc.initialize_access_store(state, store.directory, transition="fresh")
    assert not (store.directory / "mode.json").exists()
    receipt.unlink()
    with pytest.raises(nc.ConnectionError, match="policy-integrity-error"):
        nc.initialize_access_store(state, store.directory, transition="verify")


def test_first_confirmed_login_cannot_import_a_nonempty_policy(tmp_path):
    store = managed(tmp_path)
    conn = measured()
    raw = core().new_policy(conn).to_dict()
    raw["grants"] = [grant(scope(conn), ["update"])]
    with pytest.raises(nc.ConnectionError):
        store.save({**conn, "access_policy": raw}, expected_revision="", expected_policy_revision="")
    assert store.snapshot().mode == "unconfigured"


def test_new_grant_requires_current_right_and_verified_root(tmp_path):
    store = managed(tmp_path)
    conn = measured()
    row = conn["discovery_snapshot"]["file_roots"][0]
    row.update(path="", file_id="1", read_check={"status": "ok"})
    store.save(conn, expected_revision="")
    with pytest.raises(nc.ConnectionError, match="account-root-confirmation-required"):
        update_policy(store, [grant(scope(conn, "FILES", "", "1"), ["read"])])
    with pytest.raises(nc.ConnectionError, match="server-right-missing"):
        update_policy(store, [grant(scope(conn, "FILES", "Belege", "11"), ["mkdir"])])
    assert not store.snapshot().policy.grants


@pytest.mark.parametrize(
    "corrupt",
    ["extra-field", "duplicate-grant", "duplicate-op", "future-op", "wrong-owner", "invalid-generation"],
)
def test_invalid_policy_never_publishes(tmp_path, corrupt):
    store = managed(tmp_path)
    conn = measured()
    store.save(conn, expected_revision="")
    first = store.snapshot()
    policy = first.policy.to_dict()
    entry = grant(scope(conn), ["read"])
    policy["grants"] = [entry]
    if corrupt == "extra-field":
        policy["root"] = "*"
    elif corrupt == "duplicate-grant":
        policy["grants"].append(entry)
    elif corrupt == "duplicate-op":
        entry["operations"] = ["read", "read"]
    elif corrupt == "future-op":
        entry["operations"] = ["delete"]
    elif corrupt == "wrong-owner":
        policy["owner_user_id"] = "other"
    else:
        policy["generation"] = "old"
    with pytest.raises(nc.ConnectionError):
        store.save(
            {**first.connection, "access_policy": policy},
            expected_revision=first.connection["revision"],
            expected_policy_revision=first.binding.policy_revision,
        )
    assert store.snapshot().binding == first.binding
    assert not store.snapshot().policy.grants


def test_partial_preparation_can_only_finalize_matching_pair(tmp_path):
    store = managed(tmp_path)
    state = tmp_path / "state"
    receipt = state / ".layout-migrations/nextcloud-access-v1.json"
    ready = json.loads(receipt.read_text())
    receipt.write_text(json.dumps({**ready, "status": "preparing"}))
    nc.initialize_access_store(state, store.directory, transition="fresh")
    assert json.loads(receipt.read_text()) == ready
    receipt.write_text(json.dumps({**ready, "installation_id": uuid4().hex, "status": "preparing"}))
    with pytest.raises(nc.ConnectionError, match="policy-integrity-error"):
        nc.initialize_access_store(state, store.directory, transition="verify")


def test_partial_revocation_never_restores_previous_grants(tmp_path, monkeypatch):
    store = managed(tmp_path)
    conn = measured()
    store.save(conn, expected_revision="")
    first = update_policy(store, [grant(scope(conn), ["update"])])
    original = store._publish

    def interrupt(filename, data):
        if filename == "connection.json":
            raise OSError()
        original(filename, data)

    monkeypatch.setattr(store, "_publish", interrupt)
    with pytest.raises(nc.ConnectionError, match="publication-failed"):
        update_policy(store, [])
    with pytest.raises(nc.ConnectionError, match="policy-integrity-error"), store.admit(first.binding):
        pass
    with pytest.raises(nc.ConnectionError, match="policy-integrity-error"):
        store.recover()


def test_migration_preserves_independent_files_when_calendar_evidence_is_partial(tmp_path):
    store = nc.ConnectionStore(tmp_path / "mount")
    conn = measured()
    conn["discovery_snapshot"]["calendars"][0]["read_check"] = {"status": "failed"}
    stored = store.save(conn, expected_revision="")
    selected_file = grant(scope(conn, "FILES", "Belege", "11"), ["read"])
    proof = {
        "account_id": nc.account_namespace(conn),
        "owner_user_id": conn["user_id"],
        "connection_revision": stored["revision"],
        "grants": [grant(scope(conn), ["read"]), selected_file],
    }
    policy, withheld = core().migration_candidate(store.snapshot(), proof)
    assert selected_file in [g.to_dict() for g in policy.grants]
    assert withheld


@pytest.mark.parametrize("invalid_id", ["root", "0", "001", "1" * 33, "١١"])
def test_folder_identity_requires_the_verified_dav_fileid_format(invalid_id):
    conn = measured()
    row = conn["discovery_snapshot"]["folders"][0]
    row["file_id"] = invalid_id
    with pytest.raises(ValueError, match="scope-invalid"):
        core().normalize_scope(
            conn, scope(conn, "FILES", row["path"], invalid_id), conn["discovery_snapshot"]
        )


@pytest.mark.parametrize(
    "restriction, expected",
    [
        ({"can_create_file": False, "file_permissions": "G"}, "server-right-missing"),
        ({"read_check": {"status": "failed"}}, "rights-unverified"),
        ({"checked_at": "2000-01-01T00:00:00+00:00"}, "rights-stale"),
        ({"privileges_verified": False}, "rights-unverified"),
    ],
)
def test_inherited_grant_checks_exact_child_rights(tmp_path, restriction, expected):
    store = managed(tmp_path)
    conn = measured()
    store.save(conn, expected_revision="")
    parent = scope(conn, "FILES", "Belege", "11")
    child = scope(conn, "FILES", "Belege/Privat", "12")
    snap = update_policy(store, [grant(parent, ["create_file"])])
    assert core().decide_access(snap, child, "create_file", target_path="Belege/Privat/foo").allowed
    snap.connection["discovery_snapshot"]["folders"][1].update(restriction)
    assert core().decide_access(snap, child, "create_file", target_path="Belege/Privat/foo").code == expected


@pytest.mark.parametrize("missing", ["checked_at", "file_permissions", "privileges_verified"])
def test_file_evidence_requires_own_time_and_oc_permissions(tmp_path, missing):
    store = managed(tmp_path)
    conn = measured()
    store.save(conn, expected_revision="")
    parent = scope(conn, "FILES", "Belege", "11")
    snap = update_policy(store, [grant(parent, ["read"])])
    row = snap.connection["discovery_snapshot"]["folders"][0]
    row.pop(missing)
    # Old discovery used generic DAV read here even without oc:permissions.
    row.update(can_read=True, permissions=["read"], read_check={"status": "ok"})
    assert core().decide_access(snap, parent, "read").code == "rights-unverified"


@pytest.mark.parametrize("foreign", ["server_url", "account_id", "both", "missing"])
def test_account_bound_evidence_cannot_authorize_other_host_or_legacy_migration(tmp_path, foreign):
    from personal_assistant.nextcloud_access import AccessSnapshot

    store = managed(tmp_path)
    conn = measured()
    store.save(conn, expected_revision="")
    raw = scope(conn)
    snap = update_policy(store, [grant(raw, ["read"])])
    inv = snap.connection["discovery_snapshot"]
    if foreign in {"server_url", "both"}:
        inv["server_url"] = "https://other.invalid/nc"
    if foreign in {"account_id", "both"}:
        inv["account_id"] = hashlib.sha256(b"https://other.invalid/nc\0assistant").hexdigest()
    if foreign == "missing":
        inv.pop("account_id")
    assert core().decide_access(snap, raw, "read").code == "scope-unbound"
    with pytest.raises(nc.ConnectionError, match="policy-integrity-error"):
        store.save(snap.connection, expected_revision=snap.connection["revision"])
    legacy = AccessSnapshot("legacy", snap.connection)
    policy, withheld = core().migration_candidate(
        legacy,
        {
            "account_id": nc.account_namespace(conn),
            "owner_user_id": conn["user_id"],
            "connection_revision": snap.connection["revision"],
            "grants": [grant(raw, ["read"])],
        },
    )
    assert not policy.grants and withheld


def test_pre_policy_root_dav_read_with_no_oc_flags_cannot_migrate():
    conn = measured()
    root = conn["discovery_snapshot"]["file_roots"][0]
    # v1's _resource could keep generic DAV privileges if oc:permissions was absent.
    root.update(
        path="",
        file_id="1",
        can_read=True,
        can_create=True,
        privileges_verified=True,
        read_check={"status": "ok"},
        permissions=["read", "create"],
    )
    scope_root = scope(conn, "FILES", "", "1")
    snapshot = core().AccessSnapshot("legacy", conn)
    proof = {
        "account_id": nc.account_namespace(conn),
        "owner_user_id": conn["user_id"],
        "connection_revision": conn["revision"],
        "account_root_confirmed": True,
        "grants": [grant(scope_root, ["read"])],
    }
    policy, withheld = core().migration_candidate(snapshot, proof)
    assert withheld and not any(g.operations for g in policy.grants)
    root.update(file_permissions="G", can_mkdir=False, can_create_file=False)
    policy, withheld = core().migration_candidate(snapshot, proof)
    assert not withheld and policy.grants[0].operations == ("read",)
