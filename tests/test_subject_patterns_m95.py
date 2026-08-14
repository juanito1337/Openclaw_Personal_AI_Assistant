from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from mail_agent.learning_quality import LearningQualityAnalyzer
from mail_agent.models import Envelope
from mail_agent.parser import parse_eml
from mail_agent.storage import Storage
from mail_agent.utils import (
    SUBJECT_PATTERN_VERSION_CURRENT,
    normalize_subject_pattern,
    normalize_subject_pattern_v1,
)


def message(*, sender: str = "sender@example.test", subject: str, message_id: str):
    raw = (
        f"From: Sender <{sender}>\r\n"
        "To: Jan <jan@example.test>\r\n"
        f"Subject: {subject}\r\n"
        f"Message-ID: <{message_id}>\r\n"
        "Content-Type: text/plain; charset=utf-8\r\n\r\n"
        "Lokaler Testinhalt.\r\n"
    ).encode()
    return parse_eml(raw, Envelope(message_id), "INBOX")


class SubjectPatternGoldenTests(unittest.TestCase):
    def test_german_invoice_date_time_amount_and_reply_prefixes(self) -> None:
        subject = (
            "AW: Re: Rechnung Nr. RE-2026-000471 vom 14.08.2026 "
            "um 14:35 Uhr über 1.234,56 EUR"
        )
        self.assertEqual(
            normalize_subject_pattern(subject),
            "invoice <invoice-id> vom <date> um <time> über <amount>",
        )

    def test_english_invoice_date_time_and_amount(self) -> None:
        subject = (
            "Fwd: Invoice number INV_2026_9911 due August 14, 2026 "
            "at 2:30 PM - USD 1,234.56"
        )
        self.assertEqual(
            normalize_subject_pattern(subject),
            "invoice <invoice-id> due <date> at <time> - <amount>",
        )

    def test_order_tracking_uuid_and_long_numeric_ids_are_typed(self) -> None:
        self.assertEqual(
            normalize_subject_pattern(
                "Bestellung #ORD-884411 / Sendungsnummer 1Z999AA10123456784"
            ),
            "order <order-id> / tracking <tracking-id>",
        )
        self.assertEqual(
            normalize_subject_pattern(
                "Vorgang 123456789 / 550e8400-e29b-41d4-a716-446655440000"
            ),
            "vorgang <id> / <uuid>",
        )

    def test_unicode_empty_and_long_subjects_are_deterministic_and_bounded(self) -> None:
        self.assertEqual(
            normalize_subject_pattern("RE: Grüße aus München – Status 4711"),
            "grüße aus münchen – status <n>",
        )
        self.assertEqual(normalize_subject_pattern(""), "")
        long_subject = "Status " + ("ä" * 800) + " 123456789"
        first = normalize_subject_pattern(long_subject)
        self.assertEqual(first, normalize_subject_pattern(long_subject))
        self.assertLessEqual(len(first), 500)

    def test_v1_is_frozen_and_unknown_versions_fail(self) -> None:
        subject = "Invoice number INV_2026_9911 due August 14, 2026"
        self.assertEqual(
            normalize_subject_pattern_v1(subject),
            "invoice number inv_2026_9911 due august <n>, <n>",
        )
        with self.assertRaisesRegex(ValueError, "Betreffmusterversion"):
            normalize_subject_pattern(subject, version=99)


class SubjectPatternVersioningTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.storage = Storage(Path(self.temp.name) / "mail.sqlite3")

    def tearDown(self) -> None:
        self.storage.close()
        self.temp.cleanup()

    def add(self, subject: str, message_id: str, verdict: str) -> None:
        item = message(subject=subject, message_id=message_id)
        self.storage.record_feedback(item, verdict, "Agent/Korrektur")

    def test_new_feedback_is_versioned_without_rewriting_legacy_rows(self) -> None:
        self.storage.connection.execute(
            """
            INSERT INTO feedback (
                stable_key, verdict, sender_addr, sender_domain, subject,
                subject_signature, subject_pattern, pattern_version,
                source_folder, correction_folder, created_at
            ) VALUES ('legacy', 'routine', 'sender@example.test', 'example.test',
                'Invoice INV_2026_1001', 'invoice inv_2026_1001',
                'invoice inv_2026_1001', 1, 'legacy', 'legacy', '2026-01-01')
            """
        )
        self.storage.connection.commit()
        self.add("Invoice INV_2026_1002", "new@example.test", "routine")
        rows = self.storage.connection.execute(
            "SELECT stable_key, subject_pattern, pattern_version FROM feedback ORDER BY id"
        ).fetchall()
        self.assertEqual(rows[0]["pattern_version"], 1)
        self.assertEqual(rows[0]["subject_pattern"], "invoice inv_2026_1001")
        self.assertEqual(rows[1]["pattern_version"], SUBJECT_PATTERN_VERSION_CURRENT)
        self.assertEqual(rows[1]["subject_pattern"], "invoice <invoice-id>")

        self.storage.close()
        self.storage = Storage(Path(self.temp.name) / "mail.sqlite3")
        legacy = self.storage.connection.execute(
            "SELECT subject_pattern, pattern_version FROM feedback WHERE stable_key='legacy'"
        ).fetchone()
        self.assertEqual(dict(legacy), {"subject_pattern": "invoice inv_2026_1001", "pattern_version": 1})

    def test_walk_forward_report_compares_both_versions_without_self_leakage(self) -> None:
        self.add("Invoice INV_2026_1001", "one@example.test", "routine")
        self.add("Invoice INV_2026_1002", "two@example.test", "routine")
        self.add("Invoice INV_2026_1003", "three@example.test", "routine")
        report = LearningQualityAnalyzer(self.storage).report(limit=100)
        versions = report["evaluation"]["subject_pattern_versions"]
        comparison = versions["comparison"]
        self.assertEqual(comparison["sample"], 3)
        self.assertEqual(comparison["version_1"]["predictions"], 0)
        self.assertEqual(comparison["version_2"]["predictions"], 1)
        self.assertEqual(comparison["version_2"]["correct"], 1)
        self.assertFalse(versions["self_test_leakage"])
        self.assertTrue(versions["activation_gate"]["allowed"])

    def test_candidate_is_runtime_blocked_when_safety_errors_worsen(self) -> None:
        self.add("Invoice INV_2026_1001", "one@example.test", "routine")
        self.add("Invoice INV_2026_1002", "two@example.test", "routine")
        self.add("Invoice INV_2026_1003", "three@example.test", "relevant")
        gate = self.storage.pattern_activation_status()
        self.assertFalse(gate["allowed"])
        self.assertFalse(gate["relevant_missed_not_worse"])

        candidate = message(
            subject="Invoice INV_2026_1004", message_id="candidate@example.test"
        )
        decision = self.storage.pattern_feedback_decision(candidate)
        self.assertIsNone(decision["verdict"])
        self.assertEqual(decision["count"], 0)
        self.assertFalse(decision["pattern_activation"]["allowed"])

    def test_invalid_persisted_pattern_version_fails_closed(self) -> None:
        self.add("Status 1001", "bad@example.test", "routine")
        self.storage.connection.execute("UPDATE feedback SET pattern_version=99")
        self.storage.connection.commit()
        database = self.storage.path
        self.storage.close()
        with self.assertRaisesRegex(RuntimeError, "Betreffmusterversionen"):
            Storage(database)
        repair = sqlite3.connect(database)
        repair.execute("UPDATE feedback SET pattern_version=2")
        repair.commit()
        repair.close()
        self.storage = Storage(database)


if __name__ == "__main__":
    unittest.main()
