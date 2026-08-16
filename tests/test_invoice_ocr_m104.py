from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import patch

from mail_agent.config import InvoiceConfig
from mail_agent.invoice_extract import (
    INVOICE_EXTRACTOR_VERSION,
    INVOICE_RULESET_VERSION,
    InvoiceExtractor,
    OCRTextResult,
    extract_ocr_text,
    select_ocr_pages,
)
from mail_agent.models import ParsedMessage

FIXTURE = Path(__file__).parent / "fixtures/invoices/m104_ocr_corpus.json"


def message() -> ParsedMessage:
    return ParsedMessage(
        stable_key="synthetic-m104",
        mailbox_id="synthetic",
        source_folder="fixture",
        raw=b"",
        subject="Synthetischer M10.4-Beleg",
        sender_name="M104 Fiktiv GmbH",
        sender_addr="m104@example.invalid",
        received_at="Sun, 16 Aug 2026 10:00:00 +0200",
    )


def corpus_case(case_id: str) -> dict[str, object]:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    return next(case for case in payload["cases"] if case["id"] == case_id)


def test_sanitized_ocr_corpus_covers_required_pdf_shapes() -> None:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert payload["schema_version"] == 1
    assert {case["pdf_kind"] for case in payload["cases"]} == {
        "text-layer",
        "image-based",
        "mixed",
        "multipage",
        "corrupt",
    }
    raw = FIXTURE.read_text(encoding="utf-8")
    assert "/srv/openclaw" not in raw
    assert "example.invalid" in raw


def test_complete_native_required_fields_do_not_start_ocr() -> None:
    case = corpus_case("native-complete")
    extractor = InvoiceExtractor(InvoiceConfig(ocr_enabled=True))
    with patch(
        "mail_agent.invoice_extract.extract_native_text",
        return_value=(str(case["native_text"]), ""),
    ), patch("mail_agent.invoice_extract.extract_ocr_text") as ocr:
        result = extractor.extract(b"%PDF-1.7 synthetic", message(), scanner_identity="clamav:test")

    ocr.assert_not_called()
    assert result.status == "confirmed"
    assert result.technical.ocr_trigger_fields == []
    assert not result.technical.ocr_attempted
    assert result.technical.scanner_identity == "clamav:test"


def test_image_pdf_uses_bounded_ocr_for_missing_required_fields() -> None:
    case = corpus_case("image-only")
    extractor = InvoiceExtractor(InvoiceConfig(ocr_enabled=True))
    ocr_result = OCRTextResult(
        text=str(case["ocr_text"]),
        engine="tesseract",
        languages=["deu", "eng"],
        page_count=1,
        pages=[1],
        rendered_bytes=1234,
        duration_ms=12.5,
    )
    with patch("mail_agent.invoice_extract.extract_native_text", return_value=("", "")), patch(
        "mail_agent.invoice_extract.extract_ocr_text", return_value=ocr_result
    ):
        result = extractor.extract(b"%PDF-1.7 synthetic-image", message())

    assert set(result.technical.ocr_trigger_fields) == {
        "invoice_date",
        "invoice_number",
        "gross_amount",
        "supplier",
    }
    assert result.technical.ocr_pages == [1]
    assert result.technical.ocr_rendered_bytes == 1234
    assert result.invoice_number.value == "IMAGE-104"
    assert result.gross_amount.value == "238.00"
    assert result.status == "confirmed"


def test_mixed_pdf_only_needs_missing_number_and_retains_agreeing_native_fields() -> None:
    case = corpus_case("mixed-number-fallback")
    extractor = InvoiceExtractor(InvoiceConfig(ocr_enabled=True))
    with patch(
        "mail_agent.invoice_extract.extract_native_text",
        return_value=(str(case["native_text"]), ""),
    ), patch(
        "mail_agent.invoice_extract.extract_ocr_text",
        return_value=OCRTextResult(text=str(case["ocr_text"]), pages=[1], page_count=1),
    ):
        result = extractor.extract(b"%PDF-1.7 synthetic-mixed", message())

    assert result.technical.ocr_trigger_fields == ["invoice_number"]
    assert result.invoice_number.value == "MIXED-104"
    assert result.invoice_date.value == "2026-08-16"
    assert result.gross_amount.value == "357.00"
    assert result.status == "confirmed"
    assert any(
        item.field == "invoice_number" and item.outcome == "ocr-fallback"
        for item in result.technical.fusion
    )


