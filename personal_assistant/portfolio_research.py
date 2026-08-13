"""Provider-bounded, deterministic equity research for the portfolio tool."""

from __future__ import annotations

import json
import math
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Protocol

RESEARCH_MODEL_VERSION = "2026-08-13.1"
RESEARCH_PROVIDER = "eodhd"
RESEARCH_TICKER_RE = re.compile(r"^[A-Z0-9][A-Z0-9.\-]{0,39}\.[A-Z0-9]{1,20}$")
RESEARCH_STRATEGIES = (
    "balanced",
    "quality-value",
    "quality-growth",
    "dividend-quality",
)
STRATEGY_WEIGHTS: dict[str, dict[str, int]] = {
    "balanced": {
        "quality": 25,
        "value": 20,
        "growth": 20,
        "momentum": 15,
        "risk": 20,
    },
    "quality-value": {
        "quality": 30,
        "value": 35,
        "growth": 10,
        "momentum": 10,
        "risk": 15,
    },
    "quality-growth": {
        "quality": 30,
        "value": 10,
        "growth": 30,
        "momentum": 15,
        "risk": 15,
    },
    "dividend-quality": {
        "quality": 30,
        "value": 15,
        "growth": 10,
        "momentum": 10,
        "risk": 15,
        "dividend": 20,
    },
}


class ResearchProvider(Protocol):
    def screen(
        self,
        *,
        strategy: str,
        exchange: str = "",
        sector: str = "",
        limit: int = 15,
    ) -> list[dict[str, Any]]: ...

    def fundamentals(self, ticker: str) -> dict[str, Any]: ...

    def history(self, ticker: str, *, from_date: date) -> list[dict[str, Any]]: ...


def _clean_text(value: object, *, limit: int = 200) -> str:
    text = " ".join(str(value or "").split())
    return "".join(character for character in text if ord(character) >= 32)[:limit]


def _number(value: object) -> float | None:
    if value in {None, "", "None", "null"}:
        return None
    try:
        result = float(Decimal(str(value)))
    except (InvalidOperation, ValueError, TypeError, OverflowError):
        return None
    return result if math.isfinite(result) else None


def _rounded(value: float | None, digits: int = 4) -> float | None:
    return round(value, digits) if value is not None and math.isfinite(value) else None


def _score_high(value: float | None, thresholds: tuple[tuple[float, int], ...]) -> int | None:
    if value is None:
        return None
    for minimum, score in thresholds:
        if value >= minimum:
            return score
    return 0


def _score_low_positive(
    value: float | None,
    thresholds: tuple[tuple[float, int], ...],
) -> int | None:
    if value is None or value <= 0:
        return None
    for maximum, score in thresholds:
        if value <= maximum:
            return score
    return 10


def _score_range(value: float | None, ranges: tuple[tuple[float, float, int], ...]) -> int | None:
    if value is None:
        return None
    for minimum, maximum, score in ranges:
        if minimum <= value <= maximum:
            return score
    return 10


def _latest_statement(section: object) -> dict[str, Any]:
    if not isinstance(section, dict):
        return {}
    yearly = section.get("yearly")
    if not isinstance(yearly, dict):
        return {}
    rows = [item for item in yearly.values() if isinstance(item, dict)]
    return max(rows, key=lambda item: str(item.get("date") or ""), default={})


def _safe_div(numerator: float | None, denominator: float | None) -> float | None:
    if numerator is None or denominator in {None, 0.0}:
        return None
    result = numerator / denominator
    return result if math.isfinite(result) else None


def _returns(values: list[float]) -> list[float]:
    return [
        values[index] / values[index - 1] - 1.0 for index in range(1, len(values)) if values[index - 1] > 0
    ]


def _annualized_volatility(values: list[float]) -> float | None:
    changes = _returns(values)
    if len(changes) < 20:
        return None
    average = sum(changes) / len(changes)
    variance = sum((value - average) ** 2 for value in changes) / (len(changes) - 1)
    return math.sqrt(variance) * math.sqrt(252.0)


def _max_drawdown(values: list[float]) -> float | None:
    if not values:
        return None
    peak = values[0]
    worst = 0.0
    for value in values:
        peak = max(peak, value)
        if peak > 0:
            worst = min(worst, value / peak - 1.0)
    return worst


