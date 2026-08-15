from __future__ import annotations

import copy
import json
import re
import shutil
import subprocess
import tempfile
import unicodedata
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

from .config import InvoiceConfig
from .models import ParsedMessage

_DATE_TOKEN = re.compile(r"(?<!\d)(\d{1,2}[.\-/]\d{1,2}[.\-/](?:\d{2}|\d{4})|\d{4}-\d{1,2}-\d{1,2})(?!\d)")
_AMOUNT_TOKEN = re.compile(
    r"(?<![\dA-Za-z])(?:EUR\s*|€\s*)?(-?\d{1,3}(?:[.\s]\d{3})*(?:,\d{2})|-?\d+(?:[.,]\d{2}))(?:\s*(EUR|€))?(?!\d)",
    re.IGNORECASE,
)
_VAT_ID = re.compile(r"\b(?:DE\s*)?\d{9}\b", re.IGNORECASE)

_INVOICE_NUMBER_LABELS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\brechnungsnummer\b", re.IGNORECASE),
    re.compile(r"\brechnungs?[-\s]*(?:nr|no)\.?\b", re.IGNORECASE),
    re.compile(r"\binvoice\s*(?:number|no\.?|#|id)\b", re.IGNORECASE),
    re.compile(r"\bbeleg[-\s]*(?:nummer|nr\.?)\b", re.IGNORECASE),
    re.compile(r"\bfaktura[-\s]*(?:nummer|nr\.?)\b", re.IGNORECASE),
)
_NON_INVOICE_NUMBER_LABELS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "customer-number",
        re.compile(
            r"\b(?:kunden(?:nummer|[-\s]*nr\.?)|customer\s*(?:number|no\.?|id))\b",
            re.IGNORECASE,
        ),
    ),
    (
        "order-number",
        re.compile(
            r"\b(?:bestell(?:nummer|[-\s]*nr\.?)|order\s*(?:number|no\.?)|purchase\s*order)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "delivery-number",
        re.compile(
            r"\b(?:lieferschein(?:nummer|[-\s]*nr\.?)|delivery\s*(?:number|no\.?))\b",
            re.IGNORECASE,
        ),
    ),
    (
        "contract-number",
        re.compile(
            r"\b(?:vertrags(?:nummer|[-\s]*nr\.?)|contract\s*(?:number|no\.?))\b",
            re.IGNORECASE,
        ),
    ),
    ("phone-number", re.compile(r"\b(?:telefon|telephone|phone|tel\.?)\b", re.IGNORECASE)),
    (
        "tax-number",
        re.compile(
            r"\b(?:ust[-\s]*id|umsatzsteuer[-\s]*id|vat\s*id|steuernummer|tax\s*(?:number|no\.?))\b",
            re.IGNORECASE,
        ),
    ),
    (
        "tracking-number",
        re.compile(
            r"\b(?:sendungsnummer|tracking\s*(?:number|no\.?))\b",
            re.IGNORECASE,
        ),
    ),
    ("iban", re.compile(r"\biban\b", re.IGNORECASE)),
)
_OCR_SPACED_WORD = re.compile(r"(?<!\w)(?:[^\W\d_]\s+){2,}[^\W\d_](?!\w)", re.UNICODE)

_INVOICE_DATE_LABELS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\brechnungsdatum\b", re.IGNORECASE),
    re.compile(r"\bdatum\s+der\s+rechnung\b", re.IGNORECASE),
    re.compile(r"\brechnung\s+(?:vom|erstellt\s+am)\b", re.IGNORECASE),
    re.compile(r"\binvoice\s+(?:date|issued(?:\s+on)?)\b", re.IGNORECASE),
    re.compile(r"\bdocument\s+date\b", re.IGNORECASE),
    re.compile(r"\b(?:belegdatum|ausstellungsdatum)\b", re.IGNORECASE),
    re.compile(r"^\s*datum\s*(?=[:\-]|$)", re.IGNORECASE),
)
_NON_INVOICE_DATE_LABELS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "service-date",
        re.compile(
            r"\b(?:leistungsdatum|datum\s+der\s+leistung|service\s+date)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "delivery-date",
        re.compile(
            r"\b(?:lieferdatum|datum\s+der\s+lieferung|delivery\s+date)\b",
            re.IGNORECASE,
        ),
    ),
    ("order-date", re.compile(r"\b(?:bestelldatum|datum\s+der\s+bestellung|order\s+date)\b", re.IGNORECASE)),
    ("payment-date", re.compile(r"\b(?:zahlungsdatum|payment\s+date)\b", re.IGNORECASE)),
    (
        "due-date",
        re.compile(
            r"\b(?:f(?:a|ä|ae)llig(?:\s+am)?|zahlbar\s+bis|zahlungsziel|due\s+date|payment\s+due)\b",
            re.IGNORECASE,
        ),
    ),
)

