from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from mail_agent.storage import Storage as MailStorage
from personal_assistant.config import AssistantConfig, RuntimeConfig, SearchConfig
from personal_assistant.monitoring import PerformanceMonitor
from personal_assistant.storage import AssistantStorage
from personal_assistant.tool_registry import build_tool_registry
from personal_assistant.tool_settings import ToolSettings


class MonitoringToolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.assistant_db = root / "assistant.sqlite3"
        self.mail_db = root / "mail.sqlite3"
        self.monitor_db = root / "monitor.sqlite3"
        self.storage = AssistantStorage(self.assistant_db)
        self.mail = MailStorage(self.mail_db)
        now = datetime.now(timezone.utc).isoformat()

        self.storage.index_document(
            source_type="nextcloud-file",
            resource_id="nextcloud-files-main",
            source_id="Assistent/test.txt",
            uri="nextcloud://test",
            title="Test",
            modified_at=now,
            etag="etag",
            sha256="hash",
            metadata={},
            chunks=["Monitoring Testinhalt"],
        )
        self.storage.set_sync_state("nextcloud-files-main", "files", status="ok", detail="{}")
        action = self.storage.create_action(
            idempotency_key="test-action",
            action_type="files.create",
            resource_id="nextcloud-files-main",
            payload={"path": "Assistent/test.txt"},
            requires_approval=False,
        )
        self.storage.update_action(action.id, "completed")

        with self.mail.connection:
            self.mail.connection.execute(
                """
                INSERT INTO messages(
                    stable_key,message_id,mailbox_id,last_folder,sender_addr,sender_name,sender_domain,
                    subject,subject_signature,received_at,category,confidence,importance,forward_flag,
                    reason,summary,expected_action,status,destination_folder,classification_json,
                    first_seen_at,updated_at,forwarded_at,last_error,retry_count
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    "m1", "m1", "1", "INBOX", "a@example.com", "A", "example.com",
                    "Test", "test", now, "relevant", 0.92, 8, 0,
                    "", "", "", "review", "Agent/Pruefen", "{}", now, now, None, "", 0,
                ),
            )
            self.mail.connection.execute(
                "INSERT INTO actions(stable_key,action,source_folder,destination_folder,ok,detail,created_at) VALUES(?,?,?,?,?,?,?)",
                ("m1", "move", "INBOX", "Agent/Pruefen", 1, "ok", now),
            )

        config = AssistantConfig(
            runtime=RuntimeConfig(
                database=self.assistant_db,
                log_file=root / "assistant.log",
                resources_file=root / "resources.toml",
                policies_file=root / "policies.toml",
                secrets_file=root / "secrets.env",
            ),
            search=SearchConfig(mail_snapshot_dir=root / "snapshots"),
            path=root / "config.toml",
        )
        self.registry = SimpleNamespace(resources={"nextcloud-files-main": object()}, duplicate_ids=[])
        self.monitor = PerformanceMonitor(
            config,
            self.storage,
            self.registry,
            live_health=lambda: {"ok": True, "dav_status": 207},
            mail_database=self.mail_db,
            monitor_database=self.monitor_db,
            portfolio_health=lambda: {
                "enabled": True,
                "ok": False,
                "state": "degraded",
                "coverage": 0.5,
                "required": 2,
                "fresh": 1,
            },
            scheduler_health=lambda: {
                "enabled": True,
                "ok": True,
                "state": "healthy",
                "active": 0,
                "pending": 1,
                "deadline_misses": 0,
                "seven_day": {"average_wait_ms": 125.0},
            },
        )

    def tearDown(self) -> None:
        self.monitor.close()
        self.mail.close()
        self.storage.close()
        self.temp.cleanup()

    def test_report_is_evidence_based_and_bounded(self) -> None:
        report = self.monitor.report(days=7, live=True)
        self.assertGreaterEqual(report["overall_score"], 0)
        self.assertLessEqual(report["overall_score"], 100)
        self.assertEqual(sum(item["maximum"] for item in report["components"]), 100)
        self.assertIn(report["confidence"], {"niedrig", "mittel", "hoch"})
        self.assertIn("beweist nicht", report["interpretation"])
        self.assertTrue(report["metrics"]["nextcloud_live"]["ok"])
        self.assertEqual(report["metrics"]["mail"]["recent_messages"], 1)
        self.assertEqual(report["score_schema"], 3)
        self.assertEqual(report["metrics"]["scheduler"]["state"], "healthy")
        self.assertEqual(report["metrics"]["portfolio"]["state"], "degraded")
        component = next(
            item for item in report["components"] if item["id"] == "portfolio_market_data"
        )
        self.assertEqual(component["score"], 2.5)

    def test_record_and_history(self) -> None:
        first = self.monitor.record(days=7, live=False)
        second = self.monitor.record(days=7, live=False)
        self.assertTrue(first["ok"])
        self.assertGreater(second["snapshot_id"], first["snapshot_id"])
        history = self.monitor.history(days=30)
        self.assertEqual(len(history["snapshots"]), 2)
        self.assertIn(history["trend"], {"stable", "improving", "declining"})

    def test_scheduler_failure_is_visible_in_runtime_evidence(self) -> None:
        self.monitor.scheduler_health = lambda: {
            "enabled": True,
            "ok": False,
            "state": "degraded",
            "deadline_misses": 1,
        }
        report = self.monitor.report(days=7, live=False)
        runtime = next(item for item in report["components"] if item["id"] == "runtime")
        self.assertEqual(runtime["evidence"]["scheduler"]["state"], "degraded")
        self.assertTrue(
            any("scheduler doctor" in item for item in report["recommendations"])
        )

    def test_tool_registry_exposes_monitoring(self) -> None:
        ids = {item.id for item in build_tool_registry(ToolSettings(path=Path("tools.toml")))}
        self.assertIn("assistant.monitor.status", ids)
        self.assertIn("assistant.monitor.record", ids)
        self.assertIn("assistant.monitor.history", ids)


if __name__ == "__main__":
    unittest.main()
