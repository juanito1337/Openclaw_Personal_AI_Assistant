from __future__ import annotations

import hashlib
import io
import json
import urllib.error
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from docker import job_loop
from mail_agent.assistant_bridge import PersonalAssistantActionBridge
from mail_agent.models import ParsedMessage
from personal_assistant.actions import ActionService
from personal_assistant.connectors.nextcloud.client import NextcloudClient, NextcloudError
from personal_assistant.container_job_profiles import config as job_config
from personal_assistant.job_runtime import resolve_job_run
from personal_assistant.models import ActionPlan, PolicyDecision, Resource


def action(
    status: str,
    *,
    action_type: str = "files.create",
    payload: dict[str, object] | None = None,
    action_id: str = "action-1",
) -> ActionPlan:
    return ActionPlan(
        id=action_id,
        idempotency_key="key",
        action_type=action_type,
        resource_id="resource-1",
        payload=dict(payload or {}),
        status=status,
        requires_approval=True,
        created_at="2026-09-20T00:00:00+00:00",
        updated_at="2026-09-20T00:00:00+00:00",
        error="",
    )


class FakeStorage:
    def __init__(self, current: ActionPlan) -> None:
        self.current = current
        self.audits: list[tuple[str, dict[str, object]]] = []

    def get_action(self, action_id: str) -> ActionPlan:
        assert action_id == self.current.id
        return self.current

    def update_action(self, action_id: str, status: str, error: str = "") -> ActionPlan:
        assert action_id == self.current.id
        self.current = replace(self.current, status=status, error=error)
        return self.current

    def create_action(self, **values: object) -> ActionPlan:
        self.current = action(
            "proposed" if values["requires_approval"] else "approved",
            action_type=str(values["action_type"]),
            payload=dict(values["payload"]),  # type: ignore[arg-type]
        )
        return self.current

    def audit(self, event: str, detail: dict[str, object], **_: object) -> None:
        self.audits.append((event, detail))


class FakePolicy:
    def __init__(self, *, allowed: bool = True, approval: bool = True) -> None:
        self.allowed = allowed
        self.approval = approval

    def decide(self, *_: object) -> PolicyDecision:
        return PolicyDecision(self.allowed, self.approval, "denied" if not self.allowed else "ok")


class FakeFiles:
    def __init__(self) -> None:
        self.present: dict[str, bytes] = {}
        self.calls: list[tuple[str, object]] = []

    def exists(self, path: str) -> bool:
        return path in self.present

    def download(self, path: str) -> bytes:
        return self.present[path]

    def ensure_folder(self, path: str) -> None:
        self.calls.append(("mkdir", path))

    def upload_new(self, path: str, data: bytes, content_type: str) -> None:
        self.calls.append(("upload", (path, data, content_type)))
        self.present[path] = data


def action_service(current: ActionPlan, *, files: FakeFiles | None = None) -> ActionService:
    storage = FakeStorage(current)
    registry = SimpleNamespace(
        get=lambda _resource_id: Resource(
            id="resource-1",
            kind="files",
            connector="nextcloud",
            enabled=True,
            remote_id="/remote/",
            permissions=("read", "create"),
        )
    )
    return ActionService(
        storage,  # type: ignore[arg-type]
        registry,  # type: ignore[arg-type]
        FakePolicy(),  # type: ignore[arg-type]
        SimpleNamespace(),  # type: ignore[arg-type]
        files or FakeFiles(),  # type: ignore[arg-type]
        SimpleNamespace(),  # type: ignore[arg-type]
        SimpleNamespace(),  # type: ignore[arg-type]
    )


@pytest.mark.parametrize(
    ("code", "stopped", "lease_lost", "business", "result", "resume"),
    [
        (0, False, False, "healthy", "completed", False),
        (1, False, False, "degraded", "degraded", False),
        (9, False, False, "failed", "failed", False),
        (75, False, False, "healthy", "completed", True),
        (75, False, True, "interrupted", "interrupted", False),
        (0, True, False, "interrupted", "interrupted", False),
    ],
)
def test_job_result_contract_is_deterministic_and_lease_loss_wins(
    code: int,
    stopped: bool,
    lease_lost: bool,
    business: str,
    result: str,
    resume: bool,
) -> None:
    outcome = resolve_job_run(
        code,
        stopped=stopped,
        lease_lost=lease_lost,
        previous_failures=2,
        claim_run_id="run-1",
    )
    assert outcome.business_status == business
    assert outcome.result == result
    assert outcome.batch_resume is resume
    assert outcome.resume_parent_run_id == ("run-1" if resume else "")
    assert outcome.consecutive_failures == (0 if business == "healthy" else 3)


