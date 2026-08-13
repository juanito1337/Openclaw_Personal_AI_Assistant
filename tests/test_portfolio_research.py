from __future__ import annotations

import json
import os
import tempfile
import unittest
import urllib.error
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from email.message import Message
from pathlib import Path
from typing import Any, cast
from unittest.mock import patch

from personal_assistant.cli import parser as cli_parser
from personal_assistant.portfolio import PortfolioService
from personal_assistant.portfolio_research import (
    RESEARCH_MODEL_VERSION,
    EodhdResearchClient,
    ResearchProviderError,
    analyze_research_payload,
    research_models,
)
from personal_assistant.tool_registry import build_tool_registry
from personal_assistant.tool_settings import PortfolioToolSettings, ToolSettings

AAPL_ISIN = "US0378331005"
TSLA_ISIN = "US88160R1014"


class CleanAntivirus:
    pass


class MutableClock:
    def __init__(self, value: datetime) -> None:
        self.value = value

    def __call__(self) -> datetime:
        return self.value


def fundamental_fixture(
    *,
    ticker: str = "AAPL.US",
    isin: str = AAPL_ISIN,
    name: str = "Apple Inc.",
    sector: str = "Technology",
    pe: float = 18.0,
) -> dict[str, object]:
    return {
        "General": {
            "PrimaryTicker": ticker,
            "ISIN": isin,
            "Name": name,
            "Exchange": "NASDAQ",
            "CountryISO": "US",
            "CurrencyCode": "USD",
            "Sector": sector,
            "Industry": "Software",
        },
        "Highlights": {
            "ReturnOnEquityTTM": 0.22,
            "ReturnOnAssetsTTM": 0.11,
            "OperatingMarginTTM": 0.24,
            "RevenueTTM": 1000,
            "PERatio": pe,
            "PEGRatio": 1.3,
            "QuarterlyRevenueGrowthYOY": 0.12,
            "QuarterlyEarningsGrowthYOY": 0.16,
            "DividendYield": 0.025,
            "PayoutRatio": 0.45,
        },
        "Valuation": {
            "TrailingPE": pe,
            "ForwardPE": 17,
            "EnterpriseValueEbitda": 11,
        },
        "Technicals": {"Beta": 0.95},
        "Financials": {
            "Income_Statement": {"yearly": {"2025-12-31": {"date": "2025-12-31", "totalRevenue": "1000"}}},
            "Balance_Sheet": {
                "yearly": {
                    "2025-12-31": {
                        "date": "2025-12-31",
                        "totalStockholderEquity": "500",
                        "shortLongTermDebtTotal": "250",
                    }
                }
            },
            "Cash_Flow": {
                "yearly": {
                    "2025-12-31": {
                        "date": "2025-12-31",
                        "totalCashFromOperatingActivities": "180",
                        "capitalExpenditures": "-40",
                    }
                }
            },
        },
    }


def history_fixture(*, days: int = 300, last_date: date = date(2026, 8, 13)) -> list[dict[str, object]]:
    start = last_date - timedelta(days=days - 1)
    return [
        {
            "date": (start + timedelta(days=index)).isoformat(),
            "adjusted_close": 80 + index * 0.12 + (index % 7) * 0.03,
            "volume": 1_000_000 + index,
        }
        for index in range(days)
    ]


class FakeResearchProvider:
    def __init__(self) -> None:
        self.screen_calls: list[dict[str, object]] = []
        self.payloads = {
            "AAPL.US": fundamental_fixture(),
            "TSLA.US": fundamental_fixture(
                ticker="TSLA.US",
                isin=TSLA_ISIN,
                name="Tesla Inc.",
                sector="Consumer Cyclical",
                pe=45,
            ),
        }

    def screen(self, **kwargs):
        self.screen_calls.append(kwargs)
        return [
            {"ticker": "TSLA.US", "name": "Tesla Inc.", "sector": "Consumer Cyclical"},
            {"ticker": "AAPL.US", "name": "Apple Inc.", "sector": "Technology"},
        ]

    def fundamentals(self, ticker: str):
        return self.payloads[ticker]

    def history(self, ticker: str, *, from_date: date):
        assert from_date < date(2026, 1, 1)
        return history_fixture()


