from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest

from mail_agent.models import Envelope
from mail_agent.parser import parse_eml
from mail_agent.search_backfill import (
    BackfillBackendError,
    BackfillEnvelope,
    BackfillFolder,
    ConnectorCapabilities,
)
from mail_agent.search_projection_v2 import (
    PartitionedSearchSnapshotWriter,
    ProjectionOccurrenceInput,
)
from mail_agent.search_reconcile import (
    FolderReconcileScan,
    MailSearchReconciler,
    ReconcileLimits,
    ReconcileObservation,
)
from personal_assistant.cli import parser as assistant_parser
from personal_assistant.cli_handlers.mail import run_external
from personal_assistant.config import AssistantConfig
from personal_assistant.contracts.mail_projection import load_search_projection
from personal_assistant.contracts.mail_projection_v2 import MailLocator, locator_identity
from personal_assistant.job_control import default_job_specs
from personal_assistant.knowledge import KnowledgeIndexer
from personal_assistant.storage import AssistantStorage
from personal_assistant.tool_catalog import TOOLS
from personal_assistant.work_scheduler import AdaptiveWorkScheduler

STAMP = "2026-08-20T08:00:00+00:00"


def mail(body: str, *, message_id: str = "one@example.test", subject: str = "Test") -> bytes:
    return (
        "From: Sender <sender@example.test>\r\n"
        "To: Jan <jan@example.test>\r\n"
        f"Subject: {subject}\r\n"
        f"Message-ID: <{message_id}>\r\n"
        "Date: Thu, 20 Aug 2026 08:00:00 +0000\r\n"
        "\r\n"
        f"{body}\r\n"
    ).encode()


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


@dataclass
class ScanResult:
    status: str = "clean"

    @property
    def clean(self) -> bool:
        return self.status == "clean"


class Scanner:
    def __init__(self, identity: str = "clamav:m113-a") -> None:
        self.identity = identity
        self.calls: list[tuple[str, bytes]] = []
        self.block = b""
        self.fail = False

    def scanner_identity(self, *, refresh: bool = False) -> str:
        return self.identity

    def scan_bytes(self, data: bytes, *, name: str, source_type: str, use_cache: bool = True):
        del name, use_cache
        if self.fail:
            raise RuntimeError("PRIVATE scanner diagnostic")
        self.calls.append((source_type, data))
        return ScanResult("infected" if self.block and self.block in data else "clean")


class DeltaBackend:
    def __init__(self, folders: dict[str, list[ReconcileObservation]], raw: dict[tuple[str, str], bytes]):
        self.folders = folders
        self.raw = raw
        self.folder_ids = {name: f"folder:{name.casefold().replace('/', '-')}" for name in folders}
        self.uidvalidity = {name: "10" for name in folders}
        self.scan_calls: list[tuple[str, str, int]] = []
        self.raw_calls: list[tuple[str, str]] = []
        self.fail: dict[str, str] = {}
        self.partial: set[str] = set()
        self.provider_writes = 0
        self.cursor = 1

    def capabilities(self) -> ConnectorCapabilities:
        return ConnectorCapabilities(
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
            cursor_contract="fake-qresync+full-reconcile",
        )

    def inventory(self) -> list[BackfillFolder]:
        return [
            BackfillFolder(self.folder_ids[name], name, self.uidvalidity[name])
            for name in sorted(self.folders)
        ]

    def scan_folder(self, folder: BackfillFolder, *, previous_cursor: str, max_messages: int):
        self.scan_calls.append((folder.name, previous_cursor, max_messages))
        if folder.name in self.fail:
            raise BackfillBackendError(self.fail[folder.name], self.fail[folder.name])
        rows = tuple(self.folders[folder.name])
        self.cursor += 1
        return FolderReconcileScan(
            rows,
            f"modseq:{self.cursor}",
            folder.name not in self.partial,
            folder.name not in self.partial,
            "partial" if folder.name in self.partial else "",
        )

    def fetch_raw(self, folder: BackfillFolder, envelope: BackfillEnvelope) -> bytes:
        self.raw_calls.append((folder.name, envelope.uid))
        return self.raw[(folder.name, envelope.uid)]