def _rsi(values: list[float], period: int = 14) -> float | None:
    if len(values) <= period:
        return None
    changes = [values[index] - values[index - 1] for index in range(1, len(values))]
    recent = changes[-period:]
    gain = sum(max(change, 0.0) for change in recent) / period
    loss = sum(max(-change, 0.0) for change in recent) / period
    if loss == 0:
        return 100.0
    return 100.0 - 100.0 / (1.0 + gain / loss)


class EodhdResearchClient:
    """Minimal allowlisted adapter for EODHD research endpoints."""

    base_url = "https://eodhd.com/api"

    def __init__(
        self,
        api_key: str,
        *,
        timeout: int = 20,
        urlopen: Any = urllib.request.urlopen,
    ) -> None:
        self.api_key = api_key
        self.timeout = timeout
        self._urlopen = urlopen

    @staticmethod
    def validate_ticker(value: str) -> str:
        ticker = value.strip().upper()
        if not RESEARCH_TICKER_RE.fullmatch(ticker) or ".." in ticker:
            raise ValueError("EODHD-Research-Ticker ist ungueltig")
        return ticker

    def _provider_error(self, raw: bytes, fallback: str) -> RuntimeError:
        detail = ""
        try:
            payload = json.loads(raw.decode("utf-8"))
            if isinstance(payload, dict):
                detail = str(payload.get("message") or payload.get("error") or "")
        except (UnicodeDecodeError, json.JSONDecodeError):
            detail = ""
        safe = (detail or fallback).replace(self.api_key, "<redacted>")[:300]
        return RuntimeError(f"EODHD-Researchfehler: {safe}")

    def _get_json(
        self,
        path: str,
        params: dict[str, str],
        *,
        max_bytes: int,
    ) -> Any:
        query = urllib.parse.urlencode({**params, "api_token": self.api_key, "fmt": "json"})
        request = urllib.request.Request(
            f"{self.base_url}/{path}?{query}",
            headers={"User-Agent": "OpenClaw-Portfolio-Research/1"},
        )
        try:
            with self._urlopen(request, timeout=self.timeout) as response:
                raw = response.read(max_bytes + 1)
        except urllib.error.HTTPError as exc:
            try:
                raw = exc.read(64_000)
            except OSError:
                raw = b""
            raise self._provider_error(raw, f"HTTP {exc.code}") from None
        except urllib.error.URLError as exc:
            detail = str(getattr(exc, "reason", "Verbindung fehlgeschlagen"))
            raise self._provider_error(b"", detail) from None
        except (TimeoutError, OSError) as exc:
            raise self._provider_error(b"", type(exc).__name__) from None
        if len(raw) > max_bytes:
            raise RuntimeError("EODHD-Researchantwort ueberschreitet das Groessenlimit")
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RuntimeError("EODHD-Research lieferte kein gueltiges JSON") from exc
        if isinstance(payload, dict) and (
            isinstance(payload.get("code"), int) or str(payload.get("status") or "").casefold() == "error"
        ):
            raise self._provider_error(raw, "Anbieterfehler")
        return payload

    def screen(
        self,
        *,
        strategy: str,
        exchange: str = "",
        sector: str = "",
        limit: int = 15,
    ) -> list[dict[str, Any]]:
        if strategy not in RESEARCH_STRATEGIES:
            raise ValueError("Unbekanntes Research-Modell")
        filters: list[list[object]] = [
            ["market_capitalization", ">=", 1_000_000_000],
            ["earnings_share", ">", 0],
            ["avgvol_200d", ">", 100_000],
        ]
        if strategy == "dividend-quality":
            filters.append(["dividend_yield", ">", 0])
        exchange = exchange.strip().upper()
        if exchange:
            if not re.fullmatch(r"[A-Z0-9.\-]{1,20}", exchange):
                raise ValueError("EODHD-Screener-Boerse ist ungueltig")
            filters.append(["exchange", "=", exchange])
        sector = _clean_text(sector, limit=80)
        if sector:
            filters.append(["sector", "match", sector])
        payload = self._get_json(
            "screener",
            {
                "sort": "market_capitalization.desc",
                "filters": json.dumps(filters, separators=(",", ":")),
                "limit": str(max(1, min(int(limit), 30))),
                "offset": "0",
            },
            max_bytes=2_000_000,
        )
        rows = payload.get("data") if isinstance(payload, dict) else payload
        if not isinstance(rows, list):
            raise RuntimeError("EODHD-Screener lieferte keine Kandidatenliste")
        result: list[dict[str, Any]] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            code = _clean_text(row.get("code"), limit=40).upper()
            provider_exchange = _clean_text(row.get("exchange"), limit=20).upper()
            ticker = f"{code}.{provider_exchange}"
            if not RESEARCH_TICKER_RE.fullmatch(ticker) or ".." in ticker:
                continue
            result.append(
                {
                    "ticker": ticker,
                    "code": code,
                    "exchange": provider_exchange,
                    "name": _clean_text(row.get("name")),
                    "sector": _clean_text(row.get("sector"), limit=100),
                    "industry": _clean_text(row.get("industry"), limit=120),
                    "market_capitalization": _number(row.get("market_capitalization")),
                    "earnings_share": _number(row.get("earnings_share")),
                    "dividend_yield": _number(row.get("dividend_yield")),
                    "refund_1d_percent": _number(row.get("refund_1d_p")),
                    "refund_5d_percent": _number(row.get("refund_5d_p")),
                    "avg_volume_200d": _number(row.get("avgvol_200d")),
                    "adjusted_close": _number(row.get("adjusted_close")),
                }
            )
        return result

    def fundamentals(self, ticker: str) -> dict[str, Any]:
        ticker = self.validate_ticker(ticker)
        payload = self._get_json(
            f"v1.1/fundamentals/{urllib.parse.quote(ticker, safe='')}",
            {"filter": "General,Highlights,Valuation,Technicals,Financials"},
            max_bytes=10_000_000,
        )
        if not isinstance(payload, dict):
            raise RuntimeError("EODHD-Fundamentaldaten sind kein JSON-Objekt")
        return payload

    def history(self, ticker: str, *, from_date: date) -> list[dict[str, Any]]:
        ticker = self.validate_ticker(ticker)
        payload = self._get_json(
            f"eod/{urllib.parse.quote(ticker, safe='')}",
            {"order": "a", "from": from_date.isoformat()},
            max_bytes=4_000_000,
        )
        if not isinstance(payload, list):
            raise RuntimeError("EODHD-EOD-Historie lieferte keine Kursliste")
        return [item for item in payload if isinstance(item, dict)]