class EodhdResearchClientTests(unittest.TestCase):
    def test_screener_uses_only_bounded_filters_and_normalizes_rows(self) -> None:
        captured = {}

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self, limit):
                captured["limit"] = limit
                return json.dumps(
                    {
                        "data": [
                            {
                                "code": "AAPL",
                                "exchange": "US",
                                "name": "Apple Inc.",
                                "sector": "Technology",
                                "market_capitalization": 1_000_000_000,
                                "earnings_share": 4.2,
                            }
                        ]
                    }
                ).encode()

        def urlopen(request, timeout):
            captured["url"] = request.full_url
            captured["timeout"] = timeout
            return Response()

        rows = EodhdResearchClient("secret", urlopen=urlopen).screen(
            strategy="quality-value", exchange="US", sector="Technology", limit=5
        )
        self.assertEqual(rows[0]["ticker"], "AAPL.US")
        query = captured["url"]
        self.assertIn("market_capitalization", query)
        self.assertIn("avgvol_200d", query)
        self.assertIn("api_token=secret", query)
        self.assertEqual(captured["limit"], 2_000_001)

    def test_provider_error_redacts_secret(self) -> None:
        def urlopen(request, timeout):
            raise urllib.error.HTTPError(
                request.full_url,
                403,
                "forbidden",
                Message(),
                None,
            )

        client = EodhdResearchClient("top-secret", urlopen=urlopen)
        with self.assertRaisesRegex(RuntimeError, "HTTP 403") as raised:
            client.history("AAPL.US", from_date=date(2025, 1, 1))
        self.assertNotIn("top-secret", str(raised.exception))

    def test_forbidden_screener_is_classified_as_non_retryable_entitlement_failure(self) -> None:
        def urlopen(request, timeout):
            raise urllib.error.HTTPError(
                request.full_url,
                403,
                "forbidden",
                Message(),
                None,
            )

        client = EodhdResearchClient("top-secret", urlopen=urlopen)
        with self.assertRaises(ResearchProviderError) as raised:
            client.screen(strategy="quality-value", exchange="US", limit=5)
        rendered = raised.exception.render()
        self.assertEqual(rendered["endpoint"], "screener")
        self.assertEqual(rendered["status_code"], 403)
        self.assertEqual(rendered["category"], "provider-entitlement-denied")
        self.assertFalse(rendered["retryable"])
        self.assertNotIn("top-secret", json.dumps(rendered))

    def test_ticker_validation_blocks_path_and_query_injection(self) -> None:
        for value in ("../../secret", "AAPL.US?api_token=x", "AAPL..US", ""):
            with self.subTest(value=value), self.assertRaises(ValueError):
                EodhdResearchClient.validate_ticker(value)


class DeterministicResearchTests(unittest.TestCase):
    def test_complete_evidence_produces_versioned_explainable_score(self) -> None:
        result = analyze_research_payload(
            fundamental_fixture(),
            history_fixture(),
            strategy="quality-value",
            expected_ticker="AAPL.US",
            expected_isin=AAPL_ISIN,
            now=datetime(2026, 8, 13, 12, tzinfo=UTC),
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["decision"], "informational")
        self.assertEqual(result["model_version"], RESEARCH_MODEL_VERSION)
        self.assertGreaterEqual(result["metric_coverage"], 0.7)
        self.assertIsInstance(result["score"], float)
        self.assertTrue(result["metrics"])
        self.assertTrue(all(item["provider"] == "eodhd" for item in result["metrics"]))

    def test_same_provider_evidence_always_produces_same_score(self) -> None:
        now = datetime(2026, 8, 13, 12, tzinfo=UTC)
        first = analyze_research_payload(
            fundamental_fixture(), history_fixture(), strategy="balanced", now=now
        )
        second = analyze_research_payload(
            fundamental_fixture(), history_fixture(), strategy="balanced", now=now
        )
        self.assertEqual(first["score"], second["score"])
        self.assertEqual(first["pillars"], second["pillars"])
        self.assertEqual(first["verdict"], second["verdict"])

    def test_missing_history_fails_closed_without_score(self) -> None:
        result = analyze_research_payload(
            fundamental_fixture(),
            history_fixture(days=30),
            strategy="balanced",
            now=datetime(2026, 8, 13, 12, tzinfo=UTC),
        )
        self.assertFalse(result["ok"])
        self.assertEqual(result["decision"], "abstain")
        self.assertEqual(result["verdict"], "abstain")
        self.assertIsNone(result["score"])
        self.assertTrue(any("200" in blocker for blocker in result["blockers"]))

    def test_stale_history_fails_closed_without_score(self) -> None:
        result = analyze_research_payload(
            fundamental_fixture(),
            history_fixture(last_date=date(2026, 8, 1)),
            strategy="balanced",
            now=datetime(2026, 8, 13, 12, tzinfo=UTC),
        )
        self.assertFalse(result["ok"])
        self.assertEqual(result["verdict"], "abstain")
        self.assertIsNone(result["score"])
        self.assertTrue(any("sieben" in blocker for blocker in result["blockers"]))

    def test_missing_explicit_debt_does_not_substitute_total_liabilities(self) -> None:
        fundamentals = fundamental_fixture()
        financials = cast(dict[str, Any], fundamentals["Financials"])
        balance_sheet = cast(dict[str, Any], financials["Balance_Sheet"])
        yearly = cast(dict[str, Any], balance_sheet["yearly"])
        balance = cast(dict[str, Any], yearly["2025-12-31"])
        balance.pop("shortLongTermDebtTotal")
        balance["totalLiab"] = "900"
        result = analyze_research_payload(
            fundamentals,
            history_fixture(),
            strategy="balanced",
            now=datetime(2026, 8, 13, 12, tzinfo=UTC),
        )
        debt = next(item for item in result["metrics"] if item["key"] == "debt_to_equity")
        self.assertIsNone(debt["value"])
        self.assertIsNone(debt["score"])

    def test_identity_mismatch_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "erwarteten Ticker"):
            analyze_research_payload(
                fundamental_fixture(),
                history_fixture(),
                strategy="balanced",
                expected_ticker="MSFT.US",
            )

    def test_models_publish_weights_and_abstention_contract(self) -> None:
        result = research_models()
        self.assertTrue(result["ok"])
        self.assertEqual(result["model_version"], RESEARCH_MODEL_VERSION)
        self.assertEqual(sum(result["strategies"][0]["weights"].values()), 100)
        self.assertIn("abstain", result["verdicts"])


class PortfolioResearchServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.environment = patch.dict(os.environ, {"PORTFOLIO_EODHD_API_KEY": "test-secret"})
        self.environment.start()
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.settings = PortfolioToolSettings(
            enabled=True,
            database=root / "portfolio.sqlite3",
            import_root=root / "inbox",
            provider="eodhd",
        )
        self.clock = MutableClock(datetime(2026, 8, 13, 12, tzinfo=UTC))
        self.provider = FakeResearchProvider()
        self.service = PortfolioService(
            self.settings,
            CleanAntivirus(),  # type: ignore[arg-type]
            research_provider=self.provider,
            now=self.clock,
        )

    def tearDown(self) -> None:
        self.service.close()
        self.temporary.cleanup()
        self.environment.stop()

    def test_screen_ranks_candidates_and_persists_auditable_evidence(self) -> None:
        initial = self.service.research_status()
        self.assertFalse(initial["ok"])
        self.assertTrue(initial["configuration_ok"])
        self.assertEqual(initial["state"], "unverified")
        result = self.service.research_screen(strategy="quality-value", limit=2)
        self.assertTrue(result["ok"])
        self.assertEqual(result["analyzed"], 2)
        self.assertEqual(result["candidates"][0]["identity"]["ticker"], "AAPL.US")
        self.assertTrue(result["candidates"][0]["candidate_id"])
        history = self.service.research_history()
        self.assertEqual(history["count"], 1)
        self.assertEqual(history["runs"][0]["model_version"], RESEARCH_MODEL_VERSION)
        status = self.service.research_status()
        self.assertTrue(status["ok"])
        self.assertEqual(status["state"], "healthy")
        self.assertEqual(status["entitlement"]["state"], "verified")

    def test_screen_provider_entitlement_failure_abstains_and_is_audited(self) -> None:
        class DeniedProvider(FakeResearchProvider):
            def screen(self, **kwargs):
                raise ResearchProviderError(
                    "HTTP 403",
                    endpoint="screener",
                    status_code=403,
                )

        self.service.close()
        self.service = PortfolioService(
            self.settings,
            CleanAntivirus(),  # type: ignore[arg-type]
            research_provider=DeniedProvider(),
            now=self.clock,
        )
        result = self.service.research_screen(strategy="quality-value", exchange="US", limit=5)
        self.assertFalse(result["ok"])
        self.assertEqual(result["decision"], "abstain")
        self.assertEqual(result["provider_candidates"], 0)
        self.assertEqual(result["failures"][0]["endpoint"], "screener")
        self.assertEqual(result["failures"][0]["category"], "provider-entitlement-denied")
        self.assertFalse(result["failures"][0]["retryable"])
        history = self.service.research_history()
        self.assertEqual(history["runs"][0]["status"], "failed")
        self.assertIn("screener", history["runs"][0]["error"])
        self.assertIn("HTTP 403", history["runs"][0]["error"])
        status = self.service.research_status()
        self.assertFalse(status["ok"])
        self.assertEqual(status["state"], "failed")
        self.assertEqual(status["entitlement"]["state"], "denied")

    def test_screen_excludes_existing_watchlist_identity(self) -> None:
        self.service.watchlist_add(
            isin=AAPL_ISIN,
            name="Apple Inc.",
            symbol="AAPL",
            mic="XNAS",
            currency="USD",
        )
        result = self.service.research_screen(strategy="balanced", limit=2)
        tickers = [item["identity"]["ticker"] for item in result["candidates"]]
        self.assertNotIn("AAPL.US", tickers)
        self.assertIn("TSLA.US", tickers)

    def test_profile_is_append_only_and_never_changed_by_feedback(self) -> None:
        first = self.service.philosophy_set(
            risk_tolerance="balanced",
            horizon_years=10,
            strategy="quality-value",
            max_position_pct=Decimal("20"),
            max_sector_pct=Decimal("35"),
            preferred_sectors=["Technology"],
            excluded_sectors=["Tobacco"],
            notes="Long term",
        )
        self.assertEqual(first["new_version"], 1)
        screen = self.service.research_screen(strategy="auto", limit=1)
        candidate_id = screen["candidates"][0]["candidate_id"]
        feedback = self.service.philosophy_feedback(
            candidate_id=candidate_id,
            decision="interested",
            reason="Qualitaet und Bewertung passen zum Profil",
        )
        self.assertFalse(feedback["declared_profile_changed"])
        self.assertEqual(self.service.philosophy_show()["profile"]["version"], 1)
        second = self.service.philosophy_set(
            risk_tolerance="balanced",
            horizon_years=12,
            strategy="quality-growth",
            max_position_pct=Decimal("18"),
            max_sector_pct=Decimal("30"),
            preferred_sectors=["Technology"],
            excluded_sectors=["Tobacco"],
        )
        self.assertEqual(second["new_version"], 2)
        self.assertEqual(self.service.philosophy_history()["count"], 2)

    def test_unknown_feedback_candidate_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "unbekannt"):
            self.service.philosophy_feedback(
                candidate_id="not-a-candidate",
                decision="rejected",
                reason="Nicht passend",
            )

    def test_review_without_declared_profile_does_not_invent_criticism(self) -> None:
        result = self.service.philosophy_review()
        self.assertTrue(result["ok"])
        self.assertFalse(result["profile"]["configured"])
        self.assertEqual(result["praise"], [])
        self.assertEqual(result["critique"], [])
        self.assertTrue(result["limitations"])
        self.assertFalse(result["learning"]["automatic_profile_changes"])

    def test_schema_version_contains_research_and_profile_tables(self) -> None:
        version = self.service.store.connection.execute(
            "SELECT value FROM schema_meta WHERE key='schema_version'"
        ).fetchone()[0]
        self.assertEqual(version, "5")
        tables = {
            row[0]
            for row in self.service.store.connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        self.assertTrue(
            {"research_runs", "research_candidates", "investment_profiles", "investment_feedback"} <= tables
        )

    def test_schema_four_is_upgraded_without_losing_existing_portfolio_data(self) -> None:
        self.service.watchlist_add(
            isin=AAPL_ISIN,
            name="Apple Inc.",
            symbol="AAPL",
            mic="XNAS",
            currency="USD",
        )
        connection = self.service.store.connection
        with connection:
            connection.execute("DROP TABLE investment_feedback")
            connection.execute("DROP TABLE investment_profiles")
            connection.execute("DROP TABLE research_candidates")
            connection.execute("DROP TABLE research_runs")
            connection.execute("UPDATE schema_meta SET value='4' WHERE key='schema_version'")
        self.service.close()
        self.service = PortfolioService(
            self.settings,
            CleanAntivirus(),  # type: ignore[arg-type]
            research_provider=self.provider,
            now=self.clock,
        )
        self.assertEqual(self.service.watchlist()["items"][0]["isin"], AAPL_ISIN)
        version = self.service.store.connection.execute(
            "SELECT value FROM schema_meta WHERE key='schema_version'"
        ).fetchone()[0]
        self.assertEqual(version, "5")


class PortfolioResearchContractTests(unittest.TestCase):
    def test_cli_accepts_research_and_philosophy_commands(self) -> None:
        screen = cli_parser().parse_args(
            [
                "portfolio",
                "research",
                "screen",
                "--strategy",
                "quality-value",
                "--exchange",
                "US",
                "--limit",
                "5",
            ]
        )
        self.assertEqual(screen.research_command, "screen")
        self.assertEqual(screen.strategy, "quality-value")
        profile = cli_parser().parse_args(
            [
                "portfolio",
                "philosophy",
                "set",
                "--risk-tolerance",
                "balanced",
                "--horizon-years",
                "10",
                "--strategy",
                "balanced",
                "--max-position-pct",
                "20",
                "--max-sector-pct",
                "35",
                "--yes",
            ]
        )
        self.assertTrue(profile.yes)

    def test_all_research_tools_are_registered(self) -> None:
        ids = {item.id for item in build_tool_registry(ToolSettings(path=Path("tools.toml")))}
        self.assertTrue(
            {
                "portfolio.research.status",
                "portfolio.research.models",
                "portfolio.research.screen",
                "portfolio.research.analyze",
                "portfolio.research.history",
                "portfolio.philosophy.show",
                "portfolio.philosophy.review",
                "portfolio.philosophy.history",
                "portfolio.philosophy.set",
                "portfolio.philosophy.feedback",
            }
            <= ids
        )


if __name__ == "__main__":
    unittest.main()
