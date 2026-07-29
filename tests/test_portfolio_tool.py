from __future__ import annotations

import tempfile
import unittest
from unittest.mock import patch
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from personal_assistant.antivirus import AntivirusResult
from personal_assistant.portfolio import (
    PortfolioService,
    Quote,
    TwelveDataClient,
    parse_portfolio_performance_xml,
)
from personal_assistant.tool_settings import PortfolioToolSettings
from personal_assistant.tool_settings import ToolSettings
from personal_assistant.tool_registry import build_tool_registry
from personal_assistant.job_control import default_job_specs
from personal_assistant.tool_setup import configure_portfolio_tools


ISIN = "DE000BASF111"


class CleanAntivirus:
    def scan_path(self, path: Path, *, source_type: str = "file") -> AntivirusResult:
        return AntivirusResult(
            status="clean",
            sha256="a" * 64,
            size_bytes=path.stat().st_size,
            source_type=source_type,
            name=path.name,
            scanner="test",
            scanner_identity="test",
        )


class MutableClock:
    def __init__(self, value: datetime) -> None:
        self.value = value

    def __call__(self) -> datetime:
        return self.value


def xml_fixture() -> bytes:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<client>
  <securities>
    <security>
      <uuid>sec-1</uuid>
      <name>BASF SE</name>
      <currencyCode>EUR</currencyCode>
      <isin>{ISIN}</isin>
      <tickerSymbol>BAS</tickerSymbol>
    </security>
  </securities>
  <positions>
    <position isin="{ISIN}" account="DKB" shares="12.5" as_of="2026-07-28"/>
  </positions>
