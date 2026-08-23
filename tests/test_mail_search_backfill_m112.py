from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import pytest

from mail_agent.models import Envelope
from mail_agent.search_backfill import (
    BackfillBackendError,
    BackfillEnvelope,
    BackfillFolder,
    BackfillLimits,
    BackfillPage,
    ConnectorCapabilities,
    HimalayaBackfillBackend,
    MailSearchBackfill,
)
from personal_assistant.cli import parser as assistant_parser
from personal_assistant.cli_handlers.mail import run_external
from personal_assistant.contracts.mail_projection import load_search_projection
from personal_assistant.tool_catalog import TOOLS


def _mail(
    uid: int,
    *,
    subject: str = "Nachricht",
    body: str = "Belegter Inhalt",
    message_id: str | None = None,
    attachment: tuple[str, bytes] | None = None,
) -> bytes:
    lines = [
        "From: Sender <sender@example.test>",
        "To: Jan <jan@example.test>",
        f"Subject: {subject}",
        "Date: Tue, 18 Aug 2026 10:00:00 +0000",
    ]
    if message_id is not None:
        lines.append(f"Message-ID: <{message_id}>")
    if attachment is None:
        return ("\r\n".join([*lines, "", body]) + "\r\n").encode("utf-8")
    name, payload = attachment
    import base64

    encoded = base64.b64encode(payload).decode("ascii")
    return (
        "\r\n".join(
            [
                *lines,
                'Content-Type: multipart/mixed; boundary="m112"',
                "",
                "--m112",
                "Content-Type: text/plain; charset=utf-8",
                "",
                body,
                "--m112",
                "Content-Type: application/octet-stream",
                f'Content-Disposition: attachment; filename="{name}"',
                "Content-Transfer-Encoding: base64",
                "",
                encoded,
                "--m112--",
                "",
            ]
        )
    ).encode("utf-8")


@dataclass
class _Scan:
    status: str

    @property
    def clean(self) -> bool:
        return self.status == "clean"


class FakeScanner:
    def __init__(self, identity: str = "clamav:test-a", blocked: bytes = b"") -> None:
        self.identity = identity
        self.blocked = blocked
        self.calls: list[tuple[str, bytes]] = []

    def scanner_identity(self, *, refresh: bool = False) -> str:
        return self.identity

    def scan_bytes(
        self,
        data: bytes,
        *,
        name: str,
        source_type: str,
        use_cache: bool = True,
    ) -> _Scan:
        self.calls.append((source_type, data))
        if b"SCANNER-ERROR" in data:
            return _Scan("error")
        if self.blocked and self.blocked in data:
            return _Scan("infected")
        return _Scan("clean")


class FakeImap:
    def __init__(
        self,
        folders: dict[str, list[bytes]],
        *,
        capabilities: ConnectorCapabilities | None = None,
        stable_ids: dict[str, str] | None = None,
        uidvalidities: dict[str, str] | None = None,
    ) -> None:
        self.folders = folders
        self._caps = capabilities or ConnectorCapabilities(
            paging=True,
            raw_fetch=True,
            uid=True,
            uidvalidity=True,
            uidnext=True,
            modseq=True,
            condstore=True,
            qresync=True,
            idle=True,
            folder_stable_id=True,
            cursor_contract="fake-uid-page",
        )
        self.stable_ids = stable_ids or {name: f"folder:{index + 1}" for index, name in enumerate(folders)}
        self.uidvalidities = uidvalidities or {name: f"uv-{index + 1}" for index, name in enumerate(folders)}
        self.page_calls: list[tuple[str, int, int]] = []
        self.raw_calls: list[tuple[str, str]] = []
        self.failures: dict[tuple[str, int], str] = {}
        self.provider_writes = 0

    def capabilities(self) -> ConnectorCapabilities:
        return self._caps

    def inventory(self) -> list[BackfillFolder]:
        return [
            BackfillFolder(
                self.stable_ids[name],
                name,
                self.uidvalidities.get(name, "") if self._caps.uidvalidity else "",
            )
            for name in sorted(self.folders)
        ]

    def fetch_page(self, folder: BackfillFolder, *, page: int, page_size: int) -> BackfillPage:
        self.page_calls.append((folder.name, page, page_size))
        failure = self.failures.get((folder.name, page))
        if failure:
            raise BackfillBackendError(failure, failure)
        rows = self.folders[folder.name]
        start = (page - 1) * page_size
        selected = rows[start : start + page_size]
        items = tuple(
            BackfillEnvelope(
                mailbox_id=str(start + index + 1),
                uid=str(start + index + 1) if self._caps.uid else "",
                subject=f"Envelope {start + index + 1}",
                date="2026-08-18T10:00:00+00:00",
            )
            for index, _raw in enumerate(selected)
        )
        return BackfillPage(items, start + len(selected) < len(rows))

    def fetch_raw(self, folder: BackfillFolder, envelope: BackfillEnvelope) -> bytes:
        self.raw_calls.append((folder.name, envelope.mailbox_id))
        return self.folders[folder.name][int(envelope.mailbox_id) - 1]


