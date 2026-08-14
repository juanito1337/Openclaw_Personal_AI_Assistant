from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from mail_agent.learning_quality import LearningQualityAnalyzer
from mail_agent.models import Classification, Envelope
from mail_agent.parser import parse_eml
from mail_agent.storage import Storage
from personal_assistant.tool_registry import build_tool_registry
from personal_assistant.tool_settings import ToolSettings


def make_message(*, sender: str, subject: str, message_id: str):
    raw = (
        f"From: Sender <{sender}>\r\n"
        "To: Jan <jan@example.test>\r\n"
        f"Subject: {subject}\r\n"
        f"Message-ID: <{message_id}>\r\n"
        "Content-Type: text/plain; charset=utf-8\r\n\r\n"
        "Lokaler Testinhalt, der nicht exportiert werden darf.\r\n"
    ).encode()
    return parse_eml(raw, Envelope(message_id), "INBOX")


def classification(category: str) -> Classification:
    return Classification(
        category=category,
        confidence=0.8,
        importance=5,
        forward=category == "relevant",
        reason="Test",
    )


class LearningQualityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.storage = Storage(self.root / "mail.sqlite3")

    def tearDown(self) -> None:
        self.storage.close()
        self.temp.cleanup()

    def add(self, *, sender: str, subject: str, message_id: str, verdict: str, original: str) -> None:
        item = make_message(sender=sender, subject=subject, message_id=message_id)
        self.storage.upsert_message(item, classification(original), status="classified")
        self.storage.record_feedback(
            item,
            verdict,
            "Agent/Korrektur-Unwichtig" if verdict == "routine" else "Agent/Korrektur-Wichtig",
            label="newsletter" if verdict == "routine" else "security",
        )

    def seed_mixed_sender(self) -> None:
        sender = "mixed@example.test"
        self.add(
            sender=sender,
            subject="Newsletter Ausgabe 1001",
            message_id="one@example.test",
            verdict="routine",
            original="routine",
        )
        self.add(
            sender=sender,
            subject="Newsletter Ausgabe 1002",
            message_id="two@example.test",
            verdict="routine",
            original="routine",
        )
        self.add(
            sender=sender,
            subject="Dringende Sicherheitswarnung",
            message_id="three@example.test",
            verdict="relevant",
            original="routine",
        )
        self.add(
            sender=sender,
            subject="Newsletter Ausgabe 1003",
            message_id="four@example.test",
            verdict="routine",
            original="routine",
        )

    def test_walk_forward_pattern_learning_is_safer_than_sender_only(self) -> None:
        self.seed_mixed_sender()
        report = LearningQualityAnalyzer(self.storage).report(limit=100)
        baseline = report["evaluation"]["sender_only_baseline"]
        pattern = report["evaluation"]["pattern_learning"]
        self.assertEqual(baseline["relevant_missed"], 1)
        self.assertEqual(pattern["relevant_missed"], 0)
        self.assertGreater(pattern["accuracy_percent"], baseline["accuracy_percent"])
        self.assertEqual(report["data_quality"]["mixed_senders"], 1)
        self.assertFalse(report["privacy"]["mail_bodies_read"])
        self.assertFalse(report["privacy"]["report_contains_sender_addresses"])

    def test_original_decision_is_immutable_and_legacy_rows_abstain(self) -> None:
        item = make_message(
            sender="quality@example.test",
            subject="Hinweis 1001",
            message_id="quality-one@example.test",
        )
        self.storage.upsert_message(item, classification("routine"), status="classified")
        self.storage.record_feedback(item, "relevant", "Agent/Korrektur-Wichtig")
        row = self.storage.connection.execute(
            "SELECT original_category, original_source, original_snapshot_valid FROM feedback WHERE stable_key=?",
            (item.stable_key,),
        ).fetchone()
        self.assertEqual(row["original_category"], "routine")
        self.assertEqual(row["original_source"], "model")
        self.assertEqual(row["original_snapshot_valid"], 1)

        # A reversed correction keeps the original automated decision rather than
        # replacing it with the user-feedback routing decision.
        self.storage.upsert_message(
            item, Classification("relevant", 1.0, 9, True, "user", source="feedback"), status="forwarded"
        )
        self.storage.record_feedback(item, "routine", "Agent/Korrektur-Unwichtig")
        row = self.storage.connection.execute(
            "SELECT original_category, original_source, original_snapshot_valid FROM feedback WHERE stable_key=?",
            (item.stable_key,),
        ).fetchone()
        self.assertEqual(row["original_category"], "routine")
        self.assertEqual(row["original_source"], "model")
        self.assertEqual(row["original_snapshot_valid"], 1)

        legacy = make_message(
            sender="legacy@example.test", subject="Alt 1001", message_id="legacy@example.test"
        )
        self.storage.connection.execute(
            """INSERT INTO feedback (stable_key, verdict, sender_addr, sender_domain, subject,
               subject_signature, subject_pattern, source_folder, correction_folder, label,
               feature_json, original_snapshot_valid, created_at, metadata_json)
               VALUES (?, 'routine', ?, 'example.test', ?, 'alt <num>', 'alt <num>',
               'Agent/Korrektur-Unwichtig', 'Agent/Korrektur-Unwichtig', '', '{}', 0, ?, '{}')""",
            (legacy.stable_key, legacy.sender_addr, legacy.subject, "2026-07-24T00:00:00+00:00"),
        )
        self.storage.connection.commit()
        report = LearningQualityAnalyzer(self.storage).report(limit=100)
        original = report["evaluation"]["stored_original_decision"]
        self.assertEqual(original["samples"], 1)
        self.assertEqual(original["legacy_rows_without_snapshot"], 1)
        self.assertNotIn("stored_original_classifier", report["evaluation"])

    def test_pattern_evaluation_uses_two_hits_for_routine_but_one_for_relevant(self) -> None:
        sender = "threshold@example.test"
        self.add(sender=sender, subject="Newsletter 1001", message_id="t1@example.test", verdict="routine", original="routine")
        self.add(sender=sender, subject="Newsletter 1002", message_id="t2@example.test", verdict="relevant", original="routine")
        # Different sender avoids turning the whole sender mixed for the relevant protection case.
        sender2 = "security@example.test"
        self.add(sender=sender2, subject="Warnung 1001", message_id="t3@example.test", verdict="relevant", original="routine")
        self.add(sender=sender2, subject="Warnung 1002", message_id="t4@example.test", verdict="relevant", original="routine")
        report = LearningQualityAnalyzer(self.storage).report(limit=100)
        pattern = report["evaluation"]["pattern_learning"]
        self.assertEqual(pattern["relevant_missed"], 0)
        self.assertIn("by_actual_category", pattern)
        self.assertIn("confusion_matrix", pattern)

    def test_dataset_export_is_pseudonymized_and_mode_0600(self) -> None:
        self.seed_mixed_sender()
        output = self.root / "learning_dataset.json"
        LearningQualityAnalyzer(self.storage).export_dataset(output, limit=100)
        payload = json.loads(output.read_text(encoding="utf-8"))
        raw = output.read_text(encoding="utf-8")
        self.assertEqual(output.stat().st_mode & 0o777, 0o600)
        self.assertEqual(len(payload["records"]), 4)
        self.assertNotIn("mixed@example.test", raw)
        self.assertNotIn("Newsletter Ausgabe", raw)
        self.assertNotIn("Lokaler Testinhalt", raw)
        self.assertFalse(payload["privacy"]["contains_mail_bodies"])
        self.assertFalse(payload["privacy"]["contains_email_addresses"])
        self.assertEqual(len(payload["records"][0]["sender"]), 24)
        self.assertEqual(payload["schema_version"], 3)
        self.assertEqual(payload["records"][0]["pattern_version"], 2)
        self.assertTrue(payload["records"][0]["original_decision_available"])
        self.assertNotIn("Test", raw)

    def test_agent_registry_exposes_quality_tools(self) -> None:
        ids = {tool.id for tool in build_tool_registry(ToolSettings(path=Path("test-tools.toml")))}
        self.assertIn("mail.learning.evaluate", ids)
        self.assertIn("mail.learning.dataset-export", ids)


if __name__ == "__main__":
    unittest.main()
