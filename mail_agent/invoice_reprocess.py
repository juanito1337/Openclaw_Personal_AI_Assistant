from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from pathlib import Path

from personal_assistant.config import load_config as load_assistant_config
from personal_assistant.connectors.nextcloud.client import NextcloudClient
from personal_assistant.connectors.nextcloud.files import NextcloudFiles
from personal_assistant.registry import ResourceRegistry

from .invoice_extract import InvoiceExtractor, InvoiceMetadata
from .models import ParsedMessage

REPROCESS_STATUSES = frozenset({"review", "unclassified"})
PREVIEW_SCHEMA_VERSION = 1
_FIELD_NAMES = (
    "invoice_date",
    "invoice_number",
    "supplier",
    "category",
    "gross_amount",
    "net_amount",
    "tax_amount",
    "currency",
    "due_date",
)
_REQUIRED_FIELDS = ("invoice_date", "invoice_number", "supplier", "gross_amount")
_FIELD_LIMITS = {
    "invoice_date": 10,
    "invoice_number": 160,
    "supplier": 200,
    "category": 120,
    "gross_amount": 64,
    "net_amount": 64,
    "tax_amount": 64,
    "currency": 8,
    "due_date": 10,
}
_TYPED_REASON = re.compile(r"^[a-z][a-z0-9_.-]*(?::[a-z0-9_.-]+){1,3}$")
_PATH_YEAR = re.compile(r"(?:^|/)(20\d{2}|21\d{2})(?:/|$)")


@dataclass(slots=True, frozen=True)
class SourceYears:
    source_year: int
    source_basis: str
    register_year: int | None
    path_year: int | None
    received_year: int | None


class ReadOnlyInvoicePdfReader:
    """Read one allowed Nextcloud PDF without opening assistant state or audit stores."""

    def __init__(self) -> None:
        config = load_assistant_config()
        self._registry = ResourceRegistry(config.runtime.resources_file)
        self._files = NextcloudFiles(config, NextcloudClient(config))

    def read(self, remote_path: str, *, allowed_folder: str, resource_id: str) -> bytes:
        resource = self._registry.get(resource_id)
        if not resource.enabled or "read" not in resource.permissions:
            raise PermissionError(f"Ressource {resource_id} besitzt kein aktives read-Recht")
        if resource.connector != "nextcloud":
            raise PermissionError(f"Ressource {resource_id} ist keine Nextcloud-Dateiressource")
        clean_path = self._files.clean_path(remote_path)
        clean_folder = self._files.clean_path(allowed_folder)
        if clean_path != clean_folder and not clean_path.startswith(clean_folder.rstrip("/") + "/"):
            raise PermissionError("Reprocessing darf nur im konfigurierten Rechnungsordner lesen")
        configured_roots = resource.metadata.get("allowed_roots", [])
        if isinstance(configured_roots, list) and configured_roots:
            roots = [self._files.clean_path(str(root)) for root in configured_roots]
            if not any(
                clean_folder == root or clean_folder.startswith(root.rstrip("/") + "/")
                for root in roots
            ):
                raise PermissionError("Rechnungsordner liegt ausserhalb der erlaubten Ressourcenwurzel")
        data = self._files.download(clean_path)
        if not data.startswith(b"%PDF-"):
            raise ValueError("Archivdatei ist kein gueltiges PDF")
        return data


def _year_prefix(value: object) -> int | None:
    raw = str(value or "").strip()
    if len(raw) >= 4 and raw[:4].isdigit():
        year = int(raw[:4])
        if 2000 <= year <= 2100:
            return year
    return None


def _received_year(item: Mapping[str, object]) -> int | None:
    direct = _year_prefix(item.get("received_date"))
    if direct is not None:
        return direct
    raw = str(item.get("message_received_at") or "").strip()
    if not raw:
        return None
    direct = _year_prefix(raw)
    if direct is not None:
        return direct
    try:
        parsed = parsedate_to_datetime(raw)
    except (TypeError, ValueError, OverflowError):
        return None
    return parsed.year if parsed and 2000 <= parsed.year <= 2100 else None


