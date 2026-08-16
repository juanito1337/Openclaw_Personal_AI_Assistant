from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import uuid
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path

from .invoice_extract import InvoiceExtractor, InvoiceMetadata, amount_to_cents
from .invoice_reprocess import (
    build_preview_record,
    compute_metadata_proposal_sha256,
    compute_record_sha256,
    message_from_record,
)
from .storage import Storage
from .utils import now_utc_iso

APPROVAL_LABEL = "explicit-user-single-invoice-reprocess"
REGISTER_CLAIM_SECONDS = 300
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_ELIGIBLE_STATUSES = frozenset({"review", "unclassified"})
_FINAL_STATUS = "confirmed"
_SUPPORTED_CURRENCIES = frozenset({"EUR", "USD", "GBP", "CHF"})
_VALUE_LIMITS = {
    "invoice_number": 240,
    "supplier": 300,
    "category": 160,
}


class ApplyRejected(RuntimeError):
    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


def validate_apply_identifiers(attachment_hash: str, expected_preview_sha256: str) -> tuple[str, str]:
    normalized_hash = str(attachment_hash or "").strip().casefold()
    normalized_preview = str(expected_preview_sha256 or "").strip().casefold()
    if not _SHA256.fullmatch(normalized_hash):
        raise ValueError("--hash muss ein vollstaendiger SHA-256 sein")
    if not _SHA256.fullmatch(normalized_preview):
        raise ValueError("--expected-preview-sha256 muss ein vollstaendiger SHA-256 sein")
    return normalized_hash, normalized_preview


def _readonly_connection(database: Path) -> sqlite3.Connection:
    path = Path(database).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f"Rechnungsdatenbank fehlt: {path}")
    connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    return connection


def _joined_invoice(connection: sqlite3.Connection, attachment_hash: str) -> dict[str, object] | None:
    row = connection.execute(
        """
        SELECT i.*, m.message_id AS message_id, m.mailbox_id AS mailbox_id,
               m.last_folder AS last_folder, m.sender_addr AS sender_addr,
               m.sender_name AS sender_name, m.subject AS subject,
               m.received_at AS message_received_at
        FROM invoices i
        LEFT JOIN messages m ON m.stable_key = i.stable_key
        WHERE i.attachment_hash = ?
        """,
        (attachment_hash,),
    ).fetchone()
    return dict(row) if row is not None else None


def _audit_row(
    connection: sqlite3.Connection,
    attachment_hash: str,
    preview_sha256: str,
) -> dict[str, object] | None:
    exists = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='invoice_reprocess_audit'"
    ).fetchone()
    if exists is None:
        return None
    row = connection.execute(
        """
        SELECT * FROM invoice_reprocess_audit
        WHERE attachment_hash=? AND preview_sha256=?
        """,
        (attachment_hash, preview_sha256),
    ).fetchone()
    return dict(row) if row is not None else None


def _read_state(
    database: Path,
    attachment_hash: str,
    preview_sha256: str,
) -> tuple[dict[str, object] | None, dict[str, object] | None]:
    connection = _readonly_connection(database)
    try:
        return (
            _joined_invoice(connection, attachment_hash),
            _audit_row(connection, attachment_hash, preview_sha256),
        )
    finally:
        connection.close()


def _source_status(item: Mapping[str, object]) -> str:
    status = str(item.get("extraction_status") or "").strip()
    return status or "unclassified"


def _require_eligible(item: Mapping[str, object]) -> None:
    if str(item.get("status") or "") not in {"uploaded", "duplicate"}:
        raise ApplyRejected("invoice-not-archived", "Nur archivierte Rechnungen koennen uebernommen werden")
    if not str(item.get("nextcloud_path") or ""):
        raise ApplyRejected("invoice-path-missing", "Archivierte Rechnung besitzt keinen PDF-Pfad")
    status = _source_status(item)
    if status in {"confirmed", "confirmed-manual"}:
        raise ApplyRejected(
            "protected-invoice-status",
            "Bestaetigte oder manuell korrigierte Rechnungen sind geschuetzt",
        )
    if status not in _ELIGIBLE_STATUSES:
        raise ApplyRejected("unsupported-invoice-status", "Rechnung ist nicht review oder unclassified")


