from __future__ import annotations

import argparse
import hashlib
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from mail_agent.cli import _handle_invoices, build_parser
from mail_agent.config import load_config
from mail_agent.invoice_extract import FieldValue, InvoiceMetadata
from mail_agent.models import OperationResult
from mail_agent.storage import Storage
from personal_assistant.config import SelfManagementConfig
from personal_assistant.connectors.nextcloud.client import NextcloudError
from personal_assistant.connectors.nextcloud.files import NextcloudFiles
from personal_assistant.service import PersonalAssistant
from personal_assistant.tool_registry import build_tool_registry, static_tool_catalog, tool_definitions
from personal_assistant.tool_settings import InvoiceToolSettings, MailToolSettings, ToolSettings


class _RegisterClient:
    def __init__(self, status: int) -> None:
        self.username = "synthetic"
        self.status = status
        self.calls: list[tuple[str, str, dict[str, object]]] = []

    def request(self, method: str, path: str, **kwargs: object) -> SimpleNamespace:
        self.calls.append((method, path, kwargs))
        return SimpleNamespace(status=self.status, reason="Synthetic response", data=b"")


class InvoiceEffectContractM101Tests(unittest.TestCase):
    maxDiff = None

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="openclaw-m101-")
        self.root = Path(self.temporary.name)
        config_source = Path(__file__).parents[1] / "mail_agent/config.example.toml"
        config_text = config_source.read_text(encoding="utf-8").replace(
            "mail_agent/data/", str(self.root / "mail-data") + "/"
        )
        config_text = config_text.replace(
            'rules_file = "mail_agent/rules.toml"',
            f'rules_file = "{self.root / "rules.toml"}"',
        )
        self.config_path = self.root / "config.toml"
        self.config_path.write_text(config_text, encoding="utf-8")
        (self.root / "rules.toml").write_text(
            "[spam]\naddresses=[]\ndomains=[]\nsender_names=[]\nsubject_phrases=[]\n"
            "[important]\naddresses=[]\ndomains=[]\n"
            "[routine]\naddresses=[]\ndomains=[]\n",
            encoding="utf-8",
        )
        self.config = load_config(self.config_path)
        tools_source = Path(__file__).parents[1] / "personal_assistant/tools.example.toml"
        self.tools_path = self.root / "tools.toml"
        self.tools_path.write_text(tools_source.read_text(encoding="utf-8"), encoding="utf-8")
        self.environment = patch.dict(
            "os.environ", {"OPENCLAW_TOOLS_CONFIG": str(self.tools_path)}, clear=False
        )
        self.environment.start()
        storage = Storage(self.config.runtime.database)
        try:
            storage.record_invoice(
                stable_key="synthetic-mail",
                attachment_hash="a" * 64,
                original_filename="synthetic.pdf",
                nextcloud_path="Assistent/Rechnungen/2026/01/synthetic.pdf",
                size_bytes=128,
                status="uploaded",
                received_date="2026-01-02",
            )
        finally:
            storage.close()

    def tearDown(self) -> None:
        self.environment.stop()
        self.temporary.cleanup()

    def _invoice_row(self) -> dict[str, object]:
        storage = Storage(self.config.runtime.database)
        try:
            row = storage.get_invoice("a" * 64)
            self.assertIsNotNone(row)
            return dict(row)  # type: ignore[arg-type]
        finally:
            storage.close()

    def test_catalog_and_live_capabilities_report_the_real_effects(self) -> None:
        expected = {
            "assistant.invoices.export": ("read", False, "none", "--dry-run"),
            "assistant.invoices.export-nextcloud": (
                "write",
                True,
                "explicit-user-managed-register-replace",
                "--yes",
            ),
            "assistant.invoices.backfill-preview": ("read", False, "none", "--dry-run"),
            "assistant.invoices.backfill": (
                "write",
                True,
                "explicit-user-backfill-and-managed-register-replace",
                "--yes",
            ),
            "assistant.invoices.correct": (
                "write",
                True,
                "explicit-user-correction-and-managed-register-replace",
                "--yes",
            ),
        }
        definitions = {item.id: item for item in tool_definitions()}
        static = {item["id"]: item for item in static_tool_catalog()["tools"]}
        settings = ToolSettings(
            path=self.tools_path,
            mail=MailToolSettings(invoices=InvoiceToolSettings(enabled=True)),
        )
        live = {item.id: item for item in build_tool_registry(settings)}
        assistant = object.__new__(PersonalAssistant)
        assistant.registry = SimpleNamespace(list=lambda: [])
        assistant.settings = SimpleNamespace(list_safe=lambda: {})
        assistant.config = SimpleNamespace(self_management=SelfManagementConfig(enabled=False))
        assistant.tools = lambda: list(live.values())
        capabilities = {item.id: item for item in PersonalAssistant.capabilities(assistant)["tools"]}

        for tool_id, (mode, external, approval, option) in expected.items():
            definition = definitions[tool_id]
            self.assertEqual(
                (definition.mode, definition.writes_external_data, definition.approval),
                (mode, external, approval),
            )
            self.assertIn(option, definition.command)
            self.assertEqual(static[tool_id]["mode"], mode)
            self.assertEqual(static[tool_id]["writes_external_data"], external)
            self.assertEqual(live[tool_id].mode, mode)
            self.assertEqual(live[tool_id].approval, approval)
            self.assertEqual(capabilities[tool_id].mode, mode)
            self.assertEqual(capabilities[tool_id].writes_external_data, external)

        for definition in definitions.values():
            if definition.mode in {"read", "local-write"}:
                self.assertFalse(definition.writes_external_data, definition.id)

    def test_old_unapproved_forms_leave_sqlite_and_register_untouched(self) -> None:
        arguments = (
            argparse.Namespace(
                invoices_command="export",
                year=2026,
                filename="",
                nextcloud=False,
                dry_run=False,
                yes=False,
            ),
            argparse.Namespace(
                invoices_command="backfill", year=2026, limit=10, dry_run=False, yes=False
            ),
            argparse.Namespace(
                invoices_command="correct",
                attachment_hash="a" * 64,
                invoice_date="2026-01-03",
                invoice_number="SYN-1",
                supplier="Synthetic GmbH",
                category="Test",
                gross="1,00",
                net="",
                tax="",
                currency="EUR",
                due_date="",
                yes=False,
            ),
        )
        before = self._invoice_row()
        with patch("mail_agent.cli._sync_invoice_register") as sync, patch(
            "mail_agent.cli.PersonalAssistantActionBridge"
        ) as bridge:
            for args in arguments:
                with self.subTest(command=args.invoices_command):
                    with self.assertRaises(PermissionError):
                        _handle_invoices(args, self.config)
                    self.assertEqual(self._invoice_row(), before)
            sync.assert_not_called()
            bridge.assert_not_called()

        disabled = SimpleNamespace(
            mail=SimpleNamespace(invoices=InvoiceToolSettings(enabled=False))
        )
        approved_correction = argparse.Namespace(
            invoices_command="correct",
            attachment_hash="a" * 64,
            invoice_date="2026-01-03",
            invoice_number="SYN-1",
            supplier="Synthetic GmbH",
            category="Test",
            gross="1,00",
            net="",
            tax="",
            currency="EUR",
            due_date="",
            yes=True,
        )
        with (
            patch("mail_agent.cli.load_tool_settings", return_value=disabled),
            self.assertRaisesRegex(PermissionError, "deaktiviert"),
        ):
            _handle_invoices(approved_correction, self.config)
        self.assertEqual(self._invoice_row(), before)

    def test_export_preview_renders_without_opening_nextcloud_or_changing_sqlite(self) -> None:
        events: list[tuple[str, object]] = []

        class PreviewBridge:
            def __init__(inner_self, *, dry_run: bool = False, **_: object) -> None:
                events.append(("bridge-dry-run", dry_run))

            def sync_invoice_register(inner_self, **kwargs: object) -> OperationResult:
                events.append(("preview", kwargs["remote_path"]))
                return OperationResult(
                    True,
                    "would-sync-invoice-register",
                    "synthetic preview",
                    path=str(kwargs["remote_path"]),
                )

        before = self._invoice_row()
        args = argparse.Namespace(
            invoices_command="export",
            year=2026,
            filename="",
            nextcloud=False,
            dry_run=True,
            yes=False,
        )
        with patch("mail_agent.cli.PersonalAssistantActionBridge", PreviewBridge), redirect_stdout(
            StringIO()
        ):
            self.assertEqual(_handle_invoices(args, self.config), 0)
        self.assertEqual(events[0], ("bridge-dry-run", True))
        self.assertEqual(events[1][0], "preview")
        self.assertEqual(self._invoice_row(), before)

    def test_backfill_preview_reads_but_writes_neither_sqlite_nor_register(self) -> None:
        events: list[str] = []

        class ReadBridge:
            def __init__(inner_self, *, dry_run: bool = False, **_: object) -> None:
                self.assertFalse(dry_run)

            def read_invoice_pdf(inner_self, **_: object) -> bytes:
                events.append("read-pdf")
                return b"%PDF-1.4\nsynthetic"

            def sync_invoice_register(inner_self, **_: object) -> OperationResult:
                raise AssertionError("Backfill-Vorschau darf das Register nicht synchronisieren")

        class Antivirus:
            def __init__(inner_self, *_: object, **__: object) -> None:
                pass

            def scan_bytes(inner_self, *_: object, **__: object) -> SimpleNamespace:
                events.append("scan")
                return SimpleNamespace(
                    infected=False,
                    error=False,
                    signature="",
                    detail="",
                    status="clean",
                    scanner_identity="clamav:m101-test",
                )

            def close(inner_self) -> None:
                events.append("close")

        metadata = InvoiceMetadata(
            invoice_date=FieldValue("2026-01-03", 0.95, "synthetic"),
            invoice_number=FieldValue("SYN-1", 0.95, "synthetic"),
            supplier=FieldValue("Synthetic GmbH", 0.95, "synthetic"),
            gross_amount=FieldValue("1.00", 0.95, "synthetic"),
            status="confirmed",
            confidence=0.95,
            method="text",
        )
        before = self._invoice_row()
        args = argparse.Namespace(
            invoices_command="backfill", year=2026, limit=10, dry_run=True, yes=False
        )
        with patch("mail_agent.cli.PersonalAssistantActionBridge", ReadBridge), patch(
            "mail_agent.cli.HostAntivirus", Antivirus
        ), patch(
            "mail_agent.cli.InvoiceExtractor.extract", return_value=metadata
        ) as extract, redirect_stdout(StringIO()):
            self.assertEqual(_handle_invoices(args, self.config), 0)
        self.assertEqual(events, ["read-pdf", "scan", "close"])
        self.assertEqual(extract.call_args.kwargs["scanner_identity"], "clamav:m101-test")
        self.assertEqual(self._invoice_row(), before)

    def test_parser_rejects_conflicting_preview_and_write_approval(self) -> None:
        parser = build_parser()
        for command in ("export", "backfill"):
            with (
                self.subTest(command=command),
                redirect_stdout(StringIO()),
                self.assertRaises(SystemExit),
            ):
                parser.parse_args(
                    ["invoices", command, "--year", "2026", "--dry-run", "--yes"]
                )

    def _register_files(
        self, status: int, *, etag: str = '"synthetic-etag"'
    ) -> tuple[NextcloudFiles, _RegisterClient]:
        client = _RegisterClient(status)
        files = NextcloudFiles(SimpleNamespace(), client)  # type: ignore[arg-type]
        files.ensure_folder = lambda _: None  # type: ignore[method-assign]
        files._etag = lambda _: etag  # type: ignore[method-assign]
        return files, client

    @staticmethod
    def _valid_register() -> bytes:
        return (NextcloudFiles._MANAGED_REGISTER_HEADER + "\r\n").encode("utf-8-sig")

    def test_managed_register_uses_current_etag_for_conditional_replace(self) -> None:
        files, client = self._register_files(204)
        data = self._valid_register()
        files.replace_managed_invoice_register(
            "Assistent/Rechnungen/2026/Rechnungen_2026.csv",
            data,
            content_type="text/csv; charset=utf-8",
            expected_sha256=hashlib.sha256(data).hexdigest(),
        )
        method, _, kwargs = client.calls[-1]
        self.assertEqual(method, "PUT")
        self.assertEqual(kwargs["headers"]["If-Match"], '"synthetic-etag"')
        self.assertNotIn("If-None-Match", kwargs["headers"])

    def test_managed_register_etag_conflict_is_visible(self) -> None:
        files, _ = self._register_files(412)
        data = self._valid_register()
        with self.assertRaisesRegex(NextcloudError, "parallel geaendert"):
            files.replace_managed_invoice_register(
                "Assistent/Rechnungen/2026/Rechnungen_2026.csv",
                data,
                content_type="text/csv; charset=utf-8",
                expected_sha256=hashlib.sha256(data).hexdigest(),
            )

    def test_managed_register_sha_mismatch_fails_before_remote_write(self) -> None:
        files, client = self._register_files(204)
        with self.assertRaisesRegex(ValueError, "SHA-256"):
            files.replace_managed_invoice_register(
                "Assistent/Rechnungen/2026/Rechnungen_2026.csv",
                self._valid_register(),
                content_type="text/csv; charset=utf-8",
                expected_sha256="0" * 64,
            )
        self.assertEqual(client.calls, [])

    def test_managed_register_schema_error_fails_before_remote_write(self) -> None:
        files, client = self._register_files(204)
        data = b"wrong;schema\r\n"
        with self.assertRaisesRegex(ValueError, "CSV-Schema"):
            files.replace_managed_invoice_register(
                "Assistent/Rechnungen/2026/Rechnungen_2026.csv",
                data,
                content_type="text/csv; charset=utf-8",
                expected_sha256=hashlib.sha256(data).hexdigest(),
            )
        self.assertEqual(client.calls, [])

    def test_managed_register_remote_failure_is_not_reported_as_success(self) -> None:
        files, client = self._register_files(500, etag="")
        data = self._valid_register()
        with self.assertRaisesRegex(NextcloudError, "HTTP 500"):
            files.replace_managed_invoice_register(
                "Assistent/Rechnungen/2026/Rechnungen_2026.csv",
                data,
                content_type="text/csv; charset=utf-8",
                expected_sha256=hashlib.sha256(data).hexdigest(),
            )
        self.assertEqual(client.calls[-1][2]["headers"]["If-None-Match"], "*")


if __name__ == "__main__":
    unittest.main()
