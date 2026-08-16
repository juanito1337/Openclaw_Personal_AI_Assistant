from __future__ import annotations

import json
import unittest
from pathlib import Path

from mail_agent.invoice_extract import (
    AMOUNT_ARITHMETIC_TOLERANCE_CENTS,
    amount_to_cents,
    parse_invoice_text,
)
from mail_agent.models import ParsedMessage
from scripts.evaluate_invoice_quality import evaluate

FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "invoices"
CORPUS = FIXTURE_ROOT / "m103_amount_corpus.json"
BASELINE = FIXTURE_ROOT / "m103_amount_baseline.json"
COMPARISON = FIXTURE_ROOT / "m103_amount_comparison.json"


def message(*, subject: str = "Synthetischer M10.3-Beleg") -> ParsedMessage:
    return ParsedMessage(
        stable_key="synthetic-m103",
        mailbox_id="synthetic",
        source_folder="fixture",
        raw=b"",
        subject=subject,
        sender_name="M103 Fiktivtest GmbH",
        sender_addr="m103@example.invalid",
        received_at="Sat, 15 Aug 2026 10:00:00 +0200",
    )


def extract(text: str, *, subject: str = "", filename: str = ""):
    return parse_invoice_text(
        text,
        message(subject=subject or "Synthetischer M10.3-Beleg"),
        method="synthetic-text",
        document_name=filename,
    )