@pytest.mark.parametrize("job", ["mail", "sync", "supervisor", "portfolio", "monitor"])
def test_each_container_job_has_a_fixed_role_and_image_command(job: str, tmp_path: Path) -> None:
    command, interval, delay, _default_on, env = job_config(
        job,
        tmp_path / "workspace",
        tmp_path / "image",
        environ={},
    )
    assert interval > 0 and delay >= 0
    assert env["OPENCLAW_ROLE"].endswith(("worker", "proxy"))
    assert str(tmp_path / "image") in " ".join(command)
    assert all("/srv/openclaw" not in part for part in command)


def test_mail_job_profile_preserves_bounded_drain_values(tmp_path: Path) -> None:
    command, interval, delay, default_on, env = job_config(
        "mail",
        tmp_path / "workspace",
        tmp_path / "image",
        environ={
            "MAIL_DRAIN_BATCH_SIZE": "7",
            "MAIL_MAX_MESSAGES": "21",
            "MAIL_MAX_RUNTIME": "90",
            "MAIL_SHUTDOWN_RESERVE": "10",
            "MAIL_MAX_BATCHES": "3",
            "MAIL_INTERVAL_SECONDS": "60",
            "MAIL_INITIAL_DELAY_SECONDS": "4",
        },
    )
    rendered = " ".join(command)
    assert "--batch-size 7" in rendered
    assert "--max-messages 21" in rendered
    assert "--max-runtime 90" in rendered
    assert "--shutdown-reserve 10" in rendered
    assert "--max-batches 3" in rendered
    assert (interval, delay, default_on) == (60, 4, True)
    assert env["OPENCLAW_OLLAMA_PRIORITY"] == "background"


def test_unknown_container_job_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="arbitrary"):
        job_config("arbitrary", tmp_path, tmp_path, environ={})


def test_job_loop_desired_state_is_fail_safe_for_valid_and_broken_json(tmp_path: Path) -> None:
    state = tmp_path / "job_control.json"
    state.write_text('{"desired":{"mail":false,"sync":true}}', encoding="utf-8")
    assert job_loop.desired(state, "mail", True) is False
    assert job_loop.desired(state, "sync", False) is True
    assert job_loop.desired(state, "portfolio", False) is False
    state.write_text("broken", encoding="utf-8")
    assert job_loop.desired(state, "mail", True) is True
    assert job_loop.desired(tmp_path / "missing.json", "mail", False) is False


def test_job_loop_heartbeat_publish_is_atomic_and_private(tmp_path: Path) -> None:
    heartbeat = tmp_path / "nested" / "mail.json"
    job_loop.atomic_json(heartbeat, {"state": "waiting", "pid": None})
    assert heartbeat.read_text(encoding="utf-8").endswith("\n")
    assert heartbeat.stat().st_mode & 0o777 == 0o600
    assert not heartbeat.with_suffix(".json.tmp").exists()


def test_job_loop_signal_sets_stop_without_other_side_effects() -> None:
    previous = job_loop.STOP
    try:
        job_loop.STOP = False
        job_loop.handler(15, object())
        assert job_loop.STOP is True
    finally:
        job_loop.STOP = previous


def test_job_loop_refuses_execution_outside_declared_image_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(job_loop.signal, "signal", lambda *_args: None)
    monkeypatch.setattr(
        job_loop.argparse.ArgumentParser,
        "parse_args",
        lambda _self: SimpleNamespace(job="sync"),
    )
    monkeypatch.setenv("OPENCLAW_IMAGE_ROOT", str(tmp_path / "different-image"))
    with pytest.raises(SystemExit, match="Worker-Loop liegt nicht im Imagepfad"):
        job_loop.main()


def test_job_loop_clean_stop_publishes_final_heartbeat_without_running_child(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = Path(__file__).resolve().parents[1]
    status_dir = tmp_path / "status"
    previous = job_loop.STOP
    try:
        job_loop.STOP = True
        monkeypatch.setattr(job_loop.signal, "signal", lambda *_args: None)
        monkeypatch.setattr(
            job_loop.argparse.ArgumentParser,
            "parse_args",
            lambda _self: SimpleNamespace(job="supervisor"),
        )
        monkeypatch.setenv("OPENCLAW_IMAGE_ROOT", str(root))
        monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path / "workspace"))
        monkeypatch.setenv("OPENCLAW_JOB_STATUS_DIR", str(status_dir))
        monkeypatch.setenv("OPENCLAW_LOG_DIR", str(tmp_path / "logs"))
        monkeypatch.setenv("OPENCLAW_COORDINATION_DATA_DIR", str(tmp_path / "coordination"))
        assert job_loop.main() == 0
    finally:
        job_loop.STOP = previous
    payload = json.loads((status_dir / "supervisor.json").read_text(encoding="utf-8"))
    assert payload["state"] == "stopped"


