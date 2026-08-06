from __future__ import annotations

import hashlib
import html
import json
import re
from datetime import UTC, datetime
from email.header import decode_header, make_header
from pathlib import Path
from typing import Any

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")
_NUMBER_RE = re.compile(r"\b\d{2,}\b")
_DATE_RE = re.compile(r"\b(?:\d{1,2}[./-]){2}\d{2,4}\b|\b\d{4}-\d{2}-\d{2}\b")
_CURRENCY_RE = re.compile(r"(?<!\w)\d+(?:[.,]\d{1,2})?\s*(?:eur|usd|gbp|€|\$|£)\b", re.IGNORECASE)
_ORDER_TOKEN_RE = re.compile(r"\b(?=[a-z0-9-]{8,}\b)(?=[a-z0-9-]*\d)[a-z0-9]+(?:-[a-z0-9]+)+\b", re.IGNORECASE)
_LONG_TOKEN_RE = re.compile(r"\b[a-f0-9]{10,}\b", re.IGNORECASE)
_PREFIX_RE = re.compile(r"^(?:(?:re|fw|fwd|aw|wg)\s*:\s*)+", re.IGNORECASE)


def now_utc_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def utf8_clean(value: Any) -> str:
    """Return text that can always be persisted as UTF-8.

    Python may represent undecodable subprocess or malformed mail-header bytes as
    surrogate code points. Replace only those invalid code points while preserving
    all valid Unicode characters.
    """
    if value is None:
        return ""
    return str(value).encode("utf-8", errors="replace").decode("utf-8")


def decode_header_value(value: Any) -> str:
    if value is None:
        return ""
    try:
        return utf8_clean(make_header(decode_header(str(value))))
    except Exception:
        return utf8_clean(value)


def html_to_text(value: str) -> str:
    value = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", value)
    value = re.sub(r"(?i)<br\s*/?>", "\n", value)
    value = re.sub(r"(?i)</p\s*>", "\n", value)
    value = _TAG_RE.sub(" ", value)
    value = html.unescape(value)
    lines = [_WS_RE.sub(" ", line).strip() for line in value.splitlines()]
    return "\n".join(line for line in lines if line)


def normalize_subject_pattern(subject: str) -> str:
    """Return a privacy-safe semantic subject pattern for correction learning."""
    # Subprocess output may contain surrogate-escaped bytes when a remote mail
    # header is not valid UTF-8. SQLite rejects such strings, so normalize them
    # deterministically before matching or persistence.
    clean = utf8_clean(subject)
    value = _PREFIX_RE.sub("", clean).strip().lower()
    value = _DATE_RE.sub("<date>", value)
    value = _CURRENCY_RE.sub("<amount>", value)
    value = _ORDER_TOKEN_RE.sub("<id>", value)
    value = _LONG_TOKEN_RE.sub("<token>", value)
    value = _NUMBER_RE.sub("<n>", value)
    value = _WS_RE.sub(" ", value)
    return value[:500]


def normalize_subject(subject: str) -> str:
    # Compatibility alias: all existing callers now benefit from the stronger
    # pattern normalization without changing stored raw subjects.
    return normalize_subject_pattern(subject)


def normalize_address(address: str) -> str:
    return utf8_clean(address).strip().lower()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def stable_message_key(message_id: str, raw: bytes) -> str:
    normalized = (message_id or "").strip().strip("<>").lower()
    if normalized:
        return "mid:" + normalized
    return "sha256:" + sha256_bytes(raw)


def clean_single_line(value: str, limit: int = 500) -> str:
    value = " ".join((value or "").replace("\r", " ").replace("\n", " ").split())
    return value[:limit]


def extract_json_object(text: str) -> dict[str, Any]:
    text = (text or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text)
    try:
        value = json.loads(text)
        if isinstance(value, dict):
            return value
    except json.JSONDecodeError:
        pass

    start = text.find("{")
    if start < 0:
        raise ValueError("Modellantwort enthaelt kein JSON-Objekt")
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                value = json.loads(text[start:index + 1])
                if not isinstance(value, dict):
                    raise ValueError("JSON-Antwort ist kein Objekt")
                return value
    raise ValueError("Unvollstaendiges JSON-Objekt in Modellantwort")


def atomic_write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_bytes(data)
    temp.replace(path)


def safe_filename(value: str, fallback: str = "file") -> str:
    value = re.sub(r"[^A-Za-z0-9._-]+", "-", value or "").strip(".-")
    return (value or fallback)[:180]
