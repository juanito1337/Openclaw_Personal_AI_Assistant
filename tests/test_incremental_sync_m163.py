from __future__ import annotations

import argparse
import hashlib
import importlib.util
import tempfile
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from personal_assistant.cli_handlers.core import handle as handle_core
from personal_assistant.config import AssistantConfig, RuntimeConfig, SearchConfig
from personal_assistant.connectors.nextcloud.calendar import (
    CalendarMetadata,
    CalendarObject,
    NextcloudCalendar,
)
from personal_assistant.connectors.nextcloud.client import NextcloudError
from personal_assistant.connectors.nextcloud.contacts import (
    Contact,
    ContactMetadata,
    NextcloudContacts,
)
from personal_assistant.connectors.nextcloud.discovery import DiscoveredCollection
from personal_assistant.connectors.nextcloud.files import NextcloudFiles, RemoteFile
from personal_assistant.incremental_sync import RemoteObject, StageTelemetry, plan_batch
from personal_assistant.service import PersonalAssistant
from personal_assistant.storage import AssistantStorage
from personal_assistant.work_scheduler import AdaptiveWorkScheduler


class FakeClient:
    username = "jan"

    @staticmethod
    def validate_url() -> str:
        return "https://nextcloud.example"


class FakeIndexer:
    def __init__(self, storage: AssistantStorage) -> None:
        self.storage = storage
        self.calls: list[str] = []
        self.fail_after_write = False

    def index_binary_document(self, **values: object) -> bool:
        source_id = str(values["source_id"])
        self.calls.append(source_id)
        data = bytes(values["data"])
        self.storage.index_document(
            source_type=str(values["source_type"]),
            resource_id=str(values["resource_id"]),
            source_id=source_id,
            uri=str(values["uri"]),
            title=str(values["title"]),
            mime_type=str(values["mime_type"]),
            modified_at=str(values["modified_at"]),
            etag=str(values["etag"]),
            metadata=dict(values["metadata"]),
            chunks=[data.decode("utf-8")],
        )
        if self.fail_after_write:
            self.fail_after_write = False
            raise RuntimeError("simulierter Crash nach Projektion")
        return True


class FakeFiles(NextcloudFiles):
    def __init__(self, config: AssistantConfig) -> None:
        super().__init__(config, FakeClient())
        self.entries: list[RemoteFile] = []
        self.payloads: dict[str, bytes] = {}
        self.downloads: list[str] = []
        self.download_etags: list[str] = []
        self.download_error = False
        self.list_error = False

    def list_folder(self, path: str) -> list[RemoteFile]:
        if self.list_error:
            raise RuntimeError("Provider nicht erreichbar")
        return list(self.entries) if path == "Assistent" else []

    def download(self, path: str, *, expected_etag: str = "") -> bytes:
        self.downloads.append(path)
        self.download_etags.append(expected_etag)
        if self.download_error:
            raise NextcloudError("Nextcloud-Datei wurde seit der Auswahl geaendert")
        return self.payloads[path]


class FakeContacts(NextcloudContacts):
    def __init__(self, config: AssistantConfig) -> None:
        super().__init__(config, FakeClient())
        self.metadata = [ContactMetadata("/contacts/jan.vcf", '"c1"')]
        self.reads = 0

    def list_contact_metadata(self, addressbook):
        del addressbook
        return list(self.metadata)

    def read_contact(self, href: str, *, fallback_uid: str = "") -> Contact:
        del fallback_uid
        self.reads += 1
        return Contact(
            uid="jan-contact",
            name="Jan",
            emails=("jan@example.test",),
            phones=(),
            organization="",
            raw="BEGIN:VCARD\nEND:VCARD",
            href=href,
            etag='"c1"',
        )


class FakeCalendar(NextcloudCalendar):
    def __init__(self, config: AssistantConfig) -> None:
        super().__init__(config, FakeClient())
        self.metadata = [CalendarMetadata("/calendar/event.ics", '"e1"')]
        self.reads = 0

    def list_event_metadata(self, calendar):
        del calendar
        return list(self.metadata)

    def read_event(self, href: str, *, fallback_uid: str = "") -> CalendarObject:
        del fallback_uid
        self.reads += 1
        return CalendarObject(
            uid="event-1",
            summary="Termin",
            starts_at="20260920T100000Z",
            ends_at="20260920T110000Z",
            description="",
            location="",
            status="CONFIRMED",
            recurring=False,
            all_day=False,
            raw_ics="BEGIN:VCALENDAR\nEND:VCALENDAR",
            href=href,
            etag='"e1"',
        )


