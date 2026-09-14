from __future__ import annotations

import json
import subprocess
import sys
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

from mail_agent.models import ParsedMessage
from personal_assistant.action_completion import (
    ACTION_TERMINAL_STATES,
    action_completion_guard,
    advance_action_obligation,
    build_action_obligation,
    classify_action_intent,
)
from personal_assistant.actions import ActionService
from personal_assistant.agent_tool_orchestration import route_intent
from personal_assistant.calendar_from_mail import (
    build_calendar_mail_preview,
    select_unchanged_candidate,
)
from personal_assistant.connectors.nextcloud.calendar import CalendarObject
from personal_assistant.models import ActionPlan, Resource
from personal_assistant.service import PersonalAssistant
from personal_assistant.tool_settings import AntivirusToolSettings, DirectCalendarToolSettings

ROOT = Path(__file__).resolve().parents[1]
CORPUS = ROOT / "tests/fixtures/m15/action-completion-corpus.json"


def _corpus() -> dict:
    return json.loads(CORPUS.read_text(encoding="utf-8"))


def _message(body: str | None = None) -> ParsedMessage:
    source = _corpus()["synthetic_mail"]
    content = body if body is not None else source["body"]
    raw = (
        "From: Synthetic Airline <noreply@example.invalid>\r\n"
        "To: Test <test@example.invalid>\r\n"
        f"Subject: {source['subject']}\r\n"
        "Content-Type: text/plain; charset=utf-8\r\n\r\n"
        f"{content}"
    ).encode()
    return ParsedMessage(
        stable_key="synthetic-stable-key",
        mailbox_id=source["mailbox_id"],
        source_folder=source["folder"],
        raw=raw,
        subject=source["subject"],
        sender_name="Synthetic Airline",
        sender_addr="noreply@example.invalid",
        body_text=content,
    )


def _ready_obligation(*, target_count: int = 2) -> dict:
    prompt = _corpus()["cases"][0]["prompt"]
    obligation = build_action_obligation(prompt, route_intent(prompt), turn_id="turn-1")
    assert obligation is not None
    for operation, payload in (
        ("mail.search", {}),
        ("mail.read", {}),
        (
            "nextcloud.calendar.from-mail-preview",
            {"decision": "ready", "candidate_count": target_count},
        ),
    ):
        obligation = advance_action_obligation(
            obligation,
            operation=operation,
            mode="read",
            ok=True,
            payload=payload,
            evidence_turn_id="turn-1",
        )
    return obligation


