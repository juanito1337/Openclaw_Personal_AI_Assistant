from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from mail_agent.config import OllamaConfig
from personal_assistant.portfolio import EodhdClient, PortfolioService
from personal_assistant.portfolio_mapping import OllamaPortfolioMappingSelector
from personal_assistant.tool_settings import PortfolioToolSettings
from tests.test_portfolio_tool import ISIN, CleanAntivirus, csv_fixture


class Response:
    def __init__(self, payload: object) -> None:
        self.payload = payload

    def __enter__(self) -> Response:
        return self

    def __exit__(self, *args: object) -> bool:
        return False

    def read(self, limit: int) -> bytes:
        return json.dumps(self.payload).encode("utf-8")[:limit]


class EodhdMappingSearchTests(unittest.TestCase):
    @patch("personal_assistant.portfolio.urllib.request.urlopen")
    def test_search_by_isin_keeps_only_exact_supported_candidates(self, urlopen) -> None:
        urlopen.return_value = Response(
            [
                {
                    "Code": "BAS",
                    "Exchange": "XETRA",
                    "Name": "BASF SE",
                    "Currency": "EUR",
                    "ISIN": ISIN,
                    "isPrimary": True,
                },
                {
                    "Code": "WRONG",
                    "Exchange": "US",
                    "Name": "Other",
                    "Currency": "USD",
                    "ISIN": "US0378331005",
                    "isPrimary": True,
                },
                {
                    "Code": "BAS",
                    "Exchange": "F",
                    "Name": "BASF SE",
                    "Currency": "EUR",
                    "ISIN": ISIN,
                    "isPrimary": False,
                },
                {
                    "Code": "BAS;INVALID",
                    "Exchange": "XETRA",
                    "Name": "Untrusted provider text",
                    "Currency": "EUR",
                    "ISIN": ISIN,
                    "isPrimary": False,
                },
            ]
        )
        result = EodhdClient("secret-token").search_by_isin(ISIN)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["symbol"], "BAS")
        self.assertEqual(result[0]["allowed_mics"], ["XETR"])
        request = urlopen.call_args.args[0]
        self.assertIn(f"/search/{ISIN}?", request.full_url)
        self.assertIn("type=stock", request.full_url)


class OllamaMappingSelectorTests(unittest.TestCase):
    def test_selector_uses_schema_and_interactive_coordinator_headers(self) -> None:
        captured: list[object] = []

        def urlopen(request, *, timeout):
            captured.extend([request, timeout])
            return Response(
                {
                    "message": {
                        "content": json.dumps(
                            {
                                "status": "candidate",
                                "candidate_id": 1,
                                "mic": "XETR",
                                "confidence": 0.99,
                                "reason": "Primaere Xetra-Notierung",
                            }
                        )
                    }
                }
            )

        selector = OllamaPortfolioMappingSelector(
            OllamaConfig(
                base_url="http://ollama-proxy:11435",
                model="test-model",
                timeout_seconds=30,
                queue_timeout_seconds=20,
                request_timeout_margin_seconds=5,
            ),
            urlopen=urlopen,
        )
        result = selector.select(
            {
                "instrument": {"isin": ISIN, "holding_name": "BASF SE"},
                "candidates": [
                    {
                        "candidate_id": 1,
                        "symbol": "BAS",
                        "allowed_mics": ["XETR"],
                    }
                ],
            }
        )
        self.assertEqual(result["candidate_id"], 1)
        self.assertEqual(result["model"], "test-model")
        request = captured[0]
        self.assertEqual(request.headers["X-openclaw-priority"], "interactive")
        self.assertEqual(request.headers["X-openclaw-source"], "portfolio-mapping-suggest")
        payload = json.loads(request.data)
        self.assertEqual(payload["format"]["properties"]["candidate_id"]["type"], "integer")
        self.assertFalse(payload["think"])
        self.assertEqual(captured[1], 55)


class PortfolioMappingSuggestionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        inbox = root / "inbox"
        inbox.mkdir()
        (inbox / "depot.csv").write_bytes(csv_fixture())
        self.requests: list[dict[str, object]] = []

        def searcher(isin: str) -> list[dict[str, object]]:
            self.assertEqual(isin, ISIN)
            return [
                {
                    "isin": ISIN,
                    "name": "BASF SE",
                    "symbol": "BAS",
                    "exchange": "XETRA",
                    "currency": "EUR",
                    "is_primary": True,
                    "allowed_mics": ["XETR"],
                }
            ]

        def selector(request: dict[str, object]) -> dict[str, object]:
            self.requests.append(request)
            return {
                "status": "candidate",
                "candidate_id": 1,
                "mic": "XETR",
                "confidence": 0.98,
                "reason": "Exakter Providerkandidat",
                "model": "test-model",
            }

        self.service = PortfolioService(
            PortfolioToolSettings(
                enabled=True,
                database=root / "portfolio.sqlite3",
                import_root=inbox,
                provider="eodhd",
            ),
            CleanAntivirus(),  # type: ignore[arg-type]
            mapping_searcher=searcher,  # type: ignore[arg-type]
            mapping_selector=selector,  # type: ignore[arg-type]
        )
        self.service.import_csv("depot.csv", dry_run=False)

    def tearDown(self) -> None:
        self.service.close()
        self.temporary.cleanup()

    def test_suggestion_is_provider_bounded_and_does_not_store_mapping(self) -> None:
        with patch.dict("os.environ", {"PORTFOLIO_EODHD_API_KEY": "configured"}):
            result = self.service.mapping_suggest(ISIN)
        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "candidate")
        self.assertTrue(result["read_only"])
        self.assertFalse(result["stored"])
        self.assertEqual(result["candidate"]["provider_symbol"], "BAS.XETRA")
        self.assertEqual(result["approval"], "explicit-user-watchlist-change")
        candidates = self.requests[0]["candidates"]
        self.assertEqual(candidates[0]["allowed_mics"], ["XETR"])
        self.assertEqual(self.service.watchlist()["count"], 0)
        holding = next(
            item for item in self.service.holdings()["positions"] if item["isin"] == ISIN
        )
        self.assertEqual(holding["mapping_confirmed"], 0)

    def test_model_cannot_invent_provider_candidate(self) -> None:
        self.service._mapping_selector = lambda request: {
            "status": "candidate",
            "candidate_id": 99,
            "mic": "XETR",
            "confidence": 1,
            "reason": "invented",
        }
        with (
            patch.dict("os.environ", {"PORTFOLIO_EODHD_API_KEY": "configured"}),
            self.assertRaisesRegex(RuntimeError, "vorhandene EODHD-candidate_id"),
        ):
            self.service.mapping_suggest(ISIN)

    def test_model_cannot_invent_mic(self) -> None:
        self.service._mapping_selector = lambda request: {
            "status": "candidate",
            "candidate_id": 1,
            "mic": "XNYS",
            "confidence": 1,
            "reason": "wrong exchange",
        }
        with (
            patch.dict("os.environ", {"PORTFOLIO_EODHD_API_KEY": "configured"}),
            self.assertRaisesRegex(RuntimeError, "erlaubten MIC"),
        ):
            self.service.mapping_suggest(ISIN)

    def test_uncertain_selection_is_read_only_failure(self) -> None:
        self.service._mapping_selector = lambda request: {
            "status": "uncertain",
            "candidate_id": 0,
            "mic": "",
            "confidence": 0.4,
            "reason": "Boersenplatz nicht eindeutig",
            "model": "test-model",
        }
        with patch.dict("os.environ", {"PORTFOLIO_EODHD_API_KEY": "configured"}):
            result = self.service.mapping_suggest(ISIN)
        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "uncertain")
        self.assertFalse(result["stored"])
        self.assertEqual(self.service.watchlist()["count"], 0)


if __name__ == "__main__":
    unittest.main()
