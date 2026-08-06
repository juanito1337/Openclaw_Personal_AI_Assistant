from __future__ import annotations

import tempfile
import threading
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from personal_assistant.cli import _record_interactive_activity, parser
from personal_assistant.tool_registry import build_tool_registry
from personal_assistant.tool_settings import ToolSettings
from personal_assistant.work_scheduler import AdaptiveWorkScheduler


class MutableClock:
    def __init__(self) -> None:
        self.value = datetime(2026, 7, 29, 10, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.value

    def advance(self, seconds: int) -> None:
        self.value += timedelta(seconds=seconds)


class WorkSchedulerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "scheduler.sqlite3"
        self.clock = MutableClock()
        self.scheduler = AdaptiveWorkScheduler(
            self.path,
            now=self.clock,
            lease_seconds=60,
            arbitration_seconds=0,
            starvation_seconds=120,
        )

    def tearDown(self) -> None:
        self.scheduler.close()
        self.temp.cleanup()

    def test_only_one_background_task_is_granted(self) -> None:
        portfolio = self.scheduler.enqueue("portfolio", owner="portfolio-worker")
        mail = self.scheduler.enqueue("mail", owner="mail-worker")

        portfolio_claim = self.scheduler.claim(portfolio, owner="portfolio-worker")
        mail_claim = self.scheduler.claim(mail, owner="mail-worker")

        self.assertTrue(portfolio_claim.granted)
        self.assertFalse(mail_claim.granted)
        self.assertEqual(mail_claim.reason, "busy")
        snapshot = self.scheduler.snapshot()
        self.assertEqual([item["job"] for item in snapshot["active"]], ["portfolio"])
        self.assertEqual([item["job"] for item in snapshot["pending"]], ["mail"])

    def test_concurrent_workers_cannot_both_acquire_the_single_slot(self) -> None:
        portfolio = self.scheduler.enqueue("portfolio", owner="portfolio-worker")
        mail = self.scheduler.enqueue("mail", owner="mail-worker")
        barrier = threading.Barrier(2)
        results: list[bool] = []

        def claim(ticket: str, owner: str) -> None:
            worker = AdaptiveWorkScheduler(
                self.path,
                now=self.clock,
                lease_seconds=60,
                arbitration_seconds=0,
                starvation_seconds=120,
            )
            try:
                barrier.wait(timeout=2)
                results.append(worker.claim(ticket, owner=owner).granted)
            finally:
                worker.close()

        threads = [
            threading.Thread(target=claim, args=(portfolio, "portfolio-worker")),
            threading.Thread(target=claim, args=(mail, "mail-worker")),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=3)

        self.assertEqual(sorted(results), [False, True])

    def test_recent_user_topic_changes_next_task(self) -> None:
        self.scheduler.record_activity("mail", boost_minutes=30, source="test")
        portfolio = self.scheduler.enqueue("portfolio", owner="portfolio-worker")
        mail = self.scheduler.enqueue("mail", owner="mail-worker")

        mail_claim = self.scheduler.claim(mail, owner="mail-worker")
        portfolio_claim = self.scheduler.claim(portfolio, owner="portfolio-worker")

        self.assertTrue(mail_claim.granted)
        self.assertFalse(portfolio_claim.granted)
        self.assertGreater(mail_claim.score or 0, portfolio_claim.score or 0)

    def test_starvation_aging_eventually_beats_fresh_focus(self) -> None:
        sync = self.scheduler.enqueue("sync", owner="sync-worker")
        self.clock.advance(121)
        self.scheduler.record_activity("portfolio", boost_minutes=30, source="test")
        portfolio = self.scheduler.enqueue("portfolio", owner="portfolio-worker")

        sync_claim = self.scheduler.claim(sync, owner="sync-worker")
        portfolio_claim = self.scheduler.claim(portfolio, owner="portfolio-worker")

        self.assertTrue(sync_claim.granted)
        self.assertFalse(portfolio_claim.granted)

    def test_expired_lease_is_recovered_after_worker_loss(self) -> None:
        ticket = self.scheduler.enqueue("portfolio", owner="old-worker")
        claim = self.scheduler.claim(ticket, owner="old-worker")
        self.assertTrue(claim.granted)
        self.clock.advance(61)

        other = AdaptiveWorkScheduler(
            self.path,
            now=self.clock,
            lease_seconds=60,
            arbitration_seconds=0,
            starvation_seconds=120,
        )
        try:
            recovered_ticket = other.enqueue("portfolio", owner="new-worker")
            recovered = other.claim(recovered_ticket, owner="new-worker")
            self.assertEqual(recovered_ticket, ticket)
            self.assertTrue(recovered.granted)
            self.assertEqual(other.snapshot()["active"][0]["attempts"], 2)
        finally:
            other.close()

    def test_missed_queue_deadline_is_a_health_failure(self) -> None:
        self.scheduler.enqueue("sync", owner="sync-worker")
        self.clock.advance(3601)

        health = self.scheduler.health()

        self.assertFalse(health["ok"])
        self.assertEqual(health["state"], "degraded")
        self.assertEqual(health["deadline_misses"], 1)

    def test_finish_records_common_run_telemetry(self) -> None:
        ticket = self.scheduler.enqueue("monitor", owner="monitor-worker")
        self.clock.advance(5)
        claim = self.scheduler.claim(ticket, owner="monitor-worker")
        self.clock.advance(12)
        finished = self.scheduler.finish(
            claim.lease_token,
            owner="monitor-worker",
            result="completed",
            exit_code=0,
        )

        self.assertTrue(finished)
        snapshot = self.scheduler.snapshot()
        recent = snapshot["recent"][0]
        self.assertEqual(recent["job"], "monitor")
        self.assertEqual(recent["wait_ms"], 5000)
        self.assertEqual(recent["duration_ms"], 12000)
        self.assertEqual(snapshot["seven_day"]["success_rate"], 1.0)
        self.assertEqual(snapshot["seven_day"]["p95_wait_ms"], 5000)
        self.assertEqual(snapshot["seven_day"]["p95_duration_ms"], 12000)
        self.assertEqual(snapshot["seven_day"]["by_job"]["monitor"]["runs"], 1)
        self.assertEqual(
            snapshot["seven_day"]["by_job"]["monitor"]["last_success_at"],
            recent["finished_at"],
        )

    def test_unknown_jobs_and_topics_are_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self.scheduler.enqueue("shell", owner="test")
        with self.assertRaises(ValueError):
            self.scheduler.record_activity("arbitrary")

    def test_doctor_reports_integrity_and_fixed_policies(self) -> None:
        report = self.scheduler.doctor()
        self.assertTrue(report["ok"])
        self.assertEqual(report["integrity"], "ok")
        self.assertEqual(
            {item["job"] for item in report["policies"]},
            {"mail", "portfolio", "sync", "monitor"},
        )

    def test_cli_and_tool_registry_expose_scheduler(self) -> None:
        parsed = parser().parse_args(
            ["scheduler", "focus", "--topic", "portfolio", "--minutes", "15"]
        )
        self.assertEqual(parsed.scheduler_command, "focus")
        self.assertEqual(parsed.topic, "portfolio")
        ids = {
            item.id
            for item in build_tool_registry(ToolSettings(path=Path("tools.toml")))
        }
        self.assertIn("assistant.scheduler.status", ids)
        self.assertIn("assistant.scheduler.doctor", ids)
        self.assertIn("assistant.scheduler.activity", ids)
        self.assertIn("assistant.scheduler.focus", ids)

    def test_interactive_commands_record_topic_but_background_workers_do_not(self) -> None:
        interactive = parser().parse_args(["portfolio", "status"])
        with patch(
            "personal_assistant.work_scheduler.DEFAULT_SCHEDULER_DB",
            self.path,
        ):
            _record_interactive_activity(interactive)
        activity = self.scheduler.snapshot()["activity"]
        self.assertEqual(activity[0]["topic"], "portfolio")
        first_count = activity[0]["signal_count"]

        with (
            patch(
                "personal_assistant.work_scheduler.DEFAULT_SCHEDULER_DB",
                self.path,
            ),
            patch.dict(
                "os.environ",
                {"OPENCLAW_SCHEDULER_SOURCE": "background-worker"},
            ),
        ):
            _record_interactive_activity(interactive)
        self.assertEqual(
            self.scheduler.snapshot()["activity"][0]["signal_count"],
            first_count,
        )


if __name__ == "__main__":
    unittest.main()
