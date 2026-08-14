from __future__ import annotations

import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path

from mail_agent.models import Classification, Envelope
from mail_agent.parser import parse_eml
from mail_agent.review import ReviewReason
from mail_agent.storage import Storage


class StorageMigrationTests(unittest.TestCase):
    def test_legacy_feedback_table_is_upgraded_before_new_index_is_created(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            database = Path(tmp) / "legacy.sqlite3"
            connection = sqlite3.connect(database)
            connection.executescript(
                """
                CREATE TABLE feedback (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    stable_key TEXT NOT NULL,
                    verdict TEXT NOT NULL,
                    sender_addr TEXT,
                    sender_domain TEXT,
                    subject TEXT,
                    subject_signature TEXT,
                    source_folder TEXT,
                    created_at TEXT NOT NULL,
                    metadata_json TEXT
                );
                INSERT INTO feedback (
                    stable_key, verdict, sender_addr, sender_domain, subject,
                    subject_signature, source_folder, created_at, metadata_json
                ) VALUES (
                    'legacy-key', 'routine', 'sender@example.test', 'example.test',
                    'Status 4711', 'status <num>', 'Agent/Korrektur-Unwichtig',
                    '2026-07-23T00:00:00+00:00', '{}'
                );
                PRAGMA user_version=1;
                """
            )
            connection.commit()
            connection.close()

            storage = Storage(database)
            try:
                columns = {
                    row[1]
                    for row in storage.connection.execute("PRAGMA table_info(feedback)").fetchall()
                }
                self.assertTrue(
                    {
                        "subject_pattern", "correction_folder", "label", "feature_json",
                        "pattern_version",
                        "original_category", "original_confidence", "original_reason",
                        "original_source", "original_rule_decision",
                        "original_classification_json", "original_captured_at",
                        "original_snapshot_valid",
                    } <= columns
                )
                message_columns = {
                    row[1]
                    for row in storage.connection.execute("PRAGMA table_info(messages)").fetchall()
                }
                self.assertTrue(
                    {
                        "review_reason", "review_category", "review_confidence",
                        "review_source", "review_threshold", "review_captured_at",
                    } <= message_columns
                )
                row = storage.connection.execute(
                    "SELECT subject_pattern, pattern_version, correction_folder "
                    "FROM feedback WHERE stable_key=?",
                    ("legacy-key",),
                ).fetchone()
                self.assertEqual(row["subject_pattern"], "status <num>")
                self.assertEqual(row["pattern_version"], 1)
                self.assertEqual(row["correction_folder"], "Agent/Korrektur-Unwichtig")
                valid = storage.connection.execute(
                    "SELECT original_snapshot_valid FROM feedback WHERE stable_key=?",
                    ("legacy-key",),
                ).fetchone()[0]
                self.assertEqual(valid, 0)
                indexes = {
                    item[1]
                    for item in storage.connection.execute("PRAGMA index_list(feedback)").fetchall()
                }
                self.assertIn("idx_feedback_subject_pattern", indexes)
                self.assertIn("idx_feedback_pattern_version", indexes)
                self.assertEqual(
                    storage.connection.execute("PRAGMA quick_check").fetchone()[0],
                    "ok",
                )
            finally:
                storage.close()

    def test_migration_is_idempotent_after_successful_upgrade(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            database = Path(tmp) / "idempotent.sqlite3"
            first = Storage(database)
            first.close()
            second = Storage(database)
            try:
                self.assertEqual(
                    second.connection.execute("PRAGMA quick_check").fetchone()[0],
                    "ok",
                )
            finally:
                second.close()

    def test_review_migration_preserves_rows_and_only_backfills_provable_reasons(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            database = Path(tmp) / "review-v1.sqlite3"
            seed = sqlite3.connect(database)
            seed.executescript(
                """
                CREATE TABLE messages (
                    stable_key TEXT PRIMARY KEY,
                    message_id TEXT,
                    mailbox_id TEXT,
                    last_folder TEXT,
                    sender_addr TEXT,
                    sender_name TEXT,
                    sender_domain TEXT,
                    subject TEXT,
                    subject_signature TEXT,
                    received_at TEXT,
                    category TEXT,
                    confidence REAL,
                    importance INTEGER,
                    forward_flag INTEGER,
                    reason TEXT,
                    summary TEXT,
                    expected_action TEXT,
                    status TEXT,
                    destination_folder TEXT,
                    classification_json TEXT,
                    first_seen_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    forwarded_at TEXT,
                    last_error TEXT,
                    retry_count INTEGER NOT NULL DEFAULT 0
                );
                INSERT INTO messages (
                    stable_key, category, confidence, status, reason,
                    first_seen_at, updated_at
                ) VALUES
                    ('uncertain', 'uncertain', 0.61, 'review', 'raw reason', 'a', 'a'),
                    ('invoice-or-routine', 'routine', 0.99, 'review', 'raw reason', 'a', 'a'),
                    ('appointment', 'appointment', 0.98, 'appointment-review', 'raw reason', 'a', 'a'),
                    ('final', 'routine', 0.99, 'routine', 'raw reason', 'a', 'a');
                PRAGMA user_version=1;
                """
            )
            seed.commit()
            seed.close()
            backup = Path(tmp) / "review-v1.backup.sqlite3"
            shutil.copy2(database, backup)

            migrated = Storage(database)
            try:
                rows = {
                    row["stable_key"]: row
                    for row in migrated.connection.execute(
                        "SELECT * FROM messages ORDER BY stable_key"
                    ).fetchall()
                }
                self.assertEqual(
                    rows["uncertain"]["review_reason"],
                    ReviewReason.CLASSIFICATION_UNCERTAIN.value,
                )
                self.assertEqual(
                    rows["invoice-or-routine"]["review_reason"],
                    ReviewReason.UNKNOWN_LEGACY.value,
                )
                self.assertEqual(
                    rows["appointment"]["review_reason"],
                    ReviewReason.APPOINTMENT_REVIEW.value,
                )
                self.assertIsNone(rows["final"]["review_reason"])
                self.assertEqual(migrated.connection.execute("PRAGMA quick_check").fetchone()[0], "ok")
            finally:
                migrated.close()

            restored = sqlite3.connect(backup)
            try:
                self.assertEqual(
                    restored.execute("SELECT COUNT(*) FROM messages").fetchone()[0],
                    4,
                )
                self.assertEqual(restored.execute("PRAGMA quick_check").fetchone()[0], "ok")
            finally:
                restored.close()

    def test_invalid_persisted_review_reason_fails_migration(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            database = Path(tmp) / "invalid-review.sqlite3"
            storage = Storage(database)
            storage.connection.execute(
                """
                INSERT INTO messages (
                    stable_key, status, review_reason, first_seen_at, updated_at
                ) VALUES ('bad', 'review', 'free-text', 'a', 'a')
                """
            )
            storage.connection.commit()
            storage.close()
            with self.assertRaisesRegex(RuntimeError, "unbekannte Review-Gruende"):
                Storage(database)

    def test_record_review_is_validated_and_immutable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            storage = Storage(Path(tmp) / "review.sqlite3")
            try:
                raw = b"From: Sender <sender@example.test>\nSubject: Test\nMessage-ID: <review@test>\n\nBody"
                message = parse_eml(raw, Envelope("7"), "INBOX")
                original = Classification(
                    "routine", 0.89, 2, False, "not persisted in review fields", source="ollama"
                )
                storage.upsert_message(message, original, status="review")
                storage.record_review(
                    message.stable_key,
                    ReviewReason.ROUTINE_BELOW_THRESHOLD,
                    original,
                    threshold=0.90,
                )
                storage.record_review(
                    message.stable_key,
                    ReviewReason.SAFETY_BLOCKED,
                    Classification("uncertain", 0.20, 1, False, "new", source="fallback"),
                    threshold=0.95,
                )
                row = storage.get_message(message.stable_key)
                assert row is not None
                self.assertEqual(row["review_reason"], "routine-below-threshold")
                self.assertEqual(row["review_category"], "routine")
                self.assertEqual(row["review_confidence"], 0.89)
                self.assertEqual(row["review_source"], "ollama")
                self.assertEqual(row["review_threshold"], 0.90)
                self.assertNotIn("Body", tuple(row))
                with self.assertRaises(ValueError):
                    storage.record_review(message.stable_key, "free-text", original)
                with self.assertRaises(KeyError):
                    storage.record_review("missing", ReviewReason.UNKNOWN_LEGACY, original)
            finally:
                storage.close()


if __name__ == "__main__":
    unittest.main()
