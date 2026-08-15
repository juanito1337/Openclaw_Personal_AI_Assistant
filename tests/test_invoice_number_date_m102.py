from __future__ import annotations

import json
import unittest
from pathlib import Path
from unittest.mock import patch

from mail_agent.config import InvoiceConfig
from mail_agent.invoice_extract import InvoiceExtractor, parse_invoice_text
from mail_agent.models import ParsedMessage
from scripts.evaluate_invoice_quality import evaluate

FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "invoices"
CORPUS = FIXTURE_ROOT / "m102_number_date_corpus.json"
BASELINE = FIXTURE_ROOT / "m102_number_date_baseline.json"
COMPARISON = FIXTURE_ROOT / "m102_number_date_comparison.json"


def message() -> ParsedMessage:
    return ParsedMessage(
        stable_key="synthetic-m102",
        mailbox_id="synthetic",
        source_folder="fixture",
        raw=b"",
        subject="Synthetischer M10.2-Beleg",
        sender_name="M102 Fiktivtest GmbH",
        sender_addr="m102@example.invalid",
        received_at="Sat, 15 Aug 2026 10:00:00 +0200",
    )


def extract(text: str, *, filename: str = ""):
    return parse_invoice_text(
        text,
        message(),
        method="synthetic-text",
        document_name=filename,
    )


