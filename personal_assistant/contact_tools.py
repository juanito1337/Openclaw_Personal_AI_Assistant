from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from email.utils import parseaddr
from typing import Any

from .contracts.ports import MailMessagePort

_EMAIL_RE = re.compile(r"^[^\s@<>]+@[^\s@<>]+\.[^\s@<>]+$")
_PHONE_LABEL_RE = re.compile(
    r"(?im)^\s*(?:tel(?:efon)?|phone|mobile|mobil|handy)\s*[:.\-]?\s*"
    r"(\+?[0-9][0-9 ()/\.\-]{5,}[0-9])\s*$"
)
_ORG_LABEL_RE = re.compile(
    r"(?im)^\s*(?:firma|unternehmen|company|organisation|organization)\s*[:.\-]?\s*(.{2,120})\s*$"
)
_COMPANY_SUFFIX_RE = re.compile(
    r"(?i)\b(?:gmbh(?:\s*&\s*co\.?\s*kg)?|ag|ug(?:\s*\(haftungsbeschraenkt\))?|"
    r"kg|ohg|gbr|se|e\.?v\.?|ltd\.?|limited|inc\.?|llc|corp\.?|s\.?a\.?|b\.?v\.?)\b"
)
_SIGNOFF_RE = re.compile(
    r"(?i)^(?:mit freundlichen gr(?:u|ü)(?:ss|ß)en|freundliche gr(?:u|ü)(?:ss|ß)e|"
    r"beste gr(?:u|ü)(?:ss|ß)e|viele gr(?:u|ü)(?:ss|ß)e|best regards|kind regards|regards|sincerely)[,!. ]*$"
)
_AUTOMATED_LOCAL_PARTS = {
    "noreply", "no-reply", "do-not-reply", "donotreply", "mailer-daemon", "postmaster",
    "notifications", "notification", "automated", "automatic", "system", "bounce",
}


@dataclass(slots=True, frozen=True)
class ContactCandidate:
    name: str
    emails: tuple[str, ...]
    phones: tuple[str, ...] = ()
    organization: str = ""
    source: str = "manual"
    source_hash: str = ""
    confidence: float = 0.0
    automated_sender: bool = False
    field_sources: dict[str, str] | None = None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["emails"] = list(self.emails)
        data["phones"] = list(self.phones)
        data["field_sources"] = dict(self.field_sources or {})
        return data


def normalize_email(value: str) -> str:
    _, parsed = parseaddr(str(value or ""))
    email = (parsed or str(value or "")).strip().casefold()
    if not _EMAIL_RE.match(email):
        raise ValueError(f"Ungueltige E-Mail-Adresse: {value}")
    return email


def normalize_phone(value: str) -> str:
    raw = " ".join(str(value or "").split()).strip()
    if not raw:
        return ""
    digits = re.sub(r"\D", "", raw)
    if len(digits) < 7 or len(digits) > 18:
        raise ValueError(f"Unplausible Telefonnummer: {value}")
    if raw.startswith("+"):
        return "+" + digits
    if raw.startswith("00"):
        return "+" + digits[2:]
    return raw


def is_automated_email(email: str) -> bool:
    local = normalize_email(email).split("@", 1)[0]
    normalized = re.sub(r"[._]+", "-", local)
    return normalized in _AUTOMATED_LOCAL_PARTS or any(
        token in normalized for token in ("no-reply", "noreply", "do-not-reply", "donotreply", "mailer-daemon")
    )


def _clean_name(value: str) -> str:
    cleaned = " ".join(str(value or "").replace("\r", " ").replace("\n", " ").split()).strip(" ,;\t")
    if not cleaned or "@" in cleaned or len(cleaned) > 150:
        return ""
    return cleaned


def _signature_lines(body: str) -> list[str]:
    lines = [" ".join(line.split()).strip() for line in str(body or "").splitlines()]
    lines = [line for line in lines if line]
    return lines[-60:]


def _signature_name(lines: list[str]) -> str:
    for index, line in enumerate(lines):
        if not _SIGNOFF_RE.match(line):
            continue
        for candidate in lines[index + 1:index + 5]:
            cleaned = _clean_name(candidate)
            if not cleaned:
                continue
            if _COMPANY_SUFFIX_RE.search(cleaned) or re.search(r"(?i)\b(?:tel|phone|mobile|www\.|http)\b", cleaned):
                continue
            return cleaned
    return ""


def _organization(body: str, lines: list[str]) -> tuple[str, str]:
    labeled = _ORG_LABEL_RE.search(body or "")
    if labeled:
        value = _clean_name(labeled.group(1))
        if value:
            return value, "mail-signature-explicit"
    for line in reversed(lines):
        if len(line) <= 120 and _COMPANY_SUFFIX_RE.search(line) and "@" not in line:
            value = _clean_name(line)
            if value:
                return value, "mail-signature-company-line"
    return "", ""


def _phones(body: str) -> tuple[str, ...]:
    values: list[str] = []
    for match in _PHONE_LABEL_RE.finditer(body or ""):
        try:
            phone = normalize_phone(match.group(1))
        except ValueError:
            continue
        if phone and phone not in values:
            values.append(phone)
        if len(values) >= 5:
            break
    return tuple(values)


