from __future__ import annotations

import csv
import hashlib
import io
import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import PurePosixPath
from typing import Any

from .config import InvoiceConfig
from .storage import Storage

HEADERS = (
    "Status",
    "Rechnungsdatum",
    "Eingangsdatum",
    "Rechnungsnummer",
    "Rechnungssteller",
    "Kategorie",
    "Nettobetrag",
    "USt-Betrag",
    "Bruttobetrag",
    "Währung",
    "Fälligkeitsdatum",
    "Erkennung",
    "Konfidenz",
    "Nextcloud-Pfad",
    "Originaldatei",
    "SHA256",
)
_UNTRUSTED_TEXT_COLUMNS = frozenset({
    "Rechnungsnummer", "Rechnungssteller", "Kategorie", "Währung", "Erkennung",
    "Nextcloud-Pfad", "Originaldatei", "SHA256",
})


def _safe_csv_text(value: str) -> str:
    # Spreadsheet importers may ignore leading whitespace or a field-level BOM.
    # Use a visible literal marker, not an Excel-specific apostrophe/tab escape
    # which an editor can discard when saving and reopening the CSV (OWASP CSV
    # Injection). Only the export view changes; source/provenance stays intact.
    offset = 0
    while offset < len(value) and (value[offset].isspace() or value[offset] == "\ufeff"):
        offset += 1
    significant = value[offset:]
    if significant.startswith(("=", "+", "-", "@", "＝", "＋", "－", "＠")) or any(
        character in value[:offset] for character in "\t\r\n"
    ):
        return "Text: " + value
    return value


def _german_date(value: str) -> str:
    try:
        return datetime.strptime(value, "%Y-%m-%d").strftime("%d.%m.%Y")
    except (TypeError, ValueError):
        return ""


def _amount(value: Any) -> str:
    if value is None or value == "":
        return ""
    try:
        return f"{int(value) / 100:.2f}".replace(".", ",")
    except (TypeError, ValueError):
        return ""


def _status_label(value: Any) -> str:
    return {
        "confirmed": "Bestätigt",
        "confirmed-manual": "Manuell bestätigt",
        "review": "Prüfen",
        "error": "Fehler",
    }.get(str(value or "").strip(), "Prüfen")


def _row(item: Any) -> dict[str, str]:
    row = {
        "Status": _status_label(item["extraction_status"]),
        "Rechnungsdatum": _german_date(str(item["invoice_date"] or "")),
        "Eingangsdatum": _german_date(str(item["received_date"] or "")),
        "Rechnungsnummer": str(item["invoice_number"] or ""),
        "Rechnungssteller": str(item["supplier"] or ""),
        "Kategorie": str(item["category"] or "Ungeklärt"),
        "Nettobetrag": _amount(item["net_amount_cents"]),
        "USt-Betrag": _amount(item["tax_amount_cents"]),
        "Bruttobetrag": _amount(item["gross_amount_cents"]),
        "Währung": str(item["currency"] or "EUR"),
        "Fälligkeitsdatum": _german_date(str(item["due_date"] or "")),
        "Erkennung": str(item["extraction_method"] or ""),
        "Konfidenz": f"{float(item['extraction_confidence'] or 0.0):.2f}".replace(".", ","),
        "Nextcloud-Pfad": str(item["nextcloud_path"] or ""),
        "Originaldatei": str(item["original_filename"] or ""),
        "SHA256": str(item["attachment_hash"] or ""),
    }
    return {
        key: _safe_csv_text(value) if key in _UNTRUSTED_TEXT_COLUMNS else value
        for key, value in row.items()
    }


@dataclass(slots=True)
class RegisterResult:
    ok: bool
    year: int
    path: str
    rows: int
    sha256: str = ""
    detail: str = ""
    data: bytes = field(default=b"", repr=False)

    def to_dict(self) -> dict[str, object]:
        return {
            "ok": self.ok,
            "year": self.year,
            "path": self.path,
            "rows": self.rows,
            "sha256": self.sha256,
            "detail": self.detail,
        }


class InvoiceRegister:
    """Render the authoritative yearly CSV directly from the invoice database.

    R26 deliberately keeps no productive local CSV copy. The returned bytes are
    transferred immediately to the fixed Nextcloud path and may only exist in
    memory or in a short-lived action payload during that transfer.
    """

    def __init__(self, storage: Storage, config: InvoiceConfig) -> None:
        self.storage = storage
        self.config = config

    @staticmethod
    def remote_path_for_year(year: int, invoice_folder: str) -> str:
        year = int(year)
        base = str(PurePosixPath("/" + str(invoice_folder or "").lstrip("/"))).lstrip("/")
        return f"{base}/{year:04d}/Rechnungen_{year:04d}.csv"

    def render(self, year: int, *, invoice_folder: str = "") -> RegisterResult:
        year = int(year)
        rows = self.storage.list_invoice_register_rows(year=year, limit=100000)
        buffer = io.StringIO(newline="")
        writer = csv.DictWriter(
            buffer,
            fieldnames=HEADERS,
            delimiter=self.config.register_delimiter,
            extrasaction="ignore",
            lineterminator="\r\n",
        )
        writer.writeheader()
        for item in rows:
            writer.writerow(_row(item))
        data = buffer.getvalue().encode("utf-8-sig")
        path = self.remote_path_for_year(year, invoice_folder) if invoice_folder else f"Rechnungen_{year:04d}.csv"
        return RegisterResult(
            True,
            year,
            path,
            len(rows),
            hashlib.sha256(data).hexdigest(),
            "Jahresregister im Speicher neu erzeugt; keine lokale Registerkopie angelegt",
            data,
        )

    def status(self, *, invoice_folder: str = "") -> dict[str, object]:
        years = self.storage.invoice_register_years()
        return {
            "ok": True,
            "enabled": self.config.register_enabled,
            "storage": "nextcloud-only",
            "delimiter": self.config.register_delimiter,
            "years": [
                {
                    "year": year,
                    "rows": self.storage.count_invoice_register_rows(year),
                    "path": self.remote_path_for_year(year, invoice_folder) if invoice_folder else f"Rechnungen_{year:04d}.csv",
                }
                for year in years
            ],
        }

    def export_json(self, year: int, *, invoice_folder: str = "") -> str:
        return json.dumps(self.render(year, invoice_folder=invoice_folder).to_dict(), ensure_ascii=False)
