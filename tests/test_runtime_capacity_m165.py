from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from mail_agent.telemetry import PerformanceTelemetry, latency_decomposition
from personal_assistant.cli import parser
from personal_assistant.ollama_priority_proxy import ProxyStats
from personal_assistant.runtime_capacity import (
    DEFAULT_BUDGETS,
    build_capacity_report,
    classify_exit,
    current_runtime_report,
    load_budgets,
    role_observation,
)

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    ("evidence", "cause"),
    [
        ({"container_oom_killed": True, "running": False, "exit_code": 137}, "container-oom"),
        (
            {
                "container_oom_killed": True,
                "running": True,
                "exit_code": 0,
                "health_status": "healthy",
            },
            "historical-container-oom",
        ),
        (
            {
                "running": True,
                "child_exit_code": 137,
                "cgroup_oom_kill_delta": 1,
                "health_status": "healthy",
            },
            "child-oom",
        ),
        ({"running": True, "health_status": "unhealthy", "exit_code": 0}, "health-failure"),
        ({"running": False, "exit_code": 0}, "normal-exit"),
        ({"running": False, "exit_code": 143, "manual_stop": True}, "manual-stop"),
    ],
)
def test_exit_causes_remain_distinct(evidence: dict[str, object], cause: str) -> None:
    assert classify_exit(evidence)["cause"] == cause


def test_sigkill_without_cgroup_evidence_is_not_reported_as_child_oom() -> None:
    result = classify_exit({"running": True, "child_exit_code": 137, "cgroup_oom_kill_delta": 0})
    assert result["cause"] == "running"


def test_current_runtime_report_separates_container_and_host_memory(tmp_path: Path) -> None:
    cgroup = tmp_path / "cgroup"
    proc = tmp_path / "proc"
    cgroup.mkdir()
    proc.mkdir()
    (cgroup / "memory.current").write_text("100\n", encoding="utf-8")
    (cgroup / "memory.peak").write_text("200\n", encoding="utf-8")
    (cgroup / "memory.max").write_text("1073741824\n", encoding="utf-8")
    (cgroup / "memory.swap.current").write_text("5\n", encoding="utf-8")
    (cgroup / "pids.current").write_text("3\n", encoding="utf-8")
    (cgroup / "pids.max").write_text("192\n", encoding="utf-8")
    (cgroup / "memory.events").write_text("oom 2\noom_kill 1\n", encoding="utf-8")
    (cgroup / "cpu.stat").write_text("usage_usec 42\nuser_usec 30\nsystem_usec 12\n", encoding="utf-8")
    (cgroup / "io.stat").write_text("8:0 rbytes=10 wbytes=20 rios=1 wios=2\n", encoding="utf-8")
    (proc / "meminfo").write_text(
        "MemTotal: 1000 kB\nMemAvailable: 400 kB\nSwapTotal: 200 kB\nSwapFree: 150 kB\n",
        encoding="utf-8",
    )
    report = current_runtime_report(
        cgroup_root=cgroup,
        proc_root=proc,
        role="sync-worker",
    )
    assert report["ok"] is True
    assert report["container"]["memory_peak_bytes"] == 200
    assert report["container"]["io"]["write_bytes"] == 20
    assert report["host"]["swap_total_bytes"] == 204800
    assert report["host"]["foreign_load_not_attributed_to_agent"] is True
    assert report["budget"]["limits_changed"] is False


def test_role_observation_does_not_turn_historical_oom_into_current_failure() -> None:
    budget = {"memory_limit_bytes": 1024, "pids_limit": 10}
    observation = role_observation(
        role="supervisor-worker",
        inspect={
            "Name": "/openclaw-supervisor-worker",
            "RestartCount": 2,
            "State": {
                "Running": True,
                "OOMKilled": True,
                "ExitCode": 0,
                "Health": {"Status": "healthy"},
            },
            "HostConfig": {"Memory": 1024, "PidsLimit": 10, "NanoCpus": 500000000},
        },
        stats={"memory_working_set_bytes": 400, "pids_current": 3},
        cgroup={"memory_peak_bytes": 800, "oom_kill_delta": 0},
        budget=budget,
    )
    assert observation["exit"]["cause"] == "historical-container-oom"
    assert observation["memory"]["within_budget"] is True
    assert build_capacity_report([observation])["ok"] is True


