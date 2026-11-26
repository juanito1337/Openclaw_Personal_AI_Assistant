from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.request import Request

from mail_agent.classifier import OllamaClassifier, OllamaOutputTruncatedError, RuntimeBudgetExceeded
from mail_agent.config import load_config
from mail_agent.models import Classification, Envelope
from mail_agent.parser import parse_eml
from mail_agent.rules import RuleEngine
from mail_agent.storage import Storage


class _Response:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload
        self.headers: dict[str, str] = {}

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


def _classification_payload() -> dict[str, object]:
    return {
        "category": "relevant",
        "confidence": 0.96,
        "importance": 8,
        "forward": True,
        "reason": "Rueckfrage erfordert eine Antwort.",
        "summary": "Der Absender bittet um eine Rueckmeldung.",
        "expected_action": "Antworten.",
        "calendar_event": None,
        "invoice": {
            "is_invoice": False,
            "confidence": 0.0,
            "reason": "Keine Rechnung.",
            "pdf_filenames": [],
        },
        "order": {
            "is_order_event": False,
            "event_type": "unknown",
            "confidence": 0.0,
            "merchant": "",
            "order_number": "",
            "ordered_at": "",
            "expected_delivery": "",
            "carrier": "",
            "tracking_numbers": [],
            "items": [],
            "amount": "",
            "currency": "EUR",
            "return_deadline": "",
            "reason": "",
        },
    }


def _request_budget(request: Request, client_timeout: int) -> tuple[int, int, int]:
    headers = {name.lower(): value for name, value in request.header_items()}
    return (
        int(headers["x-openclaw-queue-timeout-seconds"]),
        int(headers["x-openclaw-upstream-timeout-seconds"]),
        client_timeout,
    )


class OllamaReliabilityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        source = Path(__file__).parents[1] / "mail_agent/config.example.toml"
        text = source.read_text(encoding="utf-8")
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
    def message(index: int = 1):
        raw = (
            f"From: Person {index} <person{index}@example.test>\r\n"
            "To: Jan <jan@example.test>\r\n"
            "Subject: Rueckfrage\r\n"
            f"Message-ID: <r261-{index}@example.test>\r\n"
            "Content-Type: text/plain; charset=utf-8\r\n\r\n"
            "Bitte geben Sie uns eine Rueckmeldung.\r\n"
        ).encode()
        return parse_eml(raw, Envelope(str(index)), "INBOX")

    def test_truncated_single_json_retries_with_larger_schema_budget(self) -> None:
        requests: list[dict[str, object]] = []
        budgets: list[tuple[int, int, int]] = []
        now = 1000.0
        responses = [
            _Response({
                "message": {"content": '{"category":"relevant"'},
                "done_reason": "length",
                "eval_count": 512,
            }),
            _Response({
                "message": {"content": json.dumps(_classification_payload())},
                "done_reason": "stop",
                "eval_count": 620,
            }),
        ]

        def fake_urlopen(request: Request, timeout: int) -> _Response:
            nonlocal now
            requests.append(json.loads(request.data.decode("utf-8")))
            budgets.append(_request_budget(request, timeout))
            now += 220.0
            return responses.pop(0)

        with (
            patch("mail_agent.classifier.time.monotonic", new=lambda: now),
            patch("urllib.request.urlopen", new=fake_urlopen),
        ):
            result = self.classifier.classify(self.message())

        self.assertEqual(result.source, "ollama")
        self.assertEqual(len(requests), 2)
        self.assertEqual(budgets, [(600, 600, 1230), (600, 600, 1230)])
        self.assertIsInstance(requests[0]["format"], dict)
        self.assertIsInstance(requests[1]["format"], dict)
        self.assertEqual(requests[0]["options"]["num_predict"], 512)
        self.assertEqual(requests[1]["options"]["num_predict"], 1024)
        metrics = self.classifier.metrics_snapshot()
        self.assertEqual(metrics["truncated_outputs"], 1)
        self.assertEqual(metrics["truncation_retries"], 1)

    def test_schema_fallback_recomputes_transport_timeouts_from_remaining_budget(self) -> None:
        now = 1000.0
        self.classifier.set_runtime_deadline(1330.0)
        budgets: list[tuple[int, int, int]] = []
        formats: list[object] = []
        responses = [
            _Response({"message": {"content": "unparseable reply"}, "done_reason": "stop"}),
            _Response({
                "message": {"content": json.dumps(_classification_payload())},
                "done_reason": "stop",
            }),
        ]

        def fake_urlopen(request: Request, timeout: int) -> _Response:
            nonlocal now
            budgets.append(_request_budget(request, timeout))
            formats.append(json.loads(request.data)["format"])
            now += 150.0
            return responses.pop(0)

        with (
            patch("mail_agent.classifier.time.monotonic", new=lambda: now),
            patch("urllib.request.urlopen", new=fake_urlopen),
        ):
            result = self.classifier.classify(self.message())

        self.assertEqual(result.source, "ollama")
        self.assertIsInstance(formats[0], dict)
        self.assertEqual(formats[1], "json")
        self.assertEqual(budgets, [(100, 200, 330), (50, 100, 180)])

    def test_token_retry_stops_before_transport_when_remaining_budget_is_insufficient(self) -> None:
        now = 1000.0
        self.classifier.set_runtime_deadline(1330.0)
        budgets: list[tuple[int, int, int]] = []
        responses = [
            _Response({
                "message": {"content": '{"category":"relevant"'},
                "done_reason": "length",
            }),
            _Response({
                "message": {"content": json.dumps(_classification_payload())},
                "done_reason": "stop",
            }),
        ]

        def fake_urlopen(request: Request, timeout: int) -> _Response:
            nonlocal now
            budgets.append(_request_budget(request, timeout))
            now += 220.0
            return responses.pop(0)

        with (
            patch("mail_agent.classifier.time.monotonic", new=lambda: now),
            patch("urllib.request.urlopen", new=fake_urlopen),
            self.assertRaises(RuntimeBudgetExceeded),
        ):
            self.classifier.classify(self.message())

        self.assertEqual(budgets, [(100, 200, 330)])

    def test_truncated_batch_is_split_without_repeating_format_json(self) -> None:
        self.config.ollama.batch_size = 3
        self.config.ollama.parallel_requests = 1
        messages = [self.message(index) for index in range(1, 4)]
        calls: list[int] = []

        def fake_batch(group, timeout_override=None):
            del timeout_override
            calls.append(len(group))
            if len(group) == 3:
                raise OllamaOutputTruncatedError("simuliert")
            return [
                Classification("relevant", 0.96, 8, True, "ok", source="split")
                for _ in group
            ]

        with (
            patch.object(self.classifier, "_call_model_batch", side_effect=fake_batch),
            patch.object(
                self.classifier,
                "_call_model",
                return_value=Classification("relevant", 0.96, 8, True, "ok", source="split"),
            ),
        ):
            results = self.classifier.classify_many(messages)

        self.assertEqual(calls, [3, 2])
        self.assertEqual([item.source for item in results], ["split", "split", "split"])
        self.assertEqual(self.classifier.metrics_snapshot()["batch_splits"], 1)


if __name__ == "__main__":
    unittest.main()
