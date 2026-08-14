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
SUBJECT_PATTERN_VERSION_LEGACY = 1
SUBJECT_PATTERN_VERSION_CURRENT = 2
SUBJECT_PATTERN_VERSIONS = frozenset(
    {SUBJECT_PATTERN_VERSION_LEGACY, SUBJECT_PATTERN_VERSION_CURRENT}
)

# Version 1 is deliberately frozen. Persisted legacy corrections must keep their
# original matching semantics even when the current normalizer evolves.
_V1_NUMBER_RE = re.compile(r"\b\d{2,}\b")
_V1_DATE_RE = re.compile(r"\b(?:\d{1,2}[./-]){2}\d{2,4}\b|\b\d{4}-\d{2}-\d{2}\b")
_V1_CURRENCY_RE = re.compile(
    r"(?<!\w)\d+(?:[.,]\d{1,2})?\s*(?:eur|usd|gbp|€|\$|£)\b", re.IGNORECASE
)
_V1_ORDER_TOKEN_RE = re.compile(
    r"\b(?=[a-z0-9-]{8,}\b)(?=[a-z0-9-]*\d)[a-z0-9]+(?:-[a-z0-9]+)+\b",
    re.IGNORECASE,
)
_V1_LONG_TOKEN_RE = re.compile(r"\b[a-f0-9]{10,}\b", re.IGNORECASE)

_UUID_RE = re.compile(
    r"\b[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\b",
    re.IGNORECASE,
)
_DATE_NUMERIC_RE = re.compile(
    r"\b(?:\d{1,2}[./-]){2}\d{2,4}\b|\b\d{4}-\d{2}-\d{2}\b"
)
_MONTH = (
    r"jan(?:uary|uar)?|feb(?:ruary|ruar)?|mar(?:ch|z|\u00e4rz)?|apr(?:il)?|may|mai|"
    r"jun(?:e|i)?|jul(?:y|i)?|aug(?:ust)?|sep(?:tember)?|oct(?:ober)?|okt(?:ober)?|"
    r"nov(?:ember)?|dec(?:ember)?|dez(?:ember)?"
)
_DATE_TEXT_RE = re.compile(
    rf"\b(?:{_MONTH})\s+\d{{1,2}}(?:st|nd|rd|th)?(?:,?\s+\d{{4}})?\b|"
    rf"\b\d{{1,2}}(?:st|nd|rd|th)?\s+(?:{_MONTH})(?:\s+\d{{4}})?\b",
    re.IGNORECASE,
)
_TIME_RE = re.compile(
    r"\b(?:[01]?\d|2[0-3])[:.]\d{2}(?:\s*(?:uhr|am|pm))?\b|"
    r"\b(?:0?[1-9]|1[0-2])\s*(?:am|pm)\b",
    re.IGNORECASE,
)
_AMOUNT_RE = re.compile(
    r"(?<!\w)(?:(?:eur|usd|gbp)\s*|[€$£]\s*)"
    r"\d{1,3}(?:[.,\s]\d{3})*(?:[.,]\d{1,2})?(?!\w)|"
    r"(?<!\w)\d{1,3}(?:[.,\s]\d{3})*(?:[.,]\d{1,2})?\s*(?:eur|usd|gbp|€|\$|£)(?!\w)",
    re.IGNORECASE,
)
_INVOICE_ID_RE = re.compile(
    r"\b(?:invoice(?:\s*(?:number|no\.?))?|rechnung(?:s?(?:nummer|nr\.?)|\s*nr\.?)?)"
    r"\s*[:#-]?\s*"
    r"(?=[a-z0-9./_-]{4,}\b)(?=[a-z0-9./_-]*\d)[a-z0-9][a-z0-9./_-]*\b",
    re.IGNORECASE,
)
_ORDER_ID_RE = re.compile(
    r"\b(?:order(?:\s*(?:number|no\.?))?|bestell(?:ung(?:snummer)?|nummer))"
    r"\s*[:#-]?\s*"
    r"(?=[a-z0-9./_-]{4,}\b)(?=[a-z0-9./_-]*\d)[a-z0-9][a-z0-9./_-]*\b",
    re.IGNORECASE,
)
_TRACKING_ID_RE = re.compile(
    r"\b(?:tracking(?:\s*(?:code|id|number|no\.?))?|"
    r"sendung(?:s(?:nummer|id)|\s*(?:nummer|id))?|"
    r"shipment(?:\s*(?:code|id|number|no\.?))?)\s*[:#-]?\s*"
    r"(?=[a-z0-9./_-]{6,}\b)(?=[a-z0-9./_-]*\d)[a-z0-9][a-z0-9./_-]*\b",
    re.IGNORECASE,
)
_GENERIC_TOKEN_RE = re.compile(
    r"\b(?=[a-z0-9_-]{8,}\b)(?=[a-z0-9_-]*\d)(?=[a-z0-9_-]*[a-z])"
    r"[a-z0-9]+(?:[-_][a-z0-9]+)+\b",
    re.IGNORECASE,
)
_LONG_HEX_RE = re.compile(r"\b(?=[a-f0-9]{10,}\b)(?=[a-f0-9]*\d)[a-f0-9]+\b", re.IGNORECASE)
_LONG_NUMERIC_ID_RE = re.compile(r"\b\d{6,}\b")
_NUMBER_RE = re.compile(r"\b\d{2,}\b")
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