class InvoiceNumberDateM102Tests(unittest.TestCase):
    maxDiff = None

    def test_sanitized_corpus_covers_number_and_date_contract(self) -> None:
        payload = json.loads(CORPUS.read_text(encoding="utf-8"))
        features = {feature for case in payload["cases"] for feature in case["features"]}
        self.assertEqual(payload["schema_version"], 1)
        self.assertEqual(len(payload["cases"]), 12)
        self.assertEqual({case["language"] for case in payload["cases"]}, {"de", "en"})
        self.assertTrue(
            {
                "german-label",
                "english-label",
                "hyphen",
                "slash",
                "alphanumeric",
                "unicode",
                "ocr-spacing",
                "filename-only",
                "negative-number-labels",
                "invoice-date-conflict",
                "invoice-number-conflict",
                "bounded-next-line",
            }.issubset(features)
        )
        raw = CORPUS.read_text(encoding="utf-8")
        self.assertNotIn("/srv/openclaw", raw)
        self.assertNotRegex(raw, r"[a-fA-F0-9]{32,}")
        self.assertTrue(
            all(
                case["message"]["sender_addr"].endswith("@example.invalid")
                for case in payload["cases"]
            )
        )

    def test_german_english_unicode_and_ocr_numbers_are_normalized(self) -> None:
        cases = {
            "de": ("Rechnung Nr.: RÄ-2026/001", "RÄ-2026/001"),
            "en": ("Invoice No. EN-A/42", "EN-A/42"),
            "ocr": (
                "R e c h n u n g s n u m m e r : O C R - 2 0 2 6 / 0 3",
                "OCR-2026/03",
            ),
        }
        for name, (line, expected) in cases.items():
            with self.subTest(name=name):
                metadata = extract(
                    f"M102 Fiktivtest GmbH\n{line}\nRechnungsdatum: 14.08.2026\n"
                    "Gesamtbetrag: 119,00 EUR"
                )
                self.assertEqual(metadata.invoice_number.value, expected)
                selected = [
                    item
                    for item in metadata.field_candidates
                    if item.field == "invoice_number" and not item.excluded_reason
                ]
                self.assertEqual([item.normalized_value for item in selected], [expected])
                self.assertIn(selected[0].evidence_type, {"labeled-same-line", "labeled-next-line"})
                serialized = json.loads(metadata.to_json())["field_candidates"]
                self.assertTrue(
                    {
                        "field",
                        "role",
                        "raw_value",
                        "normalized_value",
                        "source",
                        "evidence_type",
                        "evidence",
                        "confidence",
                        "excluded_reason",
                    }.issubset(serialized[0])
                )

    def test_filename_only_supports_a_labeled_document_value(self) -> None:
        text = (
            "M102 Fiktivtest GmbH\nRechnungsnummer: SYN-FILE-004\n"
            "Rechnungsdatum: 14.08.2026\nGesamtbetrag: 119,00 EUR"
        )
        supported = extract(text, filename="Rechnung_SYN-FILE-004.pdf")
        unsupported = extract(
            "M102 Fiktivtest GmbH\nRechnungsdatum: 14.08.2026\nGesamtbetrag: 119,00 EUR",
            filename="SYN-ONLY-005.pdf",
        )

        self.assertEqual(supported.invoice_number.value, "SYN-FILE-004")
        self.assertEqual(supported.invoice_number.confidence, 0.97)
        filename_match = next(
            item for item in supported.field_candidates if item.source == "filename"
        )
        self.assertEqual(filename_match.evidence_type, "supporting-filename-match")
        self.assertEqual(filename_match.excluded_reason, "")
        self.assertEqual(unsupported.invoice_number.value, "")
        self.assertEqual(unsupported.status, "review")
        filename_only = next(
            item for item in unsupported.field_candidates if item.source == "filename"
        )
        self.assertEqual(filename_only.excluded_reason, "filename-support-only")

    def test_non_invoice_identifiers_are_typed_and_excluded(self) -> None:
        text = """M102 Fiktivtest GmbH
Kundennummer: K-10001
Bestellnummer: B-20002
Lieferscheinnummer: L-30003
Vertragsnummer: V-40004
Telefon: +49 30 555000
USt-ID: DE123456789
IBAN: DE00 0000 0000 0000 0000 00
Sendungsnummer: T-50005
Rechnungsdatum: 14.08.2026
Gesamtbetrag: 119,00 EUR"""
        metadata = extract(text)
        excluded = {
            item.role: item.excluded_reason
            for item in metadata.field_candidates
            if item.field == "invoice_number" and item.excluded_reason
        }
        self.assertEqual(metadata.invoice_number.value, "")
        self.assertEqual(metadata.status, "review")
        for role in {
            "customer-number",
            "order-number",
            "delivery-number",
            "contract-number",
            "phone-number",
            "tax-number",
            "tracking-number",
            "iban",
        }:
            self.assertEqual(excluded[role], f"not-invoice-number:{role}")

    def test_invoice_number_that_is_a_date_is_a_visible_excluded_candidate(self) -> None:
        metadata = extract(
            "M102 Fiktivtest GmbH\nRechnung NR. 15.08.2026\n"
            "Rechnungsdatum: 14.08.2026\nGesamtbetrag: 119,00 EUR"
        )
        candidates = [
            item
            for item in metadata.field_candidates
            if item.field == "invoice_number" and item.role == "invoice-number"
        ]
        self.assertEqual(metadata.invoice_number.value, "")
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].normalized_value, "15.08.2026")
        self.assertEqual(candidates[0].excluded_reason, "value-is-date")
        self.assertEqual(metadata.invoice_date.value, "2026-08-14")

    def test_date_roles_are_distinct_and_only_invoice_date_is_selected(self) -> None:
        metadata = extract(
            """M102 Fiktivtest GmbH
Rechnungsnummer: SYN-DATE-1
Bestelldatum: 10.08.2026
Leistungsdatum: 11.08.2026
Lieferdatum: 12.08.2026
Zahlungsdatum: 13.08.2026
Rechnungsdatum: 14.08.2026
Faellig am: 14.09.2026
Gesamtbetrag: 119,00 EUR"""
        )
        by_role = {
            item.role: item
            for item in metadata.field_candidates
            if item.field == "invoice_date"
        }
        self.assertEqual(metadata.invoice_date.value, "2026-08-14")
        self.assertEqual(by_role["invoice-date"].excluded_reason, "")
        for role in {"order-date", "service-date", "delivery-date", "payment-date", "due-date"}:
            self.assertEqual(by_role[role].excluded_reason, f"not-invoice-date:{role}")

    def test_conflicting_invoice_dates_and_numbers_fail_closed(self) -> None:
        date_conflict = extract(
            "M102 Fiktivtest GmbH\nInvoice No: SYN-1\nInvoice Date: 2026-08-13\n"
            "Document Date: 2026-08-14\nGrand Total: 119.00 EUR"
        )
        number_conflict = extract(
            "M102 Fiktivtest GmbH\nInvoice No: SYN-1\nRechnungsnummer: SYN-2\n"
            "Invoice Date: 2026-08-14\nGrand Total: 119.00 EUR"
        )
        self.assertEqual(date_conflict.invoice_date.value, "")
        self.assertEqual(number_conflict.invoice_number.value, "")
        self.assertEqual(date_conflict.status, "review")
        self.assertEqual(number_conflict.status, "review")
        self.assertTrue(
            all(
                item.excluded_reason == "conflicting-invoice-date"
                for item in date_conflict.field_candidates
                if item.role == "invoice-date"
            )
        )
        self.assertTrue(
            all(
                item.excluded_reason == "conflicting-invoice-number"
                for item in number_conflict.field_candidates
                if item.role == "invoice-number"
            )
        )

    def test_next_line_context_is_bounded_and_does_not_steal_another_label(self) -> None:
        accepted = extract(
            "M102 Fiktivtest GmbH\nInvoice Number:\nEN-NEXT/011\n"
            "Invoice Date:\n2026-08-14\nGrand Total: 119.00 EUR"
        )
        rejected = extract(
            "M102 Fiktivtest GmbH\nInvoice Number:\nCustomer Number: C-12\n"
            "Invoice Date: 2026-08-14\nGrand Total: 119.00 EUR"
        )
        self.assertEqual(accepted.invoice_number.value, "EN-NEXT/011")
        self.assertEqual(accepted.invoice_date.value, "2026-08-14")
        self.assertEqual(rejected.invoice_number.value, "")

    def test_extractor_propagates_physical_filename_as_support_only(self) -> None:
        native_text = (
            "M102 Fiktivtest GmbH\nRechnungsnummer: SYN-PDF-13\n"
            "Rechnungsdatum: 14.08.2026\nGesamtbetrag: 119,00 EUR"
            + " Leistungsbeschreibung" * 20
        )
        extractor = InvoiceExtractor(InvoiceConfig(ocr_enabled=True, min_text_quality=0.20))
        with patch(
            "mail_agent.invoice_extract.extract_native_text",
            return_value=(native_text, ""),
        ), patch("mail_agent.invoice_extract.extract_ocr_text") as ocr:
            metadata = extractor.extract(
                b"%PDF-1.7 synthetic",
                message(),
                filename="SYN-PDF-13.pdf",
            )
        self.assertEqual(metadata.invoice_number.value, "SYN-PDF-13")
        self.assertTrue(any(item.source == "filename" for item in metadata.field_candidates))
        ocr.assert_not_called()

    def test_number_date_report_is_deterministic_and_matches_m102_baseline(self) -> None:
        first = evaluate(CORPUS)
        second = evaluate(CORPUS)
        expected = json.loads(BASELINE.read_text(encoding="utf-8"))
        self.assertEqual(first, second)
        self.assertEqual(first, expected)

    def test_number_date_comparison_improves_coverage_without_false_confirmation(self) -> None:
        report = evaluate(CORPUS)
        comparison = json.loads(COMPARISON.read_text(encoding="utf-8"))
        before = comparison["before"]
        number = report["field_metrics"]["invoice_number"]
        invoice_date = report["field_metrics"]["invoice_date"]
        self.assertGreater(number["coverage"], before["invoice_number"]["coverage"])
        self.assertGreaterEqual(number["precision"], before["invoice_number"]["precision"])
        self.assertGreaterEqual(invoice_date["coverage"], before["invoice_date"]["coverage"])
        self.assertGreaterEqual(invoice_date["precision"], before["invoice_date"]["precision"])
        self.assertLessEqual(
            report["outcomes"]["false_confirmed"], before["false_confirmed"]
        )


if __name__ == "__main__":
    unittest.main()
