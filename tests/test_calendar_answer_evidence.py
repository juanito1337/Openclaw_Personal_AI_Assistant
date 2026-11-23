"""Calendar absence is a scoped day proof, not an empty general listing."""

from __future__ import annotations

import json
import subprocess
from datetime import datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from personal_assistant.agent_tool_orchestration import (
    build_native_tool_contract,
    guard_claims,
    route_intent,
)

ROOT = Path(__file__).resolve().parents[1]
PROMPT = "Welche Termine habe ich heute?"


def day_payload() -> dict:
    zone = ZoneInfo("Europe/Berlin")
    now = datetime.now(zone)
    start = datetime.combine(now.date(), time.min, zone)
    return {
        "ok": True, "complete": True, "count": 0, "events": [],
        "resource_errors": [], "results_may_be_truncated": False,
        "absence_proven": True, "observed_at": now.isoformat(),
        "scope": {
            "kind": "calendar-day", "relative": "today", "selection": "all-selected",
            "date": now.date().isoformat(), "timezone": zone.key,
            "start": start.isoformat(), "end": (start + timedelta(days=1)).isoformat(),
            "resource_ids": ["calendar-a", "calendar-b"],
        },
    }


def evaluate(payload: dict, answer: str, *, operation: str = "nextcloud.calendar.today",
             prompt: str = PROMPT, extra: list | None = None, advance_ms: int = 0) -> dict:
    data = {"contract": build_native_tool_contract(), "prompt": prompt, "answer": answer,
            "payload": payload, "operation": operation, "extra": extra or [], "advance": advance_ms}
    script = """
import {makeEvidence,guardAnswer,routePrompt} from './docker/openclaw-personal-assistant-plugin/runtime.js';
const input = INPUT;
const operation={tool_id:input.operation,domain:'calendar',mode:'read',approval:'none'};
const evidence=makeEvidence(operation,{returncode:0},input.payload,'turn','call');
const route=routePrompt(input.contract,input.prompt);
const originalNow=Date.now;
Date.now=()=>originalNow()+input.advance;
const guard=guardAnswer(input.contract,route,input.answer,[evidence,...input.extra]);
console.log(JSON.stringify({evidence,route,guard}));
""".replace("INPUT", json.dumps(data))
    result = subprocess.run(["node", "--input-type=module"], cwd=ROOT, input=script,
                            capture_output=True, text=True, check=False, timeout=30)
    assert result.returncode == 0, result.stderr
    actual = json.loads(result.stdout)
    assert actual["route"] == route_intent(prompt)
    if not advance_ms:
        assert actual["guard"] == guard_claims(route=actual["route"], answer=answer,
                                              evidence=[actual["evidence"], *(extra or [])])
    return actual


def test_complete_empty_today_allows_only_current_calendar_absence() -> None:
    actual = evaluate(day_payload(), "Heute hast du keine Termine.")
    assert "negative" in actual["evidence"]["allowed_claims"]
    assert actual["guard"]["ok"] is True


@pytest.mark.parametrize("mutation", [
    {"complete": False}, {"ok": False}, {"results_may_be_truncated": True},
    {"resource_errors": [{"resource_id": "calendar-b", "error": "rights-unverified"}]},
    {"absence_proven": False}, {"events": [{"uid": "appointment"}], "count": 1},
    {"observed_at": "2020-01-01T12:00:00Z"}, {"observed_at": None},
    {"scope": {}}, {"count": 1}, {"resource_errors": None},
])
def test_incomplete_or_invalid_day_cannot_authorize_absence(mutation: dict) -> None:
    actual = evaluate(day_payload() | mutation, "Heute hast du keine Termine.")
    assert "negative" not in actual["evidence"]["allowed_claims"]
    assert "negative-claim-not-authorized" in actual["guard"]["issues"]


@pytest.mark.parametrize("mutation", [
    {"resource_ids": []}, {"selection": "single-resource"}, {"timezone": "Unknown/Zone"},
    {"date": "2020-01-01"}, {"start": "2020-01-01T00:00:00Z"},
])
def test_wrong_scope_cannot_authorize_absence(mutation: dict) -> None:
    payload = day_payload()
    payload["scope"].update(mutation)
    actual = evaluate(payload, "Heute hast du keine Termine.")
    assert "negative" not in actual["evidence"]["allowed_claims"]