def _safe_proposal(metadata: InvoiceMetadata, *, classification: str | None) -> tuple[int, dict[str, object]]:
    if classification is not None and classification != "improved":
        raise ApplyRejected(
            "proposal-not-improved",
            "Nur eindeutig verbesserte Vorschlaege duerfen uebernommen werden",
        )
    if metadata.status != _FINAL_STATUS:
        raise ApplyRejected(
            "proposal-not-confirmed",
            "Der Vorschlag ist weiterhin nicht sicher bestaetigt",
        )
    if metadata.review_reasons:
        raise ApplyRejected("proposal-has-conflicts", "Der Vorschlag enthaelt weiterhin Konflikte")
    if not metadata.date_confirmed:
        raise ApplyRejected("invoice-date-unconfirmed", "Das Rechnungsdatum ist nicht belegt")
    required = {
        "invoice_date": metadata.invoice_date.value,
        "invoice_number": metadata.invoice_number.value,
        "supplier": metadata.supplier.value,
        "gross_amount": metadata.gross_amount.value,
    }
    if any(not str(value or "").strip() for value in required.values()):
        raise ApplyRejected("required-field-missing", "Ein Pflichtfeld des Vorschlags fehlt")
    for field, limit in _VALUE_LIMITS.items():
        if len(str(getattr(metadata, field).value or "")) > limit:
            raise ApplyRejected(
                "field-value-too-long",
                "Ein Feldwert ueberschreitet die sichere Speichergrenze",
            )
    try:
        invoice_year = datetime.strptime(metadata.invoice_date.value, "%Y-%m-%d").year
    except ValueError as exc:
        raise ApplyRejected("invoice-date-invalid", "Das Rechnungsdatum ist ungueltig") from exc
    if not 2000 <= invoice_year <= 2100:
        raise ApplyRejected("invoice-year-invalid", "Das Rechnungsjahr liegt ausserhalb des Vertrags")
    gross = amount_to_cents(metadata.gross_amount.value)
    net = amount_to_cents(metadata.net_amount.value) if metadata.net_amount.value else None
    tax = amount_to_cents(metadata.tax_amount.value) if metadata.tax_amount.value else None
    if gross is None:
        raise ApplyRejected("gross-amount-invalid", "Der Bruttobetrag ist ungueltig")
    if net is not None and tax is not None and abs(gross - (net + tax)) > 2:
        raise ApplyRejected("amount-arithmetic-invalid", "Brutto, Netto und Steuer sind unplausibel")
    if gross >= 0 and tax is not None and tax > gross:
        raise ApplyRejected("tax-above-gross", "Der Steuerbetrag ist groesser als der Bruttobetrag")
    currency = str(metadata.currency.value or "").strip().upper()
    if currency not in _SUPPORTED_CURRENCIES:
        raise ApplyRejected("currency-unproven", "Die Waehrung ist nicht eindeutig belegt")
    values: dict[str, object] = {
        "invoice_date": metadata.invoice_date.value,
        "invoice_number": metadata.invoice_number.value,
        "supplier": metadata.supplier.value,
        "category": metadata.category.value or "Ungeklärt",
        "gross_amount_cents": gross,
        "net_amount_cents": net,
        "tax_amount_cents": tax,
        "currency": currency,
        "due_date": metadata.due_date.value,
        "extraction_status": metadata.status,
        "extraction_confidence": float(metadata.confidence),
        "extraction_method": "reprocess-" + str(metadata.method or "none")[:120],
        "extraction_json": metadata.to_json()[:20000],
        "register_year": invoice_year,
    }
    return invoice_year, values


def _operation_id(attachment_hash: str, preview_sha256: str) -> str:
    return hashlib.sha256(
        f"invoice-reprocess-apply\0{attachment_hash}\0{preview_sha256}".encode()
    ).hexdigest()