def test_budget_overrun_is_a_blocker_without_changing_limit() -> None:
    observation = role_observation(
        role="sync-worker",
        inspect={
            "Name": "/openclaw-sync-worker",
            "State": {"Running": True, "OOMKilled": False, "Health": {"Status": "healthy"}},
            "HostConfig": {"Memory": 100, "PidsLimit": 10},
        },
        stats={},
        cgroup={"memory_peak_bytes": 101},
        budget={"memory_limit_bytes": 100, "pids_limit": 10},
    )
    report = build_capacity_report([observation])
    assert report["ok"] is False
    assert report["blockers"] == [{"role": "sync-worker", "code": "memory-budget-exceeded"}]


def test_inactive_ephemeral_role_is_explicitly_not_measured() -> None:
    report = build_capacity_report(
        [
            {
                "role": "agent-cli",
                "measurement_status": "not-measured",
                "reason": "inactive-ephemeral-role",
            }
        ]
    )
    assert report["ok"] is True
    assert report["complete"] is False
    assert report["not_measured"] == [
        {"role": "agent-cli", "reason": "inactive-ephemeral-role"}
    ]


def test_capacity_budgets_match_existing_hardening_contract() -> None:
    budgets = load_budgets(DEFAULT_BUDGETS)["roles"]
    hardening = json.loads(
        (ROOT / "docs/architecture/runtime-hardening.json").read_text(encoding="utf-8")
    )["roles"]
    assert set(budgets) == set(hardening)
    for role, expected in hardening.items():
        assert budgets[role]["memory_limit_bytes"] == expected["memory"]
        assert budgets[role]["pids_limit"] == expected["pids"]
        assert budgets[role]["cpus"] == expected["cpus"]


def test_runtime_performance_cli_is_read_only_and_configuration_free() -> None:
    args = parser().parse_args(["performance", "runtime"])
    assert args.performance_command == "runtime"


def test_latency_components_reconcile_with_turn_walltime() -> None:
    result = latency_decomposition(
        total_ms=1000,
        phases={
            "ollama.prompt_preparation": {"total_ms": 20},
            "ollama.response_decode": {"total_ms": 10},
            "ollama.response_finalization": {"total_ms": 10},
        },
        external_commands={"tool.read": {"total_ms": 100}},
        ollama={
            "client_duration_ms": 600,
            "queue_wait_ms": 50,
            "server_total_duration_ms": 500,
        },
    )
    assert result["client_transport_ms"] == 30
    assert result["upstream_inference_ms"] == 500
    assert result["reconciled_ms"] == 1000
    assert result["consistent"] is True


def test_performance_record_persists_reconciled_latency(tmp_path: Path) -> None:
    telemetry = PerformanceTelemetry(tmp_path / "performance.jsonl")
    telemetry.reset("large-result")
    telemetry.record_phase("ollama.prompt_preparation", 2)
    telemetry.record_phase("ollama.response_finalization", 3)
    telemetry.finish(processed=1, skipped=0, errors=[], classifier={})
    record = json.loads((tmp_path / "performance.jsonl").read_text(encoding="utf-8"))
    assert record["latency"]["consistent"] is True
    assert record["latency"]["turn_latency_ms"] == record["latency"]["reconciled_ms"]


