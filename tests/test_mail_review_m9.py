from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from mail_agent.app import MailAgent
from mail_agent.config import load_config
from mail_agent.models import Classification, Envelope, OperationResult
from mail_agent.parser import parse_eml
from mail_agent.review import REVIEW_REASON_VALUES, ReviewReason, parse_review_reason

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