def source_years(item: Mapping[str, object]) -> SourceYears | None:
    register_year = _year_prefix(item.get("register_year"))
    match = _PATH_YEAR.search(str(item.get("nextcloud_path") or ""))
    path_year = int(match.group(1)) if match else None
    received_year = _received_year(item)
    if register_year is not None:
        return SourceYears(register_year, "register-year", register_year, path_year, received_year)
    if path_year is not None:
        return SourceYears(path_year, "path-year", None, path_year, received_year)
    return None


def _validate_selector(status: str, source_year: int, limit: int) -> tuple[str, int, int]:
    normalized_status = str(status or "").strip().casefold()
    if normalized_status not in REPROCESS_STATUSES:
        raise ValueError("Reprocessing-Status muss exakt review oder unclassified sein")
    normalized_year = int(source_year)
    if not 2000 <= normalized_year <= 2100:
        raise ValueError("Quelljahr muss zwischen 2000 und 2100 liegen")
    normalized_limit = int(limit)
    if not 1 <= normalized_limit <= 100:
        raise ValueError("Reprocessing-Limit muss zwischen 1 und 100 liegen")
    return normalized_status, normalized_year, normalized_limit


def read_reprocess_candidates(
    database: Path,
    *,
    status: str,
    source_year: int,
    limit: int,
) -> list[dict[str, object]]:
    normalized_status, normalized_year, normalized_limit = _validate_selector(
        status, source_year, limit
    )
    path = Path(database).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f"Rechnungsdatenbank fehlt: {path}")
    condition = (
        "i.extraction_status = 'review'"
        if normalized_status == "review"
        else "COALESCE(TRIM(i.extraction_status), '') = ''"
    )
    uri = path.as_uri() + "?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA query_only=ON")
        cursor = connection.execute(
            f"""
            SELECT i.*, m.message_id AS message_id, m.mailbox_id AS mailbox_id,
                   m.last_folder AS last_folder, m.sender_addr AS sender_addr,
                   m.sender_name AS sender_name, m.subject AS subject,
                   m.received_at AS message_received_at
            FROM invoices i
            LEFT JOIN messages m ON m.stable_key = i.stable_key
            WHERE i.status IN ('uploaded', 'duplicate')
              AND COALESCE(i.nextcloud_path, '') != ''
              AND {condition}
              AND COALESCE(i.extraction_status, '') NOT IN ('confirmed', 'confirmed-manual')
            ORDER BY i.created_at, i.id
            """
        )
        selected: list[dict[str, object]] = []
        for row in cursor:
            item = dict(row)
            years = source_years(item)
            if years is None or years.source_year != normalized_year:
                continue
            selected.append(item)
            if len(selected) >= normalized_limit:
                break
        return selected
    finally:
        connection.close()


def _message(item: Mapping[str, object]) -> ParsedMessage:
    received = str(
        item.get("message_received_at")
        or item.get("received_date")
        or item.get("created_at")
        or ""
    )
    return ParsedMessage(
        stable_key=str(item.get("stable_key") or ""),
        mailbox_id=str(item.get("mailbox_id") or ""),
        source_folder=str(item.get("last_folder") or ""),
        raw=b"",
        message_id=str(item.get("message_id") or ""),
        subject=str(item.get("subject") or ""),
        sender_name=str(item.get("sender_name") or ""),
        sender_addr=str(item.get("sender_addr") or ""),
        date=received,
        received_at=received,
    )


def _amount_from_cents(value: object) -> str:
    if value is None or value == "":
        return ""
    try:
        cents = int(str(value))
    except (TypeError, ValueError):
        return ""
    sign = "-" if cents < 0 else ""
    absolute = abs(cents)
    return f"{sign}{absolute // 100}.{absolute % 100:02d}"


def _old_values(item: Mapping[str, object]) -> dict[str, str]:
    return {
        "invoice_date": str(item.get("invoice_date") or ""),
        "invoice_number": str(item.get("invoice_number") or ""),
        "supplier": str(item.get("supplier") or ""),
        "category": str(item.get("category") or ""),
        "gross_amount": _amount_from_cents(item.get("gross_amount_cents")),
        "net_amount": _amount_from_cents(item.get("net_amount_cents")),
        "tax_amount": _amount_from_cents(item.get("tax_amount_cents")),
        "currency": str(item.get("currency") or ""),
        "due_date": str(item.get("due_date") or ""),
    }