def remote(path: str, etag: str, *, modified: str = "2026-09-20T10:00:00Z") -> RemoteFile:
    return RemoteFile(
        href=f"/remote.php/dav/files/jan/{path}",
        path=path,
        name=Path(path).name,
        is_collection=False,
        content_type="text/plain",
        size=20,
        etag=etag,
        modified_at=modified,
    )


@pytest.fixture
def sync_runtime() -> tuple[AssistantStorage, FakeFiles, FakeIndexer]:
    temporary = tempfile.TemporaryDirectory()
    root = Path(temporary.name)
    config = AssistantConfig(
        runtime=RuntimeConfig(database=root / "assistant.sqlite3"),
        search=SearchConfig(
            chunk_chars=500,
            chunk_overlap_chars=0,
            max_file_bytes=1_000_000,
        ),
    )
    storage = AssistantStorage(config.runtime.database)
    files = FakeFiles(config)
    indexer = FakeIndexer(storage)
    try:
        yield storage, files, indexer
    finally:
        storage.close()
        temporary.cleanup()


def run_files(
    storage: AssistantStorage,
    files: FakeFiles,
    indexer: FakeIndexer,
    *,
    batch_size: int = 100,
    max_items: int = 100,
    max_depth: int = 3,
) -> dict[str, object]:
    return files.sync_index(
        storage,
        indexer,  # type: ignore[arg-type]
        resource_id="nextcloud-files-main",
        roots=("Assistent",),
        max_items=max_items,
        max_depth=max_depth,
        batch_size=batch_size,
    )


def test_batch_cursor_resumes_and_resets_on_remote_generation_change() -> None:
    objects = [RemoteObject("a", "1"), RemoteObject("b", "2")]
    first = plan_batch(objects, batch_size=1)
    second = plan_batch(objects, cursor=first.cursor(), batch_size=1)
    reset = plan_batch(
        [RemoteObject("a", "changed"), RemoteObject("b", "2")],
        cursor=first.cursor(),
        batch_size=1,
    )

    assert first.resume_required and first.offset == 0
    assert second.resumed and second.complete and second.offset == 1
    assert reset.cursor_reset and reset.offset == 0


def test_noop_sync_avoids_download_parse_and_projection_rewrite(sync_runtime) -> None:
    storage, files, indexer = sync_runtime
    files.entries = [remote("Assistent/a.txt", '"a1"')]
    files.payloads = {"Assistent/a.txt": b"alpha"}
    first = run_files(storage, files, indexer)
    document = storage.get_document("nextcloud-files-main", "Assistent/a.txt")
    assert document is not None
    indexed_at = str(document["indexed_at"])

    files.downloads.clear()
    indexer.calls.clear()
    second = run_files(storage, files, indexer)

    assert first["indexed"] == 1
    assert second["unchanged"] == 1
    assert files.downloads == []
    assert indexer.calls == []
    assert storage.get_document("nextcloud-files-main", "Assistent/a.txt")["indexed_at"] == indexed_at


def test_single_change_only_downloads_and_indexes_affected_object(sync_runtime) -> None:
    storage, files, indexer = sync_runtime
    files.entries = [
        remote("Assistent/a.txt", '"a1"'),
        remote("Assistent/b.txt", '"b1"'),
    ]
    files.payloads = {
        "Assistent/a.txt": b"alpha",
        "Assistent/b.txt": b"beta",
    }
    run_files(storage, files, indexer)
    files.entries[1] = remote("Assistent/b.txt", '"b2"')
    files.payloads["Assistent/b.txt"] = b"beta-neu"
    files.downloads.clear()
    indexer.calls.clear()

    result = run_files(storage, files, indexer)

    assert result["indexed"] == 1
    assert result["unchanged"] == 1
    assert files.downloads == ["Assistent/b.txt"]
    assert indexer.calls == ["Assistent/b.txt"]