def test_proxy_stats_separate_queue_and_upstream_latency() -> None:
    stats = ProxyStats()
    stats.record_start(scheduled=True, priority="interactive")
    stats.record_grant(7, active_count=1, background_active=0)
    stats.record_upstream(80, request_bytes=100, response_bytes=200)
    stats.record_finish(ok=True)
    snapshot = stats.snapshot()
    assert snapshot["queue_wait_average_ms"] == 7
    assert snapshot["upstream_duration_average_ms"] == 80
    assert snapshot["request_body_bytes"] == 100
    assert snapshot["response_body_bytes"] == 200


def test_large_tool_payload_is_projected_with_digest_and_completeness_loss() -> None:
    script = """
import { projectPayload } from './docker/openclaw-personal-assistant-plugin/runtime.js';
const source={ok:true,complete:true,coverage:{ratio:1},results:Array.from(
  {length:500},(_,i)=>({id:i,text:'x'.repeat(1000)}),
)};
const projected=projectPayload(source,{max_projected_bytes:12000,max_projected_rows:40}).payload;
console.log(JSON.stringify(projected));
"""
    result = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(result.stdout)
    assert payload["complete"] is False
    assert payload["results_may_be_truncated"] is True
    assert 0 < len(payload["results"]) <= 40
    assert payload["projection"]["original_rows"] == 500
    assert len(payload["projection"]["original_sha256"]) == 64
    assert len(result.stdout.encode()) < 12000


def test_tool_capture_limit_returns_typed_error_without_partial_json() -> None:
    script = """
    import { makeEvidence, spawnJson } from './docker/openclaw-personal-assistant-plugin/runtime.js';
const invocation={
  executable:process.execPath,
  argv:['-e',"process.stdout.write(JSON.stringify({ok:true,data:'x'.repeat(5000)}))"],
  env:{},cwd:process.cwd(),stdin:null,
};
    const result=await spawnJson(
      invocation,
      {tool_timeout_seconds:10,max_capture_bytes:1000,max_error_bytes:1000},
      {cwd:process.cwd()},
    );
    const evidence=makeEvidence(
      {tool_id:'fixture.large',domain:'fixture',mode:'read',approval:'none'},
      result,
      null,
      'turn',
      'call',
    );
    console.log(JSON.stringify({result,evidence}));
"""
    completed = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(completed.stdout)
    assert payload["evidence"]["error"] == "output-limit"
    assert payload["result"]["stdout_truncated"] is True
    assert payload["result"]["stdout_bytes"] > 5000
    assert len(payload["result"]["stdout_sha256"]) == 64


def test_operator_collector_accepts_hermetic_cold_warm_health_and_shutdown_fixture(tmp_path: Path) -> None:
    fixture = {
        "host": {"swap_total_bytes": 0, "foreign_memory_bytes": 123},
        "roles": [
            {
                "role": "sync-worker",
                "inspect": {
                    "Name": "/openclaw-sync-worker",
                    "RestartCount": 0,
                    "State": {"Running": True, "OOMKilled": False, "Health": {"Status": "healthy"}},
                    "HostConfig": {"Memory": 1073741824, "PidsLimit": 192, "NanoCpus": 1000000000},
                },
                "stats": {"memory_working_set_bytes": 200, "pids_current": 4},
                "cgroup": {
                    "memory_peak_bytes": 400,
                    "startup_ms": 900,
                    "healthcheck_latency_ms": 12,
                    "shutdown_and_lease_release_ms": 40,
                },
            }
        ],
    }
    fixture_path = tmp_path / "fixture.json"
    output = tmp_path / "report.json"
    fixture_path.write_text(json.dumps(fixture), encoding="utf-8")
    completed = subprocess.run(
        [str(ROOT / "scripts/runtime-capacity.py"), "--fixture", str(fixture_path), "--output", str(output)],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["roles"][0]["timing"]["startup_ms"] == 900
    assert report["roles"][0]["timing"]["healthcheck_latency_ms"] == 12
    assert report["roles"][0]["timing"]["shutdown_and_lease_release_ms"] == 40
