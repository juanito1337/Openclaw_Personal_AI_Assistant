from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections.abc import Iterable, Mapping
from typing import Any

ACTION_SCHEMA_VERSION = 1
ACTION_INTENTS = ("execute", "preview", "explain", "ambiguous")
ACTION_TERMINAL_STATES = (
    "completed",
    "approval-required",
    "information-required",
    "blocked",
    "cancelled-by-user",
)
WORKFLOW_STEPS = (
    "select-source",
    "read-source",
    "build-preview",
    "resolve-target",
    "check-duplicate",
    "execute-one",
    "verify-one",
    "finish",
)
MAX_ACTION_TARGETS = 4
MAX_WORKFLOW_STEPS = 16
MAX_WORKFLOW_TOOL_CALLS = 12

_EXECUTE_PATTERNS = (
    r"\b(?:trag|trage|tragt|tragen)\b.{0,80}\b(?:ein|kalender)\b",
    r"\b(?:eintragen|anlegen|erstellen|verschieben|aktualisieren|abschliessen|abschließen|send(?:e|en|et)|verschick(?:e|en|t)|beantwort(?:e|en|et))\b",
    r"\b(?:fuehre|führe)\b.{0,40}\b(?:aus|durch)\b",
    r"\b(?:crea|crear|anade|añade|agrega|envia|envía|actualiza|mueve|completa)\b",
    r"\b(?:create|add|send|move|update|complete)\b",
)
_PREVIEW_PATTERNS = (
    r"\b(?:vorschau|preview|dry[- ]?run|simulier)\w*\b",
    r"\b(?:zeige|zeig)\b.{0,60}\b(?:zuerst|vorher|vorschlag)\b",
    r"\b(?:muestra|vista previa|simula)\b",
)
_EXPLAIN_PATTERNS = (
    r"\b(?:erklaer|erklär|beschreib|wie (?:geht|funktioniert|wuerdest|würdest))\w*\b",
    r"\b(?:explain|how would|como funciona|cómo funciona)\b",
)
_PROMISE_PATTERNS = (
    r"\bich (?:werde|mache|fuehre|führe|trage|lege)\b",
    r"\b(?:einen moment|gleich|jetzt werde ich|ich muss .*tool)\b",
    r"\b(?:i will|i am going to|give me a moment|voy a|ahora voy a)\b",
)
_UNBOUND_RETRY_QUESTION_PATTERNS = (
    r"\b(?:soll|sollte) ich\b.{0,180}\b(?:noch einmal|nochmal|erneut|wieder|versuch)",
    r"\b(?:moechtest|möchtest|willst) du\b.{0,180}\b(?:noch einmal|nochmal|erneut|wieder|versuch)",
    r"\bshould i\b.{0,180}\b(?:try again|retry)",
    r"\b(?:quieres que|debo)\b.{0,180}\b(?:intente de nuevo|reintente)",
)


def _normalized(value: str) -> str:
    return unicodedata.normalize("NFKC", str(value or "")).casefold().strip()


