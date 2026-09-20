from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping
from datetime import UTC, datetime
from importlib.resources import files
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BUDGETS = ROOT / "docs/architecture/runtime-capacity-budgets.json"
PACKAGED_BUDGETS = files("personal_assistant").joinpath("runtime_capacity_budgets.json")


def _as_int(value: object, default: int = 0) -> int:
    if not isinstance(value, (str, bytes, bytearray, int, float)):
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _integer(path: Path) -> int | None:
    try:
        value = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not value or value == "max":
        return None
    try:
        return int(value)
    except ValueError:
        return None


def _key_values(path: Path) -> dict[str, int]:
    values: dict[str, int] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return values
    for line in lines:
        fields = line.split()
        if len(fields) < 2:
            continue
        try:
            values[fields[0]] = int(fields[1])
        except ValueError:
            continue
    return values


def _io_values(path: Path) -> dict[str, int]:
    total = {"read_bytes": 0, "write_bytes": 0, "read_operations": 0, "write_operations": 0}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return total
    aliases = {
        "rbytes": "read_bytes",
        "wbytes": "write_bytes",
        "rios": "read_operations",
        "wios": "write_operations",
    }
    for line in lines:
        for field in line.split()[1:]:
            key, separator, raw = field.partition("=")
            target = aliases.get(key)
            if not separator or target is None:
                continue
            try:
                total[target] += int(raw)
            except ValueError:
                continue
    return total


def _meminfo(path: Path) -> dict[str, int]:
    values: dict[str, int] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return values
    for line in lines:
        key, separator, raw = line.partition(":")
        if not separator:
            continue
        match = re.match(r"\s*(\d+)", raw)
        if match:
            values[key] = int(match.group(1)) * 1024
    return values


def load_budgets(path: Path = DEFAULT_BUDGETS) -> dict[str, Any]:
    if path == DEFAULT_BUDGETS and not path.is_file():
        payload = json.loads(PACKAGED_BUDGETS.read_text(encoding="utf-8"))
    else:
        payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not isinstance(payload.get("roles"), dict):
        raise ValueError("Runtime-Kapazitaetsbudget ist ungueltig")
    return payload


def classify_exit(evidence: Mapping[str, object]) -> dict[str, object]:
    """Classify one lifecycle without inferring one failure class from another.

    Docker's OOMKilled bit, cgroup memory events and a child return code describe
    different scopes.  In particular, exit 137 alone is not proof of an OOM.
    """

    running = bool(evidence.get("running"))
    container_oom = bool(evidence.get("container_oom_killed"))
    manual_stop = bool(evidence.get("manual_stop"))
    health = str(evidence.get("health_status") or "").casefold()
    exit_code = _as_int(evidence.get("exit_code"))
    child_exit = evidence.get("child_exit_code")
    child_signal = str(evidence.get("child_signal") or "").upper()
    oom_delta = max(0, _as_int(evidence.get("cgroup_oom_kill_delta")))

    if manual_stop:
        cause = "manual-stop"
        scope = "operator"
    elif container_oom and running:
        cause = "historical-container-oom"
        scope = "container-history"
    elif container_oom:
        cause = "container-oom"
        scope = "container"
    elif oom_delta > 0 and (child_exit == 137 or child_signal == "SIGKILL"):
        cause = "child-oom"
        scope = "child-process"
    elif health == "unhealthy":
        cause = "health-failure"
        scope = "healthcheck"
    elif not running and exit_code == 0:
        cause = "normal-exit"
        scope = "container"
    elif not running:
        cause = "process-failure"
        scope = "container"
    else:
        cause = "running"
        scope = "container"
    return {
        "cause": cause,
        "scope": scope,
        "container_oom_killed": container_oom,
        "cgroup_oom_kill_delta": oom_delta,
        "health_status": health or "unknown",
        "exit_code": exit_code,
        "child_exit_code": child_exit,
        "child_signal": child_signal,
        "inference_limited": cause in {"historical-container-oom", "process-failure"},
    }


