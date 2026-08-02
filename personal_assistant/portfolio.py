from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import os
import re
import sqlite3
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, time as clock_time, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

from .antivirus import HostAntivirus
from .tool_settings import PortfolioToolSettings


SCHEMA_VERSION = 3
MAX_IMPORT_BYTES = 25_000_000
ISIN_RE = re.compile(r"^[A-Z]{2}[A-Z0-9]{9}[0-9]$")
MIC_RE = re.compile(r"^[A-Z0-9]{4}$")
EODHD_EXCHANGE_BY_MIC = {
    "XETR": "XETRA",
    "XNAS": "US",
    "XNGS": "US",
    "XNYS": "US",
}
EODHD_BATCH_LIMIT = 20
PP_INDEX_RE = re.compile(r"security\[(\d+)\]")
POSITIVE_TYPES = {"BUY", "DELIVERY_INBOUND", "TRANSFER_IN"}
NEGATIVE_TYPES = {"SELL", "DELIVERY_OUTBOUND", "TRANSFER_OUT"}
DKB_CSV_REQUIRED_HEADERS = (
    "Datum der Erstellung",
    "Depotnummer",
    "Wertpapierbezeichnung",
    "WKN",
    "ISIN",
    "Einstiegskurs",
    "Bewertungskurs",
    "Stückzahl",
    "Absoluter Gewinn",
    "Relativer Gewinn",
    "Assetklasse",
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime | None = None) -> str:
    return (value or _now()).astimezone(timezone.utc).isoformat(timespec="seconds")


def _parse_time(value: object) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].casefold()


def _child_text(node: ET.Element, *names: str) -> str:
    wanted = {name.casefold() for name in names}
    for child in node:
        if _local_name(child.tag) in wanted:
            return str(child.text or "").strip()
    return ""


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
    digits = "".join(
        str(ord(character) - 55) if character.isalpha() else character
        for character in value
    )
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


QuoteFetcher = Callable[[dict[str, str]], Quote]
EventNotifier = Callable[[str], dict[str, Any]]