class ActionIntentAndContractTests(unittest.TestCase):
    def test_generated_action_schema_is_closed_and_catalog_bound(self) -> None:
        plugin = ROOT / "docker/openclaw-personal-assistant-plugin"
        contract = json.loads((plugin / "generated-tools.json").read_text(encoding="utf-8"))
        schema = json.loads(
            (plugin / "action-obligation.schema.json").read_text(encoding="utf-8")
        )
        action = contract["action_completion"]
        self.assertFalse(schema["additionalProperties"])
        self.assertEqual(schema["properties"]["intent"]["enum"], action["intents"])
        self.assertEqual(
            schema["properties"]["terminal_state"]["enum"],
            [None, *action["terminal_states"]],
        )
        self.assertEqual(
            set(schema["properties"]["write_operations"]["items"]["enum"]),
            set(action["workflow_operations"]["execute-one"]),
        )
        self.assertFalse(action["may_authorize_write"])
        self.assertFalse(action["bulk_write"])

    def test_synthetic_corpus_has_no_productive_identifiers_and_classifies(self) -> None:
        corpus = _corpus()
        self.assertEqual(corpus["privacy"], "synthetic-only")
        serialized = json.dumps(corpus, ensure_ascii=False)
        self.assertNotIn("/srv/openclaw", serialized)
        self.assertNotIn("@zimmerei", serialized)
        for case in corpus["cases"]:
            with self.subTest(case=case["id"]):
                route = route_intent(case["prompt"])
                self.assertEqual(
                    classify_action_intent(case["prompt"], route),
                    case["expected_intent"],
                )
                obligation = build_action_obligation(
                    case["prompt"], route, turn_id="turn-current"
                )
                if case["expected_intent"] == "execute":
                    self.assertIsNotNone(obligation)
                    assert obligation is not None
                    self.assertEqual(obligation["workflow_kind"], case["expected_workflow"])
                    self.assertEqual(obligation["target_count"], case["expected_targets"])
                else:
                    self.assertIsNone(obligation)

    def test_promise_without_tool_is_rejected_and_never_terminal(self) -> None:
        prompt = _corpus()["cases"][0]["prompt"]
        obligation = build_action_obligation(prompt, route_intent(prompt), turn_id="turn-1")
        verdict = action_completion_guard(
            obligation,
            "Ich trage die Termine jetzt ein. Einen Moment bitte.",
        )
        self.assertFalse(verdict["ok"])
        self.assertIn("future-promise-without-action", verdict["issues"])
        self.assertNotIn("announced", ACTION_TERMINAL_STATES)
        self.assertNotIn("working", ACTION_TERMINAL_STATES)

    def test_old_turn_and_wrong_write_cannot_close_obligation(self) -> None:
        prompt = _corpus()["cases"][0]["prompt"]
        obligation = build_action_obligation(prompt, route_intent(prompt), turn_id="turn-1")
        assert obligation is not None
        unchanged = advance_action_obligation(
            obligation,
            operation="nextcloud.calendar.from-mail-create",
            mode="write",
            ok=True,
            postcondition_verified=True,
            argument_digest="a" * 64,
            evidence_turn_id="turn-old",
        )
        self.assertEqual(unchanged, obligation)
        blocked = advance_action_obligation(
            obligation,
            operation="mail.move",
            mode="write",
            ok=True,
            postcondition_verified=True,
            argument_digest="b" * 64,
            evidence_turn_id="turn-1",
        )
        self.assertEqual(blocked["terminal_state"], "blocked")
        self.assertEqual(blocked["last_error"], "unexpected-write-operation")
        step_jump = advance_action_obligation(
            obligation,
            operation="nextcloud.calendar.from-mail-create",
            mode="write",
            ok=True,
            postcondition_verified=True,
            argument_digest="c" * 64,
            evidence_turn_id="turn-1",
        )
        self.assertEqual(step_jump["terminal_state"], "blocked")
        self.assertEqual(step_jump["last_error"], "unexpected-workflow-step")

    def test_information_required_comes_from_tool_payload_not_model_question(self) -> None:
        prompt = _corpus()["cases"][0]["prompt"]
        obligation = build_action_obligation(prompt, route_intent(prompt), turn_id="turn-1")
        assert obligation is not None
        still_open = action_completion_guard(obligation, "Welche Uhrzeit meinst du?")
        self.assertFalse(still_open["ok"])
        for operation in ("mail.search", "mail.read"):
            obligation = advance_action_obligation(
                obligation,
                operation=operation,
                mode="read",
                ok=True,
                payload={},
                evidence_turn_id="turn-1",
            )
        terminal = advance_action_obligation(
            obligation,
            operation="nextcloud.calendar.from-mail-preview",
            mode="read",
            ok=True,
            payload={"decision": "information-required"},
            evidence_turn_id="turn-1",
        )
        self.assertEqual(terminal["terminal_state"], "information-required")
        self.assertTrue(action_completion_guard(terminal, "Die Endzeit fehlt.")["ok"])

    def test_second_candidate_failure_is_partial_and_never_completed(self) -> None:
        obligation = _ready_obligation()
        first = advance_action_obligation(
            obligation,
            operation="nextcloud.calendar.from-mail-create",
            mode="write",
            ok=True,
            postcondition_verified=True,
            argument_digest="1" * 64,
            evidence_turn_id="turn-1",
        )
        second = advance_action_obligation(
            first,
            operation="nextcloud.calendar.from-mail-create",
            mode="write",
            ok=False,
            error="operation-failed",
            argument_digest="2" * 64,
            evidence_turn_id="turn-1",
        )
        self.assertEqual(second["completed_targets"], 1)
        self.assertEqual(second["terminal_state"], "blocked")
        self.assertNotEqual(second["terminal_state"], "completed")

    def test_missing_or_stale_approval_is_a_terminal_non_execution_state(self) -> None:
        prompt = "Sende die bereits vollstaendig angezeigte Mail jetzt."
        obligation = build_action_obligation(
            prompt,
            route_intent(prompt),
            turn_id="turn-mail-send",
        )
        assert obligation is not None
        terminal = advance_action_obligation(
            obligation,
            operation="mail.compose-send",
            mode="write",
            ok=False,
            error="approval-required",
            evidence_turn_id="turn-mail-send",
        )
        self.assertEqual(terminal["terminal_state"], "approval-required")
        self.assertEqual(terminal["completed_targets"], 0)
        self.assertEqual(terminal["last_error"], "missing-or-stale-bound-approval")
        verdict = action_completion_guard(
            terminal,
            "Bitte gib /approve ein; dann versende ich die Mail.",
        )
        self.assertFalse(verdict["ok"])
        self.assertIn("stale-or-unbound-approval-command", verdict["issues"])

    def test_blocked_action_rejects_unbound_yes_no_retry_loop(self) -> None:
        prompt = "Trage Hinflug und Rueckflug aus der Mail als Termine ein."
        obligation = build_action_obligation(
            prompt,
            route_intent(prompt),
            turn_id="turn-calendar-retry",
        )
        assert obligation is not None
        blocked = advance_action_obligation(
            obligation,
            operation="mail.search",
            mode="read",
            ok=False,
            error="tool-call-limit",
            evidence_turn_id="turn-calendar-retry",
        )
        verdict = action_completion_guard(
            blocked,
            (
                "Da meine vorherigen Versuche durch ein System-Limit blockiert wurden, "
                "konnte ich sie nicht speichern. Soll ich es jetzt noch einmal versuchen, "
                "die genauen Flugzeiten einzutragen?"
            ),
        )
        self.assertFalse(verdict["ok"])
        self.assertIn("unbound-yes-no-retry-question", verdict["issues"])

    def test_mail_draft_requires_a_later_send_approval_and_is_not_completion(self) -> None:
        prompt = "Sende eine Mail an test@example.invalid."
        obligation = build_action_obligation(
            prompt,
            route_intent(prompt),
            turn_id="turn-mail-draft",
        )
        assert obligation is not None
        terminal = advance_action_obligation(
            obligation,
            operation="mail.compose-draft",
            mode="local-write",
            ok=True,
            postcondition_verified=True,
            payload={"ok": True, "draft_id": "synthetic-draft"},
            evidence_turn_id="turn-mail-draft",
        )
        self.assertEqual(terminal["terminal_state"], "approval-required")
        self.assertEqual(terminal["completed_targets"], 0)
        self.assertEqual(terminal["write_operations"], [])
        self.assertEqual(terminal["last_error"], "presented-draft-send-approval-required")

    def test_explicit_send_of_presented_draft_can_complete_directly(self) -> None:
        prompt = "Sende den bereits vollstaendig angezeigten Mailentwurf jetzt."
        obligation = build_action_obligation(
            prompt,
            route_intent(prompt),
            turn_id="turn-mail-approved",
        )
        assert obligation is not None
        terminal = advance_action_obligation(
            obligation,
            operation="mail.compose-send",
            mode="write",
            ok=True,
            postcondition_verified=True,
            argument_digest="d" * 64,
            payload={"ok": True, "draft_id": "synthetic-draft", "status": "completed"},
            evidence_turn_id="turn-mail-approved",
        )
        self.assertEqual(terminal["terminal_state"], "completed")
        self.assertEqual(terminal["completed_targets"], 1)
        self.assertEqual(terminal["write_operations"], ["mail.compose-send"])


