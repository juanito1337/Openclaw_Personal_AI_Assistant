from __future__ import annotations

import base64
import re
import tempfile
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from mail_agent.models import Envelope, OperationResult
from personal_assistant.adapters.mail import MailMoveService
from personal_assistant.cli import parser
from personal_assistant.models import Resource
from personal_assistant.policy import PolicyEngine
from personal_assistant.registry import ResourceRegistry
from personal_assistant.storage import AssistantStorage
from personal_assistant.tool_registry import build_tool_registry
from personal_assistant.tool_settings import MailMoveToolSettings, ToolSettings


def _raw_mail(pdf: bytes = b"synthetic-pdf") -> bytes:
    encoded = base64.b64encode(pdf)
    return b"\r\n".join(
        (
            b"From: Sender <sender@example.invalid>",
            b"To: Jan <jan@example.invalid>",
            b"Subject: Unterlagen mit PDF",
            b"Message-ID: <forward-source@example.invalid>",
            b"MIME-Version: 1.0",
            b'Content-Type: multipart/mixed; boundary="forward-boundary"',
            b"",
            b"--forward-boundary",
            b"Content-Type: text/plain; charset=utf-8",
            b"",
            b"Bitte Unterlagen beachten.",
            b"--forward-boundary",
            b"Content-Type: application/pdf",
            b"Content-Disposition: attachment; filename=unterlagen.pdf",
            b"Content-Transfer-Encoding: base64",
            b"",
            encoded,
            b"--forward-boundary--",
            b"",
        )
    )


class FakeForwardClient:
    def __init__(self, raw: bytes) -> None:
        self.raw = raw
        self.templates: list[str] = []
        self.save_copy_values: list[bool | None] = []
        self.archives: list[bytes] = []
        self.config = SimpleNamespace(mailbox=SimpleNamespace(from_header="Jan <jan@example.invalid>"))

    def list_folders(self):
        return ["Agent/Relevant"], ""

    def list_envelopes(self, folder, limit=None):
        assert folder == "Agent/Relevant"
        return [Envelope("42", "Unterlagen mit PDF")], ""

    def export_message(self, folder, message_id, destination):
        assert (folder, message_id) == ("Agent/Relevant", "42")
        destination.write_bytes(self.raw)
        return OperationResult(True, "exported", path=str(destination))

    def send_template(self, template, save_copy=None):
        self.templates.append(template)
        self.save_copy_values.append(save_copy)
        match = re.search(r"filename=([^\s]+) name=original-message\.eml\.zip", template)
        assert match is not None
        self.archives.append(Path(match.group(1)).read_bytes())
        return OperationResult(True, "sent")


class FakeAntivirus:
    def __init__(self, *, blocked_name: str = "") -> None:
        self.blocked_name = blocked_name
        self.calls: list[tuple[str, str, bytes]] = []

    @staticmethod
    def index_readiness():
        return {"index_ready": True, "reasons": []}

    def scan_bytes(self, data, *, name, source_type, use_cache=True):
        self.calls.append((name, source_type, data))
        clean = name != self.blocked_name
        return SimpleNamespace(
            clean=clean,
            status="clean" if clean else "infected",
            scanner_identity="clamd:synthetic",
        )


def _service(root: Path, *, raw: bytes, antivirus: FakeAntivirus | None = None):
    registry = ResourceRegistry(root / "resources.toml")
    registry.resources["mail-agent"] = Resource(
        id="mail-agent",
        kind="tool",
        connector="local",
        permissions=("read", "move", "forward"),
    )
    storage = AssistantStorage(root / "assistant.sqlite3")
    policy = PolicyEngine(root / "policies.toml", registry)
    client = FakeForwardClient(raw)
    scanner = antivirus or FakeAntivirus()
    service = MailMoveService(
        MailMoveToolSettings(enabled=True),
        registry,
        policy,
        storage,
        client,
        scanner,  # type: ignore[arg-type]
    )
    return service, storage, client, scanner


