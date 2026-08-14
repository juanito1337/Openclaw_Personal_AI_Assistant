from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from mail_agent.app import MailAgent, RunSummary
from mail_agent.cli import (
    _activate_relevant_folder,
    _productive_checks_with_folder_self_heal,
)
from mail_agent.cli import (
    build_parser as build_mail_parser,
)
from mail_agent.config import load_config
from mail_agent.learning import LearningFolderRegistry
from mail_agent.models import Classification, Envelope, OperationResult
from mail_agent.parser import parse_eml
from mail_agent.review import REVIEW_REASON_VALUES, ReviewReason, parse_review_reason
from mail_agent.review_service import ReviewService
from mail_agent.rules import RuleContext
from mail_agent.storage import Storage
from personal_assistant.cli import parser as build_assistant_parser
from personal_assistant.mail_move import MailMoveService
from personal_assistant.models import Resource
from personal_assistant.policy import PolicyEngine
from personal_assistant.registry import ResourceRegistry
from personal_assistant.storage import AssistantStorage
from personal_assistant.tool_registry import build_tool_registry
from personal_assistant.tool_settings import MailMoveToolSettings, ToolSettings

SAMPLE = b"""From: Service <service@example.test>\r
To: Jan <jan@example.test>\r
Subject: Statusinformation 1234\r
Message-ID: <review-route@example.test>\r
Date: Fri, 14 Aug 2026 10:00:00 +0200\r
Content-Type: text/plain; charset=utf-8\r
\r
Statusinformation fuer den Test.\r
"""