def test_move_reuses_projection_and_delete_removes_stale_document(sync_runtime) -> None:
    storage, files, indexer = sync_runtime
    files.entries = [remote("Assistent/alt.txt", '"stable"')]
    files.payloads = {"Assistent/alt.txt": b"inhalt"}
    run_files(storage, files, indexer)
    chunk_id = storage.knowledge_connection.execute(
        "SELECT c.id FROM chunks c JOIN documents d ON d.id=c.document_id "
        "WHERE d.resource_id=? AND d.source_id=?",
        ("nextcloud-files-main", "Assistent/alt.txt"),
    ).fetchone()[0]

    files.entries = [remote("Assistent/neu.txt", '"stable"')]
    files.payloads["Assistent/neu.txt"] = b"inhalt"
    files.downloads.clear()
    moved = run_files(storage, files, indexer)

    assert moved["moved"] == 1
    assert files.downloads == ["Assistent/neu.txt"]
    assert files.download_etags[-1] == '"stable"'
    assert storage.get_sync_inventory(
        "nextcloud-files-main", "files", "Assistent/neu.txt"
    )["sha256"] == hashlib.sha256(b"inhalt").hexdigest()
    assert storage.get_document("nextcloud-files-main", "Assistent/alt.txt") is None
    assert storage.get_document("nextcloud-files-main", "Assistent/neu.txt") is not None
    assert (
        storage.knowledge_connection.execute(
            "SELECT c.id FROM chunks c JOIN documents d ON d.id=c.document_id "
            "WHERE d.resource_id=? AND d.source_id=?",
            ("nextcloud-files-main", "Assistent/neu.txt"),
        ).fetchone()[0]
        == chunk_id
    )

    files.entries = []
    deleted = run_files(storage, files, indexer)
    assert deleted["removed"] == 1
    assert storage.get_document("nextcloud-files-main", "Assistent/neu.txt") is None


def test_provider_failure_is_fail_closed_and_keeps_projection(sync_runtime) -> None:
    storage, files, indexer = sync_runtime
    files.entries = [remote("Assistent/a.txt", '"a1"')]
    files.payloads = {"Assistent/a.txt": b"alpha"}
    run_files(storage, files, indexer)
    files.list_error = True

    failed = run_files(storage, files, indexer)

    assert failed["authoritative"] is False
    assert failed["errors"] == 1
    assert storage.get_document("nextcloud-files-main", "Assistent/a.txt") is not None


def test_last_folder_item_limit_preserves_undiscovered_projection(sync_runtime) -> None:
    storage, files, indexer = sync_runtime
    files.entries = [remote("Assistent/a.txt", '"a1"'), remote("Assistent/b.txt", '"b1"')]
    files.payloads = {"Assistent/a.txt": b"alpha", "Assistent/b.txt": b"beta"}
    run_files(storage, files, indexer)

    result = run_files(storage, files, indexer, max_items=1)

    assert result["authoritative"] is False
    assert result["complete"] is False
    assert result["results_may_be_truncated"] is True
    assert result["discovery_limitations"] == ["max-items"]
    assert result["removed"] == 0
    assert storage.get_document("nextcloud-files-main", "Assistent/b.txt") is not None
    assert storage.get_sync_state("nextcloud-files-main", "files")["status"] == "partial"


def test_exact_item_boundary_without_omission_remains_authoritative(sync_runtime) -> None:
    storage, files, indexer = sync_runtime
    files.entries = [remote("Assistent/a.txt", '"a1"'), remote("Assistent/b.txt", '"b1"')]
    files.payloads = {"Assistent/a.txt": b"alpha", "Assistent/b.txt": b"beta"}
    run_files(storage, files, indexer)
    files.entries = files.entries[:1]

    result = run_files(storage, files, indexer, max_items=1)

    assert result["authoritative"] is True
    assert result["complete"] is True
    assert result["results_may_be_truncated"] is False
    assert result["removed"] == 1


