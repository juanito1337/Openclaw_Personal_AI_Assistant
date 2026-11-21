from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from mail_agent.cli import _handle_invoices, build_parser
from mail_agent.config import InvoiceConfig
from mail_agent.storage import Storage
from personal_assistant.agent_tool_orchestration import guard_claims, route_intent
from personal_assistant.nextcloud_connection import apply_tool_connection
from personal_assistant.tool_registry import build_tool_registry
from personal_assistant.tool_settings import InvoiceToolSettings, ToolSettings

LOCAL_READS = {
    "assistant.invoices.status", "assistant.invoices.list",
    "assistant.invoices.audit", "assistant.invoices.review",
}


def managed_settings(tmp_path: Path, *, readable: bool = True, user: str = "assistant") -> ToolSettings:
    settings = ToolSettings(path=tmp_path / "unused-tools.toml", operations_profile="standard")
    settings.mail.invoices = InvoiceToolSettings(
        enabled=True, resource_id="legacy-invoices", folder="Selected/Invoices",
    )
    return apply_tool_connection(settings, {
        "server_url": "https://synthetic.invalid", "user_id": user,
        "discovery_snapshot": {"file_roots": [{
            "href": f"/remote.php/dav/files/{user}/", "can_read": readable, "can_create": False,
        }]},
    })


@pytest.mark.parametrize("readable,user", [(True, "assistant"), (False, "assistant"), (True, "changed")])
def test_managed_account_preserves_local_reads_without_retargeting_archive(tmp_path, readable, user):
    settings = managed_settings(tmp_path, readable=readable, user=user)
    ids = {tool.id for tool in build_tool_registry(settings) if "invoice" in tool.id}
    assert ids == LOCAL_READS
    assert not settings.mail.invoices.enabled
    assert settings.mail.invoices.resource_id == "legacy-invoices"
    assert settings.mail.invoices.folder == "Selected/Invoices"


def test_disabled_legacy_workflow_keeps_local_diagnostics_available(tmp_path):
    settings = ToolSettings(path=tmp_path / "unused-tools.toml")
    assert {tool.id for tool in build_tool_registry(settings) if "invoice" in tool.id} == LOCAL_READS


@pytest.fixture
def invoice_database(tmp_path):
    database = tmp_path / "mail.sqlite3"
    storage = Storage(database)
    try:
        storage.record_invoice(
            stable_key="synthetic", attachment_hash="a" * 64, original_filename="private.pdf",
            nextcloud_path="Selected/Invoices/2025/01/private.pdf", size_bytes=20, status="uploaded",
            extraction_status="review", register_year=2025,
        )
    finally:
        storage.close()
    return database


def run_read(command, database, settings, capsys, *, ocr_ok=True, limit=None):
    args = build_parser().parse_args([
        "invoices", command, *(["--limit", str(limit)] if limit is not None else []),
    ])
    config = SimpleNamespace(runtime=SimpleNamespace(database=database), invoices=InvoiceConfig())
    with (
        patch("mail_agent.cli.load_tool_settings", return_value=settings),
        patch("mail_agent.cli.InvoiceExtractor.doctor", return_value={"ok": ocr_ok}),
        patch.object(Storage, "_migrate", side_effect=AssertionError("read must not migrate")),
    ):
        code = _handle_invoices(args, config)
    return code, json.loads(capsys.readouterr().out)


@pytest.mark.parametrize("command", ["status", "list", "review", "audit"])
def test_local_invoice_reads_survive_disabled_workflow_without_migration(
    command, invoice_database, tmp_path, capsys,
):
    before = invoice_database.read_bytes()
    code, result = run_read(command, invoice_database, managed_settings(tmp_path), capsys)
    assert invoice_database.read_bytes() == before
    if command == "status":
        assert code == 1 and not result["ok"]
        assert result["status"] == "partial"
        assert result["scope"] == "local-invoice-diagnostics"
        assert result["counts"] == {"all": 1, "review": 1, "confirmed": 0}
        assert result["register"]["years"][0]["year"] == 2025
        assert result["local_evidence"]["source"] == "invoice-sqlite"
        assert result["local_evidence"]["complete"]
        datetime.fromisoformat(result["checked_at"])
        archive = result["archive"]
        assert archive["selection"]["status"] == "disabled"
        assert archive["selection"]["code"] == "managed-account-archive-selection-required"
        assert archive["selection"]["active_resource_id"] is None
        for check in (
            "availability", "authentication", "capabilities", "resource_access", "runtime_activity",
        ):
            assert archive[check]["status"] == "unknown"
        assert result["remote_state_verified"] is False
    elif command == "audit":
        assert code == 0 and result["record_count"] == 1
        assert result["effects"]["nextcloud"] == "not-accessed"
    else:
        assert code == 0 and result["count"] == 1
        assert result["records"][0]["status"] == "uploaded"
        assert result["source"] == "invoice-sqlite"
        assert result["complete"] and result["results_may_be_truncated"] is False
        assert result["remote_state_verified"] is False