def _new_values(metadata: InvoiceMetadata) -> dict[str, str]:
    return {name: str(getattr(metadata, name).value or "") for name in _FIELD_NAMES}


def _stored_metadata(item: Mapping[str, object]) -> dict[str, object]:
    try:
        payload = json.loads(str(item.get("extraction_json") or ""))
    except (json.JSONDecodeError, TypeError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _evidence_type(value: object, *, default: str) -> str:
    raw = str(value or "").casefold()
    if not raw:
        return default
    if "absender" in raw or "sender" in raw:
        return "mail-sender-fallback"
    if "dateiname" in raw or "filename" in raw:
        return "filename-support"
    if "ocr" in raw:
        return "ocr-document"
    return "document-rule"


def _old_evidence(
    field_name: str,
    item: Mapping[str, object],
    stored: Mapping[str, object],
) -> tuple[float, str, str]:
    raw_field = stored.get(field_name)
    field = raw_field if isinstance(raw_field, dict) else {}
    try:
        confidence = float(field.get("confidence", item.get("extraction_confidence") or 0.0))
    except (TypeError, ValueError):
        confidence = 0.0
    return (
        max(0.0, min(confidence, 1.0)),
        _evidence_type(field.get("evidence"), default="stored-record"),
        str(item.get("extraction_method") or "stored-record")[:80],
    )


def _new_evidence(field_name: str, metadata: InvoiceMetadata) -> tuple[float, str, str]:
    value = getattr(metadata, field_name)
    matching = [
        candidate
        for candidate in metadata.field_candidates
        if candidate.field == field_name
        and candidate.normalized_value == value.value
        and not candidate.excluded_reason
    ]
    if matching:
        selected = max(matching, key=lambda candidate: candidate.confidence)
        return (
            max(0.0, min(float(selected.confidence), 1.0)),
            str(selected.evidence_type or "document-candidate")[:80],
            str(selected.source or metadata.method or "extractor")[:80],
        )
    return (
        max(0.0, min(float(value.confidence), 1.0)),
        _evidence_type(value.evidence, default="none"),
        str(metadata.method or "extractor")[:80],
    )


def _bounded_value(field_name: str, value: str) -> tuple[str, bool]:
    limit = _FIELD_LIMITS[field_name]
    normalized = str(value or "").replace("\r", " ").replace("\n", " ").strip()
    return normalized[:limit], len(normalized) > limit


def _field_projection(
    item: Mapping[str, object], metadata: InvoiceMetadata
) -> tuple[dict[str, dict[str, object]], dict[str, str], dict[str, str]]:
    stored = _stored_metadata(item)
    old_values = _old_values(item)
    new_values = _new_values(metadata)
    fields: dict[str, dict[str, object]] = {}
    for name in _FIELD_NAMES:
        old_confidence, old_type, old_source = _old_evidence(name, item, stored)
        new_confidence, new_type, new_source = _new_evidence(name, metadata)
        old_value, old_truncated = _bounded_value(name, old_values[name])
        new_value, new_truncated = _bounded_value(name, new_values[name])
        fields[name] = {
            "old": {
                "value": old_value,
                "value_truncated": old_truncated,
                "confidence": round(old_confidence, 4),
                "evidence_type": old_type,
                "source": old_source,
            },
            "new": {
                "value": new_value,
                "value_truncated": new_truncated,
                "confidence": round(new_confidence, 4),
                "evidence_type": new_type,
                "source": new_source,
            },
            "changed": old_values[name] != new_values[name],
        }
    return fields, old_values, new_values


def _typed_conflicts(values: object) -> list[str]:
    if not isinstance(values, list):
        return []
    result: list[str] = []
    for value in values:
        normalized = str(value or "").strip().casefold()[:120]
        if _TYPED_REASON.fullmatch(normalized) and normalized not in result:
            result.append(normalized)
        if len(result) >= 32:
            break
    return sorted(result)


def _quality(
    values: Mapping[str, str],
    status: str,
    confidences: Mapping[str, float],
) -> tuple[int, int, float]:
    present = sum(bool(values[name]) for name in _REQUIRED_FIELDS)
    values_with_confidence = [confidences[name] for name in _REQUIRED_FIELDS if values[name]]
    average = sum(values_with_confidence) / len(values_with_confidence) if values_with_confidence else 0.0
    return (1 if status in {"confirmed", "confirmed-manual"} else 0, present, round(average, 6))


def classify_proposal(
    *,
    old_values: Mapping[str, str],
    new_values: Mapping[str, str],
    old_status: str,
    new_status: str,
    old_confidences: Mapping[str, float],
    new_confidences: Mapping[str, float],
    conflicts: list[str],
) -> str:
    old_projection = (dict(old_values), old_status)
    new_projection = (dict(new_values), new_status)
    if old_projection == new_projection:
        return "unchanged"
    old_quality = _quality(old_values, old_status, old_confidences)
    new_quality = _quality(new_values, new_status, new_confidences)
    lost_required = any(old_values[name] and not new_values[name] for name in _REQUIRED_FIELDS)
    if conflicts or lost_required or new_quality < old_quality:
        return "regressed"
    if new_quality > old_quality:
        return "improved"
    return "still-review"


def _canonical(value: object) -> object:
    if isinstance(value, Mapping):
        return {
            str(key): _canonical(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, (list, tuple)):
        return [_canonical(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def compute_preview_digest(
    *,
    pdf_sha256: str,
    current_record: Mapping[str, object],
    extractor_version: str,
    proposal: Mapping[str, object],
) -> str:
    payload = {
        "schema_version": PREVIEW_SCHEMA_VERSION,
        "pdf_sha256": str(pdf_sha256).casefold(),
        "current_record": _canonical(current_record),
        "extractor_version": str(extractor_version),
        "proposal": _canonical(proposal),
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _digest_proposal(
    *,
    metadata: InvoiceMetadata,
    values: Mapping[str, str],
    fields: Mapping[str, Mapping[str, object]],
    conflicts: list[str],
    recognized_year: int | None,
) -> dict[str, object]:
    return {
        "status": metadata.status,
        "confidence": round(float(metadata.confidence), 6),
        "method": metadata.method,
        "recognized_invoice_year": recognized_year,
        "ruleset_version": metadata.technical.ruleset_version,
        "values": dict(values),
        "field_evidence": {
            name: {
                "confidence": fields[name]["new"]["confidence"],  # type: ignore[index]
                "evidence_type": fields[name]["new"]["evidence_type"],  # type: ignore[index]
                "source": fields[name]["new"]["source"],  # type: ignore[index]
            }
            for name in _FIELD_NAMES
        },
        "conflicts": conflicts,
    }


def build_preview_record(
    item: Mapping[str, object],
    *,
    pdf_sha256: str,
    metadata: InvoiceMetadata,
) -> dict[str, object]:
    years = source_years(item)
    if years is None:
        raise ValueError("Reprocessing-Kandidat besitzt kein eindeutiges Quelljahr")
    status = str(item.get("extraction_status") or "").strip()
    if status in {"confirmed", "confirmed-manual"}:
        raise PermissionError("Bestaetigte Rechnungsmetadaten sind vom Reprocessing ausgeschlossen")
    old_status = status or "unclassified"
    fields, old_values, new_values = _field_projection(item, metadata)
    old_stored = _stored_metadata(item)
    old_conflicts = _typed_conflicts(old_stored.get("review_reasons"))
    new_conflicts = _typed_conflicts(metadata.review_reasons)
    old_confidences = {
        name: float(fields[name]["old"]["confidence"])  # type: ignore[index]
        for name in _FIELD_NAMES
    }
    new_confidences = {
        name: float(fields[name]["new"]["confidence"])  # type: ignore[index]
        for name in _FIELD_NAMES
    }
    classification = classify_proposal(
        old_values=old_values,
        new_values=new_values,
        old_status=old_status,
        new_status=metadata.status,
        old_confidences=old_confidences,
        new_confidences=new_confidences,
        conflicts=new_conflicts,
    )
    recognized_year = _year_prefix(metadata.invoice_date.value)
    proposal = _digest_proposal(
        metadata=metadata,
        values=new_values,
        fields=fields,
        conflicts=new_conflicts,
        recognized_year=recognized_year,
    )
    digest = compute_preview_digest(
        pdf_sha256=pdf_sha256,
        current_record=item,
        extractor_version=metadata.technical.extractor_version,
        proposal=proposal,
    )
    return {
        "record_id": int(str(item.get("id") or 0)),
        "attachment_hash": str(item.get("attachment_hash") or ""),
        "preview_sha256": digest,
        "source_status": old_status,
        "proposed_status": metadata.status,
        "classification": classification,
        "years": {
            "source_year": years.source_year,
            "source_basis": years.source_basis,
            "register_year": years.register_year,
            "path_year": years.path_year,
            "received_year": years.received_year,
            "recognized_invoice_year": recognized_year,
        },
        "extractor": {
            "extractor_version": metadata.technical.extractor_version,
            "ruleset_version": metadata.technical.ruleset_version,
            "method": metadata.method,
        },
        "conflicts": {"old": old_conflicts, "new": new_conflicts},
        "fields": fields,
    }


def run_reprocess_preview(
    database: Path,
    *,
    status: str,
    source_year: int,
    limit: int,
    extractor: InvoiceExtractor,
    read_pdf: Callable[[str], bytes],
    scan_pdf: Callable[[bytes, str], str],
) -> dict[str, object]:
    normalized_status, normalized_year, normalized_limit = _validate_selector(
        status, source_year, limit
    )
    rows = read_reprocess_candidates(
        database,
        status=normalized_status,
        source_year=normalized_year,
        limit=normalized_limit,
    )
    records: list[dict[str, object]] = []
    errors: list[dict[str, object]] = []
    for item in rows:
        attachment_hash = str(item.get("attachment_hash") or "").casefold()
        remote_path = str(item.get("nextcloud_path") or "")
        try:
            data = read_pdf(remote_path)
            actual_hash = hashlib.sha256(data).hexdigest()
            if actual_hash != attachment_hash:
                raise RuntimeError("pdf-hash-mismatch")
            scanner_identity = scan_pdf(data, str(item.get("original_filename") or "invoice.pdf"))
            metadata = extractor.extract(
                data,
                _message(item),
                filename=str(item.get("original_filename") or Path(remote_path).name or "invoice.pdf"),
                scanner_identity=scanner_identity,
            )
            records.append(build_preview_record(item, pdf_sha256=actual_hash, metadata=metadata))
        except Exception as exc:
            if isinstance(exc, RuntimeError) and str(exc) == "pdf-hash-mismatch":
                code = "pdf-hash-mismatch"
            elif isinstance(exc, RuntimeError) and str(exc) == "antivirus-gate-blocked":
                code = "antivirus-gate-blocked"
            elif isinstance(exc, PermissionError):
                code = "permission-denied"
            elif isinstance(exc, ValueError):
                code = "invalid-pdf-or-budget"
            else:
                code = "preview-failed"
            errors.append(
                {
                    "record_id": int(str(item.get("id") or 0)),
                    "attachment_hash": attachment_hash,
                    "error": code,
                    "error_type": type(exc).__name__,
                }
            )
    classifications = {name: 0 for name in ("improved", "unchanged", "regressed", "still-review")}
    for record in records:
        classifications[str(record["classification"])] += 1
    return {
        "ok": not errors,
        "dry_run": True,
        "read_only": True,
        "schema_version": PREVIEW_SCHEMA_VERSION,
        "selector": {
            "status": normalized_status,
            "source_year": normalized_year,
            "limit": normalized_limit,
        },
        "candidate_count": len(rows),
        "processed_count": len(records),
        "error_count": len(errors),
        "classifications": classifications,
        "records": records,
        "errors": errors,
        "effects": {
            "sqlite": "unchanged-read-only",
            "nextcloud_register": "not-accessed",
            "pdf": "read-only",
            "audit": "not-accessed",
        },
    }
