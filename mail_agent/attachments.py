from __future__ import annotations

from dataclasses import dataclass
from email import policy
from email.message import Message
from email.parser import BytesParser

from .models import ParsedMessage
from .utils import decode_header_value, safe_filename, sha256_bytes


@dataclass(slots=True, frozen=True)
class ExtractedAttachment:
    filename: str
    content_type: str
    data: bytes
    sha256: str

    @property
    def size(self) -> int:
        return len(self.data)

    @property
    def safe_name(self) -> str:
        return safe_filename(self.filename, "attachment.bin")


def _safe_payload(part: Message) -> bytes:
    try:
        payload = part.get_payload(decode=True)
    except Exception:
        payload = b""
    if isinstance(payload, bytes):
        return payload
    if isinstance(payload, str):
        return payload.encode("utf-8", errors="replace")
    return b""


def _physical_parts(part: Message):
    content_type = str(part.get_content_type() or "application/octet-stream").casefold()
    if content_type == "message/rfc822":
        try:
            yield part, part.as_bytes(policy=policy.default)
        except Exception:
            yield part, _safe_payload(part)
        return
    try:
        multipart = part.is_multipart()
    except Exception:
        multipart = False
    if multipart:
        try:
            children = list(part.iter_parts())
        except Exception:
            children = []
        for child in children:
            yield from _physical_parts(child)
        return
    yield part, _safe_payload(part)


def extract_all_attachments(message: ParsedMessage) -> list[ExtractedAttachment]:
    """Extract physical attachments for explicit antivirus scanning.

    The complete raw RFC822 message is scanned separately. This function adds
    per-attachment evidence and names without trusting file extensions.
    """
    try:
        parsed = BytesParser(policy=policy.default).parsebytes(message.raw)
    except Exception:
        return []

    found: list[ExtractedAttachment] = []
    index = 0
    for part, payload in _physical_parts(parsed):
        try:
            filename = decode_header_value(part.get_filename())
        except Exception:
            filename = ""
        try:
            disposition = str(part.get_content_disposition() or "").casefold()
        except Exception:
            disposition = ""
        content_type = str(part.get_content_type() or "application/octet-stream").casefold()
        is_attachment = bool(filename) or disposition == "attachment" or content_type == "message/rfc822"
        if not is_attachment or not payload:
            continue
        index += 1
        original_name = (filename or f"attachment-{index}.bin").replace("\x00", "").strip()
        original_name = original_name.replace("\\", "/").rsplit("/", 1)[-1][:500]
        found.append(
            ExtractedAttachment(
                filename=original_name,
                content_type=content_type,
                data=payload,
                sha256=sha256_bytes(payload),
            )
        )
    return found


def extract_pdf_attachments(message: ParsedMessage) -> list[ExtractedAttachment]:
    """Extract real PDF MIME parts from the original message.

    The agent never trusts the model to invent attachment bytes or paths. Only
    parts physically present in the original RFC822 message are returned.
    """
    found: list[ExtractedAttachment] = []
    for attachment in extract_all_attachments(message):
        is_pdf = (
            attachment.content_type == "application/pdf"
            or attachment.filename.casefold().endswith(".pdf")
        )
        if not is_pdf:
            continue
        if b"%PDF-" not in attachment.data[:1024]:
            continue
        filename = attachment.filename
        if not filename.casefold().endswith(".pdf"):
            filename += ".pdf"
        found.append(
            ExtractedAttachment(
                filename=filename,
                content_type="application/pdf",
                data=attachment.data,
                sha256=attachment.sha256,
            )
        )
    return found
