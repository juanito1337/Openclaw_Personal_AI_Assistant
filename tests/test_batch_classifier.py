from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from mail_agent.app import MailAgent
from mail_agent.classifier import OllamaClassifier, OllamaTimeoutError
from mail_agent.config import load_config
from mail_agent.models import Classification, Envelope, OperationResult
from mail_agent.parser import parse_eml
from mail_agent.rules import RuleEngine
from mail_agent.storage import Storage


class _Response:
    def __init__(self, payload: dict[str, object], headers: dict[str, str] | None = None) -> None:
        self.payload = payload
        self.headers = headers or {}

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


def _model_item(local_id: str, category: str = "relevant") -> dict[str, object]:
    relevant = category in {"relevant", "appointment"}
    return {
        "id": local_id,
        "category": category,
        "confidence": 0.96,
        "importance": 8 if relevant else 3,
        "forward": relevant,
        "reason": f"Testentscheidung fuer {local_id}",
        "summary": f"Zusammenfassung {local_id}",
        "expected_action": "Pruefen" if relevant else "",
        "calendar_event": None,
    }


class BatchClassifierTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        source_config = Path(__file__).parents[1] / "mail_agent/config.example.toml"
        text = source_config.read_text(encoding="utf-8")
        text = text.replace("mail_agent/data/", str(self.root / "data") + "/")
        text = text.replace(
            'rules_file = "mail_agent/rules.toml"',
            f'rules_file = "{self.root / "rules.toml"}"',
        )
        text = text.replace(
            'log_file = "mail_agent/data/mail_agent.log"',
            f'log_file = "{self.root / "mail_agent.log"}"',
        )
        self.config_path = self.root / "config.toml"
        self.config_path.write_text(text, encoding="utf-8")
        (self.root / "rules.toml").write_text(
            "[spam]\naddresses=[]\ndomains=[]\nsender_names=[]\nsubject_phrases=[]\n"
            "[important]\naddresses=[]\ndomains=[]\n"
            "[routine]\naddresses=[]\ndomains=[]\n",
            encoding="utf-8",
        )
        self.config = load_config(self.config_path)
        self.storage = Storage(self.config.runtime.database)
        self.rules = RuleEngine(self.config.runtime.rules_file, self.storage)
        self.classifier = OllamaClassifier(self.config, self.storage, self.rules)

    def tearDown(self) -> None:
        self.storage.close()
        self.temp.cleanup()

    @staticmethod
    def message(index: int, *, newsletter: bool = False):
        subject = "Newsletter: 30 Prozent Rabatt" if newsletter else f"Rueckfrage zum Vorgang {index}"
        body = (
            "Newsletter abbestellen. Sonderangebot, Rabatt und Gutschein nur heute."
            if newsletter
            else f"Guten Tag, bitte pruefen Sie den aktuellen Sachstand zum Vorgang {index}."
        )
        raw = (
            f"From: Person {index} <person{index}@example.test>\r\n"
            "To: Jan <jan@example.test>\r\n"
            f"Subject: {subject}\r\n"
            f"Message-ID: <batch-{index}@example.test>\r\n"
            "Content-Type: text/plain; charset=utf-8\r\n\r\n"
            f"{body}\r\n"
        ).encode()
        return parse_eml(raw, Envelope(str(index)), "INBOX")

    def test_five_unresolved_messages_use_one_structured_batch_request(self) -> None:
        self.config.ollama.batch_size = 5
        self.config.ollama.batch_adaptive_target_chars = 50000
        messages = [self.message(index) for index in range(1, 6)]
        response = {
            "message": {
                "content": json.dumps({
                    "results": [_model_item(f"mail-{index}") for index in range(5, 0, -1)]
                })
            }
        }
        captured: list[dict[str, object]] = []

        def fake_urlopen(request, timeout):
            captured.append(json.loads(request.data.decode("utf-8")))
            return _Response(response)

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            results = self.classifier.classify_many(messages)

        self.assertEqual(len(results), 5)
        self.assertEqual([item.source for item in results], ["ollama-batch"] * 5)
        self.assertEqual([item.summary for item in results], [f"Zusammenfassung mail-{index}" for index in range(1, 6)])
        self.assertEqual(len(captured), 1)
        payload = captured[0]
        self.assertEqual(payload["think"], False)
        self.assertEqual(payload["keep_alive"], "1h")
        self.assertEqual(payload["options"]["num_predict"], self.config.ollama.batch_num_predict)
        self.assertEqual(payload["options"]["num_ctx"], 16384)
        self.assertIn("results", payload["format"]["properties"])
        metrics = self.classifier.metrics_snapshot()
        self.assertEqual(metrics["batch_requests"], 1)
        self.assertEqual(metrics["single_requests"], 0)
        self.assertEqual(metrics["model_message_attempts"], 5)

    def test_mail_agent_run_prefetches_and_batches_five_messages(self) -> None:
        self.config.ollama.batch_size = 5
        self.config.ollama.batch_adaptive_target_chars = 50000
        agent = MailAgent(self.config, dry_run=True)
        agent.antivirus.settings.enabled = False
        envelopes = [
            Envelope(str(index), subject=f"Rueckfrage zum Vorgang {index}")
            for index in range(1, 6)
        ]

        def fake_list_envelopes(folder: str, limit: int = 100):
            return (envelopes[:limit], "") if folder == "INBOX" else ([], "")

        def fake_export_message(folder: str, message_id: str, destination: Path):
            destination.write_bytes(self.message(int(message_id)).raw)
            return OperationResult(True, "exported", path=str(destination))

        batch_data = {
            "results": [_model_item(f"mail-{index}") for index in range(1, 6)]
        }
        try:
            with (
                patch.object(
                    agent.himalaya,
                    "list_folders",
                    return_value=(["INBOX", *self.config.folders.all(), "Agent/Korrektur-Wichtig/Korrektur-Rechnungen"], ""),
                ),
                patch.object(agent.himalaya, "list_envelopes", side_effect=fake_list_envelopes),
                patch.object(agent.himalaya, "export_message", side_effect=fake_export_message),
                patch.object(agent.classifier, "_request_json", return_value=batch_data) as request_json,
            ):
                summary = agent.run(limit=5, include_digest=False)
        finally:
            agent.close()

        self.assertEqual(summary.processed, 5)
        self.assertEqual(summary.errors, [])
        self.assertEqual(summary.classifier["batch_requests"], 1)
        self.assertEqual(summary.classifier["single_requests"], 0)
        self.assertEqual(summary.classifier["model_message_attempts"], 5)
        request_json.assert_called_once()

    def test_one_unresolved_message_keeps_single_message_path(self) -> None:
        response_item = _model_item("unused")
        response_item.pop("id")
        captured: list[dict[str, object]] = []

        def fake_urlopen(request, timeout):
            captured.append(json.loads(request.data.decode("utf-8")))
            return _Response({"message": {"content": json.dumps(response_item)}})

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            result = self.classifier.classify_many([self.message(1)])[0]

        self.assertEqual(result.source, "ollama")
        self.assertEqual(len(captured), 1)
        self.assertIn("category", captured[0]["format"]["properties"])
        self.assertNotIn("results", captured[0]["format"]["properties"])
        metrics = self.classifier.metrics_snapshot()
        self.assertEqual(metrics["single_requests"], 1)
        self.assertEqual(metrics["batch_requests"], 0)

    def test_invalid_model_category_is_normalized_to_safe_review(self) -> None:
        bad_item = _model_item("unused")
        bad_item.pop("id")
        bad_item.update({
            "category": "delete-everything",
            "confidence": 9.0,
            "importance": 99,
            "forward": True,
        })

        with patch(
            "urllib.request.urlopen",
            return_value=_Response({"message": {"content": json.dumps(bad_item)}}),
        ):
            result = self.classifier.classify_many([self.message(1)])[0]

        self.assertEqual(result.category, "uncertain")
        self.assertEqual(result.confidence, 0.0)
        self.assertEqual(result.importance, 10)
        self.assertFalse(result.forward)
        self.assertIn("ungueltige Kategorie", result.reason)

    def test_hard_rule_is_not_sent_to_model(self) -> None:
        messages = [self.message(1, newsletter=True), self.message(2), self.message(3)]
        response = {
            "message": {
                "content": json.dumps({
                    "results": [_model_item("mail-1"), _model_item("mail-2")]
                })
            }
        }
        captured: list[dict[str, object]] = []

        def fake_urlopen(request, timeout):
            captured.append(json.loads(request.data.decode("utf-8")))
            return _Response(response)

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            results = self.classifier.classify_many(messages)

        self.assertEqual(results[0].category, "spam")
        self.assertEqual(results[0].source, "rule")
        self.assertEqual(results[1].source, "ollama-batch")
        self.assertEqual(len(captured), 1)
        prompt = captured[0]["messages"][1]["content"]
        self.assertNotIn("Newsletter: 30 Prozent Rabatt", prompt)
        metrics = self.classifier.metrics_snapshot()
        self.assertEqual(metrics["rule_only_messages"], 1)
        self.assertEqual(metrics["model_message_attempts"], 2)


    def test_batch_timeout_uses_one_bounded_split_then_falls_back(self) -> None:
        self.config.ollama.parallel_requests = 1
        self.config.ollama.batch_size = 5
        self.config.ollama.batch_max_split_depth = 1
        messages = [self.message(index) for index in range(1, 6)]

        with patch.object(
            self.classifier,
            "_call_model_batch",
            side_effect=OllamaTimeoutError("simulierter Timeout"),
        ) as batch_call, patch.object(self.classifier, "_call_model") as single_call:
            results = self.classifier.classify_many(messages)

        self.assertEqual(batch_call.call_count, 3)
        single_call.assert_not_called()
        self.assertEqual([item.category for item in results], ["uncertain"] * 5)
        metrics = self.classifier.metrics_snapshot()
        self.assertEqual(metrics["batch_timeouts"], 3)
        self.assertEqual(metrics["batch_splits"], 1)
        self.assertEqual(metrics["bounded_retries"], 1)
        self.assertEqual(metrics["fallback_messages"], 5)

    def test_failed_large_batch_is_split_into_smaller_groups(self) -> None:
        self.config.ollama.parallel_requests = 1
        self.config.ollama.batch_size = 4
        self.config.ollama.batch_adaptive_target_chars = 50000
        messages = [self.message(index) for index in range(1, 5)]
        calls: list[int] = []

        def fake_batch(group, timeout_override=None):
            del timeout_override
            calls.append(len(group))
            if len(group) == 4:
                raise RuntimeError("simulierter unvollstaendiger Batch")
            return [
                Classification("relevant", 0.96, 8, True, "Teilbatch", source="test-batch")
                for _ in group
            ]

        with patch.object(self.classifier, "_call_model_batch", side_effect=fake_batch):
            results = self.classifier.classify_many(messages)

        self.assertEqual(calls, [4, 2, 2])
        self.assertEqual([item.source for item in results], ["test-batch"] * 4)
        metrics = self.classifier.metrics_snapshot()
        self.assertEqual(metrics["batch_failures"], 1)
        self.assertEqual(metrics["batch_splits"], 1)
        self.assertEqual(metrics["fallback_messages"], 0)

    def test_adaptive_batching_splits_multiple_heavy_messages_without_reordering(self) -> None:
        self.config.ollama.parallel_requests = 1
        self.config.ollama.batch_size = 5
        self.config.ollama.batch_adaptive_target_chars = 50000
        messages = [self.message(index) for index in range(1, 6)]
        messages[0].body_text = "A" * 5000
        messages[1].body_text = "B" * 5000
        messages[2].body_text = "kurz"
        messages[3].body_text = "kurz"
        messages[4].body_text = "kurz"
        calls: list[list[str]] = []

        def fake_group(group):
            calls.append([item.message.mailbox_id for item in group])
            return [
                Classification("relevant", 0.96, 8, True, "adaptiv", source="adaptive-test")
                for _ in group
            ]

        with patch.object(self.classifier, "_classify_model_group", side_effect=fake_group):
            results = self.classifier.classify_many(messages)

        self.assertEqual(calls, [["1"], ["2", "3", "4", "5"]])
        self.assertEqual([item.source for item in results], ["adaptive-test"] * 5)
        metrics = self.classifier.metrics_snapshot()
        self.assertTrue(metrics["adaptive_batching"])
        self.assertEqual(metrics["adaptive_groups"], 2)
        self.assertEqual(metrics["adaptive_single_groups"], 1)
        self.assertEqual(metrics["adaptive_reductions"], 1)

    def test_two_model_groups_run_in_parallel_and_keep_result_order(self) -> None:
        import threading
        import time

        self.config.ollama.parallel_requests = 2
        messages = [self.message(index) for index in range(1, 5)]
        messages[0].body_text = "A" * 5000
        messages[1].body_text = "B" * 5000
        active = 0
        max_active = 0
        lock = threading.Lock()

        def fake_group(group):
            nonlocal active, max_active
            with lock:
                active += 1
                max_active = max(max_active, active)
            time.sleep(0.05)
            with lock:
                active -= 1
            return [
                Classification(
                    "relevant", 0.96, 8, True, "parallel",
                    summary=item.message.mailbox_id,
                    source="parallel-test",
                )
                for item in group
            ]

        with patch.object(self.classifier, "_classify_model_group", side_effect=fake_group):
            results = self.classifier.classify_many(messages)

        self.assertEqual(max_active, 2)
        self.assertEqual([item.summary for item in results], ["1", "2", "3", "4"])
        metrics = self.classifier.metrics_snapshot()
        self.assertEqual(metrics["parallel_group_runs"], 1)
        self.assertEqual(metrics["parallel_group_max_workers"], 2)

    def test_calendar_invite_is_isolated_but_surrounding_messages_stay_batched(self) -> None:
        self.config.ollama.parallel_requests = 1
        messages = [self.message(index) for index in range(1, 5)]
        messages[1].calendar_invites = ["BEGIN:VCALENDAR"]
        calls: list[list[str]] = []

        def fake_group(group):
            calls.append([item.message.mailbox_id for item in group])
            return [
                Classification("relevant", 0.96, 8, True, "adaptiv", source="adaptive-test")
                for _ in group
            ]

        with patch.object(self.classifier, "_classify_model_group", side_effect=fake_group):
            results = self.classifier.classify_many(messages)

        self.assertEqual(calls, [["1"], ["2"], ["3", "4"]])
        self.assertEqual([item.source for item in results], ["adaptive-test"] * 4)

    def test_adaptive_batching_can_be_disabled_for_legacy_grouping(self) -> None:
        self.config.ollama.parallel_requests = 1
        self.config.ollama.batch_size = 5
        self.config.ollama.batch_adaptive_enabled = False
        messages = [self.message(index) for index in range(1, 6)]
        messages[0].body_text = "A" * 5000
        messages[1].body_text = "B" * 5000
        calls: list[int] = []

        def fake_group(group):
            calls.append(len(group))
            return [
                Classification("relevant", 0.96, 8, True, "legacy", source="legacy-test")
                for _ in group
            ]

        with patch.object(self.classifier, "_classify_model_group", side_effect=fake_group):
            self.classifier.classify_many(messages)

        self.assertEqual(calls, [5])


if __name__ == "__main__":
    unittest.main()
