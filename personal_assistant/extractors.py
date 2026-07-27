from __future__ import annotations

import csv
import hashlib
import html
import io
import json
import re
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path
from xml.etree import ElementTree


_TEXT_SUFFIXES = {".txt", ".md", ".csv", ".json", ".xml", ".html", ".htm", ".ics", ".vcf", ".log"}


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def clean_text(value: str) -> str:
    value = html.unescape(value.replace("\x00", " "))
    value = re.sub(r"<[^>]+>", " ", value)
    value = re.sub(r"[\t\r ]+", " ", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip()


def extract_text(filename: str, data: bytes, *, max_chars: int = 500_000) -> str:
    suffix = Path(filename).suffix.casefold()
    if suffix in _TEXT_SUFFIXES:
        return clean_text(_decode_text(data))[:max_chars]
    if suffix == ".docx":
        return _docx_text(data)[:max_chars]
    if suffix == ".xlsx":
        return _xlsx_text(data)[:max_chars]
    if suffix == ".pdf":
        return _pdf_text(data)[:max_chars]
    return ""


def _decode_text(data: bytes) -> str:
    for encoding in ("utf-8", "utf-8-sig", "cp1252", "latin-1"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _docx_text(data: bytes) -> str:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            xml = archive.read("word/document.xml")
        root = ElementTree.fromstring(xml)
        texts = [node.text or "" for node in root.iter() if node.tag.endswith("}t")]
        return clean_text("\n".join(texts))
    except Exception:
        return ""


def _xlsx_text(data: bytes) -> str:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            shared: list[str] = []
            if "xl/sharedStrings.xml" in archive.namelist():
                root = ElementTree.fromstring(archive.read("xl/sharedStrings.xml"))
                for si in root:
                    shared.append("".join(node.text or "" for node in si.iter() if node.tag.endswith("}t")))
            values: list[str] = []
            for name in sorted(item for item in archive.namelist() if item.startswith("xl/worksheets/sheet") and item.endswith(".xml")):
                root = ElementTree.fromstring(archive.read(name))
                for cell in root.iter():
                    if not cell.tag.endswith("}c"):
                        continue
                    cell_type = cell.attrib.get("t")
                    value = next((node.text for node in cell if node.tag.endswith("}v")), None)
                    if value is None:
                        continue
                    if cell_type == "s":
                        try:
                            values.append(shared[int(value)])
                        except (ValueError, IndexError):
                            values.append(value)
                    else:
                        values.append(value)
            return clean_text("\n".join(values))
    except Exception:
        return ""


def _pdf_text(data: bytes) -> str:
    binary = shutil.which("pdftotext")
    if not binary:
        return ""
    with tempfile.TemporaryDirectory(prefix="assistant-pdf-") as temp_dir:
        source = Path(temp_dir) / "document.pdf"
        target = Path(temp_dir) / "document.txt"
        source.write_bytes(data)
        try:
            subprocess.run(
                [binary, "-layout", str(source), str(target)],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=60,
            )
            return clean_text(target.read_text(encoding="utf-8", errors="replace"))
        except (subprocess.SubprocessError, OSError):
            return ""


def chunks(text: str, *, size: int, overlap: int) -> list[str]:
    normalized = clean_text(text)
    if not normalized:
        return []
    result: list[str] = []
    start = 0
    while start < len(normalized):
        end = min(len(normalized), start + size)
        if end < len(normalized):
            boundary = normalized.rfind("\n", start + size // 2, end)
            if boundary < 0:
                boundary = normalized.rfind(" ", start + size // 2, end)
            if boundary > start:
                end = boundary
        part = normalized[start:end].strip()
        if part:
            result.append(part)
        if end >= len(normalized):
            break
        start = max(start + 1, end - overlap)
    return result
