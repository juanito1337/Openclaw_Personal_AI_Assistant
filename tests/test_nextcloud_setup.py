from __future__ import annotations

import http.client
import json
import socket
import subprocess
import threading
import time
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from personal_assistant.nextcloud_connection import ConnectionError, ConnectionStore
from personal_assistant.nextcloud_onboarding import HttpResponse, OnboardingError, normalize_server_url
from personal_assistant.nextcloud_setup import NextcloudSetupService, SetupServer, check_socket
from personal_assistant.setup_operator_proof import OperatorProofVerifier

PASSWORD = "fixture-app-password-never-in-ui"

SIGN_JS = """
const c=require('node:crypto');
const v=JSON.parse(require('node:fs').readFileSync(0,'utf8'));
const key=c.createPrivateKey({key:Buffer.concat([Buffer.from('302e020100300506032b657004220420','hex'),
 Buffer.alloc(32,v.seed||7)]),format:'der',type:'pkcs8'});
const raw=c.createPublicKey(key).export({format:'der',type:'spki'}).subarray(-32);
console.log(JSON.stringify({id:c.createHash('sha256').update(raw).digest('hex'),
 publicKey:raw.toString('base64url'),signature:c.sign(null,Buffer.from(v.payload||''),key).toString('base64url')}));
"""


def sign_fixture(payload="", *, seed=7):
    result = subprocess.run(
        ["node", "-e", SIGN_JS],
        input=json.dumps({"payload": payload, "seed": seed}),
        text=True,
        capture_output=True,
        check=True,
        timeout=5,
    )
    return json.loads(result.stdout)


@pytest.fixture
def operator_proof(tmp_path):
    identity = sign_fixture()
    registry = tmp_path / "paired.json"
    device = {
        "deviceId": identity["id"],
        "publicKey": identity["publicKey"],
        "role": "operator",
        "approvedScopes": ["operator.admin"],
        "tokens": {
            "operator": {
                "token": "fixture-device-token-not-used",
                "role": "operator",
                "scopes": ["operator.admin"],
            }
        },
    }
    registry.write_text(json.dumps({identity["id"]: device}))
    registry.chmod(0o600)
    verifier = OperatorProofVerifier(registry, replay_path=tmp_path / "nonces.sqlite3", clock=lambda: 1000)

    def request(operation="status", parameters=None, *, signing_seed=7, **overrides):
        signed = {
            "operation": operation,
            "parameters": parameters or {},
            "actor": "device:" + identity["id"],
            "issued_at": 1_000_000,
            "nonce": uuid.uuid4().hex,
            **overrides,
        }
        payload = json.dumps(signed)
        return {key: signed[key] for key in ("operation", "parameters", "actor")} | {
            "proof": {"payload": payload, "signature": sign_fixture(payload, seed=signing_seed)["signature"]}
        }

    return verifier, request, registry


class Transport:
    def __init__(self, base_url, **kwargs):
        self.base_url = normalize_server_url(base_url)
        self.expected_ips = ("192.168.2.3",)
        self.calls = []

    def resolve_url(self, url):
        return url

    def request(self, method, path, **kwargs):
        self.calls.append((method, path))
        if path == "index.php/login/v2":
            data = {
                "login": self.base_url + "/login/flow",
                "poll": {"token": "fixture-poll-token-12345", "endpoint": self.base_url + "/login/poll"},
            }
        elif path.endswith("/login/poll"):
            data = {
                "server": self.base_url,
                "loginName": "assistant@example.invalid",
                "appPassword": PASSWORD,
            }
        elif "/participants?" in path:
            data = {
                "ocs": {
                    "meta": {"status": "ok", "statuscode": 200},
                    "data": [
                        {"actorType": "users", "actorId": "jan", "displayName": "Jan"},
                        {"actorType": "guests", "actorId": "guest", "displayName": "Guest"},
                        {"actorType": "users", "actorId": "assistant", "displayName": "Assistent"},
                    ],
                }
            }
        else:
            raise AssertionError(path)
        return HttpResponse(200, {}, json.dumps(data).encode())


def discovery(transport, credentials):
    assert credentials.app_password == PASSWORD
    return {
        "server_url": transport.base_url,
        "user_id": "assistant",
        "complete": True,
        "calendars": [],
        "task_lists": [],
        "file_roots": [],
        "folders": [],
        "errors": [],
        "limitations": [],
        "talk_rooms": [{"token": "room01", "name": "Jan und Assistent", "can_read": True, "can_send": True}],
        "capabilities": {"spreed": {"features": ["chat-reference-id"]}},
    }


@pytest.fixture
def setup_service(tmp_path):
    return NextcloudSetupService(
        ConnectionStore(tmp_path / "store"),
        transport_factory=Transport,
        discovery=discovery,
        clock=lambda: 100.0,
    )


def ready(service, actor="device-one"):
    start = service.dispatch("login.start", {"server_url": "https://cloud.example/nc"}, actor=actor)
    result = service.dispatch("login.poll", {"flow_id": start["flow_id"]}, actor=actor)
    assert result["state"] == "ready"
    return result


def apply(service, preview, actor="device-one"):
    return service.dispatch(
        "connection.apply",
        {
            "flow_id": preview["flow_id"],
            "preview_digest": preview["preview_digest"],
            "expected_revision": preview["expected_revision"],
            "confirm_account_access": True,
        },
        actor=actor,
    )


def test_real_login_flow_to_atomic_store_without_credentials_in_gui(setup_service):
    assert not setup_service.status()["connected"]
    preview = ready(setup_service)
    assert setup_service.store.read() is None
    assert PASSWORD not in json.dumps(preview)
    status = apply(setup_service, preview)
    assert status["connected"] and status["user_id"] == "assistant"
    assert PASSWORD not in json.dumps(status)
    assert "app_password" not in json.dumps(status)
    assert setup_service.store.read()["login_name"] == "assistant@example.invalid"
    assert setup_service.store.read()["app_password"] == PASSWORD
    assert status["talk"]["enabled"] is False
    with pytest.raises(OnboardingError, match="Anmeldevorgang"):
        apply(setup_service, preview)


def test_cross_operator_and_unconfirmed_apply_cannot_publish(setup_service):
    preview = ready(setup_service)
    with pytest.raises(OnboardingError):
        apply(setup_service, preview, actor="different-device")
    with pytest.raises(OnboardingError):
        setup_service.dispatch(
            "connection.apply",
            {
                "flow_id": preview["flow_id"],
                "preview_digest": preview["preview_digest"],
                "expected_revision": "",
                "confirm_account_access": False,
            },
            actor="device-one",
        )
    assert setup_service.store.read() is None
    with pytest.raises(OnboardingError):
        apply(setup_service, {**preview, "preview_digest": "wrong"})
    assert setup_service.store.read() is None


def test_concurrent_operator_preview_is_revision_bound(setup_service):
    first = ready(setup_service)
    second = ready(setup_service, actor="device-two")
    apply(setup_service, first)
    with pytest.raises(ConnectionError, match="conflict"):
        apply(setup_service, second, actor="device-two")


def test_flow_expiry_cancel_and_replacement_clear_pending_credentials(setup_service):
    preview = ready(setup_service)
    old = setup_service.flows[preview["flow_id"]]
    replacement = setup_service.dispatch(
        "login.start", {"server_url": "https://cloud.example/nc"}, actor="device-one"
    )
    assert preview["flow_id"] not in setup_service.flows
    assert old.flow.poll()["state"] == "cancelled"
    assert old.credentials is None
    assert old.snapshot is None
    setup_service.clock = lambda: 2000
    with pytest.raises(OnboardingError):
        setup_service.dispatch("login.poll", {"flow_id": replacement["flow_id"]}, actor="device-one")
    assert not setup_service.flows
    assert setup_service.store.read() is None


def test_failed_discovery_discards_consumed_credentials_and_requires_new_login(setup_service):
    def failed(transport, credentials):
        raise OnboardingError("authentication-failed", PASSWORD)

    setup_service.discovery = failed
    start = setup_service.dispatch(
        "login.start", {"server_url": "https://cloud.example/nc"}, actor="device-one"
    )
    pending = setup_service.flows[start["flow_id"]]
    result = setup_service.dispatch("login.poll", {"flow_id": start["flow_id"]}, actor="device-one")
    assert (result["state"], result["code"]) == ("failed", "authentication-failed")
    assert result["diagnostics"]["user"]["loaded"] is False
    assert pending.credentials is None and pending.snapshot is None
    assert pending.flow.poll()["state"] == "cancelled"
    assert not setup_service.flows
    with pytest.raises(OnboardingError):
        setup_service.dispatch("login.poll", {"flow_id": start["flow_id"]}, actor="device-one")
    assert sum(path.endswith("/login/poll") for _, path in pending.transport.calls) == 1


def credential_preview(service, actor="device-one", **changes):
    return service.dispatch(
        "login.credentials",
        {
            "server_url": "https://cloud.example/nc",
            "login_name": "assistant@example.invalid",
            "app_password": PASSWORD,
            **changes,
        },
        actor=actor,
    )