_GROSS_ANCHORS = (
    "rechnungsbetrag", "gesamtbetrag", "endbetrag", "zahlbetrag", "zu zahlen", "bruttobetrag",
    "gesamtsumme", "summe brutto", "grand total", "total amount", "amount due", "invoice total",
)
_NET_ANCHORS = ("nettobetrag", "netto gesamt", "net amount", "subtotal", "zwischensumme")
_TAX_ANCHORS = ("umsatzsteuer", "mehrwertsteuer", "mwst", "ust", "vat")
_DUE_ANCHORS = ("faellig am", "fällig am", "zahlbar bis", "due date", "payment due")
_SUPPLIER_LABEL = re.compile(
    r"^\s*(?:rechnungssteller|lieferant|vendor|aussteller)\s*(?:(?::|-)\s*(.*))?$",
    re.IGNORECASE,
)
_COMPANY_SUFFIX = re.compile(
    r"\b(?:gmbh(?:\s*&\s*co\.?\s*kg)?|ag|kg|ohg|ug(?:\s*\(haftungsbeschraenkt\)|\s*\(haftungsbeschränkt\))?|"
    r"se|e\.?k\.?|ltd\.?|limited|inc\.?|llc|sarl|s\.?a\.?s\.?|b\.?v\.?)\b",
    re.IGNORECASE,
)
_SUPPLIER_EXCLUDE = (
    "rechnung", "invoice", "rechnungsnummer", "invoice number", "datum", "date", "leistungsdatum",
    "lieferdatum", "faellig", "fällig", "due", "gesamtbetrag", "bruttobetrag", "nettobetrag",
    "umsatzsteuer", "mehrwertsteuer", "ust", "vat", "iban", "bic", "kundennummer", "customer",
)

def _supplier_line_excluded(value: str) -> bool:
    folded = value.casefold()
    for marker in _SUPPLIER_EXCLUDE:
        escaped = re.escape(marker.casefold())
        if re.search(rf"(?<!\w){escaped}(?!\w)", folded):
            return True
    return False


_CATEGORY_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Software/IT", ("software", "hosting", "cloud", "domain", "lizenz", "license", "microsoft", "adobe", "it-service")),
    ("Telekommunikation", ("telefon", "mobilfunk", "internet", "telekom", "vodafone", "o2 ", "sim-karte")),
    ("Energie/Nebenkosten", ("strom", "gas", "energie", "wasser", "stadtwerke", "heizung", "fernwärme")),
    ("Material/Waren", ("material", "stahl", "blech", "werkstoff", "waren", "baustoff", "schrauben", "werkzeug")),
    ("Büro/Verbrauchsmaterial", ("büro", "buero", "papier", "toner", "drucker", "bueromarkt", "office")),
    ("Fahrzeug/Transport", ("kraftstoff", "diesel", "benzin", "tank", "kfz", "fahrzeug", "spedition", "fracht", "versand")),
    ("Reise/Bewirtung", ("hotel", "bahn", "flug", "restaurant", "bewirtung", "reise", "taxi")),
    ("Dienstleistungen", ("beratung", "dienstleistung", "service", "wartung", "reparatur", "consulting")),
    ("Versicherung/Gebühren", ("versicherung", "beitrag", "gebühr", "gebuehr", "bankgebühr", "kammer")),
    ("Miete/Immobilie", ("miete", "pacht", "nebenkosten", "grundsteuer", "immobilie")),
)


@dataclass(slots=True)
class FieldValue:
    value: str = ""
    confidence: float = 0.0
    evidence: str = ""


@dataclass(slots=True)
class FieldCandidate:
    field: str
    role: str
    raw_value: str
    normalized_value: str
    source: str
    evidence_type: str
    evidence: str
    confidence: float
    excluded_reason: str = ""


@dataclass(slots=True)
class InvoiceMetadata:
    invoice_date: FieldValue = field(default_factory=FieldValue)
    invoice_number: FieldValue = field(default_factory=FieldValue)
    supplier: FieldValue = field(default_factory=FieldValue)
    category: FieldValue = field(default_factory=FieldValue)
    gross_amount: FieldValue = field(default_factory=FieldValue)
    net_amount: FieldValue = field(default_factory=FieldValue)
    tax_amount: FieldValue = field(default_factory=FieldValue)
    currency: FieldValue = field(default_factory=lambda: FieldValue("EUR", 0.5, "Standardwaehrung"))
    due_date: FieldValue = field(default_factory=FieldValue)
    status: str = "review"
    confidence: float = 0.0
    method: str = "none"
    text_quality: float = 0.0
    issues: list[str] = field(default_factory=list)
    field_candidates: list[FieldCandidate] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True)

    @property
    def amount_cents(self) -> int | None:
        return amount_to_cents(self.gross_amount.value)

    @property
    def date_confirmed(self) -> bool:
        return bool(self.invoice_date.value and self.invoice_date.confidence >= 0.85)


