from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from mail_agent.app import MailAgent
from mail_agent.cli import build_parser as build_mail_parser
from mail_agent.config import load_config
from mail_agent.models import Classification, Envelope, OperationResult
from mail_agent.parser import parse_eml
from mail_agent.review import REVIEW_REASON_VALUES, ReviewReason, parse_review_reason
from mail_agent.review_service import ReviewService
from mail_agent.rules import RuleContext
from mail_agent.storage import Storage
from personal_assistant.cli import parser as build_assistant_parser
from personal_assistant.tool_registry import build_tool_registry
from personal_assistant.tool_settings import ToolSettings

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

    def test_relevant_without_forward_gate_routes_to_general_review(self) -> None:
        result = self._route(Classification("relevant", 0.99, 9, False, "Relevant"))
        self.assertEqual(result.destination, self.config.folders.review)

    def test_relevant_below_importance_gate_routes_to_general_review(self) -> None:
        result = self._route(Classification("relevant", 0.99, 6, True, "Nicht dringend"))
        self.assertEqual(result.destination, self.config.folders.review)

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


class ReviewReadOnlyToolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.storage = Storage(Path(self.temp.name) / "mail.sqlite3")
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
        self.assertIsNone(result["next_tool"])
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
        settings = ToolSettings(path=Path(self.temp.name) / "tools.toml")
        settings.mail.move.enabled = True
        tools = {item.id: item for item in build_tool_registry(settings)}
        self.assertEqual(
            {"mail.review.status", "mail.review.list", "mail.review.suggest"} - tools.keys(),
            set(),
        )
        self.assertEqual(tools["mail.review.suggest"].mode, "read")
        self.assertFalse(tools["mail.review.suggest"].writes_external_data)
