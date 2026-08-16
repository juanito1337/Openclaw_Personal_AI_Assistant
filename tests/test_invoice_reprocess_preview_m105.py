from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from mail_agent.cli import _handle_invoices, build_parser
from mail_agent.config import InvoiceConfig
from mail_agent.invoice_extract import (
    ExtractionTechnicalMetadata,
    FieldCandidate,
    FieldValue,
    InvoiceMetadata,
)
from mail_agent.invoice_reprocess import (
    build_preview_record,
    classify_proposal,
    compute_preview_digest,
    read_reprocess_candidates,
    run_reprocess_preview,
)
from mail_agent.storage import Storage
from personal_assistant.cli import parser as assistant_parser
from personal_assistant.cli_handlers.invoices import run_external as run_invoice_external
from personal_assistant.tool_registry import tool_definitions
from personal_assistant.tool_settings import AntivirusToolSettings

PRIVATE_EVIDENCE = "PRIVATE-PDF-TEXT-MUST-NEVER-LEAVE-THE-EXTRACTOR"


class _Extractor:
    def __init__(self, results: dict[bytes, InvoiceMetadata]) -> None:
        self.results = results

    def extract(self, data: bytes, *_: object, **__: object) -> InvoiceMetadata:
        return self.results[data]


def _metadata(
    *,
    invoice_date: str = "2027-02-03",
    invoice_number: str = "SYN-105",
    supplier: str = "M105 Beispiel GmbH",
    gross: str = "119.00",
    status: str = "confirmed",
    version: str = "m10.5-test",
    conflicts: list[str] | None = None,
) -> InvoiceMetadata:
    candidate = FieldCandidate(
        field="invoice_number",
        role="invoice-number",
        raw_value=invoice_number,
        normalized_value=invoice_number,
        source="native",
        evidence_type="labeled-same-line",
        evidence=PRIVATE_EVIDENCE,
        confidence=0.96,
    )
    return InvoiceMetadata(
        invoice_date=FieldValue(invoice_date, 0.95, PRIVATE_EVIDENCE),
        invoice_number=FieldValue(invoice_number, 0.96, PRIVATE_EVIDENCE),
        supplier=FieldValue(supplier, 0.94, PRIVATE_EVIDENCE),
        category=FieldValue("Test", 0.8, PRIVATE_EVIDENCE),
        gross_amount=FieldValue(gross, 0.97, PRIVATE_EVIDENCE),
        currency=FieldValue("EUR", 0.95, PRIVATE_EVIDENCE),
        status=status,
        confidence=0.95,
        method="text",
        issues=[PRIVATE_EVIDENCE],
        review_reasons=list(conflicts or []),
        field_candidates=[candidate],
        technical=ExtractionTechnicalMetadata(
            extractor_version=version,
            ruleset_version="m10.5-rules-test",
            native_duration_ms=99.9,
        ),
    )