def locator(folder_id: str, folder: str, uid: str, *, quarantine: bool = False) -> MailLocator:
    return MailLocator(
        resource_id="mail-agent",
        folder_id=folder_id,
        folder_name=folder,
        mailbox_id=uid,
        uidvalidity="10",
        uid=uid,
        observed_at=STAMP,
        quarantine=quarantine,
    )


def parsed(raw: bytes, folder: str, uid: str):
    return parse_eml(raw, Envelope(uid, subject="Test", date=STAMP), folder)


def seed(root: Path, folders: dict[str, list[tuple[str, bytes]]]) -> dict[str, str]:
    writer = PartitionedSearchSnapshotWriter(root)
    partitions = []
    locator_ids: dict[str, str] = {}
    for folder in sorted(folders):
        folder_id = f"folder:{folder.casefold().replace('/', '-')}"
        occurrences = []
        for uid, raw in folders[folder]:
            current = locator(folder_id, folder, uid, quarantine=folder == "Spamverdacht")
            locator_ids[f"{folder}:{uid}"] = locator_identity(current)
            occurrences.append(ProjectionOccurrenceInput(parsed(raw, folder, uid), (current,)))
        partitions.append(
            writer.publish_partition(
                partition_id=f"seed:{folder_id.split(':', 1)[1]}",
                folder_id=folder_id,
                folder_name=folder,
                occurrences=occurrences,
                generated_at=STAMP,
                complete=True,
                authoritative=True,
            )
        )
    writer.publish_root(
        partitions,
        expected_partition_ids=[str(item["partition_id"]) for item in partitions],
        complete=True,
        authoritative=True,
        generated_at=STAMP,
    )
    return locator_ids


def reconciler(root: Path, backend: DeltaBackend, scanner: Scanner | None = None, **kwargs):
    return MailSearchReconciler(
        backend,
        scanner or Scanner(),
        projection_root=root / "projection",
        state_path=root / "state.json",
        quarantine_folders=("Spamverdacht",),
        limits=ReconcileLimits(
            max_folders=20,
            max_messages=100,
            max_bytes=1_000_000,
            max_message_bytes=100_000,
            max_runtime_seconds=60,
            request_interval_seconds=0,
            retention_generations=2,
        ),
        **kwargs,
    )


def observation(uid: str, raw: bytes, *, move_from: str = "", verified: bool = True):
    return ReconcileObservation(
        mailbox_id=uid,
        uid=uid,
        subject="Test",
        date=STAMP,
        raw_sha256=digest(raw) if verified else "",
        raw_sha256_verified=verified,
        move_from_locator_id=move_from,
    )


def test_noop_advances_only_cursor_without_body_fts_or_model_work(tmp_path: Path) -> None:
    raw = mail("unchanged")
    seed(tmp_path / "projection", {"INBOX": [("1", raw)]})
    backend = DeltaBackend({"INBOX": [observation("1", raw)]}, {("INBOX", "1"): raw})
    before = (tmp_path / "projection" / "_projection.json").read_bytes()

    result = reconciler(tmp_path, backend).run(approved=True)

    assert result["ok"] is True and result["no_op"] is True
    assert result["published"] is False
    assert backend.raw_calls == []
    assert result["metrics"]["parser_calls"] == 0
    assert result["metrics"]["fts_rows_changed"] == 0
    assert result["metrics"]["model_calls"] == 0
    assert (tmp_path / "projection" / "_projection.json").read_bytes() == before
    assert json.loads((tmp_path / "state.json").read_text())["folder_cursors"]


def test_verified_move_to_quarantine_reuses_content_and_occurrence_without_raw(tmp_path: Path) -> None:
    raw = mail("same content")
    ids = seed(tmp_path / "projection", {"INBOX": [("1", raw)], "Spamverdacht": []})
    backend = DeltaBackend(
        {"INBOX": [], "Spamverdacht": [observation("9", raw, move_from=ids["INBOX:1"])]},
        {("Spamverdacht", "9"): raw},
    )

    result = reconciler(tmp_path, backend).run(approved=True)
    projection = load_search_projection(tmp_path / "projection")
    metadata = projection.records[0][1]["metadata"]

    assert result["metrics"]["moved"] == 1
    assert backend.raw_calls == []
    assert result["metrics"]["clamav_calls"] == 0
    assert result["metrics"]["parser_calls"] == 0
    assert len(metadata["occurrence_ids"]) == 1
    assert metadata["source_status"] == "quarantine-untrusted"
    assert metadata["locators"][0]["folder_name"] == "Spamverdacht"
    assert metadata["historical_locators"][0]["folder_name"] == "INBOX"
    assert any(item.get("reason") == "moved" for item in projection.tombstones)
    assert backend.provider_writes == 0


