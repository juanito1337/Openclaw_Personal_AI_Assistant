from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections.abc import Iterable, Mapping
from functools import lru_cache
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
    r"\bleit(?:e|en|et)\b.{0,160}\bweiter\b",
    r"\b(?:eintragen|anlegen|erstell(?:e|en|t)|importier(?:e|en|t)|verschieb(?:e|en|t)|aktualisier(?:e|en|t)|abschliessen|abschließen|send(?:e|en|et)|verschick(?:e|en|t)|beantwort(?:e|en|et)|weiterleit(?:e|en|et))\b",
    r"\b(?:fuehre|führe)\b.{0,40}\b(?:aus|durch)\b",
    r"\b(?:crea|crear|anade|añade|agrega|envia|envía|actualiza|mueve|completa)\b",
    r"\b(?:create|add|send|move|update|complete|forward)\b",
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
_WRITE_PROMISE_PATTERNS = (
    r"\bich werde\b.{0,200}\b(?:abschicken|absenden|senden|versenden|verschicken|"
    r"eintragen|anlegen|erstellen|verschieben|aktualisieren|abschliessen|abschließen)\b",
    r"\bich (?:sende|versende|verschicke|schicke|trage|lege|erstelle|verschiebe|"
    r"aktualisiere|beantworte|schliesse|schließe)\b",
    r"\bich (?:fuehre|führe)\b.{0,100}\b(?:versand|aktion|vorgang|aenderung|änderung)\b"
    r".{0,50}\b(?:aus|durch)\b",
    r"\bi (?:will|am going to)\b.{0,200}\b(?:send|create|move|update|complete)\b",
    r"\b(?:voy a|ahora voy a)\b.{0,200}\b(?:enviar|crear|mover|actualizar|completar)\b",
)
_UNBOUND_RETRY_QUESTION_PATTERNS = (
    r"\b(?:soll|sollte) ich\b.{0,180}\b(?:noch einmal|nochmal|erneut|wieder|versuch)",
    r"\b(?:moechtest|möchtest|willst) du\b.{0,180}\b(?:noch einmal|nochmal|erneut|wieder|versuch)",
    r"\bshould i\b.{0,180}\b(?:try again|retry)",
    r"\b(?:quieres que|debo)\b.{0,180}\b(?:intente de nuevo|reintente)",
)
_ALL_TARGETS_COMPLETED = (
    r"\b(?:alle|beide|all|both)\b.{0,100}\b(?:erledigt|abgeschlossen|angelegt|erstellt|"
    r"aktualisiert|gesendet|verschickt|done|completed|created|updated|sent)\b"
)


def _normalized(value: str) -> str:
    return unicodedata.normalize("NFKC", str(value or "")).casefold().strip()


