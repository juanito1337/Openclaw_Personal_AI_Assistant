from __future__ import annotations

import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from mail_agent.invoice_extract import (
    ExtractionTechnicalMetadata,
    FieldCandidate,
    FieldValue,
    InvoiceMetadata,
)
from mail_agent.invoice_migration import (
    build_path_migration_plan,
    compare_extractor_snapshots,
    extraction_evidence,
    validate_path_migration_observation,
)
from mail_agent.invoice_reprocess import build_preview_record
from personal_assistant.job_control import JobController
from personal_assistant.portfolio import PortfolioService


def _metadata(*, invoice_date: str = "2026-04-03") -> InvoiceMetadata:
    return InvoiceMetadata(
        invoice_date=FieldValue(invoice_date, 0.98, "private evidence"),
        invoice_number=FieldValue("SYN-168", 0.97, "private evidence"),
        supplier=FieldValue("Synthetic Supplier", 0.91, "private evidence"),
        gross_amount=FieldValue("119.00", 0.99, "private evidence"),
        currency=FieldValue("EUR", 0.99, "private evidence"),
        status="confirmed",
        confidence=0.96,
        method="native-text",
        field_candidates=[
            FieldCandidate(
                field="invoice_date",
                role="invoice-date",
                raw_value="03.04.2026",
                normalized_value=invoice_date,
                source="native",
                evidence_type="labeled-same-line",
                evidence="private evidence",
                confidence=0.98,
            ),
            FieldCandidate(
                field="invoice_date",
                role="due-date",
                raw_value="17.04.2026",
                normalized_value="2026-04-17",
                source="native",
                evidence_type="labeled-same-line",
                evidence="private evidence",
                confidence=0.96,
                excluded_reason="not-invoice-date:due-date",
            ),
        ],
        technical=ExtractionTechnicalMetadata(
            extractor_version="m16.8",
            ruleset_version="2026-09-21.1",
            scanner_identity="clamav:synthetic-signatures",
        ),
    )


def _item(pdf_hash: str) -> dict[str, object]:
    return {
        "id": 168,
        "attachment_hash": pdf_hash,
        "nextcloud_path": "Assistent/Rechnungen/2025/12/synthetic.pdf",
        "register_year": 2025,
        "received_date": "2025-12-31",
        "extraction_status": "review",
        "extraction_json": "{}",
        "invoice_date": "",
        "invoice_number": "",
        "supplier": "",
        "category": "",
        "gross_amount_cents": None,
        "net_amount_cents": None,
        "tax_amount_cents": None,
        "currency": "EUR",
        "due_date": "",
    }


def test_invoice_evidence_is_content_free_and_preserves_date_roles() -> None:
    evidence = extraction_evidence(_metadata())
    rendered = repr(evidence)
    assert "private evidence" not in rendered
    assert evidence["scanner_identity"] == "clamav:synthetic-signatures"
    assert evidence["extractor_version"] == "m16.8"
    assert [item["role"] for item in evidence["date_roles"]] == [  # type: ignore[index]
        "due-date",
        "invoice-date",
    ]
    assert evidence["raw_text_included"] is False


def test_preview_binds_hash_scanner_extractor_fields_and_migration() -> None:
    digest = hashlib.sha256(b"synthetic invoice m16.8").hexdigest()
    preview = build_preview_record(_item(digest), pdf_sha256=digest, metadata=_metadata())
    assert preview["evidence"]["original_sha256"] == digest  # type: ignore[index]
    assert preview["evidence"]["scanner_identity"] == "clamav:synthetic-signatures"  # type: ignore[index]
    plan = preview["path_migration"]
    assert plan["migration_required"] is True  # type: ignore[index]
    assert plan["target_path"] == "Assistent/Rechnungen/2026/04/synthetic.pdf"  # type: ignore[index]
    assert plan["no_overwrite"] is True  # type: ignore[index]
    assert plan["bulk_apply_available"] is False  # type: ignore[index]


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({"observed_source_sha256": "b" * 64}, "source-content-changed"),
        ({"target_exists": True}, "target-exists-no-overwrite"),
        ({"matching_targets": 2}, "duplicate-register-target"),
        ({"backup_verified": False}, "verified-backup-missing"),
        ({"external_snapshot_verified": False}, "external-snapshot-missing"),
        ({"source_etag_matches": False}, "source-etag-changed"),
    ],
)
def test_path_migration_blocks_each_unproven_precondition(
    overrides: dict[str, object], expected: str
) -> None:
    digest = "a" * 64
    plan = build_path_migration_plan(
        _item(digest), _metadata(), invoice_root="Assistent/Rechnungen", pdf_sha256=digest
    )
    observed: dict[str, object] = {
        "observed_source_sha256": digest,
        "source_etag_matches": True,
        "target_exists": False,
        "matching_targets": 0,
        "backup_verified": True,
        "external_snapshot_verified": True,
    }
    observed.update(overrides)
    result = validate_path_migration_observation(plan, **observed)  # type: ignore[arg-type]
    assert result["ok"] is False
    assert expected in result["blockers"]
    assert result["write_executed"] is False