def test_new_mail_is_scanned_and_parsed_once(tmp_path: Path) -> None:
    seed(tmp_path / "projection", {"INBOX": []})
    raw = mail("brand new")
    backend = DeltaBackend(
        {"INBOX": [observation("5", raw, verified=False)]},
        {("INBOX", "5"): raw},
    )

    result = reconciler(tmp_path, backend).run(approved=True)

    assert result["metrics"]["new"] == 1
    assert result["metrics"]["body_fetches"] == 1
    assert result["metrics"]["parser_calls"] == 1
    assert result["metrics"]["clamav_calls"] == 1
    assert result["metrics"]["ocr_calls"] == 0
    assert result["metrics"]["model_calls"] == 0


def test_missing_authoritative_connector_capability_preserves_root(tmp_path: Path) -> None:
    raw = mail("unchanged")
    seed(tmp_path / "projection", {"INBOX": [("1", raw)]})
    backend = DeltaBackend({"INBOX": [observation("1", raw)]}, {("INBOX", "1"): raw})
    backend.capabilities = lambda: ConnectorCapabilities(  # type: ignore[method-assign]
        paging=True,
        raw_fetch=True,
        cursor_contract="bounded-non-authoritative-fallback",
    )
    before = (tmp_path / "projection" / "_projection.json").read_bytes()

    result = reconciler(tmp_path, backend).run(approved=True)

    assert result["ok"] is False
    assert result["error"]["code"] == "authoritative-connector-required"
    assert backend.scan_calls == []
    assert backend.raw_calls == []
    assert (tmp_path / "projection" / "_projection.json").read_bytes() == before

    without_baseline = reconciler(tmp_path / "without-baseline", backend).run(approved=True)
    assert without_baseline["error"]["code"] == "authoritative-connector-required"


def test_folder_rename_keeps_locator_occurrence_and_content_identity(tmp_path: Path) -> None:
    raw = mail("rename")
    seed(tmp_path / "projection", {"Old": [("1", raw)]})
    before = load_search_projection(tmp_path / "projection").records[0][1]
    backend = DeltaBackend({"New": [observation("1", raw)]}, {("New", "1"): raw})
    backend.folder_ids = {"New": "folder:old"}
    backend.uidvalidity = {"New": "10"}

    result = reconciler(tmp_path, backend).run(approved=True)
    after = load_search_projection(tmp_path / "projection").records[0][1]

    assert result["metrics"]["moved"] == 1
    assert backend.raw_calls == []
    assert before["content_id"] == after["content_id"]
    assert before["metadata"]["occurrence_ids"] == after["metadata"]["occurrence_ids"]
    assert after["metadata"]["locators"][0]["folder_name"] == "New"


def test_batched_verified_moves_have_constant_zero_content_work(tmp_path: Path) -> None:
    rows = [(str(index), mail(f"body-{index}", message_id=f"{index}@test")) for index in range(5)]
    ids = seed(tmp_path / "projection", {"INBOX": rows, "Archive": []})
    target = [
        observation(str(index + 10), raw, move_from=ids[f"INBOX:{index}"])
        for index, (_uid, raw) in enumerate(rows)
    ]
    backend = DeltaBackend(
        {"INBOX": [], "Archive": target},
        {("Archive", item.uid): rows[index][1] for index, item in enumerate(target)},
    )

    result = reconciler(tmp_path, backend).run(approved=True)

    assert result["metrics"]["moved"] == 5
    assert result["metrics"]["seen"] == 5
    assert result["metrics"]["body_fetches"] == 0
    assert result["metrics"]["bytes"] == 0
    assert result["metrics"]["parser_calls"] == 0
    assert result["metrics"]["clamav_calls"] == 0
    assert result["metrics"]["fts_rows_changed"] == 0


