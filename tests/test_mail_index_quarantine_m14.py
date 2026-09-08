from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from mail_agent.index_quarantine import (
    blocked_report,
    select_candidate,
    verify_and_move_candidate,
)
from personal_assistant.adapters.mail import MailMoveService
from personal_assistant.cli import parser as assistant_parser
from personal_assistant.models import PolicyDecision
from personal_assistant.storage import AssistantStorage
from personal_assistant.tool_catalog import TOOLS
from personal_assistant.tool_settings import MailMoveToolSettings


def _mail(*, attachment: bytes | None = None) -> bytes:
    if attachment is None:
        return b"From: sender@example.test\r\nSubject: Test\r\n\r\nMALWARE\r\n"
    import base64

    encoded = base64.b64encode(attachment)
    return (
        b"From: sender@example.test\r\n"
        b"Subject: Test\r\n"
        b"Content-Type: multipart/mixed; boundary=m14\r\n\r\n"
        b"--m14\r\nContent-Type: text/plain\r\n\r\nclean\r\n"
        b"--m14\r\nContent-Type: application/octet-stream\r\n"
        b"Content-Disposition: attachment; filename=private.bin\r\n"
        b"Content-Transfer-Encoding: base64\r\n\r\n"
        + encoded
        + b"\r\n--m14--\r\n"
    )


