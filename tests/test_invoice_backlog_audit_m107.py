from __future__ import annotations

import json
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from mail_agent.cli import _handle_invoices, build_parser
from mail_agent.invoice_backlog_audit import run_invoice_backlog_audit
from mail_agent.storage import Storage
from personal_assistant.cli import parser as assistant_parser
from personal_assistant.cli_handlers.invoices import run_external as run_invoice_external
from personal_assistant.tool_registry import tool_definitions

PRIVATE = "PRIVATE-M107-DOCUMENT-CONTENT-MUST-NOT-LEAVE-AUDIT"


class InvoiceBacklogAuditM107Tests(unittest.TestCase):
    maxDiff = None

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="openclaw-m107-")
        self.root = Path(self.temporary.name)
        self.database = self.root / "mail.sqlite3"
        self._add(
            "legacy",
            extraction_status="",
            register_year=None,
            path="Assistent/Rechnungen/2024/01/legacy.pdf",
        )
        self._add(
            "review-in",
            extraction_status="review",
            register_year=2025,
            path="Assistent/Rechnungen/Pruefen/2025/01/review.pdf",
            tax=1200,
            metadata={"review_reasons": ["amount:gross-missing", PRIVATE]},
        )
        self._add(
            "review-out",
            archive_status="duplicate",
            extraction_status="review",
            register_year=2025,
            path="Assistent/Rechnungen/2024/02/review.pdf",
            invoice_date="2025-02-03",
            invoice_number="SYN-107",
            supplier="Synthetisch GmbH",
            gross=10000,
            net=8000,
            tax=1000,
            metadata={
                "review_reasons": ["amount:arithmetic-mismatch", PRIVATE],
                "technical": {
                    "extractor_version": "m10.4",
                    "ruleset_version": "2026-08-16.1",
                },
            },
        )
        self._add(
            "review-missing-path",
            extraction_status="review",
            register_year=2026,
            path="",
            invoice_date="untrusted-date",
        )
        self._add(
            "manual",
            extraction_status="confirmed-manual",
            register_year=2026,
            path="Assistent/Rechnungen/2026/03/manual.pdf",
            invoice_date="2026-03-04",
            invoice_number="MAN-107",
            supplier="Manuell GmbH",
            gross=-11900,
            net=-10000,
            tax=-1900,
            metadata={
                "technical": {
                    "extractor_version": "m10.4",
                    "ruleset_version": "2026-08-16.1",
                }
            },
        )
        self._add(
            "confirmed",
            extraction_status="confirmed",
            register_year=2026,
            path="Assistent/Rechnungen/2026/04/confirmed.pdf",
            invoice_date="2026-04-05",
            invoice_number="CONF-107",
            supplier="Bestaetigt GmbH",
            gross=11900,
            net=10000,
            tax=1900,
            metadata={
                "technical": {
                    "extractor_version": "m10.4",
                    "ruleset_version": "2026-08-16.1",
                }
            },
        )
        self._add(
            "invalid-metadata",
            archive_status="error",
            extraction_status="error",
            register_year=2026,
            path=f"Privat/{PRIVATE}/2026/error.pdf",
            metadata={
                "technical": {
                    "extractor_version": PRIVATE,
                    "ruleset_version": PRIVATE,
                },
                "review_reasons": [PRIVATE],
            },
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _add(
        self,
        key: str,
        *,
        extraction_status: str,
        register_year: int | None,
        path: str,
        archive_status: str = "uploaded",
        invoice_date: str = "",
        invoice_number: str = "",
        supplier: str = "",
        gross: int | None = None,
        net: int | None = None,
        tax: int | None = None,
        metadata: dict[str, object] | None = None,
    ) -> None:
        storage = Storage(self.database)
        try:
            storage.record_invoice(
                stable_key=f"m107-{key}",
                attachment_hash=(key.encode().hex() + "0" * 64)[:64],
                original_filename=f"{PRIVATE}-{key}.pdf",
                nextcloud_path=path,
                size_bytes=107,
                status=archive_status,
                invoice_date=invoice_date,
                invoice_number=invoice_number,
                supplier=supplier,
                gross_amount_cents=gross,
                net_amount_cents=net,
                tax_amount_cents=tax,
                currency="EUR",
                extraction_status=extraction_status,
                extraction_confidence=0.5,
                extraction_method=PRIVATE,
                extraction_json=json.dumps(metadata or {"issues": [PRIVATE]}),
                register_year=register_year,
            )
        finally:
            storage.close()

    def _audit(self) -> dict[str, object]:
        return run_invoice_backlog_audit(
            self.database,
            invoice_folder="Assistent/Rechnungen",
            review_subfolder="Pruefen",
        )

    def test_aggregates_status_backlog_manual_and_required_fields_exactly(self) -> None:
        result = self._audit()

        self.assertEqual(result["record_count"], 7)
        self.assertEqual(
            result["status_distribution"],
            {
                "archive": {"duplicate": 1, "error": 1, "uploaded": 5},
                "extraction": {
                    "confirmed": 1,
                    "confirmed-manual": 1,
                    "error": 1,
                    "review": 3,
                    "unclassified": 1,
                },
            },
        )
        cohorts = result["cohorts"]
        self.assertEqual(cohorts["unclassified_legacy"]["count"], 1)  # type: ignore[index]
        self.assertEqual(cohorts["review"]["count"], 3)  # type: ignore[index]
        self.assertEqual(cohorts["manual_corrections"]["count"], 1)  # type: ignore[index]
        self.assertEqual(
            cohorts["review"]["missing_required_fields"],  # type: ignore[index]
            {"invoice_date": 1, "invoice_number": 2, "supplier": 2, "gross_amount": 2},
        )
        self.assertEqual(cohorts["review"]["source_years"], {"2025": 2, "2026": 1})  # type: ignore[index]

    def test_plausibility_versions_and_path_deviations_are_aggregated(self) -> None:
        result = self._audit()
        review = result["cohorts"]["review"]  # type: ignore[index]

        self.assertEqual(review["plausibility_errors"]["inconsistent_amount_triples"], 1)  # type: ignore[index]
        self.assertEqual(review["plausibility_errors"]["tax_without_gross"], 1)  # type: ignore[index]
        self.assertEqual(review["plausibility_errors"]["invalid_invoice_date"], 1)  # type: ignore[index]
        self.assertEqual(
            review["plausibility_errors"]["typed_amount_review_reasons"],  # type: ignore[index]
            {"amount:arithmetic-mismatch": 1, "amount:gross-missing": 1},
        )
        self.assertEqual(
            result["extractor_versions"],
            {"invalid-redacted": 1, "legacy-or-missing": 3, "m10.4": 3},
        )
        self.assertEqual(
            result["path_deviations"],
            {
                "review_in_review_subfolder": 1,
                "review_outside_review_subfolder": 1,
                "review_missing_path": 1,
                "stored_outside_invoice_root": 1,
                "register_path_year_mismatch": 1,
                "automatic_move_available": False,
            },
        )

    def test_audit_is_bytewise_read_only_and_never_opens_remote_content(self) -> None:
        before = self.database.read_bytes()
        sidecars_before = sorted(path.name for path in self.root.iterdir())

        result = self._audit()

        self.assertTrue(result["read_only"])
        self.assertEqual(self.database.read_bytes(), before)
        self.assertEqual(sorted(path.name for path in self.root.iterdir()), sidecars_before)
        self.assertEqual(
            result["effects"],
            {
                "sqlite": "unchanged-read-only",
                "nextcloud": "not-accessed",
                "pdf": "not-accessed",
                "audit": "not-accessed",
            },
        )

    def test_output_contains_no_document_values_identifiers_or_paths(self) -> None:
        rendered = json.dumps(self._audit(), ensure_ascii=False)

        self.assertNotIn(PRIVATE, rendered)
        self.assertNotIn("SYN-107", rendered)
        self.assertNotIn("Assistent/Rechnungen", rendered)
        self.assertEqual(
            self._audit()["privacy"],
            {
                "document_content_included": False,
                "identifiers_included": False,
                "paths_included": False,
            },
        )

    def test_cli_routes_audit_through_the_exact_registered_command(self) -> None:
        self.assertEqual(build_parser().parse_args(["invoices", "audit"]).invoices_command, "audit")
        parsed = assistant_parser().parse_args(["invoices", "audit"])
        self.assertEqual(parsed.invoices_command, "audit")
        calls: list[list[str]] = []

        with patch(
            "personal_assistant.cli_handlers.invoices.subprocess.run",
            side_effect=lambda command, **_kwargs: calls.append(command)
            or SimpleNamespace(returncode=0),
        ):
            self.assertEqual(run_invoice_external(parsed), 0)

        self.assertEqual(calls[0][-2:], ["invoices", "audit"])

    def test_registered_audit_is_read_only_and_exposes_no_move_tool(self) -> None:
        tools = {item.id: item for item in tool_definitions()}
        audit = tools["assistant.invoices.audit"]

        self.assertEqual(audit.command, "./scripts/assistant.sh invoices audit")
        self.assertEqual(audit.mode, "read")
        self.assertFalse(audit.writes_external_data)
        self.assertEqual(audit.approval, "none")
        self.assertNotIn("assistant.invoices.move", tools)

    def test_handler_uses_read_only_audit_before_storage_and_apply_stays_approved(self) -> None:
        config = SimpleNamespace(
            runtime=SimpleNamespace(database=self.database),
            invoices=SimpleNamespace(review_subfolder="Pruefen"),
        )
        tool = SimpleNamespace(enabled=True, folder="Assistent/Rechnungen")
        output = StringIO()
        with patch(
            "mail_agent.cli.load_tool_settings",
            return_value=SimpleNamespace(mail=SimpleNamespace(invoices=tool)),
        ), patch(
            "mail_agent.invoice_backlog_audit.run_invoice_backlog_audit",
            return_value={"ok": True, "read_only": True},
        ) as audit, patch(
            "mail_agent.cli.Storage", side_effect=AssertionError("must stay read-only")
        ), redirect_stdout(output):
            code = _handle_invoices(SimpleNamespace(invoices_command="audit"), config)
        self.assertEqual(code, 0)
        audit.assert_called_once_with(
            self.database,
            invoice_folder="Assistent/Rechnungen",
            review_subfolder="Pruefen",
        )
        with self.assertRaisesRegex(PermissionError, "ausdruecklichem Nutzerauftrag"):
            _handle_invoices(
                SimpleNamespace(invoices_command="reprocess-apply", yes=False),
                config,
            )

    def test_skill_orders_status_audit_preview_and_explicit_single_apply(self) -> None:
        root = Path(__file__).resolve().parents[1]
        skill = (root / "skills/personal-assistant/SKILL.md").read_text(encoding="utf-8")
        workflow = skill.split("## Invoice backlog workflow", 1)[1]

        positions = [
            workflow.index("`invoices status`"),
            workflow.index("`invoices audit`"),
            workflow.index("`invoices\nreprocess ... --dry-run`"),
            workflow.index("`invoices reprocess-apply ... --yes`"),
        ]
        self.assertEqual(positions, sorted(positions))
        self.assertIn("Never add\n`--yes` autonomously", workflow)
        self.assertIn("memory, filename, mail text or Ollama", workflow)
        self.assertIn("do not move them", workflow)


if __name__ == "__main__":
    unittest.main()