def action_completion_contract(operation_ids: Iterable[str]) -> dict[str, Any]:
    """Return the closed M15 state contract derived against catalog operations."""

    known = set(operation_ids)
    workflow_operations = {
        "select-source": [item for item in ("mail.search", "mail.recent") if item in known],
        "read-source": [item for item in ("mail.read",) if item in known],
        "build-preview": [
            item
            for item in ("nextcloud.calendar.from-mail-preview",)
            if item in known
        ],
        "resolve-target": [
            item
            for item in (
                "nextcloud.calendar.status",
                "nextcloud.tasks.status",
                "nextcloud.contacts.status",
                "nextcloud.workspace.configure",
            )
            if item in known
        ],
        "check-duplicate": [
            item
            for item in (
                "nextcloud.calendar.search",
                "nextcloud.calendar.from-mail-preview",
            )
            if item in known
        ],
        "execute-one": sorted(
            item
            for item in known
            if item
            in {
                "nextcloud.calendar.from-mail-create",
                "nextcloud.calendar.create",
                "nextcloud.calendar.update",
                "nextcloud.tasks.create",
                "nextcloud.tasks.update",
                "nextcloud.contacts.create",
                "nextcloud.contacts.update",
                "nextcloud.workspace.mkdir",
                "nextcloud.workspace.write-text",
                "nextcloud.workspace.upload",
                "nextcloud.workspace.move",
                "mail.reply-send",
                "mail.compose-send",
                "mail.move",
            }
        ),
        "verify-one": [],
        "finish": [],
    }
    return {
        "schema_version": ACTION_SCHEMA_VERSION,
        "intents": list(ACTION_INTENTS),
        "terminal_states": list(ACTION_TERMINAL_STATES),
        "workflow_steps": list(WORKFLOW_STEPS),
        "workflow_operations": workflow_operations,
        "limits": {
            "max_targets": MAX_ACTION_TARGETS,
            "max_steps": MAX_WORKFLOW_STEPS,
            "max_tool_calls": MAX_WORKFLOW_TOOL_CALLS,
            "max_guard_revisions": 1,
        },
        "effects": [
            "external-create",
            "external-write",
            "external-update",
            "external-send",
            "external-move",
            "local-write",
        ],
        "turn_bound": True,
        "may_authorize_write": False,
        "content_may_change_route": False,
        "bulk_write": False,
        "automatic_remote_rollback": False,
    }


def classify_action_intent(prompt: str, route: Mapping[str, Any]) -> str:
    """Classify only the current user prompt; remote content is never routed."""

    text = _normalized(prompt)
    if not route.get("resolved"):
        return "ambiguous"
    if any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in _PREVIEW_PATTERNS):
        return "preview"
    if any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in _EXPLAIN_PATTERNS):
        return "explain"
    if any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in _EXECUTE_PATTERNS):
        return "execute"
    return "ambiguous"


def _workflow_kind(prompt: str, domains: set[str]) -> str:
    text = _normalized(prompt)
    source_mail = "mail" in domains or bool(
        re.search(r"\b(?:mail|e-?mail|nachricht|correo)\b", text, flags=re.IGNORECASE)
    )
    calendar = "calendar" in domains or bool(
        re.search(r"\b(?:termin|kalender|flug|calendar|evento|vuelo)\b", text, flags=re.IGNORECASE)
    )
    return "mail-to-calendar" if source_mail and calendar else "single-action"


def _target_count(prompt: str, workflow_kind: str) -> int:
    text = _normalized(prompt)
    if workflow_kind == "mail-to-calendar" and (
        re.search(r"\bhin(?:-| )?und(?:-| )?rueckflug\b", text)
        or re.search(r"\bhinflug\b", text) and re.search(r"\br(?:ue|ü)ckflug\b", text)
        or re.search(r"\b(?:fluege|flüge|flights|vuelos)\b", text)
    ):
        return 2
    return 1


