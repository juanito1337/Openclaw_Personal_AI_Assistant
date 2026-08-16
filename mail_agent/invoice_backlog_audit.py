from __future__ import annotations

import json
import re
import sqlite3
from collections import Counter
from collections.abc import Mapping
from datetime import date
from pathlib import Path, PurePosixPath
from typing import cast

AUDIT_SCHEMA_VERSION = 1

_KNOWN_ARCHIVE_STATUSES = frozenset({"uploaded", "duplicate", "error"})
_KNOWN_EXTRACTION_STATUSES = frozenset(
    {"confirmed", "confirmed-manual", "review", "error"}
)
_SAFE_EXTRACTOR_VERSION = re.compile(r"^m\d{1,3}\.\d{1,3}(?:\.\d{1,3})?$")
_SAFE_RULESET_VERSION = re.compile(r"^20\d{2}-\d{2}-\d{2}(?:\.\d{1,3})?$")
_TYPED_AMOUNT_REASON = re.compile(r"^amount:[a-z0-9_.-]+(?::[a-z0-9_.-]+){0,2}$")
_PATH_YEAR = re.compile(r"(?:^|/)(20\d{2}|21\d{2})(?:/|$)")
_REQUIRED_FIELDS = {
    "invoice_date": "invoice_date",
    "invoice_number": "invoice_number",
    "supplier": "supplier",
    "gross_amount": "gross_amount_cents",
}
_COHORT_NAMES = (
    "unclassified_legacy",
    "review",
    "confirmed",
    "manual_corrections",
    "other",
)


def _clean_remote_path(value: object) -> str:
    raw = str(value or "").strip().replace("\\", "/")
    if not raw:
        return ""
    return str(PurePosixPath("/" + raw.lstrip("/"))).lstrip("/")


def _path_inside(path: str, root: str) -> bool:
    return bool(path and root and (path == root or path.startswith(root.rstrip("/") + "/")))


def _integer(value: object) -> int | None:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return int(value)
    except ValueError:
        return None


def _year(value: object) -> int | None:
    result = _integer(value)
    if result is None:
        return None
    return result if 2000 <= result <= 2100 else None


def _path_year(value: object) -> int | None:
    match = _PATH_YEAR.search(_clean_remote_path(value))
    return int(match.group(1)) if match else None


def _source_year(row: Mapping[str, object]) -> int | None:
    return _year(row.get("register_year")) or _path_year(row.get("nextcloud_path"))


def _cohort(row: Mapping[str, object]) -> str:
    status = str(row.get("extraction_status") or "").strip().casefold()
    if not status:
        return "unclassified_legacy"
    if status == "review":
        return "review"
    if status == "confirmed":
        return "confirmed"
    if status == "confirmed-manual":
        return "manual_corrections"
    return "other"