def test_native_ocr_conflict_forces_review_despite_high_total_confidence() -> None:
    native = (
        "M104 Konflikt GmbH\nRechnungsnummer: CONFLICT-104\n"
        "Rechnungsdatum: 16.08.2026\nGesamtbetrag: 119,00 EUR"
    )
    ocr = native.replace("16.08.2026", "15.08.2026")
    extractor = InvoiceExtractor(InvoiceConfig(ocr_enabled=True))
    with patch("mail_agent.invoice_extract.extract_native_text", return_value=(native, "")), patch(
        "mail_agent.invoice_extract.required_ocr_fields", return_value=["invoice_date"]
    ), patch(
        "mail_agent.invoice_extract.extract_ocr_text",
        return_value=OCRTextResult(text=ocr, pages=[1], page_count=1),
    ):
        result = extractor.extract(b"%PDF-1.7 synthetic-conflict", message())

    assert result.status == "review"
    assert result.invoice_date.value == ""
    assert "fusion:invoice_date-conflict" in result.review_reasons
    assert any(item.outcome == "conflict-review" for item in result.technical.fusion)


def test_multipage_policy_uses_first_pages_plus_last_without_growing_budget() -> None:
    case = corpus_case("first-and-last-page")
    assert select_ocr_pages(len(case["pages"]), 2) == case["expected_pages"]
    assert select_ocr_pages(5, 3) == [1, 2, 5]
    assert select_ocr_pages(2, 2) == [1, 2]
    assert select_ocr_pages(5, 1) == [1]


def test_ocr_runner_is_local_and_renders_only_selected_pages(tmp_path: Path) -> None:
    pdf = tmp_path / "synthetic.pdf"
    pdf.write_bytes(b"%PDF-1.7 synthetic")
    commands: list[list[str]] = []

    def run(command: list[str], *, timeout: float) -> subprocess.CompletedProcess[str]:
        del timeout
        commands.append(command)
        tool = Path(command[0]).name
        if tool == "pdfinfo":
            return subprocess.CompletedProcess(command, 0, "Pages:          5\n", "")
        if tool == "pdftoppm":
            Path(command[-1]).with_suffix(".png").write_bytes(b"sanitized-image")
            return subprocess.CompletedProcess(command, 0, "", "")
        page = Path(command[1]).stem
        return subprocess.CompletedProcess(command, 0, f"OCR {page}", "")

    binaries = {name: f"/usr/bin/{name}" for name in ("pdfinfo", "pdftoppm", "tesseract")}
    with patch("mail_agent.invoice_extract.shutil.which", side_effect=binaries.get), patch(
        "mail_agent.invoice_extract._installed_ocr_languages", return_value=(["deu", "eng"], "")
    ), patch("mail_agent.invoice_extract._run", side_effect=run):
        result = extract_ocr_text(pdf, config=InvoiceConfig(ocr_max_pages=2))

    assert result.error == ""
    assert result.pages == [1, 5]
    assert result.page_count == 5
    assert [Path(command[0]).name for command in commands] == [
        "pdfinfo",
        "pdftoppm",
        "tesseract",
        "pdftoppm",
        "tesseract",
    ]
    assert all(
        not any(token.startswith(("http://", "https://")) for token in command)
        for command in commands
    )


def test_ocr_missing_binary_and_language_fail_closed(tmp_path: Path) -> None:
    pdf = tmp_path / "synthetic.pdf"
    pdf.write_bytes(b"%PDF-1.7 synthetic")
    binaries = {"pdfinfo": "/usr/bin/pdfinfo", "pdftoppm": "/usr/bin/pdftoppm"}
    with patch("mail_agent.invoice_extract.shutil.which", side_effect=binaries.get):
        missing_binary = extract_ocr_text(pdf, config=InvoiceConfig())
    assert "tesseract" in missing_binary.error

    binaries["tesseract"] = "/usr/bin/tesseract"
    with patch("mail_agent.invoice_extract.shutil.which", side_effect=binaries.get), patch(
        "mail_agent.invoice_extract._installed_ocr_languages", return_value=(["eng"], "")
    ):
        missing_language = extract_ocr_text(pdf, config=InvoiceConfig(ocr_languages="deu+eng"))
    assert missing_language.error == "OCR-Sprache fehlt: deu"


