from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from mail_agent.app import MailAgent, calendar_doctor_payload
from mail_agent.models import Classification, Envelope, OperationResult
from mail_agent.parser import parse_eml
from mail_agent.review import ReviewReason
from mail_agent.search_snapshot import (
    PROJECTION_MANIFEST,
    SearchSnapshotWriter,
    load_search_projection,
)
from mail_agent.storage import Storage
from mail_agent.utils import atomic_write_bytes
from personal_assistant.cli_handlers.core import handle as handle_core_command
from personal_assistant.config import AssistantConfig
from personal_assistant.knowledge import KnowledgeIndexer
from personal_assistant.service import PersonalAssistant


def message(subject: str, message_id: str):
    raw = (
        "From: Firma <firma@example.test>\r\n"
        "To: Jan <jan@example.test>\r\n"
        f"Subject: {subject}\r\n"
        f"Message-ID: <{message_id}>\r\n"
        "Content-Type: text/plain; charset=utf-8\r\n\r\n"
        "Suchbarer lokaler Inhalt.\r\n"
    ).encode()
    return parse_eml(raw, Envelope(message_id), "INBOX")


class FakeKnowledgeStorage:
    def __init__(self) -> None:
        self.documents: dict[tuple[str, str], dict[str, object]] = {}
        self.sync: dict[tuple[str, str], dict[str, object]] = {}
        self.index_calls = 0

    def get_document(self, resource_id: str, source_id: str):
        return self.documents.get((resource_id, source_id))

    def index_document(self, **values: object) -> None:
        self.index_calls += 1
        self.documents[(str(values["resource_id"]), str(values["source_id"]))] = {
            "modified_at": values.get("modified_at", "")
        }

    def get_sync_state(self, resource_id: str, scope: str):
        return self.sync.get((resource_id, scope))

    def set_sync_state(
        self,
        resource_id: str,
        scope: str,
        *,
        cursor: str = "",
        etag: str = "",
        status: str,
        detail: str = "",
    ) -> None:
        self.sync[(resource_id, scope)] = {
            "cursor": cursor,
            "etag": etag,
            "status": status,
            "detail": detail,
        }