class CalendarFromMailPreviewTests(unittest.TestCase):
    def test_two_flights_are_extracted_with_explicit_timezones_and_provenance(self) -> None:
        preview = build_calendar_mail_preview(
            _message(), resource_id="calendar-synthetic", default_timezone="Europe/Berlin"
        )
        self.assertTrue(preview["ok"])
        self.assertTrue(preview["complete"])
        self.assertEqual(preview["decision"], "ready")
        self.assertEqual(preview["candidate_count"], 2)
        outbound, returning = preview["candidates"]
        self.assertEqual(outbound["flight_number"], "EW4242")
        self.assertEqual(outbound["timezone_start"], "Europe/Berlin")
        self.assertEqual(outbound["timezone_end"], "Atlantic/Canary")
        self.assertEqual(returning["flight_number"], "EW4343")
        self.assertEqual(returning["timezone_start"], "Atlantic/Canary")
        self.assertEqual(returning["timezone_end"], "Europe/Berlin")
        self.assertEqual(outbound["field_provenance"]["flight_number"], "mail-body:flight-number")

    def test_missing_fields_abstain_instead_of_being_invented(self) -> None:
        preview = build_calendar_mail_preview(
            _message("Hinflug:\n06.02.2027\nDUS -> SPC\n11:05\n"),
            resource_id="calendar-synthetic",
            default_timezone="Europe/Berlin",
        )
        self.assertTrue(preview["ok"])
        self.assertFalse(preview["complete"])
        self.assertEqual(preview["decision"], "information-required")
        self.assertIn("start/end-time", preview["candidates"][0]["missing_fields"])
        self.assertIn("flight-number", preview["candidates"][0]["missing_fields"])

    def test_preview_digest_binds_exact_source_and_candidate(self) -> None:
        preview = build_calendar_mail_preview(
            _message(), resource_id="calendar-synthetic", default_timezone="Europe/Berlin"
        )
        candidate = select_unchanged_candidate(
            preview,
            expected_preview_digest=preview["preview_digest"],
            candidate_id=preview["candidates"][0]["candidate_id"],
        )
        self.assertEqual(candidate["uid"], preview["candidates"][0]["uid"])
        with self.assertRaises(PermissionError):
            select_unchanged_candidate(
                preview,
                expected_preview_digest="0" * 64,
                candidate_id=preview["candidates"][0]["candidate_id"],
            )

    def test_same_source_replays_to_same_candidate_ids_and_digest(self) -> None:
        first = build_calendar_mail_preview(
            _message(), resource_id="calendar-synthetic", default_timezone="Europe/Berlin"
        )
        second = build_calendar_mail_preview(
            _message(), resource_id="calendar-synthetic", default_timezone="Europe/Berlin"
        )
        self.assertEqual(first["preview_digest"], second["preview_digest"])
        self.assertEqual(
            [row["candidate_id"] for row in first["candidates"]],
            [row["candidate_id"] for row in second["candidates"]],
        )