def current_runtime_report(
    *,
    cgroup_root: Path = Path("/sys/fs/cgroup"),
    proc_root: Path = Path("/proc"),
    role: str | None = None,
    budgets_path: Path = DEFAULT_BUDGETS,
) -> dict[str, Any]:
    role_name = (role or os.environ.get("OPENCLAW_ROLE") or "standalone").strip()
    budgets = load_budgets(budgets_path)
    budget = dict(budgets["roles"].get(role_name) or {})
    cpu = _key_values(cgroup_root / "cpu.stat")
    events = _key_values(cgroup_root / "memory.events")
    host = _meminfo(proc_root / "meminfo")
    memory_current = _integer(cgroup_root / "memory.current")
    memory_peak = _integer(cgroup_root / "memory.peak")
    memory_limit = _integer(cgroup_root / "memory.max")
    pids_current = _integer(cgroup_root / "pids.current")
    pids_limit = _integer(cgroup_root / "pids.max")
    swap_current = _integer(cgroup_root / "memory.swap.current")
    usage = memory_peak if memory_peak is not None else memory_current
    within_memory = None
    if usage is not None and budget.get("memory_limit_bytes") is not None:
        within_memory = usage <= int(budget["memory_limit_bytes"])
    within_pids = None
    if pids_current is not None and budget.get("pids_limit") is not None:
        within_pids = pids_current <= int(budget["pids_limit"])
    complete = all(
        value is not None
        for value in (memory_current, memory_peak, memory_limit, pids_current, pids_limit)
    ) and bool(budget)
    return {
        "ok": within_memory is not False and within_pids is not False,
        "complete": complete,
        "measurement_status": "complete" if complete else "partial",
        "schema_version": 1,
        "measured_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "role": role_name,
        "source": "current-cgroup-and-proc",
        "container": {
            "memory_current_bytes": memory_current,
            "memory_working_set_bytes": memory_current,
            "memory_peak_bytes": memory_peak,
            "memory_limit_bytes": memory_limit,
            "swap_current_bytes": swap_current,
            "cpu_usage_usec": cpu.get("usage_usec"),
            "cpu_user_usec": cpu.get("user_usec"),
            "cpu_system_usec": cpu.get("system_usec"),
            "io": _io_values(cgroup_root / "io.stat"),
            "pids_current": pids_current,
            "pids_limit": pids_limit,
            "memory_events": events,
        },
        "host": {
            "memory_total_bytes": host.get("MemTotal"),
            "memory_available_bytes": host.get("MemAvailable"),
            "swap_total_bytes": host.get("SwapTotal"),
            "swap_free_bytes": host.get("SwapFree"),
            "agent_container_memory_is_subset": True,
            "foreign_load_not_attributed_to_agent": True,
        },
        "budget": {
            **budget,
            "memory_within_budget": within_memory,
            "pids_within_budget": within_pids,
            "limits_changed": False,
        },
        "not_measured": [
            "container_restart_count",
            "container_startup_ms",
            "healthcheck_latency_ms",
            "shutdown_and_lease_release_ms",
            "kernel_oom_log",
        ],
    }