def test_path_migration_positive_validation_still_does_not_execute() -> None:
    digest = "a" * 64
    plan = build_path_migration_plan(
        _item(digest), _metadata(), invoice_root="Assistent/Rechnungen", pdf_sha256=digest
    )
    result = validate_path_migration_observation(
        plan,
        observed_source_sha256=digest,
        source_etag_matches=True,
        target_exists=False,
        matching_targets=0,
        backup_verified=True,
        external_snapshot_verified=True,
    )
    assert result == {
        "ok": True,
        "plan_sha256": plan["plan_sha256"],
        "blockers": [],
        "write_executed": False,
        "no_overwrite": True,
    }


def test_legacy_and_current_extractors_are_compared_on_same_labelled_document() -> None:
    result = compare_extractor_snapshots(
        attachment_hash="a" * 64,
        legacy={
            "extractor_version": "legacy",
            "invoice_date": "2025-12-31",
            "invoice_number": "",
            "supplier": "Synthetic Supplier",
            "gross_amount": "119.00",
            "currency": "EUR",
        },
        current={
            "extractor_version": "m16.8",
            "invoice_date": "2026-04-03",
            "invoice_number": "SYN-168",
            "supplier": "Synthetic Supplier",
            "gross_amount": "119.00",
            "currency": "EUR",
        },
        expected={
            "invoice_date": "2026-04-03",
            "invoice_number": "SYN-168",
            "supplier": "Synthetic Supplier",
            "gross_amount": "119.00",
            "currency": "EUR",
        },
    )
    assert result["legacy"]["accuracy"] == 0.6  # type: ignore[index]
    assert result["current"]["accuracy"] == 1.0  # type: ignore[index]
    assert result["improved"] is True
    assert result["document_content_included"] is False


def _portfolio_diagnosis(
    *,
    enabled: bool = True,
    configured: bool = True,
    job: str = "on",
    health_ok: bool = True,
    mapped: bool = True,
    quote_error: str = "",
    research_entitlement: str = "verified",
) -> dict[str, object]:
    service = object.__new__(PortfolioService)
    service.settings = SimpleNamespace(enabled=enabled)
    service._job_state_provider = lambda: {
        "desired": job,
        "state": "configured-on" if job == "on" else "off",
        "ok": True,
    }
    service.research_status = lambda: {"entitlement": {"state": research_entitlement}}
    health = {
        "ok": health_ok,
        "state": "healthy" if health_ok else "failed",
        "last_run": {"error": quote_error},
        "instruments": [{"held": True, "mapping_confirmed": mapped}],
    }
    return service._diagnostic_state(
        health=health,
        configuration={"ok": configured},
        database_integrity="ok",
    )


@pytest.mark.parametrize(
    ("kwargs", "state", "ok"),
    [
        ({"enabled": False}, "off", True),
        ({"job": "off", "health_ok": False}, "off", True),
        ({"configured": False}, "configured-limited", False),
        ({"mapped": False, "health_ok": False}, "mapping-required", False),
        ({"health_ok": False}, "stale", False),
        ({}, "healthy", True),
        (
            {"research_entitlement": "denied"},
            "provider-entitlement-denied",
            False,
        ),
    ],
)
def test_portfolio_states_are_closed_and_job_off_is_not_failure(
    kwargs: dict[str, object], state: str, ok: bool
) -> None:
    result = _portfolio_diagnosis(**kwargs)  # type: ignore[arg-type]
    assert result["state"] == state
    assert result["ok"] is ok
    assert result["declared_profile_automatic_changes"] is False


@pytest.mark.parametrize(
    ("error", "category", "status_code", "retryable"),
    [
        ("EODHD: HTTP 401", "provider-authentication-denied", 401, False),
        ("EODHD: HTTP 402", "provider-payment-required", 402, False),
        ("EODHD: HTTP 403", "provider-entitlement-denied", 403, False),
        ("EODHD: HTTP 429", "provider-rate-limited", 429, True),
        ("EODHD lieferte keinen Kurs", "provider-empty-response", None, True),
    ],
)
def test_provider_failures_remain_distinct_without_fallback(
    error: str, category: str, status_code: int | None, retryable: bool
) -> None:
    result = PortfolioService._provider_failure(error)
    assert result == {
        "state": category,
        "status_code": status_code,
        "retryable": retryable,
    }


def test_job_desired_status_is_read_only_and_does_not_probe_runtime(tmp_path: Path) -> None:
    controller = JobController(
        state_path=tmp_path / "jobs.json",
        workspace_root=tmp_path,
        unit_dir=tmp_path / "units",
        runner=lambda *_args: pytest.fail("desired status must not execute commands"),
    )
    result = controller.desired_status("portfolio")
    assert result["desired"] == "off"
    assert result["state"] == "off"
    assert result["observed_runtime_checked"] is False
