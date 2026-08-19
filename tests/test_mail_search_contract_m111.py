from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from mail_agent.models import Envelope, ParsedMessage
from mail_agent.parser import parse_eml
from mail_agent.search_projection_v2 import (
    PartitionedSearchSnapshotWriter,
    ProjectionOccurrenceInput,
    republish_v1_projection,
)
from mail_agent.search_snapshot import SearchSnapshotWriter
from personal_assistant.config import AssistantConfig
from personal_assistant.contracts.mail_projection import (
    PROJECTION_MANIFEST,
    SearchProjectionError,
    canonical_projection_generation,
    load_search_projection,
)
from personal_assistant.contracts.mail_projection_v2 import (
    MailLocator,
    canonical_root_generation,
    content_identity,
    locator_identity,
    occurrence_identity,
    sha256_bytes,
)
from personal_assistant.knowledge import KnowledgeIndexer
from personal_assistant.storage import (
    CORE_SCHEMA_VERSION,
    KNOWLEDGE_SCHEMA_VERSION,
    SCHEMA_VERSION,
    AssistantStorage,
)

STAMP = "2026-08-19T12:00:00+00:00"


def parsed_message(
    message_id: str,
    *,
    mailbox_id: str = "1",
    folder: str = "INBOX",
    body: str = "Synthetischer Suchinhalt.",
) -> ParsedMessage:
    header = f"Message-ID: <{message_id}>\r\n" if message_id else ""
    raw = (
        "From: Synthetic Sender <sender@example.invalid>\r\n"
        "To: Synthetic Owner <owner@example.invalid>\r\n"
        "Subject: Synthetischer Vertrag\r\n"
        f"{header}"
        "Date: Wed, 19 Aug 2026 12:00:00 +0000\r\n"
        "Content-Type: text/plain; charset=utf-8\r\n\r\n"
        f"{body}\r\n"
    ).encode()
    return parse_eml(raw, Envelope(mailbox_id), folder)


def locator(
    folder_id: str,
    folder_name: str,
    mailbox_id: str,
    *,
    uidvalidity: str = "",
    uid: str = "",
) -> MailLocator:
    return MailLocator(
        resource_id="mail-agent",
        folder_id=folder_id,
        folder_name=folder_name,
        mailbox_id=mailbox_id,
        uidvalidity=uidvalidity,
        uid=uid,
        observed_at=STAMP,
    )


def publish_complete_v2(root: Path, *, two_partitions: bool = False):
    writer = PartitionedSearchSnapshotWriter(root)
    first_message = parsed_message("one@example.invalid")
    first = writer.publish_partition(
        partition_id="folder-inbox",
        folder_id="folder-inbox",
        folder_name="INBOX",
        occurrences=[
            ProjectionOccurrenceInput(
                first_message,
                (locator("folder-inbox", "INBOX", "1"),),
            )
        ],
        generated_at=STAMP,
        complete=True,
        authoritative=True,
    )
    partitions = [first]
    if two_partitions:
        second = writer.publish_partition(
            partition_id="folder-archive",
            folder_id="folder-archive",
            folder_name="Archive",
            occurrences=[
                ProjectionOccurrenceInput(
                    first_message,
                    (locator("folder-archive", "Archive", "9"),),
                )
            ],
            generated_at=STAMP,
            complete=True,
            authoritative=True,
        )
        partitions.append(second)
    writer.publish_root(
        partitions,
        expected_partition_ids=[str(item["partition_id"]) for item in partitions],
        complete=True,
        authoritative=True,
        generated_at=STAMP,
    )
    return writer, partitions