def role_observation(
    *,
    role: str,
    inspect: Mapping[str, object],
    stats: Mapping[str, object],
    cgroup: Mapping[str, object],
    budget: Mapping[str, object],
) -> dict[str, Any]:
    state_value = inspect.get("State")
    state: Mapping[str, object] = state_value if isinstance(state_value, Mapping) else {}
    host_config_value = inspect.get("HostConfig")
    host_config: Mapping[str, object] = (
        host_config_value if isinstance(host_config_value, Mapping) else {}
    )
    health_value = state.get("Health")
    health: Mapping[str, object] = health_value if isinstance(health_value, Mapping) else {}
    memory_peak = cgroup.get("memory_peak_bytes")
    memory_limit = _as_int(host_config.get("Memory")) or None
    configured_limit = _as_int(budget.get("memory_limit_bytes")) or None
    restart_count = _as_int(inspect.get("RestartCount"))
    classification = classify_exit(
        {
            "running": bool(state.get("Running")),
            "container_oom_killed": bool(state.get("OOMKilled")),
            "exit_code": _as_int(state.get("ExitCode")),
            "health_status": health.get("Status") or "",
            "cgroup_oom_kill_delta": cgroup.get("oom_kill_delta") or 0,
            "child_exit_code": cgroup.get("child_exit_code"),
            "child_signal": cgroup.get("child_signal") or "",
            "manual_stop": bool(cgroup.get("manual_stop")),
        }
    )
    memory_ok = (
        None
        if memory_peak is None or configured_limit is None
        else _as_int(memory_peak) <= configured_limit
    )
    limit_matches = (
        None
        if memory_limit is None or configured_limit is None
        else memory_limit == configured_limit
    )
    return {
        "role": role,
        "container_name": str(inspect.get("Name") or "").lstrip("/"),
        "running": bool(state.get("Running")),
        "health": str(health.get("Status") or "none"),
        "restarts": restart_count,
        "started_at": str(state.get("StartedAt") or ""),
        "finished_at": str(state.get("FinishedAt") or ""),
        "exit": classification,
        "memory": {
            "limit_bytes": memory_limit,
            "working_set_bytes": stats.get("memory_working_set_bytes"),
            "peak_bytes": memory_peak,
            "within_budget": memory_ok,
            "limit_matches_contract": limit_matches,
        },
        "cpu": {
            "limit_nano_cpus": host_config.get("NanoCpus"),
            "usage_nanoseconds": stats.get("cpu_usage_nanoseconds"),
        },
        "io": {
            "read_bytes": stats.get("read_bytes"),
            "write_bytes": stats.get("write_bytes"),
        },
        "pids": {
            "limit": host_config.get("PidsLimit"),
            "current": stats.get("pids_current"),
            "peak": cgroup.get("pids_peak"),
        },
        "timing": {
            "startup_ms": cgroup.get("startup_ms"),
            "healthcheck_latency_ms": cgroup.get("healthcheck_latency_ms"),
            "shutdown_and_lease_release_ms": cgroup.get("shutdown_and_lease_release_ms"),
        },
        "budget": dict(budget),
    }


def build_capacity_report(
    observations: list[dict[str, Any]],
    *,
    host: Mapping[str, object] | None = None,
) -> dict[str, Any]:
    blockers: list[dict[str, str]] = []
    not_measured: list[dict[str, str]] = []
    for item in observations:
        role = str(item.get("role") or "unknown")
        if item.get("measurement_status") == "not-measured":
            not_measured.append({"role": role, "reason": str(item.get("reason") or "inactive")})
            continue
        if item.get("blocker"):
            blockers.append({"role": role, "code": "observation-unavailable"})
            continue
        memory_value = item.get("memory")
        memory: Mapping[str, object] = (
            memory_value if isinstance(memory_value, Mapping) else {}
        )
        if memory.get("within_budget") is False:
            blockers.append({"role": role, "code": "memory-budget-exceeded"})
        if memory.get("limit_matches_contract") is False:
            blockers.append({"role": role, "code": "memory-limit-drift"})
        exit_value = item.get("exit")
        exit_evidence: Mapping[str, object] = (
            exit_value if isinstance(exit_value, Mapping) else {}
        )
        if exit_evidence.get("cause") in {"container-oom", "child-oom"}:
            blockers.append({"role": role, "code": str(exit_evidence["cause"])})
    return {
        "ok": not blockers,
        "complete": not not_measured,
        "schema_version": 1,
        "measured_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "roles": observations,
        "host": dict(host or {}),
        "blockers": blockers,
        "not_measured": not_measured,
        "interpretation": {
            "host_swap_is_not_container_swap": True,
            "foreign_load_is_not_agent_load": True,
            "historical_oom_is_not_current_oom": True,
            "health_failure_is_not_oom": True,
        },
    }