class CalendarFromMailServiceTests(unittest.TestCase):
    def _assistant(self, message: ParsedMessage) -> PersonalAssistant:
        assistant = object.__new__(PersonalAssistant)
        assistant.tool_settings = SimpleNamespace(
            nextcloud=SimpleNamespace(
                calendar=DirectCalendarToolSettings(
                    enabled=True,
                    resource_id="calendar-synthetic",
                    allow_create=True,
                    allow_list=True,
                    timezone="Europe/Berlin",
                )
            ),
            security=SimpleNamespace(
                antivirus=AntivirusToolSettings(
                    enabled=True,
                    fail_closed=True,
                    scan_raw_mail=True,
                    scan_attachments=True,
                )
            ),
        )
        assistant.mail_move_service = SimpleNamespace(
            read_message=lambda folder, message_id, expected_subject="": message
        )
        assistant.antivirus = SimpleNamespace(
            scan_bytes=lambda data, name, source_type: SimpleNamespace(
                clean=True, signature="", status="clean"
            )
        )
        assistant.registry = SimpleNamespace(
            get=lambda resource_id: Resource(
                id=resource_id,
                kind="calendar",
                connector="nextcloud",
                remote_id="/calendars/synthetic/",
                permissions=("read", "create"),
                metadata={"name": "Synthetic"},
            )
        )
        assistant.nextcloud_calendar = SimpleNamespace(find_events_by_uid=lambda collection, uid: [])
        return assistant

    def test_create_recomputes_source_and_executes_exactly_one_candidate(self) -> None:
        assistant = self._assistant(_message())
        preview = assistant.calendar_from_mail_preview(
            folder="Agent/Test",
            message_id="synthetic-42",
            expected_subject="Synthetische Flugbestätigung",
        )
        calls: list[dict] = []

        def create(**kwargs):
            calls.append(kwargs)
            return {"ok": True, "postcondition_verified": True, "remote": {"etag": '"etag"'}}

        assistant.calendar_create = create
        selected = preview["candidates"][1]
        result = assistant.calendar_from_mail_create(
            folder="Agent/Test",
            message_id="synthetic-42",
            expected_subject="Synthetische Flugbestätigung",
            preview_digest=preview["preview_digest"],
            candidate_id=selected["candidate_id"],
            approved=True,
        )
        self.assertTrue(result["ok"])
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["uid"], selected["uid"])
        self.assertTrue(result["single_candidate"])
        self.assertFalse(result["bulk_write"])

    def test_changed_mail_blocks_stale_preview(self) -> None:
        current = [_message()]
        assistant = self._assistant(current[0])
        assistant.mail_move_service = SimpleNamespace(
            read_message=lambda folder, message_id, expected_subject="": current[0]
        )
        preview = assistant.calendar_from_mail_preview(
            folder="Agent/Test",
            message_id="synthetic-42",
            expected_subject="Synthetische Flugbestätigung",
        )
        current[0] = _message(_corpus()["synthetic_mail"]["body"].replace("11:05", "11:35"))
        with self.assertRaises(PermissionError):
            assistant.calendar_from_mail_create(
                folder="Agent/Test",
                message_id="synthetic-42",
                expected_subject="Synthetische Flugbestätigung",
                preview_digest=preview["preview_digest"],
                candidate_id=preview["candidates"][0]["candidate_id"],
                approved=True,
            )

    def test_antivirus_block_stops_before_calendar_lookup(self) -> None:
        assistant = self._assistant(_message())
        calendar_calls: list[str] = []
        assistant.nextcloud_calendar = SimpleNamespace(
            find_events_by_uid=lambda collection, uid: calendar_calls.append(uid)
        )
        assistant.antivirus = SimpleNamespace(
            scan_bytes=lambda data, name, source_type: SimpleNamespace(
                clean=False, signature="Synthetic.Test", status="infected"
            )
        )
        with self.assertRaisesRegex(PermissionError, "Virenscanner"):
            assistant.calendar_from_mail_preview(
                folder="Agent/Test",
                message_id="synthetic-42",
                expected_subject="Synthetische Flugbestätigung",
            )
        self.assertEqual(calendar_calls, [])

    def test_raw_mail_and_each_physical_attachment_are_scanned(self) -> None:
        message = _message()
        message.raw = (
            "From: Synthetic <noreply@example.invalid>\r\n"
            "Subject: Synthetische Flugbestätigung\r\n"
            "MIME-Version: 1.0\r\n"
            "Content-Type: multipart/mixed; boundary=M15\r\n\r\n"
            "--M15\r\nContent-Type: text/plain; charset=utf-8\r\n\r\n"
            + message.body_text
            + "\r\n--M15\r\nContent-Type: text/plain\r\n"
            "Content-Disposition: attachment; filename=flight.txt\r\n\r\n"
            "synthetic attachment\r\n--M15\r\nContent-Type: text/calendar\r\n\r\n"
            "BEGIN:VCALENDAR\r\nEND:VCALENDAR\r\n--M15--\r\n"
        ).encode()
        assistant = self._assistant(message)
        sources: list[str] = []

        def scan(data, name, source_type):
            sources.append(source_type)
            return SimpleNamespace(clean=True, signature="", status="clean")

        assistant.antivirus = SimpleNamespace(scan_bytes=scan)
        preview = assistant.calendar_from_mail_preview(
            folder="Agent/Test",
            message_id="synthetic-42",
            expected_subject="Synthetische Flugbestätigung",
        )
        self.assertTrue(preview["antivirus"]["all_clean"])
        self.assertEqual(preview["antivirus"]["attachment_count"], 2)
        self.assertEqual(
            sources,
            [
                "calendar-from-mail-raw",
                "calendar-from-mail-attachment",
                "calendar-from-mail-attachment",
            ],
        )