@pytest.mark.parametrize("limit", ["depth", "items"])
def test_undiscovered_child_folder_blocks_reconciliation(sync_runtime, limit: str) -> None:
    storage, files, indexer = sync_runtime
    nested = remote("Assistent/sub/b.txt", '"b1"')
    files.entries = [remote("Assistent/a.txt", '"a1"'), nested]
    files.payloads = {"Assistent/a.txt": b"alpha", nested.path: b"beta"}
    run_files(storage, files, indexer)
    folder = replace(remote("Assistent/sub", '"folder"'), is_collection=True)
    files.entries = [files.entries[0], folder]

    result = run_files(
        storage, files, indexer,
        max_depth=0 if limit == "depth" else 3,
        max_items=1 if limit == "items" else 100,
    )

    assert result["authoritative"] is False
    assert result["discovery_limitations"] == ["max-depth" if limit == "depth" else "max-items"]
    assert result["removed"] == 0
    assert storage.get_document("nextcloud-files-main", nested.path) is not None


def test_same_etag_copy_preserves_original_projection(sync_runtime) -> None:
    storage, files, indexer = sync_runtime
    files.entries = [remote("Assistent/a.txt", '"same"')]
    files.payloads = {"Assistent/a.txt": b"alpha", "Assistent/b.txt": b"alpha"}
    run_files(storage, files, indexer)
    files.entries.append(remote("Assistent/b.txt", '"same"'))
    files.downloads.clear()

    result = run_files(storage, files, indexer)
    repeated = run_files(storage, files, indexer)

    assert result["moved"] == repeated["moved"] == 0
    assert files.downloads == ["Assistent/b.txt"]
    assert repeated["unchanged"] == 2
    assert storage.get_document("nextcloud-files-main", "Assistent/a.txt") is not None
    assert storage.get_document("nextcloud-files-main", "Assistent/b.txt") is not None


def test_ambiguous_remote_etag_never_reuses_move_projection(sync_runtime) -> None:
    storage, files, indexer = sync_runtime
    files.entries = [remote("Assistent/old.txt", '"same"')]
    files.payloads = {
        "Assistent/old.txt": b"alpha", "Assistent/b.txt": b"alpha", "Assistent/c.txt": b"alpha",
    }
    run_files(storage, files, indexer)
    files.entries = [remote("Assistent/b.txt", '"same"'), remote("Assistent/c.txt", '"same"')]
    files.downloads.clear()

    result = run_files(storage, files, indexer)

    assert result["moved"] == 0
    assert result["removed"] == 1
    assert files.downloads == ["Assistent/b.txt", "Assistent/c.txt"]


def test_partial_discovery_never_proves_old_move_locator_absent(sync_runtime) -> None:
    storage, files, indexer = sync_runtime
    files.entries = [remote("Assistent/a.txt", '"same"')]
    files.payloads = {"Assistent/a.txt": b"alpha", "Assistent/b.txt": b"alpha"}
    run_files(storage, files, indexer)
    files.entries = [remote("Assistent/b.txt", '"same"'), files.entries[0]]
    files.downloads.clear()

    result = run_files(storage, files, indexer, max_items=1)

    assert result["authoritative"] is False
    assert result["moved"] == result["removed"] == 0
    assert files.downloads == ["Assistent/b.txt"]
    assert storage.get_document("nextcloud-files-main", "Assistent/a.txt") is not None


def test_move_evidence_uses_whole_snapshot_across_batches(sync_runtime) -> None:
    storage, files, indexer = sync_runtime
    files.entries = [remote("Assistent/old.txt", '"stable"')]
    files.payloads = {
        "Assistent/old.txt": b"alpha",
        "Assistent/a.txt": b"first",
        "Assistent/z.txt": b"alpha",
    }
    run_files(storage, files, indexer)
    files.entries = [remote("Assistent/a.txt", '"new"'), remote("Assistent/z.txt", '"stable"')]
    files.downloads.clear()

    first = run_files(storage, files, indexer, batch_size=1)
    second = run_files(storage, files, indexer, batch_size=1)

    assert first["resume_required"] is True
    assert second["complete"] is True
    assert second["moved"] == 1
    assert files.downloads == ["Assistent/a.txt", "Assistent/z.txt"]
    assert storage.get_document("nextcloud-files-main", "Assistent/z.txt") is not None