@pytest.mark.parametrize("operation", ["nextcloud.calendar.list", "nextcloud.calendar.search"])
def test_general_list_or_text_search_is_not_a_day_proof(operation: str) -> None:
    actual = evaluate(day_payload(), "Heute hast du keine Termine.", operation=operation)
    assert actual["guard"]["ok"] is False


@pytest.mark.parametrize("answer", [
    "Ich kann keine Termine abrufen, weil die Kalenderrechte nicht belegt sind.",
    "Ich habe keinen Zugriff auf die Termine: rights-unverified.",
    "Die Tagesabfrage ist unvollständig; der zweite Kalender konnte nicht geprüft werden.",
    "Ich kann heute leider keine Termine zuverlässig abrufen.",
])
def test_bound_failure_can_be_reported_without_false_absence_classification(answer: str) -> None:
    actual = evaluate({"ok": False, "complete": False}, answer)
    assert actual["guard"]["ok"] is True


@pytest.mark.parametrize("answer", [
    "Heute hast du keine Termine.", "Keine Termine gefunden.", "Es gibt keine Termine.",
    "No events found today.", "No hay citas hoy.",
    "Ich kann keine Termine abrufen. Heute hast du keine Termine.",
    "Heute hast du keine weiteren Termine.",
])
def test_failure_and_other_domain_evidence_do_not_allow_calendar_absence(answer: str) -> None:
    extra = [{"tool_id": "mail.search", "domain": "mail", "ok": True,
              "allowed_claims": ["negative"]}]
    actual = evaluate({"ok": False, "complete": False}, answer, extra=extra)
    assert "negative-claim-not-authorized" in actual["guard"]["issues"]


@pytest.mark.parametrize(("prompt", "answer"), [
    ("Welche Termine habe ich morgen?", "Morgen hast du keine Termine."),
    ("Erstelle heute einen Termin", "Heute hast du keine Termine."),
    (PROMPT, "Morgen hast du keine Termine."),
    (PROMPT, "Keine Mail gefunden."),
    (PROMPT, "Keine Termine und keine Mail gefunden."),
    (PROMPT, "Du hast diese Woche keine Termine."),
    (PROMPT, "Für den 1. Januar 2020 hast du keine Termine."),
    (PROMPT, "In den nächsten Tagen hast du keine Termine."),
    (PROMPT, "No events and no tasks found today."),
    (PROMPT, "Die nächsten Tage sind alle terminfrei."),
    (PROMPT, "You are free all week."),
    ("Welche Termine habe ich heute und morgen?", "Du hast keine Termine."),
])
def test_today_proof_does_not_leak_to_other_dates_actions_or_domains(prompt: str, answer: str) -> None:
    actual = evaluate(day_payload(), answer, prompt=prompt)
    assert "negative-claim-not-authorized" in actual["guard"]["issues"]


def test_proof_expires_before_a_delayed_reply() -> None:
    actual = evaluate(day_payload(), "Heute hast du keine Termine.", advance_ms=86_400_000)
    assert "negative-claim-not-authorized" in actual["guard"]["issues"]


def test_later_calendar_observation_revokes_earlier_absence() -> None:
    later = {"tool_id": "nextcloud.calendar.today", "domain": "calendar", "ok": True,
             "complete": True, "allowed_claims": ["positive-evidence"]}
    actual = evaluate(day_payload(), "Heute hast du keine Termine.", extra=[later])
    assert "negative-claim-not-authorized" in actual["guard"]["issues"]


@pytest.mark.parametrize("prompt", [
    "What major world events happened today?",
    "Which runtime events caused the scheduler failure?",
])
def test_unrelated_events_are_not_personal_calendar_requests(prompt: str) -> None:
    actual = evaluate(day_payload(), "The evidence is incomplete.", prompt=prompt)
    assert not any(route["domain"] == "calendar" for route in actual["route"]["routes"])
