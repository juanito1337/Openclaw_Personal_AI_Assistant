from __future__ import annotations

import csv
import io
import re
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

MAX_IMPORT_BYTES = 25_000_000
ISIN_RE = re.compile(r"^[A-Z]{2}[A-Z0-9]{9}[0-9]$")
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


def _iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


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
    digits = "".join(str(ord(character) - 55) if character.isalpha() else character for character in value)
    total = 0
    double = False
    for character in reversed(digits):
        number = int(character) * (2 if double else 1)
        total += number // 10 + number % 10
        double = not double
    return total % 10 == 0


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
            "symbol": (_child_text(node, "tickerSymbol", "ticker", "symbol") or node.attrib.get("ticker", ""))
            .strip()
            .upper(),
            "mic": (_child_text(node, "mic", "market") or node.attrib.get("mic", "")).strip().upper(),
            "currency": (_child_text(node, "currencyCode", "currency") or node.attrib.get("currency", ""))
            .strip()
            .upper(),
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
                raise ValueError(f"Aktiensplit fuer {isin} kann nicht sicher ausgewertet werden")
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
            account = (
                _child_text(node, "account", "portfolio") or node.attrib.get("account", "Depot")
            ).strip()
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
        shares_text = (str(shares_node.text or "").strip() if shares_node is not None else "") or (
            shares_node.attrib.get("value", "") if shares_node is not None else ""
        )
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
        resolved_security = by_uuid.get(reference) or by_id.get(reference) or by_isin.get(reference.upper())
        if resolved_security is None:
            match = PP_INDEX_RE.search(reference)
            if match and 0 < int(match.group(1)) <= len(securities):
                resolved_security = securities[int(match.group(1)) - 1]
            elif reference.endswith("/securities/security") and securities:
                resolved_security = securities[0]
        if resolved_security is None:
            isin = (_child_text(node, "isin") or node.attrib.get("isin", "")).strip().upper()
            resolved_security = by_isin.get(isin)
        if resolved_security is None:
            continue
        tx_date = _child_text(node, "date", "datetime")[:10]
        splits = splits_by_isin.get(resolved_security["isin"], [])
        if splits and not tx_date:
            raise ValueError(
                f"Transaktionsdatum fuer Aktiensplit-Berechnung bei {resolved_security['isin']} fehlt"
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
        key = (account, resolved_security["isin"])
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


def _dkb_decimal(value: object, *, field: str, allow_currency_or_percent: bool = False) -> Decimal:
    text = str(value or "").strip().replace("\u00a0", "").replace(" ", "")
    if not text or text in {"-", "-,--"}:
        raise ValueError(f"DKB-CSV: {field} fehlt")
    is_percent = text.endswith("%")
    if allow_currency_or_percent:
        text = re.sub(r"(?:EUR|USD|GBP|CHF|€|\$|£|%)$", "", text, flags=re.I)
    normalized = (
        text if is_percent and "." in text and "," not in text else text.replace(".", "").replace(",", ".")
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
            raise ValueError(f"DKB-CSV Zeile {row_number}: ungueltiges Erstellungsdatum") from exc
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
            raise ValueError(f"DKB-CSV Zeile {row_number}: negative Kurswerte sind ungueltig")
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
        if existing and any(existing[key] != instrument[key] for key in ("name", "wkn", "currency")):
            raise ValueError(f"DKB-CSV: widerspruechliche Stammdaten fuer ISIN {isin}")
        instruments[isin] = instrument
        key = (account, isin)
        if key in positions:
            raise ValueError(f"DKB-CSV: doppelte Position fuer Depot {account} und ISIN {isin}")
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
            position for _, position in sorted(positions.items()) if Decimal(position["shares"]) != 0
        ],
    }
