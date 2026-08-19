from __future__ import annotations

import json
import sys
import tempfile
import unittest
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import benchmark_mail_search_m110 as baseline  # noqa: E402

from personal_assistant.storage import AssistantStorage  # noqa: E402


@contextmanager
def fixture_runtime() -> Iterator[
    tuple[
        dict[str, object],
        list[baseline.SyntheticMail],
        baseline.MailMoveService,
        baseline.FakeImapClient,
        AssistantStorage,
    ]
]:
    corpus = baseline.load_corpus()
    messages = baseline.materialize_messages(corpus)
    with tempfile.TemporaryDirectory(prefix="m110-test-") as temp:
        service, client, storage, _stats = baseline.build_runtime(
            Path(temp),
            corpus,
            messages,
        )
        try:
            yield corpus, messages, service, client, storage
        finally:
            storage.close()


class MailSearchCorpusM110Tests(unittest.TestCase):
    def test_corpus_is_synthetic_and_uses_only_reserved_addresses(self) -> None:
        corpus = baseline.load_corpus()
        self.assertEqual(corpus["corpus_id"], "m110-synthetic-mail-search-v1")
        self.assertTrue(corpus["privacy"]["synthetic"])
        self.assertFalse(corpus["privacy"]["productive_data"])
        self.assertEqual(len(corpus["messages"]), 13)
        self.assertEqual(len(corpus["queries"]), 13)
        for message in corpus["messages"]:
            self.assertTrue(str(message["message_id"]).endswith("@example.invalid"))
            self.assertTrue(str(message["from_addr"]).endswith(".invalid"))
            self.assertTrue(all(str(value).endswith(".invalid") for value in message["to"]))
        raw = baseline.DEFAULT_CORPUS.read_text(encoding="utf-8")
        self.assertNotIn("/srv/openclaw", raw)
        self.assertNotIn("BEGIN PRIVATE KEY", raw)

    def test_generated_eml_roundtrip_preserves_synthetic_search_fields(self) -> None:
        corpus = baseline.load_corpus()
        messages = baseline.materialize_messages(corpus)
        by_id = {item.fixture_id: item for item in messages}
        invoice = by_id["pump-invoice-en"].parsed
        self.assertIn("ZX-2048", invoice.subject)
        self.assertIn("480.00 EUR", invoice.body_text)
        self.assertEqual(invoice.sender_addr, "billing@river-pump.example.invalid")
        self.assertEqual(invoice.attachments[0].filename, "pump-repair-ZX-2048.pdf")
        self.assertTrue(all(item.parsed.raw.startswith(b"From:") for item in messages))


class ServerSearchCharacterizationM110Tests(unittest.TestCase):
    def test_search_queries_every_readable_folder_and_applies_global_limit(self) -> None:
        with fixture_runtime() as (corpus, _messages, service, client, _storage):
            client.reset_counters()
            result = service.search_messages("Projekt", limit=2)
            self.assertEqual(client.folder_list_calls, 1)
            self.assertEqual(client.search_calls, len(corpus["folders"]))
            self.assertEqual(result["count"], 2)
            self.assertTrue(result["results_may_be_truncated"])
            self.assertTrue(result["limited_folders"])

    def test_null_result_is_complete_but_one_folder_error_is_not(self) -> None:
        with fixture_runtime() as (_corpus, _messages, service, client, _storage):
            empty = service.search_messages("Polarstation", limit=10)
            self.assertTrue(empty["complete"])
            self.assertEqual(empty["messages"], [])
            self.assertFalse(empty["results_may_be_truncated"])

            client.error_folders.add("Spamverdacht")
            partial = service.search_messages("Projekt", limit=10)
            self.assertFalse(partial["complete"])
            self.assertEqual(partial["failed_folders"], 1)
            self.assertEqual(partial["folder_errors"][0]["folder"], "Spamverdacht")

    def test_current_free_text_search_does_not_support_a_structured_date_range(self) -> None:
        with fixture_runtime() as (_corpus, _messages, service, _client, storage):
            server = service.search_messages("2026-04-09 2026-04-10", limit=10)
            local = storage.search(
                "2026-04-09 2026-04-10",
                limit=10,
                source_type="email",
            )
            self.assertEqual(server["messages"], [])
            self.assertEqual(local, [])

    def test_all_folder_errors_fail_closed(self) -> None:
        with fixture_runtime() as (corpus, _messages, service, client, _storage):
            client.error_folders.update(corpus["folders"])
            with self.assertRaisesRegex(RuntimeError, "allen Ordnern fehlgeschlagen"):
                service.search_messages("Projekt", limit=10)

    def test_external_move_is_visible_only_through_another_full_server_search(self) -> None:
        with fixture_runtime() as (corpus, messages, service, _client, storage):
            client = baseline.FakeImapClient(list(corpus["folders"]), messages)
            client.move_occurrence(
                "aurora-handover-de",
                "aurora-handover-moved",
                "Archiv/2025",
            )
            service._client_override = client
            server = service.search_messages("Übergabeprotokoll", limit=10)
            local = storage.search("Übergabeprotokoll", limit=10, source_type="email")
            self.assertEqual(server["messages"][0]["folder"], "Archiv/2025")
            self.assertEqual(client.search_calls, len(corpus["folders"]))
            self.assertEqual(local[0].metadata["source_folder"], "Gesendet")
            self.assertNotIn("mailbox_id", local[0].metadata)


