from __future__ import annotations

import csv
import io
import sqlite3
from pathlib import Path
from unittest.mock import patch

from mail_agent.config import InvoiceConfig
from mail_agent.invoice_extract import (
    FieldValue,
    InvoiceExtractor,
    InvoiceMetadata,
    _choose,
    parse_invoice_text,
)
from mail_agent.invoice_register import InvoiceRegister
from mail_agent.invoices import InvoiceManager
from mail_agent.models import ParsedMessage
from mail_agent.storage import Storage
from personal_assistant.tool_registry import build_tool_registry
from personal_assistant.tool_settings import InvoiceToolSettings, MailToolSettings, ToolSettings


def message(subject: str = "Rechnung", date: str = "Fri, 24 Jul 2026 10:00:00 +0200") -> ParsedMessage:
    return ParsedMessage(
        stable_key="mail-1",
        mailbox_id="1",
        source_folder="INBOX",
        raw=b"",
        subject=subject,
        sender_name="Beispiel GmbH",
        sender_addr="rechnung@example.de",
        date=date,
    )


def test_anchored_invoice_date_wins_over_due_and_service_dates() -> None:
    text = """
    Beispiel GmbH
    Rechnungsnummer: RE-2026-4711
    Leistungsdatum: 30.06.2026
    Rechnungsdatum: 15.07.2026
    Fällig am: 14.08.2026
    Gesamtbetrag: 1.190,00 EUR
    """
    metadata = parse_invoice_text(text, message(), method="text")
    assert metadata.invoice_date.value == "2026-07-15"
    assert metadata.due_date.value == "2026-08-14"
    assert metadata.gross_amount.value == "1190.00"
    assert metadata.status == "confirmed"


def test_supplier_name_containing_lieferant_does_not_consume_next_field() -> None:
    text = """
    Muster Lieferant GmbH
    Rechnungsnummer: RE-2026-4711
    Rechnungsdatum: 15.07.2026
    Gesamtbetrag: 119,00 EUR
    """
    metadata = parse_invoice_text(text, message(), method="text")
    assert metadata.supplier.value == "Muster Lieferant GmbH"
    assert metadata.supplier.value != "Rechnungsnummer: RE-2026-4711"


def test_explicit_supplier_label_is_preferred() -> None:
    text = """
    Abrechnungsservice GmbH
    Rechnungssteller: Fachhandel Nord GmbH
    Rechnungsnummer: RE-2026-4711
    Rechnungsdatum: 15.07.2026
    Gesamtbetrag: 119,00 EUR
    """
    metadata = parse_invoice_text(text, message(), method="text")
    assert metadata.supplier.value == "Fachhandel Nord GmbH"
    assert metadata.supplier.confidence >= 0.9


def test_unanchored_date_is_not_silently_confirmed() -> None:
    text = """
    Beispiel GmbH
    15.07.2026
    Rechnungsnummer: RE-2026-4711
    Gesamtbetrag: 119,00 EUR
    """
    metadata = parse_invoice_text(text, message(), method="text")
    assert metadata.invoice_date.confidence < 0.85
    assert metadata.status == "review"
    assert "Rechnungsdatum nicht eindeutig" not in metadata.issues or metadata.invoice_date.value


def test_register_is_semicolon_utf8_bom_and_german_formatted(tmp_path: Path) -> None:
    db = tmp_path / "mail.sqlite3"
    storage = Storage(db)
    try:
        storage.record_invoice(
            stable_key="mail-1", attachment_hash="a" * 64, original_filename="rechnung.pdf",
            nextcloud_path="Assistent/Rechnungen/2026/07/Rechnung.pdf", size_bytes=123,
            status="uploaded", invoice_date="2026-07-15", received_date="2026-07-24",
            invoice_number="RE-4711", supplier="Beispiel GmbH", category="Software/IT",
            gross_amount_cents=119000, net_amount_cents=100000, tax_amount_cents=19000,
            currency="EUR", due_date="2026-08-14", extraction_status="confirmed",
            extraction_confidence=0.94, extraction_method="text", extraction_json="{}", register_year=2026,
        )
        legacy_dir = tmp_path / "register"
        config = InvoiceConfig(register_dir=legacy_dir)
        result = InvoiceRegister(storage, config).render(
            2026, invoice_folder="Assistent/Rechnungen"
        )
        raw = result.data
        assert result.path == "Assistent/Rechnungen/2026/Rechnungen_2026.csv"
        assert raw.startswith(b"\xef\xbb\xbf")
        rows = list(csv.DictReader(io.StringIO(raw.decode("utf-8-sig")), delimiter=";"))
        assert len(rows) == 1
        assert rows[0]["Status"] == "Bestätigt"
        assert rows[0]["Rechnungsdatum"] == "15.07.2026"
        assert rows[0]["Bruttobetrag"] == "1190,00"
        assert not legacy_dir.exists(), "R26 darf keine produktive lokale Registerkopie anlegen"
    finally:
        storage.close()