@dataclass(frozen=True, slots=True)
class Metric:
    key: str
    pillar: str
    value: float | None
    unit: str
    score: int | None
    rationale: str

    def render(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "pillar": self.pillar,
            "value": _rounded(self.value),
            "unit": self.unit,
            "score": self.score,
            "rationale": self.rationale,
            "provider": RESEARCH_PROVIDER,
        }


def _metric_set(values: dict[str, float | None]) -> list[Metric]:
    return [
        Metric(
            "return_on_equity",
            "quality",
            values["roe"],
            "ratio",
            _score_high(values["roe"], ((0.20, 100), (0.15, 80), (0.10, 60), (0.05, 40), (0.0, 20))),
            (
                "Hoehere nachhaltige Eigenkapitalrendite wird positiv bewertet; "
                "negatives Eigenkapital bleibt unbewertbar."
            ),
        ),
        Metric(
            "return_on_assets",
            "quality",
            values["roa"],
            "ratio",
            _score_high(values["roa"], ((0.10, 100), (0.07, 80), (0.04, 60), (0.02, 40), (0.0, 20))),
            "Kapitalrentabilitaet dient als branchenabhaengiger Qualitaetsindikator.",
        ),
        Metric(
            "operating_margin",
            "quality",
            values["operating_margin"],
            "ratio",
            _score_high(
                values["operating_margin"], ((0.25, 100), (0.15, 80), (0.10, 60), (0.05, 40), (0.0, 20))
            ),
            "Eine robuste operative Marge schafft Puffer gegen Ergebnisrueckgaenge.",
        ),
        Metric(
            "free_cash_flow_margin",
            "quality",
            values["fcf_margin"],
            "ratio",
            _score_high(values["fcf_margin"], ((0.15, 100), (0.10, 80), (0.05, 60), (0.0, 35), (-1.0, 0))),
            "Freier Cashflow relativ zum Umsatz belegt Ergebnisqualitaet.",
        ),
        Metric(
            "trailing_pe",
            "value",
            values["trailing_pe"],
            "multiple",
            _score_low_positive(values["trailing_pe"], ((12, 100), (18, 85), (25, 65), (35, 40), (50, 20))),
            (
                "Niedrigere positive Gewinnmultiplikatoren erhalten mehr Punkte; "
                "Verlustunternehmen bleiben unbewertbar."
            ),
        ),
        Metric(
            "forward_pe",
            "value",
            values["forward_pe"],
            "multiple",
            _score_low_positive(values["forward_pe"], ((12, 100), (18, 85), (25, 65), (35, 40), (50, 20))),
            "Das erwartete KGV ergaenzt die historische Bewertung, bleibt aber schaetzungsabhaengig.",
        ),
        Metric(
            "ev_to_ebitda",
            "value",
            values["ev_ebitda"],
            "multiple",
            _score_low_positive(values["ev_ebitda"], ((8, 100), (12, 80), (16, 60), (22, 35), (30, 20))),
            "Enterprise Value zu EBITDA ergaenzt das KGV um die Kapitalstruktur.",
        ),
        Metric(
            "peg_ratio",
            "value",
            values["peg"],
            "multiple",
            _score_low_positive(values["peg"], ((1.0, 100), (1.5, 80), (2.0, 60), (3.0, 35), (5.0, 20))),
            (
                "PEG verbindet Bewertung mit erwarteter Entwicklung und ist nur "
                "bei positiven Werten aussagefaehig."
            ),
        ),
        Metric(
            "revenue_growth_yoy",
            "growth",
            values["revenue_growth"],
            "ratio",
            _score_high(
                values["revenue_growth"], ((0.20, 100), (0.10, 80), (0.05, 60), (0.0, 40), (-0.10, 15))
            ),
            "Umsatzwachstum zeigt operative Expansion, ohne allein Profitabilitaet zu belegen.",
        ),
        Metric(
            "earnings_growth_yoy",
            "growth",
            values["earnings_growth"],
            "ratio",
            _score_high(
                values["earnings_growth"], ((0.25, 100), (0.15, 80), (0.05, 60), (0.0, 40), (-0.10, 15))
            ),
            "Gewinnwachstum wird getrennt vom Umsatzwachstum bewertet.",
        ),
        Metric(
            "return_12m",
            "momentum",
            values["return_12m"],
            "ratio",
            _score_high(values["return_12m"], ((0.30, 100), (0.15, 80), (0.05, 60), (0.0, 45), (-0.15, 20))),
            "Zwölfmonatsrendite misst bestaetigten Preistrend, nicht den inneren Wert.",
        ),
        Metric(
            "price_to_sma200",
            "momentum",
            values["price_to_sma200"],
            "ratio",
            _score_range(
                values["price_to_sma200"],
                ((1.0, 1.25, 100), (0.9, 1.0, 70), (1.25, 1.5, 55), (0.75, 0.9, 35)),
            ),
            (
                "Der Abstand zum 200-Tage-Mittel ordnet den langfristigen Trend ein; "
                "extreme Abstaende werden nicht belohnt."
            ),
        ),
        Metric(
            "rsi14",
            "momentum",
            values["rsi14"],
            "index",
            _score_range(values["rsi14"], ((40, 65, 100), (30, 70, 75), (25, 75, 45))),
            "RSI zwischen 40 und 65 gilt im Modell als konstruktiv; Extremwerte reduzieren die Bewertung.",
        ),
        Metric(
            "beta",
            "risk",
            values["beta"],
            "ratio",
            _score_range(values["beta"], ((0.0, 0.8, 100), (0.8, 1.1, 80), (1.1, 1.4, 55), (1.4, 1.8, 30))),
            "Niedrigere Marktsensitivitaet wird im Risikopfeiler bevorzugt.",
        ),
        Metric(
            "annualized_volatility",
            "risk",
            values["volatility"],
            "ratio",
            _score_range(
                values["volatility"], ((0.0, 0.20, 100), (0.20, 0.30, 75), (0.30, 0.45, 45), (0.45, 0.60, 20))
            ),
            "Annualisierte Schwankung wird aus taeglichen EOD-Renditen berechnet.",
        ),
        Metric(
            "max_drawdown",
            "risk",
            values["max_drawdown"],
            "ratio",
            _score_range(
                values["max_drawdown"],
                ((-0.15, 0.0, 100), (-0.25, -0.15, 75), (-0.40, -0.25, 40), (-0.60, -0.40, 15)),
            ),
            "Der groesste Rueckgang vom vorherigen Hoch bildet historischen Verluststress ab.",
        ),
        Metric(
            "debt_to_equity",
            "risk",
            values["debt_to_equity"],
            "ratio",
            _score_range(
                values["debt_to_equity"], ((0.0, 0.5, 100), (0.5, 1.0, 75), (1.0, 2.0, 40), (2.0, 3.0, 20))
            ),
            "Verschuldung relativ zum positiven Eigenkapital bewertet den Bilanzhebel.",
        ),
        Metric(
            "dividend_yield",
            "dividend",
            values["dividend_yield"],
            "ratio",
            _score_range(values["dividend_yield"], ((0.02, 0.05, 100), (0.01, 0.07, 70), (0.0, 0.10, 40))),
            "Eine moderate Dividendenrendite wird bevorzugt; sehr hohe Renditen koennen ein Warnsignal sein.",
        ),
        Metric(
            "payout_ratio",
            "dividend",
            values["payout_ratio"],
            "ratio",
            _score_range(values["payout_ratio"], ((0.20, 0.65, 100), (0.0, 0.80, 65), (0.80, 1.0, 30))),
            "Eine durch Gewinne gedeckte, nicht ueberdehnte Ausschüttung wird positiv bewertet.",
        ),
    ]