class LocalFtsCharacterizationM110Tests(unittest.TestCase):
    def test_matching_chunks_of_one_mail_are_returned_as_duplicates(self) -> None:
        with tempfile.TemporaryDirectory(prefix="m110-fts-") as temp:
            storage = AssistantStorage(Path(temp) / "assistant.sqlite3")
            try:
                storage.index_document(
                    source_type="email",
                    resource_id="mail-agent",
                    source_id="duplicate-mail",
                    uri="mail-agent://duplicate-mail",
                    title="Synthetic duplicate",
                    chunks=["Nadelwort im ersten Abschnitt", "Nadelwort im zweiten Abschnitt"],
                )
                results = storage.search("Nadelwort", limit=10, source_type="email")
                self.assertEqual([item.source_id for item in results], [
                    "duplicate-mail",
                    "duplicate-mail",
                ])
            finally:
                storage.close()

    def test_invalid_fts_query_falls_back_to_literal_like_search(self) -> None:
        with tempfile.TemporaryDirectory(prefix="m110-fts-") as temp:
            storage = AssistantStorage(Path(temp) / "assistant.sqlite3")
            try:
                storage.index_document(
                    source_type="email",
                    resource_id="mail-agent",
                    source_id="literal-mail",
                    uri="mail-agent://literal-mail",
                    title="Synthetic syntax",
                    chunks=['Der Text enthaelt literal "unclosed als Zeichenfolge.'],
                )
                results = storage.search('"unclosed', limit=10, source_type="email")
                self.assertEqual([item.source_id for item in results], ["literal-mail"])
            finally:
                storage.close()

    def test_snippet_is_chunk_prefix_instead_of_query_centered(self) -> None:
        with tempfile.TemporaryDirectory(prefix="m110-fts-") as temp:
            storage = AssistantStorage(Path(temp) / "assistant.sqlite3")
            try:
                prefix = "synthetischer anfang " * 30
                storage.index_document(
                    source_type="email",
                    resource_id="mail-agent",
                    source_id="late-match",
                    uri="mail-agent://late-match",
                    title="Synthetic long body",
                    chunks=[prefix + "Zielbegriff"],
                )
                result = storage.search("Zielbegriff", limit=10, source_type="email")[0]
                self.assertEqual(len(result.snippet), 500)
                self.assertNotIn("Zielbegriff", result.snippet)
            finally:
                storage.close()

    def test_current_projection_has_folder_metadata_but_no_live_locator(self) -> None:
        with fixture_runtime() as (_corpus, _messages, _service, _client, storage):
            result = storage.search("Tankprüfung", limit=10, source_type="email")[0]
            self.assertIn("source_folder", result.metadata)
            self.assertNotIn("mailbox_id", result.metadata)
            self.assertNotIn("uid", result.metadata)
            self.assertNotIn("uidvalidity", result.metadata)


class MailSearchBaselineReportM110Tests(unittest.TestCase):
    def test_report_measures_quality_coverage_resources_and_known_gaps(self) -> None:
        report = baseline.build_report(samples=3)
        self.assertTrue(report["ok"])
        self.assertEqual(report["inventory"]["folders"], 5)
        self.assertEqual(report["inventory"]["messages"], 13)
        self.assertEqual(report["inventory"]["projection_records"], 11)
        self.assertEqual(report["inventory"]["locatorless_documents"], 11)
        self.assertEqual(report["search"]["server"]["quality"]["evaluated_queries"], 13)
        self.assertEqual(
            report["search"]["server"]["quality_by_kind"]["lexical"]["mean_recall_at_10"],
            1.0,
        )
        self.assertEqual(
            report["search"]["local_fts"]["quality_by_kind"]["semantic"]["mean_recall_at_10"],
            0.0,
        )
        self.assertGreater(report["resources"]["python_peak_allocated_bytes"], 0)
        self.assertIsNotNone(report["inventory"]["index_age_seconds"])
        self.assertGreaterEqual(report["inventory"]["index_age_seconds"], 0)
        self.assertIn("no-live-mail-locator", report["known_gaps"])
        self.assertIn("no-structured-date-range-filter", report["known_gaps"])

    def test_report_characterizes_changes_without_claiming_incremental_support(self) -> None:
        report = baseline.build_report(samples=3)
        changes = report["change_tracking"]
        self.assertFalse(changes["implemented"])
        self.assertFalse(changes["external_move"]["locator_updated_locally"])
        self.assertEqual(changes["external_move"]["server_folders"], ["Archiv/2025"])
        self.assertEqual(changes["external_move"]["local_source_folders"], ["Gesendet"])
        self.assertEqual(changes["external_move"]["backend"]["folder_search_calls"], 5)
        self.assertFalse(changes["uidvalidity_reset"]["supported"])

    def test_report_contains_no_query_text_addresses_or_mail_bodies(self) -> None:
        corpus = baseline.load_corpus()
        report = baseline.build_report(samples=3)

        def string_values(value: object) -> list[str]:
            if isinstance(value, str):
                return [value]
            if isinstance(value, dict):
                return [item for child in value.values() for item in string_values(child)]
            if isinstance(value, list):
                return [item for child in value for item in string_values(child)]
            return []

        values = string_values(report)
        for query in corpus["queries"]:
            self.assertNotIn(str(query["query"]), values)
        for message in corpus["messages"]:
            self.assertNotIn(str(message["from_addr"]), values)
            self.assertNotIn(str(message["body"]), values)
        self.assertTrue(report["privacy"]["synthetic_only"])
        self.assertFalse(report["privacy"]["productive_data_read"])

    def test_atomic_report_output_is_valid_json(self) -> None:
        with tempfile.TemporaryDirectory(prefix="m110-report-") as temp:
            target = Path(temp) / "baseline.json"
            report = baseline.build_report(samples=3)
            baseline.atomic_write(target, report)
            loaded = json.loads(target.read_text(encoding="utf-8"))
            self.assertEqual(loaded["milestone"], "M11.0")
            self.assertEqual(loaded["schema_version"], 1)


if __name__ == "__main__":
    unittest.main()