class CalendarPostconditionTests(unittest.TestCase):
    def _service(self, current: CalendarObject) -> ActionService:
        payload = {
            "uid": current.uid,
            "title": current.summary,
            "starts_at": "2027-02-06T10:05:00+00:00",
            "ends_at": "2027-02-06T15:10:00+00:00",
            "location": current.location,
            "description": current.description,
        }
        action = ActionPlan(
            id="action-m15",
            idempotency_key="m15",
            action_type="calendar.create",
            resource_id="calendar-synthetic",
            payload=payload,
            status="completed",
            requires_approval=True,
            created_at="2026-09-13T00:00:00+00:00",
            updated_at="2026-09-13T00:00:00+00:00",
        )
        service = ActionService.__new__(ActionService)
        service.storage = SimpleNamespace(
            get_action=lambda action_id: action,
            audit=lambda event, payload, **kwargs: None,
        )
        service.registry = SimpleNamespace(
            get=lambda resource_id: Resource(
                id=resource_id,
                kind="calendar",
                connector="nextcloud",
                remote_id="/calendars/synthetic/",
                permissions=("read", "create"),
            )
        )
        service.calendar = SimpleNamespace(find_events_by_uid=lambda collection, uid: [current])
        return service

    def test_remote_uid_etag_and_fields_are_required(self) -> None:
        current = CalendarObject(
            uid="assistant-flight-synthetic@local",
            summary="Flug EW4242 DUS–SPC",
            starts_at="2027-02-06T10:05:00Z",
            ends_at="2027-02-06T15:10:00Z",
            description="Synthetische Quelle",
            location="DUS → SPC",
            status="CONFIRMED",
            recurring=False,
            all_day=False,
            raw_ics="BEGIN:VCALENDAR",
            href="/calendars/synthetic/event.ics",
            etag='"m15"',
        )
        self.assertEqual(self._service(current).verify_calendar_create("action-m15"), current)
        without_etag = replace(current, etag="")
        with self.assertRaisesRegex(RuntimeError, "etag"):
            self._service(without_etag).verify_calendar_create("action-m15")


