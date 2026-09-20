from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from mail_agent.telemetry import PerformanceTelemetry, summarize_performance
from personal_assistant.config import AssistantConfig, RuntimeConfig, SearchConfig
from personal_assistant.job_control import CommandResult, JobController
from personal_assistant.monitoring import PerformanceMonitor
from personal_assistant.run_contract import (
    RUN_RESULTS,
    RunIdentity,
    canonical_run_result,
    result_from_exit_code,
)
from personal_assistant.storage import AssistantStorage
from personal_assistant.work_scheduler import AdaptiveWorkScheduler


class MutableClock:
    def __init__(self) -> None:
        self.value = datetime(2026, 9, 20, 10, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.value

    def advance(self, seconds: int) -> None:
        self.value += timedelta(seconds=seconds)


def test_closed_result_vocabulary_and_identity_reject_unbounded_values() -> None:
    assert RUN_RESULTS == (
        "completed",
        "degraded",
        "interrupted",
        "skipped-not-due",
        "in-progress",
        "blocked",
        "failed",
    )
    assert canonical_run_result("success", allow_legacy=True) == "completed"
    assert result_from_exit_code(0) == "completed"
    assert result_from_exit_code(1) == "degraded"
    assert result_from_exit_code(2) == "failed"
    assert result_from_exit_code(0, interrupted=True) == "interrupted"
    identity = RunIdentity.create(job_id="mail-index", parent_run_id="parent-1")
    assert identity.run_id
    assert identity.attempt_id
    assert identity.parent_run_id == "parent-1"
    with pytest.raises(ValueError):
        RunIdentity.create(job_id="contains a user subject")
    with pytest.raises(ValueError):
        canonical_run_result("mostly-fine")


def test_scheduler_retry_preserves_run_and_records_interrupted_attempt(tmp_path: Path) -> None:
    clock = MutableClock()
    scheduler = AdaptiveWorkScheduler(
        tmp_path / "scheduler.sqlite3",
        now=clock,
        lease_seconds=30,
        arbitration_seconds=0,
    )
    try:
        ticket = scheduler.enqueue(
            "mail",
            owner="worker-a",
            parent_run_id="supervisor-run",
        )
        first = scheduler.claim(ticket, owner="worker-a")
        assert first.granted
        assert first.run_id == ticket
        assert first.parent_run_id == "supervisor-run"

        clock.advance(31)
        second = scheduler.claim(ticket, owner="worker-b")
        assert second.granted
        assert second.run_id == first.run_id
        assert second.attempt_id != first.attempt_id
        assert scheduler.finish(
            second.lease_token,
            owner="worker-b",
            result="completed",
            exit_code=0,
        )

        attempts = sorted(
            scheduler.snapshot()["recent_attempts"],
            key=lambda item: item["attempt_number"],
        )
        assert [item["result"] for item in attempts] == [
            "interrupted",
            "completed",
        ]
        assert {item["run_id"] for item in attempts} == {ticket}
    finally:
        scheduler.close()


def test_parent_child_telemetry_is_one_logical_run_and_partial_failure_is_visible() -> None:
    records = [
        {
            "run_id": "root",
            "attempt_id": "root-a1",
            "job_id": "mail",
            "result": "degraded",
            "operation": "drain",
            "processed": 5,
            "error_count": 1,
            "total_ms": 100,
        },
        {
            "run_id": "child",
            "parent_run_id": "root",
            "attempt_id": "child-a1",
            "job_id": "mail-index",
            "result": "failed",
            "operation": "index",
            "processed": 0,
            "error_count": 1,
            "total_ms": 80,
        },
    ]
    summary = summarize_performance(records)
    assert summary["runs"] == 1
    assert summary["results"] == {"degraded": 1}
    assert summary["errors"] == 1


def test_live_checkpoint_is_not_history_and_crash_becomes_interrupted(
    tmp_path: Path,
) -> None:
    path = tmp_path / "performance.jsonl"
    telemetry = PerformanceTelemetry(path)
    telemetry.reset("mail")
    checkpoint = json.loads(telemetry.inflight_path.read_text(encoding="utf-8"))
    assert checkpoint["result"] == "in-progress"
    assert not path.exists()

    checkpoint["pid"] = 99999999
    checkpoint["proc_start_ticks"] = "dead"
    telemetry.inflight_path.write_text(json.dumps(checkpoint), encoding="utf-8")
    resumed = PerformanceTelemetry(path)
    resumed.reset("mail")
    record = json.loads(path.read_text(encoding="utf-8"))
    assert record["result"] == "interrupted"
    assert record["run_id"] == checkpoint["run_id"]
    assert resumed.run_id != checkpoint["run_id"]


def test_nextcloud_noop_refreshes_check_without_inventing_data_change(
    tmp_path: Path,
) -> None:
    database = tmp_path / "assistant.sqlite3"
    storage = AssistantStorage(database)
    config = AssistantConfig(
        runtime=RuntimeConfig(
            database=database,
            log_file=tmp_path / "assistant.log",
            resources_file=tmp_path / "resources.toml",
            policies_file=tmp_path / "policies.toml",
            secrets_file=tmp_path / "secrets.env",
        ),
        search=SearchConfig(mail_snapshot_dir=tmp_path / "mail"),
        path=tmp_path / "config.toml",
    )
    monitor = PerformanceMonitor(
        config,
        storage,
        type("Registry", (), {"resources": {}, "duplicate_ids": []})(),
        live_health=lambda: {"ok": True, "dav_status": 207},
        monitor_database=tmp_path / "monitor.sqlite3",
    )
    try:
        storage.set_sync_state(
            "nextcloud-files-main",
            "files",
            status="ok",
            detail='{"indexed": 1}',
            data_changed=True,
        )
        first = json.loads(
            storage.get_sync_state("nextcloud-files-main", "files")["detail"]
        )["runtime"]
        storage.set_sync_state(
            "nextcloud-files-main",
            "files",
            status="ok",
            detail='{"indexed": 0, "unchanged": 5}',
            data_changed=False,
        )
        second = json.loads(
            storage.get_sync_state("nextcloud-files-main", "files")["detail"]
        )["runtime"]
        assert second["result"] == "completed"
        assert second["last_data_change_at"] == first["last_data_change_at"]
        assert second["last_successful_check_at"] >= first["last_successful_check_at"]
        report = monitor.report(days=7, live=True)
        state = report["metrics"]["assistant"]["sync_state"][0]
        assert state["result"] == "completed"
        assert state["data_changed"] is False
        assert state["age_hours"] == 0.0

        storage.set_sync_state(
            "nextcloud-files-main",
            "files",
            status="partial",
            detail='{"errors": 1}',
            data_changed=None,
        )
        degraded = monitor.report(days=7, live=True)
        state = degraded["metrics"]["assistant"]["sync_state"][0]
        assert state["result"] == "degraded"
        component = next(
            item for item in degraded["components"] if item["id"] == "nextcloud"
        )
        assert component["evidence"]["sync_failed_or_partial"] == 1
    finally:
        monitor.close()
        storage.close()


def test_alert_expires_without_becoming_a_current_failure(tmp_path: Path) -> None:
    controller = JobController(
        state_path=tmp_path / "job-control.json",
        workspace_root=tmp_path,
        runner=lambda _command, _timeout: CommandResult(0, "{}", ""),
    )
    old = datetime(2026, 9, 18, 10, 0, tzinfo=UTC)
    controller.state["active_alerts"] = {
        "mail:service-degraded": {
            "id": "mail:service-degraded",
            "job": "mail",
            "code": "service-degraded",
            "detail": "old cause",
            "first_seen": old.isoformat(),
            "last_seen": old.isoformat(),
            "expires_at": (old + timedelta(hours=24)).isoformat(),
        }
    }
    report = {
        "checked_at": datetime(2026, 9, 20, 10, 0, tzinfo=UTC).isoformat(),
        "jobs": [],
    }
    controller._record(report)
    assert report["active_alerts"] == []
    assert report["new_alerts"] == []
    assert report["resolved_alerts"][0]["resolution"] == "expired-without-refresh"