class MailReviewTaxonomyTests(unittest.TestCase):
    def test_taxonomy_is_closed_and_content_free(self) -> None:
        self.assertEqual(
            REVIEW_REASON_VALUES,
            {
                "classification-uncertain",
                "spam-below-threshold",
                "routine-below-threshold",
                "relevant-not-forwarded",
                "invoice-review",
                "appointment-review",
                "safety-blocked",
                "unknown-legacy",
            },
        )
        self.assertEqual(
            parse_review_reason(" ROUTINE-BELOW-THRESHOLD "),
            ReviewReason.ROUTINE_BELOW_THRESHOLD,
        )

    def test_unknown_or_empty_reason_is_rejected(self) -> None:
        for value in ("", "free-text-reason", "spam"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                parse_review_reason(value)


class ExistingMailReviewRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        source = Path(__file__).parents[1] / "mail_agent/config.example.toml"
        text = source.read_text(encoding="utf-8")
        text = text.replace("mail_agent/data/", str(root / "data") + "/")
        text = text.replace(
            'rules_file = "mail_agent/rules.toml"',
            f'rules_file = "{root / "rules.toml"}"',
        )
        config_path = root / "config.toml"
        config_path.write_text(text, encoding="utf-8")
        (root / "rules.toml").write_text(
            "[spam]\naddresses=[]\ndomains=[]\nsender_names=[]\nsubject_phrases=[]\n"
            "[important]\naddresses=[]\ndomains=[]\n"
            "[routine]\naddresses=[]\ndomains=[]\n",
            encoding="utf-8",
        )
        self.config = load_config(config_path)
        self.message = parse_eml(SAMPLE, Envelope("42"), "INBOX")
        self.agent = object.__new__(MailAgent)
        self.agent.config = self.config
        self.agent.dry_run = True
        self.agent._process_order_event = lambda *_args, **_kwargs: OperationResult(  # type: ignore[method-assign]
            True, "order-not-detected"
        )
        self.agent.invoices = Mock()
        self.agent.invoices.process.return_value = OperationResult(True, "not-an-invoice")
        self.agent.rules = Mock()
        self.agent.rules.is_trusted_sender.return_value = False
        self.agent.calendar = Mock()
        self.agent.calendar.process.return_value = OperationResult(True, "pending-review")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _route(self, classification: Classification) -> OperationResult:
        return self.agent._dry_route(self.message, classification)

    def _productive_route(self, classification: Classification) -> tuple[OperationResult, dict]:
        class MovingClient:
            @staticmethod
            def move_message(source, destination, message_id):
                return OperationResult(True, "moved", destination=destination)

        agent = object.__new__(MailAgent)
        agent.config = self.config
        agent.dry_run = False
        agent.storage = Storage(self.config.runtime.database)
        agent.storage.connection.execute("DELETE FROM actions")
        agent.storage.connection.execute("DELETE FROM messages")
        agent.storage.connection.commit()
        agent.telemetry = None
        agent.himalaya = MovingClient()
        agent._process_order_event = lambda *_args, **_kwargs: OperationResult(  # type: ignore[method-assign]
            True, "order-not-detected"
        )
        agent.invoices = Mock()
        agent.invoices.process.return_value = OperationResult(True, "not-an-invoice")
        agent.rules = Mock()
        agent.rules.is_trusted_sender.return_value = False
        agent.calendar = Mock()
        agent.calendar.process.return_value = OperationResult(True, "pending-review")
        agent.notifier = Mock()
        try:
            result = agent._route(self.message, classification, "INBOX")
            row = dict(agent.storage.get_message(self.message.stable_key))
            return result, row
        finally:
            agent.storage.close()

    def test_uncertain_classification_routes_to_general_review(self) -> None:
        result = self._route(Classification("uncertain", 0.60, 5, False, "Unklar"))
        self.assertEqual(result.destination, self.config.folders.review)

    def test_legitimacy_safety_guard_routes_to_general_review(self) -> None:
        result = self._route(
            Classification(
                "uncertain", 0.69, 5, False, "Spam blockiert", source="legitimacy-guard"
            )
        )
        self.assertEqual(result.destination, self.config.folders.review)

    def test_spam_below_threshold_routes_to_general_review(self) -> None:
        result = self._route(Classification("spam", 0.94, 1, False, "Unsicherer Spam"))
        self.assertEqual(result.destination, self.config.folders.review)

    def test_routine_below_threshold_routes_to_general_review(self) -> None:
        result = self._route(Classification("routine", 0.89, 2, False, "Unsichere Routine"))
        self.assertEqual(result.destination, self.config.folders.review)

    def test_relevant_without_forward_gate_routes_to_relevant_folder(self) -> None:
        result = self._route(Classification("relevant", 0.99, 9, False, "Relevant"))
        self.assertEqual(result.destination, self.config.folders.relevant)

    def test_relevant_below_importance_gate_routes_to_relevant_folder(self) -> None:
        result = self._route(Classification("relevant", 0.99, 6, True, "Nicht dringend"))
        self.assertEqual(result.destination, self.config.folders.relevant)

    def test_invoice_review_routes_to_general_review(self) -> None:
        self.agent.invoices.process.return_value = OperationResult(
            True, "invoice-review-required", "Mehrere PDFs"
        )
        result = self._route(Classification("routine", 0.99, 2, False, "Rechnung"))
        self.assertEqual(result.destination, self.config.folders.review)

    def test_invoice_failure_routes_to_error(self) -> None:
        self.agent.invoices.process.return_value = OperationResult(
            False, "invoice-failed", "Archiv nicht erreichbar"
        )
        result = self._route(Classification("routine", 0.99, 2, False, "Rechnung"))
        self.assertEqual(result.destination, self.config.folders.error)

    def test_appointment_pending_routes_to_appointment_review(self) -> None:
        result = self._route(Classification("appointment", 0.99, 8, True, "Termin"))
        self.assertEqual(result.destination, self.config.folders.appointment_review)

    def test_appointment_failure_routes_to_error(self) -> None:
        self.agent.calendar.process.return_value = OperationResult(
            False, "calendar-error", "Kalender fehlt"
        )
        result = self._route(Classification("appointment", 0.99, 8, True, "Termin"))
        self.assertEqual(result.destination, self.config.folders.error)

    def test_productive_relevant_route_matches_dry_run_and_records_reason(self) -> None:
        classification = Classification("relevant", 0.99, 9, False, "Relevant")
        dry = self._route(classification)
        result, row = self._productive_route(classification)
        self.assertEqual(result.destination, dry.destination)
        self.assertEqual(result.status, "relevant")
        self.assertEqual(row["review_reason"], "relevant-not-forwarded")
        self.assertEqual(row["destination_folder"], self.config.folders.relevant)

    def test_productive_uncertain_and_threshold_routes_record_exact_reasons(self) -> None:
        cases = (
            (
                Classification("uncertain", 0.60, 5, False, "Unklar"),
                "classification-uncertain",
            ),
            (
                Classification("uncertain", 0.69, 5, False, "Blockiert", source="legitimacy-guard"),
                "safety-blocked",
            ),
            (Classification("spam", 0.94, 1, False, "Knapp"), "spam-below-threshold"),
            (Classification("routine", 0.89, 3, False, "Knapp"), "routine-below-threshold"),
        )
        for classification, expected in cases:
            with self.subTest(expected=expected):
                result, row = self._productive_route(classification)
                self.assertEqual(result.destination, self.config.folders.review)
                self.assertEqual(row["review_reason"], expected)

    def test_legacy_config_loads_but_productive_run_requires_explicit_activation(self) -> None:
        legacy_path = Path(self.temp.name) / "legacy.toml"
        config_text = self.config.path.read_text(encoding="utf-8").replace(
            'relevant = "Agent/Relevant"\n',
            "",
        )
        legacy_path.write_text(config_text, encoding="utf-8")
        legacy = load_config(legacy_path)
        self.assertEqual(legacy.folders.relevant, "")
        agent = object.__new__(MailAgent)
        agent.config = legacy
        summary = RunSummary()
        self.assertFalse(agent._prepare_run(summary))
        self.assertIn("aktiviert", summary.errors[0])

    def test_folder_plan_is_read_only_and_reports_missing_relevant_folder(self) -> None:
        agent = object.__new__(MailAgent)
        agent.config = self.config
        agent.learning_folders = Mock()
        agent.learning_folders.active_folders.return_value = []
        agent.himalaya = Mock()
        agent.himalaya.list_folders.return_value = (["INBOX", self.config.folders.review], "")
        plan = agent.folder_plan()
        self.assertTrue(plan["read_only"])
        self.assertFalse(plan["activation_required"])
        self.assertIn(self.config.folders.relevant, plan["missing"])
        agent.himalaya.ensure_folders.assert_not_called()

    def test_productive_preflight_never_auto_creates_new_relevant_folder(self) -> None:
        agent = Mock()
        agent.config.folders.relevant = self.config.folders.relevant
        checks = {
            "folders": {
                "ok": False,
                "missing": [self.config.folders.relevant],
            }
        }
        agent.doctor.return_value = checks
        result = _productive_checks_with_folder_self_heal(agent)
        self.assertIs(result, checks)
        self.assertTrue(result["folders"]["explicit_activation_required"])
        agent.setup.assert_not_called()

    def test_explicit_relevant_activation_changes_config_and_creates_only_target(self) -> None:
        legacy_text = self.config.path.read_text(encoding="utf-8").replace(
            'relevant = "Agent/Relevant"\n',
            "",
        )
        self.config.path.write_text(legacy_text, encoding="utf-8")
        agent = object.__new__(MailAgent)
        agent.config = load_config(self.config.path)
        agent.himalaya = Mock()
        agent.himalaya.ensure_folders.return_value = [
            OperationResult(True, "exists", destination="Agent"),
            OperationResult(True, "created", destination="Agent/Relevant"),
        ]
        agent.himalaya.list_folders.return_value = (
            ["INBOX", "Agent", "Agent/Pruefen", "Agent/Relevant"],
            "",
        )

        result = _activate_relevant_folder(agent, "Agent/Relevant")

        self.assertTrue(result["ok"])
        self.assertEqual(result["moves_performed"], 0)
        self.assertTrue(result["external_change_may_persist_after_rollback"])
        self.assertEqual(load_config(self.config.path).folders.relevant, "Agent/Relevant")
        agent.himalaya.ensure_folders.assert_called_once_with(["Agent/Relevant"])

    def test_relevant_activation_rejects_existing_different_target(self) -> None:
        agent = object.__new__(MailAgent)
        agent.config = self.config
        agent.himalaya = Mock()

        with self.assertRaisesRegex(RuntimeError, "Konfigurationskonflikt"):
            _activate_relevant_folder(agent, "Agent/Andere-Ablage")

        agent.himalaya.ensure_folders.assert_not_called()

    def test_failed_relevant_activation_restores_local_configuration(self) -> None:
        original = self.config.path.read_text(encoding="utf-8").replace(
            'relevant = "Agent/Relevant"\n',
            "",
        )
        self.config.path.write_text(original, encoding="utf-8")
        agent = object.__new__(MailAgent)
        agent.config = load_config(self.config.path)
        agent.himalaya = Mock()
        agent.himalaya.ensure_folders.return_value = [
            OperationResult(False, "create-failed", "IMAP verweigert", "Agent/Relevant")
        ]
        agent.himalaya.list_folders.return_value = (["INBOX", "Agent/Pruefen"], "")

        result = _activate_relevant_folder(agent, "Agent/Relevant")

        self.assertFalse(result["ok"])
        self.assertTrue(result["configuration_restored"])
        self.assertEqual(self.config.path.read_text(encoding="utf-8"), original)
        self.assertEqual(agent.config.folders.relevant, "")


class FakeReviewClient:
    def __init__(self) -> None:
        self.folders = ["INBOX", "Agent/Pruefen"]
        self.envelope = Envelope(
            "42",
            "Statusinformation 1234",
            "Service",
            "service@example.test",
            "2026-08-14",
        )
        self.export_calls: list[tuple[str, str]] = []
        self.move_calls: list[tuple[str, str, str]] = []

    def list_folders(self):
        return list(self.folders), ""

    def list_envelopes(self, folder, limit=200):
        if folder == "Agent/Pruefen":
            return [self.envelope][:limit], ""
        return [], ""

    def export_message(self, folder, message_id, destination):
        self.export_calls.append((folder, message_id))
        destination.write_bytes(SAMPLE)
        return OperationResult(True, "exported", path=str(destination))

    def move_message(self, source, destination, message_id):
        self.move_calls.append((source, destination, message_id))
        return OperationResult(True, "moved", destination=destination)


class StaticReviewClassifier:
    def __init__(self, result: Classification) -> None:
        self.result = result
        self.calls = 0

    def classify(self, _message):
        self.calls += 1
        return self.result


class StaticReviewRules:
    def __init__(self, context: RuleContext | None = None) -> None:
        self.context = context or RuleContext()

    def evaluate(self, _message):
        return self.context


class FakeCorrectionClient:
    def __init__(self, config) -> None:
        self.config = config
        self.folders = [config.mailbox.source_folder, *config.folders.all()]
        self.messages = {
            config.folders.review: [
                Envelope(
                    "42",
                    "Statusinformation 1234",
                    "Service",
                    "service@example.test",
                    "2026-08-14",
                )
            ]
        }
        self.move_calls: list[tuple[str, str, str]] = []
        self.move_result = OperationResult(True, "moved")
        self.list_error = ""

    def list_folders(self):
        return list(self.folders), ""

    def list_envelopes(self, folder, limit=200):
        return list(self.messages.get(folder, []))[:limit], self.list_error

    def move_message(self, source, destination, message_id):
        self.move_calls.append((source, destination, message_id))
        if not self.move_result.ok:
            return self.move_result
        envelope = next(
            (item for item in self.messages.get(source, []) if item.mailbox_id == message_id),
            None,
        )
        if envelope is None:
            return OperationResult(False, "move-failed", "missing")
        self.messages[source].remove(envelope)
        self.messages.setdefault(destination, []).append(envelope)
        return OperationResult(True, "moved", destination=destination)


class ReviewReadOnlyToolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        source = Path(__file__).parents[1] / "mail_agent/config.example.toml"
        config_text = source.read_text(encoding="utf-8")
        config_text = config_text.replace("mail_agent/data/", str(root / "data") + "/")
        config_text = config_text.replace(
            'rules_file = "mail_agent/rules.toml"',
            f'rules_file = "{root / "rules.toml"}"',
        )
        config_text = config_text.replace(
            'learning_folders_file = "mail_agent/learning_folders.json"',
            f'learning_folders_file = "{root / "learning_folders.json"}"',
        )
        config_path = root / "config.toml"
        config_path.write_text(config_text, encoding="utf-8")
        (root / "rules.toml").write_text(
            "[spam]\naddresses=[]\ndomains=[]\nsender_names=[]\nsubject_phrases=[]\n"
            "[important]\naddresses=[]\ndomains=[]\n"
            "[routine]\naddresses=[]\ndomains=[]\n",
            encoding="utf-8",
        )
        self.config = load_config(config_path)
        self.storage = Storage(self.config.runtime.database)
        self.message = parse_eml(SAMPLE, Envelope("42"), "Agent/Pruefen")
        original = Classification("routine", 0.89, 3, False, "Knapp unter Schwelle", source="model")
        self.storage.upsert_message(self.message, original, status="review")
        self.storage.record_review(
            self.message.stable_key,
            ReviewReason.ROUTINE_BELOW_THRESHOLD,
            original,
            threshold=0.90,
        )
        self.client = FakeReviewClient()

    def tearDown(self) -> None:
        self.storage.close()
        self.temp.cleanup()

    def _service(
        self,
        classification: Classification | None = None,
        context: RuleContext | None = None,
    ) -> ReviewService:
        return ReviewService(
            self.storage,
            StaticReviewRules(context),  # type: ignore[arg-type]
            StaticReviewClassifier(  # type: ignore[arg-type]
                classification
                or Classification("routine", 0.97, 3, False, "Konsistentes Muster", source="feedback-pattern")
            ),
            self.client,  # type: ignore[arg-type]
        )

    def _correction_service(self):
        registry = ResourceRegistry(Path(self.temp.name) / "resources.toml")
        registry.resources["mail-agent"] = Resource(
            id="mail-agent",
            kind="tool",
            connector="local",
            permissions=("read", "move"),
        )
        assistant_storage = AssistantStorage(Path(self.temp.name) / "assistant.sqlite3")
        policy = PolicyEngine(Path(self.temp.name) / "policies.toml", registry)
        client = FakeCorrectionClient(self.config)
        service = MailMoveService(
            MailMoveToolSettings(enabled=True),
            registry,
            policy,
            assistant_storage,
            client,  # type: ignore[arg-type]
        )
        return service, assistant_storage, client

    def test_status_and_list_are_content_free_and_typed(self) -> None:
        status = self._service().status(days=7)
        self.assertEqual(status["reasons"], {"routine-below-threshold": 1})
        self.assertEqual(status["confidence_bands"], {"0.70-0.89": 1})
        self.assertNotIn("Statusinformation", str(status))
        listed = self._service().list("routine-below-threshold", limit=50)
        self.assertEqual(listed["messages"][0]["mailbox_id"], "42")
        self.assertNotIn("body_text", listed["messages"][0])
        self.assertNotIn("attachments", listed["messages"][0])
        self.assertTrue(listed["complete"])
        self.assertEqual(listed["folder_errors"], [])
        with self.assertRaises(ValueError):
            self._service().list("free-text", limit=50)

    def test_suggestion_exposes_original_evidence_and_no_side_effects(self) -> None:
        feedback_before = self.storage.connection.execute("SELECT COUNT(*) FROM feedback").fetchone()[0]
        result = self._service(
            context=RuleContext(
                notes=["Gemischter Absender; keine automatische Entscheidung."],
                prevent_spam=True,
            )
        ).suggest("Agent/Pruefen", "42", "Statusinformation 1234")
        feedback_after = self.storage.connection.execute("SELECT COUNT(*) FROM feedback").fetchone()[0]
        self.assertTrue(result["ok"])
        self.assertTrue(result["read_only"])
        self.assertEqual(result["original_decision"]["review_reason"], "routine-below-threshold")
        self.assertEqual(result["current_decision"]["source"], "feedback-pattern")
        self.assertTrue(result["uncertainty"]["conflict_or_mixed_sender"])
        self.assertEqual(result["next_step"], "request-explicit-review-correction")
        self.assertEqual(result["next_tool"], "mail.review.correct")
        self.assertEqual(feedback_before, feedback_after)
        self.assertEqual(self.client.move_calls, [])
        self.assertEqual(self.client.export_calls, [("Agent/Pruefen", "42")])

    def test_identity_guards_fail_before_classification(self) -> None:
        service = self._service()
        with self.assertRaises(ValueError):
            service.suggest("NichtDa", "42", "Statusinformation 1234")
        with self.assertRaises(ValueError):
            service.suggest("Agent/Pruefen", "999", "Statusinformation 1234")
        with self.assertRaises(PermissionError):
            service.suggest("Agent/Pruefen", "42", "Falscher Betreff")
        self.assertEqual(self.client.export_calls, [])

    def test_model_timeout_fallback_abstains(self) -> None:
        result = self._service(
            Classification(
                "uncertain",
                0.0,
                5,
                False,
                "Lokales Modell nicht verfuegbar; Timeout",
                source="fallback",
            )
        ).suggest("Agent/Pruefen", "42", "Statusinformation 1234")
        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "abstain")
        self.assertTrue(result["uncertainty"]["model_failure"])

    def test_review_correction_requires_exact_identity_source_verdict_and_approval(self) -> None:
        service, assistant_storage, client = self._correction_service()
        try:
            base = {
                "source": self.config.folders.review,
                "message_id": "42",
                "expected_subject": "Statusinformation 1234",
                "verdict": "routine",
            }
            with self.assertRaises(PermissionError):
                service.review_correct(**base)
            with self.assertRaises(PermissionError):
                service.review_correct(**{**base, "source": "INBOX"}, approved=True)
            with self.assertRaises(PermissionError):
                service.review_correct(**{**base, "expected_subject": "Falsch"}, approved=True)
            with self.assertRaises(ValueError):
                service.review_correct(**{**base, "verdict": "not_spam"}, approved=True)
            with self.assertRaises(ValueError):
                service.review_correct(**base, label="frei-erfunden", approved=True)
            with self.assertRaises(ValueError):
                service.review_correct(**{**base, "message_id": "999"}, approved=True)
            with self.assertRaises(PermissionError):
                service.move(
                    source=self.config.folders.review,
                    destination=self.config.folders.feedback_unimportant,
                    message_id="42",
                    expected_subject="Statusinformation 1234",
                )
            self.assertEqual(client.move_calls, [])
        finally:
            assistant_storage.close()

    def test_review_correction_moves_once_then_worker_records_feedback_once(self) -> None:
        service, assistant_storage, client = self._correction_service()
        try:
            arguments = {
                "source": self.config.folders.review,
                "message_id": "42",
                "expected_subject": "Statusinformation 1234",
                "verdict": "routine",
                "approved": True,
            }
            result = service.review_correct(**arguments)
            self.assertTrue(result["ok"])
            self.assertFalse(result["feedback_recorded"])
            repeated = service.review_correct(**arguments)
            self.assertTrue(repeated["duplicate"])
            self.assertEqual(len(client.move_calls), 1)

            agent = object.__new__(MailAgent)
            agent.config = self.config
            agent.storage = self.storage
            agent.himalaya = client
            agent.learning_folders = LearningFolderRegistry(self.config)
            agent.dry_run = False
            agent.telemetry = None
            agent.log = Mock()
            agent._load_message = (  # type: ignore[method-assign]
                lambda folder, envelope, _summary: parse_eml(SAMPLE, envelope, folder)
            )
            agent._route = (  # type: ignore[method-assign]
                lambda message, _classification, folder, force=False: client.move_message(
                    folder,
                    self.config.folders.routine,
                    message.mailbox_id,
                )
            )
            summary = RunSummary()
            agent._process_feedback(summary, limit=10)
            agent._process_feedback(RunSummary(), limit=10)
            count = self.storage.connection.execute(
                "SELECT COUNT(*) FROM feedback WHERE stable_key = ? AND verdict = 'routine'",
                (self.message.stable_key,),
            ).fetchone()[0]
            self.assertEqual(count, 1)
        finally:
            assistant_storage.close()

    def test_uncertain_move_is_not_reported_successful_or_retried(self) -> None:
        service, assistant_storage, client = self._correction_service()
        client.move_result = OperationResult(False, "move-uncertain", "Serverstatus unklar")
        try:
            arguments = {
                "source": self.config.folders.review,
                "message_id": "42",
                "expected_subject": "Statusinformation 1234",
                "verdict": "spam",
                "approved": True,
            }
            result = service.review_correct(**arguments)
            self.assertFalse(result["ok"])
            self.assertTrue(result["uncertain"])
            repeated = service.review_correct(**arguments)
            self.assertFalse(repeated["ok"])
            self.assertTrue(repeated["retry_blocked"])
            self.assertEqual(len(client.move_calls), 1)
        finally:
            assistant_storage.close()

    def test_imap_list_error_blocks_review_correction(self) -> None:
        service, assistant_storage, client = self._correction_service()
        client.list_error = "IMAP-Liste fehlgeschlagen"
        try:
            with self.assertRaisesRegex(RuntimeError, "IMAP-Liste"):
                service.review_correct(
                    source=self.config.folders.review,
                    message_id="42",
                    expected_subject="Statusinformation 1234",
                    verdict="routine",
                    approved=True,
                )
            self.assertEqual(client.move_calls, [])
        finally:
            assistant_storage.close()

    def test_optional_label_can_only_select_registered_active_folder(self) -> None:
        item = LearningFolderRegistry(self.config).create(
            parent="routine",
            name="Bestellungen",
            label="orders",
        )
        service, assistant_storage, client = self._correction_service()
        client.folders.append(item.folder)
        try:
            result = service.review_correct(
                source=self.config.folders.review,
                message_id="42",
                expected_subject="Statusinformation 1234",
                verdict="routine",
                label="orders",
                approved=True,
            )
            self.assertTrue(result["ok"])
            self.assertEqual(result["destination"], item.folder)
        finally:
            assistant_storage.close()

    def test_cli_and_typed_catalog_expose_same_review_commands(self) -> None:
        for argv in (
            ["review", "status", "--days", "7"],
            ["review", "list", "--reason", "unknown-legacy", "--limit", "10"],
            [
                "review",
                "suggest",
                "--folder",
                "Agent/Pruefen",
                "--message-id",
                "42",
                "--expected-subject",
                "Statusinformation 1234",
            ],
        ):
            parsed = build_mail_parser().parse_args(argv)
            self.assertEqual(parsed.command, "review")
        parsed_assistant = build_assistant_parser().parse_args(
            ["mail", "review", "status", "--days", "7"]
        )
        self.assertEqual(parsed_assistant.review_command, "status")
        folder_plan = build_assistant_parser().parse_args(["mail", "folders", "plan"])
        self.assertEqual(folder_plan.folders_command, "plan")
        folder_activation = build_assistant_parser().parse_args(
            [
                "mail",
                "folders",
                "activate-relevant",
                "--relevant",
                "Agent/Relevant",
                "--yes",
            ]
        )
        self.assertEqual(folder_activation.relevant, "Agent/Relevant")
        self.assertTrue(folder_activation.yes)
        correction = build_assistant_parser().parse_args(
            [
                "mail",
                "review",
                "correct",
                "--source",
                "Agent/Pruefen",
                "--message-id",
                "42",
                "--expected-subject",
                "Statusinformation 1234",
                "--verdict",
                "routine",
                "--yes",
            ]
        )
        self.assertTrue(correction.yes)
        settings = ToolSettings(path=Path(self.temp.name) / "tools.toml")
        settings.mail.move.enabled = True
        tools = {item.id: item for item in build_tool_registry(settings)}
        self.assertEqual(
            {
                "mail.review.status",
                "mail.review.list",
                "mail.review.suggest",
                "mail.review.correct",
                "mail.folders.plan",
                "mail.folders.apply",
                "mail.folders.activate-relevant",
            }
            - tools.keys(),
            set(),
        )
        self.assertEqual(tools["mail.review.suggest"].mode, "read")
        self.assertFalse(tools["mail.review.suggest"].writes_external_data)
        self.assertEqual(tools["mail.review.correct"].approval, "explicit-user-review-correction")
        self.assertEqual(tools["mail.folders.apply"].mode, "write")
        self.assertTrue(tools["mail.folders.apply"].writes_external_data)
        self.assertEqual(
            tools["mail.folders.activate-relevant"].approval,
            "explicit-user-configure-and-create-relevant-folder",
        )