def _checkpoint(path: Path, raw: bytes, *, status: str = "infected") -> Path:
    payload = {
        "schema": 1,
        "run_id": "run-m14",
        "status": "incomplete",
        "complete": False,
        "folders": {
            "folder:routine": {
                "name": "Agent/Routine",
                "blocked": [
                    {
                        "mailbox_id": "4711",
                        "sha256": hashlib.sha256(raw).hexdigest(),
                        "status": status,
                    }
                ],
            }
        },
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


class _Client:
    def __init__(self, raw: bytes) -> None:
        self.raw = raw
        self.moves: list[tuple[str, str, str]] = []

    def list_folders(self):
        return ["Agent/Routine", "Agent/Virusverdacht"], ""

    def export_message(self, folder: str, message_id: str, destination: Path):
        assert (folder, message_id) == ("Agent/Routine", "4711")
        destination.write_bytes(self.raw)
        return SimpleNamespace(ok=True, detail="")

    def move_message(self, source: str, destination: str, message_id: str):
        self.moves.append((source, destination, message_id))
        return SimpleNamespace(ok=True, detail="moved")


class _Scanner:
    def __init__(self, *, infected: bytes = b"MALWARE", error: bool = False) -> None:
        self.infected = infected
        self.error = error
        self.calls: list[tuple[str, bytes, bool]] = []
        self.closed = False

    def scanner_identity(self, *, refresh: bool = False) -> str:
        return "clamd:test-m14"

    def index_readiness(self):
        return {"ok": True, "index_ready": True, "transport": "test-double"}

    def scan_bytes(self, data: bytes, *, name: str, source_type: str, use_cache: bool = True):
        del name
        self.calls.append((source_type, data, use_cache))
        status = "error" if self.error else "infected" if self.infected in data else "clean"
        return SimpleNamespace(status=status, clean=status == "clean")

    def close(self) -> None:
        self.closed = True


def test_blocked_report_is_content_free_and_binds_exact_approval(tmp_path: Path) -> None:
    raw = _mail()
    checkpoint = _checkpoint(tmp_path / "checkpoint.json", raw)

    report = blocked_report(
        checkpoint,
        malware_folder="Agent/Virusverdacht",
        limit=10,
    )

    assert report["ok"] is True
    assert report["content_exposed"] is False
    assert report["infected_count"] == 1
    candidate = report["candidates"][0]
    assert candidate["source"] == "Agent/Routine"
    assert candidate["destination"] == "Agent/Virusverdacht"
    assert candidate["approval"] == "explicit-user-single-infected-mail-quarantine"
    assert "--expected-sha256" in candidate["approval_command"]
    assert "MALWARE" not in json.dumps(report)


def test_quarantine_rejects_changed_expectation_and_noninfected_checkpoint(
    tmp_path: Path,
) -> None:
    raw = _mail()
    checkpoint = _checkpoint(tmp_path / "checkpoint.json", raw)
    row = blocked_report(checkpoint, malware_folder="Agent/Virusverdacht")["candidates"][0]
    with pytest.raises(PermissionError, match="Erwartungswerte"):
        select_candidate(
            checkpoint,
            candidate_id=row["candidate_id"],
            expected_source=row["source"],
            expected_message_id=row["message_id"],
            expected_sha256="0" * 64,
        )

    error_checkpoint = _checkpoint(tmp_path / "error.json", raw, status="error")
    error_row = blocked_report(error_checkpoint, malware_folder="Agent/Virusverdacht")[
        "candidates"
    ][0]
    with pytest.raises(PermissionError, match="Nur bestaetigte"):
        select_candidate(
            error_checkpoint,
            candidate_id=error_row["candidate_id"],
            expected_source=error_row["source"],
            expected_message_id=error_row["message_id"],
            expected_sha256=error_row["raw_sha256"],
        )


def test_fresh_raw_and_attachment_scan_precedes_exact_single_move(tmp_path: Path) -> None:
    raw = _mail(attachment=b"MALWARE-ATTACHMENT")
    checkpoint = _checkpoint(tmp_path / "checkpoint.json", raw)
    row = blocked_report(checkpoint, malware_folder="Agent/Virusverdacht")["candidates"][0]
    candidate = select_candidate(
        checkpoint,
        candidate_id=row["candidate_id"],
        expected_source=row["source"],
        expected_message_id=row["message_id"],
        expected_sha256=row["raw_sha256"],
    )
    client = _Client(raw)
    scanner = _Scanner()

    result = verify_and_move_candidate(
        candidate,
        destination="Agent/Virusverdacht",
        client=client,
        scanner=scanner,
    )

    assert result["ok"] is True
    assert result["content_exposed"] is False
    assert result["scan_objects"] == 2
    assert all(use_cache is False for _source, _data, use_cache in scanner.calls)
    assert client.moves == [("Agent/Routine", "Agent/Virusverdacht", "4711")]


@pytest.mark.parametrize("mode", ["changed", "clean", "error"])
def test_failed_reverification_never_moves(tmp_path: Path, mode: str) -> None:
    raw = _mail()
    checkpoint = _checkpoint(tmp_path / "checkpoint.json", raw)
    row = blocked_report(checkpoint, malware_folder="Agent/Virusverdacht")["candidates"][0]
    candidate = select_candidate(
        checkpoint,
        candidate_id=row["candidate_id"],
        expected_source=row["source"],
        expected_message_id=row["message_id"],
        expected_sha256=row["raw_sha256"],
    )
    client = _Client(b"changed" if mode == "changed" else raw)
    scanner = _Scanner(infected=b"NEVER", error=mode == "error")

    with pytest.raises((PermissionError, RuntimeError)):
        verify_and_move_candidate(
            candidate,
            destination="Agent/Virusverdacht",
            client=client,
            scanner=scanner,
        )

    assert client.moves == []


def test_scanner_identity_failure_precedes_external_move(tmp_path: Path) -> None:
    raw = _mail()
    checkpoint = _checkpoint(tmp_path / "checkpoint.json", raw)
    row = blocked_report(checkpoint, malware_folder="Agent/Virusverdacht")["candidates"][0]
    candidate = select_candidate(
        checkpoint,
        candidate_id=row["candidate_id"],
        expected_source=row["source"],
        expected_message_id=row["message_id"],
        expected_sha256=row["raw_sha256"],
    )
    client = _Client(raw)
    scanner = _Scanner()

    def fail_identity(*, refresh: bool = False) -> str:
        del refresh
        raise RuntimeError("scanner identity unavailable")

    scanner.scanner_identity = fail_identity  # type: ignore[method-assign]

    with pytest.raises(RuntimeError, match="identity unavailable"):
        verify_and_move_candidate(
            candidate,
            destination="Agent/Virusverdacht",
            client=client,
            scanner=scanner,
        )

    assert scanner.calls == []
    assert client.moves == []


def test_service_requires_approval_and_audits_completed_action(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw = _mail()
    checkpoint = _checkpoint(tmp_path / "checkpoint.json", raw)
    row = blocked_report(checkpoint, malware_folder="Agent/Virusverdacht")["candidates"][0]
    client = _Client(raw)
    scanner = _Scanner()
    config = SimpleNamespace(
        runtime=SimpleNamespace(database=tmp_path / "mail.sqlite3", lock_file=tmp_path / "mail.lock"),
        folders=SimpleNamespace(malware="Agent/Virusverdacht"),
    )
    monkeypatch.setattr(
        MailMoveService,
        "_mail_index_checkpoint",
        staticmethod(lambda: (config, checkpoint)),
    )
    monkeypatch.setattr("personal_assistant.adapters.mail.HostAntivirus", lambda _settings: scanner)
    monkeypatch.setattr(
        "personal_assistant.adapters.mail.load_tool_settings",
        lambda: SimpleNamespace(
            security=SimpleNamespace(
                antivirus=SimpleNamespace(
                    enabled=True,
                    fail_closed=True,
                    scan_raw_mail=True,
                    scan_attachments=True,
                )
            )
        ),
    )
    storage = AssistantStorage(tmp_path / "assistant.sqlite3", enable_knowledge=False)
    policy = SimpleNamespace(
        decide=lambda _resource, _action, _payload: PolicyDecision(True, True, "allowed")
    )
    service = MailMoveService(
        MailMoveToolSettings(enabled=True),
        SimpleNamespace(),
        policy,
        storage,
        client=client,
    )
    kwargs = {
        "candidate_id": row["candidate_id"],
        "expected_source": row["source"],
        "expected_message_id": row["message_id"],
        "expected_sha256": row["raw_sha256"],
    }
    try:
        with pytest.raises(PermissionError, match="--yes"):
            service.quarantine_index_candidate(**kwargs, approved=False)
        result = service.quarantine_index_candidate(**kwargs, approved=True)
        duplicate = service.quarantine_index_candidate(**kwargs, approved=True)
        assert result["ok"] is True
        assert result["duplicate"] is False
        assert duplicate["duplicate"] is True
        assert client.moves == [("Agent/Routine", "Agent/Virusverdacht", "4711")]
        assert storage.get_action(result["action_id"]).status == "completed"
    finally:
        storage.close()
    assert scanner.closed is True


def test_cli_and_typed_catalog_publish_quarantine_contract() -> None:
    args = assistant_parser().parse_args(
        [
            "mail",
            "index",
            "quarantine",
            "--candidate-id",
            "candidate",
            "--expected-source",
            "Agent/Routine",
            "--expected-message-id",
            "4711",
            "--expected-sha256",
            "0" * 64,
            "--yes",
        ]
    )
    assert args.index_command == "quarantine"
    assert args.yes is True
    catalog = {tool.id: tool for tool in TOOLS}
    assert catalog["mail.index.blocked"].mode == "read"
    assert catalog["mail.index.quarantine"].mode == "write"
    assert catalog["mail.index.quarantine"].writes_external_data is True
    assert (
        catalog["mail.index.quarantine"].approval
        == "explicit-user-single-infected-mail-quarantine"
    )
    assert catalog["mail.index.rebuild"].mode == "local-write"