</client>
""".encode()


class PortfolioParserTests(unittest.TestCase):
    def test_parses_structured_snapshot(self) -> None:
        parsed = parse_portfolio_performance_xml(xml_fixture())
        self.assertEqual(parsed["source_type"], "portfolio-performance-xml")
        self.assertEqual(parsed["instruments"][0]["isin"], ISIN)
        self.assertEqual(parsed["positions"][0]["shares"], "12.5")

    def test_rejects_doctype(self) -> None:
        with self.assertRaisesRegex(ValueError, "DTD"):
            parse_portfolio_performance_xml(
                b'<!DOCTYPE x [<!ENTITY y SYSTEM "file:///etc/passwd">]><client>&y;</client>'
            )

    def test_parses_portfolio_performance_relative_security_reference(self) -> None:
        data = b"""
        <client>
          <securities>
            <security>
              <uuid>sec-1</uuid><name>Adidas AG</name>
              <isin>DE000A1EWWW0</isin><currencyCode>EUR</currencyCode>
            </security>
          </securities>
          <portfolios>
            <portfolio>
              <name>DKB Depot</name>
              <transactions>
                <portfolio-transaction>
                  <date>2026-07-28</date>
                  <security reference="../../../../securities/security"/>
                  <shares>47000000</shares><type>BUY</type>
                </portfolio-transaction>
              </transactions>
            </portfolio>
          </portfolios>
        </client>
        """
        parsed = parse_portfolio_performance_xml(data)
        self.assertEqual(parsed["positions"][0]["account"], "DKB Depot")
        self.assertEqual(parsed["positions"][0]["shares"], "47")
        self.assertEqual(parsed["as_of"], "2026-07-28")

    def test_stock_split_adjusts_only_older_transactions(self) -> None:
        data = b"""
        <client>
          <securities>
            <security>
              <name>Split AG</name><isin>DE0007164600</isin>
              <currencyCode>EUR</currencyCode>
              <events><event><date>2025-01-01</date><type>STOCK_SPLIT</type>
                <details>2:1</details></event></events>
            </security>
          </securities>
          <portfolios><portfolio><name>Depot</name><transactions>
            <portfolio-transaction><date>2024-01-01</date>
              <security reference="../../../../securities/security"/>
              <shares>10000000</shares><type>BUY</type></portfolio-transaction>
            <portfolio-transaction><date>2025-02-01</date>
              <security reference="../../../../securities/security"/>
              <shares>5000000</shares><type>BUY</type></portfolio-transaction>
          </transactions></portfolio></portfolios>
        </client>
        """
        parsed = parse_portfolio_performance_xml(data)
        self.assertEqual(parsed["positions"][0]["shares"], "25")

    def test_portfolio_commands_are_registered_and_job_defaults_off(self) -> None:
        ids = {
            item.id for item in build_tool_registry(ToolSettings(path=Path("tools.toml")))
        }
        self.assertIn("portfolio.status", ids)
        self.assertIn("portfolio.setup", ids)
        self.assertIn("portfolio.import.pp", ids)
        self.assertIn("portfolio.import.pp.confirm", ids)
        self.assertIn("portfolio.quotes.refresh", ids)
        self.assertIn("portfolio.analyze", ids)
        self.assertIn("portfolio.job.on", ids)
        job = next(item for item in default_job_specs() if item.name == "portfolio")
        self.assertFalse(job.default_on)
        self.assertEqual(job.health_command[-2:], ("portfolio", "doctor"))

    def test_setup_requires_explicit_permission(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaisesRegex(PermissionError, "approve-permissions"):
                configure_portfolio_tools(
                    enable=True,
                    approve_permissions=False,
                    path=Path(folder) / "tools.toml",
                )

    @patch("personal_assistant.portfolio.urllib.request.urlopen")
    def test_provider_uses_intraday_interval_and_keeps_key_out_of_url(self, urlopen) -> None:
        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self, limit):
                self.limit = limit
                return (
                    b'{"symbol":"BAS","mic_code":"XETR","currency":"EUR",'
                    b'"timestamp":1785320100,"open":"45","high":"47","low":"44",'
                    b'"close":"46","volume":"1234"}'
                )

        urlopen.return_value = Response()
        quote = TwelveDataClient(
            "top-secret", interval_minutes=15
        ).fetch({"symbol": "BAS", "mic": "XETR", "currency": "EUR"})
        request = urlopen.call_args.args[0]
        self.assertIn("interval=15min", request.full_url)
        self.assertIn("timezone=UTC", request.full_url)
        self.assertNotIn("top-secret", request.full_url)
        self.assertEqual(request.headers["Authorization"], "apikey top-secret")
        self.assertEqual(quote.price, Decimal("46"))
        self.assertEqual(quote.volume, Decimal("1234"))


class PortfolioServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.inbox = root / "inbox"
        self.inbox.mkdir()
        self.xml = self.inbox / "depot.xml"
        self.xml.write_bytes(xml_fixture())
        self.clock = MutableClock(datetime(2026, 7, 29, 10, 0, tzinfo=timezone.utc))
        self.notifications: list[str] = []
        self.price = Decimal("100")
        self.quote_offset = timedelta()
        self.provider_market_open = None

        def fetch(instrument: dict[str, str]) -> Quote:
            return Quote(
                symbol=instrument["symbol"],
                price=self.price,
                currency="EUR",
                observed_at=(self.clock() + self.quote_offset).isoformat(),
                provider="test-provider",
                market_open=self.provider_market_open,
            )

        self.service = PortfolioService(
            PortfolioToolSettings(
                enabled=True,
                database=root / "portfolio.sqlite3",
                import_root=self.inbox,
                provider="twelve-data",
                interval_minutes=30,
                stale_warning_minutes=45,
                stale_critical_minutes=90,
            ),
            CleanAntivirus(),  # type: ignore[arg-type]
            quote_fetcher=fetch,
            notifier=lambda text: self.notifications.append(text) or {"attempted": True, "ok": True},
            now=self.clock,
        )

    def tearDown(self) -> None:
        self.service.close()
        self.temporary.cleanup()

    def _prepare(self) -> None:
        preview = self.service.import_pp("depot.xml", dry_run=True)
        self.assertTrue(preview["dry_run"])
        imported = self.service.import_pp("depot.xml", dry_run=False)
        self.assertFalse(imported["duplicate"])
        self.service.watchlist_add(
            isin=ISIN, name="BASF SE", symbol="BAS", mic="XETR", currency="EUR"
        )

    def test_import_is_immutable_and_idempotent(self) -> None:
        self._prepare()
        duplicate = self.service.import_pp("depot.xml", dry_run=False)
        self.assertTrue(duplicate["duplicate"])
        holdings = self.service.holdings()
        self.assertEqual(holdings["count"], 1)
        self.assertEqual(holdings["positions"][0]["shares"], "12.5")

    def test_import_cannot_escape_controlled_root(self) -> None:
        outside = Path(self.temporary.name) / "outside.xml"
        outside.write_bytes(xml_fixture())
        with self.assertRaisesRegex(PermissionError, "import_root"):
            self.service.import_pp(outside, dry_run=True)

    def test_missing_confirmed_mapping_fails_held_quote_refresh(self) -> None:
        self.service.import_pp("depot.xml", dry_run=False)
        result = self.service.refresh_quotes(force=True)
        self.assertFalse(result["ok"])
        self.assertEqual(result["held_missing"], 1)
        self.assertEqual(self.service.health()["state"], "failed")

    def test_refresh_health_and_due_interval(self) -> None:
        self._prepare()
        refreshed = self.service.refresh_quotes(force=True)
        self.assertTrue(refreshed["ok"])
        self.assertEqual(self.service.health()["state"], "healthy")
        skipped = self.service.refresh_quotes()
        self.assertEqual(skipped["status"], "skipped-not-due")

    def test_stale_source_degrades_then_fails_closed(self) -> None:
        self._prepare()
        self.quote_offset = -timedelta(minutes=60)
        warning = self.service.refresh_quotes(force=True)
        self.assertTrue(warning["ok"])
        self.assertEqual(warning["status"], "degraded")
        self.assertEqual(self.service.health()["held_stale_warning"], 1)
        self.clock.value += timedelta(minutes=31)
        self.quote_offset = -timedelta(hours=2)
        critical = self.service.refresh_quotes(force=True)
        self.assertFalse(critical["ok"])
        self.assertEqual(critical["status"], "failed")
        self.assertEqual(critical["held_missing"], 1)

    def test_provider_closed_market_suppresses_false_holiday_staleness(self) -> None:
        self._prepare()
        self.quote_offset = -timedelta(hours=2)
        self.provider_market_open = False
        result = self.service.refresh_quotes(force=True)
        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "success")
        self.assertEqual(self.service.health()["state"], "healthy")

    def test_analysis_abstains_on_critical_staleness(self) -> None:
        self._prepare()
        self.service.refresh_quotes(force=True)
        self.clock.value += timedelta(hours=2)
        result = self.service.analyze(ISIN)
        self.assertFalse(result["ok"])
        self.assertEqual(result["decision"], "abstain")
        self.assertIn("kritisch veraltet", result["reason"])

    def test_price_alert_notifies_once_per_crossing(self) -> None:
        self._prepare()
        self.service.alert_add(
            isin=ISIN,
            direction="above",
            threshold=Decimal("110"),
            currency="EUR",
        )
        self.price = Decimal("120")
        first = self.service.refresh_quotes(force=True)
        self.clock.value += timedelta(minutes=31)
        second = self.service.refresh_quotes(force=True)
        self.assertEqual(len(first["triggered_events"]), 1)
        self.assertEqual(len(second["triggered_events"]), 0)
        self.assertEqual(len(self.notifications), 1)
        self.assertIn("keine Orderempfehlung", self.notifications[0])

    def test_signal_performance_is_separate_and_honest(self) -> None:
        result = self.service.signal_performance()
        self.assertEqual(result["status"], "insufficient_data")
        self.assertEqual(result["sample_size"], 0)
        self.assertIsNone(result["forward_returns"])


if __name__ == "__main__":
    unittest.main()
