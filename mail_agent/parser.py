from __future__ import annotations

import re
from collections.abc import Iterable
from email import policy
from email.message import Message
from email.parser import BytesParser
from email.utils import getaddresses, parseaddr

from .models import AttachmentInfo, Envelope, ParsedMessage
from .utils import decode_header_value, html_to_text, stable_message_key, utf8_clean

_MESSAGE_ID_TOKEN = re.compile(r"<([^<>\r\n]{1,998})>")


def _decode_bytes(payload: bytes, charset: str | None = None) -> str:
    """Decode malformed mail text without letting bogus charset names abort parsing."""
    candidates: list[str] = []
    if charset:
        cleaned = str(charset).strip().strip('\"\'[](){}<>')
        if cleaned:
            candidates.append(cleaned)
    candidates.extend(["utf-8", "windows-1252", "latin-1"])

    seen: set[str] = set()
    for candidate in candidates:
        key = candidate.casefold()
        if key in seen:
            continue
        seen.add(key)
        try:
            return payload.decode(candidate, errors="replace")
        except (LookupError, UnicodeError):
            continue
    return payload.decode("utf-8", errors="replace")


def _header_values(message: Message, name: str) -> list[str]:
    """Return raw header values without triggering strict structured-header parsing.

    Real-world mailboxes contain malformed address and Message-ID headers.  The
    modern header registry can raise IndexError while merely accessing such a
    header.  compat32 normally avoids that path; raw_items is an additional
    guard and preserves the original text for our tolerant decoders.
    """
    wanted = name.casefold()
    try:
        return [str(value) for key, value in message.raw_items() if str(key).casefold() == wanted]
    except Exception:
        try:
            return [str(value) for value in (message.get_all(name, []) or [])]
        except Exception:
            return []


def _header_value(message: Message, name: str) -> str:
    values = _header_values(message, name)
    return decode_header_value(values[0]) if values else ""


def _message_id_tokens(message: Message, name: str, *, limit: int) -> list[str]:
    """Parse malformed real-world relationship headers without aborting a mail."""

    result: list[str] = []
    seen: set[str] = set()
    for raw in _header_values(message, name)[:20]:
        decoded = decode_header_value(raw).replace("\r", " ").replace("\n", " ")
        candidates = _MESSAGE_ID_TOKEN.findall(decoded)
        if not candidates:
            candidates = decoded.split()
        for candidate in candidates:
            normalized = "".join(str(candidate).strip("<>").split())[:998]
            if "@" not in normalized or normalized in seen:
                continue
            seen.add(normalized)
            result.append(normalized)
            if len(result) >= limit:
                return result
    return result


def _parseaddr_loose(value: str) -> tuple[str, str]:
    try:
        return parseaddr(value or "", strict=False)
    except TypeError:  # Python versions before the strict= parameter
        try:
            return parseaddr(value or "")
        except Exception:
            return "", ""
    except Exception:
        return "", ""


def _getaddresses_loose(values: Iterable[str]) -> list[tuple[str, str]]:
    materialized = [str(value) for value in values if value is not None]
    try:
        return getaddresses(materialized, strict=False)
    except TypeError:
        try:
            return getaddresses(materialized)
        except Exception:
            return []
    except Exception:
        return []


def _safe_content_type(part: Message) -> str:
    try:
        value = part.get_content_type()
    except Exception:
        value = ""
    value = str(value or "application/octet-stream").strip().lower()
    return value if "/" in value else "application/octet-stream"


def _safe_disposition(part: Message) -> str | None:
    try:
        value = part.get_content_disposition()
        if value:
            return str(value).lower()
    except Exception:
        pass
    raw = _header_value(part, "Content-Disposition")
    if not raw:
        return None
    token = raw.split(";", 1)[0].strip().lower()
    return token or None


def _safe_filename(part: Message) -> str:
    try:
        return decode_header_value(part.get_filename())
    except Exception:
        return ""