@pytest.mark.parametrize("command", ["status", "list", "review"])
def test_missing_invoice_database_is_failure_not_new_empty_register(command, tmp_path, capsys):
    database = tmp_path / "missing.sqlite3"
    with pytest.raises((FileNotFoundError, sqlite3.OperationalError)):
        run_read(command, database, managed_settings(tmp_path), capsys)
    assert not database.exists()


def test_ocr_failure_keeps_local_metadata_and_archive_blocker(invoice_database, tmp_path, capsys):
    code, result = run_read("status", invoice_database, managed_settings(tmp_path), capsys, ocr_ok=False)
    assert code == 1 and not result["ok"]
    assert result["ocr"]["ok"] is False
    assert result["local_evidence"]["complete"]
    assert result["counts"]["all"] == 1
    assert result["archive"]["selection"]["code"] == "managed-account-archive-selection-required"


@pytest.mark.parametrize("version", [0, 4, 999])
def test_invoice_read_requires_current_schema_without_migrating(invoice_database, tmp_path, capsys, version):
    with sqlite3.connect(invoice_database) as connection:
        connection.execute(f"PRAGMA user_version={version}")
    before = invoice_database.read_bytes()
    with pytest.raises(RuntimeError, match="read-only-storage-schema-mismatch"):
        run_read("list", invoice_database, managed_settings(tmp_path), capsys)
    assert invoice_database.read_bytes() == before


@pytest.mark.parametrize("command", ["status", "list", "review"])
def test_current_version_with_missing_invoice_column_is_not_empty_state(
    command, invoice_database, tmp_path, capsys,
):
    with sqlite3.connect(invoice_database) as connection:
        connection.execute("ALTER TABLE invoices DROP COLUMN supplier")
        connection.execute("DELETE FROM invoices")
    before = invoice_database.read_bytes()
    with pytest.raises(sqlite3.OperationalError, match="supplier"):
        run_read(command, invoice_database, managed_settings(tmp_path), capsys)
    assert invoice_database.read_bytes() == before


def test_read_only_storage_rejects_writes_and_keeps_current_wal_data(invoice_database):
    writer = Storage(invoice_database)
    reader = None
    try:
        writer.connection.execute("UPDATE invoices SET status='duplicate'")
        writer.connection.commit()
        reader = Storage(invoice_database, read_only=True)
        assert reader.list_invoices()[0]["status"] == "duplicate"
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            reader.connection.execute("DELETE FROM invoices")
    finally:
        if reader is not None:
            reader.close()
        writer.close()


def test_read_only_storage_observes_writer_started_after_reader(invoice_database):
    reader = Storage(invoice_database, read_only=True)
    writer = Storage(invoice_database)
    try:
        writer.connection.execute("UPDATE invoices SET status='duplicate'")
        writer.connection.commit()
        assert reader.list_invoices()[0]["status"] == "duplicate"
    finally:
        reader.close()
        writer.close()


def test_corrupt_invoice_database_is_not_an_empty_result(tmp_path, capsys):
    database = tmp_path / "corrupt.sqlite3"
    database.write_bytes(b"not a SQLite database")
    with pytest.raises(sqlite3.DatabaseError):
        run_read("list", database, managed_settings(tmp_path), capsys)


def test_invoice_list_at_limit_cannot_claim_complete_archive(invoice_database, tmp_path, capsys):
    code, result = run_read("list", invoice_database, managed_settings(tmp_path), capsys, limit=1)
    assert code == 0 and result["count"] == 1
    assert result["complete"] is False and result["results_may_be_truncated"] is True
    assert result["remote_state_verified"] is False


def test_enabled_selection_is_not_remote_health_evidence(invoice_database, tmp_path, capsys):
    settings = ToolSettings(path=tmp_path / "unused-tools.toml")
    settings.mail.invoices.enabled = True
    code, result = run_read("status", invoice_database, settings, capsys)
    assert code == 0
    assert result["scope"] == "local-invoice-diagnostics"
    assert result["status"] == "partial" and result["complete"] is False
    assert result["archive"]["selection"]["status"] == "configured"
    assert result["archive"]["resource_access"]["status"] == "unknown"
    assert result["remote_state_verified"] is False


def test_invoice_review_result_is_current_invoice_evidence():
    verdict = guard_claims(
        route=route_intent("Welche Rechnungen brauchen Prüfung?"),
        answer="Ein gespeicherter Datensatz braucht Prüfung.",
        evidence=[{"domain": "invoices", "tool_id": "assistant.invoices.review", "ok": True}],
    )
    assert verdict["ok"]
