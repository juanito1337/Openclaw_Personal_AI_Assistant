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

from personal_assistant.action_completion import (  # noqa: E402
    action_completion_guard,
    advance_action_obligation,
    build_action_obligation,
    classify_action_intent,
)
from personal_assistant.agent_tool_orchestration import route_intent  # noqa: E402

DEFAULT_CORPUS = ROOT / "tests/fixtures/m15/action-completion-corpus.json"


def load_corpus(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("privacy") != "synthetic-only" or not isinstance(payload.get("cases"), list):
        raise ValueError("M15-Korpus ist nicht als synthetischer Fixture-Vertrag markiert")
    return payload


def replay_case(case: dict[str, Any], *, phase: str) -> dict[str, Any]:
    started = time.perf_counter()
    route = route_intent(str(case["prompt"]))
    intent = classify_action_intent(str(case["prompt"]), route)
    obligation = build_action_obligation(
        str(case["prompt"]), route, turn_id=f"synthetic-{case['id']}"
    )
    promise_blocked = None
    terminal_state = None
    tool_calls = 0
    postconditions_verified = 0
    if obligation is not None:
        promise_blocked = not action_completion_guard(
            obligation, "Ich werde das jetzt ausfuehren. Einen Moment bitte."
        )["ok"]
        if phase == "implemented":
            for operation, payload in (
                ("mail.search", {}),
                ("mail.read", {}),
                (
                    "nextcloud.calendar.from-mail-preview",
                    {
                        "decision": "ready",
                        "candidate_count": int(obligation["target_count"]),
                    },
                ),
            ):
                obligation = advance_action_obligation(
                    obligation,
                    operation=operation,
                    mode="read",
                    ok=True,
                    payload=payload,
                    evidence_turn_id=f"synthetic-{case['id']}",
                )
            for index in range(int(obligation["target_count"])):
                obligation = advance_action_obligation(
                    obligation,
                    operation="nextcloud.calendar.from-mail-create",
                    mode="write",
                    ok=True,
                    postcondition_verified=True,
                    argument_digest=f"{index + 1:064x}",
                    evidence_turn_id=f"synthetic-{case['id']}",
                )
            terminal_state = obligation["terminal_state"]
            tool_calls = obligation["tool_calls"]
            postconditions_verified = obligation["postconditions_verified"]
    expected_intent = str(case["expected_intent"])
    passed = intent == expected_intent
    if expected_intent == "execute":
        passed = bool(
            passed
            and promise_blocked
            and phase == "implemented"
            and terminal_state == "completed"
            and postconditions_verified == int(case["expected_targets"])
        )
    else:
        passed = bool(passed and obligation is None)
    return {
        "id": case["id"],
        "intent": intent,
        "obligation_created": obligation is not None,
        "promise_blocked": promise_blocked,
        "terminal_state": terminal_state,
        "tool_calls": tool_calls,
        "postconditions_verified": postconditions_verified,
        "external_writes": 0,
        "passed": passed,
        "latency_ms": round((time.perf_counter() - started) * 1000, 4),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Deterministischer M15-Aktionsabschluss-Replay")
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    parser.add_argument("--phase", choices=("legacy", "implemented"), default="implemented")
    args = parser.parse_args()
    corpus = load_corpus(args.corpus)
    results = [replay_case(case, phase=args.phase) for case in corpus["cases"]]
    latencies = sorted(float(row["latency_ms"]) for row in results)
    passed = sum(bool(row["passed"]) for row in results)
    report = {
        "ok": passed == len(results) if args.phase == "implemented" else True,
        "phase": args.phase,
        "schema_version": corpus["schema_version"],
        "case_count": len(results),
        "passed": passed,
        "failed": len(results) - passed,
        "external_writes": 0,
        "latency_ms": {
            "total": round(sum(latencies), 4),
            "maximum": max(latencies, default=0.0),
        },
        "results": results,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
