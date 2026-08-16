from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from mail_agent.app import MailAgent, RunSummary
from mail_agent.config import load_config
from mail_agent.invoices import InvoiceManager
from mail_agent.models import Classification, Envelope, InvoiceSignal, OperationResult
from mail_agent.parser import parse_eml
from mail_agent.storage import Storage
from personal_assistant.antivirus import HostAntivirus
from personal_assistant.tool_settings import AntivirusToolSettings, InvoiceToolSettings

FAKE_SCANNER = r'''#!/bin/sh
for arg in "$@"; do last="$arg"; done
case " $* " in
  *" --version "*) echo "ClamAV 1.4.5/99999/Test"; exit 0 ;;
esac
if grep -q 'MALWARE_TEST' "$last"; then
  echo "$last: Unit-Test-Signature FOUND"
  exit 1
fi
if grep -q 'SCAN_ERROR_TEST' "$last"; then
  echo "$last: scanner failure ERROR" >&2
  exit 2
fi
echo "$last: OK"
exit 0
'''


class FakeBridge:
    def __init__(self) -> None:
        self.uploads = []

    @staticmethod
    def health(*, resource_id="nextcloud-files-main"):
        return OperationResult(True, "ok")

    def archive_invoice(self, **kwargs):
        self.uploads.append(kwargs)
        return OperationResult(True, "invoice-archived", path=kwargs["remote_path"])


class AntivirusToolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.scanner_path = self.root / "fake-clamdscan"
        self.scanner_path.write_text(FAKE_SCANNER, encoding="utf-8")
        os.chmod(self.scanner_path, 0o755)
        self.settings = AntivirusToolSettings(
            enabled=True,
            binary=str(self.scanner_path),
            fallback_binary="",
            allow_standalone_fallback=False,
            fail_closed=True,
            cache_hours=24,
            max_scan_bytes=10_000_000,
            temp_dir=self.root / "scan-tmp",
        )
        self.antivirus = HostAntivirus(self.settings, database=self.root / "antivirus.sqlite3")

    def tearDown(self) -> None:
        self.antivirus.close()
        self.temp.cleanup()

    def test_clean_infected_error_and_cache(self) -> None:
        clean = self.antivirus.scan_bytes(b"hello", name="a.txt", source_type="test")
        self.assertTrue(clean.clean)
        cached = self.antivirus.scan_bytes(b"hello", name="a.txt", source_type="test")
        self.assertTrue(cached.clean)
        self.assertTrue(cached.cached)
        infected = self.antivirus.scan_bytes(b"MALWARE_TEST", name="bad.bin", source_type="test")
        self.assertTrue(infected.infected)
        self.assertEqual(infected.signature, "Unit-Test-Signature")
        error = self.antivirus.scan_bytes(b"SCAN_ERROR_TEST", name="error.bin", source_type="test")
        self.assertTrue(error.error)

    def test_invoice_is_blocked_before_bridge_upload(self) -> None:
        source = Path(__file__).parents[1] / "mail_agent/config.example.toml"
        config_path = self.root / "config.toml"
        text = source.read_text(encoding="utf-8").replace(
            "mail_agent/data/", str(self.root / "mail-data") + "/"
        ).replace(
            'rules_file = "mail_agent/rules.toml"', f'rules_file = "{self.root / "rules.toml"}"'
        )
        config_path.write_text(text, encoding="utf-8")
        (self.root / "rules.toml").write_text("", encoding="utf-8")
        config = load_config(config_path)
        storage = Storage(config.runtime.database)
        try:
            bridge = FakeBridge()
            manager = InvoiceManager(
                config,
                storage,
                bridge,
                InvoiceToolSettings(enabled=True, folder="Assistent/Rechnungen"),
                antivirus=self.antivirus,
            )
            from email.message import EmailMessage
            msg = EmailMessage()
            msg["From"] = "rechnung@example.test"
            msg["To"] = "jan@example.test"
            msg["Subject"] = "Rechnung 4711"
            msg["Message-ID"] = "<virus-invoice@example.test>"
            msg.set_content("Anbei die Rechnung")
            msg.add_attachment(
                b"%PDF-1.7\nMALWARE_TEST",
                maintype="application",
                subtype="pdf",
                filename="Rechnung-4711.pdf",
            )
            parsed = parse_eml(msg.as_bytes(), Envelope("1"), "INBOX")
            classification = Classification(
                "routine", 0.99, 2, False, "Routine-Rechnung",
                invoice=InvoiceSignal(True, 0.99, "Rechnung", ["Rechnung-4711.pdf"]),
            )
            with patch.object(manager.extractor, "extract") as extract:
                result = manager.process(parsed, classification)
            self.assertEqual(result.status, "invoice-malware-detected")
            self.assertEqual(bridge.uploads, [])
            extract.assert_not_called()
        finally:
            storage.close()

    def test_mail_attachment_is_blocked_before_classification(self) -> None:
        source = Path(__file__).parents[1] / "mail_agent/config.example.toml"
        config_path = self.root / "mail-config.toml"
        text = source.read_text(encoding="utf-8").replace(
            "mail_agent/data/", str(self.root / "mail-runtime") + "/"
        ).replace(
            'rules_file = "mail_agent/rules.toml"', f'rules_file = "{self.root / "mail-rules.toml"}"'
        )
        config_path.write_text(text, encoding="utf-8")
        (self.root / "mail-rules.toml").write_text("", encoding="utf-8")
        config = load_config(config_path)

        from email.message import EmailMessage
        msg = EmailMessage()
        msg["From"] = "attacker@example.test"
        msg["To"] = "jan@example.test"
        msg["Subject"] = "Anhang"
        msg["Message-ID"] = "<malware-mail@example.test>"
        msg.set_content("Text")
        msg.add_attachment(
            b"MALWARE_TEST", maintype="application", subtype="octet-stream", filename="bad.bin"
        )
        raw = msg.as_bytes()

        class FakeHimalaya:
            def __init__(self):
                self.moves = []
            def export_message(self, folder, mailbox_id, path):
                Path(path).write_bytes(raw)
                return OperationResult(True, "exported")
            def move_message(self, source, destination, mailbox_id):
                self.moves.append((source, destination, mailbox_id))
                return OperationResult(True, "moved", destination=destination)
            @staticmethod
            def is_missing_message_error(detail):
                return False

        scanner = HostAntivirus(self.settings, database=self.root / "mail-antivirus.sqlite3")
        agent = MailAgent(config, dry_run=False)
        agent.antivirus.close()
        agent.antivirus = scanner
        agent.tool_settings.security.antivirus = self.settings
        agent.tool_settings.security.antivirus.scan_raw_mail = False
        agent.tool_settings.security.antivirus.scan_attachments = True
        fake = FakeHimalaya()
        agent.himalaya = fake
        try:
            summary = RunSummary()
            result = agent._load_message("INBOX", Envelope("77", subject="Anhang"), summary)
            self.assertIsNone(result)
            self.assertEqual(fake.moves, [("INBOX", config.folders.malware, "77")])
            self.assertEqual(summary.actions[-1]["status"], "malware-detected")
        finally:
            agent.close()


if __name__ == "__main__":
    unittest.main()