def test_action_execute_records_failure_instead_of_claiming_completion(tmp_path: Path) -> None:
    missing = tmp_path / "missing.pdf"
    service = action_service(
        action(
            "approved",
            payload={"path": "Invoices/missing.pdf", "local_path": str(missing)},
        )
    )
    result = service.execute("action-1")
    assert result.status == "failed"
    assert "No such file" in result.error
    assert [name for name, _ in service.storage.audits][-1] == "action.failed"  # type: ignore[attr-defined]


def test_action_execute_uploads_only_after_parent_creation(tmp_path: Path) -> None:
    source = tmp_path / "invoice.pdf"
    source.write_bytes(b"%PDF-fixture")
    files = FakeFiles()
    service = action_service(
        action(
            "approved",
            payload={
                "path": "Invoices/2026/invoice.pdf",
                "local_path": str(source),
                "content_type": "application/pdf",
            },
        ),
        files=files,
    )
    result = service.execute("action-1")
    assert result.status == "completed"
    assert files.calls[0] == ("mkdir", "Invoices/2026")
    assert files.calls[1][0] == "upload"


def test_workspace_reconciliation_refuses_overwrite_on_hash_conflict() -> None:
    expected = hashlib.sha256(b"expected").hexdigest()
    files = FakeFiles()
    files.present["Invoices/register.csv"] = b"different"
    service = action_service(
        action(
            "completed",
            payload={"path": "Invoices/register.csv", "sha256": expected},
        ),
        files=files,
    )
    result, duplicate = service.execute_workspace("action-1")
    assert duplicate is False
    assert result.status == "failed"
    assert "Ueberschreiben verboten" in result.error


@pytest.mark.parametrize(
    ("method", "action_type", "required_flag"),
    [
        ("approve_configured_calendar_tool", "calendar.create", "direct_calendar_tool"),
        ("approve_configured_tasks_tool", "tasks.create", "direct_tasks_tool"),
        ("approve_configured_contacts_tool", "contacts.create", "direct_contacts_tool"),
    ],
)
def test_direct_action_approval_requires_its_bound_tool_flag(
    method: str, action_type: str, required_flag: str
) -> None:
    service = action_service(action("proposed", action_type=action_type, payload={}))
    with pytest.raises(PermissionError, match="werkzeug"):
        getattr(service, method)("action-1", evidence={"tool_enabled": True})
    service.storage.current = replace(  # type: ignore[attr-defined]
        service.storage.current,  # type: ignore[attr-defined]
        payload={required_flag: True},
    )
    approved = getattr(service, method)("action-1", evidence={"tool_enabled": True})
    assert approved.status == "approved"


class BridgeActions:
    def __init__(self, plan: ActionPlan, result: ActionPlan | None = None) -> None:
        self.plan_value = plan
        self.result = result or replace(plan, status="completed")
        self.retried = False
        self.planned_payload: dict[str, object] = {}

    def plan(self, _kind: str, _resource: str, payload: dict[str, object], **_: object) -> ActionPlan:
        self.planned_payload = payload
        return self.plan_value

    def execute(self, _action_id: str) -> ActionPlan:
        return self.result

    def execute_workspace(self, _action_id: str) -> tuple[ActionPlan, bool]:
        return self.result, False

    def retry_managed_invoice_register(self, _action_id: str) -> ActionPlan:
        self.retried = True
        self.plan_value = replace(self.plan_value, status="approved")
        return self.plan_value

    def approve_trusted_command(self, _action_id: str, **_: object) -> ActionPlan:
        self.plan_value = replace(self.plan_value, status="approved")
        return self.plan_value


class BridgeAssistant:
    def __init__(self, root: Path, actions: BridgeActions) -> None:
        self.config = SimpleNamespace(runtime=SimpleNamespace(database=root / "assistant.sqlite3"))
        self.actions = actions
        self.closed = False
        self.registry = SimpleNamespace(
            get=lambda _resource: Resource(
                id="resource-1",
                kind="files",
                connector="nextcloud",
                enabled=True,
                permissions=("read", "create"),
            )
        )
        self.nextcloud_discovery = SimpleNamespace(root_health=lambda: {"ok": True})
        self.nextcloud_files = SimpleNamespace(
            clean_path=lambda value: str(value).strip("/"),
            download=lambda _path: b"%PDF-fixture",
        )

    def close(self) -> None:
        self.closed = True


def message() -> ParsedMessage:
    return ParsedMessage(
        stable_key="mid:fixture",
        mailbox_id="1",
        source_folder="INBOX",
        raw=b"fixture",
        subject="Fixture",
        sender_addr="sender@example.invalid",
    )