def candidate_from_mail(message: MailMessagePort) -> ContactCandidate:
    email = normalize_email(message.sender_addr)
    lines = _signature_lines(message.body_text)
    header_name = _clean_name(message.sender_name)
    name = header_name
    name_source = "mail-from-header" if name else ""
    if not name:
        name = _signature_name(lines)
        name_source = "mail-signature" if name else ""
    organization, organization_source = _organization(message.body_text, lines)
    phones = _phones("\n".join(lines))
    automated = is_automated_email(email)
    confidence = 1.0 if name and header_name else 0.82 if name else 0.55
    source_hash = hashlib.sha256(
        (message.stable_key or message.message_id or f"{email}\0{message.subject}").encode("utf-8", errors="replace")
    ).hexdigest()
    field_sources = {
        "email": "mail-from-header",
        "name": name_source,
        "organization": organization_source,
        "phone": "mail-signature-labeled" if phones else "",
    }
    return ContactCandidate(
        name=name,
        emails=(email,),
        phones=phones,
        organization=organization,
        source="mail",
        source_hash=source_hash,
        confidence=confidence,
        automated_sender=automated,
        field_sources=field_sources,
    )


def candidate_manual(
    *,
    name: str,
    emails: Iterable[str],
    phones: Iterable[str] = (),
    organization: str = "",
    source: str = "manual",
    source_hash: str = "",
) -> ContactCandidate:
    clean_name = _clean_name(name)
    if not clean_name:
        raise ValueError("Kontaktname fehlt oder ist ungueltig")
    clean_emails = tuple(dict.fromkeys(normalize_email(value) for value in emails if str(value).strip()))
    if not clean_emails:
        raise ValueError("Mindestens eine E-Mail-Adresse ist erforderlich")
    clean_phones = tuple(dict.fromkeys(normalize_phone(value) for value in phones if str(value).strip()))
    clean_org = _clean_name(organization)
    return ContactCandidate(
        name=clean_name,
        emails=clean_emails,
        phones=clean_phones,
        organization=clean_org,
        source=source,
        source_hash=source_hash,
        confidence=1.0,
        automated_sender=all(is_automated_email(value) for value in clean_emails),
        field_sources={
            "name": "explicit-user-input",
            "email": "explicit-user-input",
            "phone": "explicit-user-input" if clean_phones else "",
            "organization": "explicit-user-input" if clean_org else "",
        },
    )


def _vcard_escape(value: str) -> str:
    return (
        str(value or "")
        .replace("\\", "\\\\")
        .replace("\r", "")
        .replace("\n", "\\n")
        .replace(";", "\\;")
        .replace(",", "\\,")
    )


def _split_name(name: str) -> tuple[str, str, tuple[str, ...]]:
    clean = _clean_name(name)
    if "," in clean:
        last, first = [part.strip() for part in clean.split(",", 1)]
        return last, first, ()
    parts = clean.split()
    if len(parts) <= 1:
        return clean, "", ()
    return parts[-1], parts[0], tuple(parts[1:-1])


def _fold_vcard_line(line: str, limit: int = 72) -> list[str]:
    if len(line) <= limit:
        return [line]
    parts = [line[:limit]]
    remainder = line[limit:]
    while remainder:
        parts.append(" " + remainder[: limit - 1])
        remainder = remainder[limit - 1:]
    return parts


def build_vcard(candidate: ContactCandidate, uid: str, *, note: str = "") -> str:
    last, first, middle = _split_name(candidate.name)
    n_value = ";".join(
        _vcard_escape(value)
        for value in (last, first, " ".join(middle), "", "")
    )
    lines = [
        "BEGIN:VCARD",
        "VERSION:3.0",
        f"UID:{_vcard_escape(uid)}",
        f"FN:{_vcard_escape(candidate.name)}",
        f"N:{n_value}",
    ]
    for email in candidate.emails:
        lines.append(f"EMAIL;TYPE=INTERNET:{_vcard_escape(email)}")
    for phone in candidate.phones:
        lines.append(f"TEL;TYPE=VOICE:{_vcard_escape(phone)}")
    if candidate.organization:
        lines.append(f"ORG:{_vcard_escape(candidate.organization)}")
    if note.strip():
        lines.append(f"NOTE:{_vcard_escape(note.strip())}")
    lines.append(f"X-OPENCLAW-SOURCE:{_vcard_escape(candidate.source)}")
    if candidate.source_hash:
        lines.append(f"X-OPENCLAW-SOURCE-HASH:{_vcard_escape(candidate.source_hash)}")
    lines.extend([
        "REV:" + datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ"),
        "END:VCARD",
    ])
    folded: list[str] = []
    for line in lines:
        folded.extend(_fold_vcard_line(line))
    return "\r\n".join(folded) + "\r\n"


def _vcard_unfold(raw: str) -> list[str]:
    normalized = str(raw or "").replace("\r\n", "\n").replace("\r", "\n")
    unfolded: list[str] = []
    for line in normalized.split("\n"):
        if line.startswith((" ", "\t")) and unfolded:
            unfolded[-1] += line[1:]
        elif line:
            unfolded.append(line)
    return unfolded


