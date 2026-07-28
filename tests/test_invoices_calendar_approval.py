from __future__ import annotations

import re
import tempfile
import unittest
from datetime import datetime, timedelta
from email.message import EmailMessage
from pathlib import Path
from unittest.mock import patch
import urllib.error

from mail_agent.app import MailAgent
from mail_agent.attachments import extract_pdf_attachments
from mail_agent.calendar import CalendarManager
from mail_agent.command import CommandRunner
from mail_agent.config import load_config
from mail_agent.invoices import InvoiceManager
from mail_agent.invoice_extract import FieldValue, InvoiceMetadata
from mail_agent.nextcloud_files import NextcloudFilesClient
from personal_assistant.tool_settings import CalendarMailToolSettings, InvoiceToolSettings
from mail_agent.models import (
    CalendarEvent,
    Classification,
    Envelope,
    InvoiceSignal,
    OperationResult,
    ParsedMessage,
)
from mail_agent.parser import parse_eml
from mail_agent.storage import Storage


class FakeWebDAV:
    def __init__(self) -> None:
        self.folders: list[str] = []
        self.uploads: list[tuple[str, bytes]] = []

    @staticmethod
    def missing_environment() -> list[str]:
        return []

    def ensure_folder(self, path: str) -> OperationResult:
        self.folders.append(path)
        return OperationResult(True, "nextcloud-folder-ready", destination=path)

    def upload_pdf(self, path: str, data: bytes) -> OperationResult:
        self.uploads.append((path, data))
        return OperationResult(True, "invoice-uploaded", destination=path, path=path)


class FakeAssistantBridge:
    def __init__(self) -> None:
        self.uploads: list[tuple[str, bytes]] = []
        self.events: list[tuple[str, str, str]] = []
        self.hashes: set[str] = set()
        self.registers: list[tuple[int, str, bytes]] = []

    @staticmethod
    def health(*, resource_id: str = "nextcloud-files-main") -> OperationResult:
        return OperationResult(True, "ok", destination=resource_id)

    def archive_invoice(self, *, message, attachment_hash: str, data: bytes, remote_path: str, content_type: str = "application/pdf", resource_id: str = "nextcloud-files-main") -> OperationResult:
        if attachment_hash in self.hashes:
            return OperationResult(True, "invoice-duplicate", path=remote_path)
        self.hashes.add(attachment_hash)
        self.uploads.append((remote_path, data))
        return OperationResult(True, "invoice-archived", path=remote_path)

    def sync_invoice_register(self, *, data: bytes, year: int, remote_path: str, resource_id: str = "nextcloud-files-main") -> OperationResult:
        self.registers.append((year, remote_path, data))
        return OperationResult(True, "invoice-register-synced", "Jahresregister aktualisiert", destination=resource_id, path=remote_path)

    def create_calendar_event(self, *, message, resource_id: str, ics: str, uid: str, fingerprint: str, sender: str) -> OperationResult:
        self.events.append((resource_id, uid, sender))
        return OperationResult(True, "created", destination=resource_id)


class FakeNextcloudCalendar:
    def __init__(self, root: Path) -> None:
        self.script_path = root / "nextcloud.js"
        self.script_path.write_text("// test", encoding="utf-8")
        self.created: list[object] = []

    @staticmethod
    def missing_environment() -> list[str]:
        return []

    def create_event(self, event) -> OperationResult:
        self.created.append(event)
        return OperationResult(True, "created", "Testtermin erstellt")


class InvoiceCalendarApprovalTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        source = Path(__file__).parents[1] / "mail_agent/config.example.toml"
        text = source.read_text(encoding="utf-8")
        text = text.replace("mail_agent/data/", str(self.root / "data") + "/")
        text = text.replace(
            'rules_file = "mail_agent/rules.toml"',
            f'rules_file = "{self.root / "rules.toml"}"',
        )
        text = text.replace(
            'log_file = "mail_agent/data/mail_agent.log"',
            f'log_file = "{self.root / "mail_agent.log"}"',
        )
        self.config_path = self.root / "config.toml"
        self.config_path.write_text(text, encoding="utf-8")
        (self.root / "rules.toml").write_text(
            "[spam]\naddresses=[]\ndomains=[]\nsender_names=[]\nsubject_phrases=[]\n"
            "[important]\naddresses=[]\ndomains=[]\n"
            "[routine]\naddresses=[]\ndomains=[]\n",
            encoding="utf-8",
        )
        self.config = load_config(self.config_path)
        tools_source = Path(__file__).parents[1] / "personal_assistant/tools.example.toml"
        tools_text = tools_source.read_text(encoding="utf-8").replace(
            "/home/example/.openclaw/workspace/personal_assistant/data/workspace_outbox",
            str(Path(__file__).parents[1] / "personal_assistant/data/workspace_outbox"),
        )
        self.tools_path = self.root / "tools.toml"
        self.tools_path.write_text(tools_text, encoding="utf-8")
        self.env_patcher = patch.dict(
            "os.environ", {"OPENCLAW_TOOLS_CONFIG": str(self.tools_path)}, clear=False
        )
        self.env_patcher.start()
        self.storage = Storage(self.config.runtime.database)

    def tearDown(self) -> None:
        self.storage.close()
        self.env_patcher.stop()
        self.temp.cleanup()

    @staticmethod
    def mail_with_pdfs(subject: str, names: list[str], body: str = "") -> ParsedMessage:
        msg = EmailMessage()
        msg["From"] = "Lieferant <rechnung@lieferant.test>"
        msg["To"] = "Jan <jan@example.test>"
        msg["Subject"] = subject
        msg["Message-ID"] = f"<{abs(hash((subject, tuple(names))))}@lieferant.test>"
        msg["Date"] = "Sun, 19 Jul 2026 12:00:00 +0200"
        msg.set_content(body or "Anbei erhalten Sie Ihre Rechnung. Rechnungsnummer 4711. Faellig am 31.07.2026.")
        for name in names:
            msg.add_attachment(
                (b"%PDF-1.7\n" + name.encode("utf-8")),
                maintype="application",
                subtype="pdf",
                filename=name,
            )
        return parse_eml(msg.as_bytes(), Envelope("42"), "INBOX")

    def test_pdf_extraction_returns_real_mime_payload(self) -> None:
        message = self.mail_with_pdfs("Rechnung 4711", ["Rechnung-4711.pdf"])
        pdfs = extract_pdf_attachments(message)
        self.assertEqual(len(pdfs), 1)
        self.assertEqual(pdfs[0].filename, "Rechnung-4711.pdf")
        self.assertTrue(pdfs[0].data.startswith(b"%PDF-1.7"))
        self.assertEqual(len(pdfs[0].sha256), 64)

    def test_renamed_non_pdf_is_not_archived(self) -> None:
        msg = EmailMessage()
        msg["From"] = "Lieferant <rechnung@lieferant.test>"
        msg["To"] = "Jan <jan@example.test>"
        msg["Subject"] = "Ihre Rechnung"
        msg["Message-ID"] = "<fake-pdf@lieferant.test>"
        msg.set_content("Rechnung im Anhang")
        msg.add_attachment(
            b"This is not a PDF",
            maintype="application",
            subtype="octet-stream",
            filename="Rechnung.pdf",
        )
        message = parse_eml(msg.as_bytes(), Envelope("43"), "INBOX")
        self.assertEqual(extract_pdf_attachments(message), [])

    def test_routine_invoice_pdf_is_archived_and_deduplicated(self) -> None:
        self.config.invoices.enabled = True
        self.config.nextcloud.enabled = True
        bridge = FakeAssistantBridge()
        settings = InvoiceToolSettings(enabled=True, folder="Assistent/Rechnungen")
        manager = InvoiceManager(self.config, self.storage, bridge, settings)
        manager.extractor.extract = lambda _data, _message: InvoiceMetadata(
            invoice_date=FieldValue("2026-07-15", 0.96, "Rechnungsdatum"),
            invoice_number=FieldValue("4711", 0.94, "Rechnungsnummer"),
            supplier=FieldValue("Lieferant", 0.90, "Rechnungssteller"),
            gross_amount=FieldValue("119.00", 0.95, "Gesamtbetrag"),
            category=FieldValue("Material/Waren", 0.75, "Material"),
            status="confirmed", confidence=0.94, method="test",
        )
        message = self.mail_with_pdfs("Ihre Rechnung 4711", ["Rechnung-4711.pdf"])
        classification = Classification(
            "routine",
            0.98,
            3,
            False,
            "Automatische Rechnung ohne akuten Handlungsbedarf",
            invoice=InvoiceSignal(True, 0.99, "Eindeutige Rechnungs-PDF", ["Rechnung-4711.pdf"]),
        )

        first = manager.process(message, classification)
        second = manager.process(message, classification)

        self.assertEqual(first.status, "invoice-archived")
        self.assertEqual(second.status, "invoice-duplicate")
        self.assertEqual(len(bridge.uploads), 1)
        self.assertRegex(bridge.uploads[0][0], r"^Assistent/Rechnungen/2026/07/")
        self.assertTrue(bridge.uploads[0][0].endswith(".pdf"))
        self.assertEqual(len(bridge.registers), 2, "Auch ein Dublettenlauf muss das Register reparieren koennen")
        self.assertEqual(bridge.registers[0][1], "Assistent/Rechnungen/2026/Rechnungen_2026.csv")
        self.assertIn(b"Rechnungsdatum", bridge.registers[0][2])


    def test_safe_date_archives_normally_and_marks_only_csv_metadata_for_review(self) -> None:
        self.config.invoices.enabled = True
        bridge = FakeAssistantBridge()
        settings = InvoiceToolSettings(enabled=True, folder="Assistent/Rechnungen")
        manager = InvoiceManager(self.config, self.storage, bridge, settings)
        manager.extractor.extract = lambda _data, _message: InvoiceMetadata(
            invoice_date=FieldValue("2026-05-09", 0.96, "Rechnungsdatum"),
            supplier=FieldValue("Lieferant GmbH", 0.90, "Firmenkopf"),
            category=FieldValue("Ungeklärt", 0.25, "Keine Regel"),
            status="review", confidence=0.52, method="text",
            issues=["Rechnungsnummer nicht eindeutig", "Bruttobetrag nicht eindeutig"],
        )
        message = self.mail_with_pdfs("Ihre Rechnung", ["Rechnung.pdf"])
        classification = Classification(
            "routine", 0.98, 3, False, "Routine",
            invoice=InvoiceSignal(True, 0.99, "Eindeutige Rechnung", ["Rechnung.pdf"]),
        )
        result = manager.process(message, classification)
        self.assertEqual(result.status, "invoice-archived-metadata-review")
        self.assertRegex(bridge.uploads[0][0], r"^Assistent/Rechnungen/2026/05/")
        self.assertNotIn("/Pruefen/", bridge.uploads[0][0])
        self.assertEqual(bridge.registers[0][1], "Assistent/Rechnungen/2026/Rechnungen_2026.csv")
        self.assertIn("Prüfen".encode("utf-8"), bridge.registers[0][2])

    def test_multiple_ambiguous_pdfs_are_sent_to_review(self) -> None:
        self.config.invoices.enabled = True
        self.config.nextcloud.enabled = True
        bridge = FakeAssistantBridge()
        settings = InvoiceToolSettings(enabled=True, folder="Assistent/Rechnungen")
        manager = InvoiceManager(self.config, self.storage, bridge, settings)
        message = self.mail_with_pdfs("Ihre Rechnung", ["Dokument1.pdf", "Dokument2.pdf"])
        classification = Classification(
            "routine",
            0.98,
            3,
            False,
            "Routine",
            invoice=InvoiceSignal(True, 0.96, "Rechnung enthalten", []),
        )
        result = manager.process(message, classification)
        self.assertEqual(result.status, "invoice-review-required")
        self.assertEqual(bridge.uploads, [])

    def test_non_routine_mail_is_never_auto_archived(self) -> None:
        self.config.invoices.enabled = True
        self.config.nextcloud.enabled = True
        bridge = FakeAssistantBridge()
        settings = InvoiceToolSettings(enabled=True, folder="Assistent/Rechnungen")
        manager = InvoiceManager(self.config, self.storage, bridge, settings)
        message = self.mail_with_pdfs("Ihre Rechnung", ["Rechnung.pdf"])
        classification = Classification(
            "relevant", 0.99, 9, True, "Rueckfrage zur Rechnung",
            invoice=InvoiceSignal(True, 0.99, "Rechnung", ["Rechnung.pdf"]),
        )
        result = manager.process(message, classification)
        self.assertEqual(result.status, "invoice-not-routine")
        self.assertEqual(bridge.uploads, [])

    def test_nextcloud_upload_uses_official_files_dav_and_no_overwrite(self) -> None:
        self.config.nextcloud.enabled = True
        client = NextcloudFilesClient(self.config)
        captured = {}

        class Response:
            status = 201
            reason = "Created"
            def __enter__(self): return self
            def __exit__(self, *args): return False

        def fake_urlopen(request, timeout):
            captured["url"] = request.full_url
            captured["method"] = request.get_method()
            captured["headers"] = {key.casefold(): value for key, value in request.header_items()}
            captured["data"] = request.data
            return Response()

        env = {
            self.config.nextcloud.base_url_env: "https://cloud.example.test",
            self.config.nextcloud.username_env: "jan@example.test",
            self.config.nextcloud.token_env: "app-password",
        }
        with patch.dict("os.environ", env, clear=False), patch("urllib.request.urlopen", side_effect=fake_urlopen):
            result = client.upload_pdf("Mail-Agent/Rechnungen/2026/07/Rechnung 1.pdf", b"%PDF-1.7\n")

        self.assertTrue(result.ok)
        self.assertEqual(captured["method"], "PUT")
        self.assertIn("/remote.php/dav/files/jan%40example.test/", captured["url"])
        self.assertIn("Rechnung%201.pdf", captured["url"])
        self.assertEqual(captured["headers"]["if-none-match"], "*")
        self.assertEqual(captured["headers"]["content-type"], "application/pdf")

    def test_nextcloud_precondition_failure_is_a_safe_duplicate(self) -> None:
        self.config.nextcloud.enabled = True
        client = NextcloudFilesClient(self.config)
        request_url = "https://cloud.example.test/remote.php/dav/files/jan/x.pdf"
        error = urllib.error.HTTPError(request_url, 412, "Precondition Failed", {}, None)
        env = {
            self.config.nextcloud.base_url_env: "https://cloud.example.test",
            self.config.nextcloud.username_env: "jan",
            self.config.nextcloud.token_env: "app-password",
        }
        with patch.dict("os.environ", env, clear=False), patch("urllib.request.urlopen", side_effect=error):
            result = client.upload_pdf("Mail-Agent/Rechnungen/x.pdf", b"%PDF-1.7\n")
        self.assertTrue(result.ok)
        self.assertEqual(result.status, "invoice-already-exists")

    def test_dry_run_reports_would_archive_invoice(self) -> None:
        agent = MailAgent(self.config, dry_run=True)
        message = self.mail_with_pdfs("Ihre Rechnung", ["Rechnung.pdf"])
        classification = Classification("routine", 0.99, 2, False, "Routine")
        try:
            with patch.object(
                agent.invoices,
                "process",
                return_value=OperationResult(
                    True,
                    "would-archive-invoice",
                    "Wuerde 1 Rechnungs-PDF in Nextcloud archivieren",
                    path="Mail-Agent/Rechnungen/2026/07/Rechnung.pdf",
                ),
            ):
                result = agent._dry_route(message, classification)
        finally:
            agent.close()
        self.assertIn("Wuerde 1 Rechnungs-PDF", result.detail)
        self.assertEqual(result.destination, self.config.folders.routine)
        self.assertTrue(result.path.endswith("Rechnung.pdf"))

    def test_dry_run_reports_calendar_approval_without_sending(self) -> None:
        agent = MailAgent(self.config, dry_run=True)
        message = ParsedMessage(
            stable_key="mid:dry-appointment", mailbox_id="1", source_folder="INBOX", raw=b"",
            subject="Termin", sender_addr="kunde@example.test",
        )
        classification = Classification("appointment", 0.99, 8, True, "Termin")
        try:
            with patch.object(
                agent.calendar,
                "process",
                return_value=OperationResult(
                    True, "would-request-approval", "Wuerde eine Terminfreigabe senden"
                ),
            ) as process:
                result = agent._dry_route(message, classification)
        finally:
            agent.close()
        process.assert_called_once()
        self.assertIn("Terminfreigabe", result.detail)
        self.assertEqual(result.destination, self.config.folders.appointment_review)

    def _calendar_manager(self):
        self.config.calendar.enabled = True
        self.config.calendar.backend = "nextcloud_skill"
        self.config.calendar.auto_create = True
        self.config.calendar.approval_required = True
        self.config.calendar.require_future = True
        self.config.calendar.approval_recipient = "jan.approval@example.test"
        self.config.calendar.approval_reply_from = "jan.approval@example.test"
        sent: list[dict[str, str]] = []

        def send_mail(subject: str, body: str, *, recipient: str = "", reply_to: str = "") -> OperationResult:
            sent.append({
                "subject": subject,
                "body": body,
                "recipient": recipient,
                "reply_to": reply_to,
            })
            return OperationResult(True, "sent")

        nextcloud = FakeNextcloudCalendar(self.root)
        manager = CalendarManager(
            self.config,
            self.storage,
            CommandRunner(),
            nextcloud=nextcloud,
            send_mail=send_mail,
        )
        return manager, nextcloud, sent

    def test_context_event_sends_approval_and_ja_creates_future_event(self) -> None:
        manager, nextcloud, sent = self._calendar_manager()
        start = (datetime.now().astimezone() + timedelta(days=3)).replace(microsecond=0)
        message = ParsedMessage(
            stable_key="mid:context-event",
            mailbox_id="1",
            source_folder="INBOX",
            raw=b"",
            subject="Besprechung am Donnerstag",
            sender_addr="kunde@example.test",
            sender_name="Kunde",
            body_text="Lassen Sie uns am Donnerstag um 10 Uhr sprechen.",
        )
        classification = Classification(
            "appointment",
            0.99,
            8,
            True,
            "Konkretes Datum und Uhrzeit im Mailtext erkannt",
            calendar_event=CalendarEvent(
                title="Projektbesprechung",
                start=start.isoformat(),
                end=(start + timedelta(hours=1)).isoformat(),
                timezone=str(start.tzinfo),
                confidence=0.99,
                status="proposed",
            ),
        )

        requested = manager.process(message, classification)
        self.assertEqual(requested.status, "approval-requested")
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0]["recipient"], "jan.approval@example.test")
        token = re.search(r"MAIL-AGENT TERMIN ([A-Z2-9]+)", sent[0]["subject"]).group(1)
        self.assertEqual(nextcloud.created, [])

        reply = ParsedMessage(
            stable_key="mid:approval-reply",
            mailbox_id="2",
            source_folder="INBOX",
            raw=b"",
            subject=f"Re: [MAIL-AGENT TERMIN {token}] Freigabe: Projektbesprechung",
            sender_addr="jan.approval@example.test",
            body_text="JA\n\nViele Gruesse",
        )
        created = manager.handle_approval_reply(reply)
        self.assertIsNotNone(created)
        self.assertEqual(created.status, "approval-created")
        self.assertEqual(len(nextcloud.created), 1)
        self.assertEqual(nextcloud.created[0].event.title, "Projektbesprechung")

    def test_past_context_event_never_sends_approval(self) -> None:
        manager, nextcloud, sent = self._calendar_manager()
        start = datetime.now().astimezone() - timedelta(hours=2)
        message = ParsedMessage(
            stable_key="mid:past-event",
            mailbox_id="3",
            source_folder="INBOX",
            raw=b"",
            subject="Alter Termin",
            sender_addr="kunde@example.test",
        )
        classification = Classification(
            "appointment",
            0.99,
            8,
            True,
            "Vergangener Termin",
            calendar_event=CalendarEvent(
                title="Vergangener Termin",
                start=start.isoformat(),
                end=(start + timedelta(hours=1)).isoformat(),
                timezone=str(start.tzinfo),
                confidence=0.99,
                status="confirmed",
            ),
        )
        result = manager.process(message, classification)
        self.assertEqual(result.status, "past-event")
        self.assertEqual(sent, [])
        self.assertEqual(nextcloud.created, [])

    def test_wrong_sender_cannot_approve_event(self) -> None:
        manager, nextcloud, sent = self._calendar_manager()
        start = datetime.now().astimezone() + timedelta(days=2)
        source = ParsedMessage(
            stable_key="mid:event-wrong-sender",
            mailbox_id="4",
            source_folder="INBOX",
            raw=b"",
            subject="Termin",
            sender_addr="kunde@example.test",
        )
        classification = Classification(
            "appointment", 0.99, 8, True, "Termin",
            calendar_event=CalendarEvent(
                title="Termin",
                start=start.isoformat(),
                end=(start + timedelta(hours=1)).isoformat(),
                timezone=str(start.tzinfo),
                confidence=0.99,
                status="confirmed",
            ),
        )
        manager.process(source, classification)
        token = re.search(r"MAIL-AGENT TERMIN ([A-Z2-9]+)", sent[0]["subject"]).group(1)
        reply = ParsedMessage(
            stable_key="mid:attacker",
            mailbox_id="5",
            source_folder="INBOX",
            raw=b"",
            subject=f"Re: [MAIL-AGENT TERMIN {token}] Freigabe",
            sender_addr="attacker@example.test",
            body_text="JA",
        )
        result = manager.handle_approval_reply(reply)
        self.assertEqual(result.status, "approval-sender-rejected")
        self.assertEqual(nextcloud.created, [])

    def test_nein_rejects_without_calendar_creation(self) -> None:
        manager, nextcloud, sent = self._calendar_manager()
        start = datetime.now().astimezone() + timedelta(days=2)
        source = ParsedMessage(
            stable_key="mid:event-reject",
            mailbox_id="6",
            source_folder="INBOX",
            raw=b"",
            subject="Termin",
            sender_addr="kunde@example.test",
        )
        classification = Classification(
            "appointment", 0.99, 8, True, "Termin",
            calendar_event=CalendarEvent(
                title="Termin",
                start=start.isoformat(),
                end=(start + timedelta(hours=1)).isoformat(),
                timezone=str(start.tzinfo),
                confidence=0.99,
                status="confirmed",
            ),
        )
        manager.process(source, classification)
        token = re.search(r"MAIL-AGENT TERMIN ([A-Z2-9]+)", sent[0]["subject"]).group(1)
        reply = ParsedMessage(
            stable_key="mid:reject-reply",
            mailbox_id="7",
            source_folder="INBOX",
            raw=b"",
            subject=f"Re: [MAIL-AGENT TERMIN {token}] Freigabe",
            sender_addr="jan.approval@example.test",
            body_text="NEIN",
        )
        result = manager.handle_approval_reply(reply)
        self.assertEqual(result.status, "approval-rejected")
        self.assertEqual(nextcloud.created, [])


if __name__ == "__main__":
    unittest.main()
