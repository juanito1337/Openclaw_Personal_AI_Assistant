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


def test_failed_discovery_can_retry_without_consuming_credentials_twice(setup_service):
    calls = []

    def flaky(transport, credentials):
        calls.append(True)
        if len(calls) == 1:
            raise OnboardingError("temporary-failure")
        return discovery(transport, credentials)

    setup_service.discovery = flaky
    start = setup_service.dispatch(
        "login.start", {"server_url": "https://cloud.example/nc"}, actor="device-one"
    )
    with pytest.raises(OnboardingError):
        setup_service.dispatch("login.poll", {"flow_id": start["flow_id"]}, actor="device-one")
    result = setup_service.dispatch("login.poll", {"flow_id": start["flow_id"]}, actor="device-one")
    assert result["state"] == "ready"
    pending = setup_service.flows[start["flow_id"]]
    assert sum(path.endswith("/login/poll") for _, path in pending.transport.calls) == 1


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
    tmp_path: Path, setup_service, operator_proof
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