def _crawler(
    root: Path,
    backend: FakeImap,
    scanner: FakeScanner | None = None,
    *,
    limits: BackfillLimits | None = None,
    after_partition=None,
    include_folders: tuple[str, ...] = (),
) -> MailSearchBackfill:
    return MailSearchBackfill(
        backend,
        scanner or FakeScanner(),
        projection_root=root / "projection",
        checkpoint_path=root / "checkpoint.json",
        quarantine_folders=("Spamverdacht",),
        limits=limits or BackfillLimits(page_size=2, request_interval_seconds=0),
        after_partition=after_partition,
        include_folders=include_folders,
    )


def test_plan_is_read_only_and_reports_capabilities_and_quarantine(tmp_path: Path) -> None:
    backend = FakeImap({"INBOX": [], "Spamverdacht": []})
    crawler = _crawler(tmp_path, backend)

    plan = crawler.plan()

    assert plan["writes_imap"] is False
    assert plan["writes_local_index"] is False
    assert plan["capabilities"]["qresync"] is True
    assert plan["execution_policy"]["idle_used"] is False
    assert any(item["quarantine_untrusted"] for item in plan["folders"])
    assert not (tmp_path / "checkpoint.json").exists()
    assert not (tmp_path / "projection").exists()
    assert backend.provider_writes == 0


def test_multi_folder_paging_unicode_empty_and_duplicate_ids(tmp_path: Path) -> None:
    duplicate = "same@example.test"
    backend = FakeImap(
        {
            "Archive/2026": [
                _mail(1, subject="Gruesse \u2713", message_id=duplicate),
                _mail(2, body="zweite", message_id=duplicate),
                _mail(3, body="ohne id", message_id=None),
            ],
            "Empty": [],
            "INBOX": [_mail(4, attachment=("beleg.bin", b"physical attachment"))],
        }
    )

    result = _crawler(tmp_path, backend).run(approved=True)

    assert result["ok"] is True
    assert result["complete"] is True
    assert result["metrics"]["messages"] == 4
    projection = load_search_projection(tmp_path / "projection")
    assert len(projection.records) == 4
    assert len({record[1]["stable_key"] for record in projection.records}) == 4
    assert {call[0] for call in backend.page_calls} == {"Archive/2026", "Empty", "INBOX"}
    assert backend.provider_writes == 0


@pytest.mark.parametrize("crash_page", [1, 2, 3])
def test_crash_resume_at_every_page_boundary_has_no_duplicates(tmp_path: Path, crash_page: int) -> None:
    backend = FakeImap({"INBOX": [_mail(i, message_id=f"{i}@test") for i in range(1, 6)]})
    crashed = False

    def crash(_folder: str, page: int) -> None:
        nonlocal crashed
        if page == crash_page and not crashed:
            crashed = True
            raise RuntimeError("simulated-crash")

    with pytest.raises(RuntimeError, match="simulated-crash"):
        _crawler(tmp_path, backend, after_partition=crash).run(approved=True)
    safe = json.loads((tmp_path / "checkpoint.json").read_text(encoding="utf-8"))
    assert safe["folders"]["folder:1"]["next_page"] == crash_page

    result = _crawler(tmp_path, backend).run(approved=True)

    assert result["ok"] is True
    assert result["resumed"] is True
    projection = load_search_projection(tmp_path / "projection")
    assert len(projection.records) == 5
    occurrence_ids = [
        occurrence
        for _path, record in projection.records
        for occurrence in record["metadata"]["occurrence_ids"]
    ]
    assert len(occurrence_ids) == len(set(occurrence_ids)) == 5


@pytest.mark.parametrize("failure", ["timeout", "rate-limit", "folder-page-error", "idle-abort"])
def test_backend_failure_keeps_safe_checkpoint_incomplete(tmp_path: Path, failure: str) -> None:
    backend = FakeImap({"INBOX": [_mail(1), _mail(2), _mail(3)]})
    backend.failures[("INBOX", 2)] = failure

    result = _crawler(tmp_path, backend).run(approved=True)

    assert result["complete"] is False
    assert result["stop_reason"] == failure
    checkpoint = json.loads((tmp_path / "checkpoint.json").read_text(encoding="utf-8"))
    assert checkpoint["folders"]["folder:1"]["next_page"] == 2
    assert checkpoint["folders"]["folder:1"]["messages"] == 2


