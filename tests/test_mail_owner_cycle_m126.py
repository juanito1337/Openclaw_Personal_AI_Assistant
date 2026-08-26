from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from personal_assistant import mail_owner_cycle


def _state(path: Path, enabled: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"desired": {"mail": True, "mail-index": enabled}}),
        encoding="utf-8",
    )


def test_mail_owner_does_not_run_reconcile_while_desired_state_is_off(
    tmp_path: Path,
) -> None:
    state = tmp_path / "coordination/job_control.json"
    status = tmp_path / "status"
    _state(state, False)
    argv = [
        "mail_owner_cycle",
        "--state-path",
        str(state),
        "--status-dir",
        str(status),
        "--image-root",
        str(tmp_path / "image"),
        "--",
        "mail-command",
    ]
    with mock.patch("sys.argv", argv), mock.patch(
        "subprocess.run", return_value=SimpleNamespace(returncode=0)
    ) as run:
        assert mail_owner_cycle.main() == 0
    assert run.call_count == 1
    heartbeat = json.loads((status / "mail-index.json").read_text(encoding="utf-8"))
    assert heartbeat["state"] == "disabled"


def test_mail_owner_serializes_mail_then_reconcile_when_enabled(tmp_path: Path) -> None:
    state = tmp_path / "coordination/job_control.json"
    status = tmp_path / "status"
    image = tmp_path / "image"
    _state(state, True)
    argv = [
        "mail_owner_cycle",
        "--state-path",
        str(state),
        "--status-dir",
        str(status),
        "--image-root",
        str(image),
        "--",
        "mail-command",
    ]
    with mock.patch("sys.argv", argv), mock.patch(
        "subprocess.run",
        side_effect=[SimpleNamespace(returncode=0), SimpleNamespace(returncode=0)],
    ) as run:
        assert mail_owner_cycle.main() == 0

    assert run.call_args_list[0].args[0] == ["mail-command"]
    reconcile = run.call_args_list[1].args[0]
    assert reconcile[:4] == [
        str(image / "scripts/assistant.sh"),
        "mail",
        "index",
        "reconcile",
    ]
    assert reconcile[-1] == "--yes"
    heartbeat = json.loads((status / "mail-index.json").read_text(encoding="utf-8"))
    assert heartbeat["state"] == "waiting"
    assert heartbeat["last_exit_code"] == 0


def test_failed_mail_cycle_blocks_reconcile(tmp_path: Path) -> None:
    state = tmp_path / "coordination/job_control.json"
    status = tmp_path / "status"
    _state(state, True)
    argv = [
        "mail_owner_cycle",
        "--state-path",
        str(state),
        "--status-dir",
        str(status),
        "--image-root",
        str(tmp_path / "image"),
        "--",
        "mail-command",
    ]
    with mock.patch("sys.argv", argv), mock.patch(
        "subprocess.run", return_value=SimpleNamespace(returncode=1)
    ) as run:
        assert mail_owner_cycle.main() == 1
    assert run.call_count == 1
    heartbeat = json.loads((status / "mail-index.json").read_text(encoding="utf-8"))
    assert heartbeat["result"] == "degraded"


def test_mail_lock_contention_is_deferred_without_starting_reconcile(
    tmp_path: Path,
) -> None:
    state = tmp_path / "coordination/job_control.json"
    status = tmp_path / "status"
    _state(state, True)
    argv = [
        "mail_owner_cycle",
        "--state-path",
        str(state),
        "--status-dir",
        str(status),
        "--image-root",
        str(tmp_path / "image"),
        "--",
        "mail-command",
    ]
    with mock.patch("sys.argv", argv), mock.patch(
        "subprocess.run", return_value=SimpleNamespace(returncode=3)
    ) as run:
        assert mail_owner_cycle.main() == 0

    assert run.call_count == 1
    heartbeat = json.loads((status / "mail-index.json").read_text(encoding="utf-8"))
    assert heartbeat["state"] == "waiting"
    assert heartbeat["result"] == "deferred"
    assert heartbeat["last_exit_code"] is None
    assert "Single-Writer-Sperre" in heartbeat["detail"]
    assert "last_success_at" not in heartbeat
