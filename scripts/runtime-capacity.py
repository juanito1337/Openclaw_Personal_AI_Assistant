#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from personal_assistant.runtime_capacity import (  # noqa: E402
    build_capacity_report,
    load_budgets,
    role_observation,
)

CONTAINER_PREFIX = "openclaw-"


def _bytes(value: object) -> int | None:
    text = str(value or "").strip()
    match = re.match(r"^([0-9.]+)\s*([kmgt]?i?b)$", text, re.IGNORECASE)
    if not match:
        return None
    factors = {
        "b": 1,
        "kb": 1000,
        "kib": 1024,
        "mb": 1000**2,
        "mib": 1024**2,
        "gb": 1000**3,
        "gib": 1024**3,
        "tb": 1000**4,
        "tib": 1024**4,
    }
    return int(float(match.group(1)) * factors[match.group(2).casefold()])


def _host_metrics(agent_working_set_bytes: int) -> dict[str, Any]:
    values: dict[str, int] = {}
    try:
        lines = Path("/proc/meminfo").read_text(encoding="utf-8").splitlines()
    except OSError:
        lines = []
    for line in lines:
        key, separator, raw = line.partition(":")
        match = re.match(r"\s*(\d+)", raw)
        if separator and match:
            values[key] = int(match.group(1)) * 1024
    host_used = max(0, values.get("MemTotal", 0) - values.get("MemAvailable", 0))
    return {
        "memory_total_bytes": values.get("MemTotal"),
        "memory_available_bytes": values.get("MemAvailable"),
        "host_used_bytes": host_used,
        "agent_working_set_bytes": agent_working_set_bytes,
        "host_used_not_attributed_to_agent_bytes": max(0, host_used - agent_working_set_bytes),
        "swap_total_bytes": values.get("SwapTotal"),
        "swap_free_bytes": values.get("SwapFree"),
        "foreign_load_not_attributed_to_agent": True,
    }


def _run(command: list[str], *, timeout: int = 30) -> str:
    completed = subprocess.run(command, check=False, capture_output=True, text=True, timeout=timeout)
    if completed.returncode != 0:
        raise RuntimeError((completed.stderr or completed.stdout).strip()[:2000])
    return completed.stdout


def _inspect(name: str) -> dict[str, Any]:
    payload = json.loads(_run(["docker", "inspect", name]))
    if not isinstance(payload, list) or len(payload) != 1 or not isinstance(payload[0], dict):
        raise RuntimeError(f"Docker-Inspect fuer {name} war nicht eindeutig")
    return payload[0]


def _stats(name: str) -> dict[str, Any]:
    raw = _run(["docker", "stats", "--no-stream", "--format", "{{json .}}", name])
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise RuntimeError(f"Docker-Stats fuer {name} war ungueltig")
    memory_text = str(payload.get("MemUsage") or "").split("/", 1)[0].strip()
    block_parts = [item.strip() for item in str(payload.get("BlockIO") or "").split("/")]
    return {
        "memory_working_set_bytes": _bytes(memory_text),
        "cpu_usage_nanoseconds": None,
        "read_bytes": _bytes(block_parts[0]) if block_parts else None,
        "write_bytes": _bytes(block_parts[1]) if len(block_parts) > 1 else None,
        "pids_current": int(payload.get("PIDs") or 0),
        "docker_stats": {
            "memory": str(payload.get("MemUsage") or ""),
            "cpu": str(payload.get("CPUPerc") or ""),
            "block_io": str(payload.get("BlockIO") or ""),
        },
    }


def _cgroup(name: str) -> dict[str, Any]:
    script = """
import json
from pathlib import Path
def integer(name):
    try:
        value=Path('/sys/fs/cgroup',name).read_text().strip()
        return None if value == 'max' else int(value)
    except (OSError,ValueError): return None
events={}
try:
    for line in Path('/sys/fs/cgroup/memory.events').read_text().splitlines():
        key,value=line.split(); events[key]=int(value)
except (OSError,ValueError): pass
print(json.dumps({'memory_peak_bytes':integer('memory.peak'),'pids_peak':integer('pids.peak'),'oom_kill_delta':0,'memory_events':events}))
""".strip()
    raw = _run(["docker", "exec", name, "python3", "-c", script])
    payload = json.loads(raw)
    return payload if isinstance(payload, dict) else {}


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only M16 runtime capacity collector")
    parser.add_argument("--output", type=Path, default=ROOT / "build/runtime-capacity.json")
    parser.add_argument("--fixture", type=Path, help="Hermetische Eingabe statt Docker verwenden")
    args = parser.parse_args()
    budgets = load_budgets()
    if args.fixture:
        fixture = json.loads(args.fixture.read_text(encoding="utf-8"))
        inputs = fixture.get("roles", [])
        host = fixture.get("host", {})
    else:
        inputs = []
        for role, budget in budgets["roles"].items():
            name = CONTAINER_PREFIX + str(budget["container_suffix"])
            try:
                inputs.append(
                    {
                        "role": role,
                        "inspect": _inspect(name),
                        "stats": _stats(name),
                        "cgroup": _cgroup(name),
                    }
                )
            except RuntimeError as exc:
                inputs.append({"role": role, "error": str(exc)})
        agent_working_set = sum(
            int(item.get("stats", {}).get("memory_working_set_bytes") or 0)
            for item in inputs
            if isinstance(item.get("stats"), dict)
        )
        host = _host_metrics(agent_working_set)
    observations = []
    for item in inputs:
        role = str(item.get("role") or "")
        budget = budgets["roles"].get(role, {})
        if item.get("error"):
            if budget.get("kind") in {"one-shot", "tool"}:
                observations.append(
                    {
                        "role": role,
                        "measurement_status": "not-measured",
                        "reason": "inactive-ephemeral-role",
                        "detail": str(item["error"]),
                        "budget": budget,
                    }
                )
            else:
                observations.append(
                    {"role": role, "ok": False, "blocker": str(item["error"]), "budget": budget}
                )
            continue
        observations.append(
            role_observation(
                role=role,
                inspect=item.get("inspect", {}),
                stats=item.get("stats", {}),
                cgroup=item.get("cgroup", {}),
                budget=budget,
            )
        )
    report = build_capacity_report(observations, host=host)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