def test_app_password_uses_pinned_transport_and_requires_actor_bound_confirmation(setup_service):
    seen = []

    def checked(transport, credentials):
        seen.append((transport, credentials))
        return discovery(transport, credentials)

    setup_service.discovery = checked
    preview = credential_preview(setup_service)
    assert preview["state"] == "ready"
    assert PASSWORD not in json.dumps(preview)
    assert setup_service.store.read() is None
    transport, credentials = seen[0]
    assert transport.base_url == "https://cloud.example/nc"
    assert credentials.login_name == "assistant@example.invalid" and credentials.user_id == ""
    assert transport.calls == []  # Direct credentials never request a login-v2 token.
    assert setup_service.flows[preview["flow_id"]].flow is None
    with pytest.raises(OnboardingError):
        apply(setup_service, preview, actor="another-device")
    with pytest.raises(OnboardingError):
        apply(setup_service, {**preview, "preview_digest": "f" * 64})
    with pytest.raises(ConnectionError):
        apply(setup_service, {**preview, "expected_revision": "stale"})
    assert setup_service.store.read() is None
    status = apply(setup_service, preview)
    assert status["connected"] and PASSWORD not in json.dumps(status)
    stored = setup_service.store.read()
    assert stored["expected_ips"] == ["192.168.2.3"] and stored["app_password"] == PASSWORD
    assert not setup_service.flows


@pytest.mark.parametrize(
    "change",
    [
        {"app_password": ""},
        {"app_password": "x" * 4097},
        {"app_password": "bad\nvalue"},
        {"app_password": "bad\x7fvalue"},
        {"app_password": 17},
        {"login_name": ""},
        {"login_name": "x" * 513},
        {"login_name": "bad:name"},
        {"login_name": "bad\rname"},
        {"server_url": "http://cloud.example/nc"},
        {"unknown": "ignored"},
    ],
)
def test_credential_fields_are_bounded_before_discovery(setup_service, change):
    def forbidden(*args):
        pytest.fail("invalid credentials reached discovery")

    setup_service.discovery = forbidden
    try:
        result = credential_preview(setup_service, **change)
        assert result["state"] == "failed"
    except OnboardingError as exc:
        assert exc.code == "invalid-credentials"
    assert not setup_service.flows and setup_service.store.read() is None


@pytest.mark.parametrize("method", ["login-v2", "credentials"])
def test_failed_replacement_clears_previous_preview_and_preserves_current_connection(setup_service, method):
    apply(setup_service, ready(setup_service))
    original = setup_service.store.read()
    old_preview = credential_preview(setup_service)
    pending = setup_service.flows[old_preview["flow_id"]]

    def rejected(*args):
        error = OnboardingError("authentication-failed", "upstream reflected " + PASSWORD)
        error.diagnostics = {
            "schema_version": 1,
            "user": {"status": "failed", "loaded": False, "code": "authentication-failed"},
            "calendars": {"status": "skipped"},
            "task_lists": {"status": "skipped"},
            "files": {"status": "skipped"},
            "upstream_body": PASSWORD,
        }
        raise error

    setup_service.discovery = rejected
    if method == "credentials":
        result = credential_preview(setup_service)
    else:
        start = setup_service.dispatch(
            "login.start", {"server_url": "https://cloud.example/nc"}, actor="device-one"
        )
        result = setup_service.dispatch("login.poll", {"flow_id": start["flow_id"]}, actor="device-one")
    assert result["state"] == "failed" and result["diagnostics"]["user"]["loaded"] is False
    assert "upstream_body" not in result["diagnostics"]
    assert PASSWORD not in json.dumps(result)
    assert pending.credentials is None and pending.snapshot is None and pending.preview_digest == ""
    assert not setup_service.flows and setup_service.store.read() == original


def test_partial_resource_errors_are_ready_and_reflected_password_is_redacted(setup_service):
    def partial(transport, credentials):
        return {
            **discovery(transport, credentials),
            "complete": False,
            "display_name": "upstream " + PASSWORD,
            "errors": [{"domain": "files", "code": "permission-denied"}],
            "calendars": [{"name": PASSWORD, "nested": [PASSWORD]}],
            "capabilities": {"spreed": {"features": [PASSWORD, "chat-reference-id"]}},
            "diagnostics": {"schema_version": 1, "user": {"status": "ok", "loaded": True}},
            "checked_at": "2026-10-25T10:00:00+00:00",
            "results_may_be_truncated": True,
        }

    setup_service.discovery = partial
    preview = credential_preview(setup_service)
    assert preview["state"] == "ready" and not preview["preview"]["complete"]
    assert preview["preview"]["diagnostics"]["user"]["loaded"] is True
    assert preview["preview"]["results_may_be_truncated"] is True
    assert preview["preview"]["checked_at"] == "2026-10-25T10:00:00+00:00"
    assert PASSWORD not in json.dumps(preview)
    assert PASSWORD not in json.dumps(apply(setup_service, preview))
    assert PASSWORD not in json.dumps(setup_service.status())


@pytest.mark.parametrize("method", ["login-v2", "credentials"])
def test_talk_room_id_cannot_reflect_password_in_preview_or_stored_status(setup_service, method):
    def reflected(transport, credentials):
        snapshot = discovery(transport, credentials)
        snapshot["talk_rooms"] = [
            {
                "token": PASSWORD,
                "resource_id": "nextcloud-talk-" + PASSWORD,
                "name": PASSWORD,
                "can_read": True,
                "can_send": True,
            }
        ]
        return snapshot

    setup_service.discovery = reflected
    preview = ready(setup_service) if method == "login-v2" else credential_preview(setup_service)
    room = preview["preview"]["talk_rooms"][0]
    assert room["resource_id"] == "nextcloud-talk-[redacted]"
    assert room["token"] == "[redacted]" and room["name"] == "[redacted]"
    assert PASSWORD not in json.dumps(preview)
    status = apply(setup_service, preview)
    assert PASSWORD not in json.dumps(status)
    assert PASSWORD not in json.dumps(setup_service.status())


def test_credential_cancel_expiry_and_publication_conflict_destroy_staged_secrets(setup_service):
    first = credential_preview(setup_service)
    pending = setup_service.flows[first["flow_id"]]
    assert setup_service.dispatch("login.cancel", {"flow_id": first["flow_id"]}, actor="device-one") == {
        "state": "cancelled"
    }
    assert pending.credentials is None and not setup_service.flows
    second = credential_preview(setup_service)
    pending = setup_service.flows[second["flow_id"]]
    setup_service.clock = lambda: 2000
    setup_service._prune()
    assert pending.credentials is None and not setup_service.flows
    setup_service.clock = lambda: 100
    third = credential_preview(setup_service)
    other = credential_preview(setup_service, actor="device-two")
    apply(setup_service, other, actor="device-two")
    original = setup_service.store.read()
    pending = setup_service.flows[third["flow_id"]]
    with pytest.raises(ConnectionError):
        apply(setup_service, third)
    assert pending.credentials is None and not setup_service.flows
    assert setup_service.store.read() == original


def test_unexpected_discovery_exception_clears_flow_and_hides_secret(setup_service):
    def failed(*args):
        raise RuntimeError(PASSWORD)

    setup_service.discovery = failed
    with pytest.raises(OnboardingError) as error:
        credential_preview(setup_service)
    assert PASSWORD not in str(error.value) and error.value.code == "setup-unavailable"
    assert not setup_service.flows


@pytest.mark.parametrize("password", ["a", "state", "status", "revision", "failed"])
def test_short_password_redaction_preserves_protocol_fields_and_confirmation(setup_service, password):
    def reflected(transport, credentials):
        value = discovery(
            transport, type(credentials)(credentials.server_url, credentials.login_name, PASSWORD)
        )
        value["display_name"] = password
        value["calendars"] = [{"resource_id": "nextcloud-calendar-" + "a" * 12}]
        value["diagnostics"] = {"schema_version": 1, "user": {"loaded": True, "status": "ok", "code": ""}}
        return value

    setup_service.discovery = reflected
    preview = credential_preview(setup_service, app_password=password)
    assert preview["state"] == "ready"
    assert preview["preview"]["display_name"] == "[redacted]"
    assert preview["preview"]["diagnostics"]["user"]["status"] == "ok"
    assert preview["preview"]["calendars"][0]["resource_id"] == "nextcloud-calendar-" + "a" * 12
    status = apply(setup_service, preview)
    assert status["revision"] == setup_service.store.read()["revision"]
    assert status["status_source"] == "last-verified-setup"


def test_refresh_failed_identity_shows_new_diagnostics_without_replacing_connection(setup_service):
    checked = datetime(2026, 11, 22, 10, tzinfo=UTC)
    setup_service.wall_clock = lambda: checked + timedelta(minutes=30)
    setup_service.discovery = lambda *args: {**discovery(*args), "checked_at": checked.isoformat()}
    status = apply(setup_service, ready(setup_service))
    original = setup_service.store.read()

    def revoked(*args):
        error = OnboardingError("authentication-failed", PASSWORD)
        error.diagnostics = {
            "schema_version": 1,
            "user": {"loaded": False, "status": "failed", "code": "authentication-failed"},
        }
        raise error

    setup_service.discovery = revoked
    result = setup_service.dispatch(
        "connection.refresh", {"expected_revision": status["revision"]}, actor="device-one"
    )
    assert result["state"] == "failed" and result["diagnostics"]["user"]["loaded"] is False
    assert result["diagnostics"]["availability"]["status"] == "ok"
    for domain in ("calendars", "task_lists", "addressbooks", "files", "talk"):
        assert result["diagnostics"][domain]["status"] == "skipped"
    assert result["checked_at"] == "2026-11-22T10:30:00+00:00"
    assert (result["account_id"], result["revision"]) == (status["account_id"], status["revision"])
    after = setup_service.status()
    assert after["refresh_failure"] == result
    assert after["preview"]["checked_at"] == "2026-11-22T10:00:00+00:00"
    assert PASSWORD not in json.dumps(result)
    assert setup_service.store.read() == original
    setup_service.discovery = lambda *args: {**discovery(*args), "checked_at": "2026-11-22T10:30:00+00:00"}
    refreshed = setup_service.dispatch(
        "connection.refresh", {"expected_revision": after["revision"]}, actor="device-one"
    )
    assert refreshed["preview"]["checked_at"] == result["checked_at"]
    assert "refresh_failure" not in refreshed