def _clean_line(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def _normalize_ocr_spacing(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value or "")
    return _OCR_SPACED_WORD.sub(lambda match: re.sub(r"\s+", "", match.group(0)), normalized)


def _parse_date(value: str) -> date | None:
    raw = value.strip()
    for fmt in ("%d.%m.%Y", "%d-%m-%Y", "%d/%m/%Y", "%Y-%m-%d", "%d.%m.%y", "%d-%m-%y", "%d/%m/%y"):
        try:
            parsed = datetime.strptime(raw, fmt).date()
            if parsed.year < 1970 or parsed.year > 2100:
                return None
            return parsed
        except ValueError:
            continue
    return None


def amount_to_cents(value: str) -> int | None:
    raw = (value or "").strip().replace("EUR", "").replace("€", "").replace(" ", "")
    if not raw:
        return None
    if "," in raw:
        raw = raw.replace(".", "").replace(",", ".")
    else:
        parts = raw.split(".")
        if len(parts) > 2:
            raw = "".join(parts[:-1]) + "." + parts[-1]
    try:
        amount = Decimal(raw)
    except InvalidOperation:
        return None
    return int((amount * 100).quantize(Decimal("1")))


def _format_amount(cents: int | None) -> str:
    if cents is None:
        return ""
    return f"{Decimal(cents) / 100:.2f}"


def _received_date(message: ParsedMessage) -> date:
    from email.utils import parsedate_to_datetime
    try:
        value = parsedate_to_datetime(message.received_at or message.date)
        if value:
            return value.date()
    except (TypeError, ValueError, OverflowError):
        pass
    return datetime.now().astimezone().date()


def _lines(text: str) -> list[str]:
    normalized = _normalize_ocr_spacing((text or "").replace("\x00", " "))
    return [_clean_line(line) for line in normalized.splitlines() if _clean_line(line)]


def _date_field(
    lines: list[str], anchors: Iterable[str], *, received: date,
    negative: Iterable[str] = (), require_anchor: bool = False,
) -> FieldValue:
    candidates: list[tuple[float, date, str]] = []
    negative_fold = tuple(v.casefold() for v in negative)
    for index, line in enumerate(lines):
        folded = line.casefold()
        if any(marker in folded for marker in negative_fold):
            continue
        anchor_hit = any(
            bool(re.search(r"\bdatum\b", folded)) if marker == "datum" else marker in folded
            for marker in anchors
        )
        if require_anchor and not anchor_hit:
            continue
        search_lines = [(line, 0)]
        if anchor_hit and index + 1 < len(lines):
            search_lines.append((lines[index + 1], 1))
        if anchor_hit and index + 2 < len(lines):
            search_lines.append((lines[index + 2], 2))
        for candidate_line, distance in search_lines:
            candidate_folded = candidate_line.casefold()
            if any(marker in candidate_folded for marker in negative_fold):
                continue
            for match in _DATE_TOKEN.finditer(candidate_line):
                parsed = _parse_date(match.group(1))
                if not parsed:
                    continue
                score = 0.96 if anchor_hit and distance == 0 else 0.88 if anchor_hit and distance == 1 else 0.82 if anchor_hit else 0.35
                delta = (parsed - received).days
                if delta > 14:
                    score -= 0.25
                if delta < -3650:
                    score -= 0.25
                candidates.append((max(0.0, score), parsed, line[:300]))
    if not candidates:
        return FieldValue()
    candidates.sort(key=lambda item: item[0], reverse=True)
    best = candidates[0]
    high_values = {item[1] for item in candidates if item[0] >= best[0] - 0.05 and item[0] >= 0.75}
    if len(high_values) > 1:
        return FieldValue("", 0.3, "Mehrere gleich plausible Datumswerte")
    return FieldValue(best[1].isoformat(), best[0], best[2])


def _date_role(line: str) -> tuple[str, re.Match[str] | None]:
    for role, pattern in _NON_INVOICE_DATE_LABELS:
        match = pattern.search(line)
        if match:
            return role, match
    for pattern in _INVOICE_DATE_LABELS:
        match = pattern.search(line)
        if match:
            return "invoice-date", match
    return "", None


def _invoice_date_field(
    lines: list[str], *, received: date, source: str
) -> tuple[FieldValue, list[FieldCandidate]]:
    candidates: list[FieldCandidate] = []
    accepted: list[tuple[float, date, str, FieldCandidate]] = []
    observed: set[tuple[int, str]] = set()
    for index, line in enumerate(lines):
        role, label_match = _date_role(line)
        if not role or label_match is None:
            continue
        search_lines = [(index, line, 0)]
        if not _DATE_TOKEN.search(line) and index + 1 < len(lines):
            next_role, _ = _date_role(lines[index + 1])
            if not next_role:
                search_lines.append((index + 1, lines[index + 1], 1))
        for candidate_index, candidate_line, distance in search_lines:
            for match in _DATE_TOKEN.finditer(candidate_line):
                raw = match.group(1)
                parsed = _parse_date(raw)
                if not parsed:
                    continue
                observed.add((candidate_index, raw))
                score = 0.96 if distance == 0 else 0.88
                delta = (parsed - received).days
                if delta > 14:
                    score -= 0.25
                if delta < -3650:
                    score -= 0.25
                excluded = "" if role == "invoice-date" else f"not-invoice-date:{role}"
                candidate = FieldCandidate(
                    field="invoice_date",
                    role=role,
                    raw_value=raw,
                    normalized_value=parsed.isoformat(),
                    source=source,
                    evidence_type="labeled-same-line" if distance == 0 else "labeled-next-line",
                    evidence=line[:300],
                    confidence=max(0.0, score),
                    excluded_reason=excluded,
                )
                candidates.append(candidate)
                if not excluded:
                    accepted.append((candidate.confidence, parsed, line[:300], candidate))

    for index, line in enumerate(lines):
        for match in _DATE_TOKEN.finditer(line):
            raw = match.group(1)
            if (index, raw) in observed:
                continue
            parsed = _parse_date(raw)
            if parsed:
                candidates.append(
                    FieldCandidate(
                        field="invoice_date",
                        role="unlabeled-date",
                        raw_value=raw,
                        normalized_value=parsed.isoformat(),
                        source=source,
                        evidence_type="unlabeled-document-value",
                        evidence=line[:300],
                        confidence=0.35,
                        excluded_reason="missing-invoice-date-label",
                    )
                )

    if not accepted:
        unlabelled = [
            candidate
            for candidate in candidates
            if candidate.role == "unlabeled-date"
        ]
        if unlabelled:
            first = unlabelled[0]
            return (
                FieldValue(first.normalized_value, first.confidence, first.evidence),
                candidates,
            )
        return FieldValue(), candidates
    accepted.sort(key=lambda item: item[0], reverse=True)
    best = accepted[0]
    high_values = {
        item[1]
        for item in accepted
        if item[0] >= best[0] - 0.05 and item[0] >= 0.75
    }
    if len(high_values) > 1:
        for _, value, _, candidate in accepted:
            if value in high_values:
                candidate.excluded_reason = "conflicting-invoice-date"
        return FieldValue("", 0.3, "Mehrere gleich plausible Rechnungsdaten"), candidates
    return FieldValue(best[1].isoformat(), best[0], best[2]), candidates


def _amount_field(lines: list[str], anchors: Iterable[str]) -> FieldValue:
    candidates: list[tuple[float, int, str, str]] = []
    for index, line in enumerate(lines):
        folded = line.casefold()
        anchor_hit = any(marker in folded for marker in anchors)
        if not anchor_hit:
            continue
        search_lines = [(line, 0)]
        if index + 1 < len(lines):
            search_lines.append((lines[index + 1], 1))
        for candidate_line, distance in search_lines:
            for match in _AMOUNT_TOKEN.finditer(candidate_line):
                cents = amount_to_cents(match.group(1))
                if cents is None or cents == 0:
                    continue
                currency = "EUR" if (match.group(2) or "").upper() in {"EUR", "€"} or "€" in candidate_line else "EUR"
                score = 0.95 if distance == 0 else 0.82
                candidates.append((score, cents, currency, line[:300]))
    if not candidates:
        return FieldValue()
    candidates.sort(key=lambda item: (item[0], abs(item[1])), reverse=True)
    best = candidates[0]
    top_values = {item[1] for item in candidates if item[0] >= best[0] - 0.03}
    if len(top_values) > 1:
        # Prefer the largest total only when all candidates occur under the same total anchor.
        largest = max(candidates, key=lambda item: abs(item[1]))
        if abs(largest[1]) >= abs(best[1]):
            best = largest
            confidence = min(best[0], 0.82)
        else:
            return FieldValue("", 0.35, "Mehrere gleich plausible Betragswerte")
    else:
        confidence = best[0]
    return FieldValue(_format_amount(best[1]), confidence, best[3])


def _normalize_invoice_number(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value or "")
    normalized = normalized.replace("–", "-").replace("—", "-").replace("−", "-")
    return re.sub(r"\s+", "", normalized).strip(" .,:;#")


def _number_value(line: str, label_end: int) -> tuple[str, str]:
    remainder = line[label_end:].lstrip(" .,:;#-")
    if not remainder:
        return "", ""
    raw = re.split(r"[|;,]", remainder, maxsplit=1)[0].strip()
    if not raw:
        return "", ""
    if all(character.isalnum() or character in " ._/-–—−" for character in raw):
        return raw[:100], _normalize_invoice_number(raw[:100])
    token = raw.split(maxsplit=1)[0]
    return token[:100], _normalize_invoice_number(token[:100])


def _number_exclusion(value: str) -> str:
    if not value:
        return "missing-value"
    if _parse_date(value):
        return "value-is-date"
    if len(value) < 3 or len(value) > 64:
        return "invalid-length"
    if not any(character.isdigit() for character in value):
        return "value-has-no-digit"
    if not all(character.isalnum() or character in "._/-" for character in value):
        return "invalid-characters"
    compact = re.sub(r"[._/-]", "", value).upper()
    if re.fullmatch(r"[A-Z]{2}\d{13,32}", compact):
        return "value-is-iban"
    if re.fullmatch(r"(?:DE)?\d{9}", compact):
        return "value-is-vat-id"
    return ""


def _has_number_label(line: str) -> bool:
    return any(pattern.search(line) for pattern in _INVOICE_NUMBER_LABELS) or any(
        pattern.search(line) for _, pattern in _NON_INVOICE_NUMBER_LABELS
    )


def _invoice_number_field(
    lines: list[str], *, source: str, document_name: str = ""
) -> tuple[FieldValue, list[FieldCandidate]]:
    candidates: list[FieldCandidate] = []
    accepted: list[FieldCandidate] = []
    for index, line in enumerate(lines):
        matched_positive = False
        for pattern in _INVOICE_NUMBER_LABELS:
            label_match = pattern.search(line)
            if not label_match:
                continue
            matched_positive = True
            raw, normalized = _number_value(line, label_match.end())
            distance = 0
            if not normalized and index + 1 < len(lines) and not _has_number_label(lines[index + 1]):
                raw, normalized = _number_value(lines[index + 1], 0)
                distance = 1
            excluded = _number_exclusion(normalized)
            candidate = FieldCandidate(
                field="invoice_number",
                role="invoice-number",
                raw_value=raw,
                normalized_value=normalized,
                source=source,
                evidence_type="labeled-same-line" if distance == 0 else "labeled-next-line",
                evidence=line[:300],
                confidence=0.94 if distance == 0 else 0.88,
                excluded_reason=excluded,
            )
            candidates.append(candidate)
            if not excluded:
                accepted.append(candidate)
            break
        if matched_positive:
            continue
        for role, pattern in _NON_INVOICE_NUMBER_LABELS:
            label_match = pattern.search(line)
            if not label_match:
                continue
            raw, normalized = _number_value(line, label_match.end())
            candidates.append(
                FieldCandidate(
                    field="invoice_number",
                    role=role,
                    raw_value=raw,
                    normalized_value=normalized,
                    source=source,
                    evidence_type="excluded-labeled-value",
                    evidence=line[:300],
                    confidence=0.0,
                    excluded_reason=f"not-invoice-number:{role}",
                )
            )
            break

    unique = {candidate.normalized_value.casefold() for candidate in accepted}
    if len(unique) > 1:
        for candidate in accepted:
            candidate.excluded_reason = "conflicting-invoice-number"
        result = FieldValue("", 0.35, "Mehrere Rechnungsnummern erkannt")
    elif len(unique) == 1:
        selected = accepted[0]
        result = FieldValue(selected.normalized_value, selected.confidence, selected.evidence)
    else:
        result = FieldValue()

    filename = Path(document_name).name[:300] if document_name else ""
    stem = Path(filename).stem if filename else ""
    filename_normalized = _normalize_invoice_number(stem)
    if filename_normalized and any(character.isdigit() for character in filename_normalized):
        supported = bool(
            result.value
            and result.value.casefold() in filename_normalized.casefold()
        )
        candidates.append(
            FieldCandidate(
                field="invoice_number",
                role="invoice-number",
                raw_value=stem[:100],
                normalized_value=result.value if supported else filename_normalized[:100],
                source="filename",
                evidence_type="supporting-filename-match" if supported else "filename-only",
                evidence=filename,
                confidence=0.97 if supported else 0.2,
                excluded_reason="" if supported else "filename-support-only",
            )
        )
        if supported:
            result.confidence = 0.97
            result.evidence = f"{result.evidence} / Dateiname stimmt ueberein"[:300]
    return result, candidates


def _supplier_field(lines: list[str], message: ParsedMessage) -> FieldValue:
    # Explicit labels must start the line.  A company name such as
    # "Muster Lieferant GmbH" is not a field label and must not consume the
    # following invoice-number line as the supplier.
    for index, line in enumerate(lines):
        match = _SUPPLIER_LABEL.match(line)
        if not match:
            continue
        value = _clean_line(match.group(1) or "")
        if len(value) >= 3 and not _VAT_ID.fullmatch(value.replace(" ", "")):
            return FieldValue(value[:200], 0.94, line[:300])
        if index + 1 < len(lines):
            candidate = _clean_line(lines[index + 1])
            if (
                3 <= len(candidate) <= 200
                and not _supplier_line_excluded(candidate)
                and not _VAT_ID.fullmatch(candidate.replace(" ", ""))
            ):
                return FieldValue(candidate[:200], 0.86, line[:300] + " / " + candidate[:200])

    # Prefer a company-like heading near the top of the document.  This is
    # safer than using an arbitrary next line and remains explainable.
    candidates: list[tuple[float, str]] = []
    sender = _clean_line(message.sender_name)
    for index, line in enumerate(lines[:12]):
        candidate = _clean_line(line)
        if not (3 <= len(candidate) <= 200):
            continue
        if _supplier_line_excluded(candidate):
            continue
        if _DATE_TOKEN.search(candidate) or _AMOUNT_TOKEN.search(candidate) or _VAT_ID.fullmatch(candidate.replace(" ", "")):
            continue
        alpha_words = re.findall(r"[A-Za-zÀ-ÖØ-öø-ÿ]{2,}", candidate)
        if len(alpha_words) < 2:
            continue
        score = 0.52 + (0.16 if index < 3 else 0.0)
        if _COMPANY_SUFFIX.search(candidate):
            score += 0.24
        if sender and (candidate.casefold() in sender.casefold() or sender.casefold() in candidate.casefold()):
            score += 0.08
        candidates.append((min(score, 0.92), candidate))
    if candidates:
        score, value = max(candidates, key=lambda item: item[0])
        if score >= 0.76:
            return FieldValue(value[:200], score, "Firmenkopf im oberen Dokumentbereich")

    if sender and "@" not in sender:
        return FieldValue(sender[:200], 0.68, "Absendername der E-Mail")
    domain = _clean_line(message.sender_domain)
    if domain:
        return FieldValue(domain[:200], 0.55, "Absenderdomain der E-Mail")
    return FieldValue()


def _category_field(text: str, supplier: str) -> FieldValue:
    haystack = f"{supplier} {text[:16000]}".casefold()
    scored: list[tuple[int, str, str]] = []
    for category, markers in _CATEGORY_RULES:
        hits = [marker for marker in markers if marker in haystack]
        if hits:
            scored.append((len(hits), category, ", ".join(hits[:5])))
    if not scored:
        return FieldValue("Ungeklärt", 0.25, "Keine belastbare Kategorienregel")
    scored.sort(reverse=True)
    best = scored[0]
    if len(scored) > 1 and scored[1][0] == best[0]:
        return FieldValue("Ungeklärt", 0.35, "Mehrere Kategorien gleich plausibel")
    confidence = min(0.92, 0.62 + 0.08 * best[0])
    return FieldValue(best[1], confidence, best[2])


def _finalize_metadata(metadata: InvoiceMetadata) -> InvoiceMetadata:
    standard_issues = {
        "Rechnungsdatum nicht eindeutig",
        "Rechnungsnummer nicht eindeutig",
        "Bruttobetrag nicht eindeutig",
        "Rechnungssteller nicht eindeutig",
    }
    metadata.issues = [issue for issue in metadata.issues if issue not in standard_issues]
    critical = (
        metadata.invoice_date.confidence,
        metadata.invoice_number.confidence,
        metadata.gross_amount.confidence,
        metadata.supplier.confidence,
    )
    metadata.confidence = round(
        0.30 * critical[0] + 0.20 * critical[1] + 0.30 * critical[2] + 0.20 * critical[3],
        4,
    )
    if not metadata.date_confirmed:
        metadata.issues.append("Rechnungsdatum nicht eindeutig")
    if not metadata.invoice_number.value:
        metadata.issues.append("Rechnungsnummer nicht eindeutig")
    if not metadata.gross_amount.value:
        metadata.issues.append("Bruttobetrag nicht eindeutig")
    if metadata.supplier.confidence < 0.55:
        metadata.issues.append("Rechnungssteller nicht eindeutig")
    metadata.status = "confirmed" if (
        metadata.date_confirmed
        and metadata.invoice_number.confidence >= 0.75
        and metadata.gross_amount.confidence >= 0.80
        and metadata.supplier.confidence >= 0.55
    ) else "review"
    return metadata


def parse_invoice_text(
    text: str,
    message: ParsedMessage,
    *,
    method: str,
    document_name: str = "",
) -> InvoiceMetadata:
    lines = _lines(text)
    received = _received_date(message)
    alpha = sum(ch.isalnum() for ch in text)
    quality = min(1.0, alpha / 500.0) if text else 0.0
    metadata = InvoiceMetadata(method=method, text_quality=quality)
    metadata.invoice_date, date_candidates = _invoice_date_field(
        lines, received=received, source=method
    )
    metadata.due_date = _date_field(lines, _DUE_ANCHORS, received=received, require_anchor=True)
    metadata.invoice_number, number_candidates = _invoice_number_field(
        lines, source=method, document_name=document_name
    )
    metadata.field_candidates.extend(date_candidates)
    metadata.field_candidates.extend(number_candidates)
    metadata.gross_amount = _amount_field(lines, _GROSS_ANCHORS)
    metadata.net_amount = _amount_field(lines, _NET_ANCHORS)
    metadata.tax_amount = _amount_field(lines, _TAX_ANCHORS)
    metadata.supplier = _supplier_field(lines, message)
    metadata.category = _category_field(text, metadata.supplier.value)
    if "usd" in text.casefold() or "$" in text:
        metadata.currency = FieldValue("USD", 0.75, "Waehrungssignal im Dokument")
    elif "gbp" in text.casefold() or "£" in text:
        metadata.currency = FieldValue("GBP", 0.75, "Waehrungssignal im Dokument")
    elif "eur" in text.casefold() or "€" in text:
        metadata.currency = FieldValue("EUR", 0.95, "Waehrungssignal im Dokument")

    return _finalize_metadata(metadata)


def _run(command: list[str], *, timeout: int) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, check=False, capture_output=True, text=True, timeout=timeout)