def test_bridge_archive_success_removes_staged_payload_and_closes(tmp_path: Path) -> None:
    actions = BridgeActions(action("approved"))
    assistant = BridgeAssistant(tmp_path, actions)
    bridge = PersonalAssistantActionBridge()
    bridge._open = lambda: assistant  # type: ignore[method-assign]
    result = bridge.archive_invoice(
        message=message(),
        attachment_hash="a" * 64,
        data=b"%PDF-fixture",
        remote_path="Invoices/fixture.pdf",
    )
    assert result.ok is True and result.status == "invoice-archived"
    assert assistant.closed is True
    assert not (tmp_path / "action_payloads" / "invoices" / f"{'a' * 64}.pdf").exists()
    assert actions.planned_payload["overwrite"] is False


def test_bridge_archive_failure_is_explicit_and_keeps_retry_payload(tmp_path: Path) -> None:
    actions = BridgeActions(action("proposed"))
    assistant = BridgeAssistant(tmp_path, actions)
    bridge = PersonalAssistantActionBridge()
    bridge._open = lambda: assistant  # type: ignore[method-assign]
    result = bridge.archive_invoice(
        message=message(),
        attachment_hash="b" * 64,
        data=b"%PDF-fixture",
        remote_path="Invoices/fixture.pdf",
    )
    assert result.ok is False and result.status == "invoice-action-not-approved"
    assert (tmp_path / "action_payloads" / "invoices" / f"{'b' * 64}.pdf").is_file()
    assert assistant.closed is True


def test_bridge_register_retries_only_failed_managed_plan_and_cleans_payload(tmp_path: Path) -> None:
    actions = BridgeActions(action("failed"))
    assistant = BridgeAssistant(tmp_path, actions)
    bridge = PersonalAssistantActionBridge()
    bridge._open = lambda: assistant  # type: ignore[method-assign]
    result = bridge.sync_invoice_register(
        data=b"year;amount\n2026;10\n",
        year=2026,
        remote_path="Invoices/2026/Rechnungen_2026.csv",
    )
    assert result.ok is True and actions.retried is True
    assert not list((tmp_path / "action_payloads" / "invoice-register").glob("*.csv"))
    assert assistant.closed is True


def test_bridge_pdf_read_is_confined_to_configured_folder(tmp_path: Path) -> None:
    assistant = BridgeAssistant(tmp_path, BridgeActions(action("approved")))
    bridge = PersonalAssistantActionBridge()
    bridge._open = lambda: assistant  # type: ignore[method-assign]
    with pytest.raises(PermissionError, match="konfigurierten Rechnungsordners"):
        bridge.read_invoice_pdf(
            remote_path="Other/secret.pdf",
            allowed_folder="Invoices",
            resource_id="resource-1",
        )
    assert assistant.closed is True


def test_bridge_calendar_command_approves_then_executes_once(tmp_path: Path) -> None:
    actions = BridgeActions(action("proposed", action_type="calendar.create"))
    assistant = BridgeAssistant(tmp_path, actions)
    bridge = PersonalAssistantActionBridge()
    bridge._open = lambda: assistant  # type: ignore[method-assign]
    result = bridge.create_calendar_event(
        message=message(),
        resource_id="calendar-1",
        ics="BEGIN:VCALENDAR\nEND:VCALENDAR\n",
        uid="uid-1",
        fingerprint="fingerprint",
        sender="owner@example.invalid",
    )
    assert result.ok is True and result.status == "created"
    assert assistant.closed is True


def nextcloud_config() -> SimpleNamespace:
    return SimpleNamespace(
        nextcloud=SimpleNamespace(
            base_url_env="NC_URL",
            username_env="NC_USER",
            token_env="NC_TOKEN",
            request_timeout_seconds=17,
        )
    )


@pytest.mark.parametrize("url", ["http://public.example", "ftp://cloud.example"])
def test_nextcloud_client_rejects_non_tls_public_endpoints(url: str) -> None:
    client = NextcloudClient(nextcloud_config())  # type: ignore[arg-type]
    with (
        patch.dict(
            "os.environ", {"NC_URL": url, "NC_USER": "u", "NC_TOKEN": "t"}, clear=False
        ),
        pytest.raises(NextcloudError, match="HTTPS"),
    ):
        client.validate_url()


def test_nextcloud_client_maps_http_error_without_losing_bounded_detail() -> None:
    client = NextcloudClient(nextcloud_config())  # type: ignore[arg-type]
    error = urllib.error.HTTPError(
        "https://cloud.example/remote.php/dav",
        412,
        "Precondition Failed",
        {},
        io.BytesIO(b"etag conflict"),
    )
    with (
        patch.dict(
            "os.environ",
            {"NC_URL": "https://cloud.example", "NC_USER": "u", "NC_TOKEN": "t"},
            clear=False,
        ),
        patch("urllib.request.urlopen", side_effect=error),
        pytest.raises(NextcloudError, match="HTTP 412.*etag conflict"),
    ):
        client.request("PUT", "/remote.php/dav", expected={201, 204})