def test_copy_delete_last_locator_and_reappearance_are_distinct(tmp_path: Path) -> None:
    raw = mail("copy")
    seed(tmp_path / "projection", {"INBOX": [("1", raw)], "Archive": []})
    backend = DeltaBackend(
        {
            "INBOX": [observation("1", raw)],
            "Archive": [observation("2", raw)],
        },
        {("INBOX", "1"): raw, ("Archive", "2"): raw},
    )
    first = reconciler(tmp_path, backend).run(approved=True)
    assert first["metrics"]["copied"] == 1
    first_metadata = load_search_projection(tmp_path / "projection").records[0][1]["metadata"]
    assert len(first_metadata["occurrence_ids"]) == 2

    backend.folders = {"INBOX": [], "Archive": [observation("2", raw)]}
    second = reconciler(tmp_path, backend).run(approved=True)
    assert second["metrics"]["removed"] == 1
    assert len(load_search_projection(tmp_path / "projection").records) == 1

    backend.folders = {"INBOX": [], "Archive": []}
    third = reconciler(tmp_path, backend).run(approved=True)
    assert third["metrics"]["removed"] == 1
    assert load_search_projection(tmp_path / "projection").records == ()

    backend.folders = {"INBOX": [observation("7", raw, verified=False)], "Archive": []}
    backend.raw[("INBOX", "7")] = raw
    fourth = reconciler(tmp_path, backend).run(approved=True)
    assert fourth["metrics"]["new"] == 1
    assert fourth["metrics"]["body_fetches"] == 1
    assert len(load_search_projection(tmp_path / "projection").records) == 1


def test_ambiguous_move_fetches_raw_sha_but_reuses_parser_scanner_and_content(tmp_path: Path) -> None:
    raw = mail("ambiguous")
    seed(tmp_path / "projection", {"INBOX": [("1", raw)], "Archive": []})
    backend = DeltaBackend(
        {"INBOX": [], "Archive": [observation("8", raw, verified=False)]},
        {("Archive", "8"): raw},
    )
    scanner = Scanner()

    result = reconciler(tmp_path, backend, scanner).run(approved=True)

    assert result["metrics"]["moved"] == 1
    assert result["metrics"]["body_fetches"] == 1
    assert result["metrics"]["parser_calls"] == 0
    assert result["metrics"]["clamav_calls"] == 0
    assert scanner.calls == []


def test_changed_content_and_scanner_identity_trigger_bounded_rescan(tmp_path: Path) -> None:
    old = mail("old")
    new = mail("new")
    seed(tmp_path / "projection", {"INBOX": [("1", old)]})
    backend = DeltaBackend({"INBOX": [observation("1", new)]}, {("INBOX", "1"): new})
    scanner = Scanner()

    changed = reconciler(tmp_path, backend, scanner).run(approved=True)

    assert changed["metrics"]["changed"] == 1
    assert changed["metrics"]["body_fetches"] == 1
    assert changed["metrics"]["parser_calls"] == 1
    assert changed["metrics"]["clamav_calls"] == 1

    scanner.identity = "clamav:m113-b"
    backend.folders = {"INBOX": [observation("1", new)]}
    rescanned = reconciler(tmp_path, backend, scanner).run(approved=True)
    assert rescanned["metrics"]["body_fetches"] == 1
    assert rescanned["metrics"]["clamav_calls"] == 1
    assert rescanned["metrics"]["parser_calls"] == 0


@pytest.mark.parametrize("failure", ["partial", "network"])
def test_partial_or_network_scan_preserves_root_and_cursor(tmp_path: Path, failure: str) -> None:
    raw = mail("safe")
    seed(tmp_path / "projection", {"INBOX": [("1", raw)]})
    backend = DeltaBackend({"INBOX": [observation("1", raw)]}, {("INBOX", "1"): raw})
    state = tmp_path / "state.json"
    state.write_text('{"schema":1,"root_generation":"old","scanner_identity":"clamav:m113-a","folder_cursors":{"folder:inbox":"old"}}')
    root_before = (tmp_path / "projection" / "_projection.json").read_bytes()
    state_before = state.read_bytes()
    if failure == "partial":
        backend.partial.add("INBOX")
    else:
        backend.fail["INBOX"] = "network-loss"

    result = reconciler(tmp_path, backend).run(approved=True)

    assert result["complete"] is False and result["cursor_advanced"] is False
    assert (tmp_path / "projection" / "_projection.json").read_bytes() == root_before
    assert state.read_bytes() == state_before