class MailProjectionM96Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "projection"
        self.config = AssistantConfig()
        self.config.search.mail_snapshot_dir = self.root
        self.config.search.mail_projection_max_age_seconds = 7200
        self.storage = FakeKnowledgeStorage()
        self.indexer = KnowledgeIndexer(self.config, self.storage)  # type: ignore[arg-type]

    def tearDown(self) -> None:
        os.chmod(self.root, 0o755) if self.root.exists() else None
        self.temp.cleanup()

    def test_complete_projection_reports_age_and_source_generation(self) -> None:
        writer = SearchSnapshotWriter(self.root)
        writer.write(message("Rechnung 1001", "one@example.test"))
        writer.write(message("Rechnung 1002", "two@example.test"))
        self.root.chmod(0o555)

        result = self.indexer.index_mail_snapshots()
        self.assertTrue(result["published"])
        self.assertEqual(result["seen"], 2)
        self.assertEqual(result["indexed"], 2)
        self.assertGreaterEqual(result["age_seconds"], 0)
        self.assertEqual(
            result["last_complete_source_generation"], result["source_generation"]
        )
        state = self.storage.sync[("mail-agent", "projection")]
        self.assertEqual(state["status"], "ok")
        self.assertEqual(state["cursor"], result["source_generation"])
        self.assertIn("generated_at", json.loads(str(state["detail"])))

    def test_mail_owner_refreshes_an_unchanged_complete_generation(self) -> None:
        writer = SearchSnapshotWriter(self.root)
        writer.write(message("Unveraendert", "same@example.test"))
        path = self.root / PROJECTION_MANIFEST
        manifest = json.loads(path.read_text(encoding="utf-8"))
        generation = manifest["source_generation"]
        manifest["generated_at"] = "2020-01-01T00:00:00+00:00"
        path.write_text(json.dumps(manifest), encoding="utf-8")

        writer.refresh()
        refreshed = load_search_projection(self.root, max_age_seconds=60)
        self.assertEqual(refreshed.generation, generation)
        self.assertLessEqual(refreshed.age_seconds, 1)

    def test_crash_before_manifest_publication_keeps_previous_generation(self) -> None:
        writer = SearchSnapshotWriter(self.root)
        writer.write(message("Erste Mail", "one@example.test"))
        before = (self.root / PROJECTION_MANIFEST).read_bytes()

        def interrupted(path: Path, data: bytes) -> None:
            if path.name == PROJECTION_MANIFEST:
                raise OSError("simulierter Crash vor Veroeffentlichung")
            atomic_write_bytes(path, data)

        with (
            patch("mail_agent.search_snapshot.atomic_write_bytes", side_effect=interrupted),
            self.assertRaisesRegex(OSError, "simulierter Crash"),
        ):
            writer.write(message("Zweite Mail", "two@example.test"))

        self.assertEqual((self.root / PROJECTION_MANIFEST).read_bytes(), before)
        projection = load_search_projection(self.root)
        self.assertEqual(len(projection.records), 1)
        self.assertEqual(projection.records[0][1]["stable_key"], "mid:one@example.test")

    def test_corrupt_projection_is_rejected_before_any_index_write(self) -> None:
        writer = SearchSnapshotWriter(self.root)
        writer.write(message("Rechnung", "one@example.test"))
        manifest = json.loads((self.root / PROJECTION_MANIFEST).read_text(encoding="utf-8"))
        record = self.root / manifest["records"][0]["filename"]
        record.write_text("{kaputt", encoding="utf-8")
        self.storage.sync[("mail-agent", "projection")] = {
            "cursor": "last-good-generation",
            "status": "ok",
        }

        result = self.indexer.index_mail_snapshots()
        self.assertFalse(result["published"])
        self.assertEqual(result["state"], "invalid")
        self.assertEqual(result["last_complete_source_generation"], "last-good-generation")
        self.assertEqual(self.storage.index_calls, 0)

    def test_stale_projection_is_not_indexed(self) -> None:
        SearchSnapshotWriter(self.root).write(message("Alt", "old@example.test"))
        path = self.root / PROJECTION_MANIFEST
        manifest = json.loads(path.read_text(encoding="utf-8"))
        manifest["generated_at"] = "2020-01-01T00:00:00+00:00"
        path.write_text(json.dumps(manifest), encoding="utf-8")

        result = self.indexer.index_mail_snapshots()
        self.assertFalse(result["published"])
        self.assertEqual(result["state"], "stale")
        self.assertEqual(self.storage.index_calls, 0)

    def test_sync_worker_never_opens_locked_mail_database(self) -> None:
        database = Path(self.temp.name) / "mail_agent.sqlite3"
        writer = sqlite3.connect(database)
        writer.execute("PRAGMA journal_mode=WAL")
        writer.execute("CREATE TABLE messages(stable_key TEXT)")
        writer.commit()
        writer.execute("BEGIN IMMEDIATE")
        writer.execute("INSERT INTO messages VALUES ('uncommitted')")

        class ProjectionOnlyIndexer:
            database_calls = 0

            @staticmethod
            def index_mail_snapshots():
                return {"published": True, "source_generation": "generation-1"}

            def index_mail_database(self, _path: Path):
                self.database_calls += 1
                raise AssertionError("Sync-Worker darf die Mail-Datenbank nicht oeffnen")

        service = object.__new__(PersonalAssistant)
        service.role = "sync-worker"
        service.indexer = ProjectionOnlyIndexer()
        try:
            result = PersonalAssistant.sync_mail(service)
        finally:
            writer.rollback()
            writer.close()
        self.assertEqual(service.indexer.database_calls, 0)
        self.assertEqual(result["database"]["mail_database_access"], "none")
        self.assertTrue(result["projection"]["published"])

    def test_sync_worker_discovers_nextcloud_without_writing_core_registry(self) -> None:
        service = object.__new__(PersonalAssistant)
        service.role = "sync-worker"
        service.config = SimpleNamespace(
            nextcloud=SimpleNamespace(enabled=True, allowed_file_roots=("Assistent",)),
            search=SimpleNamespace(nextcloud_max_items=100, nextcloud_max_depth=3),
        )
        discovery_calls: list[bool] = []

        def discover_nextcloud(*, persist: bool = True):
            discovery_calls.append(persist)
            return {"health": {"ok": True}, "persisted": persist}

        service.discover_nextcloud = discover_nextcloud
        service.nextcloud_discovery = SimpleNamespace(
            calendars=lambda: ["calendar"],
            addressbooks=lambda: ["addressbook"],
        )
        service.storage = object()
        service.indexer = object()
        service.nextcloud_files = SimpleNamespace(
            sync_index=lambda *_args, **_kwargs: {"files": 2, "errors": 0}
        )
        service.nextcloud_contacts = SimpleNamespace(
            sync_index=lambda *_args, **_kwargs: {"contacts": 3, "errors": 0}
        )
        service.nextcloud_calendar = SimpleNamespace(
            sync_index=lambda *_args, **_kwargs: {"events": 4, "errors": 0}
        )

        result = PersonalAssistant.sync_nextcloud(service)

        self.assertTrue(result["ok"])
        self.assertEqual(discovery_calls, [False])
        self.assertFalse(result["discovery"]["persisted"])

    def test_sync_worker_reports_nextcloud_failure_without_core_audit_write(self) -> None:
        class ReadOnlyCoreStorage:
            core_read_only = True

            @staticmethod
            def audit(*_args, **_kwargs):
                raise AssertionError("Sync-Worker darf nicht in die Core-Auditdatenbank schreiben")

        service = object.__new__(PersonalAssistant)
        service.role = "sync-worker"
        service.log = SimpleNamespace(warning=lambda *_args: None)
        service.storage = ReadOnlyCoreStorage()
        service.sync_mail = lambda: {"projection": {"published": True}}

        def failed_nextcloud_sync():
            raise OSError("simulierter Nextcloud-Fehler")

        service.sync_nextcloud = failed_nextcloud_sync

        result = PersonalAssistant.sync_all(service)

        self.assertFalse(result["ok"])
        self.assertEqual(result["nextcloud"]["error"], "simulierter Nextcloud-Fehler")

    def test_index_all_returns_degraded_exit_for_reported_sync_failure(self) -> None:
        emitted: list[dict[str, object]] = []
        assistant = SimpleNamespace(sync_all=lambda: {"ok": False, "nextcloud": {"error": "x"}})

        exit_code = handle_core_command(
            SimpleNamespace(command="index", index_command="all"),
            assistant,
            emitted.append,
        )

        self.assertEqual(exit_code, 1)
        self.assertFalse(emitted[0]["ok"])

    def test_missing_calendar_doctor_result_is_actionable_but_read_only(self) -> None:
        result = calendar_doctor_payload(
            ok=False,
            backend="nextcloud_skill",
            detail="Nicht bereit: Kalender 'calendar-old'",
            nextcloud_health={
                "selected_calendar_found": False,
                "calendar": "calendar-old",
            },
        )
        self.assertEqual(result["problem"], "configured-calendar-missing")
        self.assertEqual(result["resource"], "calendar-old")
        self.assertTrue(result["selection_required"])
        self.assertFalse(result["automatic_change"])
        next_step = result["allowed_next_step"]
        self.assertEqual(next_step["command"], "./scripts/assistant.sh calendar discover")
        self.assertEqual(next_step["mode"], "read")
        self.assertFalse(next_step["changes_configuration"])
        self.assertFalse(next_step["expands_permissions"])

    def test_invalid_appointment_data_routes_to_appointment_review_not_error(self) -> None:
        database = Path(self.temp.name) / "mail.sqlite3"
        mail_storage = Storage(database)
        agent = object.__new__(MailAgent)
        agent.config = type("Config", (), {})()
        agent.config.thresholds = type(
            "Thresholds",
            (),
            {"calendar": 0.90, "relevant": 0.90, "min_forward_importance": 7},
        )()
        agent.config.calendar = type(
            "Calendar", (), {"trust_feedback_count": 2, "approval_required": True}
        )()
        agent.config.folders = type(
            "Folders",
            (),
            {"appointment_review": "Agent/Termin-Pruefen", "error": "Agent/Fehler"},
        )()
        agent.dry_run = False
        agent.storage = mail_storage
        agent.rules = type(
            "Rules", (), {"is_trusted_sender": staticmethod(lambda *_args: False)}
        )()
        agent.calendar = type(
            "Calendar",
            (),
            {
                "process": staticmethod(
                    lambda *_args, **_kwargs: OperationResult(
                        False, "invalid-event", "Startdatum ist ungueltig"
                    )
                )
            },
        )()
        moved: dict[str, str] = {}

        def move(_message, _source, destination, status, detail="", **_kwargs):
            moved.update(destination=destination, status=status, detail=detail)
            return OperationResult(True, status, detail, destination=destination)

        agent._move = move
        item = message("Unklare Termindaten", "calendar-invalid@example.test")
        classification = Classification(
            "appointment", 0.99, 8, False, "Termin erkannt", source="ollama"
        )
        try:
            result = agent._route(item, classification, "INBOX")
            row = mail_storage.get_message(item.stable_key)
        finally:
            mail_storage.close()
        self.assertTrue(result.ok)
        self.assertEqual(moved["destination"], "Agent/Termin-Pruefen")
        self.assertEqual(moved["status"], "appointment-review")
        self.assertEqual(row["review_reason"], ReviewReason.APPOINTMENT_REVIEW.value)
        self.assertIn("Startdatum", moved["detail"])


if __name__ == "__main__":
    unittest.main()