def _notify_openclaw(text: str) -> dict[str, Any]:
    try:
        completed = subprocess.run(
            ["openclaw", "system", "event", "--text", text[:1800], "--mode", "now"],
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

    def __init__(self, api_key: str, *, timeout: int = 20) -> None:
        self.api_key = api_key
        self.timeout = timeout

    @staticmethod
    def ticker(instrument: dict[str, str]) -> str:
        symbol = str(instrument.get("symbol") or "").strip().upper()
        mic = str(instrument.get("mic") or "").strip().upper()
        if not symbol:
            raise ValueError("EODHD-Zuordnung enthaelt kein Symbol")
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

    def fetch_many(self, instruments: list[dict[str, str]]) -> dict[str, Quote]:
        if not instruments:
            return {}
        if len(instruments) > EODHD_BATCH_LIMIT:
            raise ValueError(f"EODHD-Batch darf hoechstens {EODHD_BATCH_LIMIT} Symbole enthalten")
        ticker_to_item: dict[str, dict[str, str]] = {}
        for item in instruments:
            ticker = self.ticker(item)
            if ticker in ticker_to_item:
                raise ValueError(f"Doppelte EODHD-Zuordnung im Batch: {ticker}")
            ticker_to_item[ticker] = item
        tickers = list(ticker_to_item)
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
            isinstance(payload.get("code"), int)
            or str(payload.get("status") or "").casefold() == "error"
        ):
            raise self._provider_error(raw, "Anbieterfehler")
        rows = payload if isinstance(payload, list) else [payload]
        quotes: dict[str, Quote] = {}
        for row in rows:
            if not isinstance(row, dict):
                continue
            code = str(row.get("code") or "").strip().upper()
            item = ticker_to_item.get(code)
            if item is None:
                continue
            price_text = row.get("close")
            timestamp = row.get("timestamp")
            if price_text in {None, ""} or not str(timestamp or "").isdigit():
                continue
            observed = _iso(datetime.fromtimestamp(int(timestamp), timezone.utc))
            quotes[str(item["isin"])] = Quote(
                symbol=str(item["symbol"]),
                price=_decimal(price_text),
                currency=str(item.get("currency") or "").upper(),
                observed_at=observed,
                provider="eodhd",
                open=_decimal(row["open"]) if row.get("open") not in {None, ""} else None,
                high=_decimal(row["high"]) if row.get("high") not in {None, ""} else None,
                low=_decimal(row["low"]) if row.get("low") not in {None, ""} else None,
                volume=_decimal(row["volume"]) if row.get("volume") not in {None, ""} else None,
            )
        return quotes

    def fetch(self, instrument: dict[str, str]) -> Quote:
        quote = self.fetch_many([instrument]).get(str(instrument.get("isin") or ""))
        if quote is None:
            raise RuntimeError(f"EODHD lieferte keinen Kurs fuer {self.ticker(instrument)}")
        return quote


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
            """
        )
        quote_columns = {
            str(row[1]) for row in self.connection.execute("PRAGMA table_info(quotes)").fetchall()
        }
        for column in ("open", "high", "low", "volume", "market_open"):
            if column not in quote_columns:
                self.connection.execute(f"ALTER TABLE quotes ADD COLUMN {column} TEXT")
        position_columns = {
            str(row[1])
            for row in self.connection.execute(
                "PRAGMA table_info(position_snapshots)"
            ).fetchall()
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
                    f"ALTER TABLE position_snapshots "
                    f"ADD COLUMN {column} TEXT NOT NULL DEFAULT ''"
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


def parse_portfolio_performance_xml(data: bytes) -> dict[str, Any]:
    if len(data) > MAX_IMPORT_BYTES:
        raise ValueError("Portfolio-XML ist groesser als 25 MB")
    upper = data[:100_000].upper()
    if b"<!DOCTYPE" in upper or b"<!ENTITY" in upper:
        raise ValueError("DTD und externe Entitaeten sind im Portfolio-XML verboten")
    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        raise ValueError(f"Ungueltiges Portfolio-XML: {exc}") from exc

    securities: list[dict[str, str]] = []
    by_uuid: dict[str, dict[str, str]] = {}
    by_isin: dict[str, dict[str, str]] = {}
    by_id: dict[str, dict[str, str]] = {}
    splits_by_isin: dict[str, list[tuple[str, Decimal]]] = {}
    for node in root.iter():
        if _local_name(node.tag) != "security":
            continue
        isin = (_child_text(node, "isin") or node.attrib.get("isin", "")).strip().upper()
        if not _valid_isin(isin):
            continue
        item = {
            "isin": isin,
            "uuid": (_child_text(node, "uuid", "id") or node.attrib.get("uuid", "")).strip(),
            "name": (_child_text(node, "name") or node.attrib.get("name", "") or isin).strip(),
            "wkn": (_child_text(node, "wkn") or node.attrib.get("wkn", "")).strip().upper(),
            "symbol": (
                _child_text(node, "tickerSymbol", "ticker", "symbol")
                or node.attrib.get("ticker", "")
            ).strip().upper(),
            "mic": (
                _child_text(node, "mic", "market")
                or node.attrib.get("mic", "")
            ).strip().upper(),
            "currency": (
                _child_text(node, "currencyCode", "currency")
                or node.attrib.get("currency", "")
            ).strip().upper(),
        }
        securities.append(item)
        by_isin[isin] = item
        if item["uuid"]:
            by_uuid[item["uuid"]] = item
        if node.attrib.get("id"):
            by_id[str(node.attrib["id"])] = item
        splits: list[tuple[str, Decimal]] = []
        for event in node.iter():
            if _local_name(event.tag) not in {"event", "security-event"}:
                continue
            if _child_text(event, "type").strip().upper() != "STOCK_SPLIT":
                continue
            event_date = _child_text(event, "date")[:10]
            details = _child_text(event, "details")
            match = re.search(r"(\d+(?:[.,]\d+)?)\s*:\s*(\d+(?:[.,]\d+)?)", details)
            if not event_date or not match:
                raise ValueError(
                    f"Aktiensplit fuer {isin} kann nicht sicher ausgewertet werden"
                )
            numerator = _decimal(match.group(1))
            denominator = _decimal(match.group(2))
            if numerator <= 0 or denominator <= 0:
                raise ValueError(f"Ungueltiges Aktiensplit-Verhaeltnis fuer {isin}")
            splits.append((event_date, numerator / denominator))
        splits_by_isin[isin] = sorted(splits)

    positions: dict[tuple[str, str], Decimal] = {}
    as_of = ""
    transaction_accounts: dict[int, str] = {}

    def remember_accounts(node: ET.Element, account: str = "Depot") -> None:
        local = _local_name(node.tag)
        if local == "portfolio":
            account = _child_text(node, "name") or account
        if local in {"portfolio-transaction", "portfolio_transaction", "transaction"}:
            transaction_accounts[id(node)] = account
        for child in node:
            remember_accounts(child, account)

    remember_accounts(root)
    for node in root.iter():
        local = _local_name(node.tag)
        if local == "position":
            isin = (_child_text(node, "isin") or node.attrib.get("isin", "")).strip().upper()
            if isin not in by_isin:
                continue
            shares_raw = _child_text(node, "shares", "quantity") or node.attrib.get("shares", "")
            account = (_child_text(node, "account", "portfolio") or node.attrib.get("account", "Depot")).strip()
            positions[(account or "Depot", isin)] = _decimal(shares_raw)
            as_of = _child_text(node, "date", "asOf") or node.attrib.get("as_of", "") or as_of
            continue
        if local not in {"portfolio-transaction", "portfolio_transaction", "transaction"}:
            continue
        tx_type = (_child_text(node, "type") or node.attrib.get("type", "")).strip().upper()
        if tx_type not in POSITIVE_TYPES | NEGATIVE_TYPES:
            continue
        shares_node = next(
            (child for child in node if _local_name(child.tag) in {"shares", "quantity"}),
            None,
        )
        shares_text = (
            str(shares_node.text or "").strip() if shares_node is not None else ""
        ) or (shares_node.attrib.get("value", "") if shares_node is not None else "")
        # Portfolio Performance serializes Values.Share with six decimal places
        # (for example 47 shares as 47000000).
        scale = 6 if shares_text and shares_text.lstrip("-").isdigit() else 0
        shares = _decimal(shares_text, scale=scale)
        security_node = next(
            (child for child in node if _local_name(child.tag) == "security"),
            None,
        )
        reference = ""
        if security_node is not None:
            reference = str(
                security_node.attrib.get("reference")
                or security_node.attrib.get("uuid")
                or security_node.text
                or ""
            ).strip()
        item = by_uuid.get(reference) or by_id.get(reference) or by_isin.get(reference.upper())
        if item is None:
            match = PP_INDEX_RE.search(reference)
            if match and 0 < int(match.group(1)) <= len(securities):
                item = securities[int(match.group(1)) - 1]
            elif reference.endswith("/securities/security") and securities:
                item = securities[0]
        if item is None:
            isin = (_child_text(node, "isin") or node.attrib.get("isin", "")).strip().upper()
            item = by_isin.get(isin)
        if item is None:
            continue
        tx_date = _child_text(node, "date", "datetime")[:10]
        splits = splits_by_isin.get(item["isin"], [])
        if splits and not tx_date:
            raise ValueError(
                f"Transaktionsdatum fuer Aktiensplit-Berechnung bei {item['isin']} fehlt"
            )
        for split_date, factor in splits:
            if tx_date < split_date:
                shares *= factor
        account = (
            _child_text(node, "portfolio", "account")
            or node.attrib.get("account", "")
            or transaction_accounts.get(id(node), "Depot")
        ).strip()
        sign = Decimal("-1") if tx_type in NEGATIVE_TYPES else Decimal("1")
        key = (account, item["isin"])
        positions[key] = positions.get(key, Decimal("0")) + sign * shares
        as_of = max(as_of, tx_date or as_of)

    if not securities:
        raise ValueError("Keine Wertpapiere mit gueltiger ISIN im Portfolio-XML gefunden")
    return {
        "source_type": "portfolio-performance-xml",
        "as_of": as_of or _iso(),
        "instruments": securities,
        "positions": [
            {"account": account, "isin": isin, "shares": str(shares)}
            for (account, isin), shares in sorted(positions.items())
            if shares != 0
        ],
    }


def _dkb_decimal(
    value: object, *, field: str, allow_currency_or_percent: bool = False
) -> Decimal:
    text = str(value or "").strip().replace("\u00a0", "").replace(" ", "")
    if not text or text in {"-", "-,--"}:
        raise ValueError(f"DKB-CSV: {field} fehlt")
    is_percent = text.endswith("%")
    if allow_currency_or_percent:
        text = re.sub(r"(?:EUR|USD|GBP|CHF|€|\$|£|%)$", "", text, flags=re.I)
    normalized = (
        text
        if is_percent and "." in text and "," not in text
        else text.replace(".", "").replace(",", ".")
    )
    try:
        return Decimal(normalized)
    except InvalidOperation as exc:
        raise ValueError(f"DKB-CSV: {field} ist ungueltig: {text[:80]}") from exc


def _dkb_currency(*values: object) -> str:
    currencies: set[str] = set()
    for value in values:
        text = str(value or "").strip().upper().replace("\u00a0", " ")
        if "€" in text or re.search(r"(?:^|\W)EUR(?:$|\W)", text):
            currencies.add("EUR")
        if "$" in text or re.search(r"(?:^|\W)USD(?:$|\W)", text):
            currencies.add("USD")
        if "£" in text or re.search(r"(?:^|\W)GBP(?:$|\W)", text):
            currencies.add("GBP")
        if re.search(r"(?:^|\W)CHF(?:$|\W)", text):
            currencies.add("CHF")
    if len(currencies) != 1:
        raise ValueError("DKB-CSV: Waehrung fehlt oder ist widerspruechlich")
    return next(iter(currencies))


def _dkb_optional_decimal(value: object, *, field: str) -> str:
    text = str(value or "").strip().replace("\u00a0", "").replace(" ", "")
    if not text or text in {"-", "-,--"}:
        return ""
    return str(
        _dkb_decimal(
            value,
            field=field,
            allow_currency_or_percent=True,
        )
    )


def parse_dkb_portfolio_csv(data: bytes) -> dict[str, Any]:
    """Parse one strict DKB depot snapshot exported with German column names."""
    if len(data) > MAX_IMPORT_BYTES:
        raise ValueError("Portfolio-CSV ist groesser als 25 MB")
    if b"\x00" in data:
        raise ValueError("Portfolio-CSV enthaelt ungueltige Nullbytes")
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError("DKB-CSV muss UTF-8-kodiert sein") from exc
    reader = csv.DictReader(io.StringIO(text, newline=""), delimiter=";")
    headers = tuple(str(value or "").strip() for value in (reader.fieldnames or []))
    missing = [name for name in DKB_CSV_REQUIRED_HEADERS if name not in headers]
    if missing:
        raise ValueError("DKB-CSV: Pflichtspalten fehlen: " + ", ".join(missing))

    instruments: dict[str, dict[str, str]] = {}
    positions: dict[tuple[str, str], dict[str, str]] = {}
    snapshot_date = ""
    rows = 0
    for row_number, row in enumerate(reader, start=2):
        if not any(str(value or "").strip() for value in row.values()):
            continue
        rows += 1
        if rows > 10_000:
            raise ValueError("DKB-CSV enthaelt mehr als 10000 Depotpositionen")
        raw_date = str(row.get("Datum der Erstellung") or "").strip()
        try:
            parsed_date = datetime.strptime(raw_date, "%d.%m.%Y").date().isoformat()
        except ValueError as exc:
            raise ValueError(
                f"DKB-CSV Zeile {row_number}: ungueltiges Erstellungsdatum"
            ) from exc
        if snapshot_date and parsed_date != snapshot_date:
            raise ValueError("DKB-CSV enthaelt mehrere unterschiedliche Stichtage")
        snapshot_date = parsed_date

        account = str(row.get("Depotnummer") or "").strip()
        if not account or len(account) > 100:
            raise ValueError(f"DKB-CSV Zeile {row_number}: Depotnummer fehlt oder ist zu lang")
        isin = str(row.get("ISIN") or "").strip().upper()
        if not _valid_isin(isin):
            raise ValueError(f"DKB-CSV Zeile {row_number}: ISIN ist ungueltig")
        name = " ".join(str(row.get("Wertpapierbezeichnung") or "").split())
        if not name or len(name) > 300:
            raise ValueError(f"DKB-CSV Zeile {row_number}: Wertpapierbezeichnung fehlt")
        wkn = str(row.get("WKN") or "").strip().upper()
        if len(wkn) > 32:
            raise ValueError(f"DKB-CSV Zeile {row_number}: WKN ist zu lang")
        currency = _dkb_currency(row.get("Einstiegskurs"), row.get("Bewertungskurs"))
        shares = _dkb_decimal(row.get("Stückzahl"), field=f"Stueckzahl in Zeile {row_number}")
        if shares < 0:
            raise ValueError(f"DKB-CSV Zeile {row_number}: negative Stueckzahl ist ungueltig")
        entry_price = _dkb_decimal(
            row.get("Einstiegskurs"),
            field=f"Einstiegskurs in Zeile {row_number}",
            allow_currency_or_percent=True,
        )
        valuation_price = _dkb_decimal(
            row.get("Bewertungskurs"),
            field=f"Bewertungskurs in Zeile {row_number}",
            allow_currency_or_percent=True,
        )
        if entry_price < 0 or valuation_price < 0:
            raise ValueError(
                f"DKB-CSV Zeile {row_number}: negative Kurswerte sind ungueltig"
            )
        absolute_gain = _dkb_optional_decimal(
            row.get("Absoluter Gewinn"),
            field=f"Absoluter Gewinn in Zeile {row_number}",
        )
        relative_gain = _dkb_optional_decimal(
            row.get("Relativer Gewinn"),
            field=f"Relativer Gewinn in Zeile {row_number}",
        )
        asset_class = " ".join(str(row.get("Assetklasse") or "").split())
        if not asset_class or len(asset_class) > 100:
            raise ValueError(f"DKB-CSV Zeile {row_number}: Assetklasse fehlt oder ist zu lang")

        instrument = {
            "isin": isin,
            "name": name,
            "wkn": wkn,
            "symbol": "",
            "mic": "",
            "currency": currency,
        }
        existing = instruments.get(isin)
        if existing and any(
            existing[key] != instrument[key] for key in ("name", "wkn", "currency")
        ):
            raise ValueError(f"DKB-CSV: widerspruechliche Stammdaten fuer ISIN {isin}")
        instruments[isin] = instrument
        key = (account, isin)
        if key in positions:
            raise ValueError(
                f"DKB-CSV: doppelte Position fuer Depot {account} und ISIN {isin}"
            )
        positions[key] = {
            "account": account,
            "isin": isin,
            "shares": str(shares),
            "entry_price": str(entry_price),
            "valuation_price": str(valuation_price),
            "absolute_gain": absolute_gain,
            "relative_gain_percent": relative_gain,
            "asset_class": asset_class,
            "snapshot_currency": currency,
        }

    if not rows:
        raise ValueError("DKB-CSV enthaelt keine Depotpositionen")
    return {
        "source_type": "dkb-depot-csv",
        "as_of": snapshot_date,
        "instruments": [instruments[key] for key in sorted(instruments)],
        "positions": [
            position
            for _, position in sorted(positions.items())
            if Decimal(position["shares"]) != 0
        ],
    }


class PortfolioService:
    def __init__(
        self,
        settings: PortfolioToolSettings,
        antivirus: HostAntivirus,
        *,
        quote_fetcher: QuoteFetcher | None = None,
        notifier: EventNotifier | None = None,
        now: Callable[[], datetime] = _now,
    ) -> None:
        self.settings = settings
        self.antivirus = antivirus
        self.store = PortfolioStore(settings.database)
        self._quote_fetcher = quote_fetcher
        self._notifier = notifier or _notify_openclaw
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
            raise PermissionError(
                f"Portfolio-Import fail-closed gesperrt: ClamAV-Status {scan.status}"
            )
        data = path.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        parsed = parser(data)
        if expected_as_of and str(parsed.get("as_of") or "") != expected_as_of:
            raise ValueError(
                "Erwarteter Portfolio-Stichtag stimmt nicht mit dem Dateiinhalt ueberein"
            )
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
                        currency=CASE WHEN excluded.currency!='' THEN excluded.currency ELSE instruments.currency END,
                        updated_at=excluded.updated_at
                    """,
                    (
                        item["isin"], item["name"], item["wkn"], item["symbol"],
                        item["mic"], item["currency"], now,
                    ),
                )
            cursor = self.store.connection.execute(
                """
                INSERT INTO imports(
                    sha256,source_type,source_name,imported_at,as_of,instruments,positions
                ) VALUES(?,?,?,?,?,?,?)
                """,
                (
                    digest, parsed["source_type"], display_name, now, parsed["as_of"],
                    len(parsed["instruments"]), len(parsed["positions"]),
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
                   i.currency AS quote_currency,
                   i.name,i.wkn,i.symbol,i.mic,i.mapping_confirmed
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

    def watchlist_add(
        self, *, isin: str, name: str, symbol: str, mic: str, currency: str
    ) -> dict[str, Any]:
        self._require_enabled()
        isin = isin.strip().upper()
        symbol = symbol.strip().upper()
        mic = mic.strip().upper()
        currency = currency.strip().upper()
        if not _valid_isin(isin):
            raise ValueError("ISIN ist ungueltig")
        if not symbol or len(symbol) > 40:
            raise ValueError("Boersensymbol fehlt oder ist zu lang")
        if not MIC_RE.fullmatch(mic):
            raise ValueError("MIC muss aus genau vier Buchstaben/Ziffern bestehen")
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
            "ok": True, "isin": isin, "name": name.strip() or isin,
            "symbol": symbol, "mic": mic, "currency": currency,
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
        held = {
            row["isin"]
            for row in self.holdings()["positions"]
            if _decimal(row["shares"]) != 0
        }
        watched = {
            row["isin"]
            for row in self.store.connection.execute(
                "SELECT isin FROM watchlist WHERE enabled=1"
            ).fetchall()
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

    def _fetch_quotes(
        self, items: list[dict[str, Any]]
    ) -> tuple[dict[str, Quote], dict[str, str]]:
        if self._quote_fetcher is not None:
            quotes: dict[str, Quote] = {}
            errors: dict[str, str] = {}
            for item in items:
                try:
                    quotes[str(item["isin"])] = self._quote_fetcher(item)
                except Exception as exc:
                    errors[str(item["isin"])] = str(exc)[:500]
            return quotes, errors
        if self.settings.provider != "eodhd":
            raise RuntimeError("Kein EODHD-Marktdatenanbieter konfiguriert")
        api_key = os.environ.get(self.settings.api_key_env, "").strip()
        if not api_key:
            raise RuntimeError(
                f"API-Schluessel fehlt in Umgebungsvariable {self.settings.api_key_env}"
            )
        client = EodhdClient(api_key, timeout=self.settings.request_timeout_seconds)
        quotes: dict[str, Quote] = {}
        errors: dict[str, str] = {}
        valid_items: list[dict[str, Any]] = []
        for item in items:
            try:
                client.ticker(item)
                valid_items.append(item)
            except ValueError as exc:
                errors[str(item["isin"])] = str(exc)[:500]
        for offset in range(0, len(valid_items), EODHD_BATCH_LIMIT):
            chunk = valid_items[offset : offset + EODHD_BATCH_LIMIT]
            try:
                quotes.update(client.fetch_many(chunk))
            except Exception as exc:
                safe = str(exc).replace(api_key, "<redacted>")[:500]
                for item in chunk:
                    errors[str(item["isin"])] = safe
        return quotes, errors

    def refresh_quotes(self, *, force: bool = False) -> dict[str, Any]:
        self._require_enabled()
        started_dt = self._now()
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
                "next_due_in_seconds": max(
                    0, int(due_after - (started_dt - last_finished).total_seconds())
                ),
            }
        started = time.perf_counter()
        targets = self._targets()[: self.settings.max_symbols]
        received = 0
        failures: list[dict[str, str]] = []
        warnings: list[dict[str, str]] = []
        triggered_events: list[dict[str, Any]] = []
        held_missing = 0
        eligible: list[dict[str, Any]] = []
        for item in targets:
            if not item["mapping_confirmed"] or not item["symbol"] or not item["mic"]:
                failures.append({"isin": item["isin"], "error": "Symbol/MIC-Zuordnung nicht bestaetigt"})
                held_missing += int(item["held"])
            else:
                eligible.append(item)
        quotes: dict[str, Quote] = {}
        fetch_errors: dict[str, str] = {}
        if eligible:
            try:
                quotes, fetch_errors = self._fetch_quotes(eligible)
            except Exception as exc:
                error = str(exc)[:500]
                fetch_errors = {str(item["isin"]): error for item in eligible}
        for item in eligible:
            isin = str(item["isin"])
            if isin in fetch_errors:
                failures.append({"isin": isin, "error": fetch_errors[isin]})
                held_missing += int(item["held"])
                continue
            quote = quotes.get(isin)
            if quote is None:
                failures.append({
                    "isin": isin,
                    "error": f"EODHD lieferte keinen Kurs fuer {item['symbol']}/{item['mic']}",
                })
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
                    (quote.market_open if quote.market_open is not None else self._instrument_market_open(item, received_at))
                    and source_age > self.settings.stale_critical_minutes * 60
                ):
                    raise ValueError("Marktdatenquelle lieferte einen kritisch veralteten Kurs")
                if (
                    (quote.market_open if quote.market_open is not None else self._instrument_market_open(item, received_at))
                    and source_age > self.settings.stale_warning_minutes * 60
                ):
                    warnings.append(
                        {"isin": item["isin"], "warning": "Marktdatenquelle lieferte einen veralteten Kurs"}
                    )
                delay = max(0, int((received_at - observed).total_seconds()))
                with self.store.connection:
                    self.store.connection.execute(
                        """
                        INSERT OR IGNORE INTO quotes(
                            isin,provider,price,currency,observed_at,received_at,delay_seconds,
                            open,high,low,volume,market_open
                        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
                        """,
                        (
                            item["isin"], quote.provider, str(quote.price),
                            quote.currency or item["currency"], _iso(observed),
                            _iso(received_at), delay,
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
        if held_missing:
            status = "failed"
        elif failures or warnings:
            status = "degraded"
        latency = round((time.perf_counter() - started) * 1000.0, 2)
        error = "; ".join(f"{item['isin']}: {item['error']}" for item in failures)[:4000]
        with self.store.connection:
            cursor = self.store.connection.execute(
                """
                INSERT INTO quote_runs(
                    started_at,finished_at,status,expected,received,held_missing,latency_ms,error
                ) VALUES(?,?,?,?,?,?,?,?)
                """,
                (
                    _iso(started_dt), _iso(self._now()), status, len(targets),
                    received, held_missing, latency, error,
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
            return local.weekday() < 5 and clock_time(9, 0) <= local.time().replace(tzinfo=None) <= clock_time(17, 30)
        if mic in {"XNAS", "XNGS", "XNYS"}:
            local = now.astimezone(ZoneInfo("America/New_York"))
            return local.weekday() < 5 and clock_time(9, 30) <= local.time().replace(tzinfo=None) <= clock_time(16, 0)
        return self._market_open(now)

    def health(self) -> dict[str, Any]:
        enabled = self.settings.enabled
        if not enabled:
            return {
                "enabled": False, "ok": True, "state": "disabled",
                "coverage": None, "required": 0, "fresh": 0,
            }
        targets = self._targets()
        now = self._now()
        market_open = any(self._instrument_market_open(item, now) for item in targets)
        held_total = sum(int(item["held"]) for item in targets)
        held_fresh = 0
        held_stale = 0
        watch_missing = 0
        held_missing = 0
        details: list[dict[str, Any]] = []
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
            stale = bool(
                not mapping_ok or (effective_open and (age is None or age > warning_seconds))
            )
            critical = bool(
                not mapping_ok or (effective_open and (age is None or age > critical_seconds))
            )
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
                    "isin": item["isin"], "held": bool(item["held"]),
                    "observed_at": row["observed_at"] if row else None,
                    "age_seconds": age, "stale": stale, "critical": critical,
                    "mapping_confirmed": mapping_ok,
                    "mapping_error": mapping_error,
                    "provider_market_open": provider_open,
                    "quote_provider": row["provider"] if row else None,
                    "provider_symbol": provider_symbol,
                }
            )
        last_run = self.store.connection.execute(
            "SELECT * FROM quote_runs ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if held_missing:
            state = "failed"
        elif held_stale or watch_missing or (last_run and last_run["status"] != "success"):
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
            "last_run": dict(last_run) if last_run else None,
            "instruments": details,
            "database_integrity": self.store.integrity(),
        }

    def status(self) -> dict[str, Any]:
        health = self.health()
        return {
            "ok": bool(health["ok"]),
            "enabled": self.settings.enabled,
            "database": str(self.store.path),
            "import_root": str(self.settings.import_root),
            "nextcloud_folder": self.settings.nextcloud_folder,
            "provider": self.settings.provider,
            "interval_minutes": self.settings.interval_minutes,
            "stale_warning_minutes": self.settings.stale_warning_minutes,
            "stale_critical_minutes": self.settings.stale_critical_minutes,
            "health": health,
            "holdings": self.holdings(),
            "watchlist": self.watchlist(),
        }

    def doctor(self) -> dict[str, Any]:
        health = self.health()
        key_present = bool(os.environ.get(self.settings.api_key_env, "").strip())
        configuration_ok = (
            not self.settings.enabled
            or (
                self.settings.provider == "eodhd"
                and key_present
                and self.settings.import_root.is_dir()
            )
        )
        return {
            "ok": configuration_ok and self.store.integrity() == "ok" and bool(health["ok"]),
            "configuration_ok": configuration_ok,
            "api_key_present": key_present,
            "api_key_env": self.settings.api_key_env,
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
        """Return one stored quote without turning a price lookup into analysis."""
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
        return {
            "ok": not bool(health_item["critical"]),
            "isin": isin,
            "name": str(instrument["name"] if instrument else isin),
            "symbol": str(instrument["symbol"] if instrument else ""),
            "mic": str(instrument["mic"] if instrument else ""),
            "provider_symbol": health_item.get("provider_symbol"),
            "price": quote["close"],
            "currency": quote["currency"],
            "observed_at": quote["observed_at"],
            "received_at": quote["received_at"],
            "provider": quote["provider"],
            "market_open": quote["market_open"],
            "age_seconds": health_item["age_seconds"],
            "stale": bool(health_item["stale"]),
            "critical": bool(health_item["critical"]),
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
                "ok": False, "decision": "abstain", "isin": isin,
                "reason": "Keine Kursdaten vorhanden", "series": [],
            }
        if health_item["critical"]:
            return {
                "ok": False, "decision": "abstain", "isin": isin,
                "reason": "Pflichtkurs ist kritisch veraltet", "as_of": series[-1]["observed_at"],
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
            "indicators": {
                "trend": trend, "sma20": sma20, "sma50": sma50,
                "sma200": sma200, "rsi14": rsi14,
            },
            "series": series,
            "disclaimer": "Informationssystem ohne Orderausfuehrung; keine individuelle Anlageberatung.",
        }

    def alert_add(
        self, *, isin: str, direction: str, threshold: Decimal,
        currency: str, hysteresis_bps: int = 25, cooldown_minutes: int = 60,
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
                    rule_id, isin, direction, str(threshold), currency,
                    max(0, min(hysteresis_bps, 5000)),
                    max(0, min(cooldown_minutes, 10080)), now, now,
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
            state = "crossed" if crossed else "clear"
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
                            str(uuid.uuid4()), rule["id"], isin, "triggered",
                            str(price), quote.observed_at, _iso(now),
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