def research_models() -> dict[str, Any]:
    return {
        "ok": True,
        "model_version": RESEARCH_MODEL_VERSION,
        "strategies": [
            {
                "id": strategy,
                "weights": weights,
                "minimum_metric_coverage": 0.70,
                "minimum_history_points": 200,
            }
            for strategy, weights in STRATEGY_WEIGHTS.items()
        ],
        "verdicts": {
            "research-candidate": "Score mindestens 70 und vollstaendige Mindestdaten",
            "watch": "Score 55 bis unter 70",
            "not-prioritized": "Score unter 55",
            "abstain": "Mindestdaten oder Aktualitaet nicht ausreichend",
        },
        "disclaimer": "Erklaerbares Research-Modell; keine individuelle Anlageberatung oder Orderfreigabe.",
    }


def analyze_research_payload(
    fundamentals: dict[str, Any],
    history: list[dict[str, Any]],
    *,
    strategy: str,
    expected_ticker: str = "",
    expected_isin: str = "",
    now: datetime | None = None,
) -> dict[str, Any]:
    """Create a deterministic scorecard from provider facts only."""
    if strategy not in STRATEGY_WEIGHTS:
        raise ValueError("Unbekanntes Research-Modell")
    general = fundamentals.get("General")
    highlights = fundamentals.get("Highlights")
    valuation = fundamentals.get("Valuation")
    technicals = fundamentals.get("Technicals")
    financials = fundamentals.get("Financials")
    if not all(isinstance(item, dict) for item in (general, highlights, valuation, technicals)):
        raise ValueError("EODHD-Fundamentaldaten enthalten nicht alle Pflichtbereiche")
    assert isinstance(general, dict)
    assert isinstance(highlights, dict)
    assert isinstance(valuation, dict)
    assert isinstance(technicals, dict)
    financials = financials if isinstance(financials, dict) else {}
    ticker = _clean_text(general.get("PrimaryTicker"), limit=60).upper()
    isin = _clean_text(general.get("ISIN"), limit=20).upper()
    if not RESEARCH_TICKER_RE.fullmatch(ticker) or ".." in ticker:
        raise ValueError("EODHD-Fundamentaldaten enthalten keinen gueltigen Primaerticker")
    if expected_ticker and ticker != expected_ticker.strip().upper():
        raise ValueError("EODHD-Fundamentaldaten gehoeren nicht zum erwarteten Ticker")
    if expected_isin and isin != expected_isin.strip().upper():
        raise ValueError("EODHD-Fundamentaldaten gehoeren nicht zur erwarteten ISIN")

    normalized_history: list[dict[str, Any]] = []
    for row in history:
        raw_date = _clean_text(row.get("date"), limit=10)
        try:
            parsed_date = date.fromisoformat(raw_date)
        except ValueError:
            continue
        adjusted = _number(row.get("adjusted_close"))
        close = adjusted if adjusted is not None and adjusted > 0 else _number(row.get("close"))
        if close is None or close <= 0:
            continue
        normalized_history.append(
            {
                "date": parsed_date,
                "close": close,
                "volume": _number(row.get("volume")),
            }
        )
    normalized_history.sort(key=lambda item: item["date"])
    deduplicated = {item["date"]: item for item in normalized_history}
    normalized_history = [deduplicated[key] for key in sorted(deduplicated)]
    values = [float(item["close"]) for item in normalized_history]
    latest_date = normalized_history[-1]["date"] if normalized_history else None
    now = (now or datetime.now(UTC)).astimezone(UTC)
    history_age_days = (now.date() - latest_date).days if latest_date else None

    income = _latest_statement(financials.get("Income_Statement"))
    balance = _latest_statement(financials.get("Balance_Sheet"))
    cash_flow = _latest_statement(financials.get("Cash_Flow"))
    revenue = _number(income.get("totalRevenue")) or _number(highlights.get("RevenueTTM"))
    operating_cash = _number(cash_flow.get("totalCashFromOperatingActivities"))
    capital_expenditure = _number(cash_flow.get("capitalExpenditures"))
    free_cash_flow = None
    if operating_cash is not None and capital_expenditure is not None:
        free_cash_flow = (
            operating_cash + capital_expenditure
            if capital_expenditure < 0
            else operating_cash - capital_expenditure
        )
    equity = _number(balance.get("totalStockholderEquity"))
    total_debt = _number(balance.get("shortLongTermDebtTotal"))
    if total_debt is None:
        long_term_debt = _number(balance.get("longTermDebt"))
        short_term_debt = _number(balance.get("shortTermDebt"))
        if long_term_debt is not None or short_term_debt is not None:
            total_debt = (long_term_debt or 0.0) + (short_term_debt or 0.0)
    return_12m = values[-1] / values[-252] - 1.0 if len(values) >= 252 else None
    sma200 = sum(values[-200:]) / 200 if len(values) >= 200 else None
    metric_values = {
        "roe": _number(highlights.get("ReturnOnEquityTTM")) if (equity or 0) > 0 else None,
        "roa": _number(highlights.get("ReturnOnAssetsTTM")),
        "operating_margin": _number(highlights.get("OperatingMarginTTM")),
        "fcf_margin": _safe_div(free_cash_flow, revenue),
        "trailing_pe": _number(valuation.get("TrailingPE")) or _number(highlights.get("PERatio")),
        "forward_pe": _number(valuation.get("ForwardPE")),
        "ev_ebitda": _number(valuation.get("EnterpriseValueEbitda")),
        "peg": _number(highlights.get("PEGRatio")),
        "revenue_growth": _number(highlights.get("QuarterlyRevenueGrowthYOY")),
        "earnings_growth": _number(highlights.get("QuarterlyEarningsGrowthYOY")),
        "return_12m": return_12m,
        "price_to_sma200": _safe_div(values[-1], sma200) if values else None,
        "rsi14": _rsi(values),
        "beta": _number(technicals.get("Beta")),
        "volatility": _annualized_volatility(values[-253:]),
        "max_drawdown": _max_drawdown(values[-253:]),
        "debt_to_equity": _safe_div(total_debt, equity) if (equity or 0) > 0 else None,
        "dividend_yield": _number(highlights.get("DividendYield")),
        "payout_ratio": _number(highlights.get("PayoutRatio")),
    }
    metrics = _metric_set(metric_values)
    rendered_metrics = [metric.render() for metric in metrics]
    weights = STRATEGY_WEIGHTS[strategy]
    pillars: dict[str, dict[str, Any]] = {}
    total_weight = 0.0
    weighted_score = 0.0
    available_metrics = 0
    relevant_metrics = [metric for metric in metrics if metric.pillar in weights]
    for pillar, weight in weights.items():
        selected = [metric for metric in metrics if metric.pillar == pillar]
        scores = [metric.score for metric in selected if metric.score is not None]
        coverage = len(scores) / len(selected) if selected else 0.0
        score = sum(scores) / len(scores) if scores else None
        pillars[pillar] = {
            "score": _rounded(score, 2),
            "coverage": _rounded(coverage, 4),
            "weight": weight,
        }
        if score is not None:
            total_weight += weight
            weighted_score += score * weight
            available_metrics += len(scores)
    metric_coverage = available_metrics / len(relevant_metrics) if relevant_metrics else 0.0
    score = weighted_score / total_weight if total_weight else None
    missing = [metric.key for metric in relevant_metrics if metric.score is None]
    blockers: list[str] = []
    if len(values) < 200:
        blockers.append("Mindestens 200 gueltige taegliche EOD-Beobachtungen erforderlich")
    if history_age_days is None or history_age_days > 7:
        blockers.append("Letzter EOD-Kurs fehlt oder ist aelter als sieben Kalendertage")
    if metric_coverage < 0.70:
        blockers.append("Weniger als 70 Prozent der modellrelevanten Kennzahlen verfuegbar")
    for pillar in ("quality", "risk"):
        if float(pillars.get(pillar, {}).get("coverage") or 0) < 0.50:
            blockers.append(f"Pflichtpfeiler {pillar} hat weniger als 50 Prozent Datenabdeckung")
    complete = not blockers and score is not None
    if not complete:
        verdict = "abstain"
    else:
        assert score is not None
        verdict = "research-candidate" if score >= 70 else "watch" if score >= 55 else "not-prioritized"
    positive = sorted(
        (metric for metric in relevant_metrics if metric.score is not None),
        key=lambda metric: (-int(metric.score or 0), metric.key),
    )[:4]
    negative = sorted(
        (metric for metric in relevant_metrics if metric.score is not None),
        key=lambda metric: (int(metric.score or 0), metric.key),
    )[:4]
    return {
        "ok": complete,
        "decision": "informational" if complete else "abstain",
        "verdict": verdict,
        "model_version": RESEARCH_MODEL_VERSION,
        "strategy": strategy,
        "provider": RESEARCH_PROVIDER,
        "identity": {
            "isin": isin,
            "ticker": ticker,
            "name": _clean_text(general.get("Name")),
            "exchange": _clean_text(general.get("Exchange"), limit=40),
            "country": _clean_text(general.get("CountryISO"), limit=3).upper(),
            "currency": _clean_text(general.get("CurrencyCode"), limit=3).upper(),
            "sector": _clean_text(general.get("Sector"), limit=100),
            "industry": _clean_text(general.get("Industry"), limit=120),
        },
        "score": _rounded(score, 2) if complete else None,
        "metric_coverage": _rounded(metric_coverage, 4),
        "history": {
            "points": len(values),
            "first_date": normalized_history[0]["date"].isoformat() if normalized_history else None,
            "last_date": latest_date.isoformat() if latest_date else None,
            "age_days": history_age_days,
            "last_close": _rounded(values[-1]) if values else None,
            "currency": _clean_text(general.get("CurrencyCode"), limit=3).upper(),
        },
        "fundamentals_as_of": max(
            (_clean_text(item.get("date"), limit=10) for item in (income, balance, cash_flow)),
            default="",
        )
        or None,
        "pillars": pillars,
        "metrics": rendered_metrics,
        "strengths": [metric.render() for metric in positive if int(metric.score or 0) >= 70],
        "risks": [metric.render() for metric in negative if int(metric.score or 0) <= 40],
        "missing_metrics": missing,
        "blockers": blockers,
        "method": (
            "Deterministische, versionierte Mehrfaktoranalyse aus EODHD-Fundamental- und EOD-Kursdaten."
        ),
        "disclaimer": "Research-Kandidat, keine Kauf-/Verkaufsempfehlung und keine Orderfreigabe.",
    }