def test_existing_invoice_schema_is_migrated_without_data_loss(tmp_path: Path) -> None:
    db = tmp_path / "old.sqlite3"
    connection = sqlite3.connect(db)
    connection.executescript(
        """
        CREATE TABLE invoices (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            stable_key TEXT NOT NULL,
            attachment_hash TEXT NOT NULL UNIQUE,
            original_filename TEXT,
            nextcloud_path TEXT,
            size_bytes INTEGER,
            status TEXT NOT NULL,
            error TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        INSERT INTO invoices(stable_key, attachment_hash, original_filename, nextcloud_path,
            size_bytes, status, error, created_at, updated_at)
        VALUES('mail-1', 'hash-1', 'old.pdf', 'Assistent/Rechnungen/2026/old.pdf',
            10, 'uploaded', '', '2026-01-01', '2026-01-01');
        """
    )
    connection.commit()
    connection.close()
    storage = Storage(db)
    try:
        columns = {row[1] for row in storage.connection.execute("PRAGMA table_info(invoices)")}
        assert "invoice_date" in columns
        assert "extraction_json" in columns
        assert storage.get_invoice("hash-1")["original_filename"] == "old.pdf"
    finally:
        storage.close()


def test_backfill_candidates_only_include_legacy_archives(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "mail.sqlite3")
    try:
        storage.record_invoice(
            stable_key="mail-1", attachment_hash="legacy", original_filename="old.pdf",
            nextcloud_path="Assistent/Rechnungen/2026/old.pdf", size_bytes=10, status="uploaded",
        )
        storage.record_invoice(
            stable_key="mail-2", attachment_hash="new", original_filename="new.pdf",
            nextcloud_path="Assistent/Rechnungen/2026/new.pdf", size_bytes=10, status="uploaded",
            extraction_status="confirmed", register_year=2026,
        )
        rows = storage.list_invoice_backfill_candidates()
        assert [row["attachment_hash"] for row in rows] == ["legacy"]
    finally:
        storage.close()


def test_invoice_agent_tools_include_backfill(tmp_path: Path) -> None:
    settings = ToolSettings(
        path=tmp_path / "tools.toml",
        mail=MailToolSettings(invoices=InvoiceToolSettings(enabled=True)),
    )
    ids = {tool.id for tool in build_tool_registry(settings)}
    assert "assistant.invoices.backfill-preview" in ids
    assert "assistant.invoices.backfill" in ids


def test_review_target_uses_received_date_not_guessed_invoice_date(tmp_path: Path) -> None:
    # Construct without invoking network or OCR; only path logic is tested.
    manager = object.__new__(InvoiceManager)
    manager.settings = type("Settings", (), {"folder": "Assistent/Rechnungen", "organize_by_year_month": True})()
    manager.config = type("Config", (), {"invoices": InvoiceConfig(review_subfolder="Pruefen")})()
    metadata = InvoiceMetadata(status="review")
    assert manager._target_folder(message(), metadata).endswith("Pruefen/2026/07")


def test_safe_invoice_date_controls_folder_even_when_optional_metadata_is_missing() -> None:
    manager = object.__new__(InvoiceManager)
    manager.settings = type("Settings", (), {"folder": "Assistent/Rechnungen", "organize_by_year_month": True})()
    manager.config = type("Config", (), {"invoices": InvoiceConfig(review_subfolder="Pruefen")})()
    metadata = InvoiceMetadata(
        invoice_date=FieldValue("2026-06-03", 0.96, "Rechnungsdatum: 03.06.2026"),
        status="review",
        issues=["Bruttobetrag nicht eindeutig"],
    )
    assert manager._target_folder(message(), metadata) == "Assistent/Rechnungen/2026/06"


def test_native_pdf_text_with_safe_date_does_not_trigger_ocr() -> None:
    native_text = """
    Beispiel GmbH
    Rechnung vom 15.07.2026
    Rechnungsnummer: RE-2026-4711
    Gesamtbetrag: 119,00 EUR
    """ + (" Leistungsbeschreibung" * 20)
    extractor = InvoiceExtractor(InvoiceConfig(ocr_enabled=True, min_text_quality=0.20))
    with patch("mail_agent.invoice_extract.extract_native_text", return_value=(native_text, "")), patch(
        "mail_agent.invoice_extract.extract_ocr_text"
    ) as ocr:
        metadata = extractor.extract(b"%PDF-1.7 test", message())
    assert metadata.invoice_date.value == "2026-07-15"
    assert metadata.date_confirmed
    ocr.assert_not_called()


def test_generic_datum_label_is_accepted_but_service_date_is_rejected() -> None:
    text = """
    Beispiel GmbH
    Rechnung RE-22
    Leistungsdatum: 30.06.2026
    Datum: 15.07.2026
    Rechnungsnummer: RE-22
    Gesamtbetrag: 119,00 EUR
    """
    metadata = parse_invoice_text(text, message(), method="text")
    assert metadata.invoice_date.value == "2026-07-15"
    assert metadata.date_confirmed


def test_native_ocr_date_conflict_is_never_silently_confirmed() -> None:
    native = InvoiceMetadata(
        invoice_date=FieldValue("2026-07-15", 0.96, "Rechnungsdatum"),
        method="text",
    )
    ocr = InvoiceMetadata(
        invoice_date=FieldValue("2026-07-16", 0.82, "OCR Rechnungsdatum"),
        method="ocr",
    )
    selected = _choose(native, ocr)
    assert selected.invoice_date.value == ""
    assert not selected.date_confirmed
    assert selected.status == "review"
    assert "fusion:invoice_date-conflict" in selected.review_reasons
    assert any("widersprechen" in issue for issue in selected.issues)
