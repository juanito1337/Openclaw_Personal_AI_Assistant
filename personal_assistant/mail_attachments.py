from __future__ import annotations

from dataclasses import dataclass
from email import policy
from email.message import Message
from email.parser import BytesParser
from typing import Any


@dataclass(frozen=True, slots=True)
class PhysicalMailAttachment:
    name: str
    data: bytes


def _payload(part: Message) -> bytes:
    try:
        value = part.get_payload(decode=True)
    except Exception:
        return b""
    if isinstance(value, bytes):
        return value
    if isinstance(value, str):
        return value.encode(errors="replace")
    return b""


def _parts(part: Message):
    if str(part.get_content_type() or "").casefold() == "message/rfc822":
        try:
            yield part, part.as_bytes(policy=policy.default)
        except Exception:
            yield part, _payload(part)
        return
    try:
        children = list(part.get_payload()) if part.is_multipart() else []
    except Exception:
        children = []
    if children:
        for child in children:
            if isinstance(child, Message):
                yield from _parts(child)
        return
    yield part, _payload(part)


def physical_mail_attachments(message: Any) -> list[PhysicalMailAttachment]:
    """Return every physical attachment without importing the mail adapter."""

    try:
        parsed = BytesParser(policy=policy.default).parsebytes(bytes(message.raw))
    except Exception:
        return []
    result: list[PhysicalMailAttachment] = []
    for index, (part, data) in enumerate(_parts(parsed), start=1):
        filename = str(part.get_filename() or "").replace("\x00", "").strip()
        disposition = str(part.get_content_disposition() or "").casefold()
        content_type = str(part.get_content_type() or "application/octet-stream").casefold()
        if not (
            filename
            or disposition == "attachment"
            or content_type == "message/rfc822"
            or content_type not in {"text/plain", "text/html"}
        ) or not data:
            continue
        safe_name = filename.replace("\\", "/").rsplit("/", 1)[-1][:500]
        result.append(
            PhysicalMailAttachment(
                name=safe_name or f"attachment-{index}.bin",
                data=data,
            )
        )
    return result


__all__ = ["PhysicalMailAttachment", "physical_mail_attachments"]
