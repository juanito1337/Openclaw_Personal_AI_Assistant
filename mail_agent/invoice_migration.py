from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from datetime import date
from pathlib import PurePosixPath

from .invoice_extract import InvoiceMetadata

_SHA256 = re.compile(r"^[a-f0-9]{64}$")
_YEAR = re.compile(r"(?:^|/)(20\d{2}|21\d{2})(?:/|$)")


def _clean_path(value: object) -> str:
    raw = str(value or "").strip().replace("\\", "/")
    return str(PurePosixPath("/" + raw.lstrip("/"))).lstrip("/") if raw else ""


def _path_year(value: object) -> int | None:
    match = _YEAR.search(_clean_path(value))
    return int(match.group(1)) if match else None


def _invoice_date(value: object) -> date | None:
    try:
        parsed = date.fromisoformat(str(value or ""))
    except ValueError:
        return None
    return parsed if 2000 <= parsed.year <= 2100 else None


def _date_roles(metadata: InvoiceMetadata) -> list[dict[str, object]]:
    roles: dict[tuple[str, str, str], dict[str, object]] = {}
    for candidate in metadata.field_candidates:
        if candidate.field != "invoice_date":
            continue
        key = (candidate.role, candidate.source, candidate.evidence_type)
        roles[key] = {
            "role": candidate.role,
            "source": candidate.source,
            "evidence_type": candidate.evidence_type,
            "selected": bool(
                not candidate.excluded_reason
                and candidate.normalized_value == metadata.invoice_date.value
            ),
            "excluded_reason": candidate.excluded_reason,
        }
    return [roles[key] for key in sorted(roles)]


def _field_provenance(metadata: InvoiceMetadata) -> dict[str, dict[str, object]]:
    result: dict[str, dict[str, object]] = {}
    for name in (
        "invoice_date",
        "invoice_number",
        "supplier",
        "gross_amount",
        "net_amount",
        "tax_amount",
        "currency",
        "due_date",
    ):
        field = getattr(metadata, name)
        selected = next(
            (
                candidate
                for candidate in metadata.field_candidates
                if candidate.field == name
                and not candidate.excluded_reason
                and candidate.normalized_value == field.value
            ),
            None,
        )
        result[name] = {
            "present": bool(field.value),
            "confidence": round(float(field.confidence), 6),
            "source": selected.source if selected is not None else metadata.method,
            "evidence_type": selected.evidence_type if selected is not None else "",
        }
    return result