class PluginActionRuntimeTests(unittest.TestCase):
    def test_node_runtime_enforces_promise_guard_and_turn_binding(self) -> None:
        script = r"""
import {
  advanceActionObligation, buildActionObligation, guardActionCompletion, makeEvidence, routePrompt
} from './docker/openclaw-personal-assistant-plugin/runtime.js';
import contract from './docker/openclaw-personal-assistant-plugin/generated-tools.json' with {type:'json'};
const prompt = 'Trage Hinflug und Rueckflug aus der Mail als Termine in den Kalender ein.';
const route = routePrompt(contract, prompt);
const base = buildActionObligation(contract, prompt, route, 'turn-1');
const promise = guardActionCompletion(contract, base, 'Ich trage sie jetzt ein. Einen Moment bitte.');
const previewPrompt = 'Zeige zuerst eine Vorschau der Termine aus der Mail.';
const previewRoute = routePrompt(contract, previewPrompt);
const preview = buildActionObligation(contract, previewPrompt, previewRoute, 'turn-2');
const op = contract.operations.find((row) => row.tool_id === 'nextcloud.calendar.from-mail-create');
let ready = base;
for (const [id, payload] of [
  ['mail.search', {ok:true,complete:true,results:[{folder:'INBOX',mailbox_id:'42'}]}],
  ['mail.read', {ok:true,complete:true,subject:'Synthetic'}],
  ['nextcloud.calendar.from-mail-preview', {ok:true,decision:'ready',candidate_count:2}],
]) {
  const readOp = contract.operations.find((row) => row.tool_id === id);
  const evidence = makeEvidence(readOp, {returncode:0,stderr:''}, payload, 'turn-1', `call-${id}`);
  ready = advanceActionObligation(contract, ready, readOp, evidence, payload, '');
}
const okEvidence = makeEvidence(op, {returncode:0,stderr:''}, {
  ok:true, postcondition_verified:true, remote:{uid:'synthetic',etag:'etag'}
}, 'turn-1', 'call-1');
const oldEvidence = {...okEvidence, turn_id:'turn-old'};
const unchanged = advanceActionObligation(contract, ready, op, oldEvidence, {ok:true}, 'a'.repeat(64));
const jumped = advanceActionObligation(contract, base, op, okEvidence, {ok:true}, 'a'.repeat(64));
const first = advanceActionObligation(contract, ready, op, okEvidence, {ok:true}, 'a'.repeat(64));
const secondEvidence = {...okEvidence, turn_id:'turn-1', run_id:'second'};
const second = advanceActionObligation(contract, first, op, secondEvidence, {ok:true}, 'b'.repeat(64));
const unverifiedEvidence = makeEvidence(op, {returncode:0,stderr:''}, {
  ok:true, postcondition_verified:false, delivery_uncertain:true
}, 'turn-1', 'call-3');
const blocked = advanceActionObligation(contract, ready, op, unverifiedEvidence, {ok:true}, 'c'.repeat(64));
console.log(JSON.stringify({promise, preview, unchanged, jumped, first, second, blocked}));
"""
        result = subprocess.run(
            ["node", "--input-type=module", "-e", script],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        payload = json.loads(result.stdout)
        self.assertFalse(payload["promise"]["ok"])
        self.assertIsNone(payload["preview"])
        self.assertEqual(payload["unchanged"]["completed_targets"], 0)
        self.assertEqual(payload["jumped"]["last_error"], "unexpected-workflow-step")
        self.assertIsNone(payload["first"]["terminal_state"])
        self.assertEqual(payload["first"]["completed_targets"], 1)
        self.assertEqual(payload["second"]["terminal_state"], "completed")
        self.assertEqual(payload["blocked"]["last_error"], "write-postcondition-unverified")

    def test_m15_deterministic_benchmark_has_no_external_writes(self) -> None:
        result = subprocess.run(
            [sys.executable, str(ROOT / "scripts/benchmark-m15.py")],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["passed"], payload["case_count"])
        self.assertEqual(payload["external_writes"], 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
