#!/usr/bin/env python3
"""Evaluate the current invoice text extractor against synthetic M10 fixtures."""

from __future__ import annotations

import argparse
import json
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mail_agent.invoice_extract import amount_to_cents, parse_invoice_text  # noqa: E402
from mail_agent.models import ParsedMessage  # noqa: E402

DEFAULT_CORPUS = ROOT / "tests" / "fixtures" / "invoices" / "m10_sanitized_corpus.json"
DEFAULT_BASELINE = ROOT / "tests" / "fixtures" / "invoices" / "m10_extractor_baseline.json"
EVALUATED_FIELDS = (
    "invoice_date",
    "invoice_number",
    "supplier",
    "gross_amount",
    "net_amount",
    "tax_amount",
    "currency",
    "due_date",
)
AMOUNT_FIELDS = {"gross_amount", "net_amount", "tax_amount"}
CRITICAL_FIELDS = ("invoice_date", "invoice_number", "supplier", "gross_amount")


def _rate(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 4) if denominator else None


def _normal(value: object, field: str) -> object:
    text = str(value or "").strip()
    if field in AMOUNT_FIELDS:
        try:
            return Decimal(text).quantize(Decimal("0.01")) if text else None
        except InvalidOperation:
            return text
    if field == "currency":
        return text.upper()
    return " ".join(text.casefold().split())


def _load_corpus(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != 1 or not isinstance(payload.get("cases"), list):
        raise ValueError(f"Ungueltiges M10-Korpusformat: {path}")
    return payload


def _message(case: dict[str, Any]) -> ParsedMessage:
    source = case["message"]
    case_id = str(case["case_id"])
    return ParsedMessage(
        stable_key=f"synthetic-{case_id}",
        mailbox_id="synthetic",
        source_folder="fixture",
        raw=b"",
        subject=str(source["subject"]),
        sender_name=str(source["sender_name"]),
        sender_addr=str(source["sender_addr"]),
        received_at=str(source["received_at"]),
    )


def _arithmetic_error(actual: dict[str, str]) -> bool | None:
    values = [amount_to_cents(actual[field]) for field in ("gross_amount", "net_amount", "tax_amount")]
    if any(value is None for value in values):
        return None
    gross, net, tax = values
    assert gross is not None and net is not None and tax is not None
    return abs(gross - net - tax) > 2


def evaluate(corpus_path: Path = DEFAULT_CORPUS) -> dict[str, Any]:
    corpus = _load_corpus(corpus_path)
    field_counts = {
        field: {"expected": 0, "predicted": 0, "correct": 0}
        for field in EVALUATED_FIELDS
    }
    cases: list[dict[str, Any]] = []
    confirmed = 0
    review = 0
    false_confirmed = 0
    complete_triples = 0
    arithmetic_errors = 0

    for case in corpus["cases"]:
        text = "\n\f\n".join(str(page) for page in case["pages"])
        metadata = parse_invoice_text(text, _message(case), method="synthetic-text")
        actual = {
            field: str(getattr(metadata, field).value or "")
            for field in EVALUATED_FIELDS
        }
        expected = {field: str(case["expected"].get(field) or "") for field in EVALUATED_FIELDS}
        mismatches: list[str] = []
        for field in EVALUATED_FIELDS:
            expected_present = bool(expected[field])
            actual_present = bool(actual[field])
            if expected_present:
                field_counts[field]["expected"] += 1
            if actual_present:
                field_counts[field]["predicted"] += 1
            values_match = _normal(expected[field], field) == _normal(actual[field], field)
            if expected_present and actual_present and values_match:
                field_counts[field]["correct"] += 1
            elif expected_present != actual_present or (
                expected_present and _normal(expected[field], field) != _normal(actual[field], field)
            ):
                mismatches.append(field)

        is_false_confirmed = metadata.status == "confirmed" and any(
            not expected[field]
            or not actual[field]
            or _normal(expected[field], field) != _normal(actual[field], field)
            for field in CRITICAL_FIELDS
        )
        arithmetic_error = _arithmetic_error(actual)
        if metadata.status == "confirmed":
            confirmed += 1
        else:
            review += 1
        if is_false_confirmed:
            false_confirmed += 1
        if arithmetic_error is not None:
            complete_triples += 1
            arithmetic_errors += int(arithmetic_error)
        cases.append(
            {
                "case_id": str(case["case_id"]),
                "status": metadata.status,
                "mismatches": mismatches,
                "false_confirmed": is_false_confirmed,
                "arithmetic_error": arithmetic_error,
            }
        )

    field_metrics: dict[str, dict[str, int | float | None]] = {}
    expected_total = predicted_total = correct_total = 0
    for field in EVALUATED_FIELDS:
        counts = field_counts[field]
        expected_total += counts["expected"]
        predicted_total += counts["predicted"]
        correct_total += counts["correct"]
        field_metrics[field] = {
            **counts,
            "precision": _rate(counts["correct"], counts["predicted"]),
            "coverage": _rate(counts["correct"], counts["expected"]),
        }

    case_count = len(cases)
    return {
        "schema_version": 1,
        "corpus_id": str(corpus["corpus_id"]),
        "case_count": case_count,
        "metric_definitions": {
            "field_precision": "correct non-empty predictions / all non-empty predictions",
            "field_coverage": "correct non-empty predictions / all non-empty expected values",
            "false_confirmed": "confirmed although a required expected fact is missing or incorrect",
            "review_rate": "review outcomes / all cases",
            "arithmetic_error": "complete gross/net/tax triple differs by more than two cents",
        },
        "field_metrics": field_metrics,
        "overall_fields": {
            "expected": expected_total,
            "predicted": predicted_total,
            "correct": correct_total,
            "precision": _rate(correct_total, predicted_total),
            "coverage": _rate(correct_total, expected_total),
        },
        "outcomes": {
            "confirmed": confirmed,
            "review": review,
            "review_rate": _rate(review, case_count),
            "false_confirmed": false_confirmed,
            "false_confirmed_rate": _rate(false_confirmed, confirmed),
        },
        "arithmetic": {
            "complete_triples": complete_triples,
            "errors": arithmetic_errors,
            "error_rate": _rate(arithmetic_errors, complete_triples),
        },
        "cases": cases,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    parser.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    parser.add_argument(
        "--verify",
        action="store_true",
        help="Mit der versionierten M10.0-Baseline vergleichen",
    )
    return parser


def main() -> int:
    args = _parser().parse_args()
    report = evaluate(args.corpus)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    if not args.verify:
        return 0
    expected = json.loads(args.baseline.read_text(encoding="utf-8"))
    if report != expected:
        print(f"M10.0-Baseline weicht ab: {args.baseline}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