def _vcard_property_name(line: str) -> str:
    head = str(line or "").split(":", 1)[0]
    base = head.split(";", 1)[0]
    return base.rsplit(".", 1)[-1].upper()


def _validated_optional_name(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = _clean_name(value)
    if not cleaned:
        raise ValueError("Kontaktname darf nicht leer oder ungueltig sein")
    return cleaned


def normalize_contact_update(
    *,
    name: str | None = None,
    emails: Iterable[str] | None = None,
    phones: Iterable[str] | None = None,
    organization: str | None = None,
    note: str | None = None,
) -> dict[str, Any]:
    """Validate a partial contact update.

    ``None`` means leave the field unchanged. An empty tuple/string explicitly
    clears the corresponding optional field. The contact name cannot be
    cleared. The result is JSON-serializable for the audited ActionPlan.
    """
    result: dict[str, Any] = {}
    clean_name = _validated_optional_name(name)
    if clean_name is not None:
        result["name"] = clean_name
    if emails is not None:
        result["emails"] = list(dict.fromkeys(
            normalize_email(value) for value in emails if str(value).strip()
        ))
    if phones is not None:
        result["phones"] = list(dict.fromkeys(
            normalize_phone(value) for value in phones if str(value).strip()
        ))
    if organization is not None:
        raw_org = str(organization or "")
        clean_org = _clean_name(raw_org) if raw_org.strip() else ""
        if raw_org.strip() and not clean_org:
            raise ValueError("Organisation ist ungueltig")
        result["organization"] = clean_org
    if note is not None:
        clean_note = str(note or "").replace("\r", "").strip()
        if len(clean_note) > 4000:
            raise ValueError("Kontaktnotiz ist auf 4000 Zeichen begrenzt")
        result["note"] = clean_note
    if not result:
        raise ValueError("Mindestens ein Kontaktfeld muss geaendert werden")
    return result


def update_vcard(raw: str, uid: str, changes: dict[str, Any]) -> str:
    """Patch selected vCard fields while preserving all unrelated properties.

    Existing photos, addresses, birthdays, categories, custom Nextcloud fields
    and unknown extensions remain byte-semantically represented as unfolded
    properties. The UID and object href are never changed.
    """
    lines = _vcard_unfold(raw)
    if not lines or lines[0].strip().upper() != "BEGIN:VCARD" or lines[-1].strip().upper() != "END:VCARD":
        raise ValueError("Bestehender CardDAV-Kontakt enthaelt keine gueltige vCard")
    current_uid = ""
    for line in lines:
        if _vcard_property_name(line) == "UID" and ":" in line:
            current_uid = line.split(":", 1)[1].strip()
            break
    if current_uid and current_uid != uid:
        raise ValueError("Kontakt-UID stimmt nicht mit der ausgewaehlten vCard ueberein")

    targeted: set[str] = {"REV"}
    if "name" in changes:
        targeted.update({"FN", "N"})
    if "emails" in changes:
        targeted.add("EMAIL")
    if "phones" in changes:
        targeted.add("TEL")
    if "organization" in changes:
        targeted.add("ORG")
    if "note" in changes:
        targeted.add("NOTE")

    preserved: list[str] = []
    for line in lines[1:-1]:
        prop = _vcard_property_name(line)
        if prop in targeted:
            continue
        preserved.append(line)

    additions: list[str] = []
    if not current_uid:
        additions.append(f"UID:{_vcard_escape(uid)}")
    if "name" in changes:
        name = str(changes["name"])
        last, first, middle = _split_name(name)
        n_value = ";".join(
            _vcard_escape(value) for value in (last, first, " ".join(middle), "", "")
        )
        additions.extend([f"FN:{_vcard_escape(name)}", f"N:{n_value}"])
    for email in changes.get("emails", []) if "emails" in changes else []:
        additions.append(f"EMAIL;TYPE=INTERNET:{_vcard_escape(str(email))}")
    for phone in changes.get("phones", []) if "phones" in changes else []:
        additions.append(f"TEL;TYPE=VOICE:{_vcard_escape(str(phone))}")
    if "organization" in changes and str(changes["organization"]):
        additions.append(f"ORG:{_vcard_escape(str(changes['organization']))}")
    if "note" in changes and str(changes["note"]):
        additions.append(f"NOTE:{_vcard_escape(str(changes['note']))}")
    additions.append("REV:" + datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ"))

    # Keep VERSION and UID near the top, then place changed canonical fields
    # before the remaining preserved properties. This avoids changing the UID
    # or dropping non-modeled Nextcloud/vCard data.
    prefix: list[str] = []
    rest: list[str] = []
    for line in preserved:
        if _vcard_property_name(line) in {"VERSION", "UID"}:
            prefix.append(line)
        else:
            rest.append(line)
    rendered = ["BEGIN:VCARD", *prefix, *additions, *rest, "END:VCARD"]
    folded: list[str] = []
    for line in rendered:
        folded.extend(_fold_vcard_line(line))
    return "\r\n".join(folded) + "\r\n"