def test_status_does_not_renew_saved_evidence(setup_service):
    checked = datetime(2026, 11, 22, 10, tzinfo=UTC)
    now = [checked + timedelta(minutes=20)]
    setup_service.wall_clock = lambda: now[0]
    setup_service.discovery = lambda *args: {**discovery(*args), "checked_at": checked.isoformat()}
    status = apply(setup_service, ready(setup_service))
    assert status["diagnostics_freshness"] == {
        "state": "fresh",
        "age_seconds": 1200,
        "max_age_seconds": 86400,
    }
    now[0] = checked + timedelta(hours=24)
    stale = setup_service.status()
    assert stale["diagnostics_freshness"]["state"] == "stale"
    assert stale["diagnostics_freshness"]["age_seconds"] == 86400
    assert stale["preview"]["checked_at"] == status["preview"]["checked_at"] == checked.isoformat()
    assert stale["revision"] == status["revision"]


@pytest.mark.parametrize("checked_at", [None, "invalid", "2026-11-22T11:00:00", "2026-11-22T11:00:00Z"])
def test_missing_future_and_invalid_measurements_are_not_fresh(setup_service, checked_at):
    setup_service.wall_clock = lambda: datetime(2026, 11, 22, 10, tzinfo=UTC)
    setup_service.discovery = lambda *args: {**discovery(*args), "checked_at": checked_at}
    status = apply(setup_service, ready(setup_service))
    assert status["diagnostics_freshness"] == {
        "state": "unknown",
        "age_seconds": None,
        "max_age_seconds": 86400,
    }


def test_folder_rpc_binds_exact_folder_evidence_to_current_connection(setup_service, monkeypatch):
    current = apply(setup_service, ready(setup_service))
    href = "/nc/remote.php/dav/files/assistant/Projects/"
    selected = {
        "path": "Projects",
        "href": href,
        "file_id": "42",
        "can_read": True,
        "can_create_file": False,
        "can_mkdir": True,
        "read_check": {"status": "ok", "code": "", "scope": "first-level", "content_checked": False},
    }
    checked_at = "2026-11-22T10:30:00+00:00"

    def propfind(transport, credentials, path):
        assert (transport.base_url, credentials.user_id, path) == (
            current["server_url"],
            "assistant",
            "Projects",
        )
        return {
            "path": path,
            "folders": [selected],
            "selected_folder": selected,
            "checked_at": checked_at,
            "depth": 1,
            "inventory_complete": True,
            "complete": True,
            "errors": [],
            "limitations": [],
            "results_may_be_truncated": False,
        }

    monkeypatch.setattr("personal_assistant.nextcloud_setup.discover_folders", propfind)
    result = setup_service.dispatch("folders.list", {"path": "Projects"}, actor="device-one")
    assert (result["account_id"], result["revision"]) == (current["account_id"], current["revision"])
    assert result["checked_at"] == checked_at and result["selected_folder"] == selected
    assert PASSWORD not in json.dumps(result) and setup_service.status()["revision"] == current["revision"]


@pytest.mark.parametrize("method", ["login-v2", "credentials"])
def test_missing_identity_cannot_become_a_ready_or_retained_flow(setup_service, method):
    setup_service.discovery = lambda transport, credentials: {"server_url": transport.base_url, "user_id": ""}
    if method == "credentials":
        result = credential_preview(setup_service)
    else:
        start = setup_service.dispatch(
            "login.start", {"server_url": "https://cloud.example/nc"}, actor="device-one"
        )
        result = setup_service.dispatch("login.poll", {"flow_id": start["flow_id"]}, actor="device-one")
    assert (result["state"], result["code"]) == ("failed", "invalid-user-id")
    assert result["diagnostics"]["addressbooks"]["status"] == "skipped"
    assert not setup_service.flows and setup_service.store.read() is None


def test_server_idle_cycle_expires_credentials_without_another_rpc(tmp_path, setup_service, operator_proof):
    preview = credential_preview(setup_service)
    pending = setup_service.flows[preview["flow_id"]]
    server = SetupServer(tmp_path / "idle.sock", setup_service, proof_verifier=operator_proof[0])
    try:
        setup_service.clock = lambda: 2000
        server.service_actions()
        assert not setup_service.flows and pending.credentials is None
        assert pending.snapshot is None and pending.preview_digest == ""
    finally:
        server.server_close()


def test_talk_pairing_uses_live_room_and_user_membership(setup_service):
    state = apply(setup_service, ready(setup_service))
    participants = setup_service.dispatch("talk.participants", {"room_token": "room01"}, actor="device-one")
    assert {item["user_id"] for item in participants["participants"]} == {"jan", "assistant"}
    params = {
        "enabled": True,
        "room_tokens": ["room01"],
        "allowed_user_ids": ["jan"],
        "expected_revision": state["revision"],
    }
    for bad in (
        {"room_tokens": ["unknown"]},
        {"allowed_user_ids": ["guest"]},
        {"allowed_user_ids": ["unknown"]},
    ):
        with pytest.raises(OnboardingError):
            setup_service.dispatch("talk.configure", {**params, **bad}, actor="device-one")
        assert setup_service.store.read()["talk"]["enabled"] is False
    result = setup_service.dispatch("talk.configure", params, actor="device-one")
    assert result["talk"] == {"enabled": True, "room_tokens": ["room01"], "allowed_user_ids": ["jan"]}
    stopped = setup_service.dispatch(
        "talk.configure", {"enabled": False, "expected_revision": result["revision"]}, actor="device-one"
    )
    assert stopped["talk"]["enabled"] is False


def test_talk_capability_missing_or_read_only_never_enables_channel(setup_service):
    state = apply(setup_service, ready(setup_service))

    def restricted(transport, credentials):
        value = discovery(transport, credentials)
        value["capabilities"]["spreed"]["features"] = []
        return value

    setup_service.discovery = restricted
    with pytest.raises(OnboardingError):
        setup_service.dispatch(
            "talk.configure",
            {
                "enabled": True,
                "expected_revision": state["revision"],
                "room_tokens": ["room01"],
                "allowed_user_ids": ["jan"],
            },
            actor="device-one",
        )
    assert not setup_service.store.read()["talk"]["enabled"]


def test_socket_transport_health_dispatch_and_malformed_request(
    tmp_path: Path, setup_service, operator_proof, capfd
):
    path = tmp_path / "control.sock"
    verifier, signed_request, _ = operator_proof
    server = SetupServer(path, setup_service, proof_verifier=verifier)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def request(body):
        client = http.client.HTTPConnection("localhost", timeout=2)
        client.sock = socket.socket(socket.AF_UNIX)
        client.sock.connect(str(path))
        client.request("POST", "/rpc", body, {"Content-Type": "application/json"})
        response = client.getresponse()
        result = response.status, json.loads(response.read())
        client.close()
        return result

    try:
        assert check_socket(path)
        status, value = request(json.dumps(signed_request()))
        assert status == 200 and value == {"ok": True, "result": {"connected": False, "revision": ""}}
        status, value = request(json.dumps({**signed_request(), "actor": "device:forged"}))
        assert status == 200 and value["error"]["code"] == "operator-proof-invalid"
        status, value = request("{")
        assert status == 400 and value["error"]["code"] == "invalid-request"
        credentials = {
            "server_url": "https://cloud.example/nc",
            "login_name": "assistant@example.invalid",
            "app_password": PASSWORD,
        }
        signed = signed_request("login.credentials", credentials)
        tampered = {**signed, "parameters": {**credentials, "app_password": "changed"}}
        status, value = request(json.dumps(tampered))
        assert status == 200 and value["error"]["code"] == "operator-proof-invalid"
        assert setup_service.store.read() is None and not setup_service.flows
        status, value = request(json.dumps(signed))
        assert status == 200 and value["ok"] is True and value["result"]["state"] == "ready"
        assert PASSWORD not in json.dumps(value)
        assert setup_service.store.read() is None
        preview = value["result"]
        status, value = request(
            json.dumps(
                signed_request(
                    "connection.apply",
                    {
                        "flow_id": preview["flow_id"],
                        "preview_digest": preview["preview_digest"],
                        "expected_revision": preview["expected_revision"],
                        "confirm_account_access": True,
                    },
                )
            )
        )
        assert status == 200 and value["ok"] is True and value["result"]["connected"] is True
        assert PASSWORD not in json.dumps(value)
        assert setup_service.store.read()["app_password"] == PASSWORD
        output = capfd.readouterr()
        assert PASSWORD not in output.out + output.err
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def test_operator_proof_bound_to_arguments_device_and_restart_replay(operator_proof):
    verifier, signed_request, registry = operator_proof
    request = signed_request("talk.configure", {"enabled": True, "room_tokens": ["chosen"]})
    for change in (
        {"operation": "connection.refresh"},
        {"parameters": {"enabled": True, "room_tokens": ["other"]}},
        {"actor": "device:" + "0" * 64},
    ):
        with pytest.raises(OnboardingError) as error:
            verifier.verify({**request, **change})
        assert error.value.code == "operator-proof-invalid"
    verifier.verify(request)
    restarted = OperatorProofVerifier(registry, replay_path=verifier.replay_path, clock=lambda: 1000)
    with pytest.raises(OnboardingError):
        restarted.verify(request)
    restarted.verify(signed_request())