@pytest.mark.parametrize("boundary", ["after-scan", "before-root"])
def test_crash_before_root_keeps_previous_generation(tmp_path: Path, boundary: str) -> None:
    raw = mail("move")
    ids = seed(tmp_path / "projection", {"INBOX": [("1", raw)], "Archive": []})
    backend = DeltaBackend(
        {"INBOX": [], "Archive": [observation("2", raw, move_from=ids["INBOX:1"])]},
        {("Archive", "2"): raw},
    )
    before = (tmp_path / "projection" / "_projection.json").read_bytes()

    def crash(event: str) -> None:
        if event == boundary:
            raise RuntimeError("crash-boundary")

    with pytest.raises(RuntimeError, match="crash-boundary"):
        reconciler(tmp_path, backend, hook=crash).run(approved=True)
    assert (tmp_path / "projection" / "_projection.json").read_bytes() == before
    assert not (tmp_path / "state.json").exists()


def test_crash_after_verified_root_keeps_old_cursor_and_replays_safely(tmp_path: Path) -> None:
    raw = mail("move")
    ids = seed(tmp_path / "projection", {"INBOX": [("1", raw)], "Archive": []})
    backend = DeltaBackend(
        {"INBOX": [], "Archive": [observation("2", raw, move_from=ids["INBOX:1"])]},
        {("Archive", "2"): raw},
    )

    def crash(event: str) -> None:
        if event == "after-root":
            raise RuntimeError("after-root")

    with pytest.raises(RuntimeError, match="after-root"):
        reconciler(tmp_path, backend, hook=crash).run(approved=True)
    assert load_search_projection(tmp_path / "projection").complete is True
    assert not (tmp_path / "state.json").exists()

    replay = reconciler(tmp_path, backend).run(approved=True)
    assert replay["ok"] is True
    assert len(load_search_projection(tmp_path / "projection").records) == 1


def test_uidvalidity_reset_reuses_verified_content_but_changes_occurrence(tmp_path: Path) -> None:
    raw = mail("reset")
    seed(tmp_path / "projection", {"INBOX": [("1", raw)]})
    old_ids = load_search_projection(tmp_path / "projection").records[0][1]["metadata"]["occurrence_ids"]
    backend = DeltaBackend({"INBOX": [observation("1", raw)]}, {("INBOX", "1"): raw})
    backend.uidvalidity["INBOX"] = "11"

    result = reconciler(tmp_path, backend).run(approved=True)
    new_ids = load_search_projection(tmp_path / "projection").records[0][1]["metadata"]["occurrence_ids"]

    assert result["metrics"]["changed"] == 1
    assert old_ids != new_ids
    assert backend.raw_calls == []


def test_antivirus_block_preserves_complete_root_and_no_body_leaks(tmp_path: Path) -> None:
    old = mail("old")
    bad = mail("MALWARE PRIVATE")
    seed(tmp_path / "projection", {"INBOX": [("1", old)]})
    backend = DeltaBackend({"INBOX": [observation("1", bad)]}, {("INBOX", "1"): bad})
    scanner = Scanner()
    scanner.block = b"MALWARE"
    before = (tmp_path / "projection" / "_projection.json").read_bytes()

    result = reconciler(tmp_path, backend, scanner).run(approved=True)

    assert result["error"]["code"] == "antivirus-blocked"
    assert result["metrics"]["blocked"] == 1
    assert (tmp_path / "projection" / "_projection.json").read_bytes() == before
    state_path = tmp_path / "state.json"
    assert not state_path.exists() or b"PRIVATE" not in state_path.read_bytes()


def test_antivirus_error_is_fail_closed_and_content_free(tmp_path: Path) -> None:
    raw = mail("PRIVATE new body")
    seed(tmp_path / "projection", {"INBOX": []})
    backend = DeltaBackend(
        {"INBOX": [observation("1", raw, verified=False)]},
        {("INBOX", "1"): raw},
    )
    scanner = Scanner()
    scanner.fail = True
    before = (tmp_path / "projection" / "_projection.json").read_bytes()

    result = reconciler(tmp_path, backend, scanner).run(approved=True)

    assert result["error"] == {"code": "antivirus-error", "detail": "RuntimeError"}
    assert (tmp_path / "projection" / "_projection.json").read_bytes() == before
    assert "PRIVATE" not in json.dumps(result)


