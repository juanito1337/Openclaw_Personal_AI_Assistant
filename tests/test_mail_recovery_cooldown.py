from __future__ import annotations

import io
import json
import subprocess
from contextlib import redirect_stdout
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from mail_agent import cli as mail_cli
from mail_agent import setup_assistant
from personal_assistant import mail_worker
from personal_assistant.job_control import CommandResult, JobController, default_job_specs


@pytest.fixture
def gate_fixture(monkeypatch, tmp_path):
    config = SimpleNamespace(
        path=tmp_path / "mail.toml",
        runtime=SimpleNamespace(
            database=tmp_path / "mail.sqlite3", log_file=tmp_path / "mail.log",
            rules_file=tmp_path / "rules.toml", learning_folders_file=tmp_path / "learning.toml",
        ),
        nextcloud=SimpleNamespace(enabled=False),
        invoices=SimpleNamespace(enabled=False),
    )
    clock = datetime(2026, 11, 26, 20, tzinfo=UTC)

    class FixtureClock:
        @staticmethod
        def now():
            return clock

    monkeypatch.setattr(setup_assistant, "datetime", FixtureClock)
    checks = {
        name: {"ok": True} for name in ("himalaya", "folders", "mail_sources", "ollama", "database", "config")
    }
    monkeypatch.setattr(mail_cli, "load_env_file", lambda *args: None)
    monkeypatch.setattr(mail_cli, "load_config", lambda *args: config)
    monkeypatch.setattr(mail_cli, "_configure_logging", lambda *args: None)
    monkeypatch.setattr(mail_cli, "MailAgent", lambda *args, **kwargs: SimpleNamespace(
        doctor=lambda: checks, close=lambda: None,
    ))
    config.path.write_text("# previously validated\n", encoding="utf-8")
    setup_assistant.record_dry_run(config, processed=5, limit=5, errors=[])
    config.path.write_text("# new source fingerprint\n", encoding="utf-8")

    def production_check():
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            code = mail_cli.main(["production-check"])
        return subprocess.CompletedProcess([], code, stdout.getvalue(), "")

    def failed_dry_run():
        nonlocal clock
        clock += timedelta(seconds=1)
        errors = ["dry-run-model-timeout"]
        setup_assistant.record_dry_run(config, processed=2, limit=5, errors=errors)
        return subprocess.CompletedProcess([], 1, json.dumps({"processed": 2, "errors": errors}), "")

    return config.path, production_check, failed_dry_run


def _invoke(caller, monkeypatch, tmp_path, capture):
    if caller == "worker":
        monkeypatch.setattr(mail_worker, "_run_capture", capture)
        monkeypatch.setattr(
            mail_worker, "_run_productive", lambda *args, **kwargs: pytest.fail("productive run"),
        )
        return lambda: mail_worker.run_cycle(
            ["mail-agent", "run"],
            mail_agent="/fixture/mail-agent",
            state_path=tmp_path / "worker-recovery.json",
        )
    spec = next(item for item in default_job_specs() if item.name == "mail")

    def runner(command, timeout):
        result = capture(command)
        return CommandResult(result.returncode, result.stdout, result.stderr)

    controller = JobController(state_path=tmp_path / "controller-recovery.json", runner=runner, specs=(spec,))
    monkeypatch.setattr(controller, "_quiesce_mail_for_recovery", lambda _: {"ok": True})
    monkeypatch.setattr(controller, "_mail_lock_status", lambda: {"locked": False})
    monkeypatch.setattr(controller, "_restore_mail_timer", lambda _: {"ok": True})
    return lambda: controller._recover_mail_production_gate(spec)


@pytest.mark.parametrize("caller", ["worker", "controller"])
def test_same_fingerprint_keeps_cooldown_after_real_failed_dry_run_stamp(
    caller, monkeypatch, tmp_path, gate_fixture,
):
    config_path, production_check, failed_dry_run = gate_fixture
    first = json.loads(production_check().stdout)
    dry_run_attempts = []

    def capture(command, **_):
        if command[-1] == "production-check":
            return production_check()
        assert "--dry-run" in command
        dry_run_attempts.append(command)
        return failed_dry_run()

    run = _invoke(caller, monkeypatch, tmp_path, capture)
    run()
    after = json.loads(production_check().stdout)
    assert first["auto_recoverable"] and after["auto_recoverable"]
    assert first["blockers"] != after["blockers"]
    for field in ("last_dry_run_ok", "last_dry_run_at", "stored_fingerprint"):
        assert first["gate"][field] != after["gate"][field]
    assert first["gate"]["current_fingerprint"] == after["gate"]["current_fingerprint"]

    retry = run()
    assert len(dry_run_attempts) == 1
    assert retry == 4 if caller == "worker" else retry["cooldown"] is True

    config_path.write_text("# different verified fingerprint\n", encoding="utf-8")
    run()
    assert len(dry_run_attempts) == 2


@pytest.mark.parametrize("caller", ["worker", "controller"])
def test_recovery_rejects_readiness_without_verified_fingerprint(caller, monkeypatch, tmp_path):
    readiness = {"ok": False, "auto_recoverable": True, "gate": {"current_fingerprint": "invalid"}}
    calls = []

    def capture(command, **_):
        calls.append(command)
        return subprocess.CompletedProcess([], 4, json.dumps(readiness), "")

    result = _invoke(caller, monkeypatch, tmp_path, capture)()
    assert len(calls) == 1
    assert result == 4 if caller == "worker" else result["attempted"] is False
