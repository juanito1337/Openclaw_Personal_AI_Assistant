from __future__ import annotations

import copy
import json
import re
import shutil
import subprocess
import tempfile
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
_INVOICE_NUMBER = re.compile(
    r"(?:rechnungs(?:nummer|nr\.?|\s*nr\.?)|invoice\s*(?:number|no\.?|#)|belegnummer|faktura(?:nummer|nr\.?)?)"
    r"\s*[:#]?\s*([A-Z0-9][A-Z0-9._/\-]{2,50})",
    re.IGNORECASE,
)
_VAT_ID = re.compile(r"\b(?:DE\s*)?\d{9}\b", re.IGNORECASE)

_DATE_ANCHORS = (
    "rechnungsdatum", "datum der rechnung", "rechnung vom", "rechnung erstellt am",
    "invoice date", "invoice issued", "document date", "belegdatum", "ausstellungsdatum", "datum",
)
_DATE_NEGATIVE = (
    "leistungsdatum", "lieferdatum", "faellig", "fällig", "zahlungsziel", "bestelldatum",
    "lieferzeitraum", "datum der leistung", "datum der lieferung", "datum der bestellung",
    "due date", "service date", "delivery date", "order date",
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
    return [_clean_line(line) for line in (text or "").replace("\x00", " ").splitlines() if _clean_line(line)]


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


def _invoice_number_field(lines: list[str]) -> FieldValue:
    found: list[tuple[str, str]] = []
    for line in lines:
        match = _INVOICE_NUMBER.search(line)
        if match:
            value = match.group(1).strip(" .,:;#")
            if len(value) >= 3:
                found.append((value, line[:300]))
    unique = {value.casefold(): (value, evidence) for value, evidence in found}
    if len(unique) == 1:
        value, evidence = next(iter(unique.values()))
        return FieldValue(value, 0.94, evidence)
    if len(unique) > 1:
        return FieldValue("", 0.35, "Mehrere Rechnungsnummern erkannt")
    return FieldValue()


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


def parse_invoice_text(text: str, message: ParsedMessage, *, method: str) -> InvoiceMetadata:
    lines = _lines(text)
    received = _received_date(message)
    alpha = sum(ch.isalnum() for ch in text)
    quality = min(1.0, alpha / 500.0) if text else 0.0
    metadata = InvoiceMetadata(method=method, text_quality=quality)
    metadata.invoice_date = _date_field(lines, _DATE_ANCHORS, received=received, negative=_DATE_NEGATIVE)
    metadata.due_date = _date_field(lines, _DUE_ANCHORS, received=received, require_anchor=True)
    metadata.invoice_number = _invoice_number_field(lines)
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
        left: FieldValue = getattr(chosen, name)
        right: FieldValue = getattr(ocr, name)
        if (not left.value or left.confidence < 0.55) and right.value and right.confidence > left.confidence:
            setattr(chosen, name, copy.deepcopy(right))
    chosen.method = "text+ocr-fallback"
    chosen.text_quality = max(native.text_quality, ocr.text_quality)
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

    def extract(self, data: bytes, message: ParsedMessage) -> InvoiceMetadata:
        if not self.config.metadata_enabled:
            return InvoiceMetadata(status="review", method="disabled", issues=["Metadatenextraktion ist deaktiviert"])
        with tempfile.TemporaryDirectory(prefix="invoice-extract-") as temp:
            pdf_path = Path(temp) / "invoice.pdf"
            pdf_path.write_bytes(data)
            native_text, native_error = extract_native_text(pdf_path, timeout=self.config.text_timeout_seconds)
            native = parse_invoice_text(native_text, message, method="text")
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
                    ocr = parse_invoice_text(ocr_text, message, method="ocr")
                elif ocr_error:
                    native.issues.append(ocr_error)
            selected = _choose(native, ocr)
            if not selected.date_confirmed or selected.confidence < self.config.metadata_min_confidence:
                selected.status = "review"
            return selected
