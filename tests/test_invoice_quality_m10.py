from __future__ import annotations

import json
import re
import tempfile
import unittest
from pathlib import Path

from mail_agent.cli import _backfill_year
from mail_agent.storage import Storage
from scripts.evaluate_invoice_quality import DEFAULT_BASELINE, DEFAULT_CORPUS, evaluate
from scripts.invoice_ocr_fixture import build_sanitized_pdf

M100_BASELINE = Path(__file__).parent / "fixtures" / "invoices" / "m100_extractor_baseline.json"


class InvoiceQualityBaselineM10Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.corpus = json.loads(DEFAULT_CORPUS.read_text(encoding="utf-8"))

    def test_corpus_covers_required_synthetic_invoice_features(self) -> None:
        cases = self.corpus["cases"]
        features = {feature for case in cases for feature in case["features"]}
        self.assertGreaterEqual(len(cases), 8)
        self.assertEqual({case["language"] for case in cases}, {"de", "en"})
        self.assertTrue(
            {
                "explicit-invoice-number",
                "missing-invoice-number",
                "invoice-date",
                "service-date",
                "due-date",
                "gross-net-tax",
                "tax-percentage",
                "credit-note",
                "multipage-pdf",
            }.issubset(features)
        )
        multipage = next(case for case in cases if "multipage-pdf" in case["features"])
        self.assertGreater(len(multipage["pages"]), 1)
        pdf = build_sanitized_pdf()
        self.assertTrue(pdf.startswith(b"%PDF-1.4\n"))
        self.assertEqual(len(re.findall(rb"/Type /Page\b", pdf)), 3)
        self.assertIn(b"BENCH-104", pdf)

    def test_corpus_contains_no_product_identifiers_or_private_values(self) -> None:
        raw = DEFAULT_CORPUS.read_text(encoding="utf-8")
        forbidden_keys = {
            "message_id",
            "stable_key",
            "attachment_hash",
            "original_filename",
            "nextcloud_path",
            "account_number",
            "order_number",
        }
        for case in self.corpus["cases"]:
            self.assertTrue(case["message"]["sender_addr"].endswith("@example.invalid"))
            self.assertTrue(forbidden_keys.isdisjoint(case))
            self.assertTrue(forbidden_keys.isdisjoint(case["message"]))
        pdf_text = build_sanitized_pdf().decode("ascii")
        self.assertNotRegex(pdf_text.casefold(), r"\b(?:iban|bic|kontonummer|bestellnummer)\b")
        self.assertIsNone(re.search(r"\b[A-Fa-f0-9]{32,}\b", raw))
        self.assertNotRegex(raw.casefold(), r"\b(?:iban|bic|kontonummer|bestellnummer)\b")

    def test_evaluation_is_deterministic_and_matches_versioned_baseline(self) -> None:
        first = evaluate(DEFAULT_CORPUS)
        second = evaluate(DEFAULT_CORPUS)
        baseline = json.loads(DEFAULT_BASELINE.read_text(encoding="utf-8"))
        self.assertEqual(first, second)
        self.assertEqual(first, baseline)
        self.assertEqual(first["case_count"], 8)

    def test_m103_corrects_the_m100_multiple_total_false_confirmation(self) -> None:
        report = evaluate(DEFAULT_CORPUS)
        current_error = next(case for case in report["cases"] if case["case_id"] == "de-multiple-totals")
        self.assertEqual(current_error["status"], "confirmed")
        self.assertFalse(current_error["false_confirmed"])
        self.assertEqual(current_error["mismatches"], [])
        self.assertEqual(report["outcomes"]["false_confirmed"], 0)
        historical = json.loads(M100_BASELINE.read_text(encoding="utf-8"))
        self.assertEqual(historical["outcomes"]["false_confirmed"], 1)

    def test_legacy_backfill_excludes_review_and_keeps_ten_empty_states_separate(self) -> None:
        with tempfile.TemporaryDirectory(prefix="openclaw-m10-") as temporary:
            storage = Storage(Path(temporary) / "mail.sqlite3")
            try:
                for index in range(48):
                    self._record(storage, f"review-{index:02d}", extraction_status="review")
                for index in range(5):
                    self._record(storage, f"confirmed-{index:02d}", extraction_status="confirmed")
                for index in range(10):
                    self._record(storage, f"empty-{index:02d}", extraction_status="")

                candidates = storage.list_invoice_backfill_candidates(limit=5000)
                candidate_hashes = {str(row["attachment_hash"]) for row in candidates}
                review_hashes = {
                    str(row["attachment_hash"])
                    for row in storage.list_invoices(extraction_status="review", limit=5000)
                }
                empty_count = storage.connection.execute(
                    "SELECT COUNT(*) FROM invoices WHERE COALESCE(extraction_status, '') = ''"
                ).fetchone()[0]

                self.assertEqual(len(review_hashes), 48)
                self.assertEqual(empty_count, 10)
                self.assertEqual(candidate_hashes, {f"empty-{index:02d}" for index in range(10)})
                self.assertTrue(candidate_hashes.isdisjoint(review_hashes))
            finally:
                storage.close()

    def test_current_backfill_year_priority_is_characterized(self) -> None:
        # This freezes the observed order only. M10.0 does not endorse it as
        # the desired semantics for future reprocessing.
        row = {
            "invoice_date": "2024-12-31",
            "received_date": "2025-01-02",
            "created_at": "2026-01-03T00:00:00+00:00",
            "nextcloud_path": "Assistent/Rechnungen/2027/synthetic.pdf",
            "received_at": "Mon, 04 Jan 2028 10:00:00 +0100",
        }
        self.assertEqual(_backfill_year(row), 2024)
        row["invoice_date"] = ""
        self.assertEqual(_backfill_year(row), 2025)
        row["received_date"] = ""
        self.assertEqual(_backfill_year(row), 2026)
        row["created_at"] = ""
        self.assertEqual(_backfill_year(row), 2027)
        row["nextcloud_path"] = "Assistent/Rechnungen/Pruefen/synthetic.pdf"
        self.assertEqual(_backfill_year(row), 2028)

    @staticmethod
    def _record(storage: Storage, attachment_hash: str, *, extraction_status: str) -> None:
        storage.record_invoice(
            stable_key=f"synthetic-{attachment_hash}",
            attachment_hash=attachment_hash,
            original_filename="synthetic.pdf",
            nextcloud_path="Assistent/Rechnungen/2026/synthetic.pdf",
            size_bytes=1,
            status="uploaded",
            extraction_status=extraction_status,
        )


if __name__ == "__main__":
    unittest.main()