def test_forward_draft_and_approved_send_preserve_complete_original_mail() -> None:
    with tempfile.TemporaryDirectory() as temp:
        raw = _raw_mail()
        service, storage, client, scanner = _service(Path(temp), raw=raw)
        try:
            draft = service.draft_forward(
                "Agent/Relevant",
                "42",
                "Empfang <recipient@example.invalid>",
                "Hallo, anbei die Originalnachricht.",
                expected_subject="Unterlagen mit PDF",
            )
            assert draft["status"] == "proposed"
            assert draft["to"] == "recipient@example.invalid"
            assert draft["subject"] == "Fwd: Unterlagen mit PDF"
            assert draft["attachment"]["name"] == "original-message.eml.zip"
            assert draft["attachment"]["original_attachment_names"] == ["unterlagen.pdf"]
            assert draft["antivirus"]["raw_clean"]
            assert [call[0] for call in scanner.calls] == [
                "original-message.eml",
                "unterlagen.pdf",
            ]
            assert client.templates == []

            with pytest.raises(PermissionError, match="Freigabe"):
                service.send_forward(draft["draft_id"])
            with pytest.raises(PermissionError, match="falschen Typ"):
                service.send_message(draft["draft_id"], approved=True)

            sent = service.send_forward(draft["draft_id"], approved=True)
            assert sent["ok"]
            assert sent["attachment_sha256"] == draft["attachment"]["sha256"]
            assert client.save_copy_values == [False]
            assert "To: recipient@example.invalid" in client.templates[0]
            assert "name=original-message.eml.zip" in client.templates[0]
            with tempfile.TemporaryDirectory() as extracted:
                archive_path = Path(extracted) / "forward.zip"
                archive_path.write_bytes(client.archives[0])
                with zipfile.ZipFile(archive_path) as archive:
                    assert archive.namelist() == ["original-message.eml"]
                    assert archive.read("original-message.eml") == raw
            assert [call[0] for call in scanner.calls] == [
                "original-message.eml",
                "unterlagen.pdf",
                "original-message.eml",
                "unterlagen.pdf",
            ]
        finally:
            storage.close()


def test_forward_send_fails_closed_when_source_changes_after_preview() -> None:
    with tempfile.TemporaryDirectory() as temp:
        service, storage, client, _scanner = _service(Path(temp), raw=_raw_mail())
        try:
            draft = service.draft_forward(
                "Agent/Relevant",
                "42",
                "recipient@example.invalid",
                "Anbei.",
                expected_subject="Unterlagen mit PDF",
            )
            client.raw = _raw_mail(b"changed-pdf")
            with pytest.raises(PermissionError, match="geaendert"):
                service.send_forward(draft["draft_id"], approved=True)
            assert client.templates == []
            assert storage.get_action(draft["draft_id"]).status == "failed"
        finally:
            storage.close()


def test_forward_draft_blocks_infected_physical_attachment() -> None:
    with tempfile.TemporaryDirectory() as temp:
        scanner = FakeAntivirus(blocked_name="unterlagen.pdf")
        service, storage, client, _scanner = _service(Path(temp), raw=_raw_mail(), antivirus=scanner)
        try:
            with pytest.raises(PermissionError, match="Originalanhang"):
                service.draft_forward(
                    "Agent/Relevant",
                    "42",
                    "recipient@example.invalid",
                    "Anbei.",
                    expected_subject="Unterlagen mit PDF",
                )
            assert client.templates == []
            assert storage.list_actions(limit=10) == []
        finally:
            storage.close()


def test_forward_cli_and_typed_catalog_are_exposed() -> None:
    parsed = parser().parse_args(
        [
            "mail",
            "forward-draft",
            "--folder",
            "Agent/Relevant",
            "--message-id",
            "42",
            "--expected-subject",
            "Unterlagen mit PDF",
            "--to",
            "recipient@example.invalid",
            "--body",
            "Anbei.",
        ]
    )
    assert parsed.mail_command == "forward-draft"
    assert parsed.to == "recipient@example.invalid"
    send = parser().parse_args(["mail", "forward-send", "--draft-id", "draft-forward", "--yes"])
    assert send.mail_command == "forward-send"
    assert send.yes

    settings = ToolSettings(path=Path("tools.toml"))
    settings.mail.move.enabled = True
    tools = {tool.id: tool for tool in build_tool_registry(settings)}
    assert tools["mail.forward-draft"].approval == "draft-only-no-send"
    assert tools["mail.forward-send"].approval == "explicit-user-approved-presented-draft"
