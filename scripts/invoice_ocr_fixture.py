from __future__ import annotations


def _pdf_string(value: str) -> str:
    return value.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def build_sanitized_pdf() -> bytes:
    """Create the deterministic, non-productive PDF used by M10 OCR checks."""
    pages = (
        ("M104 Synthetic Invoice GmbH", "Invoice Date: 2026-08-16"),
        ("Synthetic line items", "No personal or productive data"),
        ("Invoice Number: BENCH-104", "Grand Total: 119.00 EUR"),
    )
    font_object = 3 + len(pages) * 2
    objects: list[bytes] = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        (
            f"<< /Type /Pages /Kids [{' '.join(f'{3 + index * 2} 0 R' for index in range(len(pages)))}] "
            f"/Count {len(pages)} >>"
        ).encode("ascii"),
    ]
    for index, lines in enumerate(pages):
        content_object = 4 + index * 2
        page = (
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
            f"/Resources << /Font << /F1 {font_object} 0 R >> >> "
            f"/Contents {content_object} 0 R >>"
        ).encode("ascii")
        commands = ["BT", "/F1 16 Tf", "72 780 Td"]
        for line_index, line in enumerate(lines):
            if line_index:
                commands.append("0 -28 Td")
            commands.append(f"({_pdf_string(line)}) Tj")
        commands.append("ET")
        stream = "\n".join(commands).encode("ascii")
        content = (
            b"<< /Length "
            + str(len(stream)).encode("ascii")
            + b" >>\nstream\n"
            + stream
            + b"\nendstream"
        )
        objects.extend((page, content))
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")

    output = bytearray(b"%PDF-1.4\n%M104\n")
    offsets = [0]
    for number, body in enumerate(objects, start=1):
        offsets.append(len(output))
        output.extend(f"{number} 0 obj\n".encode("ascii"))
        output.extend(body)
        output.extend(b"\nendobj\n")
    xref = len(output)
    output.extend(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
    output.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        output.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    output.extend(
        (
            f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
            f"startxref\n{xref}\n%%EOF\n"
        ).encode("ascii")
    )
    return bytes(output)
