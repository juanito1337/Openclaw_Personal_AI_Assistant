from __future__ import annotations

import copy
import hashlib
import json
import sqlite3
import tempfile
import threading
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from mail_agent.cli import _handle_invoices, build_parser
from mail_agent.invoice_extract import (
    ExtractionTechnicalMetadata,
    FieldCandidate,
    FieldValue,
    InvoiceMetadata,
)
from mail_agent.invoice_reprocess import build_preview_record, read_reprocess_candidates
from mail_agent.invoice_reprocess_apply import APPROVAL_LABEL, run_reprocess_apply
from mail_agent.storage import SCHEMA_VERSION, Storage
from personal_assistant.cli import parser as assistant_parser
from personal_assistant.cli_handlers.invoices import run_external as run_invoice_external
from personal_assistant.tool_registry import tool_definitions

PRIVATE_CONTENT = "PRIVATE-M106-PDF-EVIDENCE-MUST-NOT-ENTER-AUDIT"


class _Extractor:
    def __init__(
        self,
        metadata: InvoiceMetadata,
        barrier: threading.Barrier | None = None,
    ) -> None:
        self.metadata = metadata
        self.barrier = barrier

    def extract(self, *_: object, **__: object) -> InvoiceMetadata:
        if self.barrier is not None:
            self.barrier.wait(timeout=5)
        return copy.deepcopy(self.metadata)


def _metadata(
    *,
    status: str = "confirmed",
    version: str = "m10.6-test",
    review_reasons: list[str] | None = None,
    net: str = "100.00",
    tax: str = "19.00",
    supplier: str = "M106 Beispiel GmbH",
) -> InvoiceMetadata:
    candidate = FieldCandidate(
        field="invoice_number",
        role="invoice-number",
        raw_value="NEW-106",
        normalized_value="NEW-106",
        source="native",
        evidence_type="labeled-same-line",
        evidence=PRIVATE_CONTENT,
        confidence=0.98,
    )
    return InvoiceMetadata(
        invoice_date=FieldValue("2027-02-03", 0.98, PRIVATE_CONTENT),
        invoice_number=FieldValue("NEW-106", 0.98, PRIVATE_CONTENT),
        supplier=FieldValue(supplier, 0.97, PRIVATE_CONTENT),
        category=FieldValue("Test", 0.9, PRIVATE_CONTENT),
        gross_amount=FieldValue("119.00", 0.99, PRIVATE_CONTENT),
        net_amount=FieldValue(net, 0.97, PRIVATE_CONTENT),
        tax_amount=FieldValue(tax, 0.97, PRIVATE_CONTENT),
        currency=FieldValue("EUR", 0.99, PRIVATE_CONTENT),
        status=status,
        confidence=0.97,
        method="text",
        issues=[PRIVATE_CONTENT],
        review_reasons=list(review_reasons or []),
        field_candidates=[candidate],
        technical=ExtractionTechnicalMetadata(
            extractor_version=version,
            ruleset_version="m10.6-rules-test",
        ),
    )