class ProjectionGoldenM111Tests(unittest.TestCase):
    def test_v1_and_v2_are_both_readable(self) -> None:
        with tempfile.TemporaryDirectory(prefix="m111-golden-") as temp:
            root = Path(temp)
            v1 = root / "v1"
            SearchSnapshotWriter(v1).write(parsed_message("v1@example.invalid"))  # type: ignore[arg-type]
            loaded_v1 = load_search_projection(v1)
            self.assertEqual(loaded_v1.schema, 1)
            self.assertFalse(loaded_v1.coverage["account_coverage_proven"])

            v2 = root / "v2"
            publish_complete_v2(v2)
            loaded_v2 = load_search_projection(v2)
            self.assertEqual(loaded_v2.schema, 2)
            self.assertTrue(loaded_v2.complete)
            self.assertEqual(len(loaded_v2.partitions), 1)
            self.assertEqual(len(loaded_v2.records), 1)

    def test_unknown_future_version_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory(prefix="m111-future-") as temp:
            root = Path(temp)
            root.joinpath(PROJECTION_MANIFEST).write_text(
                json.dumps({"schema": 99}),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(SearchProjectionError, "Unbekannte.*99"):
                load_search_projection(root)

    def test_missing_partition_and_wrong_digest_are_rejected(self) -> None:
        for failure in ("missing", "digest"):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory(
                prefix="m111-corrupt-"
            ) as temp:
                root = Path(temp)
                _writer, partitions = publish_complete_v2(root)
                reference = partitions[0]
                if failure == "missing":
                    root.joinpath(str(reference["filename"])).unlink()
                else:
                    manifest = json.loads(
                        root.joinpath(PROJECTION_MANIFEST).read_text(encoding="utf-8")
                    )
                    manifest["partitions"][0]["sha256"] = "0" * 64
                    manifest["root_generation"] = canonical_root_generation(
                        complete=True,
                        coverage=manifest["coverage"],
                        partitions=manifest["partitions"],
                    )
                    root.joinpath(PROJECTION_MANIFEST).write_text(
                        json.dumps(manifest),
                        encoding="utf-8",
                    )
                with self.assertRaises(SearchProjectionError):
                    load_search_projection(root)

    def test_v1_duplicate_stable_key_and_unsafe_filename_are_rejected(self) -> None:
        for failure in ("duplicate", "unsafe"):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory(
                prefix="m111-v1-invalid-"
            ) as temp:
                root = Path(temp)
                SearchSnapshotWriter(root).write(parsed_message("one@example.invalid"))  # type: ignore[arg-type]
                path = root / PROJECTION_MANIFEST
                manifest = json.loads(path.read_text(encoding="utf-8"))
                if failure == "duplicate":
                    manifest["records"].append(dict(manifest["records"][0]))
                    manifest["record_count"] = 2
                else:
                    manifest["records"][0]["filename"] = "../escape.json"
                    manifest["source_generation"] = canonical_projection_generation(
                        manifest["records"]
                    )
                path.write_text(json.dumps(manifest), encoding="utf-8")
                with self.assertRaises(SearchProjectionError):
                    load_search_projection(root)

    def test_duplicate_occurrence_across_partitions_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory(prefix="m111-duplicate-occurrence-") as temp:
            root = Path(temp)
            writer, partitions = publish_complete_v2(root)
            duplicate = dict(partitions[0])
            duplicate["partition_id"] = "folder-duplicate"
            partition_payload = json.loads(
                root.joinpath(str(partitions[0]["filename"])).read_text(encoding="utf-8")
            )
            partition_payload["partition_id"] = "folder-duplicate"
            from personal_assistant.contracts.mail_projection_v2 import (
                canonical_json_bytes,
                canonical_partition_generation,
            )

            partition_payload["partition_generation"] = canonical_partition_generation(
                partition_id="folder-duplicate",
                resource_id=partition_payload["resource_id"],
                folder_id=partition_payload["folder_id"],
                complete=True,
                authoritative=True,
                records=partition_payload["records"],
                tombstones=[],
            )
            data = canonical_json_bytes(partition_payload)
            duplicate_path = root / "partition-duplicate.json"
            duplicate_path.write_bytes(data)
            duplicate.update(
                {
                    "filename": duplicate_path.name,
                    "sha256": sha256_bytes(data),
                    "partition_generation": partition_payload["partition_generation"],
                    "complete": True,
                    "authoritative": True,
                }
            )
            writer.publish_root(
                [partitions[0], duplicate],
                expected_partition_ids=["folder-inbox", "folder-duplicate"],
                complete=True,
                authoritative=True,
                generated_at=STAMP,
            )
            with self.assertRaisesRegex(SearchProjectionError, "Occurrence mehrfach"):
                load_search_projection(root)


class MailIdentityM111Tests(unittest.TestCase):
    def test_same_message_id_with_different_raw_content_is_not_merged(self) -> None:
        first = parsed_message("same@example.invalid", body="Version eins")
        second = parsed_message("same@example.invalid", body="Version zwei")
        first_sha = sha256_bytes(first.raw)  # type: ignore[attr-defined]
        second_sha = sha256_bytes(second.raw)  # type: ignore[attr-defined]
        self.assertNotEqual(
            content_identity("mail-agent", first_sha),
            content_identity("mail-agent", second_sha),
        )

    def test_identical_raw_content_in_two_folders_reuses_content(self) -> None:
        with tempfile.TemporaryDirectory(prefix="m111-copy-") as temp:
            root = Path(temp)
            publish_complete_v2(root, two_partitions=True)
            projection = load_search_projection(root)
            self.assertEqual(len(projection.records), 1)
            metadata = projection.records[0][1]["metadata"]
            self.assertEqual(len(metadata["occurrence_ids"]), 2)
            self.assertEqual(len(metadata["locators"]), 2)
            content_files = list(root.glob("content-*.json"))
            self.assertEqual(len(content_files), 1)

    def test_missing_message_id_still_has_content_and_occurrence_identity(self) -> None:
        message = parsed_message("")
        raw_sha256 = sha256_bytes(message.raw)  # type: ignore[attr-defined]
        current_locator = locator("folder-inbox", "INBOX", "3")
        self.assertTrue(content_identity("mail-agent", raw_sha256).startswith("content:"))
        self.assertTrue(
            occurrence_identity(current_locator, raw_sha256).startswith("occurrence:")
        )

    def test_uidvalidity_reset_changes_occurrence_but_folder_rename_does_not(self) -> None:
        raw_sha256 = sha256_bytes(b"synthetic")
        before = locator(
            "folder-stable",
            "INBOX",
            "ignored",
            uidvalidity="10",
            uid="42",
        )
        renamed = locator(
            "folder-stable",
            "Archive/Renamed",
            "ignored",
            uidvalidity="10",
            uid="42",
        )
        reset = locator(
            "folder-stable",
            "Archive/Renamed",
            "ignored",
            uidvalidity="11",
            uid="42",
        )
        self.assertEqual(locator_identity(before), locator_identity(renamed))
        self.assertEqual(
            occurrence_identity(before, raw_sha256),
            occurrence_identity(renamed, raw_sha256),
        )
        self.assertNotEqual(
            occurrence_identity(before, raw_sha256),
            occurrence_identity(reset, raw_sha256),
        )

    def test_partial_copy_delete_overlap_preserves_both_occurrences(self) -> None:
        with tempfile.TemporaryDirectory(prefix="m111-overlap-") as temp:
            projection_root = Path(temp)
            publish_complete_v2(projection_root, two_partitions=True)
            projection = load_search_projection(projection_root)
            occurrence_ids = projection.records[0][1]["metadata"]["occurrence_ids"]
            self.assertEqual(len(set(occurrence_ids)), 2)


class ProjectionSafetyM111Tests(unittest.TestCase):
    def test_tombstone_and_global_complete_require_authoritative_coverage(self) -> None:
        with tempfile.TemporaryDirectory(prefix="m111-partial-") as temp:
            root = Path(temp)
            writer = PartitionedSearchSnapshotWriter(root)
            item = ProjectionOccurrenceInput(
                parsed_message("one@example.invalid"),
                (locator("folder-inbox", "INBOX", "1"),),
            )
            with self.assertRaisesRegex(SearchProjectionError, "Tombstones"):
                writer.publish_partition(
                    partition_id="folder-inbox",
                    folder_id="folder-inbox",
                    folder_name="INBOX",
                    occurrences=[item],
                    generated_at=STAMP,
                    complete=False,
                    authoritative=False,
                    tombstones=[
                        {"occurrence_id": "occurrence:old", "tombstoned_at": STAMP}
                    ],
                )
            partial = writer.publish_partition(
                partition_id="folder-inbox",
                folder_id="folder-inbox",
                folder_name="INBOX",
                occurrences=[item],
                generated_at=STAMP,
                complete=False,
                authoritative=False,
            )
            with self.assertRaisesRegex(
                SearchProjectionError,
                "Coverage|Vollstaendigkeit",
            ):
                writer.publish_root(
                    [partial],
                    expected_partition_ids=["folder-inbox", "folder-missing"],
                    complete=True,
                    authoritative=True,
                    generated_at=STAMP,
                )
            writer.publish_root(
                [partial],
                expected_partition_ids=["folder-inbox", "folder-missing"],
                complete=False,
                authoritative=False,
                incomplete_partition_ids=["folder-inbox", "folder-missing"],
                incomplete_reasons={"folder-missing": "synthetic failure"},
                generated_at=STAMP,
            )
            projection = load_search_projection(root)
            self.assertFalse(projection.complete)
            self.assertIn("folder-missing", projection.coverage["incomplete_partition_ids"])

    def test_partial_root_must_account_for_every_expected_partition_exactly(self) -> None:
        with tempfile.TemporaryDirectory(prefix="m111-coverage-") as temp:
            root = Path(temp)
            writer = PartitionedSearchSnapshotWriter(root)
            partial = writer.publish_partition(
                partition_id="folder-inbox",
                folder_id="folder-inbox",
                folder_name="INBOX",
                occurrences=[],
                generated_at=STAMP,
                complete=False,
                authoritative=False,
            )
            with self.assertRaisesRegex(SearchProjectionError, "Coverage"):
                writer.publish_root(
                    [partial],
                    expected_partition_ids=["folder-inbox", "folder-missing"],
                    complete=False,
                    authoritative=False,
                    incomplete_partition_ids=["folder-missing"],
                    generated_at=STAMP,
                )

    def test_crash_before_record_partition_or_root_replace_keeps_last_root(self) -> None:
        for prefix in ("content-", "partition-", PROJECTION_MANIFEST):
            with self.subTest(prefix=prefix), tempfile.TemporaryDirectory(
                prefix="m111-crash-"
            ) as temp:
                root = Path(temp)
                writer, old_partitions = publish_complete_v2(root)
                before = root.joinpath(PROJECTION_MANIFEST).read_bytes()
                original = __import__(
                    "mail_agent.search_projection_v2",
                    fromlist=["atomic_write_bytes"],
                ).atomic_write_bytes

                def interrupted(
                    path: Path,
                    data: bytes,
                    *,
                    current_prefix: str = prefix,
                    write_bytes=original,
                ) -> None:
                    if path.name.startswith(current_prefix):
                        raise OSError(f"synthetic crash before {current_prefix}")
                    write_bytes(path, data)

                with patch(
                    "mail_agent.search_projection_v2.atomic_write_bytes",
                    side_effect=interrupted,
                ):
                    if prefix == PROJECTION_MANIFEST:
                        with self.assertRaises(OSError):
                            writer.publish_root(
                                old_partitions,
                                expected_partition_ids=["folder-inbox"],
                                complete=True,
                                authoritative=True,
                                generated_at="2026-08-19T13:00:00+00:00",
                            )
                    else:
                        with self.assertRaises(OSError):
                            writer.publish_partition(
                                partition_id="folder-new",
                                folder_id="folder-new",
                                folder_name="New",
                                occurrences=[
                                    ProjectionOccurrenceInput(
                                        parsed_message("new@example.invalid"),  # type: ignore[arg-type]
                                        (locator("folder-new", "New", "2"),),
                                    )
                                ],
                                generated_at="2026-08-19T13:00:00+00:00",
                                complete=True,
                                authoritative=True,
                            )
                self.assertEqual(root.joinpath(PROJECTION_MANIFEST).read_bytes(), before)
                self.assertTrue(load_search_projection(root).complete)

    def test_incomplete_v2_is_rejected_before_knowledge_write(self) -> None:
        class FakeStorage:
            index_calls = 0
            sync: dict[tuple[str, str], dict[str, str]] = {}

            def get_sync_state(self, resource_id: str, scope: str):
                return self.sync.get((resource_id, scope))

            def set_sync_state(self, resource_id: str, scope: str, **values: str) -> None:
                self.sync[(resource_id, scope)] = values

            def index_document(self, **_values: object) -> None:
                self.index_calls += 1

        with tempfile.TemporaryDirectory(prefix="m111-index-partial-") as temp:
            root = Path(temp)
            writer = PartitionedSearchSnapshotWriter(root)
            partition = writer.publish_partition(
                partition_id="folder-inbox",
                folder_id="folder-inbox",
                folder_name="INBOX",
                occurrences=[],
                generated_at=STAMP,
                complete=False,
                authoritative=False,
            )
            writer.publish_root(
                [partition],
                expected_partition_ids=["folder-inbox"],
                complete=False,
                authoritative=False,
                incomplete_partition_ids=["folder-inbox"],
                generated_at=STAMP,
            )
            config = AssistantConfig()
            config.search.mail_snapshot_dir = root
            config.search.mail_projection_max_age_seconds = 10**9
            storage = FakeStorage()
            indexer = KnowledgeIndexer(config, storage)  # type: ignore[arg-type]
            result = indexer.index_mail_snapshots()
            self.assertEqual(result["state"], "partial")
            self.assertEqual(storage.index_calls, 0)


class ProjectionMigrationM111Tests(unittest.TestCase):
    def test_v1_republication_is_repeatable_additive_and_incomplete(self) -> None:
        with tempfile.TemporaryDirectory(prefix="m111-republish-") as temp:
            root = Path(temp)
            source = root / "v1"
            target = root / "v2"
            writer = SearchSnapshotWriter(source)
            writer.write(parsed_message("one@example.invalid"))
            writer.write(parsed_message("two@example.invalid", folder="Archive"))
            source_before = source.joinpath(PROJECTION_MANIFEST).read_bytes()

            republish_v1_projection(source, target)
            first = target.joinpath(PROJECTION_MANIFEST).read_bytes()
            projection = load_search_projection(target)
            self.assertEqual(projection.schema, 2)
            self.assertFalse(projection.complete)
            self.assertEqual(len(projection.records), 2)
            self.assertEqual(source.joinpath(PROJECTION_MANIFEST).read_bytes(), source_before)

            republish_v1_projection(source, target)
            self.assertEqual(target.joinpath(PROJECTION_MANIFEST).read_bytes(), first)
            self.assertEqual(len(load_search_projection(target).records), 2)

    def test_split_runtime_keeps_core_schema_stable_and_migrates_only_knowledge(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory(prefix="m111-split-schema-") as temp:
            root = Path(temp)
            core = root / "core.sqlite3"
            knowledge_root = root / "knowledge"
            with patch.dict(
                os.environ,
                {"OPENCLAW_KNOWLEDGE_DATA_DIR": str(knowledge_root)},
            ):
                storage = AssistantStorage(core)
                try:
                    self.assertEqual(
                        storage.connection.execute("PRAGMA user_version").fetchone()[0],
                        CORE_SCHEMA_VERSION,
                    )
                    self.assertEqual(
                        storage.knowledge_connection.execute(
                            "PRAGMA user_version"
                        ).fetchone()[0],
                        KNOWLEDGE_SCHEMA_VERSION,
                    )
                finally:
                    storage.close()

    def test_v1_knowledge_database_migration_preserves_rows_and_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory(prefix="m111-knowledge-") as temp:
            root = Path(temp)
            knowledge_root = root / "knowledge"
            knowledge_root.mkdir()
            knowledge = knowledge_root / "knowledge.sqlite3"
            seed = sqlite3.connect(knowledge)
            seed.executescript(
                """
                CREATE TABLE sync_state (
                    resource_id TEXT NOT NULL,
                    scope TEXT NOT NULL,
                    cursor TEXT,
                    etag TEXT,
                    synced_at TEXT,
                    status TEXT NOT NULL,
                    detail TEXT,
                    PRIMARY KEY(resource_id, scope)
                );
                CREATE TABLE documents (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_type TEXT NOT NULL,
                    resource_id TEXT NOT NULL,
                    source_id TEXT NOT NULL,
                    uri TEXT NOT NULL,
                    title TEXT NOT NULL,
                    mime_type TEXT,
                    modified_at TEXT,
                    etag TEXT,
                    sha256 TEXT,
                    metadata_json TEXT NOT NULL,
                    indexed_at TEXT NOT NULL,
                    UNIQUE(resource_id, source_id)
                );
                CREATE TABLE chunks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
                    chunk_index INTEGER NOT NULL,
                    text TEXT NOT NULL,
                    UNIQUE(document_id, chunk_index)
                );
                INSERT INTO sync_state VALUES
                    ('mail-agent','projection','generation-v1','generation-v1',
                     '2026-08-18T00:00:00+00:00','ok','preserve-history');
                INSERT INTO documents VALUES
                    (1,'email','mail-agent','mid:legacy','mail-agent://mid:legacy',
                     'Legacy title','message/rfc822','2026-08-18','etag-v1','abc',
                     '{"source_folder":"INBOX"}','2026-08-18T00:00:00+00:00');
                INSERT INTO chunks VALUES (1,1,0,'Legacy searchable text');
                PRAGMA user_version=1;
                """
            )
            seed.commit()
            seed.close()

            with patch.dict(os.environ, {"OPENCLAW_KNOWLEDGE_DATA_DIR": str(knowledge_root)}):
                first = AssistantStorage(root / "assistant.sqlite3")
                try:
                    self.assertEqual(
                        first.knowledge_connection.execute("PRAGMA user_version").fetchone()[0],
                        SCHEMA_VERSION,
                    )
                    row = first.get_document("mail-agent", "mid:legacy")
                    assert row is not None
                    self.assertEqual(row["source_status"], "legacy")
                    self.assertEqual(row["title"], "Legacy title")
                    self.assertEqual(
                        first.get_sync_state("mail-agent", "projection")["detail"],
                        "preserve-history",
                    )
                    tables = {
                        item[0]
                        for item in first.knowledge_connection.execute(
                            "SELECT name FROM sqlite_master WHERE type='table'"
                        ).fetchall()
                    }
                    self.assertTrue(
                        {
                            "mail_search_contents",
                            "mail_search_occurrences",
                            "mail_search_locators",
                            "mail_search_tags",
                            "mail_search_thread_edges",
                            "mail_search_generations",
                        }.issubset(tables)
                    )
                    self.assertEqual(first.integrity(), "ok")
                finally:
                    first.close()
                second = AssistantStorage(root / "assistant.sqlite3")
                try:
                    self.assertEqual(
                        second.knowledge_connection.execute(
                            "SELECT COUNT(*) FROM documents"
                        ).fetchone()[0],
                        1,
                    )
                    self.assertEqual(
                        second.knowledge_connection.execute(
                            "SELECT COUNT(*) FROM chunks"
                        ).fetchone()[0],
                        1,
                    )
                    self.assertEqual(second.integrity(), "ok")
                finally:
                    second.close()


class ThreadHeaderM111Tests(unittest.TestCase):
    def test_malformed_and_large_thread_headers_are_tolerant_and_bounded(self) -> None:
        reply_ids = " ".join(f"<reply-{index}@example.invalid>" for index in range(30))
        reference_ids = " ".join(
            f"<reference-{index}@example.invalid>" for index in range(70)
        )
        raw = (
            "From: Sender <sender@example.invalid>\r\n"
            "To: Owner <owner@example.invalid>\r\n"
            "Subject: Thread test\r\n"
            "Message-ID: <thread@example.invalid>\r\n"
            f"In-Reply-To: malformed {reply_ids} <>\r\n"
            f"References: {reference_ids} malformed-without-at\r\n"
            "\r\nBody"
        ).encode()
        message = parse_eml(raw, Envelope("1"), "INBOX")
        self.assertEqual(len(message.in_reply_to), 20)
        self.assertEqual(len(message.references), 50)
        self.assertEqual(message.in_reply_to[0], "reply-0@example.invalid")
        self.assertEqual(message.references[-1], "reference-49@example.invalid")


if __name__ == "__main__":
    unittest.main()
