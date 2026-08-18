from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shlex
import sqlite3
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from datetime import time as clock_time
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .antivirus import HostAntivirus
from .gateway_events import event_command
from .portfolio_import import parse_dkb_portfolio_csv, parse_portfolio_performance_xml
from .portfolio_research import (
    RESEARCH_STRATEGIES,
    EodhdResearchClient,
    ResearchProvider,
    ResearchProviderError,
    analyze_research_payload,
    research_models,
)
from .tool_settings import PortfolioToolSettings

SCHEMA_VERSION = 5
MAX_IMPORT_BYTES = 25_000_000
ISIN_RE = re.compile(r"^[A-Z]{2}[A-Z0-9]{9}[0-9]$")
MIC_RE = re.compile(r"^[A-Z0-9]{4}$")
MAPPING_SYMBOL_RE = re.compile(r"^[A-Z0-9][A-Z0-9.\-]{0,39}$")
EODHD_EXCHANGE_BY_MIC = {
    "XLON": "LSE",
    "XETR": "XETRA",
    "XNAS": "US",
    "XNGS": "US",
    "XNYS": "US",
}
EODHD_BATCH_LIMIT = 20
PORTFOLIO_REPORTING_CURRENCY = "EUR"
EODHD_MINOR_UNIT_SCALES: dict[tuple[str, str], Decimal] = {
    # EODHD labels London sterling instruments as GBP while returning the
    # exchange price in GBX (pence). Store major currency units consistently.
    ("XLON", "GBP"): Decimal("0.01"),
}


def _canonical_eodhd_symbol(value: object) -> str:
    """Return the EODHD code component without a display-only trailing dot."""
    symbol = str(value or "").strip().upper().rstrip(".")
    if not MAPPING_SYMBOL_RE.fullmatch(symbol):
        raise ValueError("Boersensymbol ist fuer EODHD ungueltig")
    return symbol


def _eodhd_price_scale(instrument: dict[str, str]) -> Decimal:
    mic = str(instrument.get("mic") or "").strip().upper()
    currency = str(instrument.get("currency") or "").strip().upper()
    return EODHD_MINOR_UNIT_SCALES.get((mic, currency), Decimal("1"))


def _scaled_eodhd_price(value: object, instrument: dict[str, str]) -> Decimal:
    return _decimal(value) * _eodhd_price_scale(instrument)


def _now() -> datetime:
    return datetime.now(UTC)


def _iso(value: datetime | None = None) -> str:
    return (value or _now()).astimezone(UTC).isoformat(timespec="seconds")


def _parse_time(value: object) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _decimal(value: object, *, scale: int = 0) -> Decimal:
    text = str(value or "").strip().replace(",", ".")
    if not text:
        return Decimal("0")
    try:
        number = Decimal(text)
    except InvalidOperation as exc:
        raise ValueError(f"Ungueltige Zahl im Portfolio-Import: {text[:80]}") from exc
    return number / (Decimal(10) ** scale) if scale else number


def _valid_isin(value: str) -> bool:
    if not ISIN_RE.fullmatch(value):
        return False
    digits = "".join(str(ord(character) - 55) if character.isalpha() else character for character in value)
    total = 0
    double = False
    for character in reversed(digits):
        number = int(character) * (2 if double else 1)
        total += number // 10 + number % 10
        double = not double
    return total % 10 == 0


@dataclass(frozen=True, slots=True)
class Quote:
    symbol: str
    price: Decimal
    currency: str
    observed_at: str
    provider: str = "eodhd"
    open: Decimal | None = None
    high: Decimal | None = None
    low: Decimal | None = None
    volume: Decimal | None = None
    market_open: bool | None = None


@dataclass(frozen=True, slots=True)
class FxQuote:
    base_currency: str
    quote_currency: str
    rate: Decimal
    observed_at: str
    provider: str = "eodhd"


QuoteFetcher = Callable[[dict[str, str]], Quote]
FxQuoteFetcher = Callable[[str, str], FxQuote]
EventNotifier = Callable[[str], dict[str, Any]]
MappingSearcher = Callable[[str], list[dict[str, Any]]]
MappingSelector = Callable[[dict[str, Any]], dict[str, Any]]


SUPPORTED_MICS_BY_EODHD_EXCHANGE: dict[str, tuple[str, ...]] = {
    "LSE": ("XLON",),
    "XETRA": ("XETR",),
    "US": ("XNAS", "XNYS"),
    "NASDAQ": ("XNAS",),
    "NYSE": ("XNYS",),
}
US_PRIMARY_VENUE_FILTERS: tuple[str, ...] = ("NASDAQ", "NYSE")


def _notify_openclaw(text: str) -> dict[str, Any]:
    try:
        completed = subprocess.run(
            _system_event_command(text),
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        return {
            "attempted": True,
            "ok": completed.returncode == 0,
            "returncode": completed.returncode,
            "detail": (completed.stderr.strip() or completed.stdout.strip())[-1000:],
        }
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"attempted": True, "ok": False, "detail": str(exc)}


class EodhdClient:
    endpoint = "https://eodhd.com/api/real-time"
    search_endpoint = "https://eodhd.com/api/search"

    def __init__(self, api_key: str, *, timeout: int = 20) -> None:
        self.api_key = api_key
        self.timeout = timeout

    @staticmethod
    def ticker(instrument: dict[str, str]) -> str:
        symbol = _canonical_eodhd_symbol(instrument.get("symbol"))
        mic = str(instrument.get("mic") or "").strip().upper()
        exchange = EODHD_EXCHANGE_BY_MIC.get(mic)
        if not exchange:
            raise ValueError(f"EODHD-Boersencode fuer MIC {mic or '<leer>'} ist nicht registriert")
        return f"{symbol}.{exchange}"

    def _provider_error(self, raw: bytes, fallback: str) -> RuntimeError:
        detail = ""
        try:
            payload = json.loads(raw.decode("utf-8"))
            if isinstance(payload, dict):
                detail = str(payload.get("message") or payload.get("error") or "")
        except (UnicodeDecodeError, json.JSONDecodeError):
            detail = ""
        safe = (detail or fallback).replace(self.api_key, "<redacted>")[:300]
        return RuntimeError(f"EODHD-Marktdatenfehler: {safe}")

    @staticmethod
    def fx_ticker(base_currency: str, quote_currency: str) -> str:
        base = base_currency.strip().upper()
        quote = quote_currency.strip().upper()
        if len(base) != 3 or len(quote) != 3 or not base.isalpha() or not quote.isalpha() or base == quote:
            raise ValueError("EODHD-FX-Paar benoetigt zwei verschiedene ISO-Waehrungen")
        return f"{base}{quote}.FOREX"

    def fetch_market_data(
        self,
        instruments: list[dict[str, str]],
        fx_pairs: list[tuple[str, str]] | None = None,
    ) -> tuple[dict[str, Quote], dict[tuple[str, str], FxQuote]]:
        pairs = [(str(base).upper(), str(quote).upper()) for base, quote in (fx_pairs or [])]
        if not instruments and not pairs:
            return {}, {}
        if len(instruments) + len(pairs) > EODHD_BATCH_LIMIT:
            raise ValueError(f"EODHD-Batch darf hoechstens {EODHD_BATCH_LIMIT} Symbole enthalten")
        ticker_to_item: dict[str, dict[str, str]] = {}
        for item in instruments:
            ticker = self.ticker(item)
            if ticker in ticker_to_item:
                raise ValueError(f"Doppelte EODHD-Zuordnung im Batch: {ticker}")
            ticker_to_item[ticker] = item
        ticker_to_pair: dict[str, tuple[str, str]] = {}
        for pair in pairs:
            ticker = self.fx_ticker(*pair)
            if ticker in ticker_to_pair:
                raise ValueError(f"Doppeltes EODHD-FX-Paar im Batch: {ticker}")
            ticker_to_pair[ticker] = pair
        tickers = [*ticker_to_item, *ticker_to_pair]
        params = {"fmt": "json", "api_token": self.api_key}
        if len(tickers) > 1:
            params["s"] = ",".join(tickers[1:])
        url = f"{self.endpoint}/{urllib.parse.quote(tickers[0], safe='')}?" + urllib.parse.urlencode(params)
        request = urllib.request.Request(url, headers={"User-Agent": "OpenClaw-Portfolio/1"})
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw = response.read(2_000_000)
        except urllib.error.HTTPError as exc:
            try:
                raw = exc.read(64_000)
            except OSError:
                raw = b""
            # HTTPError carries the full request URL, including EODHD's required
            # query token. Suppress exception chaining so tracebacks cannot leak it.
            raise self._provider_error(raw, f"HTTP {exc.code}") from None
        except urllib.error.URLError as exc:
            reason = str(getattr(exc, "reason", "Verbindung fehlgeschlagen"))
            raise self._provider_error(b"", reason) from None
        except (TimeoutError, OSError) as exc:
            raise self._provider_error(b"", type(exc).__name__) from None
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RuntimeError("EODHD lieferte kein gueltiges JSON") from exc
        if isinstance(payload, dict) and (
            isinstance(payload.get("code"), int) or str(payload.get("status") or "").casefold() == "error"
        ):
            raise self._provider_error(raw, "Anbieterfehler")
        rows = payload if isinstance(payload, list) else [payload]
        quotes: dict[str, Quote] = {}
        fx_quotes: dict[tuple[str, str], FxQuote] = {}
        for row in rows:
            if not isinstance(row, dict):
                continue
            code = str(row.get("code") or "").strip().upper()
            item = ticker_to_item.get(code)
            pair = ticker_to_pair.get(code)
            if item is None and pair is None:
                continue
            price_text = row.get("close")
            timestamp = row.get("timestamp")
            if price_text in {None, ""} or not str(timestamp or "").isdigit():
                continue
            observed = _iso(datetime.fromtimestamp(int(timestamp), UTC))
            if item is not None:
                quotes[str(item["isin"])] = Quote(
                    symbol=str(item["symbol"]),
                    price=_scaled_eodhd_price(price_text, item),
                    currency=str(item.get("currency") or "").upper(),
                    observed_at=observed,
                    provider="eodhd",
                    open=(
                        _scaled_eodhd_price(row["open"], item) if row.get("open") not in {None, ""} else None
                    ),
                    high=(
                        _scaled_eodhd_price(row["high"], item) if row.get("high") not in {None, ""} else None
                    ),
                    low=(_scaled_eodhd_price(row["low"], item) if row.get("low") not in {None, ""} else None),
                    volume=_decimal(row["volume"]) if row.get("volume") not in {None, ""} else None,
                )
            elif pair is not None:
                fx_quotes[pair] = FxQuote(
                    base_currency=pair[0],
                    quote_currency=pair[1],
                    rate=_decimal(price_text),
                    observed_at=observed,
                    provider="eodhd",
                )
        return quotes, fx_quotes

    def fetch_many(self, instruments: list[dict[str, str]]) -> dict[str, Quote]:
        quotes, _ = self.fetch_market_data(instruments)
        return quotes

    def fetch(self, instrument: dict[str, str]) -> Quote:
        quote = self.fetch_many([instrument]).get(str(instrument.get("isin") or ""))
        if quote is None:
            raise RuntimeError(f"EODHD lieferte keinen Kurs fuer {self.ticker(instrument)}")
        return quote

    def _search_rows(
        self,
        query: str,
        *,
        limit: int,
        exchange: str | None = None,
    ) -> list[dict[str, Any]]:
        params = {
            "api_token": self.api_key,
            "fmt": "json",
            "limit": str(max(1, min(int(limit), 100))),
            "type": "stock",
        }
        if exchange:
            params["exchange"] = exchange
        url = f"{self.search_endpoint}/{urllib.parse.quote(query, safe='')}?" + urllib.parse.urlencode(params)
        request = urllib.request.Request(url, headers={"User-Agent": "OpenClaw-Portfolio/1"})
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw = response.read(2_000_000)
        except urllib.error.HTTPError as exc:
            try:
                raw = exc.read(64_000)
            except OSError:
                raw = b""
            raise self._provider_error(raw, f"HTTP {exc.code}") from None
        except urllib.error.URLError as exc:
            reason = str(getattr(exc, "reason", "Verbindung fehlgeschlagen"))
            raise self._provider_error(b"", reason) from None
        except (TimeoutError, OSError) as exc:
            raise self._provider_error(b"", type(exc).__name__) from None
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RuntimeError("EODHD-Suche lieferte kein gueltiges JSON") from exc
        if isinstance(payload, dict) and (
            isinstance(payload.get("code"), int) or str(payload.get("status") or "").casefold() == "error"
        ):
            raise self._provider_error(raw, "Anbieterfehler")
        if not isinstance(payload, list):
            raise RuntimeError("EODHD-Suche lieferte keine Kandidatenliste")
        return [row for row in payload if isinstance(row, dict)]

    @staticmethod
    def _exact_search_identity(
        row: dict[str, Any],
        normalized_isin: str,
    ) -> tuple[str, str] | None:
        row_isin = str(row.get("ISIN") or row.get("isin") or "").strip().upper()
        try:
            symbol = _canonical_eodhd_symbol(row.get("Code") or row.get("code"))
        except ValueError:
            return None
        currency = str(row.get("Currency") or row.get("currency") or "").strip().upper()
        if row_isin != normalized_isin or len(currency) != 3 or not currency.isalpha():
            return None
        return symbol, currency

    def _verified_us_primary_venues(
        self,
        normalized_isin: str,
        candidates: list[dict[str, Any]],
        *,
        limit: int,
    ) -> dict[tuple[str, str], str]:
        unresolved = {
            (str(item["symbol"]), str(item["currency"]))
            for item in candidates
            if item["exchange"] == "US" and item["is_primary"]
        }
        verified: dict[tuple[str, str], str] = {}
        for venue in US_PRIMARY_VENUE_FILTERS:
            if not unresolved:
                break
            rows = self._search_rows(
                normalized_isin,
                limit=limit,
                exchange=venue,
            )
            for row in rows:
                identity = self._exact_search_identity(row, normalized_isin)
                if identity not in unresolved:
                    continue
                verified[identity] = venue
                unresolved.remove(identity)
        return verified

    def search_by_isin(self, isin: str, *, limit: int = 25) -> list[dict[str, Any]]:
        """Return active EODHD candidates with a provider-verified US venue."""
        normalized = isin.strip().upper()
        if not _valid_isin(normalized):
            raise ValueError("ISIN ist ungueltig")
        payload = self._search_rows(normalized, limit=limit)

        candidates: list[dict[str, Any]] = []
        for row in payload:
            identity = self._exact_search_identity(row, normalized)
            if identity is None:
                continue
            symbol, currency = identity
            exchange = str(row.get("Exchange") or row.get("exchange") or "").strip().upper()
            name = str(row.get("Name") or row.get("name") or "").strip()
            allowed_mics = SUPPORTED_MICS_BY_EODHD_EXCHANGE.get(exchange, ())
            if not allowed_mics:
                continue
            candidates.append(
                {
                    "isin": normalized,
                    "name": name or normalized,
                    "symbol": symbol,
                    "exchange": exchange,
                    "currency": currency,
                    "is_primary": bool(row.get("isPrimary") or row.get("is_primary")),
                    "allowed_mics": list(allowed_mics),
                    "venue_source": "eodhd-search",
                }
            )

        verified_venues = self._verified_us_primary_venues(
            normalized,
            candidates,
            limit=limit,
        )
        for candidate in candidates:
            identity = (str(candidate["symbol"]), str(candidate["currency"]))
            venue = verified_venues.get(identity)
            if candidate["exchange"] != "US" or venue is None:
                continue
            candidate["exchange"] = venue
            candidate["allowed_mics"] = list(SUPPORTED_MICS_BY_EODHD_EXCHANGE[venue])
            candidate["venue_source"] = "eodhd-search-exchange-filter"
            candidate["venue_filter"] = venue
        return candidates

    def search_by_query(self, query: str, *, limit: int = 25) -> list[dict[str, Any]]:
        """Return provider-supplied stock identities for a bounded name/ticker query."""
        if any(ord(character) < 32 for character in query):
            raise ValueError("Wertpapiersuche enthaelt ungueltige Steuerzeichen")
        normalized_query = " ".join(query.split())
        if len(normalized_query) < 2 or len(normalized_query) > 120:
            raise ValueError("Wertpapiersuche benoetigt 2 bis 120 Zeichen")
        payload = self._search_rows(normalized_query, limit=limit)
        candidates: list[dict[str, Any]] = []
        seen: set[tuple[str, str, str, str]] = set()
        for row in payload:
            isin = str(row.get("ISIN") or row.get("isin") or "").strip().upper()
            try:
                symbol = _canonical_eodhd_symbol(row.get("Code") or row.get("code"))
            except ValueError:
                continue
            exchange = str(row.get("Exchange") or row.get("exchange") or "").strip().upper()
            currency = str(row.get("Currency") or row.get("currency") or "").strip().upper()
            if (
                not _valid_isin(isin)
                or not re.fullmatch(r"[A-Z0-9]{1,20}", exchange)
                or len(currency) != 3
                or not currency.isalpha()
            ):
                continue
            identity = (isin, symbol, exchange, currency)
            if identity in seen:
                continue
            seen.add(identity)
            candidates.append(
                {
                    "isin": isin,
                    "name": str(row.get("Name") or row.get("name") or isin).strip()[:200],
                    "symbol": symbol,
                    "exchange": exchange,
                    "currency": currency,
                    "is_primary": bool(row.get("isPrimary") or row.get("is_primary")),
                }
            )
        return candidates


