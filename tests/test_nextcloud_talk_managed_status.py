"""Managed Talk diagnostics use only synthetic stores, observations and API replies."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from personal_assistant import nextcloud_talk, nextcloud_talk_status
from personal_assistant.nextcloud_connection import ConnectionError
from personal_assistant.nextcloud_onboarding import HttpResponse, OnboardingError
from personal_assistant.nextcloud_talk_account import TalkAccountWorker, TalkInbox, account_identity

NOW = 1_700_000_000.0


@pytest.fixture
def connection() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "revision": "revision-one",
        "server_url": "https://cloud.example/nc",
        "login_name": "agent",
        "app_password": "synthetic-never-return-this",
        "user_id": "agent",
        "expected_ips": ["192.168.12.30"],
        "discovery_snapshot": {"capabilities": {"spreed": {"features": ["chat-reference-id"]}}},
        "talk": {"enabled": True, "room_tokens": ["roomA"], "allowed_user_ids": ["jan"]},
    }


@pytest.fixture
def managed(monkeypatch, tmp_path, connection):
    monkeypatch.setenv("OPENCLAW_CORE_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(nextcloud_talk, "load_connection", lambda: connection)
    monkeypatch.setattr(nextcloud_talk_status.time, "time", lambda: NOW)
    # Managed diagnostics must never fall into legacy secret/config probing.
    monkeypatch.setattr(nextcloud_talk, "inspect_secret", lambda *_: pytest.fail("legacy secret inspected"))
    monkeypatch.setattr(
        nextcloud_talk, "load_json_object", lambda *_a, **_kw: pytest.fail("legacy config read")
    )
    return tmp_path


def observation(path: Path, connection: dict[str, Any], **changes: Any) -> Path:
    payload = {
        "schema_version": 1,
        "account": account_identity(connection),
        "connection_revision": connection["revision"],
        "observed_at": NOW - 1,
        "enabled": True,
        "connected": True,
        "code": "talk-poll-ok",
        "room_count": 1,
        "counts": {"accepted": 0, "processing": 0, "completed": 2, "uncertain": 0, "delivery_uncertain": 0},
        **changes,
    }
    target = path / "nextcloud-talk.status.json"
    target.write_text(json.dumps(payload))
    target.chmod(0o600)
    return target


def test_status_never_creates_inbox_or_starts_worker(managed, connection):
    assert list(managed.iterdir()) == []
    report = nextcloud_talk.status()
    assert report["mode"] == "managed-account"
    assert report["enabled"] is True and report["connected"] is False
    assert report["state"] == "not-observed" and report["ok"] is False
    assert list(managed.iterdir()) == []
    connection["talk"]["enabled"] = False
    disabled = nextcloud_talk.status()
    assert disabled["state"] == "disabled" and disabled["desired_state"] == "OFF"
    assert disabled["ok"] is True and disabled["connected"] is False
    assert list(managed.iterdir()) == []


@pytest.mark.parametrize(
    ("changes", "state", "connected"),
    [
        ({}, "active", True),
        ({"observed_at": NOW - 91}, "stale", False),
        ({"account": "different-account"}, "pending", False),
        ({"connection_revision": "previous-revision"}, "pending", False),
        ({"room_count": 2}, "pending", False),
        ({"enabled": False, "connected": False, "code": "talk-disabled"}, "pending", False),
        ({"connected": False, "code": "talk-poll-failed"}, "failed", False),
        ({"connected": False, "code": "talk-configured"}, "pending", False),
        ({"counts": {"uncertain": 1}}, "degraded", True),
        ({"counts": {"delivery_uncertain": 1}}, "degraded", True),
    ],
)
def test_worker_observation_distinguishes_current_stale_failed_and_uncertain(
    managed, connection, changes, state, connected
):
    observation(managed, connection, **changes)
    report = nextcloud_talk.status()
    assert report["state"] == state
    assert report["connected"] is connected
    assert report["ok"] is (state == "active")
    encoded = json.dumps(report)
    assert connection["app_password"] not in encoded
    assert connection["login_name"] not in encoded
    assert "roomA" not in encoded and "jan" not in encoded


@pytest.mark.parametrize(
    "changes",
    [
        {"observed_at": NOW + 60},
        {"observed_at": float("nan")},
        {"schema_version": 99},
        {"enabled": "yes"},
        {"code": "arbitrary-secret-value"},
        {"counts": {"accepted": -1}},
    ],
)
def test_invalid_telemetry_fails_closed_without_echoing_payload(managed, connection, changes):
    observation(managed, connection, **changes)
    report = nextcloud_talk.status()
    assert report["state"] == "invalid" and report["ok"] is False
    assert "arbitrary-secret-value" not in json.dumps(report)


def test_symlink_and_oversized_observations_are_not_followed_or_read_unbounded(managed, connection):
    other = managed / "unrelated.txt"
    other.write_text("private content")
    path = managed / "nextcloud-talk.status.json"
    path.symlink_to(other)
    assert nextcloud_talk.status()["state"] == "invalid"
    path.unlink()
    path.write_bytes(b"x" * (nextcloud_talk_status.MAX_OBSERVATION_BYTES + 1))
    path.chmod(0o600)
    assert nextcloud_talk.status()["state"] == "invalid"
    assert other.read_text() == "private content"


def test_disabled_configuration_does_not_reuse_old_connected_heartbeat(managed, connection):
    observation(managed, connection)
    connection["talk"]["enabled"] = False
    report = nextcloud_talk.status()
    assert report["state"] == "disabled" and report["connected"] is False
    assert report["warnings"]


def test_no_live_doctor_does_not_construct_transport(managed, connection, monkeypatch):
    observation(managed, connection)
    monkeypatch.setattr(
        nextcloud_talk_status, "SafeNextcloudTransport", lambda *_a, **_kw: pytest.fail("network")
    )
    report = nextcloud_talk.doctor(live=False)
    assert report["state"] == "active"
    assert report["live"] == {"requested": False, "ok": None, "worker_health_proven": False}


def api_reply(rooms=None, *, status=200):
    rooms = rooms if rooms is not None else [{"token": "roomA", "permissions": 128, "readOnly": 0}]
    return HttpResponse(
        status, {}, json.dumps({"ocs": {"meta": {"status": "ok", "statuscode": 200}, "data": rooms}}).encode()
    )


def fake_api(monkeypatch, reply=None, error=None):
    calls = []

    class Transport:
        def __init__(self, base_url, **kwargs):
            calls.append(("init", base_url, kwargs))
            if error:
                raise error

        def request(self, method, path, **kwargs):
            calls.append((method, path, kwargs))
            return reply or api_reply()

    monkeypatch.setattr(nextcloud_talk_status, "SafeNextcloudTransport", Transport)
    return calls


def test_live_doctor_uses_one_bound_authenticated_read_and_preserves_worker_failure(
    managed, connection, monkeypatch
):
    observation(managed, connection, connected=False, code="talk-poll-failed")
    calls = fake_api(monkeypatch)
    report = nextcloud_talk.doctor(timeout=3)
    assert report["live"]["ok"] is True and report["live"]["api_reachable"] is True
    assert report["live"]["worker_health_proven"] is False
    assert report["state"] == "failed" and report["ok"] is False and report["connected"] is False
    assert calls[0] == (
        "init",
        connection["server_url"],
        {
            "allow_private": True,
            "expected_ips": connection["expected_ips"],
            "timeout_seconds": 3,
            "max_response_bytes": 262_144,
        },
    )
    assert len(calls) == 2 and calls[1][:2] == ("GET", "ocs/v2.php/apps/spreed/api/v4/room?noStatusUpdate=1")
    assert calls[1][2]["headers"]["Authorization"].startswith("Basic ")
    assert "Authorization" not in json.dumps(report)


@pytest.mark.parametrize("worker_age", [91, 200])
def test_api_success_never_relabels_stale_worker_as_healthy(managed, connection, monkeypatch, worker_age):
    observation(managed, connection, observed_at=NOW - worker_age)
    fake_api(monkeypatch)
    report = nextcloud_talk.doctor()
    assert report["live"]["ok"] is True
    assert report["state"] == "stale" and report["connected"] is False and report["ok"] is False


@pytest.mark.parametrize(
    "reply,code",
    [
        (api_reply([], status=403), "talk-api-unavailable"),
        (api_reply([]), "talk-selected-room-not-writable"),
        (api_reply([{"token": "roomA", "permissions": 0, "readOnly": 0}]), "talk-selected-room-not-writable"),
        (
            api_reply([{"token": "roomA", "permissions": 128, "readOnly": 1}]),
            "talk-selected-room-not-writable",
        ),
        (api_reply([{"token": "bad/token"}]), "talk-api-response-invalid"),
    ],
)
def test_live_api_failure_does_not_leave_positive_worker_connection_claim(
    managed, connection, monkeypatch, reply, code
):
    observation(managed, connection)
    fake_api(monkeypatch, reply=reply)
    report = nextcloud_talk.doctor()
    assert report["live"]["code"] == code
    assert report["ok"] is False and report["connected"] is False and report["state"] == "failed"


@pytest.mark.parametrize(
    "error,code",
    [
        (OnboardingError("dns-changed", "sensitive upstream detail"), "dns-changed"),
        (OSError("synthetic-never-return-this"), "talk-api-check-failed"),
    ],
)
def test_transport_exceptions_never_echo_credentials_or_upstream_text(managed, monkeypatch, error, code):
    fake_api(monkeypatch, error=error)
    report = nextcloud_talk.doctor()
    assert report["live"]["code"] == code
    assert "sensitive upstream detail" not in json.dumps(report)
    assert "synthetic-never-return-this" not in json.dumps(report)


def test_broken_managed_connection_does_not_fall_back_to_legacy_secret_probe(managed, monkeypatch):
    def broken():
        raise ConnectionError("nextcloud-connection-invalid")

    monkeypatch.setattr(nextcloud_talk, "load_connection", broken)
    for report in [nextcloud_talk.status(), nextcloud_talk.doctor()]:
        assert report["mode"] == "managed-account" and report["state"] == "invalid"
        assert report["issues"] == ["nextcloud-connection-invalid"]


@pytest.mark.parametrize("explicit", ["config_path", "policy_path", "secret_path"])
def test_explicit_legacy_paths_never_load_managed_account(tmp_path, monkeypatch, explicit):
    config = tmp_path / "openclaw.json"
    config.write_text("{}")
    paths = {
        "config_path": config,
        "policy_path": tmp_path / "policy.json",
        "secret_path": tmp_path / "bot-secret",
    }
    monkeypatch.setattr(nextcloud_talk, "load_connection", lambda: pytest.fail("managed account accessed"))
    for key, value in paths.items():
        monkeypatch.setattr(nextcloud_talk, "default_" + key, lambda value=value: value)
    monkeypatch.setattr(nextcloud_talk, "default_contract_path", lambda: tmp_path / "contract.json")
    report = nextcloud_talk.status(**{explicit: paths[explicit]})
    assert report["state"] == "disabled" and "mode" not in report
    offline = nextcloud_talk.doctor(**{explicit: paths[explicit]}, live=False)
    assert offline["state"] == "disabled" and "mode" not in offline


def test_worker_persists_revision_bound_content_free_health(tmp_path, connection, monkeypatch):
    monkeypatch.setenv("OPENCLAW_CORE_DATA_DIR", str(tmp_path))
    inbox = TalkInbox(tmp_path / "nextcloud-talk.sqlite3")

    class Transport:
        def __init__(self, *_args, **_kwargs):
            pass

        def request(self, *_args, **_kwargs):
            return HttpResponse(200, {}, b'{"ocs":{"meta":{"statuscode":200},"data":[]}}')

    try:
        worker = TalkAccountWorker(inbox, lambda: copy.deepcopy(connection), Transport)
        worker.command({"operation": "poll"})
        stored = json.loads(inbox.status_path.read_text())
        assert stored["connection_revision"] == connection["revision"]
        assert stored["account"] == account_identity(connection)
        assert stored["connected"] is True
        assert connection["app_password"] not in json.dumps(stored)
        assert nextcloud_talk_status.managed_status(connection)["state"] == "active"
        connection["revision"] = "revision-two"
        assert nextcloud_talk_status.managed_status(connection)["state"] == "pending"
        connection["talk"]["enabled"] = False
        worker.command({"operation": "poll"})
        disabled = json.loads(inbox.status_path.read_text())
        assert disabled["connection_revision"] == "revision-two" and disabled["connected"] is False
    finally:
        inbox.close()