def test_corrupt_pdf_timeout_and_size_budget_fail_closed(tmp_path: Path) -> None:
    pdf = tmp_path / "corrupt.pdf"
    pdf.write_bytes(b"not-a-pdf")
    binaries = {name: f"/usr/bin/{name}" for name in ("pdfinfo", "pdftoppm", "tesseract")}
    with patch("mail_agent.invoice_extract.shutil.which", side_effect=binaries.get), patch(
        "mail_agent.invoice_extract._installed_ocr_languages", return_value=(["deu", "eng"], "")
    ), patch(
        "mail_agent.invoice_extract._run",
        return_value=subprocess.CompletedProcess([], 1, "", "Syntax Error: corrupt PDF"),
    ):
        corrupt = extract_ocr_text(pdf, config=InvoiceConfig())
    assert "corrupt PDF" in corrupt.error

    with patch("mail_agent.invoice_extract.shutil.which", side_effect=binaries.get), patch(
        "mail_agent.invoice_extract._installed_ocr_languages", return_value=(["deu", "eng"], "")
    ), patch(
        "mail_agent.invoice_extract._deadline_run",
        side_effect=subprocess.TimeoutExpired(["pdfinfo"], 1),
    ):
        timeout = extract_ocr_text(pdf, config=InvoiceConfig())
    assert "Seitenpruefung" in timeout.error

    with patch("mail_agent.invoice_extract.shutil.which") as which:
        too_large = extract_ocr_text(pdf, config=InvoiceConfig(max_pdf_bytes=4))
    assert "Groessenbudget" in too_large.error
    which.assert_not_called()


def test_rendered_bytes_and_output_char_budgets_fail_closed(tmp_path: Path) -> None:
    pdf = tmp_path / "synthetic.pdf"
    pdf.write_bytes(b"%PDF-1.7 synthetic")
    binaries = {name: f"/usr/bin/{name}" for name in ("pdfinfo", "pdftoppm", "tesseract")}

    def rendered_too_large(
        command: list[str], *, timeout: float
    ) -> subprocess.CompletedProcess[str]:
        del timeout
        if Path(command[0]).name == "pdfinfo":
            return subprocess.CompletedProcess(command, 0, "Pages: 1\n", "")
        Path(command[-1]).with_suffix(".png").write_bytes(b"x" * 20)
        return subprocess.CompletedProcess(command, 0, "", "")

    with patch("mail_agent.invoice_extract.shutil.which", side_effect=binaries.get), patch(
        "mail_agent.invoice_extract._installed_ocr_languages", return_value=(["deu", "eng"], "")
    ), patch("mail_agent.invoice_extract._run", side_effect=rendered_too_large):
        rendered = extract_ocr_text(
            pdf, config=InvoiceConfig(ocr_max_rendered_bytes=10)
        )
    assert "Ressourcenbudget" in rendered.error

    def output_too_large(
        command: list[str], *, timeout: float
    ) -> subprocess.CompletedProcess[str]:
        del timeout
        tool = Path(command[0]).name
        if tool == "pdfinfo":
            return subprocess.CompletedProcess(command, 0, "Pages: 1\n", "")
        if tool == "pdftoppm":
            Path(command[-1]).with_suffix(".png").write_bytes(b"x")
            return subprocess.CompletedProcess(command, 0, "", "")
        return subprocess.CompletedProcess(command, 0, "x" * 20, "")

    with patch("mail_agent.invoice_extract.shutil.which", side_effect=binaries.get), patch(
        "mail_agent.invoice_extract._installed_ocr_languages", return_value=(["deu", "eng"], "")
    ), patch("mail_agent.invoice_extract._run", side_effect=output_too_large):
        output = extract_ocr_text(pdf, config=InvoiceConfig(ocr_max_output_chars=10))
    assert "Zeichenbudget" in output.error


def test_technical_metadata_contains_identity_but_no_document_content() -> None:
    text = str(corpus_case("native-complete")["native_text"])
    extractor = InvoiceExtractor(InvoiceConfig(ocr_enabled=True))
    with patch("mail_agent.invoice_extract.extract_native_text", return_value=(text, "")):
        result = extractor.extract(b"%PDF-1.7 synthetic", message(), scanner_identity="clamav:test")
    technical = json.loads(result.to_json())["technical"]
    assert technical["extractor_version"] == INVOICE_EXTRACTOR_VERSION
    assert technical["ruleset_version"] == INVOICE_RULESET_VERSION
    assert technical["scanner_identity"] == "clamav:test"
    serialized = json.dumps(technical, ensure_ascii=False)
    assert "NATIVE-104" not in serialized
    assert "M104 Fiktiv GmbH" not in serialized