class PortfolioStore:
    def __init__(self, path: Path) -> None:
        self.path = path.expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA busy_timeout=5000")
        self.connection.execute("PRAGMA foreign_keys=ON")
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS schema_meta (
                key TEXT PRIMARY KEY, value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS imports (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sha256 TEXT NOT NULL UNIQUE,
                source_type TEXT NOT NULL,
                source_name TEXT NOT NULL,
                imported_at TEXT NOT NULL,
                as_of TEXT NOT NULL,
                instruments INTEGER NOT NULL,
                positions INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS instruments (
                isin TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                wkn TEXT NOT NULL DEFAULT '',
                symbol TEXT NOT NULL DEFAULT '',
                mic TEXT NOT NULL DEFAULT '',
                currency TEXT NOT NULL DEFAULT '',
                mapping_confirmed INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS position_snapshots (
                import_id INTEGER NOT NULL REFERENCES imports(id),
                account TEXT NOT NULL,
                isin TEXT NOT NULL REFERENCES instruments(isin),
                shares TEXT NOT NULL,
                as_of TEXT NOT NULL,
                entry_price TEXT NOT NULL DEFAULT '',
                valuation_price TEXT NOT NULL DEFAULT '',
                absolute_gain TEXT NOT NULL DEFAULT '',
                relative_gain_percent TEXT NOT NULL DEFAULT '',
                asset_class TEXT NOT NULL DEFAULT '',
                snapshot_currency TEXT NOT NULL DEFAULT '',
                PRIMARY KEY(import_id, account, isin)
            );
            CREATE TABLE IF NOT EXISTS watchlist (
                isin TEXT PRIMARY KEY REFERENCES instruments(isin),
                enabled INTEGER NOT NULL,
                added_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS quotes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                isin TEXT NOT NULL REFERENCES instruments(isin),
                provider TEXT NOT NULL,
                price TEXT NOT NULL,
                currency TEXT NOT NULL,
                observed_at TEXT NOT NULL,
                received_at TEXT NOT NULL,
                delay_seconds INTEGER,
                open TEXT,
                high TEXT,
                low TEXT,
                volume TEXT,
                market_open INTEGER,
                UNIQUE(isin, provider, observed_at)
            );
            CREATE INDEX IF NOT EXISTS idx_quotes_isin_time
                ON quotes(isin, observed_at DESC, id DESC);
            CREATE TABLE IF NOT EXISTS fx_quotes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                base_currency TEXT NOT NULL,
                quote_currency TEXT NOT NULL,
                provider TEXT NOT NULL,
                rate TEXT NOT NULL,
                observed_at TEXT NOT NULL,
                received_at TEXT NOT NULL,
                delay_seconds INTEGER,
                UNIQUE(base_currency, quote_currency, provider, observed_at)
            );
            CREATE INDEX IF NOT EXISTS idx_fx_quotes_pair_time
                ON fx_quotes(base_currency, quote_currency, observed_at DESC, id DESC);
            CREATE TABLE IF NOT EXISTS quote_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                started_at TEXT NOT NULL,
                finished_at TEXT NOT NULL,
                status TEXT NOT NULL,
                expected INTEGER NOT NULL,
                received INTEGER NOT NULL,
                held_missing INTEGER NOT NULL,
                latency_ms REAL NOT NULL,
                error TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS alert_rules (
                id TEXT PRIMARY KEY,
                isin TEXT NOT NULL REFERENCES instruments(isin),
                direction TEXT NOT NULL CHECK(direction IN ('above','below')),
                threshold TEXT NOT NULL,
                currency TEXT NOT NULL,
                hysteresis_bps INTEGER NOT NULL DEFAULT 25,
                cooldown_minutes INTEGER NOT NULL DEFAULT 60,
                enabled INTEGER NOT NULL,
                last_state TEXT NOT NULL DEFAULT 'unknown',
                last_triggered_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS alert_events (
                id TEXT PRIMARY KEY,
                rule_id TEXT NOT NULL REFERENCES alert_rules(id),
                isin TEXT NOT NULL,
                state TEXT NOT NULL,
                price TEXT NOT NULL,
                observed_at TEXT NOT NULL,
                created_at TEXT NOT NULL,
                acknowledged INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS research_runs (
                id TEXT PRIMARY KEY,
                created_at TEXT NOT NULL,
                kind TEXT NOT NULL CHECK(kind IN ('screen','analysis')),
                strategy TEXT NOT NULL,
                request_json TEXT NOT NULL,
                provider TEXT NOT NULL,
                model_version TEXT NOT NULL,
                status TEXT NOT NULL,
                candidate_count INTEGER NOT NULL,
                error TEXT NOT NULL DEFAULT ''
            );
            CREATE INDEX IF NOT EXISTS idx_research_runs_created
                ON research_runs(created_at DESC);
            CREATE TABLE IF NOT EXISTS research_candidates (
                id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL REFERENCES research_runs(id),
                rank INTEGER NOT NULL,
                isin TEXT NOT NULL,
                ticker TEXT NOT NULL,
                name TEXT NOT NULL,
                sector TEXT NOT NULL,
                industry TEXT NOT NULL,
                strategy TEXT NOT NULL,
                score TEXT,
                metric_coverage TEXT NOT NULL,
                verdict TEXT NOT NULL,
                eligible INTEGER NOT NULL,
                analysis_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_research_candidates_isin
                ON research_candidates(isin, created_at DESC);
            CREATE TABLE IF NOT EXISTS investment_profiles (
                version INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                risk_tolerance TEXT NOT NULL,
                horizon_years INTEGER NOT NULL,
                strategy TEXT NOT NULL,
                max_position_pct TEXT NOT NULL,
                max_sector_pct TEXT NOT NULL,
                preferred_sectors_json TEXT NOT NULL,
                excluded_sectors_json TEXT NOT NULL,
                notes TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS investment_feedback (
                id TEXT PRIMARY KEY,
                candidate_id TEXT NOT NULL REFERENCES research_candidates(id),
                decision TEXT NOT NULL CHECK(
                    decision IN ('interested','rejected','watch','bought','sold')
                ),
                reason TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_investment_feedback_created
                ON investment_feedback(created_at DESC);
            """
        )
        quote_columns = {
            str(row[1]) for row in self.connection.execute("PRAGMA table_info(quotes)").fetchall()
        }
        for column in ("open", "high", "low", "volume", "market_open"):
            if column not in quote_columns:
                self.connection.execute(f"ALTER TABLE quotes ADD COLUMN {column} TEXT")
        position_columns = {
            str(row[1]) for row in self.connection.execute("PRAGMA table_info(position_snapshots)").fetchall()
        }
        for column in (
            "entry_price",
            "valuation_price",
            "absolute_gain",
            "relative_gain_percent",
            "asset_class",
            "snapshot_currency",
        ):
            if column not in position_columns:
                self.connection.execute(
                    f"ALTER TABLE position_snapshots ADD COLUMN {column} TEXT NOT NULL DEFAULT ''"
                )
        self.connection.execute(
            "INSERT OR REPLACE INTO schema_meta(key,value) VALUES('schema_version',?)",
            (str(SCHEMA_VERSION),),
        )
        self.connection.commit()

    def close(self) -> None:
        self.connection.close()

    def integrity(self) -> str:
        row = self.connection.execute("PRAGMA integrity_check").fetchone()
        return str(row[0] if row else "unknown")


class PortfolioService:
    def __init__(
        self,
        settings: PortfolioToolSettings,
        antivirus: HostAntivirus,
        *,
        quote_fetcher: QuoteFetcher | None = None,
        fx_quote_fetcher: FxQuoteFetcher | None = None,
        notifier: EventNotifier | None = None,
        mapping_searcher: MappingSearcher | None = None,
        mapping_query_searcher: MappingSearcher | None = None,
        mapping_selector: MappingSelector | None = None,
        research_provider: ResearchProvider | None = None,
        now: Callable[[], datetime] = _now,
    ) -> None:
        self.settings = settings
        self.antivirus = antivirus
        self.store = PortfolioStore(settings.database)
        self._quote_fetcher = quote_fetcher
        self._fx_quote_fetcher = fx_quote_fetcher
        self._notifier = notifier or _notify_openclaw
        self._mapping_searcher = mapping_searcher
        self._mapping_query_searcher = mapping_query_searcher
        self._mapping_selector = mapping_selector
        self._research_provider = research_provider
        self._now = now

    def close(self) -> None:
        self.store.close()

    def _require_enabled(self) -> None:
        if not self.settings.enabled:
            raise PermissionError("Portfolio-Werkzeug ist in tools.toml nicht aktiviert")

    def _input_path(self, value: str | Path) -> Path:
        path = Path(value).expanduser()
        if not path.is_absolute():
            path = self.settings.import_root / path
        path = path.resolve()
        try:
            path.relative_to(self.settings.import_root.resolve())
        except ValueError as exc:
            raise PermissionError("Portfolio-Import ist nur aus portfolio.import_root erlaubt") from exc
        if not path.is_file():
            raise FileNotFoundError(path)
        return path

    def _import_snapshot(
        self,
        value: str | Path,
        *,
        parser: Callable[[bytes], dict[str, Any]],
        dry_run: bool,
        source_name: str = "",
        expected_as_of: str = "",
    ) -> dict[str, Any]:
        self._require_enabled()
        path = self._input_path(value)
        scan = self.antivirus.scan_path(path, source_type="portfolio-import")
        if not scan.clean:
            raise PermissionError(f"Portfolio-Import fail-closed gesperrt: ClamAV-Status {scan.status}")
        data = path.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        parsed = parser(data)
        if expected_as_of and str(parsed.get("as_of") or "") != expected_as_of:
            raise ValueError("Erwarteter Portfolio-Stichtag stimmt nicht mit dem Dateiinhalt ueberein")
        display_name = str(source_name or path.name).strip()
        if not display_name or len(display_name) > 255:
            raise ValueError("Portfolio-Quelldateiname fehlt oder ist zu lang")
        result = {
            "ok": True,
            "dry_run": dry_run,
            "sha256": digest,
            "source": display_name,
            "source_type": parsed["source_type"],
            "as_of": parsed["as_of"],
            "instruments": len(parsed["instruments"]),
            "positions": len(parsed["positions"]),
            "antivirus": scan.to_dict(),
        }
        if dry_run:
            result["preview"] = {
                "instruments": parsed["instruments"][:20],
                "positions": parsed["positions"][:20],
            }
            return result
        existing = self.store.connection.execute(
            "SELECT id FROM imports WHERE sha256=?", (digest,)
        ).fetchone()
        if existing:
            backfilled = 0
            currency_backfilled = 0
            if parsed["source_type"] == "dkb-depot-csv":
                with self.store.connection:
                    for item in parsed["positions"]:
                        cursor = self.store.connection.execute(
                            """
                            UPDATE position_snapshots
                            SET entry_price=?,valuation_price=?,absolute_gain=?,
                                relative_gain_percent=?,asset_class=?
                            WHERE import_id=? AND account=? AND isin=?
                              AND entry_price='' AND valuation_price=''
                            """,
                            (
                                item.get("entry_price", ""),
                                item.get("valuation_price", ""),
                                item.get("absolute_gain", ""),
                                item.get("relative_gain_percent", ""),
                                item.get("asset_class", ""),
                                int(existing["id"]),
                                item["account"],
                                item["isin"],
                            ),
                        )
                        backfilled += max(0, int(cursor.rowcount))
                        currency_cursor = self.store.connection.execute(
                            """
                            UPDATE position_snapshots SET snapshot_currency=?
                            WHERE import_id=? AND account=? AND isin=?
                              AND snapshot_currency=''
                            """,
                            (
                                item.get("snapshot_currency", ""),
                                int(existing["id"]),
                                item["account"],
                                item["isin"],
                            ),
                        )
                        currency_backfilled += max(0, int(currency_cursor.rowcount))
            return {
                **result,
                "duplicate": True,
                "import_id": int(existing["id"]),
                "snapshot_metrics_backfilled": backfilled,
                "snapshot_currency_backfilled": currency_backfilled,
            }
        now = _iso(self._now())
        with self.store.connection:
            for item in parsed["instruments"]:
                self.store.connection.execute(
                    """
                    INSERT INTO instruments(isin,name,wkn,symbol,mic,currency,mapping_confirmed,updated_at)
                    VALUES(?,?,?,?,?,?,0,?)
                    ON CONFLICT(isin) DO UPDATE SET
                        name=excluded.name,
                        wkn=CASE WHEN excluded.wkn!='' THEN excluded.wkn ELSE instruments.wkn END,
                        currency=CASE
                            WHEN instruments.mapping_confirmed=1 THEN instruments.currency
                            WHEN excluded.currency!='' THEN excluded.currency
                            ELSE instruments.currency
                        END,
                        updated_at=excluded.updated_at
                    """,
                    (
                        item["isin"],
                        item["name"],
                        item["wkn"],
                        item["symbol"],
                        item["mic"],
                        item["currency"],
                        now,
                    ),
                )
            cursor = self.store.connection.execute(
                """
                INSERT INTO imports(
                    sha256,source_type,source_name,imported_at,as_of,instruments,positions
                ) VALUES(?,?,?,?,?,?,?)
                """,
                (
                    digest,
                    parsed["source_type"],
                    display_name,
                    now,
                    parsed["as_of"],
                    len(parsed["instruments"]),
                    len(parsed["positions"]),
                ),
            )
            import_id = int(cursor.lastrowid)
            for item in parsed["positions"]:
                self.store.connection.execute(
                    """
                    INSERT INTO position_snapshots(
                        import_id,account,isin,shares,as_of,entry_price,
                        valuation_price,absolute_gain,relative_gain_percent,asset_class,
                        snapshot_currency
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        import_id,
                        item["account"],
                        item["isin"],
                        item["shares"],
                        parsed["as_of"],
                        item.get("entry_price", ""),
                        item.get("valuation_price", ""),
                        item.get("absolute_gain", ""),
                        item.get("relative_gain_percent", ""),
                        item.get("asset_class", ""),
                        item.get("snapshot_currency", ""),
                    ),
                )
        return {**result, "duplicate": False, "import_id": import_id}

    def import_pp(self, value: str | Path, *, dry_run: bool = True) -> dict[str, Any]:
        return self._import_snapshot(
            value,
            parser=parse_portfolio_performance_xml,
            dry_run=dry_run,
        )

    def import_csv(
        self,
        value: str | Path,
        *,
        dry_run: bool = True,
        source_name: str = "",
        expected_as_of: str = "",
    ) -> dict[str, Any]:
        return self._import_snapshot(
            value,
            parser=parse_dkb_portfolio_csv,
            dry_run=dry_run,
            source_name=source_name,
            expected_as_of=expected_as_of,
        )

    def holdings(self) -> dict[str, Any]:
        latest = self.store.connection.execute(
            "SELECT id,as_of,source_name,imported_at FROM imports ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if not latest:
            return {"ok": True, "as_of": None, "positions": [], "count": 0}
        rows = self.store.connection.execute(
            """
            SELECT p.account,p.isin,p.shares,p.entry_price,p.valuation_price,
                   p.absolute_gain,p.relative_gain_percent,p.asset_class,
                   COALESCE(NULLIF(p.snapshot_currency,''),i.currency) AS currency,
                   CASE
                       WHEN i.mapping_confirmed=1 AND i.symbol!='' AND i.mic!=''
                       THEN i.currency ELSE ''
                   END AS quote_currency,
                   i.name,i.wkn,i.symbol,i.mic,
                   CASE
                       WHEN i.mapping_confirmed=1 AND i.symbol!='' AND i.mic!=''
                       THEN 1 ELSE 0
                   END AS mapping_confirmed
            FROM position_snapshots p JOIN instruments i ON i.isin=p.isin
            WHERE p.import_id=? ORDER BY i.name,p.account
            """,
            (latest["id"],),
        ).fetchall()
        return {
            "ok": True,
            "as_of": latest["as_of"],
            "source": latest["source_name"],
            "imported_at": latest["imported_at"],
            "count": len(rows),
            "positions": [dict(row) for row in rows],
        }

    def watchlist(self) -> dict[str, Any]:
        rows = self.store.connection.execute(
            """
            SELECT w.isin,w.enabled,w.added_at,w.updated_at,i.name,i.symbol,i.mic,
                   i.currency,i.mapping_confirmed
            FROM watchlist w JOIN instruments i ON i.isin=w.isin
            WHERE w.enabled=1 ORDER BY i.name
            """
        ).fetchall()
        return {"ok": True, "count": len(rows), "items": [dict(row) for row in rows]}

    def mapping_suggest(self, isin: str = "", *, query: str = "") -> dict[str, Any]:
        """Build a read-only, provider-bounded Ollama mapping proposal."""
        self._require_enabled()
        normalized = isin.strip().upper()
        if any(ord(character) < 32 for character in query):
            raise ValueError("Wertpapiersuche enthaelt ungueltige Steuerzeichen")
        normalized_query = " ".join(query.split())
        if bool(normalized) == bool(normalized_query):
            raise ValueError("Genau eine ISIN oder Suchanfrage ist erforderlich")
        discovery: dict[str, Any] | None = None
        api_key = os.environ.get(self.settings.api_key_env, "").strip()
        if normalized_query:
            if len(normalized_query) < 2 or len(normalized_query) > 120:
                raise ValueError("Wertpapiersuche benoetigt 2 bis 120 Zeichen")
            if not api_key:
                raise RuntimeError(f"API-Schluessel fehlt in Umgebungsvariable {self.settings.api_key_env}")
            query_searcher = self._mapping_query_searcher
            if query_searcher is None:
                query_searcher = EodhdClient(
                    api_key,
                    timeout=self.settings.request_timeout_seconds,
                ).search_by_query
            raw_discovery = query_searcher(normalized_query)
            discovery_candidates: list[dict[str, Any]] = []
            for item in raw_discovery:
                candidate_isin = str(item.get("isin") or "").strip().upper()
                symbol = str(item.get("symbol") or "").strip().upper()
                exchange = str(item.get("exchange") or "").strip().upper()
                currency = str(item.get("currency") or "").strip().upper()
                if (
                    not _valid_isin(candidate_isin)
                    or not MAPPING_SYMBOL_RE.fullmatch(symbol)
                    or not re.fullmatch(r"[A-Z0-9]{1,20}", exchange)
                    or len(currency) != 3
                    or not currency.isalpha()
                ):
                    continue
                discovery_candidates.append(
                    {
                        "isin": candidate_isin,
                        "name": str(item.get("name") or candidate_isin).strip()[:200],
                        "symbol": symbol,
                        "exchange": exchange,
                        "currency": currency,
                        "is_primary": bool(item.get("is_primary")),
                    }
                )
            if not discovery_candidates:
                return {
                    "ok": False,
                    "status": "no-provider-candidate",
                    "read_only": True,
                    "stored": False,
                    "query": normalized_query,
                    "error": "EODHD lieferte keine gueltige Wertpapieridentitaet",
                }
            distinct_isins = sorted({item["isin"] for item in discovery_candidates})
            primary_isins = sorted({item["isin"] for item in discovery_candidates if item["is_primary"]})
            if len(primary_isins) == 1:
                normalized = primary_isins[0]
                discovery_policy = "unique-provider-primary-isin"
            elif len(distinct_isins) == 1:
                normalized = distinct_isins[0]
                discovery_policy = "unique-provider-isin"
            else:
                return {
                    "ok": False,
                    "status": "ambiguous-query",
                    "read_only": True,
                    "stored": False,
                    "query": normalized_query,
                    "provider_candidates": len(discovery_candidates),
                    "candidates": discovery_candidates[:20],
                    "error": "EODHD lieferte mehrere nicht eindeutig aufloesbare ISINs",
                }
            selected_discovery = next(
                item
                for item in discovery_candidates
                if item["isin"] == normalized and (item["is_primary"] or len(primary_isins) != 1)
            )
            discovery = {
                "query": normalized_query,
                "provider_candidates": len(discovery_candidates),
                "selection_policy": discovery_policy,
                "selected_isin": normalized,
                "name": selected_discovery["name"],
            }
        elif not _valid_isin(normalized):
            raise ValueError("ISIN ist ungueltig")
        row = self.store.connection.execute(
            """
            SELECT isin,name,wkn,symbol,mic,currency,mapping_confirmed
            FROM instruments WHERE isin=?
            """,
            (normalized,),
        ).fetchone()
        current = (
            dict(row)
            if row is not None
            else {
                "isin": normalized,
                "name": str((discovery or {}).get("name") or normalized),
                "wkn": "",
                "symbol": "",
                "mic": "",
                "currency": "",
                "mapping_confirmed": 0,
            }
        )
        if current["mapping_confirmed"] and current["symbol"] and current["mic"]:
            result = {
                "ok": True,
                "status": "already-confirmed",
                "read_only": True,
                "stored": False,
                "candidate": {
                    "isin": normalized,
                    "name": current["name"],
                    "symbol": current["symbol"],
                    "mic": current["mic"],
                    "currency": current["currency"],
                    "provider_symbol": EodhdClient.ticker(
                        {"symbol": str(current["symbol"]), "mic": str(current["mic"])}
                    ),
                },
            }
            if discovery is not None:
                result["discovery"] = discovery
            return result

        if not api_key:
            raise RuntimeError(f"API-Schluessel fehlt in Umgebungsvariable {self.settings.api_key_env}")
        searcher = self._mapping_searcher
        if searcher is None:
            searcher = EodhdClient(
                api_key,
                timeout=self.settings.request_timeout_seconds,
            ).search_by_isin
        provider_candidates = searcher(normalized)
        bounded_candidates: list[dict[str, Any]] = []
        for candidate_id, item in enumerate(provider_candidates, 1):
            if str(item.get("isin") or "").strip().upper() != normalized:
                continue
            symbol = str(item.get("symbol") or "").strip().upper()
            exchange = str(item.get("exchange") or "").strip().upper()
            currency = str(item.get("currency") or "").strip().upper()
            allowed_mics = tuple(
                mic
                for mic in (str(value).strip().upper() for value in item.get("allowed_mics") or ())
                if MIC_RE.fullmatch(mic) and mic in EODHD_EXCHANGE_BY_MIC
            )
            if (
                not MAPPING_SYMBOL_RE.fullmatch(symbol)
                or len(currency) != 3
                or not currency.isalpha()
                or not allowed_mics
            ):
                continue
            bounded_candidates.append(
                {
                    "candidate_id": candidate_id,
                    "isin": normalized,
                    "name": str(item.get("name") or current["name"]).strip()[:200],
                    "symbol": symbol,
                    "exchange": exchange,
                    "currency": currency,
                    "is_primary": bool(item.get("is_primary")),
                    "allowed_mics": list(dict.fromkeys(allowed_mics)),
                    "venue_source": str(item.get("venue_source") or "eodhd-search")[:80],
                    "venue_filter": str(item.get("venue_filter") or "")[:20],
                }
            )
        if not bounded_candidates:
            return {
                "ok": False,
                "status": "no-provider-candidate",
                "read_only": True,
                "stored": False,
                "isin": normalized,
                "error": "EODHD lieferte keinen unterstuetzten exakten ISIN-Kandidaten",
            }

        verified_primary_candidates = [
            item
            for item in bounded_candidates
            if item["is_primary"]
            and item["venue_source"] == "eodhd-search-exchange-filter"
            and len(item["allowed_mics"]) == 1
        ]
        if len(verified_primary_candidates) == 1:
            selection_candidates = verified_primary_candidates
            selection_policy = "provider-verified-primary"
        else:
            selection_candidates = bounded_candidates
            selection_policy = "ollama-bounded-choice"

        selector = self._mapping_selector
        if selector is None:
            from mail_agent.config import load_config as load_mail_config

            from .portfolio_mapping import OllamaPortfolioMappingSelector

            selector = OllamaPortfolioMappingSelector(load_mail_config().ollama).select
        selection = selector(
            {
                "task": "select-exact-portfolio-market-mapping",
                "selection_policy": selection_policy,
                "instrument": {
                    "isin": normalized,
                    "holding_name": current["name"],
                    "wkn": current["wkn"],
                },
                "candidates": selection_candidates,
            }
        )
        status = str(selection.get("status") or "").strip().lower()
        raw_confidence = selection.get("confidence")
        if isinstance(raw_confidence, bool) or not isinstance(raw_confidence, (int, float)):
            raise RuntimeError("Ollama lieferte keine gueltige Konfidenz")
        confidence = float(raw_confidence)
        if not math.isfinite(confidence) or not 0 <= confidence <= 1:
            raise RuntimeError("Ollama-Konfidenz liegt ausserhalb von 0 bis 1")
        if status == "uncertain":
            return {
                "ok": False,
                "status": "uncertain",
                "read_only": True,
                "stored": False,
                "isin": normalized,
                "provider_candidates": len(bounded_candidates),
                "selection_candidates": len(selection_candidates),
                "selection_policy": selection_policy,
                "ollama": {
                    "model": str(selection.get("model") or ""),
                    "confidence": round(confidence, 4),
                    "reason": str(selection.get("reason") or "")[:240],
                },
                "error": "Ollama konnte keinen belastbaren Boersenplatz auswaehlen",
            }
        if status != "candidate":
            raise RuntimeError("Ollama lieferte keinen gueltigen Mappingstatus")
        raw_selected_id = selection.get("candidate_id")
        if isinstance(raw_selected_id, bool) or not isinstance(raw_selected_id, int):
            raise RuntimeError("Ollama lieferte keine gueltige candidate_id")
        selected_id = raw_selected_id
        selected = next(
            (item for item in selection_candidates if item["candidate_id"] == selected_id),
            None,
        )
        if selected is None:
            raise RuntimeError("Ollama waehlte keine vorhandene EODHD-candidate_id")
        mic = str(selection.get("mic") or "").strip().upper()
        if mic not in selected["allowed_mics"]:
            raise RuntimeError("Ollama waehlte keinen fuer den EODHD-Kandidaten erlaubten MIC")
        candidate = {
            "isin": normalized,
            "name": selected["name"] or current["name"],
            "symbol": selected["symbol"],
            "mic": mic,
            "currency": selected["currency"],
            "provider_symbol": EodhdClient.ticker({"symbol": selected["symbol"], "mic": mic}),
            "provider_exchange": selected["exchange"],
            "provider_primary": selected["is_primary"],
            "provider_venue_source": selected["venue_source"],
        }
        next_argv = [
            "portfolio",
            "watchlist",
            "add",
            "--isin",
            str(candidate["isin"]),
            "--name",
            str(candidate["name"]),
            "--symbol",
            str(candidate["symbol"]),
            "--mic",
            str(candidate["mic"]),
            "--currency",
            str(candidate["currency"]),
            "--yes",
        ]
        result = {
            "ok": True,
            "status": "candidate",
            "read_only": True,
            "stored": False,
            "source": "eodhd-search+ollama-selection",
            "provider_candidates": len(bounded_candidates),
            "selection_candidates": len(selection_candidates),
            "selection_policy": selection_policy,
            "candidate": candidate,
            "ollama": {
                "model": str(selection.get("model") or ""),
                "confidence": round(confidence, 4),
                "reason": str(selection.get("reason") or "")[:240],
            },
            "approval_required": True,
            "approval": "explicit-user-watchlist-change",
            "next_tool": "portfolio.watchlist.add",
            "next_action": {
                "tool_id": "portfolio.watchlist.add",
                "approval": "explicit-user-watchlist-change",
                "argv": next_argv,
                "command": shlex.join(["/opt/openclaw-agent/scripts/assistant.sh", *next_argv]),
            },
        }
        if discovery is not None:
            result["source"] = "eodhd-name-search+eodhd-isin-search+ollama-selection"
            result["discovery"] = discovery
        return result

    def watchlist_add(self, *, isin: str, name: str, symbol: str, mic: str, currency: str) -> dict[str, Any]:
        self._require_enabled()
        isin = isin.strip().upper()
        symbol = _canonical_eodhd_symbol(symbol)
        mic = mic.strip().upper()
        currency = currency.strip().upper()
        if not _valid_isin(isin):
            raise ValueError("ISIN ist ungueltig")
        if not MIC_RE.fullmatch(mic):
            raise ValueError("MIC muss aus genau vier Buchstaben/Ziffern bestehen")
        if mic not in EODHD_EXCHANGE_BY_MIC:
            raise ValueError(f"EODHD-Boersencode fuer MIC {mic} ist nicht registriert")
        if len(currency) != 3 or not currency.isalpha():
            raise ValueError("Waehrung muss ein dreistelliger Code sein")
        now = _iso(self._now())
        with self.store.connection:
            self.store.connection.execute(
                """
                INSERT INTO instruments(isin,name,symbol,mic,currency,mapping_confirmed,updated_at)
                VALUES(?,?,?,?,?,1,?)
                ON CONFLICT(isin) DO UPDATE SET
                    name=excluded.name,symbol=excluded.symbol,mic=excluded.mic,
                    currency=excluded.currency,mapping_confirmed=1,updated_at=excluded.updated_at
                """,
                (isin, name.strip() or isin, symbol, mic, currency, now),
            )
            self.store.connection.execute(
                """
                INSERT INTO watchlist(isin,enabled,added_at,updated_at) VALUES(?,1,?,?)
                ON CONFLICT(isin) DO UPDATE SET enabled=1,updated_at=excluded.updated_at
                """,
                (isin, now, now),
            )
        return {
            "ok": True,
            "isin": isin,
            "name": name.strip() or isin,
            "symbol": symbol,
            "mic": mic,
            "currency": currency,
            "mapping_confirmed": True,
        }

    def watchlist_disable(self, isin: str) -> dict[str, Any]:
        self._require_enabled()
        with self.store.connection:
            cursor = self.store.connection.execute(
                "UPDATE watchlist SET enabled=0,updated_at=? WHERE isin=? AND enabled=1",
                (_iso(self._now()), isin.strip().upper()),
            )
        if cursor.rowcount != 1:
            raise ValueError("Aktiver Watchlist-Eintrag nicht gefunden")
        return {"ok": True, "isin": isin.strip().upper(), "enabled": False}

    def _targets(self) -> list[dict[str, Any]]:
        held = {row["isin"] for row in self.holdings()["positions"] if _decimal(row["shares"]) != 0}
        watched = {
            row["isin"]
            for row in self.store.connection.execute("SELECT isin FROM watchlist WHERE enabled=1").fetchall()
        }
        targets = sorted(held | watched)
        if not targets:
            return []
        placeholders = ",".join("?" for _ in targets)
        rows = self.store.connection.execute(
            f"""
            SELECT isin,name,symbol,mic,currency,mapping_confirmed
            FROM instruments WHERE isin IN ({placeholders}) ORDER BY isin
            """,
            tuple(targets),
        ).fetchall()
        return [{**dict(row), "held": row["isin"] in held} for row in rows]

    def _required_fx_pairs(self) -> list[tuple[str, str]]:
        currencies: set[str] = set()
        for item in self.holdings().get("positions", []):
            if _decimal(item.get("shares")) == 0:
                continue
            snapshot_currency = str(item.get("currency") or "").strip().upper()
            quote_currency = str(item.get("quote_currency") or "").strip().upper()
            currencies.update(currency for currency in (snapshot_currency, quote_currency) if currency)
        currencies.update(
            str(item.get("currency") or "").strip().upper()
            for item in self._targets()
            if item.get("mapping_confirmed") and item.get("currency")
        )
        # EODHD EURUSD means USD per one EUR. Using EUR as the base gives one
        # deterministic reporting contract: divide a USD amount by EURUSD.
        return sorted(
            (PORTFOLIO_REPORTING_CURRENCY, currency)
            for currency in currencies
            if currency != PORTFOLIO_REPORTING_CURRENCY
        )

    def _fetch_quotes(
        self,
        items: list[dict[str, Any]],
        fx_pairs: list[tuple[str, str]],
    ) -> tuple[
        dict[str, Quote],
        dict[str, str],
        dict[tuple[str, str], FxQuote],
        dict[tuple[str, str], str],
    ]:
        if self._quote_fetcher is not None:
            override_quotes: dict[str, Quote] = {}
            override_errors: dict[str, str] = {}
            for item in items:
                try:
                    override_quotes[str(item["isin"])] = self._quote_fetcher(item)
                except Exception as exc:
                    override_errors[str(item["isin"])] = str(exc)[:500]
            override_fx_quotes: dict[tuple[str, str], FxQuote] = {}
            override_fx_errors: dict[tuple[str, str], str] = {}
            for pair in fx_pairs:
                if self._fx_quote_fetcher is None:
                    override_fx_errors[pair] = "Kein EODHD-FX-Abruf fuer die Waehrungsumrechnung verfuegbar"
                    continue
                try:
                    override_fx_quotes[pair] = self._fx_quote_fetcher(*pair)
                except Exception as exc:
                    override_fx_errors[pair] = str(exc)[:500]
            return override_quotes, override_errors, override_fx_quotes, override_fx_errors
        if self.settings.provider != "eodhd":
            raise RuntimeError("Kein EODHD-Marktdatenanbieter konfiguriert")
        api_key = os.environ.get(self.settings.api_key_env, "").strip()
        if not api_key:
            raise RuntimeError(f"API-Schluessel fehlt in Umgebungsvariable {self.settings.api_key_env}")
        client = EodhdClient(api_key, timeout=self.settings.request_timeout_seconds)
        quotes: dict[str, Quote] = {}
        errors: dict[str, str] = {}
        fx_quotes: dict[tuple[str, str], FxQuote] = {}
        fx_errors: dict[tuple[str, str], str] = {}
        valid_items: list[dict[str, Any]] = []
        for item in items:
            try:
                client.ticker(item)
                valid_items.append(item)
            except ValueError as exc:
                errors[str(item["isin"])] = str(exc)[:500]
        if len(fx_pairs) >= EODHD_BATCH_LIMIT:
            raise ValueError("Zu viele verschiedene Waehrungspaare fuer einen sicheren EODHD-Batch")
        offset = 0
        first = True
        while offset < len(valid_items) or (first and fx_pairs):
            included_pairs = fx_pairs if first else []
            room = EODHD_BATCH_LIMIT - len(included_pairs)
            chunk = valid_items[offset : offset + room]
            offset += len(chunk)
            try:
                fetched_quotes, fetched_fx = client.fetch_market_data(chunk, included_pairs)
                quotes.update(fetched_quotes)
                fx_quotes.update(fetched_fx)
            except Exception as exc:
                safe = str(exc).replace(api_key, "<redacted>")[:500]
                for item in chunk:
                    errors[str(item["isin"])] = safe
                for pair in included_pairs:
                    fx_errors[pair] = safe
            first = False
        for pair in fx_pairs:
            if pair not in fx_quotes and pair not in fx_errors:
                fx_errors[pair] = f"EODHD lieferte keinen Wechselkurs fuer {EodhdClient.fx_ticker(*pair)}"
        return quotes, errors, fx_quotes, fx_errors

    def refresh_quotes(self, *, force: bool = False) -> dict[str, Any]:
        self._require_enabled()
        started_dt = self._now()
        last_attempt = self.store.connection.execute(
            """
            SELECT finished_at,status,error FROM quote_runs
            ORDER BY id DESC LIMIT 1
            """
        ).fetchone()
        if not force and last_attempt and last_attempt["status"] == "failed":
            error = str(last_attempt["error"] or "")
            non_retryable = next(
                (code for code in ("HTTP 401", "HTTP 402", "HTTP 403") if code in error),
                "",
            )
            last_attempt_at = _parse_time(last_attempt["finished_at"])
            if non_retryable and last_attempt_at is not None:
                next_retry_at = (last_attempt_at.astimezone(UTC) + timedelta(days=1)).replace(
                    hour=0, minute=0, second=0, microsecond=0
                )
                if started_dt < next_retry_at:
                    return {
                        "ok": False,
                        "status": "skipped-provider-cooldown",
                        "provider": self.settings.provider,
                        "reason": non_retryable,
                        "last_attempt_at": _iso(last_attempt_at),
                        "next_retry_at": _iso(next_retry_at),
                        "force_allowed_only_for_explicit_diagnostic": True,
                    }
        last_success = self.store.connection.execute(
            """
            SELECT finished_at FROM quote_runs
            WHERE status='success' ORDER BY id DESC LIMIT 1
            """
        ).fetchone()
        last_finished = _parse_time(last_success["finished_at"]) if last_success else None
        due_after = self.settings.interval_minutes * 60
        if (
            not force
            and last_finished is not None
            and (started_dt - last_finished).total_seconds() < due_after
        ):
            return {
                "ok": True,
                "status": "skipped-not-due",
                "last_success_at": _iso(last_finished),
                "next_due_in_seconds": max(0, int(due_after - (started_dt - last_finished).total_seconds())),
            }
        started = time.perf_counter()
        targets = self._targets()[: self.settings.max_symbols]
        received = 0
        failures: list[dict[str, str]] = []
        warnings: list[dict[str, str]] = []
        triggered_events: list[dict[str, Any]] = []
        held_missing = 0
        fx_pairs = self._required_fx_pairs()
        fx_failures: list[dict[str, str]] = []
        eligible: list[dict[str, Any]] = []
        for item in targets:
            if not item["mapping_confirmed"] or not item["symbol"] or not item["mic"]:
                failures.append({"isin": item["isin"], "error": "Symbol/MIC-Zuordnung nicht bestaetigt"})
                held_missing += int(item["held"])
            else:
                eligible.append(item)
        quotes: dict[str, Quote] = {}
        fetch_errors: dict[str, str] = {}
        fx_quotes: dict[tuple[str, str], FxQuote] = {}
        fx_fetch_errors: dict[tuple[str, str], str] = {}
        if eligible or fx_pairs:
            try:
                quotes, fetch_errors, fx_quotes, fx_fetch_errors = self._fetch_quotes(eligible, fx_pairs)
            except Exception as exc:
                error = str(exc)[:500]
                fetch_errors = {str(item["isin"]): error for item in eligible}
                fx_fetch_errors = {pair: error for pair in fx_pairs}
        for pair in fx_pairs:
            fx_quote = fx_quotes.get(pair)
            if pair in fx_fetch_errors:
                fx_failures.append(
                    {
                        "pair": f"{pair[0]}/{pair[1]}",
                        "provider_symbol": EodhdClient.fx_ticker(*pair),
                        "error": fx_fetch_errors[pair],
                    }
                )
                continue
            if fx_quote is None:
                fx_failures.append(
                    {
                        "pair": f"{pair[0]}/{pair[1]}",
                        "provider_symbol": EodhdClient.fx_ticker(*pair),
                        "error": "EODHD lieferte keinen Wechselkurs",
                    }
                )
                continue
            try:
                if fx_quote.rate <= 0 or not math.isfinite(float(fx_quote.rate)):
                    raise ValueError("Wechselkurs ist nicht positiv oder nicht endlich")
                observed = _parse_time(fx_quote.observed_at)
                if observed is None:
                    raise ValueError("FX-Quellzeitstempel fehlt oder ist ungueltig")
                received_at = self._now()
                source_age = (received_at - observed).total_seconds()
                if source_age < -300:
                    raise ValueError("FX-Quellzeitstempel liegt unplausibel in der Zukunft")
                with self.store.connection:
                    self.store.connection.execute(
                        """
                        INSERT OR IGNORE INTO fx_quotes(
                            base_currency,quote_currency,provider,rate,observed_at,
                            received_at,delay_seconds
                        ) VALUES(?,?,?,?,?,?,?)
                        """,
                        (
                            fx_quote.base_currency,
                            fx_quote.quote_currency,
                            fx_quote.provider,
                            str(fx_quote.rate),
                            _iso(observed),
                            _iso(received_at),
                            max(0, int(source_age)),
                        ),
                    )
            except Exception as exc:
                fx_failures.append(
                    {
                        "pair": f"{pair[0]}/{pair[1]}",
                        "provider_symbol": EodhdClient.fx_ticker(*pair),
                        "error": str(exc)[:500],
                    }
                )
        for item in eligible:
            isin = str(item["isin"])
            if isin in fetch_errors:
                failures.append({"isin": isin, "error": fetch_errors[isin]})
                held_missing += int(item["held"])
                continue
            quote = quotes.get(isin)
            if quote is None:
                failures.append(
                    {
                        "isin": isin,
                        "error": f"EODHD lieferte keinen Kurs fuer {item['symbol']}/{item['mic']}",
                    }
                )
                held_missing += int(item["held"])
                continue
            try:
                if quote.price <= 0 or not math.isfinite(float(quote.price)):
                    raise ValueError("Kurs ist nicht positiv oder nicht endlich")
                if (
                    item.get("currency")
                    and quote.currency
                    and quote.currency.upper() != str(item["currency"]).upper()
                ):
                    raise ValueError(
                        "Kurswaehrung stimmt nicht mit der bestaetigten Instrumentzuordnung ueberein"
                    )
                observed = _parse_time(quote.observed_at)
                if observed is None:
                    raise ValueError("Quellzeitstempel fehlt oder ist ungueltig")
                received_at = self._now()
                source_age = (received_at - observed).total_seconds()
                if source_age < -300:
                    raise ValueError("Quellzeitstempel liegt unplausibel in der Zukunft")
                if (
                    quote.market_open
                    if quote.market_open is not None
                    else self._instrument_market_open(item, received_at)
                ) and source_age > self.settings.stale_critical_minutes * 60:
                    raise ValueError("Marktdatenquelle lieferte einen kritisch veralteten Kurs")
                if (
                    quote.market_open
                    if quote.market_open is not None
                    else self._instrument_market_open(item, received_at)
                ) and source_age > self.settings.stale_warning_minutes * 60:
                    warnings.append(
                        {"isin": item["isin"], "warning": "Marktdatenquelle lieferte einen veralteten Kurs"}
                    )
                delay = max(0, int((received_at - observed).total_seconds()))
                with self.store.connection:
                    self.store.connection.execute(
                        """
                        INSERT INTO quotes(
                            isin,provider,price,currency,observed_at,received_at,delay_seconds,
                            open,high,low,volume,market_open
                        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
                        ON CONFLICT(isin,provider,observed_at) DO UPDATE SET
                            price=excluded.price,
                            currency=excluded.currency,
                            received_at=excluded.received_at,
                            delay_seconds=excluded.delay_seconds,
                            open=excluded.open,
                            high=excluded.high,
                            low=excluded.low,
                            volume=excluded.volume,
                            market_open=excluded.market_open
                        """,
                        (
                            item["isin"],
                            quote.provider,
                            str(quote.price),
                            quote.currency or item["currency"],
                            _iso(observed),
                            _iso(received_at),
                            delay,
                            str(quote.open) if quote.open is not None else None,
                            str(quote.high) if quote.high is not None else None,
                            str(quote.low) if quote.low is not None else None,
                            str(quote.volume) if quote.volume is not None else None,
                            int(quote.market_open) if quote.market_open is not None else None,
                        ),
                    )
                received += 1
                triggered_events.extend(self._evaluate_alerts(item["isin"], quote))
            except Exception as exc:
                failures.append({"isin": item["isin"], "error": str(exc)[:500]})
                held_missing += int(item["held"])
        status = "success"
        if held_missing or fx_failures:
            status = "failed"
        elif failures or warnings:
            status = "degraded"
        latency = round((time.perf_counter() - started) * 1000.0, 2)
        error = "; ".join(
            [
                *(f"{item['isin']}: {item['error']}" for item in failures),
                *(f"FX {item['pair']}: {item['error']}" for item in fx_failures),
            ]
        )[:4000]
        with self.store.connection:
            cursor = self.store.connection.execute(
                """
                INSERT INTO quote_runs(
                    started_at,finished_at,status,expected,received,held_missing,latency_ms,error
                ) VALUES(?,?,?,?,?,?,?,?)
                """,
                (
                    _iso(started_dt),
                    _iso(self._now()),
                    status,
                    len(targets),
                    received,
                    held_missing,
                    latency,
                    error,
                ),
            )
        notification = {"attempted": False, "ok": True, "detail": "Keine neue Kursmarke"}
        if triggered_events:
            summary = " | ".join(
                (
                    f"KURSMARKE {event['isin']}: {event['direction']} "
                    f"{event['threshold']} {event['currency']}, beobachtet "
                    f"{event['price']} um {event['observed_at']}"
                )
                for event in triggered_events[:8]
            )
            notification = self._notifier(
                "Eine ueberwachte Kursmarke wurde neu erreicht. "
                + summary
                + " Dies ist ein regelbasiertes Informationssignal und keine Orderempfehlung."
            )
        return {
            "ok": status != "failed",
            "status": status,
            "run_id": int(cursor.lastrowid),
            "expected": len(targets),
            "received": received,
            "held_missing": held_missing,
            "fx_expected": len(fx_pairs),
            "fx_received": len(fx_pairs) - len(fx_failures),
            "fx_failures": fx_failures,
            "failures": failures,
            "warnings": warnings,
            "triggered_events": triggered_events,
            "notification": notification,
            "latency_ms": latency,
        }

    def _market_open(self, now: datetime) -> bool:
        local = now.astimezone(ZoneInfo(self.settings.timezone))
        if local.weekday() >= 5:
            return False
        opening = clock_time.fromisoformat(self.settings.market_open)
        closing = clock_time.fromisoformat(self.settings.market_close)
        return opening <= local.time().replace(tzinfo=None) <= closing

    def _instrument_market_open(self, item: dict[str, Any], now: datetime) -> bool:
        mic = str(item.get("mic") or "").upper()
        if mic == "XETR":
            local = now.astimezone(ZoneInfo("Europe/Berlin"))
            return local.weekday() < 5 and clock_time(9, 0) <= local.time().replace(
                tzinfo=None
            ) <= clock_time(17, 30)
        if mic == "XLON":
            local = now.astimezone(ZoneInfo("Europe/London"))
            return local.weekday() < 5 and clock_time(8, 0) <= local.time().replace(
                tzinfo=None
            ) <= clock_time(16, 30)
        if mic in {"XNAS", "XNGS", "XNYS"}:
            local = now.astimezone(ZoneInfo("America/New_York"))
            return local.weekday() < 5 and clock_time(9, 30) <= local.time().replace(
                tzinfo=None
            ) <= clock_time(16, 0)
        return self._market_open(now)

    def health(self) -> dict[str, Any]:
        enabled = self.settings.enabled
        if not enabled:
            return {
                "enabled": False,
                "ok": True,
                "state": "disabled",
                "coverage": None,
                "required": 0,
                "fresh": 0,
            }
        targets = self._targets()
        now = self._now()
        market_open = any(self._instrument_market_open(item, now) for item in targets)
        held_total = sum(int(item["held"]) for item in targets)
        held_fresh = 0
        held_stale = 0
        watch_missing = 0
        held_missing = 0
        fx_stale = 0
        fx_missing = 0
        details: list[dict[str, Any]] = []
        fx_details: list[dict[str, Any]] = []
        warning_seconds = self.settings.stale_warning_minutes * 60
        critical_seconds = self.settings.stale_critical_minutes * 60
        for item in targets:
            row = self.store.connection.execute(
                """
                SELECT price,currency,provider,observed_at,received_at,delay_seconds,market_open
                FROM quotes WHERE isin=? ORDER BY observed_at DESC,id DESC LIMIT 1
                """,
                (item["isin"],),
            ).fetchone()
            observed = _parse_time(row["observed_at"]) if row else None
            age = int((now - observed).total_seconds()) if observed else None
            mapping_ok = bool(item["mapping_confirmed"] and item["symbol"] and item["mic"])
            provider_symbol = None
            mapping_error = None
            if mapping_ok and self.settings.provider == "eodhd":
                try:
                    provider_symbol = EodhdClient.ticker(item)
                except ValueError as exc:
                    mapping_ok = False
                    mapping_error = str(exc)
            provider_open = None if not row or row["market_open"] is None else bool(row["market_open"])
            effective_open = self._instrument_market_open(item, now) and provider_open is not False
            stale = bool(not mapping_ok or (effective_open and (age is None or age > warning_seconds)))
            critical = bool(not mapping_ok or (effective_open and (age is None or age > critical_seconds)))
            if item["held"]:
                if critical or row is None:
                    held_missing += 1
                elif stale:
                    held_stale += 1
                else:
                    held_fresh += 1
            elif stale or row is None:
                watch_missing += 1
            details.append(
                {
                    "isin": item["isin"],
                    "held": bool(item["held"]),
                    "observed_at": row["observed_at"] if row else None,
                    "age_seconds": age,
                    "stale": stale,
                    "critical": critical,
                    "mapping_confirmed": mapping_ok,
                    "mapping_error": mapping_error,
                    "provider_market_open": provider_open,
                    "quote_provider": row["provider"] if row else None,
                    "provider_symbol": provider_symbol,
                }
            )
        forex_open = self._forex_market_open(now)
        for base_currency, quote_currency in self._required_fx_pairs():
            row = self.store.connection.execute(
                """
                SELECT rate,provider,observed_at,received_at
                FROM fx_quotes
                WHERE base_currency=? AND quote_currency=?
                ORDER BY observed_at DESC,id DESC LIMIT 1
                """,
                (base_currency, quote_currency),
            ).fetchone()
            observed = _parse_time(row["observed_at"]) if row else None
            age = int((now - observed).total_seconds()) if observed else None
            stale = bool(row is None or (forex_open and (age is None or age > warning_seconds)))
            critical = bool(row is None or (forex_open and (age is None or age > critical_seconds)))
            if critical:
                fx_missing += 1
            elif stale:
                fx_stale += 1
            fx_details.append(
                {
                    "pair": f"{base_currency}/{quote_currency}",
                    "provider_symbol": EodhdClient.fx_ticker(base_currency, quote_currency),
                    "rate": row["rate"] if row else None,
                    "provider": row["provider"] if row else None,
                    "observed_at": row["observed_at"] if row else None,
                    "received_at": row["received_at"] if row else None,
                    "age_seconds": age,
                    "stale": stale,
                    "critical": critical,
                }
            )
        last_run = self.store.connection.execute(
            "SELECT * FROM quote_runs ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if held_missing or fx_missing:
            state = "failed"
        elif held_stale or watch_missing or fx_stale or (last_run and last_run["status"] != "success"):
            state = "degraded"
        else:
            state = "healthy"
        coverage = held_fresh / held_total if held_total else (1.0 if not targets else None)
        return {
            "enabled": True,
            "ok": state == "healthy",
            "state": state,
            "provider": self.settings.provider,
            "market_open": market_open,
            "required": held_total,
            "fresh": held_fresh,
            "coverage": round(coverage, 4) if coverage is not None else None,
            "held_missing_or_critical": held_missing,
            "held_stale_warning": held_stale,
            "watchlist_missing_or_stale": watch_missing,
            "fx_required": len(fx_details),
            "fx_fresh": len(fx_details) - fx_missing - fx_stale,
            "fx_missing_or_critical": fx_missing,
            "fx_stale_warning": fx_stale,
            "fx_market_open": forex_open,
            "fx_quotes": fx_details,
            "last_run": dict(last_run) if last_run else None,
            "instruments": details,
            "database_integrity": self.store.integrity(),
        }

    def _research_client(self) -> ResearchProvider:
        self._require_enabled()
        if self.settings.provider != "eodhd":
            raise RuntimeError("EODHD-Research ist nicht konfiguriert")
        if self._research_provider is not None:
            return self._research_provider
        api_key = os.environ.get(self.settings.api_key_env, "").strip()
        if not api_key:
            raise RuntimeError(f"API-Schluessel fehlt in Umgebungsvariable {self.settings.api_key_env}")
        return EodhdResearchClient(
            api_key,
            timeout=self.settings.request_timeout_seconds,
        )

    def research_models(self) -> dict[str, Any]:
        return research_models()

    def research_status(self) -> dict[str, Any]:
        api_key_present = bool(os.environ.get(self.settings.api_key_env, "").strip())
        database_integrity = self.store.integrity()
        last_run = self.store.connection.execute(
            """
            SELECT id,created_at,kind,strategy,status,candidate_count,error
            FROM research_runs ORDER BY created_at DESC,id DESC LIMIT 1
            """
        ).fetchone()
        successful = self.store.connection.execute(
            "SELECT created_at FROM research_runs WHERE status='success' ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        configuration_ok = bool(
            self.settings.enabled
            and self.settings.provider == "eodhd"
            and api_key_present
            and database_integrity == "ok"
        )
        last_status = str(last_run["status"] or "") if last_run else ""
        if not configuration_ok:
            state = "misconfigured"
        elif not last_run:
            state = "unverified"
        elif last_status == "success":
            state = "healthy"
        elif last_status == "partial":
            state = "degraded"
        else:
            state = "failed"
        last_error = str(last_run["error"] or "") if last_run else ""
        entitlement_state = (
            "denied"
            if state == "failed" and ("HTTP 402" in last_error or "HTTP 403" in last_error)
            else "verified"
            if state == "healthy"
            else "unverified"
        )
        return {
            "ok": state == "healthy",
            "state": state,
            "configuration_ok": configuration_ok,
            "enabled": self.settings.enabled,
            "provider": self.settings.provider,
            "api_key_present": api_key_present,
            "api_key_env": self.settings.api_key_env,
            "database_integrity": database_integrity,
            "models": research_models(),
            "entitlement": {
                "state": entitlement_state,
                "required_endpoints": ["screener", "v1.1/fundamentals", "eod"],
                "verified_by_successful_run": successful is not None,
                "currently_verified": state == "healthy",
                "last_verified_at": successful["created_at"] if successful else None,
            },
            "last_run": dict(last_run) if last_run else None,
            "profile": self.philosophy_show(),
        }

    def _research_ticker_for_isin(self, isin: str) -> str:
        normalized = isin.strip().upper()
        if not _valid_isin(normalized):
            raise ValueError("ISIN ist ungueltig")
        row = self.store.connection.execute(
            """
            SELECT isin,symbol,mic,currency,mapping_confirmed
            FROM instruments WHERE isin=?
            """,
            (normalized,),
        ).fetchone()
        if row and row["mapping_confirmed"] and row["symbol"] and row["mic"]:
            return EodhdClient.ticker(dict(row))
        api_key = os.environ.get(self.settings.api_key_env, "").strip()
        searcher = self._mapping_searcher
        if searcher is None:
            if not api_key:
                raise RuntimeError(f"API-Schluessel fehlt in Umgebungsvariable {self.settings.api_key_env}")
            searcher = EodhdClient(
                api_key,
                timeout=self.settings.request_timeout_seconds,
            ).search_by_isin
        candidates = searcher(normalized)
        primary = [
            item
            for item in candidates
            if bool(item.get("is_primary")) and str(item.get("venue_source") or "").startswith("eodhd-search")
        ]
        selected = primary if len(primary) == 1 else candidates if len(candidates) == 1 else []
        if len(selected) != 1:
            raise RuntimeError(
                "EODHD konnte fuer die Research-Analyse keine eindeutige Primaernotierung belegen"
            )
        item = selected[0]
        symbol = _canonical_eodhd_symbol(item.get("symbol"))
        exchange = str(item.get("exchange") or "").strip().upper()
        suffix = {
            "NASDAQ": "US",
            "NYSE": "US",
            "US": "US",
            "XETRA": "XETRA",
            "LSE": "LSE",
        }.get(exchange)
        if not suffix:
            raise RuntimeError("EODHD-Research unterstuetzt diese Primaernotierung noch nicht")
        return f"{symbol}.{suffix}"

    def _store_research_run(
        self,
        *,
        kind: str,
        strategy: str,
        request: dict[str, Any],
        analyses: list[dict[str, Any]],
        failures: list[dict[str, str]],
    ) -> tuple[str, list[dict[str, Any]]]:
        run_id = str(uuid.uuid4())
        created_at = _iso(self._now())
        status = "success" if analyses and not failures else "partial" if analyses else "failed"
        model_version = str(research_models()["model_version"])
        stored: list[dict[str, Any]] = []
        with self.store.connection:
            self.store.connection.execute(
                """
                INSERT INTO research_runs(
                    id,created_at,kind,strategy,request_json,provider,model_version,
                    status,candidate_count,error
                ) VALUES(?,?,?,?,?,'eodhd',?,?,?,?)
                """,
                (
                    run_id,
                    created_at,
                    kind,
                    strategy,
                    json.dumps(request, ensure_ascii=False, sort_keys=True),
                    model_version,
                    status,
                    len(analyses),
                    "; ".join(
                        (
                            f"{item.get('endpoint')}: {item['error']}"
                            if item.get("endpoint")
                            else item["error"]
                        )
                        for item in failures
                    )[:1000],
                ),
            )
            for rank, analysis in enumerate(analyses, start=1):
                identity = analysis["identity"]
                candidate_id = str(uuid.uuid4())
                rendered = {**analysis, "candidate_id": candidate_id, "research_run_id": run_id}
                self.store.connection.execute(
                    """
                    INSERT INTO research_candidates(
                        id,run_id,rank,isin,ticker,name,sector,industry,strategy,score,
                        metric_coverage,verdict,eligible,analysis_json,created_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        candidate_id,
                        run_id,
                        rank,
                        str(identity.get("isin") or ""),
                        str(identity.get("ticker") or ""),
                        str(identity.get("name") or "")[:200],
                        str(identity.get("sector") or "")[:100],
                        str(identity.get("industry") or "")[:120],
                        strategy,
                        "" if analysis.get("score") is None else str(analysis["score"]),
                        str(analysis.get("metric_coverage") or 0),
                        str(analysis.get("verdict") or "abstain"),
                        int(bool(analysis.get("profile_fit", {}).get("eligible", True))),
                        json.dumps(rendered, ensure_ascii=False, sort_keys=True),
                        created_at,
                    ),
                )
                stored.append(rendered)
        return run_id, stored

    @staticmethod
    def _research_failure(exc: RuntimeError | ValueError, *, ticker: str = "") -> dict[str, Any]:
        if isinstance(exc, ResearchProviderError):
            return exc.render(ticker=ticker)
        result = {"error": str(exc)}
        if ticker:
            result["ticker"] = ticker
        return result

    def research_analyze(self, isin: str, *, strategy: str = "auto") -> dict[str, Any]:
        profile = self.philosophy_show()
        selected_strategy = self._research_strategy(strategy, profile)
        ticker = self._research_ticker_for_isin(isin)
        client = self._research_client()
        from_date = self._now().date() - timedelta(days=550)
        failures: list[dict[str, str]] = []
        try:
            analysis = analyze_research_payload(
                client.fundamentals(ticker),
                client.history(ticker, from_date=from_date),
                strategy=selected_strategy,
                expected_ticker=ticker,
                expected_isin=isin,
                now=self._now(),
            )
            analysis["profile_fit"] = self._research_profile_fit(analysis, profile)
            analyses = [analysis]
        except (RuntimeError, ValueError) as exc:
            failures = [self._research_failure(exc, ticker=ticker)]
            analyses = []
        run_id, stored = self._store_research_run(
            kind="analysis",
            strategy=selected_strategy,
            request={"isin": isin.strip().upper(), "ticker": ticker},
            analyses=analyses,
            failures=failures,
        )
        if not stored:
            return {
                "ok": False,
                "decision": "abstain",
                "research_run_id": run_id,
                "strategy": selected_strategy,
                "failures": failures,
            }
        return stored[0]

    @staticmethod
    def _research_strategy(strategy: str, profile: dict[str, Any]) -> str:
        normalized = strategy.strip().casefold()
        if normalized == "auto":
            configured = profile.get("profile") if profile.get("configured") else None
            normalized = str((configured or {}).get("strategy") or "balanced")
        if normalized not in RESEARCH_STRATEGIES:
            raise ValueError("Research-Modell ist ungueltig")
        return normalized

    @staticmethod
    def _research_profile_fit(
        analysis: dict[str, Any],
        profile_result: dict[str, Any],
    ) -> dict[str, Any]:
        if not profile_result.get("configured"):
            return {
                "configured": False,
                "eligible": True,
                "alignment": "unknown",
                "reasons": ["Noch kein ausdruecklich bestaetigtes Investmentprofil vorhanden"],
            }
        profile = profile_result["profile"]
        sector = str(analysis.get("identity", {}).get("sector") or "").casefold()
        excluded = {str(item).casefold() for item in profile.get("excluded_sectors", [])}
        preferred = {str(item).casefold() for item in profile.get("preferred_sectors", [])}
        reasons: list[str] = []
        eligible = not sector or sector not in excluded
        if sector in excluded:
            reasons.append("Sektor ist im bestaetigten Investmentprofil ausgeschlossen")
        if preferred and sector in preferred:
            reasons.append("Sektor entspricht einer ausdruecklich bevorzugten Branche")
        if not reasons:
            reasons.append("Keine ausdrueckliche Sektorabweichung zum Profil erkannt")
        return {
            "configured": True,
            "profile_version": profile["version"],
            "eligible": eligible,
            "alignment": "excluded" if not eligible else "preferred" if sector in preferred else "neutral",
            "reasons": reasons,
        }

    def research_screen(
        self,
        *,
        strategy: str = "auto",
        exchange: str = "",
        sector: str = "",
        limit: int = 5,
    ) -> dict[str, Any]:
        if limit < 1 or limit > 10:
            raise ValueError("Research-Screener-Limit muss zwischen 1 und 10 liegen")
        profile = self.philosophy_show()
        selected_strategy = self._research_strategy(strategy, profile)
        client = self._research_client()
        request = {
            "strategy": selected_strategy,
            "exchange": exchange.strip().upper(),
            "sector": " ".join(sector.split())[:80],
            "limit": limit,
        }
        try:
            provider_rows = client.screen(
                strategy=selected_strategy,
                exchange=exchange,
                sector=sector,
                limit=min(30, max(limit * 3, 10)),
            )
        except (RuntimeError, ValueError) as exc:
            screen_failures = [self._research_failure(exc)]
            run_id, _ = self._store_research_run(
                kind="screen",
                strategy=selected_strategy,
                request=request,
                analyses=[],
                failures=screen_failures,
            )
            return {
                "ok": False,
                "decision": "abstain",
                "research_run_id": run_id,
                "provider": "eodhd",
                "strategy": selected_strategy,
                "model_version": research_models()["model_version"],
                "provider_candidates": 0,
                "analyzed": 0,
                "suggestion_count": 0,
                "suggestions": [],
                "candidates": [],
                "failures": screen_failures,
                "profile": profile,
                "disclaimer": (
                    "Keine Analyse ohne belegte EODHD-Providerdaten; "
                    "keine Kauf-/Verkaufsempfehlung oder Orderfreigabe."
                ),
            }
        known = {
            str(row[0]).upper()
            for row in self.store.connection.execute(
                """
                SELECT DISTINCT i.isin FROM instruments i
                LEFT JOIN watchlist w ON w.isin=i.isin
                WHERE w.enabled=1 OR i.isin IN (
                    SELECT isin FROM position_snapshots
                    WHERE import_id=(SELECT MAX(id) FROM imports)
                )
                """
            ).fetchall()
        }
        analyses: list[dict[str, Any]] = []
        failures: list[dict[str, str]] = []
        from_date = self._now().date() - timedelta(days=550)
        for row in provider_rows:
            ticker = str(row.get("ticker") or "")
            try:
                analysis = analyze_research_payload(
                    client.fundamentals(ticker),
                    client.history(ticker, from_date=from_date),
                    strategy=selected_strategy,
                    expected_ticker=ticker,
                    now=self._now(),
                )
                if str(analysis["identity"].get("isin") or "").upper() in known:
                    continue
                analysis["screen_evidence"] = row
                analysis["profile_fit"] = self._research_profile_fit(analysis, profile)
                analyses.append(analysis)
            except (RuntimeError, ValueError) as exc:
                failures.append(self._research_failure(exc, ticker=ticker))
            if len(analyses) >= limit * 2:
                break
        analyses.sort(
            key=lambda item: (
                not bool(item.get("ok")),
                not bool(item.get("profile_fit", {}).get("eligible", True)),
                -float(item.get("score") or -1),
                str(item.get("identity", {}).get("ticker") or ""),
            )
        )
        analyses = analyses[:limit]
        run_id, stored = self._store_research_run(
            kind="screen",
            strategy=selected_strategy,
            request=request,
            analyses=analyses,
            failures=failures,
        )
        suggestions = [
            item
            for item in stored
            if item.get("verdict") == "research-candidate"
            and item.get("profile_fit", {}).get("eligible", True)
        ]
        return {
            "ok": bool(stored),
            "decision": "informational" if stored else "abstain",
            "research_run_id": run_id,
            "provider": "eodhd",
            "strategy": selected_strategy,
            "model_version": research_models()["model_version"],
            "provider_candidates": len(provider_rows),
            "analyzed": len(stored),
            "suggestion_count": len(suggestions),
            "suggestions": suggestions,
            "candidates": stored,
            "failures": failures,
            "profile": profile,
            "disclaimer": (
                "Erklaerbare Research-Kandidaten; keine Kauf-/Verkaufsempfehlung oder Orderfreigabe."
            ),
        }

    def research_history(self, *, limit: int = 20) -> dict[str, Any]:
        rows = self.store.connection.execute(
            """
            SELECT id,created_at,kind,strategy,provider,model_version,status,
                   candidate_count,error
            FROM research_runs ORDER BY created_at DESC,id DESC LIMIT ?
            """,
            (max(1, min(int(limit), 100)),),
        ).fetchall()
        return {"ok": True, "count": len(rows), "runs": [dict(row) for row in rows]}

    @staticmethod
    def _sector_list(values: list[str]) -> list[str]:
        result: list[str] = []
        seen: set[str] = set()
        for value in values:
            cleaned = " ".join(str(value or "").split())[:100]
            key = cleaned.casefold()
            if cleaned and key not in seen:
                result.append(cleaned)
                seen.add(key)
        return result[:20]

    def philosophy_show(self) -> dict[str, Any]:
        row = self.store.connection.execute(
            "SELECT * FROM investment_profiles ORDER BY version DESC LIMIT 1"
        ).fetchone()
        if not row:
            return {
                "ok": True,
                "configured": False,
                "profile": None,
                "learning_policy": "Beobachtungen aendern das deklarierte Profil niemals automatisch.",
            }
        profile = dict(row)
        profile["preferred_sectors"] = json.loads(profile.pop("preferred_sectors_json"))
        profile["excluded_sectors"] = json.loads(profile.pop("excluded_sectors_json"))
        return {
            "ok": True,
            "configured": True,
            "profile": profile,
            "learning_policy": "Beobachtungen aendern das deklarierte Profil niemals automatisch.",
        }

    def philosophy_set(
        self,
        *,
        risk_tolerance: str,
        horizon_years: int,
        strategy: str,
        max_position_pct: Decimal,
        max_sector_pct: Decimal,
        preferred_sectors: list[str],
        excluded_sectors: list[str],
        notes: str = "",
    ) -> dict[str, Any]:
        self._require_enabled()
        risk = risk_tolerance.strip().casefold()
        if risk not in {"conservative", "balanced", "growth"}:
            raise ValueError("Risikotoleranz muss conservative, balanced oder growth sein")
        strategy = strategy.strip().casefold()
        if strategy not in RESEARCH_STRATEGIES:
            raise ValueError("Investmentstil ist ungueltig")
        if horizon_years < 1 or horizon_years > 50:
            raise ValueError("Anlagehorizont muss zwischen 1 und 50 Jahren liegen")
        if not Decimal("1") <= max_position_pct <= Decimal("100"):
            raise ValueError("Maximale Positionsgroesse muss zwischen 1 und 100 Prozent liegen")
        if not Decimal("1") <= max_sector_pct <= Decimal("100"):
            raise ValueError("Maximale Sektorquote muss zwischen 1 und 100 Prozent liegen")
        preferred = self._sector_list(preferred_sectors)
        excluded = self._sector_list(excluded_sectors)
        overlap = {item.casefold() for item in preferred} & {item.casefold() for item in excluded}
        if overlap:
            raise ValueError("Ein Sektor darf nicht zugleich bevorzugt und ausgeschlossen sein")
        notes = " ".join(notes.split())[:1000]
        with self.store.connection:
            cursor = self.store.connection.execute(
                """
                INSERT INTO investment_profiles(
                    created_at,risk_tolerance,horizon_years,strategy,max_position_pct,
                    max_sector_pct,preferred_sectors_json,excluded_sectors_json,notes
                ) VALUES(?,?,?,?,?,?,?,?,?)
                """,
                (
                    _iso(self._now()),
                    risk,
                    horizon_years,
                    strategy,
                    str(max_position_pct),
                    str(max_sector_pct),
                    json.dumps(preferred, ensure_ascii=False),
                    json.dumps(excluded, ensure_ascii=False),
                    notes,
                ),
            )
        result = self.philosophy_show()
        if cursor.lastrowid is None:
            raise RuntimeError("Anlageprofil konnte nicht versioniert werden")
        result["new_version"] = int(cursor.lastrowid)
        result["append_only"] = True
        return result

    def philosophy_history(self, *, limit: int = 20) -> dict[str, Any]:
        rows = self.store.connection.execute(
            "SELECT * FROM investment_profiles ORDER BY version DESC LIMIT ?",
            (max(1, min(int(limit), 100)),),
        ).fetchall()
        versions: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            item["preferred_sectors"] = json.loads(item.pop("preferred_sectors_json"))
            item["excluded_sectors"] = json.loads(item.pop("excluded_sectors_json"))
            versions.append(item)
        return {"ok": True, "count": len(versions), "versions": versions}

    def philosophy_feedback(
        self,
        *,
        candidate_id: str,
        decision: str,
        reason: str,
    ) -> dict[str, Any]:
        self._require_enabled()
        decision = decision.strip().casefold()
        if decision not in {"interested", "rejected", "watch", "bought", "sold"}:
            raise ValueError("Unbekannte Investment-Rueckmeldung")
        candidate = self.store.connection.execute(
            """
            SELECT id,isin,ticker,name,sector,strategy,verdict,created_at
            FROM research_candidates WHERE id=?
            """,
            (candidate_id.strip(),),
        ).fetchone()
        if not candidate:
            raise ValueError("Research-Kandidat ist unbekannt")
        reason = " ".join(reason.split())[:500]
        if not reason:
            raise ValueError("Rueckmeldung benoetigt eine kurze Begruendung")
        feedback_id = str(uuid.uuid4())
        created_at = _iso(self._now())
        with self.store.connection:
            self.store.connection.execute(
                """
                INSERT INTO investment_feedback(id,candidate_id,decision,reason,created_at)
                VALUES(?,?,?,?,?)
                """,
                (feedback_id, candidate_id.strip(), decision, reason, created_at),
            )
        return {
            "ok": True,
            "feedback_id": feedback_id,
            "candidate": dict(candidate),
            "decision": decision,
            "reason": reason,
            "created_at": created_at,
            "declared_profile_changed": False,
        }

    def philosophy_review(self) -> dict[str, Any]:
        profile_result = self.philosophy_show()
        praise: list[dict[str, Any]] = []
        critique: list[dict[str, Any]] = []
        limitations: list[str] = []
        valuation = self.valuation()
        concentration: dict[str, Any] = {
            "valuation_complete": bool(valuation.get("ok")),
            "positions": [],
            "sectors": [],
        }
        if not profile_result.get("configured"):
            limitations.append(
                "Kein bestaetigtes Investmentprofil; keine persoenliche Grenzwertkritik moeglich"
            )
        if not valuation.get("ok"):
            limitations.append("Aktuelle EUR-Bewertung ist unvollstaendig; Konzentration wird nicht bewertet")
        if profile_result.get("configured") and valuation.get("ok"):
            profile = profile_result["profile"]
            positions = valuation.get("positions", [])
            total = sum(Decimal(str(item["current_value"])) for item in positions)
            rendered_positions: list[dict[str, Any]] = []
            for item in positions:
                value = Decimal(str(item["current_value"]))
                percentage = value / total * Decimal("100") if total else Decimal("0")
                rendered_positions.append(
                    {
                        "isin": item["isin"],
                        "name": item["name"],
                        "value_eur": item["current_value"],
                        "percentage": self._rounded(percentage),
                    }
                )
            rendered_positions.sort(key=lambda item: Decimal(item["percentage"]), reverse=True)
            concentration["positions"] = rendered_positions
            max_position = Decimal(str(profile["max_position_pct"]))
            if rendered_positions and Decimal(rendered_positions[0]["percentage"]) > max_position:
                critique.append(
                    {
                        "rule": "max-position-pct",
                        "evidence": rendered_positions[0],
                        "message": "Groesste Position ueberschreitet die selbst gesetzte Obergrenze.",
                    }
                )
            elif rendered_positions:
                praise.append(
                    {
                        "rule": "max-position-pct",
                        "evidence": rendered_positions[0],
                        "message": "Groesste Position liegt innerhalb der selbst gesetzten Obergrenze.",
                    }
                )
            sector_rows = self.store.connection.execute(
                """
                SELECT c.isin,c.sector FROM research_candidates c
                JOIN (
                    SELECT isin,MAX(created_at) AS latest
                    FROM research_candidates GROUP BY isin
                ) latest ON latest.isin=c.isin AND latest.latest=c.created_at
                """
            ).fetchall()
            sectors_by_isin = {str(row["isin"]): str(row["sector"]) for row in sector_rows if row["sector"]}
            sector_values: dict[str, Decimal] = {}
            covered = Decimal("0")
            for item in positions:
                sector_name = sectors_by_isin.get(str(item["isin"]))
                if not sector_name:
                    continue
                value = Decimal(str(item["current_value"]))
                sector_values[sector_name] = sector_values.get(sector_name, Decimal("0")) + value
                covered += value
            sector_coverage = covered / total if total else Decimal("0")
            concentration["sector_coverage"] = self._rounded(sector_coverage * Decimal("100"))
            if sector_coverage < Decimal("0.8"):
                limitations.append("Sektorabdeckung der gehaltenen Positionen liegt unter 80 Prozent")
            else:
                maximum = Decimal(str(profile["max_sector_pct"]))
                excluded = {str(item).casefold() for item in profile["excluded_sectors"]}
                for name, value in sorted(sector_values.items(), key=lambda item: item[1], reverse=True):
                    percentage = value / total * Decimal("100") if total else Decimal("0")
                    evidence = {"sector": name, "percentage": self._rounded(percentage)}
                    concentration["sectors"].append(evidence)
                    if name.casefold() in excluded:
                        critique.append(
                            {
                                "rule": "excluded-sector",
                                "evidence": evidence,
                                "message": (
                                    "Gehaltene Position widerspricht einem ausdruecklich "
                                    "ausgeschlossenen Sektor."
                                ),
                            }
                        )
                    if percentage > maximum:
                        critique.append(
                            {
                                "rule": "max-sector-pct",
                                "evidence": evidence,
                                "message": "Sektorgewicht ueberschreitet die selbst gesetzte Obergrenze.",
                            }
                        )
                if not any(item["rule"] == "max-sector-pct" for item in critique):
                    praise.append(
                        {
                            "rule": "max-sector-pct",
                            "evidence": {"limit": str(maximum)},
                            "message": (
                                "Alle ausreichend belegten Sektorgewichte liegen innerhalb der Obergrenze."
                            ),
                        }
                    )
        feedback_rows = self.store.connection.execute(
            """
            SELECT f.decision,c.sector,c.strategy
            FROM investment_feedback f
            JOIN research_candidates c ON c.id=f.candidate_id
            ORDER BY f.created_at
            """
        ).fetchall()
        sector_feedback: dict[str, dict[str, int]] = {}
        for row in feedback_rows:
            sector_name = str(row["sector"] or "Unbekannt")
            bucket = sector_feedback.setdefault(sector_name, {"positive": 0, "negative": 0})
            if row["decision"] in {"interested", "watch", "bought"}:
                bucket["positive"] += 1
            elif row["decision"] == "rejected":
                bucket["negative"] += 1
        samples = len(feedback_rows)
        inferred = [
            {
                "sector": sector_name,
                **counts,
                "sample_size": counts["positive"] + counts["negative"],
            }
            for sector_name, counts in sorted(sector_feedback.items())
            if counts["positive"] + counts["negative"] >= 2
        ]
        confidence = "high" if samples >= 15 else "medium" if samples >= 5 else "low"
        return {
            "ok": True,
            "profile": profile_result,
            "concentration": concentration,
            "praise": praise,
            "critique": critique,
            "learning": {
                "feedback_samples": samples,
                "confidence": confidence,
                "inferred_sector_preferences": inferred,
                "automatic_profile_changes": False,
            },
            "limitations": limitations,
            "method": (
                "Kritik und Lob nur gegen ausdruecklich bestaetigte Grenzwerte und belegte EUR-/Sektordaten."
            ),
            "disclaimer": "Verhaltens- und Portfolioreflexion; keine individuelle Anlageberatung.",
        }

    def status(self) -> dict[str, Any]:
        health = self.health()
        configuration = self._configuration_status()
        database_integrity = self.store.integrity()
        return {
            "ok": bool(configuration["ok"] and database_integrity == "ok" and health["ok"]),
            "enabled": self.settings.enabled,
            "database": str(self.store.path),
            "import_root": str(self.settings.import_root),
            "nextcloud_folder": self.settings.nextcloud_folder,
            "provider": self.settings.provider,
            "interval_minutes": self.settings.interval_minutes,
            "stale_warning_minutes": self.settings.stale_warning_minutes,
            "stale_critical_minutes": self.settings.stale_critical_minutes,
            "configuration": configuration,
            "database_integrity": database_integrity,
            "health": health,
            "holdings": self.holdings(),
            "watchlist": self.watchlist(),
        }

    def _configuration_status(self) -> dict[str, Any]:
        key_present = bool(os.environ.get(self.settings.api_key_env, "").strip())
        provider_ok = not self.settings.enabled or self.settings.provider == "eodhd"
        import_root_present = self.settings.import_root.is_dir()
        configuration_ok = not self.settings.enabled or (provider_ok and key_present and import_root_present)
        return {
            "ok": configuration_ok,
            "provider_ok": provider_ok,
            "api_key_present": key_present,
            "api_key_env": self.settings.api_key_env,
            "import_root_present": import_root_present,
        }

    def doctor(self) -> dict[str, Any]:
        health = self.health()
        configuration = self._configuration_status()
        return {
            "ok": configuration["ok"] and self.store.integrity() == "ok" and bool(health["ok"]),
            "configuration_ok": configuration["ok"],
            "provider_ok": configuration["provider_ok"],
            "api_key_present": configuration["api_key_present"],
            "api_key_env": configuration["api_key_env"],
            "import_root_present": configuration["import_root_present"],
            "database_integrity": self.store.integrity(),
            "health": health,
        }

    def _series(self, isin: str, limit: int = 500) -> list[dict[str, Any]]:
        rows = self.store.connection.execute(
            """
            SELECT price AS close,open,high,low,volume,currency,observed_at,received_at,
                   provider,market_open
            FROM quotes WHERE isin=? ORDER BY observed_at DESC,id DESC LIMIT ?
            """,
            (isin, max(1, min(limit, 5000))),
        ).fetchall()
        return [dict(row) for row in reversed(rows)]

    def latest_quote(self, isin: str) -> dict[str, Any]:
        """Return one stored quote plus its fail-closed current EUR value."""
        isin = isin.strip().upper()
        health_item = next(
            (item for item in self.health().get("instruments", []) if item["isin"] == isin),
            None,
        )
        if health_item is None:
            raise ValueError("ISIN ist weder im Depot noch auf der Watchlist")
        instrument = self.store.connection.execute(
            "SELECT name,symbol,mic,currency FROM instruments WHERE isin=?", (isin,)
        ).fetchone()
        series = self._series(isin, limit=1)
        if not series:
            return {
                "ok": False,
                "isin": isin,
                "name": str(instrument["name"] if instrument else isin),
                "reason": "Kein gespeicherter Kurs vorhanden",
                "stale": True,
                "critical": True,
            }
        quote = series[-1]
        quote_currency = str(quote["currency"] or "").strip().upper()
        conversion_error = None
        price_eur = None
        fx_detail = None
        try:
            if not quote_currency:
                raise ValueError("Kurswaehrung fehlt")
            conversion = self._fx_conversion(
                source_currency=quote_currency,
                target_currency=PORTFOLIO_REPORTING_CURRENCY,
            )
            price_eur = self._rounded(_decimal(quote["close"]) * conversion["rate"], "0.000001")
            if conversion["provider_symbol"] is not None:
                fx_detail = self._render_fx_detail(
                    source_currency=quote_currency,
                    target_currency=PORTFOLIO_REPORTING_CURRENCY,
                    conversion=conversion,
                )
        except (ValueError, InvalidOperation, ZeroDivisionError) as exc:
            conversion_error = str(exc)
        return {
            "ok": not bool(health_item["critical"]) and conversion_error is None,
            "isin": isin,
            "name": str(instrument["name"] if instrument else isin),
            "symbol": str(instrument["symbol"] if instrument else ""),
            "mic": str(instrument["mic"] if instrument else ""),
            "provider_symbol": health_item.get("provider_symbol"),
            "price": quote["close"],
            "currency": quote_currency,
            "price_eur": price_eur,
            "reporting_currency": PORTFOLIO_REPORTING_CURRENCY,
            "fx": fx_detail,
            "conversion_error": conversion_error,
            "observed_at": quote["observed_at"],
            "received_at": quote["received_at"],
            "provider": quote["provider"],
            "market_open": quote["market_open"],
            "age_seconds": health_item["age_seconds"],
            "stale": bool(health_item["stale"]),
            "critical": bool(health_item["critical"]),
        }

    @staticmethod
    def _rounded(value: Decimal, places: str = "0.01") -> str:
        return str(value.quantize(Decimal(places), rounding=ROUND_HALF_UP))

    @staticmethod
    def _forex_market_open(now: datetime) -> bool:
        utc = now.astimezone(UTC)
        if utc.weekday() in {0, 1, 2, 3}:
            return True
        if utc.weekday() == 4:
            return utc.hour < 22
        if utc.weekday() == 6:
            return utc.hour >= 22
        return False

    def _fx_conversion(
        self,
        *,
        source_currency: str,
        target_currency: str,
    ) -> dict[str, Any]:
        source = source_currency.strip().upper()
        target = target_currency.strip().upper()
        if source == target:
            return {
                "rate": Decimal("1"),
                "provider_rate": Decimal("1"),
                "provider_symbol": None,
                "base_currency": target,
                "quote_currency": source,
                "observed_at": None,
                "received_at": None,
                "provider": None,
                "inverted": False,
            }
        row = self.store.connection.execute(
            """
            SELECT base_currency,quote_currency,provider,rate,observed_at,received_at
            FROM fx_quotes
            WHERE base_currency=? AND quote_currency=?
            ORDER BY observed_at DESC,id DESC LIMIT 1
            """,
            (target, source),
        ).fetchone()
        inverted = True
        if row is None:
            row = self.store.connection.execute(
                """
                SELECT base_currency,quote_currency,provider,rate,observed_at,received_at
                FROM fx_quotes
                WHERE base_currency=? AND quote_currency=?
                ORDER BY observed_at DESC,id DESC LIMIT 1
                """,
                (source, target),
            ).fetchone()
            inverted = False
        if row is None:
            raise ValueError(f"Kein gespeicherter EODHD-Wechselkurs fuer {source}/{target} vorhanden")
        provider_rate = _decimal(row["rate"])
        if provider_rate <= 0 or not math.isfinite(float(provider_rate)):
            raise ValueError(f"Gespeicherter EODHD-Wechselkurs fuer {source}/{target} ist ungueltig")
        observed = _parse_time(row["observed_at"])
        if observed is None:
            raise ValueError(f"EODHD-Wechselkurs fuer {source}/{target} hat keinen Quellzeitstempel")
        age_seconds = int((self._now() - observed).total_seconds())
        if age_seconds < -300:
            raise ValueError(f"EODHD-Wechselkurs fuer {source}/{target} liegt in der Zukunft")
        if self._forex_market_open(self._now()) and age_seconds > self.settings.stale_critical_minutes * 60:
            raise ValueError(f"EODHD-Wechselkurs fuer {source}/{target} ist kritisch veraltet")
        # If the stored provider pair is TARGET/SOURCE (EURUSD for USD->EUR),
        # EODHD reports SOURCE units per TARGET unit, hence the reciprocal.
        conversion_rate = Decimal("1") / provider_rate if inverted else provider_rate
        return {
            "rate": conversion_rate,
            "provider_rate": provider_rate,
            "provider_symbol": EodhdClient.fx_ticker(str(row["base_currency"]), str(row["quote_currency"])),
            "base_currency": str(row["base_currency"]),
            "quote_currency": str(row["quote_currency"]),
            "observed_at": str(row["observed_at"]),
            "received_at": str(row["received_at"]),
            "provider": str(row["provider"]),
            "inverted": inverted,
            "age_seconds": age_seconds,
        }

    def _render_fx_detail(
        self,
        *,
        source_currency: str,
        target_currency: str,
        conversion: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            "provider_symbol": conversion["provider_symbol"],
            "base_currency": conversion["base_currency"],
            "quote_currency": conversion["quote_currency"],
            "provider_rate": self._rounded(conversion["provider_rate"], "0.00000001"),
            "conversion_rate": self._rounded(conversion["rate"], "0.00000001"),
            "conversion": (
                f"1 {source_currency} = {self._rounded(conversion['rate'], '0.00000001')} {target_currency}"
            ),
            "observed_at": conversion["observed_at"],
            "received_at": conversion["received_at"],
            "provider": conversion["provider"],
            "inverted": bool(conversion["inverted"]),
            "age_seconds": conversion.get("age_seconds"),
        }

    def valuation(self) -> dict[str, Any]:
        """Value every latest holding in EUR without mixing currencies."""
        holdings = self.holdings()
        health_by_isin = {str(item["isin"]): item for item in self.health().get("instruments", [])}
        positions: list[dict[str, Any]] = []
        failures: list[dict[str, Any]] = []
        totals: dict[str, dict[str, Decimal]] = {}
        fx_used: dict[str, dict[str, Any]] = {}
        for item in holdings.get("positions", []):
            isin = str(item["isin"])
            try:
                shares = _decimal(item.get("shares"))
                entry_text = str(item.get("entry_price") or "").strip()
                snapshot_currency = str(item.get("currency") or "").strip().upper()
                quote_currency = str(item.get("quote_currency") or "").strip().upper()
                if not entry_text:
                    raise ValueError("Einstiegskurs fehlt im Depot-Snapshot")
                if not snapshot_currency:
                    raise ValueError("Snapshot-Waehrung fehlt")
                if not quote_currency:
                    raise ValueError("Bestaetigte Kurswaehrung fehlt")
                health_item = health_by_isin.get(isin)
                if health_item is None or bool(health_item.get("critical")):
                    raise ValueError("Aktienkurs fehlt oder ist kritisch veraltet")
                quote = self.store.connection.execute(
                    """
                    SELECT price,currency,provider,observed_at,received_at
                    FROM quotes WHERE isin=? ORDER BY observed_at DESC,id DESC LIMIT 1
                    """,
                    (isin,),
                ).fetchone()
                if quote is None:
                    raise ValueError("Kein gespeicherter Aktienkurs vorhanden")
                stored_quote_currency = str(quote["currency"] or "").upper()
                if stored_quote_currency != quote_currency:
                    raise ValueError("Gespeicherte Kurswaehrung widerspricht der bestaetigten Zuordnung")
                quote_observed = _parse_time(quote["observed_at"])
                if quote_observed is None:
                    raise ValueError("Aktienkurs hat keinen gueltigen Quellzeitstempel")
                quote_price = _decimal(quote["price"])
                quote_conversion = self._fx_conversion(
                    source_currency=quote_currency,
                    target_currency=PORTFOLIO_REPORTING_CURRENCY,
                )
                entry_conversion = self._fx_conversion(
                    source_currency=snapshot_currency,
                    target_currency=PORTFOLIO_REPORTING_CURRENCY,
                )
                converted_price = quote_price * quote_conversion["rate"]
                entry_price = _decimal(entry_text)
                entry_price_eur = entry_price * entry_conversion["rate"]
                cost_basis = shares * entry_price_eur
                current_value = shares * converted_price
                gain = current_value - cost_basis
                gain_percent = gain / cost_basis * Decimal("100") if cost_basis != 0 else None
                fx_detail = None
                if quote_conversion["provider_symbol"] is not None:
                    fx_detail = self._render_fx_detail(
                        source_currency=quote_currency,
                        target_currency=PORTFOLIO_REPORTING_CURRENCY,
                        conversion=quote_conversion,
                    )
                    fx_used[str(quote_conversion["provider_symbol"])] = fx_detail
                entry_fx_detail = None
                if entry_conversion["provider_symbol"] is not None:
                    entry_fx_detail = self._render_fx_detail(
                        source_currency=snapshot_currency,
                        target_currency=PORTFOLIO_REPORTING_CURRENCY,
                        conversion=entry_conversion,
                    )
                    fx_used[str(entry_conversion["provider_symbol"])] = entry_fx_detail
                positions.append(
                    {
                        "account": item["account"],
                        "isin": isin,
                        "name": item["name"],
                        "shares": str(shares),
                        "entry_price": self._rounded(entry_price),
                        "entry_currency": snapshot_currency,
                        "entry_price_eur": self._rounded(entry_price_eur, "0.000001"),
                        "current_price": str(quote_price),
                        "quote_currency": quote_currency,
                        "current_price_converted": self._rounded(converted_price, "0.000001"),
                        "current_price_eur": self._rounded(converted_price, "0.000001"),
                        "valuation_currency": PORTFOLIO_REPORTING_CURRENCY,
                        "cost_basis": self._rounded(cost_basis),
                        "current_value": self._rounded(current_value),
                        "gain": self._rounded(gain),
                        "gain_percent": self._rounded(gain_percent) if gain_percent is not None else None,
                        "quote_observed_at": str(quote["observed_at"]),
                        "quote_received_at": str(quote["received_at"]),
                        "quote_provider": str(quote["provider"]),
                        "fx": fx_detail,
                        "entry_fx": entry_fx_detail,
                    }
                )
                bucket = totals.setdefault(
                    PORTFOLIO_REPORTING_CURRENCY,
                    {"cost_basis": Decimal("0"), "current_value": Decimal("0"), "gain": Decimal("0")},
                )
                bucket["cost_basis"] += cost_basis
                bucket["current_value"] += current_value
                bucket["gain"] += gain
            except (ValueError, InvalidOperation, ZeroDivisionError) as exc:
                error = str(exc)
                health_item = health_by_isin.get(isin) or {}
                latest_quote = self.store.connection.execute(
                    """
                    SELECT price,currency,provider,observed_at,received_at
                    FROM quotes WHERE isin=? ORDER BY observed_at DESC,id DESC LIMIT 1
                    """,
                    (isin,),
                ).fetchone()
                mapping_confirmed = bool(health_item.get("mapping_confirmed"))
                if error == "Aktienkurs fehlt oder ist kritisch veraltet":
                    failure_code = "equity-quote-missing-or-critical"
                elif "Wechselkurs" in error or "Waehrung" in error:
                    failure_code = "currency-conversion-unavailable"
                else:
                    failure_code = "valuation-input-invalid"
                registered_next_commands = [
                    "portfolio quotes status",
                    f'portfolio quotes get --isin "{isin}"',
                    "portfolio doctor",
                    "jobs check --target all --deep",
                ]
                if not mapping_confirmed:
                    registered_next_commands.insert(
                        2,
                        f'portfolio mapping suggest --isin "{isin}"',
                    )
                failures.append(
                    {
                        "isin": isin,
                        "name": str(item.get("name") or isin),
                        "error": error,
                        "failure_code": failure_code,
                        "mapping_confirmed": mapping_confirmed,
                        "mapping_action_required": not mapping_confirmed,
                        "symbol": str(item.get("symbol") or ""),
                        "mic": str(item.get("mic") or ""),
                        "provider_symbol": health_item.get("provider_symbol"),
                        "quote_price": latest_quote["price"] if latest_quote else None,
                        "quote_currency": latest_quote["currency"] if latest_quote else None,
                        "quote_provider": latest_quote["provider"] if latest_quote else None,
                        "quote_observed_at": latest_quote["observed_at"] if latest_quote else None,
                        "quote_received_at": latest_quote["received_at"] if latest_quote else None,
                        "quote_age_seconds": health_item.get("age_seconds"),
                        "quote_stale": bool(health_item.get("stale", True)),
                        "quote_critical": bool(health_item.get("critical", True)),
                        "provider_market_open": health_item.get("provider_market_open"),
                        "registered_next_commands": registered_next_commands,
                        "web_fallback_allowed": False,
                    }
                )
        complete = not failures and len(positions) == int(holdings.get("count") or 0)
        rendered_totals: dict[str, dict[str, str]] | None = None
        if complete:
            rendered_totals = {}
            for currency, values in totals.items():
                percentage = (
                    values["gain"] / values["cost_basis"] * Decimal("100")
                    if values["cost_basis"] != 0
                    else Decimal("0")
                )
                rendered_totals[currency] = {
                    "cost_basis": self._rounded(values["cost_basis"]),
                    "current_value": self._rounded(values["current_value"]),
                    "gain": self._rounded(values["gain"]),
                    "gain_percent": self._rounded(percentage),
                }
        return {
            "ok": complete,
            "status": "success" if complete else "incomplete",
            "snapshot_as_of": holdings.get("as_of"),
            "source": holdings.get("source"),
            "positions_expected": int(holdings.get("count") or 0),
            "positions_valued": len(positions),
            "positions": positions,
            "totals": rendered_totals,
            "reporting_currency": PORTFOLIO_REPORTING_CURRENCY,
            "fx_quotes": list(fx_used.values()),
            "failures": failures,
            "method": (
                "Aktueller EODHD-Kurs und Einstiegskurs, bei Fremdwaehrung mit "
                "zeitgestempeltem EODHD-FX-Kurs immer in EUR umgerechnet"
            ),
            "estimated": True,
        }

    @staticmethod
    def _sma(values: list[float], size: int) -> float | None:
        return round(sum(values[-size:]) / size, 8) if len(values) >= size else None

    @staticmethod
    def _rsi(values: list[float], size: int = 14) -> float | None:
        if len(values) <= size:
            return None
        changes = [values[index] - values[index - 1] for index in range(1, len(values))]
        recent = changes[-size:]
        gain = sum(max(change, 0.0) for change in recent) / size
        loss = sum(max(-change, 0.0) for change in recent) / size
        if loss == 0:
            return 100.0
        return round(100.0 - 100.0 / (1.0 + gain / loss), 4)

    def analyze(self, isin: str, *, limit: int = 500) -> dict[str, Any]:
        isin = isin.strip().upper()
        health_item = next(
            (item for item in self.health().get("instruments", []) if item["isin"] == isin),
            None,
        )
        series = self._series(isin, limit=limit)
        if health_item is None:
            raise ValueError("ISIN ist weder im Depot noch auf der Watchlist")
        if not series:
            return {
                "ok": False,
                "decision": "abstain",
                "isin": isin,
                "reason": "Keine Kursdaten vorhanden",
                "series": [],
            }
        if health_item["critical"]:
            return {
                "ok": False,
                "decision": "abstain",
                "isin": isin,
                "reason": "Pflichtkurs ist kritisch veraltet",
                "as_of": series[-1]["observed_at"],
                "series": series,
            }
        euro_quote = self.latest_quote(isin)
        if euro_quote.get("conversion_error"):
            return {
                "ok": False,
                "decision": "abstain",
                "isin": isin,
                "reason": f"Aktueller EUR-Wert nicht verfuegbar: {euro_quote['conversion_error']}",
                "as_of": series[-1]["observed_at"],
                "last_price": series[-1]["close"],
                "currency": series[-1]["currency"],
                "last_price_eur": None,
                "reporting_currency": PORTFOLIO_REPORTING_CURRENCY,
                "series": series,
            }
        values = [float(row["close"]) for row in series]
        sma20 = self._sma(values, 20)
        sma50 = self._sma(values, 50)
        sma200 = self._sma(values, 200)
        rsi14 = self._rsi(values)
        if len(values) < 20:
            decision = "abstain"
            trend = "insufficient_data"
            reason = "Mindestens 20 Beobachtungen fuer eine belastbare Trendanalyse erforderlich"
        else:
            decision = "informational"
            trend = (
                "up"
                if sma20 is not None and values[-1] > sma20 and (sma50 is None or sma20 > sma50)
                else "down"
                if sma20 is not None and values[-1] < sma20 and (sma50 is None or sma20 < sma50)
                else "sideways"
            )
            reason = "Deterministische Indikatoren; keine Kauf- oder Verkaufsempfehlung"
        return {
            "ok": decision != "abstain",
            "decision": decision,
            "reason": reason,
            "isin": isin,
            "as_of": series[-1]["observed_at"],
            "market_state": "open" if self._market_open(self._now()) else "closed",
            "source": series[-1]["provider"],
            "points": len(series),
            "last_price": values[-1],
            "currency": series[-1]["currency"],
            "last_price_eur": euro_quote["price_eur"],
            "reporting_currency": PORTFOLIO_REPORTING_CURRENCY,
            "fx": euro_quote["fx"],
            "indicators": {
                "trend": trend,
                "sma20": sma20,
                "sma50": sma50,
                "sma200": sma200,
                "rsi14": rsi14,
            },
            "series": series,
            "disclaimer": "Informationssystem ohne Orderausfuehrung; keine individuelle Anlageberatung.",
        }

    def alert_add(
        self,
        *,
        isin: str,
        direction: str,
        threshold: Decimal,
        currency: str,
        hysteresis_bps: int = 25,
        cooldown_minutes: int = 60,
    ) -> dict[str, Any]:
        self._require_enabled()
        isin = isin.strip().upper()
        direction = direction.strip().casefold()
        if direction not in {"above", "below"}:
            raise ValueError("Richtung muss above oder below sein")
        if threshold <= 0:
            raise ValueError("Kursschwelle muss positiv sein")
        instrument = self.store.connection.execute(
            "SELECT currency FROM instruments WHERE isin=?", (isin,)
        ).fetchone()
        if not instrument:
            raise ValueError("ISIN ist unbekannt")
        currency = currency.strip().upper()
        if instrument["currency"] and currency != str(instrument["currency"]).upper():
            raise ValueError("Alarmwaehrung stimmt nicht mit der Instrumentwaehrung ueberein")
        rule_id = str(uuid.uuid4())
        now = _iso(self._now())
        with self.store.connection:
            self.store.connection.execute(
                """
                INSERT INTO alert_rules(
                    id,isin,direction,threshold,currency,hysteresis_bps,cooldown_minutes,
                    enabled,created_at,updated_at
                ) VALUES(?,?,?,?,?,?,?,1,?,?)
                """,
                (
                    rule_id,
                    isin,
                    direction,
                    str(threshold),
                    currency,
                    max(0, min(hysteresis_bps, 5000)),
                    max(0, min(cooldown_minutes, 10080)),
                    now,
                    now,
                ),
            )
        return {"ok": True, "id": rule_id, "isin": isin, "direction": direction}

    def alerts(self) -> dict[str, Any]:
        rules = self.store.connection.execute(
            "SELECT * FROM alert_rules WHERE enabled=1 ORDER BY created_at"
        ).fetchall()
        events = self.store.connection.execute(
            "SELECT * FROM alert_events ORDER BY created_at DESC LIMIT 100"
        ).fetchall()
        return {
            "ok": True,
            "rules": [dict(row) for row in rules],
            "events": [dict(row) for row in events],
        }

    def alert_disable(self, rule_id: str) -> dict[str, Any]:
        self._require_enabled()
        with self.store.connection:
            cursor = self.store.connection.execute(
                "UPDATE alert_rules SET enabled=0,updated_at=? WHERE id=? AND enabled=1",
                (_iso(self._now()), rule_id),
            )
        if cursor.rowcount != 1:
            raise ValueError("Aktive Alarmregel nicht gefunden")
        return {"ok": True, "id": rule_id, "enabled": False}

    def _evaluate_alerts(self, isin: str, quote: Quote) -> list[dict[str, Any]]:
        rules = self.store.connection.execute(
            "SELECT * FROM alert_rules WHERE enabled=1 AND isin=?", (isin,)
        ).fetchall()
        now = self._now()
        events: list[dict[str, Any]] = []
        for rule in rules:
            threshold = _decimal(rule["threshold"])
            price = quote.price
            crossed = price >= threshold if rule["direction"] == "above" else price <= threshold
            last_triggered = _parse_time(rule["last_triggered_at"])
            cooldown = int(rule["cooldown_minutes"]) * 60
            can_trigger = last_triggered is None or (now - last_triggered).total_seconds() >= cooldown
            if crossed and rule["last_state"] != "crossed" and can_trigger:
                with self.store.connection:
                    self.store.connection.execute(
                        """
                        INSERT INTO alert_events(id,rule_id,isin,state,price,observed_at,created_at)
                        VALUES(?,?,?,?,?,?,?)
                        """,
                        (
                            str(uuid.uuid4()),
                            rule["id"],
                            isin,
                            "triggered",
                            str(price),
                            quote.observed_at,
                            _iso(now),
                        ),
                    )
                    self.store.connection.execute(
                        """
                        UPDATE alert_rules SET last_state='crossed',last_triggered_at=?,
                            updated_at=? WHERE id=?
                        """,
                        (_iso(now), _iso(now), rule["id"]),
                    )
                events.append(
                    {
                        "rule_id": rule["id"],
                        "isin": isin,
                        "direction": rule["direction"],
                        "threshold": rule["threshold"],
                        "currency": rule["currency"],
                        "price": str(price),
                        "observed_at": quote.observed_at,
                    }
                )
            elif not crossed and rule["last_state"] == "crossed":
                hysteresis = threshold * Decimal(int(rule["hysteresis_bps"])) / Decimal(10_000)
                cleared = (
                    price <= threshold - hysteresis
                    if rule["direction"] == "above"
                    else price >= threshold + hysteresis
                )
                if cleared:
                    with self.store.connection:
                        self.store.connection.execute(
                            "UPDATE alert_rules SET last_state='clear',updated_at=? WHERE id=?",
                            (_iso(now), rule["id"]),
                        )
        return events

    def signal_performance(self) -> dict[str, Any]:
        return {
            "ok": True,
            "sample_size": 0,
            "coverage": 0.0,
            "forward_returns": None,
            "benchmark_adjusted": None,
            "max_drawdown": None,
            "status": "insufficient_data",
            "limitation": (
                "Technische Betriebsleistung ist getrennt. Signalqualitaet wird erst "
                "nach gespeicherten, zeitlich abgeschlossenen Beobachtungsfenstern bewertet."
            ),
        }


def _system_event_command(text: str) -> list[str]:
    return event_command(text)
