#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from personal_assistant.agent_tool_orchestration import guard_claims, route_intent  # noqa: E402

DEFAULT_CORPUS = ROOT / "tests/fixtures/m13/tool-use-corpus.json"


def load_corpus(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("privacy") != "synthetic-only" or not isinstance(payload.get("cases"), list):
        raise ValueError("M13-Korpus ist nicht als synthetischer Fixture-Vertrag markiert")
    return payload


def replay_case(case: dict[str, Any]) -> dict[str, Any]:
    started = time.perf_counter()
    route = route_intent(str(case["prompt"]))
    domains = [item["domain"] for item in route["routes"]]
    expected_domains = list(case["expected_domains"])
    routed = all(domain in domains for domain in expected_domains)
    operation_sets = [set(item["operations"]) for item in route["routes"]]
    first_tool_possible = any(
        set(case["allowed_first_operations"]) & operations for operations in operation_sets
    )
    guard = None
    if case.get("scripted_answer") is not None:
        guard = guard_claims(
            route=route,
            answer=str(case["scripted_answer"]),
            evidence=list(case.get("scripted_evidence") or []),
        )
    expected_guard = case.get("expected_guard")
    guard_ok = expected_guard is None or (expected_guard == "block" and guard and not guard["ok"])
    return {
        "id": case["id"],
        "routed": routed,
        "first_tool_possible": first_tool_possible,
        "guard_expected": expected_guard,
        "guard_ok": bool(guard_ok),
        "guard": guard,
        "tool_calls": 0,
        "external_writes": 0,
        "latency_ms": round((time.perf_counter() - started) * 1000, 3),
        "context_characters": len(str(case["prompt"])),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Deterministischer M13-Toolrouting-Replay")
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    parser.add_argument("--phase", choices=("legacy", "implemented"), default="implemented")
    args = parser.parse_args()
    payload = load_corpus(args.corpus)
    if args.phase == "legacy":
        results = [
            {
                "id": case["id"],
                "observed_failure": case["legacy_failure"],
                "passed": False,
            }
            for case in payload["cases"]
        ]
    else:
        results = [replay_case(case) for case in payload["cases"]]
        for result in results:
            result["passed"] = bool(
                result["routed"] and result["first_tool_possible"] and result["guard_ok"]
            )
    passed = sum(bool(result["passed"]) for result in results)
    report = {
        "ok": passed == len(results) if args.phase == "implemented" else True,
        "phase": args.phase,
        "schema_version": payload["schema_version"],
        "case_count": len(results),
        "passed": passed,
        "failed": len(results) - passed,
        "external_writes": 0,
        "results": results,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
