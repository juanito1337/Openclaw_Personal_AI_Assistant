#!/usr/bin/env python3
from __future__ import annotations

import json
import resource
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mail_agent.config import InvoiceConfig  # noqa: E402
from mail_agent.invoice_extract import (  # noqa: E402
    INVOICE_EXTRACTOR_VERSION,
    INVOICE_RULESET_VERSION,
    extract_native_text,
    extract_ocr_text,
)
from scripts.invoice_ocr_fixture import build_sanitized_pdf  # noqa: E402


def _tool_version(name: str) -> str:
    binary = shutil.which(name)
    if not binary:
        return "missing"
    flag = "--version" if name == "tesseract" else "-v"
    result = subprocess.run(
        [binary, flag], check=False, capture_output=True, text=True, timeout=10
    )
    output = (result.stdout or result.stderr or "").splitlines()
    return output[0][:200] if output else "unknown"


def main() -> int:
    config = InvoiceConfig()
    payload = build_sanitized_pdf()
    with tempfile.TemporaryDirectory(prefix="m104-benchmark-") as temp:
        pdf = Path(temp) / "sanitized-three-page.pdf"
        pdf.write_bytes(payload)

        native_started = time.monotonic()
        native_text, native_error = extract_native_text(pdf, timeout=config.text_timeout_seconds)
        native_ms = round((time.monotonic() - native_started) * 1000.0, 3)

        ocr_started = time.monotonic()
        ocr = extract_ocr_text(pdf, config=config)
        ocr_wall_ms = round((time.monotonic() - ocr_started) * 1000.0, 3)

    report = {
        "ok": not native_error and not ocr.error,
        "fixture": {
            "kind": "generated-sanitized-three-page-pdf",
            "input_bytes": len(payload),
            "contains_productive_data": False,
        },
        "identity": {
            "extractor_version": INVOICE_EXTRACTOR_VERSION,
            "ruleset_version": INVOICE_RULESET_VERSION,
            "pdftotext": _tool_version("pdftotext"),
            "pdfinfo": _tool_version("pdfinfo"),
            "pdftoppm": _tool_version("pdftoppm"),
            "tesseract": _tool_version("tesseract"),
        },
        "budgets": {
            "pdf_bytes": config.max_pdf_bytes,
            "pages": config.ocr_max_pages,
            "dpi": config.ocr_dpi,
            "total_timeout_seconds": config.ocr_timeout_seconds,
            "rendered_bytes": config.ocr_max_rendered_bytes,
            "output_chars": config.ocr_max_output_chars,
        },
        "measurement": {
            "native_wall_ms": native_ms,
            "ocr_wall_ms": ocr_wall_ms,
            "ocr_internal_ms": ocr.duration_ms,
            "page_count": ocr.page_count,
            "selected_pages": ocr.pages,
            "rendered_bytes": ocr.rendered_bytes,
            "native_output_chars": len(native_text),
            "ocr_output_chars": len(ocr.text),
            "child_max_rss_kib": resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss,
        },
        "errors": [value for value in (native_error, ocr.error) if value],
    }
    print(json.dumps(report, indent=2, ensure_ascii=False, sort_keys=True))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