@pytest.mark.parametrize("offset", [-120_001, 30_001])
def test_expired_or_future_operator_proof_fails(operator_proof, offset):
    verifier, request, _ = operator_proof
    with pytest.raises(OnboardingError):
        verifier.verify(request(issued_at=1_000_000 + offset))


@pytest.mark.parametrize("change", ["revoked", "scope", "token-scope", "role", "unknown-key"])
def test_operator_pairing_revocation_and_scope_are_checked_each_call(operator_proof, change):
    verifier, request, registry = operator_proof
    data = json.loads(registry.read_text())
    device = next(iter(data.values()))
    if change == "revoked":
        device["tokens"]["operator"]["revokedAtMs"] = 999999
    elif change == "scope":
        device["approvedScopes"] = ["operator.read"]
    elif change == "token-scope":
        device["tokens"]["operator"]["scopes"] = ["operator.read"]
    elif change == "role":
        device["role"] = "node"
    else:
        device["publicKey"] = sign_fixture(seed=8)["publicKey"]
    registry.write_text(json.dumps(data))
    with pytest.raises(OnboardingError):
        verifier.verify(request())


def test_raw_socket_actor_does_not_authorize_and_signature_cannot_be_forged(operator_proof):
    verifier, request, _ = operator_proof
    value = request()
    for proof in (
        {},
        {"payload": value["proof"]["payload"], "signature": "A" * 86},
        {
            "payload": value["proof"]["payload"],
            "signature": sign_fixture(value["proof"]["payload"], seed=8)["signature"],
        },
    ):
        with pytest.raises(OnboardingError):
            verifier.verify({**value, "proof": proof})


@pytest.fixture
def access_service(tmp_path):
    from test_nextcloud_access import managed, measured

    store = managed(tmp_path)
    value = measured()
    for row in value["discovery_snapshot"]["file_roots"]:
        row.update(
            path="",
            file_id="1",
            checked_at=value["discovery_snapshot"]["checked_at"],
            privileges_verified=True,
            file_permissions="GK",
            can_create_file=False,
            can_mkdir=True,
            read_check={"status": "ok"},
        )
    store.save(value, expected_revision="")
    now = datetime.now(UTC)
    return NextcloudSetupService(store, clock=lambda: 100, wall_clock=lambda: now)


def access_params(service, levels):
    status = service.status()
    rows = status["access"]["rows"]
    edits = []
    for component, path, level in levels:
        row = next(r for r in rows if r["scope"]["component"] == component and r["scope"]["path"] == path)
        edits.append({"scope": row["scope"], "level": level})
    return {
        "account_id": status["account_id"],
        "expected_revision": status["revision"],
        "expected_policy_revision": status["access"]["binding"]["policy_revision"],
        "edits": edits,
        "confirm_root": False,
    }


def confirmation(preview):
    return {
        k: preview[k]
        for k in ("preview_id", "preview_digest", "expected_revision", "expected_policy_revision")
    } | {"confirm": True}