class InvoiceReprocessApplyM106Tests(unittest.TestCase):
    maxDiff = None

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="openclaw-m106-")
        self.root = Path(self.temporary.name)
        self.database = self.root / "mail.sqlite3"
        self.pdf = b"%PDF-1.7\nsynthetic-m106\n" + PRIVATE_CONTENT.encode()
        self.digest = hashlib.sha256(self.pdf).hexdigest()
        self.remote_path = "Assistent/Rechnungen/2024/02/m106.pdf"
        storage = Storage(self.database)
        try:
            storage.record_invoice(
                stable_key="m106-review",
                attachment_hash=self.digest,
                original_filename="m106.pdf",
                nextcloud_path=self.remote_path,
                size_bytes=len(self.pdf),
                status="uploaded",
                received_date="2024-02-04",
                invoice_number="OLD-106",
                gross_amount_cents=9900,
                currency="EUR",
                extraction_status="review",
                extraction_confidence=0.6,
                extraction_method="legacy-text",
                extraction_json=json.dumps({"evidence": PRIVATE_CONTENT}),
                register_year=2024,
            )
        finally:
            storage.close()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _preview_sha256(self, metadata: InvoiceMetadata | None = None) -> str:
        rows = read_reprocess_candidates(
            self.database,
            status="review",
            source_year=2024,
            limit=1,
        )
        self.assertEqual(len(rows), 1)
        return str(
            build_preview_record(
                rows[0],
                pdf_sha256=self.digest,
                metadata=metadata or _metadata(),
            )["preview_sha256"]
        )

    def _run(
        self,
        preview_sha256: str,
        *,
        metadata: InvoiceMetadata | None = None,
        data: bytes | None = None,
        sync: object | None = None,
        barrier: threading.Barrier | None = None,
    ) -> dict[str, object]:
        callback = sync or (
            lambda _storage, year: {
                "ok": True,
                "status": "invoice-register-synced",
                "year": year,
                "sha256": "a" * 64,
                "path": f"Assistent/Rechnungen/{year}/Rechnungen_{year}.csv",
            }
        )
        return run_reprocess_apply(
            self.database,
            attachment_hash=self.digest,
            expected_preview_sha256=preview_sha256,
            extractor=_Extractor(metadata or _metadata(), barrier),  # type: ignore[arg-type]
            read_pdf=lambda _path: self.pdf if data is None else data,
            scan_pdf=lambda _data, _name: "clamav:m10.6-test",
            sync_register=callback,  # type: ignore[arg-type]
        )

    def _invoice(self) -> dict[str, object]:
        storage = Storage(self.database)
        try:
            row = storage.get_invoice(self.digest)
            self.assertIsNotNone(row)
            return dict(row)  # type: ignore[arg-type]
        finally:
            storage.close()

    def _audit(self) -> list[dict[str, object]]:
        storage = Storage(self.database)
        try:
            return [
                dict(row)
                for row in storage.connection.execute(
                    "SELECT * FROM invoice_reprocess_audit ORDER BY created_at"
                )
            ]
        finally:
            storage.close()

    def test_single_apply_changes_one_row_syncs_both_years_and_keeps_pdf_unchanged(self) -> None:
        preview_sha256 = self._preview_sha256()
        pdf_before = bytes(self.pdf)
        calls: list[int] = []

        result = self._run(
            preview_sha256,
            sync=lambda _storage, year: calls.append(year)
            or {"ok": True, "status": "invoice-register-synced", "sha256": "b" * 64},
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "reprocess-applied")
        self.assertEqual(calls, [2024, 2027])
        invoice = self._invoice()
        self.assertEqual(invoice["extraction_status"], "confirmed")
        self.assertEqual(invoice["invoice_number"], "NEW-106")
        self.assertEqual(invoice["gross_amount_cents"], 11900)
        self.assertEqual(invoice["register_year"], 2027)
        self.assertEqual(self.pdf, pdf_before)
        audit = self._audit()
        self.assertEqual(len(audit), 1)
        self.assertEqual(audit[0]["approval_label"], APPROVAL_LABEL)
        self.assertEqual(audit[0]["result_status"], "completed")
        rendered_audit = json.dumps(audit, ensure_ascii=False)
        self.assertNotIn(PRIVATE_CONTENT, rendered_audit)
        self.assertNotIn(self.remote_path, rendered_audit)

    def test_wrong_pdf_or_preview_digest_is_read_only(self) -> None:
        preview_sha256 = self._preview_sha256()
        before = self.database.read_bytes()
        calls: list[int] = []
        wrong_hash = run_reprocess_apply(
            self.database,
            attachment_hash="e" * 64,
            expected_preview_sha256=preview_sha256,
            extractor=_Extractor(_metadata()),  # type: ignore[arg-type]
            read_pdf=lambda _path: self.pdf,
            scan_pdf=lambda _data, _name: "clamav:m10.6-test",
            sync_register=lambda _storage, year: calls.append(year) or {"ok": True},
        )
        self.assertEqual(wrong_hash["error"], "invoice-not-found")
        self.assertEqual(self.database.read_bytes(), before)
        bad_pdf = self._run(
            preview_sha256,
            data=b"%PDF-1.7\nchanged",
            sync=lambda _storage, year: calls.append(year) or {"ok": True},
        )
        self.assertEqual(bad_pdf["error"], "pdf-hash-mismatch")
        self.assertEqual(self.database.read_bytes(), before)
        wrong_preview = self._run(
            "f" * 64,
            sync=lambda _storage, year: calls.append(year) or {"ok": True},
        )
        self.assertEqual(wrong_preview["error"], "preview-drift")
        self.assertEqual(self.database.read_bytes(), before)

        def blocked_scan(_data: bytes, _name: str) -> str:
            raise RuntimeError("antivirus-gate-blocked")

        blocked = run_reprocess_apply(
            self.database,
            attachment_hash=self.digest,
            expected_preview_sha256=preview_sha256,
            extractor=_Extractor(_metadata()),  # type: ignore[arg-type]
            read_pdf=lambda _path: self.pdf,
            scan_pdf=blocked_scan,
            sync_register=lambda _storage, year: calls.append(year) or {"ok": True},
        )
        self.assertEqual(blocked["error"], "antivirus-gate-blocked")
        self.assertEqual(self.database.read_bytes(), before)
        self.assertEqual(calls, [])

    def test_record_drift_and_protected_status_are_rejected_without_apply(self) -> None:
        preview_sha256 = self._preview_sha256()
        storage = Storage(self.database)
        try:
            storage.connection.execute(
                "UPDATE invoices SET supplier='Changed after preview' WHERE attachment_hash=?",
                (self.digest,),
            )
            storage.connection.commit()
        finally:
            storage.close()
        drifted = self._run(preview_sha256)
        self.assertEqual(drifted["error"], "preview-drift")
        self.assertEqual(self._audit(), [])

        storage = Storage(self.database)
        try:
            storage.connection.execute(
                "UPDATE invoices SET extraction_status='confirmed-manual' WHERE attachment_hash=?",
                (self.digest,),
            )
            storage.connection.commit()
        finally:
            storage.close()
        protected = self._run(preview_sha256)
        self.assertEqual(protected["error"], "protected-invoice-status")
        self.assertEqual(self._audit(), [])

    def test_regression_review_and_arithmetic_conflict_are_rejected(self) -> None:
        cases = (
            (_metadata(status="review"), "proposal-not-confirmed"),
            (_metadata(review_reasons=["amount:gross-conflict"]), "proposal-not-improved"),
            (_metadata(net="100.00", tax="30.00"), "amount-arithmetic-invalid"),
            (_metadata(supplier="X" * 301), "field-value-too-long"),
        )
        for metadata, expected in cases:
            with self.subTest(expected=expected):
                preview_sha256 = self._preview_sha256(metadata)
                result = self._run(preview_sha256, metadata=metadata)
                self.assertEqual(result["error"], expected)
                self.assertEqual(self._invoice()["extraction_status"], "review")
                self.assertEqual(self._audit(), [])

    def test_register_failure_is_visible_and_retry_resumes_idempotently(self) -> None:
        preview_sha256 = self._preview_sha256()
        failing_calls: list[int] = []

        def fail_on_new_year(_storage: Storage, year: int) -> dict[str, object]:
            failing_calls.append(year)
            return {
                "ok": year == 2024,
                "status": "invoice-register-synced" if year == 2024 else "etag-conflict",
            }

        failed = self._run(preview_sha256, sync=fail_on_new_year)
        self.assertFalse(failed["ok"])
        self.assertEqual(failed["status"], "local-applied-register-failed")
        self.assertEqual(failed["error"], "register-conflict")
        self.assertTrue(failed["local_applied"])
        self.assertTrue(failed["retry_safe"])
        invoice_after_failure = self._invoice()
        self.assertEqual(self._audit()[0]["result_status"], "register-failed")

        def unavailable(_storage: Storage, _year: int) -> dict[str, object]:
            raise RuntimeError("PRIVATE-REMOTE-RESPONSE-MUST-NOT-LEAK")

        unavailable_result = self._run(preview_sha256, sync=unavailable)
        self.assertFalse(unavailable_result["ok"])
        self.assertEqual(unavailable_result["error"], "register-sync-failed")
        self.assertNotIn("PRIVATE-REMOTE", json.dumps(unavailable_result))

        recovered_calls: list[int] = []
        recovered = self._run(
            preview_sha256,
            sync=lambda _storage, year: recovered_calls.append(year)
            or {"ok": True, "status": "invoice-register-synced"},
        )
        self.assertTrue(recovered["ok"])
        self.assertEqual(recovered["status"], "reprocess-already-applied")
        self.assertFalse(recovered["local_applied"])
        self.assertTrue(recovered["idempotent"])
        self.assertEqual(self._invoice(), invoice_after_failure)
        audit = self._audit()
        self.assertEqual(len(audit), 1)
        self.assertEqual(audit[0]["attempt_count"], 3)
        self.assertEqual(audit[0]["result_status"], "completed")
        self.assertEqual(failing_calls, [2024, 2027])
        self.assertEqual(recovered_calls, [2024, 2027])

    def test_completed_apply_remains_one_operation_and_revalidates_registers(self) -> None:
        preview_sha256 = self._preview_sha256()
        first = self._run(preview_sha256)
        invoice = self._invoice()
        calls: list[int] = []
        second = self._run(
            preview_sha256,
            sync=lambda _storage, year: calls.append(year)
            or {"ok": True, "status": "already-current"},
        )
        self.assertTrue(first["ok"])
        self.assertTrue(second["ok"])
        self.assertEqual(second["status"], "reprocess-already-applied")
        self.assertEqual(self._invoice(), invoice)
        self.assertEqual(len(self._audit()), 1)
        self.assertEqual(calls, [2024, 2027])

    def test_concurrent_apply_has_one_local_writer_and_one_register_claim(self) -> None:
        preview_sha256 = self._preview_sha256()
        barrier = threading.Barrier(2)
        register_entered = threading.Event()
        worker_finished = threading.Event()
        release_register = threading.Event()
        register_calls: list[int] = []
        results: list[dict[str, object]] = []

        def sync(_storage: Storage, year: int) -> dict[str, object]:
            register_calls.append(year)
            register_entered.set()
            release_register.wait(timeout=5)
            return {"ok": True, "status": "invoice-register-synced"}

        def worker() -> None:
            results.append(
                self._run(
                    preview_sha256,
                    sync=sync,
                    barrier=barrier,
                )
            )
            worker_finished.set()

        threads = [threading.Thread(target=worker) for _ in range(2)]
        for thread in threads:
            thread.start()
        self.assertTrue(register_entered.wait(timeout=5))
        self.assertTrue(worker_finished.wait(timeout=5))
        release_register.set()
        for thread in threads:
            thread.join(timeout=10)
        self.assertFalse(any(thread.is_alive() for thread in threads))
        self.assertEqual(len(results), 2)
        self.assertEqual(sum(bool(item["local_applied"]) for item in results), 1)
        self.assertEqual(len(self._audit()), 1)
        self.assertIn("register-sync-in-progress", {str(item["status"]) for item in results})
        self.assertEqual(register_calls, [2024, 2027])

    def test_schema_three_migration_is_additive_repeatable_and_preserves_invoice(self) -> None:
        before = self._invoice()
        connection = sqlite3.connect(self.database)
        try:
            connection.execute("DROP TABLE invoice_reprocess_audit")
            connection.execute("PRAGMA user_version=3")
            connection.commit()
        finally:
            connection.close()

        for _ in range(2):
            storage = Storage(self.database)
            try:
                self.assertEqual(
                    storage.connection.execute("PRAGMA user_version").fetchone()[0],
                    SCHEMA_VERSION,
                )
                self.assertEqual(storage.connection.execute("PRAGMA quick_check").fetchone()[0], "ok")
                self.assertEqual(dict(storage.get_invoice(self.digest)), before)  # type: ignore[arg-type]
                self.assertEqual(
                    storage.connection.execute(
                        "SELECT COUNT(*) FROM invoice_reprocess_audit"
                    ).fetchone()[0],
                    0,
                )
            finally:
                storage.close()

    def test_cli_requires_yes_and_publishes_exact_typed_contract(self) -> None:
        command = [
            "invoices",
            "reprocess-apply",
            "--hash",
            self.digest,
            "--expected-preview-sha256",
            "a" * 64,
        ]
        internal_args = build_parser().parse_args(command)
        config = SimpleNamespace(runtime=SimpleNamespace(database=self.database))
        with (
            patch("mail_agent.cli.Storage", side_effect=AssertionError("database opened")),
            patch("mail_agent.cli.load_tool_settings", side_effect=AssertionError("tools loaded")),
            redirect_stdout(StringIO()),
            self.assertRaises(PermissionError),
        ):
            _handle_invoices(internal_args, config)  # type: ignore[arg-type]

        stable_args = assistant_parser().parse_args([*command, "--yes"])
        with patch("personal_assistant.cli_handlers.invoices.subprocess.run") as delegated:
            delegated.return_value.returncode = 0
            self.assertEqual(run_invoice_external(stable_args), 0)
        self.assertEqual(
            delegated.call_args.args[0][2:],
            ["mail_agent", *command, "--yes"],
        )
        definition = next(
            item for item in tool_definitions() if item.id == "assistant.invoices.reprocess-apply"
        )
        self.assertEqual(
            (definition.mode, definition.writes_external_data, definition.approval),
            ("write", True, APPROVAL_LABEL),
        )


if __name__ == "__main__":
    unittest.main()