@pytest.mark.parametrize(
    "previous_sha256,replacement", [("present", b"beta"), ("missing", b"alpha")]
)
def test_cross_locator_etag_is_not_content_identity(
    sync_runtime, previous_sha256, replacement
) -> None:
    storage, files, indexer = sync_runtime
    files.entries = [remote("Assistent/a.txt", '"same"')]
    files.payloads = {"Assistent/a.txt": b"alpha", "Assistent/b.txt": replacement}
    run_files(storage, files, indexer)
    if previous_sha256 == "missing":
        storage.upsert_sync_inventory(
            resource_id="nextcloud-files-main", scope="files", remote_id="Assistent/a.txt",
            source_id="Assistent/a.txt", source_type="nextcloud-file", etag='"same"',
        )
    files.entries = [remote("Assistent/b.txt", '"same"')]
    files.downloads.clear()
    indexer.calls.clear()

    result = run_files(storage, files, indexer)

    assert result["complete"] is True
    assert result["moved"] == 0
    assert result["indexed"] == result["removed"] == 1
    assert files.downloads == indexer.calls == ["Assistent/b.txt"]
    assert files.download_etags[-1] == '"same"'
    assert storage.get_document("nextcloud-files-main", "Assistent/a.txt") is None
    row = storage.knowledge_connection.execute(
        "SELECT c.text FROM chunks c JOIN documents d ON d.id=c.document_id "
        "WHERE d.resource_id=? AND d.source_id=?",
        ("nextcloud-files-main", "Assistent/b.txt"),
    ).fetchone()
    assert row[0] == replacement.decode("utf-8")


def test_list_to_download_etag_conflict_keeps_previous_projection(sync_runtime) -> None:
    storage, files, indexer = sync_runtime
    files.entries = [remote("Assistent/a.txt", '"same"')]
    files.payloads = {"Assistent/a.txt": b"alpha"}
    run_files(storage, files, indexer)
    files.entries = [remote("Assistent/b.txt", '"same"')]
    files.download_error = True
    indexer.calls.clear()

    result = run_files(storage, files, indexer)

    assert result["errors"] == 1
    assert result["complete"] is False
    assert result["moved"] == result["removed"] == 0
    assert files.download_etags[-1] == '"same"'
    assert indexer.calls == []
    assert storage.get_document("nextcloud-files-main", "Assistent/a.txt") is not None
    assert storage.get_document("nextcloud-files-main", "Assistent/b.txt") is None


def test_inventory_move_without_new_hash_preserves_existing_hash(sync_runtime) -> None:
    storage, files, indexer = sync_runtime
    files.entries = [remote("Assistent/a.txt", '"same"')]
    files.payloads = {"Assistent/a.txt": b"alpha"}
    run_files(storage, files, indexer)

    storage.move_sync_inventory(
        resource_id="nextcloud-files-main", scope="files", previous_remote_id="Assistent/a.txt",
        remote_id="Assistent/b.txt", source_id="Assistent/b.txt", source_type="nextcloud-file",
        etag='"same"',
    )

    assert storage.get_sync_inventory(
        "nextcloud-files-main", "files", "Assistent/b.txt"
    )["sha256"] == hashlib.sha256(b"alpha").hexdigest()


def test_service_surfaces_partial_discovery_even_without_transport_errors() -> None:
    service = object.__new__(PersonalAssistant)
    service.role = "sync-worker"
    service.config = SimpleNamespace(
        nextcloud=SimpleNamespace(enabled=True, allowed_file_roots=("Assistent",)),
        search=SimpleNamespace(nextcloud_max_items=1, nextcloud_max_depth=3),
    )
    service.discover_nextcloud = lambda **_kwargs: {"health": {"ok": True}}
    service.nextcloud_discovery = SimpleNamespace(calendars=lambda: [], addressbooks=lambda: [])
    service.storage = object()
    service.indexer = object()
    service.nextcloud_files = SimpleNamespace(sync_index=lambda *_args, **_kwargs: {
        "errors": 0, "authoritative": False, "discovery_limitations": ["max-items"],
    })
    service.nextcloud_contacts = SimpleNamespace(sync_index=lambda *_args, **_kwargs: {"errors": 0})
    service.nextcloud_calendar = SimpleNamespace(sync_index=lambda *_args, **_kwargs: {"errors": 0})

    result = service.sync_nextcloud()

    assert result["ok"] is False
    assert result["complete"] is False
    assert result["partial"] is True
    assert result["partial_scopes"] == ["files"]