def test_signed_access_preview_and_apply(access_service, tmp_path, operator_proof):
    verifier, sign, registry = operator_proof
    paired = json.loads(registry.read_text())
    other = sign_fixture(seed=8)
    paired[other["id"]] = {
        **next(iter(paired.values())),
        "deviceId": other["id"],
        "publicKey": other["publicKey"],
    }
    registry.write_text(json.dumps(paired))
    path = tmp_path / "access.sock"
    server = SetupServer(path, access_service, proof_verifier=verifier)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def rpc(op, params, **signing):
        client = http.client.HTTPConnection("localhost", timeout=5)
        client.sock = socket.socket(socket.AF_UNIX)
        client.sock.connect(str(path))
        client.request(
            "POST", "/rpc", json.dumps(sign(op, params, **signing)), {"Content-Type": "application/json"}
        )
        response = json.loads(client.getresponse().read())
        client.close()
        return response

    try:
        before = access_service.store.snapshot()
        params = access_params(
            access_service, [("VEVENT", None, "read"), ("VTODO", None, "update"), ("FILES", "Belege", "off")]
        )
        preview = rpc("access.preview", params)["result"]
        assert before == access_service.store.snapshot()
        assert len(preview["changes"]) == 3
        wrong_actor = rpc(
            "access.apply", confirmation(preview), signing_seed=8, actor="device:" + other["id"]
        )
        assert wrong_actor["error"]["code"] == "access-preview-unavailable"
        assert rpc("access.apply", {**confirmation(preview), "preview_digest": "a" * 64})["ok"] is False
        preview = rpc("access.preview", params)["result"]
        result = rpc("access.apply", confirmation(preview))["result"]
        assert result["access"] == access_service.status()["access"]
        assert result["access"]["binding"]["policy_revision"] != params["expected_policy_revision"]
        desired = {
            g.scope.component: set(g.operations) for g in access_service.store.snapshot().policy.grants
        }
        assert desired["VEVENT"] == {"list", "read", "search"}
        assert desired["VTODO"] == {"list", "read", "create", "update"}
        assert desired["FILES"] == set()
        assert rpc("access.apply", confirmation(preview))["ok"] is False
        allowed = rpc("access.preview", access_params(access_service, [("FILES", "Belege", "read")]))[
            "result"
        ]
        assert rpc("access.apply", confirmation(allowed))["ok"]
        conn = access_service.store.read()
        access_service.store.save({**conn, "app_password": "11"}, expected_revision=conn["revision"])
        def reject_account(*args):
            raise OnboardingError("authentication-failed")

        access_service.transport_factory = Transport
        access_service.discovery = reject_account
        failed = rpc("connection.refresh", {"expected_revision": access_service.store.read()["revision"]})
        assert failed["result"]["code"] == "authentication-failed"
        current = rpc("status", {})["result"]
        masked = next(r for r in current["access"]["rows"] if r["scope"]["file_id"] == "[redacted]")
        assert "read" in masked["desired_operations"] and "read" in masked["server"]["operations"]
        assert masked["effective_operations"] == [] and masked["supported_levels"] == ["off"]
        revoke = {
            **params,
            "expected_revision": current["revision"],
            "edits": [{"level": "off", "revoke_handle": masked["revoke_handle"]}],
            "expected_policy_revision": current["access"]["binding"]["policy_revision"],
        }
        masked_diff = rpc("access.preview", revoke)["result"]
        assert masked_diff["changes"][0]["revoke_handle"] == masked["revoke_handle"]
        assert rpc("access.apply", confirmation(masked_diff))["ok"]
        assert all(
            not g.operations
            for g in access_service.store.snapshot().policy.grants
            if g.scope.component == "FILES"
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(3)


@pytest.mark.parametrize("race", ["actor", "competing-preview", "talk", "refresh", "expiry", "account"])
def test_access_candidates_are_bound_once(access_service, race):
    service = access_service
    params = access_params(service, [("FILES", "Belege", "create")])
    actor = "device-one"
    preview = service.dispatch("access.preview", params, actor=actor)
    if race == "actor":
        actor = "another-device"
    elif race == "competing-preview":
        service.dispatch("access.preview", params, actor=actor)
    elif race == "talk":
        service.dispatch(
            "talk.configure",
            {"enabled": False, "expected_revision": params["expected_revision"]},
            actor=actor,
        )
    elif race == "refresh":
        value = service.store.read()
        service.discovery = lambda *args: value["discovery_snapshot"]
        service.transport_factory = Transport
        service.dispatch(
            "connection.refresh", {"expected_revision": params["expected_revision"]}, actor=actor
        )
    elif race == "expiry":
        service.clock = lambda: 701
    else:
        from test_nextcloud_access import measured

        service.store.save(measured("other"), expected_revision=params["expected_revision"])
    with pytest.raises((ConnectionError, OnboardingError)):
        service.dispatch("access.apply", confirmation(preview), actor=actor)
    assert not service.store.snapshot().policy.grants


def test_explicit_root_and_exact_c_and_k(access_service):
    params = access_params(access_service, [("FILES", "", "create")])
    with pytest.raises(OnboardingError) as error:
        access_service.dispatch("access.preview", params, actor="one")
    assert error.value.code == "account-root-confirmation-required"
    preview = access_service.dispatch("access.preview", {**params, "confirm_root": True}, actor="one")
    assert {"mkdir", "list", "read", "search"} == set(preview["changes"][0]["after_operations"])
    assert any(item["operation"] == "create_file" for item in preview["withheld"])


def test_missing_selected_folder_can_be_revoked(access_service):
    s = access_service
    params = access_params(s, [("FILES", "Belege", "create")])
    s.dispatch("access.apply", confirmation(s.dispatch("access.preview", params, actor="one")), actor="one")
    current = s.store.read()
    current["discovery_snapshot"]["folders"] = []
    s.store.save(current, expected_revision=current["revision"])
    row = next(r for r in s.status()["access"]["rows"] if r["scope"]["path"] == "Belege")
    assert (
        row["desired_operations"] and row["effective_operations"] == [] and row["supported_levels"] == ["off"]
    )
    preview = s.dispatch("access.preview", access_params(s, [("FILES", "Belege", "off")]), actor="one")
    assert preview["changes"][0]["before_operations"] == row["desired_operations"]
    s.dispatch("access.apply", confirmation(preview), actor="one")
    assert s.store.snapshot().policy.grants[0].operations == ()


@pytest.mark.parametrize("change", ["raw", "missing", "foreign-time", "after-preview"])
def test_only_exact_opened_own_folder_row_and_original_timestamp_authorize(
    access_service, monkeypatch, change
):
    s = access_service
    s.transport_factory = Transport
    conn = s.store.read()
    raw = dict(conn["discovery_snapshot"]["folders"][0])
    raw.pop("checked_at", None)  # Production returns the request timestamp beside the raw own row.
    path = "Belege/Team"
    raw.update(path=path, href="/nc/remote.php/dav/files/assistant/Belege/Team/", file_id="98")
    measured_at = (datetime.now(UTC) - timedelta(minutes=16)).isoformat()
    result = {
        "path": path,
        "folders": [raw] if change != "missing" else [],
        "selected_folder": {**raw, "can_mkdir": True, "file_permissions": "GCK"},
        "checked_at": None if change == "foreign-time" else measured_at,
    }
    monkeypatch.setattr("personal_assistant.nextcloud_setup.discover_folders", lambda *args: dict(result))
    opened = s.dispatch("folders.list", {"path": path}, actor="one")
    args = access_params(s, [])
    args["edits"] = [
        {
            "scope": {
                "resource_id": args["account_id"] + "-files",
                "component": "FILES",
                "path": path,
                "file_id": "98",
            },
            "level": "create",
        }
    ]
    if change in {"missing", "foreign-time"}:
        with pytest.raises(OnboardingError):
            s.dispatch("access.preview", args, actor="one")
        return
    assert set(opened["access_row"]["server"]["operations"]) == {"list", "read", "search", "create_file"}
    preview = s.dispatch("access.preview", args, actor="one")
    if change == "after-preview":
        result["folders"] = []
        s.dispatch("folders.list", {"path": path}, actor="two")
        with pytest.raises(OnboardingError):
            s.dispatch("access.apply", confirmation(preview), actor="one")
        return
    s.dispatch("access.apply", confirmation(preview), actor="one")
    inventory = s.store.read()["discovery_snapshot"]
    saved = next(row for row in inventory["folders"] if row["path"] == path)
    assert saved["checked_at"] == measured_at and saved["source_account_id"] == inventory["account_id"]
    assert saved["file_permissions"] == "GC" and saved["privileges_verified"] is True
    incoming = {**conn["discovery_snapshot"], "checked_at": datetime.now(UTC).isoformat()}
    s.discovery = lambda *args: incoming
    refreshed = s.dispatch("connection.refresh", {"expected_revision": s.status()["revision"]}, actor="one")
    row = next(r for r in refreshed["access"]["rows"] if r["scope"]["path"] == path)
    assert row["server"]["checked_at"] == measured_at
    assert "mkdir" not in row["effective_operations"] and "create_file" in row["effective_operations"]


def test_uncertain_store_publication_status_and_once_only_candidate(access_service, monkeypatch):
    s = access_service
    params = access_params(s, [("FILES", "Belege", "read")])
    preview = s.dispatch("access.preview", params, actor="one")
    original = s.store._publish
    writes = []

    def fail_committing(name, data):
        writes.append(name)
        if name == "mode.json" and json.loads(data)["mode"] == "active":
            raise OSError("synthetic publication failure")
        original(name, data)

    monkeypatch.setattr(s.store, "_publish", fail_committing)
    with pytest.raises(ConnectionError, match="nextcloud-connection-publication-failed"):
        s.dispatch("access.apply", confirmation(preview), actor="one")
    count = len(writes)
    assert s.dispatch("status", {}, actor="one")["access"]["mode"] == "integrity-error"
    with pytest.raises(OnboardingError):
        s.dispatch("access.apply", confirmation(preview), actor="one")
    assert len(writes) == count


@pytest.mark.parametrize("select_later_calendar", [False, True])
def test_legacy_refresh_only_proposes_previously_known_operations(tmp_path, select_later_calendar):
    from test_nextcloud_access import measured

    from personal_assistant.nextcloud_connection import initialize_access_store

    original = measured()
    original["discovery_snapshot"]["calendars"][0]["can_update"] = False
    store = ConnectionStore(tmp_path / "store")
    store.save(original, expected_revision="")
    state = tmp_path / "state"
    state.mkdir()
    initialize_access_store(state, store.directory, transition="verified-pre-policy")
    s = NextcloudSetupService(store, transport_factory=Transport)
    incoming = measured()["discovery_snapshot"]
    extra = {**incoming["calendars"][0], "name": "New", "href": "/nc/remote.php/dav/calendars/assistant/new/"}
    incoming["calendars"].append(extra)
    s.discovery = lambda *args: incoming
    status = s.dispatch("connection.refresh", {"expected_revision": s.status()["revision"]}, actor="one")
    params = {
        "expected_revision": status["revision"],
        "expected_policy_revision": "",
        "account_id": status["account_id"],
        "edits": [],
        "confirm_root": False,
    }
    s = NextcloudSetupService(ConnectionStore(store.directory, require_mode=True))
    preview = s.dispatch("access.preview", params, actor="one")
    later = next(c for c in preview["changes"] if c["name"] == "New")
    assert {w["code"] for w in preview["withheld"] if w["scope"] == later["scope"]} == {
        "legacy-outside-original-selection"
    }
    assert not any(c["name"] == "New" and c["after_operations"] for c in preview["changes"])
    assert any(c["name"] == "New" and "read" in c["before_operations"] for c in preview["changes"])
    assert all(
        "update" not in c["after_operations"]
        for c in preview["changes"]
        if c["scope"]["component"] == "VEVENT"
    )
    expected = {"list", "read", "search"} if select_later_calendar else set()
    if select_later_calendar:
        params["edits"] = [{"scope": later["scope"], "level": "read"}]
        preview = s.dispatch("access.preview", params, actor="one")
    change = next(c for c in preview["changes"] if c["scope"] == later["scope"])
    assert set(change["after_operations"]) == expected
    withheld = [w for w in preview["withheld"] if w["scope"] == later["scope"]]
    assert {w["operation"] for w in withheld} == {"list", "read", "search", "create", "update"} - expected
    assert all(w["code"] == "legacy-outside-original-selection" for w in withheld)
    assert any(
        w["scope"] is None and w["code"] == "legacy-invoice-archive-unbound" for w in preview["withheld"]
    )
    result = s.dispatch("access.apply", confirmation(preview), actor="one")
    saved = next(row for row in result["access"]["rows"] if row["scope"] == later["scope"])
    assert set(saved["desired_operations"]) == set(saved["effective_operations"]) == expected
    assert result["access"]["mode"] == "active"
    assert any(c["after_operations"] for c in preview["changes"])
    assert any(w["workflow"] == "invoice-archive" for w in preview["affected_workflows"])


@pytest.mark.parametrize(
    "bad",
    [
        "extra",
        "nested-extra",
        "duplicate",
        "oversize-path",
        "oversize-batch",
        "confirm",
        "foreign",
        "empty",
        "unbound",
        "bad-file-level",
    ],
)
def test_owner_revalidates_closed_access_request(access_service, bad):
    s = access_service
    params = access_params(s, [("FILES", "Belege", "read")])
    edit = params["edits"][0]
    match bad:
        case "extra":
            params["unknown"] = 1
        case "nested-extra":
            edit["scope"]["raw"] = "ignored"
        case "duplicate":
            params["edits"].append(edit)
        case "oversize-path":
            edit["scope"]["path"] = "界" * 700
        case "oversize-batch":
            params["edits"] = [
                {"scope": {**edit["scope"], "path": str(i) + "界" * 400}, "level": "read"} for i in range(32)
            ]
        case "confirm":
            params["confirm_root"] = 1
        case "foreign":
            params["account_id"] = "nextcloud-account-" + "0" * 16
            edit["scope"]["resource_id"] = params["account_id"] + "-files"
        case "empty":
            params["edits"] = []
        case "unbound":
            edit["scope"]["resource_id"] = "nextcloud-calendar-001"
        case "bad-file-level":
            edit["level"] = "update"
    with pytest.raises(OnboardingError):
        s.dispatch("access.preview", params, actor="one")
    assert not s.store.snapshot().policy.grants


def test_recreated_folder_diff_keeps_both_file_identities(access_service):
    s = access_service
    args = access_params(s, [("FILES", "Belege", "read")])
    s.dispatch("access.apply", confirmation(s.dispatch("access.preview", args, actor="one")), actor="one")
    conn = s.store.read()
    old_id = args["edits"][0]["scope"]["file_id"]
    conn["discovery_snapshot"]["folders"][0]["file_id"] = "93"
    s.store.save(conn, expected_revision=conn["revision"])
    status = s.status()
    rows = [r for r in status["access"]["rows"] if r["scope"]["path"] == "Belege"]
    assert len(rows) == 2
    new = next(r for r in rows if r["scope"]["file_id"] == "93")
    assert new["desired_operations"] == [] and not new["effective_operations"]
    args = access_params(s, [])
    args["edits"].append({"scope": new["scope"], "level": "read"})
    preview = s.dispatch("access.preview", args, actor="one")
    changes = {c["scope"]["file_id"]: c for c in preview["changes"]}
    assert changes[old_id]["before_operations"] and not changes[old_id]["after_operations"]
    assert not changes["93"]["before_operations"] and changes["93"]["after_operations"]


def test_legacy_refresh_cannot_turn_failed_prior_probe_into_automatic_grant(tmp_path):
    from test_nextcloud_access import measured

    from personal_assistant.nextcloud_connection import initialize_access_store

    conn = measured()
    conn["discovery_snapshot"]["calendars"][0]["read_check"] = {"status": "failed"}
    store = ConnectionStore(tmp_path / "store")
    store.save(conn, expected_revision="")
    state = tmp_path / "state"
    state.mkdir()
    initialize_access_store(state, store.directory, transition="verified-pre-policy")
    s = NextcloudSetupService(
        store, transport_factory=Transport, discovery=lambda *args: measured()["discovery_snapshot"]
    )
    status = s.dispatch("connection.refresh", {"expected_revision": s.status()["revision"]}, actor="one")
    params = {
        "expected_revision": status["revision"],
        "expected_policy_revision": "",
        "account_id": status["account_id"],
        "edits": [],
        "confirm_root": False,
    }
    preview = s.dispatch("access.preview", params, actor="one")
    assert not any("update" in c["after_operations"] for c in preview["changes"])
    assert any(
        w["code"] == "legacy-original-proof-missing" and w["operation"] == "read"
        for w in preview["withheld"]
    )


def test_failed_refresh_invalidates_inflight_access_confirmation(access_service):
    s = access_service
    params = access_params(s, [("FILES", "Belege", "read")])
    preview = s.dispatch("access.preview", params, actor="one")

    def failed(*args):
        raise OnboardingError("authentication-failed")

    s.transport_factory = Transport
    s.discovery = failed
    assert (
        s.dispatch("connection.refresh", {"expected_revision": params["expected_revision"]}, actor="one")[
            "state"
        ]
        == "failed"
    )
    with pytest.raises(OnboardingError):
        s.dispatch("access.apply", confirmation(preview), actor="one")
    assert not s.store.snapshot().policy.grants


def test_status_bounds_policy_diagnostics_on_large_inventory(access_service, monkeypatch):
    from personal_assistant import nextcloud_access as core

    s = access_service
    conn = s.store.read()
    inv = conn["discovery_snapshot"]
    inv["calendars"] = [
        {**inv["calendars"][0], "href": f"/nc/remote.php/dav/calendars/assistant/n{n}/"} for n in range(150)
    ]
    s.store.save(conn, expected_revision=conn["revision"])
    checks = 0
    original = core._url_path

    def count(*args):
        nonlocal checks
        checks += 1
        assert checks < 5000, "rights projection repeatedly scanned the full resource inventory"
        return original(*args)

    monkeypatch.setattr(core, "_url_path", count)
    result = s.status()["access"]
    assert result["complete"] and len(result["rows"]) >= 150


def test_legacy_status_distinguishes_existing_effective_rights_from_unselected_policy(tmp_path):
    from test_nextcloud_access import measured

    from personal_assistant.nextcloud_connection import initialize_access_store

    store = ConnectionStore(tmp_path / "store")
    store.save(measured(), expected_revision="")
    state = tmp_path / "state"
    state.mkdir()
    initialize_access_store(state, store.directory, transition="verified-pre-policy")
    result = NextcloudSetupService(store).status()["access"]
    row = next(r for r in result["rows"] if r["scope"]["component"] == "VEVENT")
    assert result["mode"] == "legacy" and result["binding"] is None
    assert not row["desired_operations"] and "read" in row["effective_operations"]


def test_status_resolves_lost_success_reply_without_repeating_save(access_service, monkeypatch):
    s = access_service
    params = access_params(s, [("FILES", "Belege", "create")])
    preview = s.dispatch("access.preview", params, actor="one")
    save = s.store.save
    calls = 0

    def lose_reply(*args, **kwargs):
        nonlocal calls
        calls += 1
        save(*args, **kwargs)
        raise ConnectionError("nextcloud-connection-readback-failed")

    monkeypatch.setattr(s.store, "save", lose_reply)
    with pytest.raises(ConnectionError):
        s.dispatch("access.apply", confirmation(preview), actor="one")
    current = s.dispatch("status", {}, actor="one")
    assert current["revision"] != params["expected_revision"]
    assert any("create_file" in r["effective_operations"] for r in current["access"]["rows"])
    with pytest.raises(OnboardingError):
        s.dispatch("access.apply", confirmation(preview), actor="one")
    assert calls == 1


def test_large_status_preserves_revocable_grants_and_reports_diagnostic_limits(access_service):
    s = access_service
    args = access_params(s, [("FILES", "Belege", "read")])
    s.dispatch("access.apply", confirmation(s.dispatch("access.preview", args, actor="one")), actor="one")
    current = s.store.read()
    current["discovery_snapshot"]["folders"].append({"name": "x" * (2 * 1024 * 1024), "path": "example"})
    s.store.save(current, expected_revision=current["revision"])
    status = s.status()
    assert len(json.dumps({"ok": True, "result": status}, ensure_ascii=False).encode()) < 2 * 1024 * 1024
    assert status["complete"] is False and "operator-response-limit" in status["preview"]["limitations"]
    assert any(r["scope"]["path"] == "Belege" and r["desired_operations"] for r in status["access"]["rows"])


def test_large_legacy_adoption_requires_explicit_finite_choices(tmp_path):
    from test_nextcloud_access import measured

    from personal_assistant.nextcloud_connection import initialize_access_store

    conn = measured()
    inv = conn["discovery_snapshot"]
    inv["calendars"] = [
        {**inv["calendars"][0], "href": f"/nc/remote.php/dav/calendars/assistant/n{n}/"} for n in range(75)
    ]
    store = ConnectionStore(tmp_path / "store")
    store.save(conn, expected_revision="")
    state = tmp_path / "state"
    state.mkdir()
    initialize_access_store(state, store.directory, transition="verified-pre-policy")
    s = NextcloudSetupService(store)
    status = s.status()
    one = status["access"]["rows"][0]
    params = {
        "expected_revision": status["revision"],
        "expected_policy_revision": "",
        "account_id": status["account_id"],
        "edits": [{"scope": one["scope"], "level": "read"}],
        "confirm_root": False,
    }
    preview = s.dispatch("access.preview", params, actor="one")
    assert len(preview["changes"]) == 1 and any(
        w["code"] == "legacy-bounded-explicit-selection" for w in preview["withheld"]
    )
    s.dispatch("access.apply", confirmation(preview), actor="one")
    assert len(s.store.snapshot().policy.grants) == 1


def test_large_selection_pages_reach_missing_grants_with_revision_guard(access_service, monkeypatch):
    from test_nextcloud_access import update_policy

    from personal_assistant import nextcloud_setup_access as editor

    monkeypatch.setattr(editor, "STATUS_BYTE_LIMIT", 18 * 1024, raising=False)
    s = access_service
    template = access_params(s, [("FILES", "Belege", "off")])["edits"][0]["scope"]
    scopes = [{**template, "path": f"missing-{n:02}-" + "x" * 300, "file_id": str(n + 80)} for n in range(32)]
    update_policy(s.store, [{"scope": scope, "operations": []} for scope in scopes])
    first = s.status()
    offset = first["access"]["next_offset"]
    assert 0 < offset < len(scopes)
    rows = list(first["access"]["rows"])
    while offset:
        page = s.dispatch(
            "status", {"expected_revision": first["revision"], "access_offset": offset}, actor="one"
        )
        assert (
            len(json.dumps({"ok": True, "result": page}, ensure_ascii=False).encode())
            < editor.STATUS_BYTE_LIMIT
        )
        rows.extend(page["access"]["rows"])
        offset = page["access"]["next_offset"]
    assert set(r["scope"]["path"] for r in rows) >= {sc["path"] for sc in scopes}
    s.dispatch("talk.configure", {"enabled": False, "expected_revision": first["revision"]}, actor="two")
    with pytest.raises(ConnectionError):
        s.dispatch(
            "status",
            {"expected_revision": first["revision"], "access_offset": first["access"]["next_offset"]},
            actor="one",
        )


def test_protocol_access_levels_survive_short_credentials(access_service):
    s = access_service
    record = s.store.read()
    record["app_password"] = "rea"
    s.store.save(record, expected_revision=record["revision"])
    params = access_params(s, [("FILES", "Belege", "create")])
    preview = s.dispatch("access.preview", params, actor="one")
    assert "read" in preview["changes"][0]["after_operations"]
    status = s.dispatch("access.apply", confirmation(preview), actor="one")
    row = next(r for r in status["access"]["rows"] if r["scope"]["path"] == "Belege")
    assert "create" in row["supported_levels"] and "read" in row["effective_operations"]


def test_new_positive_grant_requires_resolving_current_authentication_failure(access_service):
    s = access_service
    params = access_params(s, [("FILES", "Belege", "create")])
    s.transport_factory = Transport

    def fail(*args):
        raise OnboardingError("authentication-failed")

    s.discovery = fail
    failure = s.dispatch(
        "connection.refresh", {"expected_revision": params["expected_revision"]}, actor="one"
    )
    params["expected_revision"] = failure["revision"]
    with pytest.raises(OnboardingError) as error:
        s.dispatch("access.preview", params, actor="one")
    assert error.value.code == "rights-unverified"


def test_folder_own_timestamp_is_never_advanced_to_enclosing_observation(access_service, monkeypatch):
    s = access_service
    s.transport_factory = Transport
    conn = s.store.read()
    stamp = (datetime.now(UTC) - timedelta(hours=25)).isoformat()
    old = {**conn["discovery_snapshot"]["folders"][0], "checked_at": stamp}

    def measure(*args):
        return {"path": old["path"], "folders": [old], "checked_at": datetime.now(UTC).isoformat()}

    monkeypatch.setattr("personal_assistant.nextcloud_setup.discover_folders", measure)
    opened = s.dispatch("folders.list", {"path": "Belege"}, actor="one")
    assert opened["access_row"]["server"]["checked_at"] == stamp
    assert opened["access_row"]["supported_levels"] == ["off"]


def test_confirmed_login_and_account_replacement_start_without_grants(tmp_path):
    from test_nextcloud_access import managed, measured

    identity = ["assistant"]
    s = NextcloudSetupService(
        managed(tmp_path),
        transport_factory=Transport,
        discovery=lambda *args: measured(identity[0])["discovery_snapshot"],
    )
    original = apply(s, ready(s))
    assert original["access"]["mode"] == "active" and not s.store.snapshot().policy.grants
    params = access_params(s, [("FILES", "Belege", "read")])
    confirmed = s.dispatch(
        "access.apply", confirmation(s.dispatch("access.preview", params, actor="one")), actor="one"
    )
    for uid in ("other", "assistant"):
        identity[0] = uid
        now = apply(s, ready(s))
        assert now["user_id"] == uid and now["access"]["mode"] == "active"
        assert now["access"]["binding"]["generation"] != confirmed["access"]["binding"]["generation"]
        assert not any(
            row["desired_operations"] or row["effective_operations"] for row in now["access"]["rows"]
        )


def test_task_only_restriction_discloses_index_and_approval_revision_effect(access_service):
    s = access_service
    params = access_params(s, [("VTODO", None, "read")])
    first = s.dispatch("access.preview", params, actor="one")
    assert {"search-and-sync", "pending-actions", "tasks"} <= {
        w["workflow"] for w in first["affected_workflows"]
    }


def grant_levels(s, levels):
    params = access_params(s, levels)
    preview = s.dispatch("access.preview", params, actor="one")
    return s.dispatch("access.apply", confirmation(preview), actor="one")


@pytest.mark.parametrize("metadata_save", ["none", "revoke", "talk"])
def test_negative_account_observation_survives_owner_restart_and_metadata_cas(access_service, metadata_save):
    s = access_service
    grant_levels(s, [("FILES", "Belege", "read")])
    s.transport_factory = Transport

    def failed(*args):
        raise OnboardingError("authentication-failed")

    s.discovery = failed
    failure = s.dispatch("connection.refresh", {"expected_revision": s.status()["revision"]}, actor="one")
    if metadata_save == "revoke":
        grant_levels(s, [("FILES", "Belege", "off")])
    elif metadata_save == "talk":
        s.dispatch(
            "talk.configure", {"enabled": False, "expected_revision": s.status()["revision"]}, actor="one"
        )
    restarted = NextcloudSetupService(s.store, transport_factory=Transport)
    status = restarted.status()
    assert status["refresh_failure"]["code"] == failure["code"]
    assert status["refresh_failure"]["revision"] == status["revision"]
    with pytest.raises(OnboardingError) as exc:
        grant_levels(restarted, [("VEVENT", None, "read")])
    assert exc.value.code == "rights-unverified"
    restarted.discovery = lambda *args: s.store.read()["discovery_snapshot"]
    restarted.dispatch("connection.refresh", {"expected_revision": status["revision"]}, actor="one")
    grant_levels(restarted, [("VEVENT", None, "read")])


@pytest.mark.parametrize("newest", ["replaced", "absent", "failed"])
def test_new_folder_evidence_reaches_status_runtime_and_unrelated_apply(access_service, monkeypatch, newest):
    from personal_assistant.nextcloud_access import decide_access

    s = access_service
    grant_levels(s, [("FILES", "Belege", "read"), ("FILES", "BelegeAlt", "read")])
    snap = s.store.snapshot()
    original = next(g.scope for g in snap.policy.grants if g.scope.path == "Belege")
    sibling = next(g.scope for g in snap.policy.grants if g.scope.path == "BelegeAlt")
    raw = {**snap.connection["discovery_snapshot"]["folders"][0], "file_id": "88"}

    def probe(t, c, path):
        assert path == "Belege"
        if newest == "failed":
            raise OnboardingError("resource-unavailable")
        return {
            "path": path,
            "checked_at": raw["checked_at"],
            "folders": [raw] if newest == "replaced" else [],
        }

    monkeypatch.setattr("personal_assistant.nextcloud_setup.discover_folders", probe)
    s.transport_factory = Transport
    if newest == "failed":
        with pytest.raises(OnboardingError):
            s.dispatch("folders.list", {"path": "/Belege/"}, actor="one")
    else:
        opened = s.dispatch("folders.list", {"path": "/Belege/"}, actor="one")
        assert opened["path"] == "Belege"
        assert opened.get("access_row", {}).get("scope", {}).get("file_id") == (
            "88" if newest == "replaced" else None
        )
    for current in (s, NextcloudSetupService(s.store)):
        assert current.store.snapshot().binding == snap.binding
        assert not decide_access(current.store.snapshot(), original, "read").allowed
        assert decide_access(current.store.snapshot(), sibling, "read").allowed
        row = next(r for r in current.status()["access"]["rows"] if r["scope"] == original.to_dict())
        assert row["effective_operations"] == [] and row["supported_levels"] == ["off"]
    grant_levels(s, [("VEVENT", None, "read")])
    assert not decide_access(s.store.snapshot(), original, "read").allowed
    assert decide_access(s.store.snapshot(), sibling, "read").allowed


def test_policy_capacity_is_a_preview_limit_and_existing_rows_remain_editable(access_service):
    from test_nextcloud_access import update_policy

    from personal_assistant.nextcloud_access import MAX_GRANTS

    s = access_service
    scope = access_params(s, [("FILES", "Belege", "off")])["edits"][0]["scope"]
    grants = [
        {"scope": {**scope, "path": f"old-{i:04}", "file_id": str(100 + i)}, "operations": []}
        for i in range(MAX_GRANTS)
    ]
    update_policy(s.store, grants)
    before = s.store.snapshot()
    args = {
        "account_id": before.binding.account_id,
        "expected_revision": before.connection["revision"],
        "expected_policy_revision": before.binding.policy_revision,
        "confirm_root": False,
        "edits": [{"scope": scope, "level": "read"}],
    }
    with pytest.raises(OnboardingError) as exc:
        s.dispatch("access.preview", args, actor="one")
    assert exc.value.code == "access-grant-limit"
    assert "1024" in str(exc.value) and s.store.snapshot() == before
    args["edits"] = [{"scope": grants[0]["scope"], "level": "off"}]
    assert s.dispatch("access.preview", args, actor="one")["state"] == "ready"


@pytest.mark.parametrize("credential", ["11", "Belege"])
def test_redacted_selected_scope_can_only_be_revoked_using_its_current_handle(access_service, credential):
    s = access_service
    grant_levels(s, [("FILES", "Belege", "read"), ("FILES", "BelegeAlt", "read")])
    old = s.store.read()
    s.store.save({**old, "app_password": credential}, expected_revision=old["revision"])
    status = s.status()
    chosen = next(r for r in status["access"]["rows"] if r["scope"]["path"] in {"Belege", "[redacted]"})
    assert "[redacted]" in chosen["scope"].values()
    args = {
        "account_id": status["account_id"],
        "expected_revision": status["revision"],
        "expected_policy_revision": status["access"]["binding"]["policy_revision"],
        "confirm_root": False,
        "edits": [{"revoke_handle": chosen["revoke_handle"], "level": "off"}],
    }
    assert chosen["supported_levels"] == ["off"]
    with pytest.raises(OnboardingError):
        s.dispatch("access.preview", {**args, "edits": [{**args["edits"][0], "level": "read"}]}, actor="one")
    preview = s.dispatch("access.preview", args, actor="one")
    assert preview["changes"][0]["scope"] == chosen["scope"]
    s.dispatch("access.apply", confirmation(preview), actor="one")
    assert {g.scope.path: bool(g.operations) for g in s.store.snapshot().policy.grants} == {
        "Belege": False,
        "BelegeAlt": True,
    }
    current = s.store.snapshot()
    with pytest.raises(OnboardingError):
        s.dispatch(
            "access.preview",
            {
                **args,
                "expected_revision": current.connection["revision"],
                "expected_policy_revision": current.binding.policy_revision,
            },
            actor="one",
        )


def test_failed_legacy_root_probe_is_withheld_on_implicit_migration_after_restart(tmp_path, monkeypatch):
    from test_nextcloud_access import measured

    from personal_assistant.nextcloud_connection import initialize_access_store

    conn = measured()
    root = conn["discovery_snapshot"]["file_roots"][0]
    root.update(
        path="",
        file_id="1",
        file_permissions="GCK",
        read_check={"status": "ok"},
        privileges_verified=True,
        can_read=True,
        can_create_file=True,
        can_mkdir=True,
    )
    store = ConnectionStore(tmp_path / "store")
    store.save(conn, expected_revision="")
    state = tmp_path / "state"
    state.mkdir()
    initialize_access_store(state, store.directory, transition="verified-pre-policy")
    s = NextcloudSetupService(store, transport_factory=Transport)

    def missing(t, c, path):
        assert path == ""
        return {"path": "", "folders": []}

    monkeypatch.setattr("personal_assistant.nextcloud_setup.discover_folders", missing)
    s.dispatch("folders.list", {"path": "/"}, actor="one")
    s = NextcloudSetupService(store)
    current = s.status()
    preview = s.dispatch(
        "access.preview",
        {
            "expected_revision": current["revision"],
            "expected_policy_revision": "",
            "account_id": current["account_id"],
            "confirm_root": True,
            "edits": [],
        },
        actor="one",
    )
    withheld = [w for w in preview["withheld"] if (w["scope"] or {}).get("component") == "FILES"]
    assert {w["operation"] for w in withheld} == {"list", "read", "search", "mkdir", "create_file"}
    assert all(w["code"] == "rights-unverified" and w["scope"]["file_id"] == "1" for w in withheld)
    assert not any(c["scope"]["component"] == "FILES" and c["before_operations"] for c in preview["changes"])
    s.dispatch("access.apply", confirmation(preview), actor="one")
    assert all(g.scope.component != "FILES" or not g.operations for g in store.snapshot().policy.grants)


def test_browsed_draft_folder_remains_selectable_across_independent_folder_measurement(
    access_service, monkeypatch
):
    s = access_service
    s.transport_factory = Transport
    grant_levels(s, [("FILES", "BelegeAlt", "read")])
    conn = s.store.read()
    raw = conn["discovery_snapshot"]["folders"][0]

    def measure(t, c, path):
        file_id = "50" if path == "Belege/Team" else "13"
        own = {
            **raw,
            "path": path,
            "file_id": file_id,
            "href": "/nc/remote.php/dav/files/assistant/" + path + "/",
            "checked_at": datetime.now(UTC).isoformat(),
        }
        return {"path": path, "folders": [own], "checked_at": own["checked_at"]}

    monkeypatch.setattr("personal_assistant.nextcloud_setup.discover_folders", measure)
    first = s.dispatch("folders.list", {"path": "Belege/Team"}, actor="one")
    second = s.dispatch("folders.list", {"path": "/BelegeAlt/"}, actor="one")
    assert second["source_revision"] == first["revision"] and second["revision"] != first["revision"]
    rows = s.status()["access"]["rows"]
    assert any(r["scope"] == first["access_row"]["scope"] for r in rows)
    params = access_params(
        s, [("VEVENT", None, "read"), ("FILES", "Belege/Team", "read"), ("FILES", "BelegeAlt", "create")]
    )
    preview = s.dispatch("access.preview", params, actor="one")
    assert len(preview["changes"]) == 3


@pytest.mark.parametrize("mode", ["active", "legacy"])
def test_failed_account_withholds_existing_domain_content_until_verified(tmp_path, monkeypatch, mode):
    from types import SimpleNamespace

    from test_nextcloud_access import managed, measured, scope

    from mail_agent.config import NextcloudConfig
    from mail_agent.nextcloud import NextcloudSkillClient
    from personal_assistant.config import AssistantConfig
    from personal_assistant.connectors.nextcloud.client import DavResponse, NextcloudClient
    from personal_assistant.nextcloud_access import Scope, decide_access
    from personal_assistant.nextcloud_connection import effective_resources, initialize_access_store
    from personal_assistant.nextcloud_index_access import document_visible, scope_record, sync_state_visible

    conn = measured()
    if mode == "legacy":
        store = ConnectionStore(tmp_path / "store")
        store.save(conn, expected_revision="")
        state = tmp_path / "state"
        state.mkdir()
        initialize_access_store(state, store.directory, transition="verified-pre-policy")
    else:
        store = managed(tmp_path)
        store.save(conn, expected_revision="")
    service = NextcloudSetupService(store, transport_factory=Transport)
    if mode == "active":
        grant_levels(service, [("FILES", "Belege", "read"), ("VEVENT", None, "read")])
    folder = Scope(**scope(conn, "FILES", "Belege", "11"))
    path = "Belege/fixture"
    generation = json.dumps(scope_record(store.snapshot(), folder, path)) if mode == "active" else None

    def visible():
        return document_visible(store.snapshot(), "nextcloud-file", folder.resource_id, path, generation)

    assert visible() and effective_resources(store.snapshot())

    def failed(*args):
        raise OnboardingError("authentication-failed")

    service.discovery = failed
    service.dispatch("connection.refresh", {"expected_revision": service.status()["revision"]}, actor="one")
    service = NextcloudSetupService(store, transport_factory=Transport)
    snap = store.snapshot()
    assert not visible()
    assert document_visible(snap, "email", "mail-agent", "fixture", None)
    assert sync_state_visible(snap, {"scope": "projection", "resource_id": "mail-agent"})
    state = {"scope": "files", "resource_id": folder.resource_id}
    if mode == "active":
        state["detail"] = json.dumps({"nextcloud_source": scope_record(snap, folder)})
    assert not sync_state_visible(snap, state)
    assert not effective_resources(snap)
    if mode == "active":
        assert decide_access(snap, folder, "read").code == "rights-unverified"
        assert decide_access(snap, Scope(**scope(conn)), "read").code == "rights-unverified"
        row = next(r for r in service.status()["access"]["rows"] if r["scope"] == folder.to_dict())
        assert "read" in row["desired_operations"] and "read" in row["server"]["operations"]
        assert row["effective_operations"] == [] and row["supported_levels"] == ["off"]
        raw = conn["discovery_snapshot"]["folders"][0]
        monkeypatch.setattr(
            "personal_assistant.nextcloud_setup.discover_folders",
            lambda t, c, path: {"path": path, "folders": [raw]},
        )
        opened = service.dispatch("folders.list", {"path": folder.path}, actor="one")["access_row"]
        assert "read" in opened["server"]["operations"]
        assert opened["supported_levels"] == ["off"] and not visible()
    else:
        monkeypatch.setenv("OPENCLAW_NEXTCLOUD_CONNECTION_DIR", str(store.directory))
        bridge = NextcloudSkillClient(
            SimpleNamespace(
                nextcloud=NextcloudConfig(enabled=True, contact_cache_file=tmp_path / "cache"),
                runtime=SimpleNamespace(command_timeout_seconds=5),
            ),
            None,
        )
        bridge._write_contact_cache(
            {"fixture@example.invalid"}, {"legacy_revision": snap.connection["revision"]}, time.time()
        )
        assert not bridge.is_known_contact("fixture@example.invalid")
        assert bridge._last_contact_error == "rights-unverified"
        client = NextcloudClient(AssistantConfig())
        sent = []

        def transport(method, target, *args, **kwargs):
            sent.append((method, target))
            return DavResponse(200, "", {}, b"", target)

        monkeypatch.setattr(client, "_managed_request", transport)
        with pytest.raises(PermissionError, match="rights-unverified"):
            client.request("GET", "/fixture")
        client.request("GET", "/status.php")
        assert sent == [("GET", "/status.php")]

    service.discovery = lambda *args: conn["discovery_snapshot"]
    service.dispatch("connection.refresh", {"expected_revision": service.status()["revision"]}, actor="one")
    assert visible() and effective_resources(store.snapshot())


def test_diagnostics_age_uses_the_oldest_retained_check_of_each_domain(access_service):
    s = access_service
    now = datetime.now(UTC)
    conn = s.store.read()
    inv = conn["discovery_snapshot"]
    old = (now - timedelta(seconds=86399)).isoformat()
    latest = now.isoformat()
    inv.update(checked_at=latest, oldest_collection_check_at=old)
    # A mixed calendar's fresh probe and the independently retained address book.
    inv["calendars"][0].update(checked_at=latest, read_check_source="probed")
    inv["addressbooks"] = [{**inv["calendars"][0], "href": "/nc/custom/book/", "kind": "addressbook",
                            "checked_at": old, "read_check_source": "retained", "components": ["VCARD"]}]
    s.store.save(conn, expected_revision=conn["revision"])
    s.wall_clock = lambda: now
    status = s.dispatch("status", {}, actor="operator")
    assert status["diagnostics_freshness"]["age_seconds"] == 0
    domain = status["diagnostics_domain_freshness"]
    assert domain["addressbooks"]["age_seconds"] == 86399 and domain["addressbooks"]["checked_at"] == old
    assert domain.get("calendars", status["diagnostics_freshness"])["age_seconds"] == 0
    s.wall_clock = lambda: now + timedelta(seconds=2)
    later = s.status()
    assert later["diagnostics_domain_freshness"]["addressbooks"]["state"] == "stale"
    assert later["diagnostics_freshness"]["state"] == "fresh"