def action_completion_contract(operation_ids: Iterable[str]) -> dict[str, Any]:
    """Return the closed M15 state contract derived against catalog operations."""

    known = set(operation_ids)
    workflow_operations = {
        "select-source": [item for item in ("mail.search", "mail.recent") if item in known],
        "read-source": [item for item in ("mail.read",) if item in known],
        "build-preview": [item for item in ("nextcloud.calendar.from-mail-preview",) if item in known],
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
                "mail.forward-send",
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


def _target_scope(prompt: str, workflow_kind: str) -> tuple[int, str | None]:
    # Counts inside quoted titles and dates are not requested target quantities.
    # This detects ambiguity only; it never grants authority or resolves targets.
    text = re.sub(r'"[^"\n]*"|\x27[^\x27\n]*\x27|„[^“\n]*“|«[^»\n]*»', "", _normalized(prompt))
    plural = (
        r"(?:aufgaben|tasks|termine|events|kontakte|contacts|mails|e-mails|nachrichten|messages|"
        r"dateien|files|folders|fluege|flüge|flights|vuelos)"
    )
    counts = {
        "zwei": 2,
        "beide": 2,
        "beiden": 2,
        "two": 2,
        "both": 2,
        "dos": 2,
        "drei": 3,
        "three": 3,
        "tres": 3,
        "vier": 4,
        "four": 4,
        "cuatro": 4,
        "fuenf": 5,
        "fünf": 5,
        "five": 5,
    }
    matches = re.findall(rf"\b(\d{{1,4}}|{'|'.join(counts)})\s+(?:{plural}|ordner)\b(?!-)", text)
    quantities = [int(item) if item.isdecimal() else counts[item] for item in matches]
    if quantities and (max(quantities) > MAX_ACTION_TARGETS or min(quantities) < 1):
        return MAX_ACTION_TARGETS, "target-limit"
    if len(set(quantities)) > 1:
        return max(quantities), "multiple-targets-not-bound"
    count = max(quantities, default=1)
    if workflow_kind == "mail-to-calendar" and (
        re.search(r"\bhin(?:-| )?und(?:-| )?rueckflug\b", text)
        or re.search(r"\bhinflug\b", text)
        and re.search(r"\br(?:ue|ü)ckflug\b", text)
        or re.search(r"\b(?:fluege|flüge|flights|vuelos)\b", text)
    ):
        return max(2, count), None
    if workflow_kind == "mail-to-calendar":
        return count, None  # The registered preview binds exact candidate identities.
    plural_request = (
        r"(?:^|\b(?:bitte|erstelle|erstellen|aktualisiere|aktualisieren|verschiebe|verschieben|"
        r"sende|send|create|update|complete|move|forward)\s+)"
        r"(?:(?:die|diese|meine|alle|saemtliche|sämtliche|mehrere|einige|the|these|all|some)\s+)?"
        rf"{plural}\b(?!-)"
    )
    singular = (
        r"(?:aufgabe|task|termin|event|kontakt|contact|mail|e-mail|nachricht|message|datei|file|ordner|folder)"
    )
    repeated_target = (
        rf"\b{singular}\b.{{0,160}}\b(?:und|and|sowie)\s+"
        r"(?:(?:erstelle|aktualisiere|verschiebe|erledige|create|update|complete|move)\s+)?"
        rf"(?:(?:eine|einen|ein|die|den|der|a|an|the)\s+)?{singular}\b"
    )
    if (
        count > 1
        or re.search(plural_request, text)
        or re.search(repeated_target, text)
        or re.search(r"(?:^|\n)\s*(?:[-*]|\d+[.)])\s", text)
    ):
        return count, "multiple-targets-not-bound"
    return count, None


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
    target_count, scope_error = _target_scope(prompt, workflow_kind)
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
                "target_count": target_count,
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
        "target_count": target_count,
        "completed_targets": 0,
        "steps": steps,
        "current_step": steps[0],
        "step_index": 0,
        "tool_calls": 0,
        "status": "terminal" if scope_error else "open",
        "terminal_state": ("blocked" if scope_error == "target-limit" else "information-required")
        if scope_error
        else None,
        "last_error": scope_error,
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


@lru_cache(maxsize=1)
def _operation_domains() -> dict[str, str]:
    from .tool_registry import tool_definitions

    return {item.id: item.domain for item in tool_definitions()}


@lru_cache(maxsize=1)
def _read_operation_domains() -> dict[str, str]:
    from .tool_registry import tool_definitions

    return {item.id: item.domain for item in tool_definitions() if item.mode == "read"}


@lru_cache(maxsize=1)
def _local_preview_operations() -> set[str]:
    from .tool_registry import tool_definitions

    return {
        item.id for item in tool_definitions()
        if item.mode == "local-write" and "--dry-run" in item.command.split()
    }


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
    if evidence_turn_id != str(result.get("turn_id") or ""):
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
        # Use the typed catalog rather than guessing a domain from an ID prefix
        # (mail.invoice and mail.order operations belong to different domains).
        domain = _operation_domains().get(operation, "")
        if domain not in result.get("domains", []) or (
            workflow_kind == "mail-to-calendar" and operation != "nextcloud.calendar.from-mail-create"
        ):
            result.update(
                status="terminal",
                terminal_state="blocked",
                last_error="unexpected-write-operation",
            )
            return result
        if mode == "local-write" and operation in _local_preview_operations():
            return result
        if operation in {"mail.reply-draft", "mail.compose-draft", "mail.forward-draft"}:
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
        if not postcondition_verified or (payload or {}).get("dry_run") is True:
            result.update(
                status="terminal",
                terminal_state="blocked",
                last_error="write-postcondition-unverified",
            )
            return result
        identity_digest = str(argument_digest or "")
        if workflow_kind == "mail-to-calendar":
            candidate_id = str((payload or {}).get("candidate_id") or "")
            if not candidate_id:
                result.update(
                    status="terminal", terminal_state="blocked", last_error="target-identity-unverified"
                )
                return result
            identity_digest = hashlib.sha256(
                json.dumps(
                    {"operation": operation, "target": candidate_id},
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest()
        if identity_digest and identity_digest in result.get("write_digests", []):
            result.update(status="terminal", terminal_state="blocked", last_error="duplicate-target-evidence")
            return result
        result["write_operations"] = [*list(result.get("write_operations") or []), operation]
        result["write_digests"] = [*list(result.get("write_digests") or []), identity_digest]
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
    if workflow_kind == "mail-to-calendar":
        allowed, next_step = mail_calendar_progress.get(current, (set(), ""))
    elif current in {"resolve-target", "execute-one"}:
        allowed = {
            item for item, domain in _read_operation_domains().items() if domain in result.get("domains", [])
        }
        next_step = current
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
    normalized = _normalized(answer)
    if obligation is None:
        no_obligation_issues: list[str] = []
        if any(re.search(pattern, normalized, flags=re.IGNORECASE) for pattern in _WRITE_PROMISE_PATTERNS):
            no_obligation_issues.append("write-promise-without-action-obligation")
        return {
            "ok": not no_obligation_issues,
            "issues": no_obligation_issues,
            "terminal_state": None,
            "fail_closed": True,
        }
    terminal = obligation.get("terminal_state")
    if terminal in ACTION_TERMINAL_STATES:
        issues: list[str] = []
        completed = int(obligation.get("completed_targets") or 0)
        incomplete = completed < int(obligation.get("target_count") or 1)
        if (incomplete and terminal == "completed") or (
            (incomplete or completed < 2) and re.search(_ALL_TARGETS_COMPLETED, normalized)
        ):
            issues.append("all-targets-completion-unverified")
        if terminal in {"approval-required", "information-required", "blocked"} and any(
            re.search(pattern, normalized, flags=re.IGNORECASE) for pattern in _WRITE_PROMISE_PATTERNS
        ):
            issues.append("write-promise-without-action-obligation")
        if terminal in {"approval-required", "information-required", "blocked"} and any(
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