def build_path_migration_plan(
    item: Mapping[str, object],
    metadata: InvoiceMetadata,
    *,
    invoice_root: str,
    pdf_sha256: str,
) -> dict[str, object]:
    """Describe one content-bound path correction without authorizing a write."""
    digest = str(pdf_sha256 or "").strip().casefold()
    stored_digest = str(item.get("attachment_hash") or "").strip().casefold()
    source_path = _clean_path(item.get("nextcloud_path"))
    root = _clean_path(invoice_root)
    filename = PurePosixPath(source_path).name if source_path else ""
    recognized = _invoice_date(metadata.invoice_date.value)
    blockers: list[str] = []
    if not _SHA256.fullmatch(digest) or digest != stored_digest:
        blockers.append("original-hash-mismatch")
    if recognized is None:
        blockers.append("recognized-invoice-date-missing")
    if not source_path or not filename.casefold().endswith(".pdf"):
        blockers.append("source-path-invalid")
    if not root:
        blockers.append("invoice-root-invalid")

    target_path = ""
    if recognized is not None and root and filename:
        target_path = _clean_path(
            f"{root}/{recognized.year:04d}/{recognized.month:02d}/{filename}"
        )
    source_year = _path_year(source_path)
    target_year = recognized.year if recognized is not None else None
    migration_required = bool(target_path and target_path != source_path)
    if target_path == source_path and source_path:
        blockers.append("path-already-canonical")

    payload = {
        "schema_version": 1,
        "attachment_hash": digest,
        "source_path": source_path,
        "target_path": target_path,
        "source_year": source_year,
        "target_year": target_year,
        "migration_required": migration_required,
        "preconditions": [
            "verified-local-backup",
            "externally-restorable-nextcloud-snapshot",
            "source-sha256-match",
            "target-absent",
            "single-target",
            "source-etag-unchanged",
        ],
        "no_overwrite": True,
        "bulk_apply_available": False,
        "apply_available": False,
        "approval": "separate-explicit-single-invoice-path-migration",
        "blockers": sorted(set(blockers)),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return {**payload, "plan_sha256": hashlib.sha256(encoded).hexdigest()}


def validate_path_migration_observation(
    plan: Mapping[str, object],
    *,
    observed_source_sha256: str,
    source_etag_matches: bool,
    target_exists: bool,
    matching_targets: int,
    backup_verified: bool,
    external_snapshot_verified: bool,
) -> dict[str, object]:
    """Fail closed before a future migration executor could be authorized."""
    raw_blockers = plan.get("blockers")
    blockers = (
        [str(item) for item in raw_blockers]
        if isinstance(raw_blockers, (list, tuple))
        else ["migration-plan-invalid"]
    )
    if observed_source_sha256.casefold() != str(plan.get("attachment_hash") or "").casefold():
        blockers.append("source-content-changed")
    if not source_etag_matches:
        blockers.append("source-etag-changed")
    if target_exists:
        blockers.append("target-exists-no-overwrite")
    if matching_targets != 0:
        blockers.append("duplicate-register-target")
    if not backup_verified:
        blockers.append("verified-backup-missing")
    if not external_snapshot_verified:
        blockers.append("external-snapshot-missing")
    unique = sorted(set(blockers))
    return {
        "ok": not unique,
        "plan_sha256": str(plan.get("plan_sha256") or ""),
        "blockers": unique,
        "write_executed": False,
        "no_overwrite": True,
    }


def extraction_evidence(metadata: InvoiceMetadata) -> dict[str, object]:
    """Return bounded provenance; raw PDF/OCR evidence is deliberately absent."""
    return {
        "scanner_identity": metadata.technical.scanner_identity,
        "extractor_version": metadata.technical.extractor_version,
        "ruleset_version": metadata.technical.ruleset_version,
        "method": metadata.method,
        "review_reasons": sorted(set(metadata.review_reasons)),
        "date_roles": _date_roles(metadata),
        "field_provenance": _field_provenance(metadata),
        "raw_text_included": False,
    }


def compare_extractor_snapshots(
    *,
    attachment_hash: str,
    legacy: Mapping[str, object],
    current: Mapping[str, object],
    expected: Mapping[str, object],
) -> dict[str, object]:
    """Compare versioned, synthetic or labelled extraction snapshots."""
    digest = str(attachment_hash).casefold()
    if not _SHA256.fullmatch(digest):
        raise ValueError("Vergleich benoetigt einen gueltigen Original-SHA-256")
    fields = ("invoice_date", "invoice_number", "supplier", "gross_amount", "currency")

    def score(snapshot: Mapping[str, object]) -> dict[str, object]:
        comparable = [name for name in fields if name in expected]
        matches = [name for name in comparable if snapshot.get(name) == expected.get(name)]
        return {
            "version": str(snapshot.get("extractor_version") or "legacy-or-missing"),
            "comparable_fields": len(comparable),
            "matching_fields": len(matches),
            "accuracy": round(len(matches) / len(comparable), 6) if comparable else None,
            "missing_fields": [name for name in fields if not snapshot.get(name)],
        }

    legacy_score = score(legacy)
    current_score = score(current)
    changed = [name for name in fields if legacy.get(name) != current.get(name)]
    legacy_accuracy = legacy_score["accuracy"]
    current_accuracy = current_score["accuracy"]
    return {
        "ok": True,
        "attachment_hash": digest,
        "legacy": legacy_score,
        "current": current_score,
        "changed_fields": changed,
        "improved": (
            isinstance(current_accuracy, (int, float))
            and isinstance(legacy_accuracy, (int, float))
            and current_accuracy > legacy_accuracy
        ),
        "document_content_included": False,
    }
