from __future__ import annotations

import os
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from personal_assistant.antivirus import AntivirusResult
from personal_assistant.cli import parser as cli_parser
from personal_assistant.connectors.nextcloud.files import NextcloudFiles, RemoteFile
from personal_assistant.job_control import default_job_specs
from personal_assistant.models import PolicyDecision
from personal_assistant.portfolio import (
    EodhdClient,
    FxQuote,
    PortfolioService,
    Quote,
    parse_dkb_portfolio_csv,
    parse_portfolio_performance_xml,
)
from personal_assistant.service import PersonalAssistant
from personal_assistant.tool_registry import build_tool_registry
from personal_assistant.tool_settings import PortfolioToolSettings, ToolSettings, load_tool_settings
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


def csv_fixture() -> bytes:
    text = (
        "Datum der Erstellung;Depotnummer;Wertpapierbezeichnung;WKN;ISIN;"
        "Einstiegskurs;Bewertungskurs;Stückzahl;Absoluter Gewinn;Relativer Gewinn;Assetklasse\r\n"
        f"31.07.2026;123456789;BASF SE;BASF11;{ISIN};45,10 €;46,20 €;12,5;13,75 €;2.44%;Aktien\r\n"
        "31.07.2026;123456789;ADIDAS AG;A1EWWW;DE000A1EWWW0;"
        "180,00 €;190,00 €;2;20,00 €;5.55%;Aktien\r\n"
    )
    return b"\xef\xbb\xbf" + text.encode("utf-8")


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

    def test_parses_strict_dkb_csv_snapshot(self) -> None:
        parsed = parse_dkb_portfolio_csv(csv_fixture())
        self.assertEqual(parsed["source_type"], "dkb-depot-csv")
        self.assertEqual(parsed["as_of"], "2026-07-31")
        self.assertEqual(len(parsed["instruments"]), 2)
        self.assertEqual(parsed["positions"][0]["account"], "123456789")
        self.assertEqual(parsed["positions"][0]["shares"], "2")
        self.assertEqual(parsed["positions"][0]["entry_price"], "180.00")
        self.assertEqual(parsed["positions"][0]["valuation_price"], "190.00")
        self.assertEqual(parsed["positions"][0]["absolute_gain"], "20.00")
        self.assertEqual(parsed["positions"][0]["relative_gain_percent"], "5.55")
        self.assertEqual(parsed["positions"][0]["asset_class"], "Aktien")
        self.assertEqual(parsed["positions"][0]["snapshot_currency"], "EUR")
        self.assertTrue(all(item["currency"] == "EUR" for item in parsed["instruments"]))

    def test_dkb_csv_rejects_inconsistent_snapshot_dates(self) -> None:
        data = csv_fixture().replace(b"31.07.2026;123456789;ADIDAS", b"30.07.2026;123456789;ADIDAS")
        with self.assertRaisesRegex(ValueError, "mehrere unterschiedliche Stichtage"):
            parse_dkb_portfolio_csv(data)

    def test_dkb_csv_accepts_empty_optional_gain_values(self) -> None:
        data = csv_fixture().replace(
            b"13,75\xc2\xa0\xe2\x82\xac;2.44%",
            b";",
        )
        parsed = parse_dkb_portfolio_csv(data)
        basf = next(item for item in parsed["positions"] if item["isin"] == ISIN)
        self.assertEqual(basf["entry_price"], "45.10")
        self.assertEqual(basf["valuation_price"], "46.20")
        self.assertEqual(basf["absolute_gain"], "")
        self.assertEqual(basf["relative_gain_percent"], "")

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
        self.assertIn("portfolio.import.csv", ids)
        self.assertIn("portfolio.import.csv.nextcloud", ids)
        self.assertIn("portfolio.import.csv.confirm", ids)
        self.assertIn("portfolio.import.csv.nextcloud.confirm", ids)
        self.assertIn("portfolio.quotes.refresh", ids)
        self.assertIn("portfolio.quotes.get", ids)
        self.assertIn("portfolio.mapping.suggest", ids)
        self.assertIn("portfolio.mapping.discover", ids)
        self.assertIn("portfolio.valuation", ids)
        self.assertIn("portfolio.analyze", ids)
        self.assertIn("portfolio.job.on", ids)
        job = next(item for item in default_job_specs() if item.name == "portfolio")
        self.assertFalse(job.default_on)
        self.assertEqual(job.health_command[-2:], ("portfolio", "doctor"))

    def test_cli_accepts_local_and_nextcloud_csv_sources(self) -> None:
        local = cli_parser().parse_args(
            ["portfolio", "import-csv", "--file", "snapshot.csv", "--dry-run"]
        )
        self.assertEqual(local.file, "snapshot.csv")
        self.assertTrue(local.dry_run)
        remote = cli_parser().parse_args(
            [
                "portfolio",
                "import-csv",
                "--nextcloud-path",
                "Assistent/Finanzen/Portfolio/snapshot-31.07.2026.csv",
                "--yes",
            ]
        )
        self.assertEqual(
            remote.nextcloud_path,
            "Assistent/Finanzen/Portfolio/snapshot-31.07.2026.csv",
        )
        self.assertTrue(remote.yes)
        quote = cli_parser().parse_args(
            ["portfolio", "quotes", "get", "--isin", ISIN]
        )
        self.assertEqual(quote.isin, ISIN)
        mapping = cli_parser().parse_args(
            ["portfolio", "mapping", "suggest", "--isin", ISIN]
        )
        self.assertEqual(mapping.mapping_command, "suggest")
        self.assertEqual(mapping.isin, ISIN)
        discovery = cli_parser().parse_args(
            ["portfolio", "mapping", "suggest", "--query", "BAE Systems"]
        )
        self.assertEqual(discovery.query, "BAE Systems")
        self.assertIsNone(discovery.isin)
        valuation = cli_parser().parse_args(["portfolio", "valuation"])
        self.assertEqual(valuation.portfolio_command, "valuation")
        free_interval = cli_parser().parse_args(
            [
                "setup", "portfolio", "--provider", "eodhd",
                "--interval-minutes", "90", "--approve-permissions",
            ]
        )
        self.assertEqual(free_interval.interval_minutes, 90)

    def test_setup_requires_explicit_permission(self) -> None:
        with (
            tempfile.TemporaryDirectory() as folder,
            self.assertRaisesRegex(PermissionError, "approve-permissions"),
        ):
            configure_portfolio_tools(
                enable=True,
                approve_permissions=False,
                path=Path(folder) / "tools.toml",
            )

    def test_setup_selects_eodhd_secret_and_fifteen_minute_interval(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            result = configure_portfolio_tools(
                enable=True,
                provider="eodhd",
                interval_minutes=15,
                approve_permissions=True,
                path=Path(folder) / "tools.toml",
            )
        self.assertEqual(result["portfolio"]["provider"], "eodhd")
        self.assertEqual(result["api_key_env"], "PORTFOLIO_EODHD_API_KEY")
        self.assertEqual(result["portfolio"]["interval_minutes"], 15)

    def test_setup_supports_conservative_free_account_interval(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            result = configure_portfolio_tools(
                enable=True,
                provider="eodhd",
                interval_minutes=90,
                approve_permissions=True,
                path=Path(folder) / "tools.toml",
            )
        self.assertEqual(result["portfolio"]["interval_minutes"], 90)
        self.assertEqual(result["portfolio"]["stale_warning_minutes"], 110)
        self.assertEqual(result["portfolio"]["stale_critical_minutes"], 180)

    def test_legacy_twelve_data_config_migrates_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "tools.toml"
            path.write_text(
                "[portfolio]\n"
                "enabled = true\n"
                'provider = "twelve-data"\n'
                'api_key_env = "PORTFOLIO_MARKET_DATA_API_KEY"\n',
                encoding="utf-8",
            )
            settings = load_tool_settings(path)
        self.assertEqual(settings.portfolio.provider, "disabled")
        self.assertEqual(settings.portfolio.api_key_env, "PORTFOLIO_EODHD_API_KEY")

    @patch("personal_assistant.portfolio.urllib.request.urlopen")
    def test_eodhd_provider_batches_symbols_and_parses_delayed_quotes(self, urlopen) -> None:
        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self, limit):
                self.limit = limit
                return (
                    b'[{"code":"BAS.XETRA","timestamp":1785320100,"open":45,'
                    b'"high":47,"low":44,"close":46,"volume":1234},'
                    b'{"code":"TSLA.US","timestamp":1785320100,"open":300,'
                    b'"high":310,"low":295,"close":305,"volume":4321},'
                    b'{"code":"EURUSD.FOREX","timestamp":1785320100,'
                    b'"close":1.16}]'
                )

        urlopen.return_value = Response()
        quotes, fx_quotes = EodhdClient("top-secret").fetch_market_data(
            [
                {"isin": ISIN, "symbol": "BAS", "mic": "XETR", "currency": "EUR"},
                {
                    "isin": "US88160R1014", "symbol": "TSLA", "mic": "XNGS",
                    "currency": "USD",
                },
            ],
            [("EUR", "USD")],
        )
        request = urlopen.call_args.args[0]
        self.assertIn("/BAS.XETRA?", request.full_url)
        self.assertIn("s=TSLA.US", request.full_url)
        self.assertIn("EURUSD.FOREX", request.full_url)
        self.assertIn("fmt=json", request.full_url)
        self.assertEqual(quotes[ISIN].price, Decimal("46"))
        self.assertEqual(quotes[ISIN].volume, Decimal("1234"))
        self.assertEqual(quotes["US88160R1014"].currency, "USD")
        self.assertEqual(fx_quotes[("EUR", "USD")].rate, Decimal("1.16"))

    def test_eodhd_errors_never_expose_api_key(self) -> None:
        error = EodhdClient("top-secret")._provider_error(
            b'{"message":"invalid top-secret"}', "fallback top-secret"
        )
        self.assertNotIn("top-secret", str(error))
        self.assertIn("<redacted>", str(error))


class PortfolioServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.inbox = root / "inbox"
        self.inbox.mkdir()
        self.xml = self.inbox / "depot.xml"
        self.xml.write_bytes(xml_fixture())
        self.csv = self.inbox / "depot-export-31.07.2026.csv"
        self.csv.write_bytes(csv_fixture())
        self.clock = MutableClock(datetime(2026, 7, 29, 10, 0, tzinfo=UTC))
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
                provider="eodhd",
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

    def test_dkb_csv_import_is_previewed_and_idempotent(self) -> None:
        preview = self.service.import_csv(self.csv.name, dry_run=True)
        self.assertTrue(preview["dry_run"])
        self.assertEqual(preview["source_type"], "dkb-depot-csv")
        self.assertEqual(preview["as_of"], "2026-07-31")
        imported = self.service.import_csv(self.csv.name, dry_run=False)
        self.assertFalse(imported["duplicate"])
        duplicate = self.service.import_csv(self.csv.name, dry_run=False)
        self.assertTrue(duplicate["duplicate"])
        holdings = self.service.holdings()
        self.assertEqual(holdings["count"], 2)
        basf = next(item for item in holdings["positions"] if item["isin"] == ISIN)
        self.assertEqual(basf["entry_price"], "45.10")
        self.assertEqual(basf["valuation_price"], "46.20")
        self.assertEqual(basf["absolute_gain"], "13.75")
        self.assertEqual(basf["relative_gain_percent"], "2.44")
        self.assertEqual(basf["asset_class"], "Aktien")
        self.assertEqual(basf["currency"], "EUR")

    def test_duplicate_dkb_csv_backfills_metrics_from_same_verified_sha(self) -> None:
        imported = self.service.import_csv(self.csv.name, dry_run=False)
        import_id = imported["import_id"]
        with self.service.store.connection:
            self.service.store.connection.execute(
                """
                UPDATE position_snapshots
                SET entry_price='',valuation_price='',absolute_gain='',
                    relative_gain_percent='',asset_class='',snapshot_currency=''
                WHERE import_id=?
                """,
                (import_id,),
            )
        duplicate = self.service.import_csv(self.csv.name, dry_run=False)
        self.assertTrue(duplicate["duplicate"])
        self.assertEqual(duplicate["snapshot_metrics_backfilled"], 2)
        self.assertEqual(duplicate["snapshot_currency_backfilled"], 2)
        holdings = self.service.holdings()
        self.assertTrue(all(item["entry_price"] for item in holdings["positions"]))

    def test_holdings_keep_snapshot_currency_separate_from_quote_currency(self) -> None:
        self.service.import_csv(self.csv.name, dry_run=False)
        unmapped = next(
            item for item in self.service.holdings()["positions"] if item["isin"] == ISIN
        )
        self.assertEqual(unmapped["currency"], "EUR")
        self.assertEqual(unmapped["quote_currency"], "")
        self.service.watchlist_add(
            isin=ISIN, name="BASF SE", symbol="BAS", mic="XETR", currency="USD"
        )
        basf = next(
            item for item in self.service.holdings()["positions"] if item["isin"] == ISIN
        )
        self.assertEqual(basf["currency"], "EUR")
        self.assertEqual(basf["quote_currency"], "USD")

    def test_holdings_never_claim_blank_symbol_mapping_is_confirmed(self) -> None:
        self.service.import_csv(self.csv.name, dry_run=False)
        with self.service.store.connection:
            self.service.store.connection.execute(
                "UPDATE instruments SET mapping_confirmed=1,symbol='',mic='' WHERE isin=?",
                (ISIN,),
            )
        basf = next(
            item for item in self.service.holdings()["positions"] if item["isin"] == ISIN
        )
        self.assertEqual(basf["mapping_confirmed"], 0)
        self.assertEqual(basf["quote_currency"], "")

    def test_status_exposes_configuration_blockers_before_quote_refresh(self) -> None:
        with patch.dict(os.environ, {"PORTFOLIO_EODHD_API_KEY": ""}):
            status = self.service.status()
            doctor = self.service.doctor()
        self.assertFalse(status["configuration"]["ok"])
        self.assertFalse(status["configuration"]["api_key_present"])
        self.assertTrue(status["configuration"]["provider_ok"])
        self.assertTrue(status["configuration"]["import_root_present"])
        self.assertFalse(status["ok"])
        self.assertFalse(doctor["configuration_ok"])
        self.assertFalse(doctor["api_key_present"])

        with patch.dict(os.environ, {"PORTFOLIO_EODHD_API_KEY": "configured"}):
            configured = self.service.status()["configuration"]
        self.assertTrue(configured["ok"])
        self.assertTrue(configured["api_key_present"])

    def test_new_dkb_snapshot_preserves_confirmed_quote_currency(self) -> None:
        self.service.import_csv(self.csv.name, dry_run=False)
        self.service.watchlist_add(
            isin=ISIN, name="BASF SE", symbol="BAS", mic="XETR", currency="USD"
        )
        newer = self.inbox / "depot-export-01.08.2026.csv"
        newer.write_bytes(csv_fixture().replace(b"31.07.2026", b"01.08.2026"))
        imported = self.service.import_csv(newer.name, dry_run=False)
        self.assertFalse(imported["duplicate"])
        basf = next(
            item for item in self.service.holdings()["positions"] if item["isin"] == ISIN
        )
        self.assertEqual(basf["currency"], "EUR")
        self.assertEqual(basf["quote_currency"], "USD")
        self.assertEqual(basf["mapping_confirmed"], 1)

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

    def test_refresh_deduplicates_holdings_and_enabled_watchlist(self) -> None:
        self._prepare()
        second_isin = "DE000A1EWWW0"
        self.service.watchlist_add(
            isin=second_isin, name="ADIDAS AG", symbol="ADS", mic="XETR", currency="EUR"
        )
        fetched: list[str] = []

        def fetch(instrument: dict[str, str]) -> Quote:
            fetched.append(instrument["isin"])
            return Quote(
                symbol=instrument["symbol"], price=Decimal("100"), currency="EUR",
                observed_at=self.clock().isoformat(), provider="test-provider",
            )

        self.service._quote_fetcher = fetch
        result = self.service.refresh_quotes(force=True)
        self.assertTrue(result["ok"])
        self.assertEqual(result["expected"], 2)
        self.assertEqual(sorted(fetched), sorted([ISIN, second_isin]))

    def test_non_retryable_provider_failure_enters_daily_cooldown(self) -> None:
        self._prepare()
        self.service._quote_fetcher = lambda instrument: (_ for _ in ()).throw(
            RuntimeError("EODHD-Marktdatenfehler: HTTP 402")
        )
        first = self.service.refresh_quotes(force=True)
        self.assertFalse(first["ok"])
        self.clock.value += timedelta(minutes=15)
        skipped = self.service.refresh_quotes()
        self.assertFalse(skipped["ok"])
        self.assertEqual(skipped["status"], "skipped-provider-cooldown")
        self.assertEqual(skipped["reason"], "HTTP 402")
        self.assertTrue(skipped["force_allowed_only_for_explicit_diagnostic"])

    def test_latest_quote_returns_price_without_analysis_or_sqlite_access(self) -> None:
        self._prepare()
        self.price = Decimal("123.45")
        self.assertTrue(self.service.refresh_quotes(force=True)["ok"])
        result = self.service.latest_quote(ISIN)
        self.assertTrue(result["ok"])
        self.assertEqual(result["price"], "123.45")
        self.assertEqual(result["currency"], "EUR")
        self.assertEqual(result["provider"], "test-provider")
        self.assertEqual(result["provider_symbol"], "BAS.XETRA")
        self.assertFalse(result["critical"])

    def test_valuation_converts_usd_quotes_with_eodhd_fx_and_totals_in_eur(self) -> None:
        self.clock.value = datetime(2026, 8, 3, 10, 0, tzinfo=UTC)
        self.service.import_csv(self.csv.name, dry_run=False)
        self.service.watchlist_add(
            isin=ISIN, name="BASF SE", symbol="BAS", mic="XETR", currency="USD"
        )
        self.service.watchlist_add(
            isin="DE000A1EWWW0", name="ADIDAS AG", symbol="ADS", mic="XETR", currency="EUR"
        )

        def fetch(instrument: dict[str, str]) -> Quote:
            return Quote(
                symbol=instrument["symbol"],
                price=Decimal("60") if instrument["isin"] == ISIN else Decimal("200"),
                currency=instrument["currency"],
                observed_at=self.clock().isoformat(),
                provider="eodhd",
            )

        self.service._quote_fetcher = fetch
        self.service._fx_quote_fetcher = lambda base, quote: FxQuote(
            base_currency=base,
            quote_currency=quote,
            rate=Decimal("1.20"),
            observed_at=self.clock().isoformat(),
        )
        refreshed = self.service.refresh_quotes(force=True)
        self.assertTrue(refreshed["ok"])
        self.assertEqual(refreshed["fx_expected"], 1)
        self.assertEqual(refreshed["fx_received"], 1)

        result = self.service.valuation()
        self.assertTrue(result["ok"])
        basf = next(item for item in result["positions"] if item["isin"] == ISIN)
        self.assertEqual(basf["current_price"], "60")
        self.assertEqual(basf["quote_currency"], "USD")
        self.assertEqual(basf["current_price_converted"], "50.000000")
        self.assertEqual(basf["gain"], "61.25")
        self.assertEqual(basf["fx"]["provider_symbol"], "EURUSD.FOREX")
        self.assertEqual(basf["fx"]["conversion"], "1 USD = 0.83333333 EUR")
        self.assertEqual(result["totals"]["EUR"]["cost_basis"], "923.75")
        self.assertEqual(result["totals"]["EUR"]["current_value"], "1025.00")
        self.assertEqual(result["totals"]["EUR"]["gain"], "101.25")

    def test_valuation_fails_closed_without_required_fx_rate(self) -> None:
        self.clock.value = datetime(2026, 8, 3, 10, 0, tzinfo=UTC)
        self.service.import_csv(self.csv.name, dry_run=False)
        self.service.watchlist_add(
            isin=ISIN, name="BASF SE", symbol="BAS", mic="XETR", currency="USD"
        )
        self.service.watchlist_add(
            isin="DE000A1EWWW0", name="ADIDAS AG", symbol="ADS", mic="XETR", currency="EUR"
        )
        self.service._quote_fetcher = lambda instrument: Quote(
            symbol=instrument["symbol"],
            price=Decimal("100"),
            currency=instrument["currency"],
            observed_at=self.clock().isoformat(),
            provider="eodhd",
        )
        refreshed = self.service.refresh_quotes(force=True)
        self.assertFalse(refreshed["ok"])
        self.assertEqual(refreshed["fx_received"], 0)
        result = self.service.valuation()
        self.assertFalse(result["ok"])
        self.assertIsNone(result["totals"])
        self.assertTrue(any("Wechselkurs" in item["error"] for item in result["failures"]))

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


class PortfolioNextcloudCsvTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.inbox = root / "inbox"
        self.inbox.mkdir()
        self.portfolio = PortfolioService(
            PortfolioToolSettings(
                enabled=True,
                database=root / "portfolio.sqlite3",
                import_root=self.inbox,
                nextcloud_folder="Assistent/Finanzen/Portfolio",
                provider="eodhd",
            ),
            CleanAntivirus(),  # type: ignore[arg-type]
        )
        remote_path = "Assistent/Finanzen/Portfolio/depot-export-31.07.2026.csv"
        entry = RemoteFile(
            href="/remote/depot.csv",
            path=remote_path,
            name="depot-export-31.07.2026.csv",
            is_collection=False,
            content_type="text/csv",
            size=len(csv_fixture()),
            etag="etag-1",
            modified_at="Fri, 31 Jul 2026 10:00:00 GMT",
        )

        class FakeFiles:
            clean_path = staticmethod(NextcloudFiles.clean_path)

            def __init__(self):
                self.expected_etags = []

            def list_folder(self, path):
                return [entry]

            def download(self, path, *, expected_etag=""):
                self.expected_etags.append(expected_etag)
                return csv_fixture()

        class FakeStorage:
            def __init__(self):
                self.events = []

            def audit(self, event, detail, **kwargs):
                self.events.append((event, detail, kwargs))

        assistant = object.__new__(PersonalAssistant)
        assistant.portfolio = self.portfolio
        assistant.nextcloud_files = FakeFiles()
        assistant.policy = SimpleNamespace(
            decide=lambda *args, **kwargs: PolicyDecision(True, False, "allowed")
        )
        assistant.storage = FakeStorage()
        assistant.tool_settings = SimpleNamespace(
            portfolio=self.portfolio.settings,
            nextcloud=SimpleNamespace(
                workspace=SimpleNamespace(enabled=True, resource_id="nextcloud-files-main")
            ),
        )
        self.assistant = assistant
        self.remote_path = remote_path

    def tearDown(self) -> None:
        self.portfolio.close()
        self.temporary.cleanup()

    def test_nextcloud_csv_is_downloaded_scanned_and_previewed(self) -> None:
        result = self.assistant.portfolio_import_csv(
            nextcloud_path=self.remote_path,
            dry_run=True,
        )
        self.assertTrue(result["dry_run"])
        self.assertEqual(result["nextcloud_path"], self.remote_path)
        self.assertEqual(result["nextcloud_etag"], "etag-1")
        self.assertEqual(self.assistant.nextcloud_files.expected_etags, ["etag-1"])
        staged = list((self.inbox / ".nextcloud-staging").glob("*.csv"))
        self.assertEqual(staged, [])

    def test_nextcloud_filename_date_is_checked_before_productive_import(self) -> None:
        wrong = self.remote_path.replace("31.07.2026", "30.07.2026")
        self.assistant.nextcloud_files.list_folder = lambda path: [RemoteFile(
            href="/remote/depot.csv", path=wrong, name=Path(wrong).name,
            is_collection=False, content_type="text/csv", size=len(csv_fixture()),
            etag="etag-2", modified_at="",
        )]
        with self.assertRaisesRegex(ValueError, "Stichtag"):
            self.assistant.portfolio_import_csv(nextcloud_path=wrong, dry_run=False)
        self.assertEqual(self.portfolio.holdings()["count"], 0)

    def test_disabled_portfolio_does_not_access_nextcloud(self) -> None:
        self.portfolio.settings.enabled = False
        self.assistant.nextcloud_files.list_folder = lambda path: self.fail(
            "Deaktiviertes Werkzeug darf Nextcloud nicht lesen"
        )
        with self.assertRaisesRegex(PermissionError, "nicht aktiviert"):
            self.assistant.portfolio_import_csv(
                nextcloud_path=self.remote_path,
                dry_run=True,
            )


if __name__ == "__main__":
    unittest.main()