def build_action_obligation(
    prompt: str,
    route: Mapping[str, Any],
    *,
    turn_id: str,
) -> dict[str, Any] | None:
    intent = classify_action_intent(prompt, route)
    if intent != "execute":
        return None
    domains = {str(item.get("domain") or "") for item in route.get("routes") or []}
    workflow_kind = _workflow_kind(prompt, domains)
    steps = (
        list(WORKFLOW_STEPS)
        if workflow_kind == "mail-to-calendar"
        else ["resolve-target", "execute-one", "verify-one", "finish"]
    )
    digest = hashlib.sha256(
        json.dumps(
            {
                "turn_id": str(turn_id),
                "intent": intent,
                "domains": sorted(domains),
                "workflow_kind": workflow_kind,
                "target_count": _target_count(prompt, workflow_kind),
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return {
        "schema_version": ACTION_SCHEMA_VERSION,
        "obligation_id": digest[:24],
        "turn_id": str(turn_id),
        "intent": intent,
        "domains": sorted(domains),
        "workflow_kind": workflow_kind,
        "effect": "external-create" if workflow_kind == "mail-to-calendar" else "external-write",
        "target_count": _target_count(prompt, workflow_kind),
        "completed_targets": 0,
        "steps": steps,
        "current_step": steps[0],
        "step_index": 0,
        "tool_calls": 0,
        "status": "open",
        "terminal_state": None,
        "last_error": None,
        "write_operations": [],
        "write_digests": [],
        "required_slots": (
            ["folder", "message_id", "expected_subject", "preview_digest", "candidate_id"]
            if workflow_kind == "mail-to-calendar"
            else []
        ),
        "postconditions": (
            ["remote-uid-read-back", "remote-etag-present", "approved-fields-match"]
            if workflow_kind == "mail-to-calendar"
            else ["registered-operation-postcondition"]
        ),
        "postconditions_verified": 0,
    }


def _copy_obligation(obligation: Mapping[str, Any]) -> dict[str, Any]:
    return json.loads(json.dumps(dict(obligation)))


def advance_action_obligation(
    obligation: Mapping[str, Any],
    *,
    operation: str,
    mode: str,
    ok: bool,
    postcondition_verified: bool = False,
    error: str | None = None,
    argument_digest: str = "",
    payload: Mapping[str, Any] | None = None,
    evidence_turn_id: str = "",
) -> dict[str, Any]:
    """Advance a bounded workflow from actual tool evidence, never model prose."""

    result = _copy_obligation(obligation)
    if result.get("terminal_state") in ACTION_TERMINAL_STATES:
        return result
    if evidence_turn_id and evidence_turn_id != str(result.get("turn_id") or ""):
        return result
    if int(result.get("tool_calls") or 0) >= MAX_WORKFLOW_TOOL_CALLS:
        result.update(status="terminal", terminal_state="blocked", last_error="tool-call-limit")
        return result
    result["tool_calls"] = int(result.get("tool_calls") or 0) + 1
    if not ok:
        if error == "approval-required":
            result.update(
                status="terminal",
                terminal_state="approval-required",
                last_error="missing-or-stale-bound-approval",
            )
            return result
        result.update(
            status="terminal",
            terminal_state="blocked",
            last_error=str(error or "operation-failed"),
        )
        return result
    steps = list(result.get("steps") or [])
    current = str(result.get("current_step") or "")
    workflow_kind = str(result.get("workflow_kind") or "single-action")

    if mode != "read":
        if (
            workflow_kind == "mail-to-calendar"
            and operation != "nextcloud.calendar.from-mail-create"
        ):
            result.update(
                status="terminal",
                terminal_state="blocked",
                last_error="unexpected-write-operation",
            )
            return result
        if operation in {"mail.reply-draft", "mail.compose-draft"}:
            result.update(
                status="terminal",
                terminal_state="approval-required",
                current_step="execute-one",
                step_index=steps.index("execute-one"),
                last_error="presented-draft-send-approval-required",
            )
            return result
        if current == "resolve-target" and workflow_kind == "single-action":
            current = "execute-one"
            result["current_step"] = current
            result["step_index"] = steps.index(current)
        if current != "execute-one":
            result.update(
                status="terminal",
                terminal_state="blocked",
                last_error="unexpected-workflow-step",
            )
            return result
        result["write_operations"] = [*list(result.get("write_operations") or []), operation]
        result["write_digests"] = [
            *list(result.get("write_digests") or []),
            str(argument_digest or ""),
        ]
        if not postcondition_verified:
            result.update(
                status="terminal",
                terminal_state="blocked",
                last_error="write-postcondition-unverified",
            )
            return result
        completed = int(result.get("completed_targets") or 0) + 1
        result["completed_targets"] = completed
        result["postconditions_verified"] = int(result.get("postconditions_verified") or 0) + 1
        if completed >= int(result.get("target_count") or 1):
            result.update(
                status="terminal",
                terminal_state="completed",
                current_step="finish",
                step_index=max(0, len(steps) - 1),
            )
        else:
            result.update(status="open", current_step="execute-one")
        return result

    mail_calendar_progress: dict[str, tuple[set[str], str]] = {
        "select-source": ({"mail.search", "mail.recent"}, "read-source"),
        "read-source": ({"mail.read"}, "build-preview"),
        # The registered preview resolves the exact configured resource and
        # performs the duplicate lookup, so these two abstract steps are
        # deliberately atomic at the tool boundary.
        "build-preview": ({"nextcloud.calendar.from-mail-preview"}, "execute-one"),
    }
    single_operations = {
        "nextcloud.calendar.status",
        "nextcloud.calendar.search",
        "nextcloud.tasks.status",
        "nextcloud.tasks.list",
        "nextcloud.contacts.status",
        "nextcloud.contacts.search",
        "mail.search",
        "mail.read",
    }
    if workflow_kind == "mail-to-calendar":
        allowed, next_step = mail_calendar_progress.get(current, (set(), ""))
    elif current == "resolve-target":
        allowed, next_step = single_operations, "execute-one"
    else:
        allowed, next_step = set(), ""
    if operation not in allowed:
        result.update(
            status="terminal",
            terminal_state="blocked",
            last_error="unexpected-workflow-step",
        )
        return result
    if operation == "nextcloud.calendar.from-mail-preview":
        if str((payload or {}).get("decision") or "") == "information-required":
            result.update(
                status="terminal",
                terminal_state="information-required",
                last_error="source-fields-incomplete",
            )
            return result
        candidate_count = (payload or {}).get("candidate_count")
        if isinstance(candidate_count, int):
            if candidate_count < int(result.get("target_count") or 1):
                result.update(
                    status="terminal",
                    terminal_state="information-required",
                    last_error="requested-targets-missing",
                )
                return result
            if candidate_count > MAX_ACTION_TARGETS:
                result.update(
                    status="terminal",
                    terminal_state="blocked",
                    last_error="target-limit",
                )
                return result
            result["target_count"] = candidate_count
    if next_step:
        result["current_step"] = next_step
        result["step_index"] = steps.index(next_step) if next_step in steps else result.get("step_index", 0)
    return result


def mark_approval_required(obligation: Mapping[str, Any]) -> dict[str, Any]:
    result = _copy_obligation(obligation)
    result.update(status="terminal", terminal_state="approval-required")
    return result


def action_completion_guard(
    obligation: Mapping[str, Any] | None,
    answer: str,
) -> dict[str, Any]:
    if obligation is None:
        return {"ok": True, "issues": [], "terminal_state": None, "fail_closed": True}
    terminal = obligation.get("terminal_state")
    if terminal in ACTION_TERMINAL_STATES:
        normalized = _normalized(answer)
        issues: list[str] = []
        if terminal in {"approval-required", "blocked"} and any(
            re.search(pattern, normalized, flags=re.IGNORECASE)
            for pattern in _UNBOUND_RETRY_QUESTION_PATTERNS
        ):
            issues.append("unbound-yes-no-retry-question")
        if terminal == "approval-required":
            if re.search(r"(?:^|\s)/approve(?:\s|$)", normalized):
                issues.append("stale-or-unbound-approval-command")
            if re.search(
                r"\b(?:mail\s+)?(?:gesendet|verschickt|sent|enviad[oa])\b",
                normalized,
            ):
                issues.append("send-claim-before-completion")
        return {
            "ok": not issues,
            "issues": issues,
            "terminal_state": terminal,
            "fail_closed": True,
        }
    normalized = _normalized(answer)
    issues = ["open-action-obligation"]
    if not normalized:
        issues.append("empty-action-response")
    if any(re.search(pattern, normalized, flags=re.IGNORECASE) for pattern in _PROMISE_PATTERNS):
        issues.append("future-promise-without-action")
    return {"ok": False, "issues": issues, "terminal_state": None, "fail_closed": True}


__all__ = [
    "ACTION_INTENTS",
    "ACTION_SCHEMA_VERSION",
    "ACTION_TERMINAL_STATES",
    "MAX_ACTION_TARGETS",
    "MAX_WORKFLOW_STEPS",
    "MAX_WORKFLOW_TOOL_CALLS",
    "WORKFLOW_STEPS",
    "action_completion_contract",
    "action_completion_guard",
    "advance_action_obligation",
    "build_action_obligation",
    "classify_action_intent",
    "mark_approval_required",
]