def test_carddav_and_caldav_noop_stop_before_object_download(sync_runtime) -> None:
    storage, files, _indexer = sync_runtime
    contacts = FakeContacts(files.config)
    calendar = FakeCalendar(files.config)
    addressbook = DiscoveredCollection(
        kind="addressbook",
        href="/contacts/",
        name="Kontakte",
        resource_id="addressbook-1",
    )
    event_calendar = DiscoveredCollection(
        kind="calendar",
        href="/calendar/",
        name="Kalender",
        resource_id="calendar-1",
    )

    first_contacts = contacts.sync_index(storage, [addressbook])
    first_calendar = calendar.sync_index(storage, [event_calendar])
    second_contacts = contacts.sync_index(storage, [addressbook])
    second_calendar = calendar.sync_index(storage, [event_calendar])

    assert first_contacts["indexed"] == first_calendar["indexed"] == 1
    assert second_contacts["unchanged"] == second_calendar["unchanged"] == 1
    assert contacts.reads == 1
    assert calendar.reads == 1


def test_crash_after_projection_resumes_without_duplicate_chunks(sync_runtime) -> None:
    storage, files, indexer = sync_runtime
    files.entries = [remote("Assistent/a.txt", '"a1"')]
    files.payloads = {"Assistent/a.txt": b"alpha"}
    indexer.fail_after_write = True

    failed = run_files(storage, files, indexer)
    resumed = run_files(storage, files, indexer)

    assert failed["errors"] == 1
    assert resumed["unchanged"] == 1
    assert storage.knowledge_connection.execute("SELECT count(*) FROM chunks").fetchone()[0] == 1


def test_sync_batches_release_slot_for_time_critical_mail(tmp_path: Path) -> None:
    scheduler = AdaptiveWorkScheduler(
        tmp_path / "scheduler.sqlite3",
        arbitration_seconds=0,
    )
    try:
        sync = scheduler.enqueue("sync", owner="sync-worker")
        first = scheduler.claim(sync, owner="sync-worker")
        assert first.granted
        assert scheduler.finish(
            first.lease_token,
            owner="sync-worker",
            result="completed",
            exit_code=75,
            detail="batch-resume",
        )
        next_sync = scheduler.enqueue("sync", owner="sync-worker", parent_run_id=first.run_id)
        mail = scheduler.enqueue("mail", owner="mail-worker")

        mail_claim = scheduler.claim(mail, owner="mail-worker")
        sync_claim = scheduler.claim(next_sync, owner="sync-worker")

        assert mail_claim.granted
        assert not sync_claim.granted and sync_claim.reason == "busy"
        assert scheduler.snapshot()["pending"][0]["scheduler_class"] == "background"
    finally:
        scheduler.close()


def test_stage_telemetry_is_content_free_and_requires_closed_stages() -> None:
    telemetry = StageTelemetry()
    telemetry.start("download")
    with pytest.raises(RuntimeError):
        telemetry.to_dict()
    telemetry.stop("download")
    telemetry.add("download_bytes", 42)
    payload = telemetry.to_dict()
    assert payload["content_fields"] == []
    assert payload["counters"] == {"download_bytes": 42}


def test_batched_cli_uses_private_resume_exit_code(monkeypatch) -> None:
    class Assistant:
        @staticmethod
        def sync_all() -> dict[str, object]:
            return {"ok": True, "resume_required": True}

    emitted: list[object] = []
    monkeypatch.setenv("OPENCLAW_BATCHED_JOB", "1")
    result = handle_core(
        argparse.Namespace(command="index", index_command="all"),
        Assistant(),
        emitted.append,
    )
    assert result == 75
    assert emitted == [{"ok": True, "resume_required": True}]


def test_sync_worker_enables_batched_job_contract() -> None:
    path = Path(__file__).resolve().parents[1] / "docker/job_loop.py"
    spec = importlib.util.spec_from_file_location("m163_job_loop", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    _command, _interval, _delay, _default, environment = module.config(
        "sync", Path("/workspace"), Path("/image")
    )
    assert module.BATCH_RESUME_EXIT_CODE == 75
    assert environment["OPENCLAW_BATCHED_JOB"] == "1"