def normalize_subject_pattern_v1(subject: str) -> str:
    """Return the frozen legacy pattern used by already persisted corrections."""
    # Subprocess output may contain surrogate-escaped bytes when a remote mail
    # header is not valid UTF-8. SQLite rejects such strings, so normalize them
    # deterministically before matching or persistence.
    clean = utf8_clean(subject)
    value = _PREFIX_RE.sub("", clean).strip().lower()
    value = _V1_DATE_RE.sub("<date>", value)
    value = _V1_CURRENCY_RE.sub("<amount>", value)
    value = _V1_ORDER_TOKEN_RE.sub("<id>", value)
    value = _V1_LONG_TOKEN_RE.sub("<token>", value)
    value = _V1_NUMBER_RE.sub("<n>", value)
    value = _WS_RE.sub(" ", value)
    return value[:500]


def normalize_subject_pattern_v2(subject: str) -> str:
    """Normalize volatile subject values into typed, deterministic placeholders."""
    clean = utf8_clean(subject)
    value = _PREFIX_RE.sub("", clean).strip().lower()
    value = _UUID_RE.sub("<uuid>", value)
    value = _DATE_NUMERIC_RE.sub("<date>", value)
    value = _DATE_TEXT_RE.sub("<date>", value)
    value = _TIME_RE.sub("<time>", value)
    value = _AMOUNT_RE.sub("<amount>", value)
    value = _INVOICE_ID_RE.sub("invoice <invoice-id>", value)
    value = _ORDER_ID_RE.sub("order <order-id>", value)
    value = _TRACKING_ID_RE.sub("tracking <tracking-id>", value)
    value = _GENERIC_TOKEN_RE.sub("<token>", value)
    value = _LONG_HEX_RE.sub("<token>", value)
    value = _LONG_NUMERIC_ID_RE.sub("<id>", value)
    value = _NUMBER_RE.sub("<n>", value)
    value = _WS_RE.sub(" ", value)
    return value[:500]


def normalize_subject_pattern(subject: str, *, version: int = SUBJECT_PATTERN_VERSION_CURRENT) -> str:
    """Return a versioned privacy-safe semantic subject pattern."""
    if int(version) == SUBJECT_PATTERN_VERSION_LEGACY:
        return normalize_subject_pattern_v1(subject)
    if int(version) == SUBJECT_PATTERN_VERSION_CURRENT:
        return normalize_subject_pattern_v2(subject)
    raise ValueError(f"Nicht unterstuetzte Betreffmusterversion: {version}")


def subject_patterns(subject: str) -> dict[int, str]:
    """Return every supported representation for version-aware legacy matching."""
    return {
        version: normalize_subject_pattern(subject, version=version)
        for version in sorted(SUBJECT_PATTERN_VERSIONS)
    }


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