class InvoiceAmountsM103Tests(unittest.TestCase):
    maxDiff = None

    def test_sanitized_corpus_covers_amount_contract(self) -> None:
        payload = json.loads(CORPUS.read_text(encoding="utf-8"))
        features = {feature for case in payload["cases"] for feature in case["features"]}
        self.assertEqual(payload["schema_version"], 1)
        self.assertEqual(len(payload["cases"]), 15)
        self.assertEqual({case["language"] for case in payload["cases"]}, {"de", "en"})
        self.assertTrue(
            {
                "german-decimal",
                "english-decimal",
                "german-thousands",
                "english-thousands",
                "percentage-not-money",
                "multiple-totals",
                "not-largest",
                "cent-tolerance",
                "arithmetic-conflict",
                "tax-above-gross",
                "advance-payment",
                "credit-note",
                "positive-credit",
                "discount",
                "unit-price",
                "foreign-currency",
                "currency-conflict",
            }.issubset(features)
        )
        raw = CORPUS.read_text(encoding="utf-8")
        self.assertNotIn("/srv/openclaw", raw)
        self.assertNotRegex(raw, r"[a-fA-F0-9]{32,}")
        self.assertTrue(
            all(case["message"]["sender_addr"].endswith("@example.invalid") for case in payload["cases"])
        )

    def test_decimal_thousands_signs_and_iso_currencies_are_deterministic(self) -> None:
        values = {
            "1.234,56 EUR": 123456,
            "EUR 1 234,56": 123456,
            "USD 1,234.56": 123456,
            "1'234.56 CHF": 123456,
            "GBP -1,234.56": -123456,
            "(1.234,56) €": -123456,
            "+19.00": 1900,
        }
        for raw, expected in values.items():
            with self.subTest(raw=raw):
                self.assertEqual(amount_to_cents(raw), expected)
        for raw in {"1,2,3", "EUR --19.00", "1.2345", "(19.00"}:
            with self.subTest(invalid=raw):
                self.assertIsNone(amount_to_cents(raw))

    def test_tax_rates_are_typed_and_never_selected_as_money(self) -> None:
        for rate in ("19 %", "7 %", "19.00 %", "7,00 %"):
            with self.subTest(rate=rate):
                metadata = extract(
                    "M103 Fiktivtest GmbH\nRechnungsnummer: M103-RATE-1\n"
                    f"Rechnungsdatum: 14.08.2026\nSteuersatz: {rate}\n"
                    "Gesamtbetrag: 100,00 EUR"
                )
                self.assertEqual(metadata.tax_amount.value, "")
                rates = [candidate for candidate in metadata.field_candidates if candidate.role == "tax-rate"]
                self.assertEqual(len(rates), 1)
                self.assertEqual(rates[0].excluded_reason, "percentage-is-not-money")
                self.assertEqual(rates[0].currency, "")

    def test_labeled_amount_due_wins_without_largest_value_heuristic(self) -> None:
        metadata = extract(
            """M103 Fiktivtest GmbH
Rechnungsnummer: M103-DUE-1
Rechnungsdatum: 14.08.2026
Gesamtsumme Positionen: 999,00 EUR
Zu zahlen: 119,00 EUR"""
        )
        self.assertEqual(metadata.gross_amount.value, "119.00")
        self.assertEqual(metadata.net_amount.value, "")
        by_role = {
            candidate.role: candidate
            for candidate in metadata.field_candidates
            if candidate.role in {"subtotal", "amount-due"}
        }
        self.assertEqual(by_role["amount-due"].excluded_reason, "")
        self.assertEqual(
            by_role["subtotal"].excluded_reason,
            "subtotal-not-arithmetically-validated",
        )

    def test_incompatible_totals_fail_closed_with_typed_reason(self) -> None:
        metadata = extract(
            """M103 Fiktivtest GmbH
Invoice Number: M103-CONFLICT-1
Invoice Date: 2026-08-14
Grand Total: 100.00 EUR
Invoice Total: 120.00 EUR"""
        )
        self.assertEqual(metadata.gross_amount.value, "")
        self.assertEqual(metadata.status, "review")
        self.assertIn("amount:gross_amount-conflict", metadata.review_reasons)
        gross_candidates = [
            candidate for candidate in metadata.field_candidates if candidate.role == "gross-total"
        ]
        self.assertEqual(len(gross_candidates), 2)
        self.assertTrue(
            all(candidate.excluded_reason == "conflicting-gross-total" for candidate in gross_candidates)
        )

    def test_arithmetic_tolerance_and_implausible_triples(self) -> None:
        accepted = extract(
            """M103 Fiktivtest GmbH
Rechnungsnummer: M103-ROUND-1
Rechnungsdatum: 14.08.2026
Nettobetrag: 8,41 EUR
Umsatzsteuer: 1,60 EUR
Gesamtbetrag: 10,00 EUR"""
        )
        inconsistent = extract(
            """M103 Fiktivtest GmbH
Rechnungsnummer: M103-BAD-1
Rechnungsdatum: 14.08.2026
Nettobetrag: 100,00 EUR
Umsatzsteuer: 19,00 EUR
Gesamtbetrag: 120,00 EUR"""
        )
        tax_above_gross = extract(
            """M103 Fiktivtest GmbH
Rechnungsnummer: M103-BAD-2
Rechnungsdatum: 14.08.2026
Nettobetrag: -9,00 EUR
Umsatzsteuer: 19,00 EUR
Gesamtbetrag: 10,00 EUR"""
        )
        self.assertEqual(AMOUNT_ARITHMETIC_TOLERANCE_CENTS, 2)
        self.assertEqual(accepted.status, "confirmed")
        self.assertNotIn("amount:arithmetic-mismatch", accepted.review_reasons)
        self.assertEqual(inconsistent.status, "review")
        self.assertIn("amount:arithmetic-mismatch", inconsistent.review_reasons)
        self.assertEqual(tax_above_gross.status, "review")
        self.assertIn("amount:tax-exceeds-gross", tax_above_gross.review_reasons)
        self.assertIn("amount:sign-mismatch", tax_above_gross.review_reasons)

    def test_discount_advance_credit_and_unit_price_keep_distinct_roles(self) -> None:
        metadata = extract(
            """M103 Fiktivtest GmbH
Rechnungsnummer: M103-ROLES-1
Rechnungsdatum: 14.08.2026
Einzelpreis: 999,00 EUR
Zwischensumme: 100,00 EUR
Rabatt: 10,00 EUR
Nettobetrag: 90,00 EUR
Umsatzsteuer: 17,10 EUR
Rechnungssumme: 107,10 EUR
Abschlagszahlung: 38,10 EUR
Noch zu zahlen: 69,00 EUR"""
        )
        roles = {candidate.role: candidate for candidate in metadata.field_candidates}
        self.assertEqual(metadata.gross_amount.value, "69.00")
        self.assertEqual(metadata.status, "review")
        self.assertIn("amount:arithmetic-mismatch", metadata.review_reasons)
        for role in {"unit-price", "discount", "advance-payment"}:
            self.assertEqual(roles[role].excluded_reason, f"not-invoice-total:{role}")
        self.assertEqual(roles["subtotal"].excluded_reason, "lower-priority-than:net-total")
        self.assertEqual(roles["gross-total"].excluded_reason, "lower-priority-than:amount-due")

        negative_credit = extract(
            "M103 Fiktivtest GmbH\nRechnungsnummer: M103-CREDIT-1\n"
            "Rechnungsdatum: 14.08.2026\nGutschriftsbetrag: -59,50 EUR"
        )
        positive_credit = extract(
            "M103 Fiktivtest GmbH\nRechnungsnummer: M103-CREDIT-2\n"
            "Rechnungsdatum: 14.08.2026\nGuthaben: 59,50 EUR"
        )
        self.assertEqual(negative_credit.gross_amount.value, "-59.50")
        self.assertEqual(negative_credit.status, "confirmed")
        self.assertEqual(positive_credit.gross_amount.value, "")
        self.assertEqual(positive_credit.status, "review")
        self.assertIn("amount:credit-sign-ambiguous", positive_credit.review_reasons)

    def test_mixed_currencies_are_not_silently_corrected(self) -> None:
        metadata = extract(
            """M103 Fiktivtest GmbH
Invoice Number: M103-CURRENCY-1
Invoice Date: 2026-08-14
Net Amount: 100.00 USD
Tax Amount: 19.00 EUR
Invoice Total: 119.00 EUR"""
        )
        self.assertEqual(metadata.currency.value, "")
        self.assertEqual(metadata.status, "review")
        self.assertIn("amount:currency-conflict", metadata.review_reasons)
        currencies = {
            candidate.currency
            for candidate in metadata.field_candidates
            if candidate.field in {"gross_amount", "net_amount", "tax_amount"}
            and candidate.role != "tax-rate"
        }
        self.assertEqual(currencies, {"EUR", "USD"})

    def test_amounts_are_never_taken_from_mail_filename_or_ollama(self) -> None:
        metadata = extract(
            "M103 Fiktivtest GmbH\nRechnungsnummer: M103-NO-AMOUNT\nRechnungsdatum: 14.08.2026",
            subject="Amount Due: 999.00 USD",
            filename="Invoice_777.00_EUR.pdf",
        )
        self.assertEqual(metadata.gross_amount.value, "")
        self.assertEqual(metadata.net_amount.value, "")
        self.assertEqual(metadata.tax_amount.value, "")
        amount_candidates = [
            candidate
            for candidate in metadata.field_candidates
            if candidate.field in {"gross_amount", "net_amount", "tax_amount", "amount"}
        ]
        self.assertEqual(amount_candidates, [])
        self.assertNotIn("ollama", metadata.method.casefold())

    def test_amount_report_is_deterministic_and_matches_m103_baseline(self) -> None:
        first = evaluate(CORPUS)
        second = evaluate(CORPUS)
        expected = json.loads(BASELINE.read_text(encoding="utf-8"))
        self.assertEqual(first, second)
        self.assertEqual(first, expected)

    def test_direct_before_after_comparison_improves_amount_quality(self) -> None:
        report = evaluate(CORPUS)
        comparison = json.loads(COMPARISON.read_text(encoding="utf-8"))
        before = comparison["before"]
        for field in ("gross_amount", "net_amount", "tax_amount", "currency"):
            self.assertGreaterEqual(
                report["field_metrics"][field]["precision"],
                before[field]["precision"],
            )
            self.assertGreaterEqual(
                report["field_metrics"][field]["coverage"],
                before[field]["coverage"],
            )
        self.assertLess(
            report["outcomes"]["false_confirmed"],
            before["false_confirmed"],
        )
        self.assertLess(
            report["arithmetic"]["errors"],
            before["arithmetic_errors"],
        )


if __name__ == "__main__":
    unittest.main()
