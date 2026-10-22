from __future__ import annotations

import csv
import hashlib
import io
from pathlib import Path

import pytest

from mail_agent.config import InvoiceConfig
from mail_agent.invoice_register import HEADERS, InvoiceRegister, _row
from mail_agent.storage import Storage


def _invoice() -> dict[str, object]:
    return {
        "stable_key": "synthetic-invoice",
        "attachment_hash": "a" * 64,
        "original_filename": "synthetic.pdf",
        "nextcloud_path": "Assistent/Rechnungen/2020/synthetic.pdf",
        "size_bytes": 123,
        "status": "uploaded",
        "invoice_date": "2020-01-01",
        "received_date": "2020-01-01",
        "invoice_number": "R-2020-1",
        "supplier": "Synthetic Example GmbH",
        "category": "Software/IT",
        "gross_amount_cents": -11900,
        "net_amount_cents": -10000,
        "tax_amount_cents": -1900,
        "currency": "EUR",
        "due_date": "2020-02-01",
        "extraction_status": "confirmed",
        "extraction_confidence": 0.95,
        "extraction_method": "text",
        "extraction_json": '{"synthetic_provenance":"unchanged"}',
        "register_year": 2020,
    }


@pytest.mark.parametrize(
    "payload",
    (
        "=1+1", "+1+1", "-1+1", "@SUM(1)", "＝1+1", "＋1+1", "－1+1", "＠SUM(1)",
        "\tplain-text", "\rplain-text", "\nplain-text", "  =1+1", "\u00a0+1+1",
        "\ufeff=1+1", " \ufeff \ufeff\t=1+1", '="x";=1+1\r\n=2+2',
    ),
)
def test_dangerous_text_has_visible_export_marker(payload: str) -> None:
    item = _invoice()
    item["invoice_number"] = payload
    before = dict(item)
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=HEADERS, delimiter=";")
    writer.writeheader()
    writer.writerow(_row(item))
    rows = list(csv.DictReader(io.StringIO(output.getvalue()), delimiter=";"))
    assert len(rows) == 1
    assert set(rows[0]) == set(HEADERS)
    assert rows[0]["Rechnungsnummer"] == "Text: " + payload
    assert item == before


@pytest.mark.parametrize(
    ("field", "column"),
    (
        ("invoice_number", "Rechnungsnummer"), ("supplier", "Rechnungssteller"),
        ("category", "Kategorie"), ("currency", "Währung"), ("extraction_method", "Erkennung"),
        ("nextcloud_path", "Nextcloud-Pfad"), ("original_filename", "Originaldatei"),
        ("attachment_hash", "SHA256"),
    ),
)
def test_every_untrusted_text_column_is_neutralized(field: str, column: str) -> None:
    item = _invoice()
    item[field] = "=1+1"
    assert _row(item)[column] == "Text: =1+1"


@pytest.mark.parametrize("value", ("R-2020-1", 'Example; "=1+1"', "example\n=1+1", "", "O'Reilly"))
def test_benign_text_is_not_reformatted(value: str) -> None:
    item = _invoice()
    item["invoice_number"] = value
    assert _row(item)["Rechnungsnummer"] == value


def test_render_preserves_database_provenance_and_signed_numeric_columns(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "synthetic.sqlite3")
    try:
        item = _invoice()
        item["supplier"] = '=HYPERLINK("https://example.invalid/synthetic","x")'
        item["original_filename"] = "+synthetic.pdf"
        storage.record_invoice(**item)
        before = dict(storage.get_invoice("a" * 64))
        result = InvoiceRegister(storage, InvoiceConfig()).render(2020)
        rows = list(csv.DictReader(io.StringIO(result.data.decode("utf-8-sig")), delimiter=";"))
        assert len(rows) == 1
        assert rows[0]["Rechnungssteller"] == "Text: " + str(item["supplier"])
        assert rows[0]["Originaldatei"] == "Text: +synthetic.pdf"
        assert rows[0]["Nettobetrag"] == "-100,00"
        assert rows[0]["USt-Betrag"] == "-19,00"
        assert rows[0]["Bruttobetrag"] == "-119,00"
        assert rows[0]["Konfidenz"] == "0,95"
        assert rows[0]["Rechnungsdatum"] == "01.01.2020"
        assert dict(storage.get_invoice("a" * 64)) == before
        assert result.sha256 == hashlib.sha256(result.data).hexdigest()
    finally:
        storage.close()