def extract_native_text(pdf_path: Path, *, timeout: int) -> tuple[str, str]:
    binary = shutil.which("pdftotext")
    if not binary:
        return "", "pdftotext ist nicht installiert"
    try:
        result = _run([binary, "-layout", "-enc", "UTF-8", str(pdf_path), "-"], timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return "", f"pdftotext fehlgeschlagen: {exc}"
    if result.returncode != 0:
        return "", (result.stderr or "pdftotext fehlgeschlagen")[-1000:]
    return result.stdout or "", ""


def extract_ocr_text(pdf_path: Path, *, config: InvoiceConfig) -> tuple[str, str]:
    pdftoppm = shutil.which("pdftoppm")
    tesseract = shutil.which("tesseract")
    if not pdftoppm or not tesseract:
        missing = [name for name, value in (("pdftoppm", pdftoppm), ("tesseract", tesseract)) if not value]
        return "", "OCR-Werkzeug fehlt: " + ", ".join(missing)
    with tempfile.TemporaryDirectory(prefix="invoice-ocr-") as temp:
        prefix = Path(temp) / "page"
        try:
            render = _run([
                pdftoppm, "-f", "1", "-l", str(max(1, config.ocr_max_pages)),
                "-r", str(max(150, config.ocr_dpi)), "-png", str(pdf_path), str(prefix),
            ], timeout=config.ocr_timeout_seconds)
        except (OSError, subprocess.TimeoutExpired) as exc:
            return "", f"PDF-Rendering fuer OCR fehlgeschlagen: {exc}"
        if render.returncode != 0:
            return "", (render.stderr or "pdftoppm fehlgeschlagen")[-1000:]
        pages = sorted(Path(temp).glob("page-*.png"))
        if not pages:
            return "", "OCR konnte keine PDF-Seiten rendern"
        texts: list[str] = []
        for page in pages:
            try:
                result = _run([
                    tesseract, str(page), "stdout", "-l", config.ocr_languages,
                    "--psm", str(config.ocr_page_segmentation),
                ], timeout=config.ocr_timeout_seconds)
            except (OSError, subprocess.TimeoutExpired) as exc:
                return "", f"Tesseract fehlgeschlagen: {exc}"
            if result.returncode != 0:
                return "", (result.stderr or "Tesseract fehlgeschlagen")[-1000:]
            texts.append(result.stdout or "")
        return "\n\n".join(texts), ""


def _choose(native: InvoiceMetadata, ocr: InvoiceMetadata | None) -> InvoiceMetadata:
    if ocr is None:
        return native
    critical_names = ("invoice_date", "invoice_number", "gross_amount")
    conflicts: list[str] = []
    retained_native_conflicts: list[str] = []
    for name in critical_names:
        left: FieldValue = getattr(native, name)
        right: FieldValue = getattr(ocr, name)
        if left.value and right.value and left.value != right.value and min(left.confidence, right.confidence) >= 0.75:
            if left.confidence >= 0.90 and left.confidence > right.confidence:
                retained_native_conflicts.append(name)
            else:
                conflicts.append(name)
    # Native PDF text remains authoritative whenever it contains a usable
    # value. OCR is a fallback that fills missing/weak fields instead of
    # replacing an otherwise readable text layer wholesale.
    chosen = copy.deepcopy(native)
    for name in (
        "invoice_date",
        "invoice_number",
        "supplier",
        "category",
        "gross_amount",
        "net_amount",
        "tax_amount",
        "currency",
        "due_date",
    ):
        chosen_value: FieldValue = getattr(chosen, name)
        ocr_value: FieldValue = getattr(ocr, name)
        if (
            (not chosen_value.value or chosen_value.confidence < 0.55)
            and ocr_value.value
            and ocr_value.confidence > chosen_value.confidence
        ):
            setattr(chosen, name, copy.deepcopy(ocr_value))
    chosen.method = "text+ocr-fallback"
    chosen.text_quality = max(native.text_quality, ocr.text_quality)
    known_candidates = {
        (
            item.field,
            item.role,
            item.normalized_value,
            item.source,
            item.evidence_type,
            item.excluded_reason,
        )
        for item in chosen.field_candidates
    }
    for candidate in ocr.field_candidates:
        identity = (
            candidate.field,
            candidate.role,
            candidate.normalized_value,
            candidate.source,
            candidate.evidence_type,
            candidate.excluded_reason,
        )
        if identity not in known_candidates:
            chosen.field_candidates.append(copy.deepcopy(candidate))
            known_candidates.add(identity)
    chosen.issues.extend(issue for issue in ocr.issues if issue not in chosen.issues)
    labels = {
        "invoice_date": "Rechnungsdatum",
        "invoice_number": "Rechnungsnummer",
        "gross_amount": "Bruttobetrag",
    }
    if retained_native_conflicts:
        chosen.issues.append(
            "Abweichende OCR-Werte zugunsten der hochkonfidenten nativen Textschicht ignoriert bei: "
            + ", ".join(labels[v] for v in retained_native_conflicts)
        )
    if conflicts:
        for name in conflicts:
            setattr(chosen, name, FieldValue("", 0.0, "Textschicht/OCR-Konflikt"))
        chosen.issues.append("Textschicht und OCR widersprechen sich bei: " + ", ".join(labels[v] for v in conflicts))
    return _finalize_metadata(chosen)


class InvoiceExtractor:
    def __init__(self, config: InvoiceConfig) -> None:
        self.config = config

    def doctor(self) -> dict[str, object]:
        pdftotext = shutil.which("pdftotext") or ""
        pdftoppm = shutil.which("pdftoppm") or ""
        tesseract = shutil.which("tesseract") or ""
        installed_languages: list[str] = []
        language_error = ""
        if tesseract:
            roots: list[Path] = []
            configured = __import__("os").environ.get("TESSDATA_PREFIX", "").strip()
            if configured:
                roots.extend((Path(configured), Path(configured) / "tessdata"))
            roots.extend((
                Path("/usr/share/tesseract-ocr/5/tessdata"),
                Path("/usr/share/tesseract-ocr/4.00/tessdata"),
                Path("/usr/share/tessdata"),
                Path("/usr/local/share/tessdata"),
            ))
            seen: set[str] = set()
            for root in roots:
                try:
                    for source in root.glob("*.traineddata"):
                        seen.add(source.stem)
                except OSError as exc:
                    language_error = str(exc)
            installed_languages = sorted(seen)
        requested = [value.strip() for value in self.config.ocr_languages.split("+") if value.strip()]
        missing_languages = [value for value in requested if value not in installed_languages]
        ocr_available = bool(pdftoppm and tesseract and not missing_languages)
        native_available = bool(pdftotext)
        return {
            "ok": native_available or (self.config.ocr_enabled and ocr_available),
            "native_text": {"binary": pdftotext, "available": native_available},
            "ocr": {
                "enabled": self.config.ocr_enabled,
                "pdftoppm": pdftoppm,
                "tesseract": tesseract,
                "available": ocr_available,
                "requested_languages": requested,
                "available_requested_languages": [value for value in requested if value in installed_languages],
                "installed_language_count": len(installed_languages),
                "missing_languages": missing_languages,
                "language_error": language_error,
                "max_pages": self.config.ocr_max_pages,
                "dpi": self.config.ocr_dpi,
            },
        }

    def extract(
        self, data: bytes, message: ParsedMessage, *, filename: str = ""
    ) -> InvoiceMetadata:
        if not self.config.metadata_enabled:
            return InvoiceMetadata(status="review", method="disabled", issues=["Metadatenextraktion ist deaktiviert"])
        with tempfile.TemporaryDirectory(prefix="invoice-extract-") as temp:
            pdf_path = Path(temp) / "invoice.pdf"
            pdf_path.write_bytes(data)
            native_text, native_error = extract_native_text(pdf_path, timeout=self.config.text_timeout_seconds)
            native = parse_invoice_text(
                native_text, message, method="text", document_name=filename
            )
            if native_error:
                native.issues.append(native_error)
            # OCR is intentionally only a fallback. Missing optional accounting
            # fields must not cause a clean native text layer to be OCR'd again.
            need_ocr = self.config.ocr_enabled and (
                native.text_quality < self.config.min_text_quality
                or not native.date_confirmed
            )
            ocr: InvoiceMetadata | None = None
            if need_ocr:
                ocr_text, ocr_error = extract_ocr_text(pdf_path, config=self.config)
                if ocr_text:
                    ocr = parse_invoice_text(
                        ocr_text, message, method="ocr", document_name=filename
                    )
                elif ocr_error:
                    native.issues.append(ocr_error)
            selected = _choose(native, ocr)
            if not selected.date_confirmed or selected.confidence < self.config.metadata_min_confidence:
                selected.status = "review"
            return selected