def _decode_part(part: Message) -> str:
    try:
        content = part.get_content()  # available with modern policies
        if isinstance(content, str):
            return content
        if isinstance(content, bytes):
            return _decode_bytes(content, part.get_content_charset())
    except Exception:
        pass

    try:
        payload = part.get_payload(decode=True)
    except Exception:
        try:
            payload = part.get_payload()
        except Exception:
            payload = b""
    if isinstance(payload, bytes):
        try:
            charset = part.get_content_charset()
        except Exception:
            charset = None
        return _decode_bytes(payload, charset)
    if isinstance(payload, str):
        return payload
    return ""


def _walk_parts(message: Message) -> list[Message]:
    try:
        return list(message.walk())
    except Exception:
        return [message]


def _body_text(message: Message) -> str:
    plain_parts: list[str] = []
    html_parts: list[str] = []
    try:
        multipart = message.is_multipart()
    except Exception:
        multipart = False
    if multipart:
        for part in _walk_parts(message):
            try:
                if part.is_multipart():
                    continue
            except Exception:
                pass
            disposition = _safe_disposition(part)
            filename = _safe_filename(part)
            if disposition == "attachment" or filename:
                continue
            content_type = _safe_content_type(part)
            if content_type == "text/plain":
                plain_parts.append(_decode_part(part))
            elif content_type == "text/html":
                html_parts.append(html_to_text(_decode_part(part)))
    else:
        value = _decode_part(message)
        if _safe_content_type(message) == "text/html":
            html_parts.append(html_to_text(value))
        else:
            plain_parts.append(value)
    text = "\n\n".join(item.strip() for item in plain_parts if item.strip())
    if not text:
        text = "\n\n".join(item.strip() for item in html_parts if item.strip())
    return utf8_clean(text.replace("\x00", "").strip())


def parse_eml(raw: bytes, envelope: Envelope, source_folder: str) -> ParsedMessage:
    # compat32 deliberately keeps headers as tolerant strings.  Strict structured
    # parsing is unsuitable for archival mail because malformed From/To/Message-ID
    # headers can otherwise abort processing of the entire message.
    message = BytesParser(policy=policy.compat32).parsebytes(raw)

    subject = _header_value(message, "Subject") or envelope.subject
    sender_name, sender_addr = _parseaddr_loose(_header_value(message, "From"))
    sender_name = sender_name or envelope.sender_name
    sender_addr = sender_addr or envelope.sender_addr

    recipient_headers: list[str] = []
    for key in ("To", "Cc"):
        recipient_headers.extend(_header_values(message, key))
    recipients = [
        addr.strip()
        for _, addr in _getaddresses_loose(recipient_headers)
        if addr and addr.strip()
    ]

    attachments: list[AttachmentInfo] = []
    calendar_invites: list[str] = []
    for part in _walk_parts(message):
        try:
            if part.is_multipart():
                continue
        except Exception:
            pass
        filename = _safe_filename(part)
        content_type = _safe_content_type(part)
        try:
            payload = part.get_payload(decode=True) or b""
        except Exception:
            payload = b""
        if not isinstance(payload, bytes):
            payload = str(payload).encode("utf-8", errors="replace") if payload else b""
        if filename or _safe_disposition(part) == "attachment":
            attachments.append(AttachmentInfo(filename or "attachment", content_type, len(payload)))
        if content_type == "text/calendar" or filename.lower().endswith(".ics"):
            try:
                charset = part.get_content_charset()
            except Exception:
                charset = None
            calendar_invites.append(_decode_bytes(payload, charset))

    message_id = _header_value(message, "Message-ID")
    header_date = utf8_clean(_header_value(message, "Date") or envelope.date)
    received_at = utf8_clean(envelope.received_at or envelope.date or header_date)
    return ParsedMessage(
        stable_key=stable_message_key(message_id, raw),
        mailbox_id=envelope.mailbox_id,
        source_folder=source_folder,
        raw=raw,
        message_id=utf8_clean(message_id),
        subject=utf8_clean(subject),
        sender_name=decode_header_value(sender_name),
        sender_addr=utf8_clean(sender_addr).lower().strip(),
        recipients=[utf8_clean(item) for item in recipients],
        date=header_date,
        received_at=received_at,
        body_text=_body_text(message),
        attachments=attachments,
        calendar_invites=[utf8_clean(item) for item in calendar_invites],
        in_reply_to=_message_id_tokens(message, "In-Reply-To", limit=20),
        references=_message_id_tokens(message, "References", limit=50),
    )
