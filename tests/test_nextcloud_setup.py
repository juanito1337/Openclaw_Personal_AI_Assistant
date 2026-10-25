from __future__ import annotations

import http.client
import json
import socket
import subprocess
import threading
import uuid
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

    def request(operation="status", parameters=None, **overrides):
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
            "proof": {"payload": payload, "signature": sign_fixture(payload)["signature"]}
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
    assert result == {"state": "failed", "code": "authentication-failed"}
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
    assert PASSWORD not in json.dumps(result)
    assert setup_service.store.read() == original


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
    assert result == {"state": "failed", "code": "invalid-user-id"}
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
