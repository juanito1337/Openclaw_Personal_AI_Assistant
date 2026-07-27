from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

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
                        "original_category", "original_confidence", "original_reason",
                        "original_source", "original_rule_decision",
                        "original_classification_json", "original_captured_at",
                        "original_snapshot_valid",
                    } <= columns
                )
                row = storage.connection.execute(
                    "SELECT subject_pattern, correction_folder FROM feedback WHERE stable_key=?",
                    ("legacy-key",),
                ).fetchone()
                self.assertEqual(row["subject_pattern"], "status <num>")
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


if __name__ == "__main__":
    unittest.main()