def _json_years(value: object) -> list[int]:
    try:
        parsed = json.loads(str(value or "[]"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return []
    if not isinstance(parsed, list):
        return []
    years: set[int] = set()
    for value in parsed:
        try:
            year = int(str(value))
        except (TypeError, ValueError):
            continue
        if 2000 <= year <= 2100:
            years.add(year)
    return sorted(years)


def _begin_local_apply(
    storage: Storage,
    *,
    item: Mapping[str, object],
    attachment_hash: str,
    preview_sha256: str,
    proposal_sha256: str,
    extractor_version: str,
    values: Mapping[str, object],
) -> tuple[dict[str, object], bool]:
    connection = storage.connection
    operation_id = _operation_id(attachment_hash, preview_sha256)
    old_state_sha256 = compute_record_sha256(item)
    old_register_year = (
        int(str(item["register_year"])) if item.get("register_year") is not None else None
    )
    new_register_year = int(str(values["register_year"]))
    register_years = sorted(
        {year for year in (old_register_year, new_register_year) if year is not None}
    )
    timestamp = now_utc_iso()
    try:
        connection.execute("BEGIN IMMEDIATE")
        existing = _audit_row(connection, attachment_hash, preview_sha256)
        if existing is not None:
            connection.rollback()
            return existing, False
        current = _joined_invoice(connection, attachment_hash)
        if current is None:
            raise ApplyRejected("invoice-not-found", "Rechnung wurde vor der Uebernahme entfernt")
        _require_eligible(current)
        if compute_record_sha256(current) != old_state_sha256:
            raise ApplyRejected("record-drift", "Rechnungsdatensatz hat sich seit der Vorschau geaendert")
        cursor = connection.execute(
            """
            UPDATE invoices SET invoice_date=?, invoice_number=?, supplier=?, category=?,
                gross_amount_cents=?, net_amount_cents=?, tax_amount_cents=?, currency=?, due_date=?,
                extraction_status=?, extraction_confidence=?, extraction_method=?, extraction_json=?,
                register_year=?, register_updated_at=?, updated_at=?
            WHERE id=? AND attachment_hash=?
            """,
            (
                values["invoice_date"],
                values["invoice_number"],
                values["supplier"],
                values["category"],
                values["gross_amount_cents"],
                values["net_amount_cents"],
                values["tax_amount_cents"],
                values["currency"],
                values["due_date"],
                values["extraction_status"],
                values["extraction_confidence"],
                values["extraction_method"],
                values["extraction_json"],
                new_register_year,
                timestamp,
                timestamp,
                int(str(current["id"])),
                attachment_hash,
            ),
        )
        if cursor.rowcount != 1:
            raise ApplyRejected("single-record-boundary", "Die Einzel-Datensatz-Grenze wurde verletzt")
        updated = _joined_invoice(connection, attachment_hash)
        if updated is None:
            raise ApplyRejected("invoice-not-found", "Rechnung fehlt nach der lokalen Uebernahme")
        new_state_sha256 = compute_record_sha256(updated)
        connection.execute(
            """
            INSERT INTO invoice_reprocess_audit (
                operation_id, invoice_id, attachment_hash, preview_sha256,
                old_state_sha256, new_state_sha256, proposal_sha256,
                extractor_version, approval_label, source_status, proposed_status,
                old_register_year, new_register_year, register_years_json,
                completed_years_json, result_status, attempt_count, error_code,
                claim_token, created_at, updated_at, completed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '[]',
                      'register-pending', 0, '', '', ?, ?, '')
            """,
            (
                operation_id,
                int(str(current["id"])),
                attachment_hash,
                preview_sha256,
                old_state_sha256,
                new_state_sha256,
                proposal_sha256,
                extractor_version,
                APPROVAL_LABEL,
                _source_status(current),
                str(values["extraction_status"]),
                old_register_year,
                new_register_year,
                json.dumps(register_years, separators=(",", ":")),
                timestamp,
                timestamp,
            ),
        )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    audit = _audit_row(connection, attachment_hash, preview_sha256)
    if audit is None:
        raise RuntimeError("invoice-reprocess-audit-missing-after-commit")
    return audit, True


def _claim_is_stale(updated_at: object) -> bool:
    try:
        parsed = datetime.fromisoformat(str(updated_at))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return (datetime.now(UTC) - parsed.astimezone(UTC)).total_seconds() >= REGISTER_CLAIM_SECONDS
    except (TypeError, ValueError):
        return True


def _claim_register_sync(
    storage: Storage,
    *,
    operation_id: str,
    expected_new_state_sha256: str,
) -> tuple[dict[str, object], str | None]:
    connection = storage.connection
    token = uuid.uuid4().hex
    timestamp = now_utc_iso()
    try:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            "SELECT * FROM invoice_reprocess_audit WHERE operation_id=?",
            (operation_id,),
        ).fetchone()
        if row is None:
            raise ApplyRejected("audit-missing", "Reprocessing-Audit fehlt")
        audit = dict(row)
        current = _joined_invoice(connection, str(audit["attachment_hash"]))
        if current is None or compute_record_sha256(current) != expected_new_state_sha256:
            raise ApplyRejected("local-state-drift", "Lokal uebernommener Datensatz ist gedriftet")
        if str(current.get("extraction_status") or "") != str(audit["proposed_status"]):
            raise ApplyRejected("protected-status-drift", "Extraktionsstatus wurde nachtraeglich geaendert")
        status = str(audit["result_status"])
        if status == "register-syncing" and not _claim_is_stale(audit["updated_at"]):
            connection.rollback()
            return audit, None
        if status not in {"register-pending", "register-failed", "completed", "register-syncing"}:
            raise ApplyRejected(
                "audit-state-invalid",
                "Reprocessing-Audit besitzt keinen fortsetzbaren Zustand",
            )
        connection.execute(
            """
            UPDATE invoice_reprocess_audit
            SET result_status='register-syncing', claim_token=?, attempt_count=attempt_count+1,
                error_code='', updated_at=?
            WHERE operation_id=?
            """,
            (token, timestamp, operation_id),
        )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    claimed = connection.execute(
        "SELECT * FROM invoice_reprocess_audit WHERE operation_id=?",
        (operation_id,),
    ).fetchone()
    if claimed is None:
        raise RuntimeError("invoice-reprocess-audit-missing-after-claim")
    return dict(claimed), token


def _finish_register_sync(
    storage: Storage,
    *,
    operation_id: str,
    claim_token: str,
    completed_years: list[int],
    error_code: str = "",
) -> dict[str, object]:
    successful = not error_code
    status = "completed" if successful else "register-failed"
    timestamp = now_utc_iso()
    cursor = storage.connection.execute(
        """
        UPDATE invoice_reprocess_audit
        SET result_status=?, completed_years_json=?, error_code=?, claim_token='',
            updated_at=?, completed_at=?
        WHERE operation_id=? AND result_status='register-syncing' AND claim_token=?
        """,
        (
            status,
            json.dumps(sorted(set(completed_years)), separators=(",", ":")),
            error_code[:120],
            timestamp,
            timestamp if successful else "",
            operation_id,
            claim_token,
        ),
    )
    if cursor.rowcount != 1:
        storage.connection.rollback()
        raise ApplyRejected("register-claim-lost", "Register-Synchronisationsclaim ging verloren")
    storage.connection.commit()
    row = storage.connection.execute(
        "SELECT * FROM invoice_reprocess_audit WHERE operation_id=?",
        (operation_id,),
    ).fetchone()
    if row is None:
        raise RuntimeError("invoice-reprocess-audit-missing-after-result")
    return dict(row)


def _register_result(value: Mapping[str, object], year: int) -> dict[str, object]:
    status = re.sub(r"[^a-z0-9_.-]+", "-", str(value.get("status") or "unknown").casefold())[:80]
    return {
        "year": year,
        "ok": bool(value.get("ok")),
        "status": status,
        "sha256": str(value.get("sha256") or "")[:64],
        "path": str(value.get("path") or "")[:300],
    }


def _register_error_code(result: Mapping[str, object]) -> str:
    status = str(result.get("status") or "").casefold()
    detail = str(result.get("detail") or "").casefold()
    if "conflict" in status or "parallel geaendert" in detail or "etag" in detail:
        return "register-conflict"
    if "unavailable" in status or "timeout" in detail or "nicht erreichbar" in detail:
        return "register-unavailable"
    return "register-sync-failed"


def _result_payload(
    audit: Mapping[str, object],
    *,
    ok: bool,
    status: str,
    local_applied: bool,
    idempotent: bool,
    registers: list[dict[str, object]] | None = None,
    error: str = "",
) -> dict[str, object]:
    return {
        "ok": ok,
        "status": status,
        "operation_id": str(audit.get("operation_id") or ""),
        "attachment_hash": str(audit.get("attachment_hash") or ""),
        "preview_sha256": str(audit.get("preview_sha256") or ""),
        "extractor_version": str(audit.get("extractor_version") or ""),
        "audit_status": str(audit.get("result_status") or ""),
        "local_applied": local_applied,
        "idempotent": idempotent,
        "register_years": _json_years(audit.get("register_years_json")),
        "registers": list(registers or []),
        "retry_safe": not ok and status in {"register-sync-in-progress", "local-applied-register-failed"},
        "error": error,
        "effects": {
            "sqlite": "single-record-and-content-free-audit" if local_applied else "unchanged",
            "nextcloud_register": "etag-sha-schema-guarded",
            "pdf": "read-only-unchanged",
        },
    }


def run_reprocess_apply(
    database: Path,
    *,
    attachment_hash: str,
    expected_preview_sha256: str,
    extractor: InvoiceExtractor,
    read_pdf: Callable[[str], bytes],
    scan_pdf: Callable[[bytes, str], str],
    sync_register: Callable[[Storage, int], Mapping[str, object]],
) -> dict[str, object]:
    attachment_hash, expected_preview_sha256 = validate_apply_identifiers(
        attachment_hash, expected_preview_sha256
    )
    local_applied = False
    local_state_present = False
    try:
        item, existing_audit = _read_state(database, attachment_hash, expected_preview_sha256)
        local_state_present = existing_audit is not None
        if item is None:
            raise ApplyRejected("invoice-not-found", "Rechnung mit diesem Hash wurde nicht gefunden")
        if existing_audit is None:
            _require_eligible(item)
        else:
            if compute_record_sha256(item) != str(existing_audit["new_state_sha256"]):
                raise ApplyRejected("local-state-drift", "Lokal uebernommener Datensatz ist gedriftet")
            if str(item.get("extraction_status") or "") != str(existing_audit["proposed_status"]):
                raise ApplyRejected(
                    "protected-status-drift",
                    "Extraktionsstatus wurde nachtraeglich geaendert",
                )

        data = read_pdf(str(item.get("nextcloud_path") or ""))
        actual_hash = hashlib.sha256(data).hexdigest()
        if actual_hash != attachment_hash:
            raise ApplyRejected(
                "pdf-hash-mismatch",
                "PDF-Inhalt stimmt nicht mit dem freigegebenen Hash ueberein",
            )
        scanner_identity = scan_pdf(data, str(item.get("original_filename") or "invoice.pdf"))
        metadata = extractor.extract(
            data,
            message_from_record(item),
            filename=str(item.get("original_filename") or "invoice.pdf"),
            scanner_identity=scanner_identity,
        )
        proposal_sha256 = compute_metadata_proposal_sha256(item, metadata)

        idempotent = existing_audit is not None
        if existing_audit is None:
            preview = build_preview_record(item, pdf_sha256=actual_hash, metadata=metadata)
            if str(preview["preview_sha256"]) != expected_preview_sha256:
                raise ApplyRejected(
                    "preview-drift",
                    "Vorschau-Digest stimmt nicht mehr mit dem Eingang ueberein",
                )
            _, values = _safe_proposal(metadata, classification=str(preview["classification"]))
            storage = Storage(Path(database))
            try:
                audit, local_applied = _begin_local_apply(
                    storage,
                    item=item,
                    attachment_hash=attachment_hash,
                    preview_sha256=expected_preview_sha256,
                    proposal_sha256=proposal_sha256,
                    extractor_version=metadata.technical.extractor_version,
                    values=values,
                )
            finally:
                storage.close()
            local_state_present = True
            idempotent = not local_applied
            if not local_applied:
                if metadata.technical.extractor_version != str(audit["extractor_version"]):
                    raise ApplyRejected(
                        "extractor-version-drift",
                        "Extraktorversion hat sich seit der Freigabe geaendert",
                    )
                if proposal_sha256 != str(audit["proposal_sha256"]):
                    raise ApplyRejected(
                        "proposal-drift",
                        "Extraktionsvorschlag hat sich seit der Freigabe geaendert",
                    )
        else:
            _safe_proposal(metadata, classification=None)
            if metadata.technical.extractor_version != str(existing_audit["extractor_version"]):
                raise ApplyRejected(
                    "extractor-version-drift",
                    "Extraktorversion hat sich seit der Freigabe geaendert",
                )
            if proposal_sha256 != str(existing_audit["proposal_sha256"]):
                raise ApplyRejected(
                    "proposal-drift",
                    "Extraktionsvorschlag hat sich seit der Freigabe geaendert",
                )
            audit = existing_audit

        storage = Storage(Path(database))
        try:
            current = _joined_invoice(storage.connection, attachment_hash)
            if current is None or compute_record_sha256(current) != str(audit["new_state_sha256"]):
                raise ApplyRejected("local-state-drift", "Lokal uebernommener Datensatz ist gedriftet")
            claimed, token = _claim_register_sync(
                storage,
                operation_id=str(audit["operation_id"]),
                expected_new_state_sha256=str(audit["new_state_sha256"]),
            )
            if token is None:
                return _result_payload(
                    claimed,
                    ok=False,
                    status="register-sync-in-progress",
                    local_applied=local_applied,
                    idempotent=idempotent,
                    error="register-sync-in-progress",
                )
            completed_years: list[int] = []
            register_results: list[dict[str, object]] = []
            for year in _json_years(claimed["register_years_json"]):
                current = _joined_invoice(storage.connection, attachment_hash)
                if current is None or compute_record_sha256(current) != str(claimed["new_state_sha256"]):
                    failed = _finish_register_sync(
                        storage,
                        operation_id=str(claimed["operation_id"]),
                        claim_token=token,
                        completed_years=completed_years,
                        error_code="local-state-drift",
                    )
                    return _result_payload(
                        failed,
                        ok=False,
                        status="local-applied-register-failed",
                        local_applied=local_applied,
                        idempotent=idempotent,
                        registers=register_results,
                        error="local-state-drift",
                    )
                try:
                    raw_result = sync_register(storage, year)
                except Exception as exc:
                    raw_result = {
                        "ok": False,
                        "status": "register-exception-" + type(exc).__name__.casefold(),
                    }
                result = _register_result(raw_result, year)
                register_results.append(result)
                if not result["ok"]:
                    error_code = _register_error_code(raw_result)
                    failed = _finish_register_sync(
                        storage,
                        operation_id=str(claimed["operation_id"]),
                        claim_token=token,
                        completed_years=completed_years,
                        error_code=error_code,
                    )
                    return _result_payload(
                        failed,
                        ok=False,
                        status="local-applied-register-failed",
                        local_applied=local_applied,
                        idempotent=idempotent,
                        registers=register_results,
                        error=error_code,
                    )
                completed_years.append(year)
            completed = _finish_register_sync(
                storage,
                operation_id=str(claimed["operation_id"]),
                claim_token=token,
                completed_years=completed_years,
            )
            return _result_payload(
                completed,
                ok=True,
                status="reprocess-applied" if local_applied else "reprocess-already-applied",
                local_applied=local_applied,
                idempotent=idempotent,
                registers=register_results,
            )
        finally:
            storage.close()
    except ApplyRejected as exc:
        return {
            "ok": False,
            "status": "rejected",
            "attachment_hash": attachment_hash,
            "preview_sha256": expected_preview_sha256,
            "error": exc.code,
            "detail": exc.detail,
            "local_applied": local_applied,
            "local_apply_present": local_state_present,
            "retry_safe": False,
            "effects": {
                "sqlite": (
                    "single-record-and-content-free-audit"
                    if local_state_present
                    else "unchanged"
                ),
                "nextcloud_register": "not-accessed",
                "pdf": "read-only",
            },
        }
    except Exception as exc:
        if isinstance(exc, RuntimeError) and str(exc) == "antivirus-gate-blocked":
            code = "antivirus-gate-blocked"
        elif isinstance(exc, PermissionError):
            code = "permission-denied"
        elif isinstance(exc, (FileNotFoundError, OSError)):
            code = "source-read-failed"
        elif isinstance(exc, ValueError):
            code = "invalid-pdf-or-budget"
        else:
            code = "apply-failed"
        return {
            "ok": False,
            "status": (
                "local-applied-reconcile-failed" if local_state_present else "apply-failed"
            ),
            "attachment_hash": attachment_hash,
            "preview_sha256": expected_preview_sha256,
            "error": code,
            "error_type": type(exc).__name__,
            "local_applied": local_applied,
            "local_apply_present": local_state_present,
            "retry_safe": local_state_present,
            "effects": {
                "sqlite": (
                    "single-record-and-content-free-audit"
                    if local_state_present
                    else "unchanged"
                ),
                "nextcloud_register": "not-confirmed",
                "pdf": "read-only",
            },
        }