def test_knowledge_apply_is_atomic_and_move_changes_no_fts_rows(tmp_path: Path) -> None:
    raw = mail("searchable")
    seed(tmp_path / "projection", {"INBOX": [("1", raw)]})
    config = AssistantConfig()
    config.runtime.database = tmp_path / "core.sqlite3"
    config.search.mail_snapshot_dir = tmp_path / "projection"
    config.search.mail_projection_max_age_seconds = 10**9
    storage = AssistantStorage(config.runtime.database)
    try:
        first = KnowledgeIndexer(config, storage).index_mail_snapshots()
        assert first["published"] is True and first["indexed"] == 1
        chunk_count = storage.knowledge_connection.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]

        metadata = load_search_projection(tmp_path / "projection").records[0][1]["metadata"]
        ids = {"INBOX:1": metadata["locators"][0]["locator_id"]}
        backend = DeltaBackend(
            {"INBOX": [], "Archive": [observation("2", raw, move_from=ids["INBOX:1"])]},
            {("Archive", "2"): raw},
        )
        backend.folder_ids["Archive"] = "folder:archive"
        backend.uidvalidity["Archive"] = "10"
        assert reconciler(tmp_path, backend).run(approved=True)["ok"] is True
        second = KnowledgeIndexer(config, storage).index_mail_snapshots()
        assert second["fts_rows_changed"] == 0
        assert second["unchanged"] == 1
        row = storage.knowledge_connection.execute("SELECT COUNT(*) FROM chunks").fetchone()
        assert row[0] == chunk_count

        cursor_before = storage.get_sync_state("mail-agent", "projection")["cursor"]
        failing = KnowledgeIndexer(
            config,
            storage,
            before_mail_projection_commit=lambda: (_ for _ in ()).throw(RuntimeError("commit-crash")),
        ).index_mail_snapshots()
        assert failing["published"] is False
        assert storage.get_sync_state("mail-agent", "projection")["cursor"] == cursor_before
    finally:
        storage.close()


def test_retention_keeps_active_and_previous_and_never_mail_source(tmp_path: Path) -> None:
    first_raw = mail("one")
    seed(tmp_path / "projection", {"INBOX": [("1", first_raw)]})
    source = tmp_path / "projection" / "mail_agent.sqlite3"
    source.write_bytes(b"do-not-delete")
    backend = DeltaBackend({"INBOX": [observation("1", mail("two"))]}, {("INBOX", "1"): mail("two")})
    first = reconciler(tmp_path, backend).run(approved=True)
    assert first["retention"]["kept_generations"] == 2

    third_raw = mail("three")
    backend.folders = {"INBOX": [observation("1", third_raw)]}
    backend.raw[("INBOX", "1")] = third_raw
    second = reconciler(tmp_path, backend).run(approved=True)
    assert second["retention"]["kept_generations"] == 2
    assert len(list((tmp_path / "projection").glob("root-*.json"))) == 2
    assert source.read_bytes() == b"do-not-delete"


def test_tool_cli_scheduler_and_approval_contract(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    catalog = {tool.id: tool for tool in TOOLS}
    tool = catalog["mail.index.reconcile"]
    assert tool.mode == "local-write"
    assert tool.writes_external_data is False
    assert tool.approval == "explicit-user-local-mail-index-reconcile"
    with pytest.raises(PermissionError, match="--yes"):
        raw = mail("x")
        seed(tmp_path / "projection", {"INBOX": [("1", raw)]})
        reconciler(
            tmp_path,
            DeltaBackend({"INBOX": [observation("1", raw)]}, {("INBOX", "1"): raw}),
        ).run(approved=False)

    captured: dict[str, object] = {}
    def fake_run(command, *, check, env):
        del check, env
        captured["command"] = command
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr("personal_assistant.cli_handlers.mail.subprocess.run", fake_run)
    args = assistant_parser().parse_args(["mail", "index", "reconcile", "--yes"])
    assert run_external(args) == 0
    assert captured["command"][3:5] == ["index", "reconcile"]
    assert "--yes" in captured["command"]

    scheduler = AdaptiveWorkScheduler(tmp_path / "scheduler.sqlite3")
    try:
        policy = scheduler.policy("mail-index")
        assert policy.topic == "knowledge"
        assert policy.max_runtime_seconds == 3600
        index_job = {item.name: item for item in default_job_specs()}["mail-index"]
        assert index_job.default_on is False
        assert index_job.service_unit == "mail-agent.service"
    finally:
        scheduler.close()