class InvoiceReprocessPreviewM105Tests(unittest.TestCase):
    maxDiff = None

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="openclaw-m105-")
        self.root = Path(self.temporary.name)
        self.database = self.root / "mail.sqlite3"
        self.documents: dict[str, bytes] = {}
        self.hashes: dict[str, str] = {}
        self._add(
            "review",
            extraction_status="review",
            register_year=2024,
            path_year=2025,
            received_date="2026-01-02",
            invoice_number="OLD-105",
            gross_amount_cents=9900,
        )
        self._add(
            "unclassified",
            extraction_status="",
            register_year=None,
            path_year=2024,
            received_date="2023-12-31",
        )
        self._add(
            "confirmed",
            extraction_status="confirmed",
            register_year=2024,
            path_year=2024,
            received_date="2024-01-01",
        )
        self._add(
            "manual",
            extraction_status="confirmed-manual",
            register_year=2024,
            path_year=2024,
            received_date="2024-01-01",
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _add(
        self,
        key: str,
        *,
        extraction_status: str,
        register_year: int | None,
        path_year: int,
        received_date: str,
        invoice_number: str = "",
        gross_amount_cents: int | None = None,
    ) -> None:
        data = f"%PDF-1.7\nsynthetic-{key}".encode()
        digest = hashlib.sha256(data).hexdigest()
        path = f"Assistent/Rechnungen/{path_year}/02/{key}.pdf"
        self.documents[path] = data
        self.hashes[key] = digest
        storage = Storage(self.database)
        try:
            storage.record_invoice(
                stable_key=f"m105-{key}",
                attachment_hash=digest,
                original_filename=f"{key}.pdf",
                nextcloud_path=path,
                size_bytes=len(data),
                status="uploaded",
                received_date=received_date,
                invoice_number=invoice_number,
                gross_amount_cents=gross_amount_cents,
                currency="EUR",
                extraction_status=extraction_status,
                extraction_confidence=0.7,
                extraction_method="legacy-text",
                extraction_json=json.dumps(
                    {
                        "invoice_number": {
                            "value": invoice_number,
                            "confidence": 0.7,
                            "evidence": PRIVATE_EVIDENCE,
                        },
                        "review_reasons": ["amount:gross-conflict", PRIVATE_EVIDENCE],
                    }
                ),
                register_year=register_year,
            )
        finally:
            storage.close()

    def _run(self, *, status: str, source_year: int, metadata: InvoiceMetadata) -> dict[str, object]:
        rows = read_reprocess_candidates(
            self.database,
            status=status,
            source_year=source_year,
            limit=100,
        )
        results = {self.documents[str(row["nextcloud_path"])]: metadata for row in rows}
        return run_reprocess_preview(
            self.database,
            status=status,
            source_year=source_year,
            limit=100,
            extractor=_Extractor(results),  # type: ignore[arg-type]
            read_pdf=self.documents.__getitem__,
            scan_pdf=lambda _data, _name: "clamav:m10.5-test",
        )

    def test_review_and_unclassified_are_selected_separately(self) -> None:
        review = read_reprocess_candidates(
            self.database, status="review", source_year=2024, limit=100
        )
        unclassified = read_reprocess_candidates(
            self.database, status="unclassified", source_year=2024, limit=100
        )
        self.assertEqual([row["attachment_hash"] for row in review], [self.hashes["review"]])
        self.assertEqual(
            [row["attachment_hash"] for row in unclassified],
            [self.hashes["unclassified"]],
        )

    def test_confirmed_rows_are_hard_excluded_and_manipulated_status_is_rejected(self) -> None:
        selected = read_reprocess_candidates(
            self.database, status="review", source_year=2024, limit=100
        )
        hashes = {str(row["attachment_hash"]) for row in selected}
        self.assertNotIn(self.hashes["confirmed"], hashes)
        self.assertNotIn(self.hashes["manual"], hashes)
        for status in ("confirmed", "confirmed-manual", "", "review OR 1=1"):
            with self.subTest(status=status), self.assertRaises(ValueError):
                read_reprocess_candidates(
                    self.database, status=status, source_year=2024, limit=100
                )

    def test_source_path_received_and_recognized_years_remain_distinct(self) -> None:
        result = self._run(status="review", source_year=2024, metadata=_metadata())
        years = result["records"][0]["years"]  # type: ignore[index]
        self.assertEqual(
            years,
            {
                "source_year": 2024,
                "source_basis": "register-year",
                "register_year": 2024,
                "path_year": 2025,
                "received_year": 2026,
                "recognized_invoice_year": 2027,
            },
        )

    def test_path_year_is_the_explicit_fallback_not_received_year(self) -> None:
        selected = read_reprocess_candidates(
            self.database, status="unclassified", source_year=2024, limit=100
        )
        self.assertEqual(len(selected), 1)
        payload = self._run(
            status="unclassified",
            source_year=2024,
            metadata=_metadata(invoice_date="2025-01-01", status="review"),
        )
        years = payload["records"][0]["years"]  # type: ignore[index]
        self.assertEqual(years["source_basis"], "path-year")
        self.assertEqual(years["received_year"], 2023)

    def test_preview_keeps_database_pdfs_register_and_audit_unchanged(self) -> None:
        before_database = self.database.read_bytes()
        before_documents = dict(self.documents)
        register_etag = '"m105-register-etag"'
        audit = self.root / "invoice-audit.sqlite3"

        payload = self._run(status="review", source_year=2024, metadata=_metadata())

        self.assertTrue(payload["ok"])
        self.assertEqual(self.database.read_bytes(), before_database)
        self.assertEqual(self.documents, before_documents)
        self.assertEqual(register_etag, '"m105-register-etag"')
        self.assertFalse(audit.exists())
        self.assertEqual(payload["effects"]["nextcloud_register"], "not-accessed")  # type: ignore[index]

        events: list[str] = []
        cache_paths: list[Path] = []
        documents = self.documents

        class Reader:
            def read(inner_self, remote_path: str, **_: object) -> bytes:
                events.append("read")
                return documents[remote_path]

        class Antivirus:
            def __init__(inner_self, *_: object, database: Path, **__: object) -> None:
                cache_paths.append(database)

            def scan_bytes(inner_self, *_: object, **__: object) -> SimpleNamespace:
                events.append("scan")
                return SimpleNamespace(clean=True, scanner_identity="clamav:m10.5-cli-test")

            def close(inner_self) -> None:
                events.append("close")

        tools = SimpleNamespace(
            mail=SimpleNamespace(
                invoices=SimpleNamespace(
                    enabled=True,
                    folder="Assistent/Rechnungen",
                    resource_id="nextcloud-files-main",
                )
            ),
            security=SimpleNamespace(
                antivirus=AntivirusToolSettings(temp_dir=self.root / "forbidden")
            ),
        )
        config = SimpleNamespace(
            runtime=SimpleNamespace(database=self.database),
            invoices=InvoiceConfig(),
        )
        args = SimpleNamespace(
            invoices_command="reprocess",
            status="review",
            source_year=2024,
            limit=100,
            dry_run=True,
        )

        def extracted(*_: object, **__: object) -> InvoiceMetadata:
            events.append("extract")
            return _metadata()

        with (
            patch("mail_agent.cli.load_tool_settings", return_value=tools),
            patch("mail_agent.cli.ReadOnlyInvoicePdfReader", return_value=Reader()),
            patch("mail_agent.cli.HostAntivirus", Antivirus),
            patch("mail_agent.cli.InvoiceExtractor.extract", side_effect=extracted),
            patch("mail_agent.cli.Storage", side_effect=AssertionError("Storage write path opened")),
            patch("mail_agent.cli._sync_invoice_register") as register_sync,
            redirect_stdout(StringIO()),
        ):
            self.assertEqual(_handle_invoices(args, config), 0)  # type: ignore[arg-type]

        self.assertEqual(events, ["read", "scan", "extract", "close"])
        register_sync.assert_not_called()
        self.assertTrue(cache_paths)
        self.assertFalse(cache_paths[0].parent.exists())
        self.assertEqual(self.database.read_bytes(), before_database)
        self.assertEqual(self.documents, before_documents)
        self.assertEqual(register_etag, '"m105-register-etag"')
        self.assertFalse(audit.exists())

    def test_same_input_and_extractor_produce_the_same_digest(self) -> None:
        first_metadata = _metadata()
        second_metadata = _metadata()
        second_metadata.technical.native_duration_ms = 1234.5
        second_metadata.technical.scanner_identity = "clamav:another-runtime-instance"
        first = self._run(status="review", source_year=2024, metadata=first_metadata)
        second = self._run(status="review", source_year=2024, metadata=second_metadata)
        self.assertEqual(
            first["records"][0]["preview_sha256"],  # type: ignore[index]
            second["records"][0]["preview_sha256"],  # type: ignore[index]
        )

    def test_digest_changes_with_pdf_record_extractor_or_proposal(self) -> None:
        base = {
            "pdf_sha256": "a" * 64,
            "current_record": {"id": 1, "status": "review"},
            "extractor_version": "m10.5-a",
            "proposal": {"invoice_number": "A"},
        }
        original = compute_preview_digest(**base)
        variants = (
            {**base, "pdf_sha256": "b" * 64},
            {**base, "current_record": {"id": 1, "status": "changed"}},
            {**base, "extractor_version": "m10.5-b"},
            {**base, "proposal": {"invoice_number": "B"}},
        )
        self.assertEqual(len({original, *(compute_preview_digest(**item) for item in variants)}), 5)

    def test_output_contains_bounded_evidence_but_no_pdf_text_or_issues(self) -> None:
        payload = self._run(status="review", source_year=2024, metadata=_metadata())
        rendered = json.dumps(payload, ensure_ascii=False)
        self.assertNotIn(PRIVATE_EVIDENCE, rendered)
        record = payload["records"][0]  # type: ignore[index]
        evidence = record["fields"]["invoice_number"]["new"]  # type: ignore[index]
        self.assertEqual(evidence["evidence_type"], "labeled-same-line")
        self.assertLessEqual(len(str(evidence["source"])), 80)
        self.assertEqual(record["conflicts"]["old"], ["amount:gross-conflict"])  # type: ignore[index]

    def test_classification_contract_covers_all_four_results(self) -> None:
        blank = {name: "" for name in ("invoice_date", "invoice_number", "supplier", "gross_amount")}
        full = {
            "invoice_date": "2026-01-01",
            "invoice_number": "A",
            "supplier": "Synthetic",
            "gross_amount": "1.00",
        }
        confidence = {name: 0.9 for name in blank}
        cases = (
            (blank, full, "review", "confirmed", [], "improved"),
            (full, full, "review", "review", [], "unchanged"),
            (full, {**full, "invoice_number": ""}, "review", "review", [], "regressed"),
            (full, {**full, "invoice_number": "B"}, "review", "review", [], "still-review"),
        )
        for old, new, old_status, new_status, conflicts, expected in cases:
            with self.subTest(expected=expected):
                self.assertEqual(
                    classify_proposal(
                        old_values=old,
                        new_values=new,
                        old_status=old_status,
                        new_status=new_status,
                        old_confidences=confidence,
                        new_confidences=confidence,
                        conflicts=conflicts,
                    ),
                    expected,
                )

    def test_cli_and_catalog_publish_only_the_read_only_contract(self) -> None:
        parser = build_parser()
        command_args = [
            "invoices",
            "reprocess",
            "--status",
            "review",
            "--source-year",
            "2024",
            "--limit",
            "100",
            "--dry-run",
        ]
        args = parser.parse_args(command_args)
        self.assertTrue(args.dry_run)
        with redirect_stdout(StringIO()), redirect_stderr(StringIO()), self.assertRaises(SystemExit):
            parser.parse_args(
                ["invoices", "reprocess", "--status", "review", "--source-year", "2024"]
            )

        stable_args = assistant_parser().parse_args(command_args)
        self.assertTrue(stable_args.dry_run)
        with patch("personal_assistant.cli_handlers.invoices.subprocess.run") as delegated:
            delegated.return_value.returncode = 0
            self.assertEqual(run_invoice_external(stable_args), 0)
        delegated_command = delegated.call_args.args[0]
        self.assertEqual(
            delegated_command[2:],
            [
                "mail_agent",
                "invoices",
                "reprocess",
                "--status",
                "review",
                "--source-year",
                "2024",
                "--limit",
                "100",
                "--dry-run",
            ],
        )
        definition = next(
            item for item in tool_definitions() if item.id == "assistant.invoices.reprocess-preview"
        )
        self.assertEqual(
            (definition.mode, definition.writes_external_data, definition.approval),
            ("read", False, "none"),
        )
        self.assertEqual(
            definition.command,
            './scripts/assistant.sh invoices reprocess --status "<review|unclassified>" '
            "--source-year <YYYY> --limit 100 --dry-run",
        )

    def test_pdf_hash_mismatch_fails_without_running_scanner_or_extractor(self) -> None:
        calls: list[str] = []
        payload = run_reprocess_preview(
            self.database,
            status="review",
            source_year=2024,
            limit=100,
            extractor=_Extractor({}),  # type: ignore[arg-type]
            read_pdf=lambda _path: b"%PDF-1.7\nchanged",
            scan_pdf=lambda _data, _name: calls.append("scan") or "clamav:test",
        )
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["errors"][0]["error"], "pdf-hash-mismatch")  # type: ignore[index]
        self.assertEqual(calls, [])

        matching = self.documents[
            "Assistent/Rechnungen/2025/02/review.pdf"
        ]

        def blocked_scan(_data: bytes, _name: str) -> str:
            raise RuntimeError("antivirus-gate-blocked")

        blocked = run_reprocess_preview(
            self.database,
            status="review",
            source_year=2024,
            limit=100,
            extractor=_Extractor({}),  # type: ignore[arg-type]
            read_pdf=lambda _path: matching,
            scan_pdf=blocked_scan,
        )
        self.assertEqual(blocked["errors"][0]["error"], "antivirus-gate-blocked")  # type: ignore[index]

    def test_build_preview_refuses_a_confirmed_record_even_without_selector(self) -> None:
        rows = read_reprocess_candidates(
            self.database, status="review", source_year=2024, limit=100
        )
        manipulated = dict(rows[0])
        manipulated["extraction_status"] = "confirmed-manual"
        with self.assertRaises(PermissionError):
            build_preview_record(
                manipulated,
                pdf_sha256=str(manipulated["attachment_hash"]),
                metadata=_metadata(),
            )


if __name__ == "__main__":
    unittest.main()