def _metadata(value: object) -> dict[str, object]:
    try:
        parsed = json.loads(str(value or ""))
    except (json.JSONDecodeError, TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _safe_metadata_version(metadata: Mapping[str, object], name: str) -> str:
    technical = metadata.get("technical")
    raw = technical.get(name) if isinstance(technical, dict) else ""
    value = str(raw or "").strip()
    if not value:
        return "legacy-or-missing"
    pattern = (
        _SAFE_EXTRACTOR_VERSION if name == "extractor_version" else _SAFE_RULESET_VERSION
    )
    return value if pattern.fullmatch(value) else "invalid-redacted"


def _typed_amount_reasons(metadata: Mapping[str, object]) -> list[str]:
    raw = metadata.get("review_reasons")
    if not isinstance(raw, list):
        return []
    return sorted(
        {
            value
            for item in raw
            if (value := str(item or "").strip().casefold())
            and _TYPED_AMOUNT_REASON.fullmatch(value)
        }
    )[:32]


def _missing_required(row: Mapping[str, object]) -> dict[str, bool]:
    result: dict[str, bool] = {}
    for public_name, column in _REQUIRED_FIELDS.items():
        value = row.get(column)
        result[public_name] = value is None if column.endswith("_cents") else not str(value or "").strip()
    return result


def _invoice_date(row: Mapping[str, object]) -> date | None:
    raw = str(row.get("invoice_date") or "").strip()
    if not raw:
        return None
    try:
        parsed = date.fromisoformat(raw)
    except ValueError:
        return None
    return parsed if 2000 <= parsed.year <= 2100 else None


def _plausibility(row: Mapping[str, object]) -> dict[str, bool]:
    gross = _integer(row.get("gross_amount_cents"))
    net = _integer(row.get("net_amount_cents"))
    tax = _integer(row.get("tax_amount_cents"))
    triple = all(value is not None for value in (gross, net, tax))
    amounts = [value for value in (gross, net, tax) if value is not None]
    signs = {1 if value > 0 else -1 for value in amounts if value != 0}
    parsed_date = _invoice_date(row)
    register_year = _year(row.get("register_year"))
    return {
        "complete_amount_triples": triple,
        "inconsistent_amount_triples": bool(
            gross is not None
            and net is not None
            and tax is not None
            and abs(gross - net - tax) > 2
        ),
        "tax_without_gross": tax is not None and gross is None,
        "tax_above_gross": bool(
            tax is not None and gross is not None and abs(tax) > abs(gross)
        ),
        "mixed_sign_amounts": len(signs) > 1,
        "invalid_invoice_date": bool(str(row.get("invoice_date") or "").strip())
        and parsed_date is None,
        "register_invoice_year_mismatch": bool(
            parsed_date is not None
            and register_year is not None
            and parsed_date.year != register_year
        ),
    }


def _empty_cohort() -> dict[str, object]:
    return {
        "count": 0,
        "source_years": {},
        "missing_required_fields": {name: 0 for name in _REQUIRED_FIELDS},
        "plausibility_errors": {
            "complete_amount_triples": 0,
            "inconsistent_amount_triples": 0,
            "tax_without_gross": 0,
            "tax_above_gross": 0,
            "mixed_sign_amounts": 0,
            "invalid_invoice_date": 0,
            "register_invoice_year_mismatch": 0,
            "typed_amount_review_reasons": {},
        },
    }


def _increment_cohort(
    target: dict[str, object],
    row: Mapping[str, object],
    metadata: Mapping[str, object],
) -> None:
    target["count"] = cast(int, target["count"]) + 1
    years = cast(dict[str, int], target["source_years"])
    year = _source_year(row)
    year_key = str(year) if year is not None else "unknown"
    years[year_key] = int(years.get(year_key, 0)) + 1

    missing = cast(dict[str, int], target["missing_required_fields"])
    for name, is_missing in _missing_required(row).items():
        missing[name] = int(missing[name]) + int(is_missing)

    plausibility = cast(dict[str, object], target["plausibility_errors"])
    for name, present in _plausibility(row).items():
        plausibility[name] = cast(int, plausibility[name]) + int(present)
    reasons = cast(dict[str, int], plausibility["typed_amount_review_reasons"])
    for reason in _typed_amount_reasons(metadata):
        reasons[reason] = int(reasons.get(reason, 0)) + 1


def _sorted_counts(values: Mapping[str, int]) -> dict[str, int]:
    def key(item: tuple[str, int]) -> tuple[int, object]:
        name = item[0]
        return (0, int(name)) if name.isdigit() else (1, name)

    return dict(sorted(values.items(), key=key))


def run_invoice_backlog_audit(
    database: Path,
    *,
    invoice_folder: str,
    review_subfolder: str,
) -> dict[str, object]:
    """Aggregate invoice quality without returning identifiers or opening remote services."""
    path = Path(database).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f"Rechnungsdatenbank fehlt: {path}")
    invoice_root = _clean_remote_path(invoice_folder)
    review_root = _clean_remote_path(f"{invoice_root}/{review_subfolder}")
    if not invoice_root or not review_root:
        raise ValueError("Konfigurierter Rechnungs- oder Pruefordner ist ungueltig")

    # SQLite's WAL read path may create shared-memory sidecars. A closed database
    # without a WAL can instead be opened immutable and remains byte-/directory-
    # side-effect-free. An active WAL must stay visible, so that case retains the
    # normal read-only URI and never claims an older main-file snapshot.
    wal_path = path.with_name(path.name + "-wal")
    uri = path.as_uri() + ("?mode=ro" if wal_path.exists() else "?mode=ro&immutable=1")
    connection = sqlite3.connect(uri, uri=True)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA query_only=ON")
        rows = connection.execute(
            """
            SELECT status, invoice_date, invoice_number, supplier,
                   gross_amount_cents, net_amount_cents, tax_amount_cents,
                   extraction_status, extraction_json, register_year, nextcloud_path
            FROM invoices
            ORDER BY id
            """
        ).fetchall()
    finally:
        connection.close()

    archive_statuses: Counter[str] = Counter()
    extraction_statuses: Counter[str] = Counter()
    extractor_versions: Counter[str] = Counter()
    ruleset_versions: Counter[str] = Counter()
    cohorts = {name: _empty_cohort() for name in _COHORT_NAMES}
    path_deviations = {
        "review_in_review_subfolder": 0,
        "review_outside_review_subfolder": 0,
        "review_missing_path": 0,
        "stored_outside_invoice_root": 0,
        "register_path_year_mismatch": 0,
    }

    for stored in rows:
        row = dict(stored)
        archive_status = str(row.get("status") or "").strip().casefold()
        archive_statuses[
            archive_status if archive_status in _KNOWN_ARCHIVE_STATUSES else "other"
        ] += 1
        extraction_status = str(row.get("extraction_status") or "").strip().casefold()
        extraction_statuses[
            extraction_status
            if extraction_status in _KNOWN_EXTRACTION_STATUSES
            else ("unclassified" if not extraction_status else "other")
        ] += 1

        metadata = _metadata(row.get("extraction_json"))
        extractor_versions[_safe_metadata_version(metadata, "extractor_version")] += 1
        ruleset_versions[_safe_metadata_version(metadata, "ruleset_version")] += 1
        _increment_cohort(cohorts[_cohort(row)], row, metadata)

        remote_path = _clean_remote_path(row.get("nextcloud_path"))
        if remote_path and not _path_inside(remote_path, invoice_root):
            path_deviations["stored_outside_invoice_root"] += 1
        register_year = _year(row.get("register_year"))
        path_year = _path_year(remote_path)
        if register_year is not None and path_year is not None and register_year != path_year:
            path_deviations["register_path_year_mismatch"] += 1
        if extraction_status == "review":
            if not remote_path:
                path_deviations["review_missing_path"] += 1
            elif _path_inside(remote_path, review_root):
                path_deviations["review_in_review_subfolder"] += 1
            else:
                path_deviations["review_outside_review_subfolder"] += 1

    for cohort in cohorts.values():
        years = cast(dict[str, int], cohort["source_years"])
        cohort["source_years"] = _sorted_counts(years)
        plausibility = cast(dict[str, object], cohort["plausibility_errors"])
        reasons = cast(dict[str, int], plausibility["typed_amount_review_reasons"])
        plausibility["typed_amount_review_reasons"] = dict(sorted(reasons.items()))

    return {
        "ok": True,
        "read_only": True,
        "schema_version": AUDIT_SCHEMA_VERSION,
        "record_count": len(rows),
        "status_distribution": {
            "archive": dict(sorted(archive_statuses.items())),
            "extraction": dict(sorted(extraction_statuses.items())),
        },
        "cohorts": cohorts,
        "extractor_versions": dict(sorted(extractor_versions.items())),
        "ruleset_versions": dict(sorted(ruleset_versions.items())),
        "path_deviations": {
            **path_deviations,
            "automatic_move_available": False,
        },
        "workflow": {
            "preview_statuses": ["review", "unclassified"],
            "bulk_apply_available": False,
            "single_apply_requires_explicit_approval": True,
        },
        "privacy": {
            "document_content_included": False,
            "identifiers_included": False,
            "paths_included": False,
        },
        "effects": {
            "sqlite": "unchanged-read-only",
            "nextcloud": "not-accessed",
            "pdf": "not-accessed",
            "audit": "not-accessed",
        },
    }