def test_missing_uidvalidity_uses_bound_fallback_but_never_claims_complete(
    tmp_path: Path,
) -> None:
    caps = ConnectorCapabilities(
        paging=True,
        raw_fetch=True,
        cursor_contract="bounded-page-number-fallback",
    )
    backend = FakeImap({"INBOX": [_mail(1)]}, capabilities=caps)

    plan = _crawler(tmp_path, backend).plan()
    result = _crawler(tmp_path, backend).run(approved=True)

    assert "UIDVALIDITY fehlt" in plan["capability_issues"][0]
    assert result["complete"] is False
    projection = json.loads((tmp_path / "projection" / "_projection.json").read_text(encoding="utf-8"))
    assert projection["complete"] is False
    assert projection["coverage"]["authoritative"] is False


def test_folder_rename_and_uidvalidity_reset_change_fingerprint(tmp_path: Path) -> None:
    backend = FakeImap({"Old": [_mail(1)]}, stable_ids={"Old": "folder:stable"}, uidvalidities={"Old": "10"})
    assert _crawler(tmp_path, backend).run(approved=True)["complete"] is True
    backend.folders = {"New": backend.folders.pop("Old")}
    backend.stable_ids = {"New": "folder:stable"}
    backend.uidvalidities = {"New": "10"}
    plan = _crawler(tmp_path, backend).plan()
    assert plan["folder_changes"]["renamed"][0]["from"] == "Old"
    assert plan["folder_changes"]["renamed"][0]["to"] == "New"
    backend.uidvalidities = {"New": "11"}
    assert _crawler(tmp_path, backend).run(approved=True)["resumed"] is False


def test_antivirus_blocks_raw_and_attachment_without_body_projection(tmp_path: Path) -> None:
    backend = FakeImap(
        {
            "INBOX": [
                _mail(1, body="RAW-MALWARE secret", message_id="raw@test"),
                _mail(
                    2,
                    body="BODY-MUST-NOT-BE-PUBLISHED",
                    message_id="attachment@test",
                    attachment=("bad.bin", b"ATTACHMENT-MALWARE"),
                ),
            ]
        }
    )
    scanner = FakeScanner(blocked=b"MALWARE")

    result = _crawler(tmp_path, backend, scanner).run(approved=True)

    assert result["complete"] is False
    assert result["blocked_count"] == 2
    published = b"".join(path.read_bytes() for path in (tmp_path / "projection").glob("*.json"))
    assert b"RAW-MALWARE" not in published
    assert b"BODY-MUST-NOT-BE-PUBLISHED" not in published
    checkpoint = (tmp_path / "checkpoint.json").read_text(encoding="utf-8")
    assert "RAW-MALWARE" not in checkpoint
    assert "ATTACHMENT-MALWARE" not in checkpoint


def test_scanner_identity_change_restarts_and_rescans(tmp_path: Path) -> None:
    caps = ConnectorCapabilities(paging=True, raw_fetch=True)
    backend = FakeImap({"INBOX": [_mail(1)]}, capabilities=caps)
    first = FakeScanner("clamav:identity-a")
    assert _crawler(tmp_path, backend, first).run(approved=True)["complete"] is False
    second = FakeScanner("clamav:identity-b")

    result = _crawler(tmp_path, backend, second).run(approved=True)

    assert result["resumed"] is False
    assert any(source.endswith("raw") for source, _data in second.calls)


def test_scanner_error_and_oversized_mail_are_content_free(tmp_path: Path) -> None:
    backend = FakeImap(
        {
            "INBOX": [
                _mail(1, body="SCANNER-ERROR PRIVATE-BODY"),
                _mail(2, body="HUGE-PRIVATE-BODY" + "x" * 2000),
            ]
        }
    )
    limits = BackfillLimits(
        page_size=10,
        max_pages=5,
        max_messages=10,
        max_bytes=100_000,
        max_message_bytes=500,
        max_runtime_seconds=60,
        request_interval_seconds=0,
    )

    result = _crawler(tmp_path, backend, FakeScanner(), limits=limits).run(approved=True)

    assert result["complete"] is False
    assert result["blocked_count"] == 2
    checkpoint = json.loads((tmp_path / "checkpoint.json").read_text(encoding="utf-8"))
    statuses = {item["status"] for item in checkpoint["folders"]["folder:1"]["blocked"]}
    assert statuses == {"error", "too-large"}
    published = b"".join(path.read_bytes() for path in (tmp_path / "projection").glob("*.json"))
    assert b"PRIVATE-BODY" not in published


