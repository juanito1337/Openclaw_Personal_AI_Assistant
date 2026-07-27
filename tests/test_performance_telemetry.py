from __future__ import annotations

import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from mail_agent.classifier import OllamaClassifier
from mail_agent.cli import build_parser
from mail_agent.command import CommandRunner
from mail_agent.telemetry import (
    PerformanceTelemetry,
    command_category,
    read_recent_performance,
    summarize_performance,
)


class _FakeResponse:
    def __init__(self, payload: dict[str, object], headers: dict[str, str] | None = None) -> None:
        self.payload = payload
        self.headers = headers or {}

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


class PerformanceTelemetryTests(unittest.TestCase):
    def test_cli_exposes_performance_report_without_changing_run_options(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["performance", "--limit", "25", "--raw"])
        self.assertEqual(args.command, "performance")
        self.assertEqual(args.limit, 25)
        self.assertTrue(args.raw)
        run_args = parser.parse_args(["run", "--dry-run", "--limit", "5"])
        self.assertEqual(run_args.command, "run")
        self.assertTrue(run_args.dry_run)
        self.assertEqual(run_args.limit, 5)

    def test_telemetry_records_metrics_without_sensitive_arguments(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "performance.jsonl"
            telemetry = PerformanceTelemetry(path)
            telemetry.reset("test")
            runner = CommandRunner(observer=telemetry.observe_command)
            secret = "user@example.invalid"
            result = runner.run([sys.executable, "-c", f"print({secret!r})"])
            self.assertTrue(result.ok)
            with telemetry.phase("parse"):
                time.sleep(0.001)
            snapshot = telemetry.finish(
                processed=1,
                skipped=0,
                errors=[],
                classifier={"model_requests": 0},
            )
            self.assertEqual(snapshot["operation"], "test")
            raw = path.read_text(encoding="utf-8")
            self.assertNotIn(secret, raw)
            self.assertNotIn("print(", raw)
            record = json.loads(raw)
            self.assertEqual(record["processed"], 1)
            self.assertIn("parse", record["phases"])
            self.assertTrue(any(key.startswith("external.python") for key in record["external_commands"]))

    def test_command_category_is_privacy_safe(self) -> None:
        category = command_category([
            "/usr/local/bin/himalaya",
            "--account",
            "secret-account",
            "message",
            "move",
            "12345",
            "Agent/Spam",
        ])
        self.assertEqual(category, "himalaya.message.move")
        self.assertNotIn("secret", category)
        self.assertNotIn("12345", category)

    def test_ollama_metrics_are_collected_from_response(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            telemetry = PerformanceTelemetry(Path(temp_dir) / "performance.jsonl")
            telemetry.reset("ollama-test")
            classifier = OllamaClassifier.__new__(OllamaClassifier)
            classifier.config = SimpleNamespace(
                ollama=SimpleNamespace(
                    temperature=0.0,
                    num_ctx=0,
                    model="gemma4:31b",
                    think=False,
                    keep_alive="10m",
                    base_url="http://127.0.0.1:11434",
                    batch_enabled=True,
                    batch_size=5,
                )
            )
            classifier.telemetry = telemetry
            classifier.log = __import__("logging").getLogger(__name__)
            classifier.reset_metrics()
            response = {
                "message": {"content": '{"category":"spam"}'},
                "total_duration": 2_000_000_000,
                "load_duration": 100_000_000,
                "prompt_eval_count": 321,
                "prompt_eval_duration": 1_200_000_000,
                "eval_count": 17,
                "eval_duration": 600_000_000,
                "done_reason": "stop",
            }
            with patch("urllib.request.urlopen", return_value=_FakeResponse(response)):
                data = classifier._request_json(
                    system_prompt="system",
                    user_prompt="user",
                    schema={"type": "object"},
                    num_predict=100,
                    timeout_seconds=5,
                )
            self.assertEqual(data["category"], "spam")
            metrics = classifier.metrics_snapshot()
            self.assertEqual(metrics["ollama_attempts"], 1)
            self.assertEqual(metrics["ollama_prompt_eval_count"], 321)
            self.assertEqual(metrics["ollama_eval_count"], 17)
            self.assertEqual(metrics["ollama_server_total_duration_ms"], 2000.0)

    def test_recent_reader_and_summary_ignore_partial_lines(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "performance.jsonl"
            valid = {
                "operation": "run",
                "started_at": "2026-07-23T10:00:00+00:00",
                "finished_at": "2026-07-23T10:00:01+00:00",
                "total_ms": 1000,
                "processed": 2,
                "error_count": 0,
                "phases": {"classification": {"count": 1, "total_ms": 800, "max_ms": 800}},
                "external_commands": {},
                "ollama": {"summary": {"attempt_count": 1, "client_duration_ms": 800}},
            }
            path.write_text(json.dumps(valid) + "\n{partial", encoding="utf-8")
            records = read_recent_performance(path, limit=10)
            self.assertEqual(len(records), 1)
            summary = summarize_performance(records)
            self.assertEqual(summary["runs"], 1)
            self.assertEqual(summary["processed"], 2)
            self.assertEqual(summary["slowest_phases"][0]["name"], "classification")

    def test_stale_inflight_checkpoint_becomes_interrupted_run(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "performance.jsonl"
            first = PerformanceTelemetry(path)
            first.reset("run")
            first.checkpoint("ollama.schema")
            inflight = json.loads(first.inflight_path.read_text(encoding="utf-8"))
            inflight["pid"] = 99999999
            inflight["proc_start_ticks"] = "dead"
            first.inflight_path.write_text(json.dumps(inflight), encoding="utf-8")

            second = PerformanceTelemetry(path)
            second.reset("run")
            records = read_recent_performance(path, limit=10)
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0]["outcome"], "interrupted")
            self.assertEqual(records[0]["last_phase"], "ollama.schema")
            self.assertNotEqual(records[0]["run_id"], second.run_id)
            second.finish(processed=0, skipped=0, errors=[], classifier={})
            self.assertFalse(second.inflight_path.exists())

    def test_live_inflight_owner_is_not_overwritten_or_marked_interrupted(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "performance.jsonl"
            first = PerformanceTelemetry(path)
            first.reset("mail-agent")
            first.checkpoint("ollama.schema")
            original = json.loads(first.inflight_path.read_text(encoding="utf-8"))

            second = PerformanceTelemetry(path)
            second.reset("mail-agent")

            current = json.loads(first.inflight_path.read_text(encoding="utf-8"))
            self.assertEqual(current["run_id"], original["run_id"])
            self.assertNotEqual(second.run_id, original["run_id"])
            self.assertFalse(path.exists())
            second.finish(processed=0, skipped=0, errors=[], classifier={})
            self.assertTrue(first.inflight_path.exists())
            first.finish(processed=1, skipped=0, errors=[], classifier={})
            self.assertFalse(first.inflight_path.exists())

    def test_recent_reader_deduplicates_run_ids(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "performance.jsonl"
            records = [
                {"run_id": "same", "operation": "drain", "finished_at": "2026-07-26T08:00:00+00:00", "processed": 0},
                {"run_id": "same", "operation": "drain", "finished_at": "2026-07-26T08:05:00+00:00", "processed": 2},
                {"run_id": "other", "operation": "mail-agent", "finished_at": "2026-07-26T08:06:00+00:00", "processed": 1},
            ]
            path.write_text("\n".join(json.dumps(item) for item in records) + "\n", encoding="utf-8")
            recent = read_recent_performance(path, limit=10)
            self.assertEqual(len(recent), 2)
            by_id = {item["run_id"]: item for item in recent}
            self.assertEqual(by_id["same"]["processed"], 2)
            self.assertEqual(summarize_performance(recent)["runs"], 2)

    def test_write_failure_is_fail_open(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            directory = Path(temp_dir) / "directory-as-file"
            directory.mkdir()
            telemetry = PerformanceTelemetry(directory)
            telemetry.reset("fail-open")
            result = telemetry.finish(
                processed=0,
                skipped=0,
                errors=[],
                classifier={},
            )
            self.assertEqual(result["operation"], "fail-open")

    def test_zero_processed_messages_have_no_per_message_average(self) -> None:
        summary = summarize_performance([{
            "operation": "run",
            "started_at": "2026-07-23T10:00:00+00:00",
            "finished_at": "2026-07-23T10:00:01+00:00",
            "total_ms": 1000,
            "processed": 0,
            "error_count": 1,
            "outcome": "interrupted",
            "phases": {},
            "external_commands": {},
            "ollama": {"summary": {}},
        }])
        self.assertIsNone(summary["average_ms_per_processed_message"])


if __name__ == "__main__":
    unittest.main()