def test_large_message_and_synthetic_load_remain_page_bounded(tmp_path: Path) -> None:
    messages = [_mail(i, body="x" * 200, message_id=f"{i}@test") for i in range(1, 102)]
    backend = FakeImap({"INBOX": messages})
    limits = BackfillLimits(
        page_size=7,
        max_pages=20,
        max_messages=200,
        max_bytes=1_000_000,
        max_message_bytes=10_000,
        max_runtime_seconds=60,
        request_interval_seconds=0,
    )

    result = _crawler(tmp_path, backend, limits=limits).run(approved=True)

    assert result["complete"] is True
    assert result["metrics"]["peak_page_messages"] <= 7
    assert result["metrics"]["peak_page_raw_bytes"] <= 7 * 1000
    assert len(backend.page_calls) == 15
    assert result["metrics"]["backend_calls"] == 1 + 15 + 101


def test_backfill_requires_explicit_approval_and_catalog_contract(tmp_path: Path) -> None:
    crawler = _crawler(tmp_path, FakeImap({"INBOX": []}))
    with pytest.raises(PermissionError, match="--yes"):
        crawler.run(approved=False)
    assert not (tmp_path / "projection").exists()
    catalog = {tool.id: tool for tool in TOOLS}
    assert catalog["mail.index.plan"].mode == "read"
    assert catalog["mail.index.backfill"].mode == "local-write"
    assert catalog["mail.index.backfill"].approval == "explicit-user-local-mail-index-backfill"
    assert catalog["mail.index.backfill"].writes_external_data is False
    assert catalog["mail.index.canary"].approval == "explicit-user-local-mail-index-canary"


def test_canary_indexes_only_exact_selected_folder_and_rejects_missing_folder(
    tmp_path: Path,
) -> None:
    backend = FakeImap(
        {"INBOX": [_mail(1, message_id="inbox@test")], "Archive": [_mail(2, message_id="archive@test")]}
    )
    plan = _crawler(tmp_path, backend, include_folders=("Archive",)).plan()
    assert plan["folder_count"] == 1
    assert [item["name"] for item in plan["folders"]] == ["Archive"]
    assert plan["execution_policy"]["initial_scan"] == "bounded-folder-canary"

    result = _crawler(tmp_path, backend, include_folders=("Archive",)).run(approved=True)
    assert result["complete"] is True
    assert {folder for folder, _page, _size in backend.page_calls} == {"Archive"}

    with pytest.raises(BackfillBackendError) as raised:
        _crawler(tmp_path / "missing", backend, include_folders=("Nicht da",)).plan()
    assert raised.value.kind == "canary-folder-missing"


def test_stable_assistant_cli_forwards_bounded_backfill(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_run(command, *, check, env):
        captured["command"] = command
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr("personal_assistant.cli_handlers.mail.subprocess.run", fake_run)
    args = assistant_parser().parse_args(
        [
            "mail",
            "index",
            "backfill",
            "--page-size",
            "7",
            "--max-pages",
            "9",
            "--max-messages",
            "11",
            "--max-bytes",
            "13000",
            "--max-message-bytes",
            "12000",
            "--max-runtime",
            "17",
            "--request-interval",
            "0.5",
            "--yes",
        ]
    )

    assert run_external(args) == 0
    command = captured["command"]
    assert isinstance(command, list)
    assert command[3:5] == ["index", "backfill"]
    assert "--yes" in command
    assert command[command.index("--page-size") + 1] == "7"


def test_connector_page_cap_and_per_invocation_resume_budget(tmp_path: Path) -> None:
    class CappedClient:
        config = SimpleNamespace(mailbox=SimpleNamespace(page_size=2))

        def list_envelopes_page(self, folder: str, *, page: int, page_size: int):
            assert folder == "INBOX"
            assert page == 1
            assert page_size == 2
            return [Envelope("1"), Envelope("2")], ""

    adapter = HimalayaBackfillBackend(CappedClient())  # type: ignore[arg-type]
    page = adapter.fetch_page(BackfillFolder("folder:cap", "INBOX"), page=1, page_size=50)
    assert page.has_more is True

    backend = FakeImap({"INBOX": [_mail(i, message_id=f"{i}@budget") for i in range(1, 6)]})
    limits = BackfillLimits(
        page_size=2,
        max_pages=1,
        max_messages=10,
        max_bytes=100_000,
        max_message_bytes=10_000,
        max_runtime_seconds=60,
        request_interval_seconds=0,
    )
    first = _crawler(tmp_path, backend, limits=limits).run(approved=True)
    second = _crawler(tmp_path, backend, limits=limits).run(approved=True)
    third = _crawler(tmp_path, backend, limits=limits).run(approved=True)

    assert first["stop_reason"] == second["stop_reason"] == "page-limit"
    assert first["invocation"]["pages"] == second["invocation"]["pages"] == 1
    assert third["complete"] is True
    assert third["resumed"] is True
    assert third["metrics"]["pages"] == 3
