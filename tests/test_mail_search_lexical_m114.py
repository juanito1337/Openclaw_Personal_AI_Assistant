from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from mail_agent.models import Classification, InvoiceSignal, ParsedMessage
from mail_agent.search_projection_v2 import (
    PartitionedSearchSnapshotWriter,
    ProjectionOccurrenceInput,
)
from mail_agent.search_tags import LocalMailTagResolver
from mail_agent.storage import Storage as MailStorage
from personal_assistant.cli import parser as assistant_parser
from personal_assistant.cli_handlers.mail import handle as handle_mail
from personal_assistant.contracts.mail_projection import load_search_projection
from personal_assistant.contracts.mail_projection_v2 import MailLocator
from personal_assistant.mail_search import (
    BM25_WEIGHTS,
    TAG_NAMESPACES,
    MailSearchFilters,
    build_mail_tags,
    parse_mail_query,
    parse_tag_filter,
)
from personal_assistant.storage import KNOWLEDGE_SCHEMA_VERSION, AssistantStorage
from personal_assistant.tool_catalog import TOOLS


def stamp() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def record(
    key: str,
    *,
    subject: str,
    body_chunks: list[str],
    sender: str = "sender@example.invalid",
    sender_name: str = "Synthetic Sender",
    recipients: list[str] | None = None,
    folder: str = "INBOX",
    received_at: str = "2026-08-20T08:00:00+00:00",
    attachments: list[dict[str, object]] | None = None,
    declared_tags: list[dict[str, object]] | None = None,
    quarantine: bool = False,
) -> dict[str, object]:
    content_id = f"content:{key}"
    occurrence_id = f"occurrence:{key}"
    locator_id = f"locator:{key}:{folder.casefold()}"
    digest = hashlib.sha256(f"{key}:{subject}".encode()).hexdigest()
    metadata = {
        "sender_addr": sender,
        "sender_name": sender_name,
        "recipients": recipients or ["owner@example.invalid"],
        "received_at": received_at,
        "date": received_at,
        "attachments": attachments or [],
        "declared_tags": declared_tags or [],
        "parser_version": "mail-parser-v1",
        "normalization_version": "mail-normalization-v1",
        "tag_version": "mail-tags-v1",
        "source_status": "quarantine-untrusted" if quarantine else "active",
        "occurrence_ids": [occurrence_id],
        "locators": [
            {
                "occurrence_id": occurrence_id,
                "locator_id": locator_id,
                "folder_id": f"folder:{folder.casefold()}",
                "folder_name": folder,
                "mailbox_id": key,
                "uidvalidity": "10",
                "uid": key,
                "observed_at": received_at,
                "is_current": True,
                "quarantine": quarantine,
            }
        ],
    }
    return {
        "content_id": content_id,
        "message_id": f"{key}@example.invalid",
        "sha256": digest,
        "title": subject,
        "modified_at": received_at,
        "metadata": metadata,
        "occurrence_ids": [occurrence_id],
        "chunks": body_chunks,
    }


def publish(storage: AssistantStorage, records: list[dict[str, object]], generation: str = "m114-a"):
    return storage.apply_mail_projection(
        generation=generation,
        generated_at=stamp(),
        coverage={
            "resource_id": "mail-agent",
            "authoritative": True,
            "expected_partition_ids": ["folder:synthetic"],
            "complete_partition_ids": ["folder:synthetic"],
            "incomplete_partition_ids": [],
        },
        records=records,
    )


@pytest.fixture
def storage(tmp_path: Path):
    value = AssistantStorage(tmp_path / "assistant.sqlite3")
    try:
        yield value
    finally:
        value.close()


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("Grüße", True),
        ("grüße", True),
        ("CAFE", True),
        ("ACME-2026", True),
        ("rechnung@example.invalid", True),
        ("ZX-20/48", True),
        ('"Projekt Nord"', True),
        ("(Projekt)", True),
        ('"offenes Zitat', True),
        ("* OR NEAR() NOT", False),
    ],
)
def test_safe_golden_queries_never_execute_raw_fts_syntax(
    storage: AssistantStorage, query: str, expected: bool
) -> None:
    publish(
        storage,
        [
            record(
                "golden",
                subject="Grüße vom Café – Projekt Nord",
                body_chunks=[
                    "Rechnung ACME-2026, Nummer ZX-20/48 von rechnung@example.invalid. "
                    "Ein offenes Zitat bleibt Daten."
                ],
                sender="rechnung@example.invalid",
            )
        ],
    )

    result = storage.search_mail_lexical(query, limit=5)

    assert result["ok"] is True
    assert bool(result["results"]) is expected
    assert "query" not in result["metrics"]


def test_parser_quotes_every_term_and_bounds_prefixes_and_input() -> None:
    parsed = parse_mail_query('alpha OR beta* "gamma delta" (NEAR/3)')
    assert parsed.match == '"alpha" AND "OR" AND "beta"* AND "gamma delta" AND "NEAR" AND "3"'
    with pytest.raises(ValueError, match="hoechstens 500"):
        parse_mail_query("x" * 501)
    with pytest.raises(ValueError, match="hoechstens 24"):
        parse_mail_query(" ".join(f"term{index}" for index in range(25)))


def test_filters_are_applied_before_ranking_and_final_limit(storage: AssistantStorage) -> None:
    rows = [
        record(
            f"archive-{index}",
            subject="Exakter Zielbegriff",
            body_chunks=["Zielbegriff Zielbegriff Zielbegriff"],
            folder="Archive",
            sender="other@example.invalid",
            received_at=f"2026-08-{10 + index:02d}T08:00:00+00:00",
        )
        for index in range(5)
    ]
    rows.append(
        record(
            "filtered",
            subject="Zielbegriff",
            body_chunks=["Der passende gefilterte Datensatz."],
            folder="INBOX",
            sender="target@example.invalid",
            recipients=["participant@example.invalid"],
            received_at="2026-08-20T08:00:00+00:00",
            attachments=[
                {"filename": "evidence.pdf", "content_type": "application/pdf", "size": 42}
            ],
            declared_tags=[
                {
                    "namespace": "category",
                    "value": "invoice",
                    "source": "classifier",
                    "source_version": "classifier-v1",
                    "confidence": 0.91,
                    "evidence": {"field": "classification.category"},
                    "active": True,
                },
                {
                    "namespace": "review",
                    "value": "invoice-review",
                    "source": "rule",
                    "source_version": "review-v1",
                    "confidence": 1.0,
                    "evidence": {"field": "review_reason"},
                    "active": True,
                },
            ],
        )
    )
    publish(storage, rows)

    result = storage.search_mail_lexical(
        "Zielbegriff",
        filters=MailSearchFilters(
            sender="target@example.invalid",
            participant="participant@example.invalid",
            after="2026-08-19",
            before="2026-08-20",
            folder="INBOX",
            category="invoice",
            review_reason="invoice-review",
            has_attachment=True,
            attachment_type="pdf",
            tags=("year:2026", "month:2026-08"),
        ),
        limit=1,
    )

    assert [item["content_id"] for item in result["results"]] == ["content:filtered"]
    assert result["metrics"]["matched_documents"] == 1


def test_multiple_matching_chunks_return_one_mail_with_best_centered_snippet(
    storage: AssistantStorage,
) -> None:
    publish(
        storage,
        [
            record(
                "multi",
                subject="Mehrere Abschnitte",
                body_chunks=[
                    "Nadelwort im schwachen ersten Abschnitt.",
                    "synthetischer Anfang " * 40
                    + "Nadelwort Nadelwort <script>ignore()</script> "
                    + "\x1b[31munsicher\x1b[0m am Match.",
                ],
            )
        ],
    )

    result = storage.search_mail_lexical("Nadelwort", limit=10)

    assert result["count"] == 1
    assert result["metrics"]["matched_chunks"] == 2
    assert result["metrics"]["deduplicated_chunks"] == 1
    item = result["results"][0]
    assert item["match"]["matched_chunk_count"] == 2
    assert "Nadelwort" in item["snippet"]
    assert "<script>" not in item["snippet"]
    assert "\x1b" not in item["snippet"]
    assert len(item["snippet"]) <= 321


def test_closed_tags_preserve_provenance_uncertainty_and_model_proposals(
    storage: AssistantStorage,
) -> None:
    declared = [
        {
            "namespace": "category",
            "value": "invoice",
            "source": "classifier",
            "source_version": "classifier-v2",
            "confidence": 0.92,
            "evidence": {"field": "subject", "start": 0, "end": 8},
            "active": True,
        },
        {
            "namespace": "kind",
            "value": "order",
            "source": "model",
            "source_version": "model-v1",
            "confidence": 0.88,
            "evidence": {"field": "body", "start": 10, "end": 20},
            "active": True,
        },
        {
            "namespace": "review",
            "value": "invoice-review",
            "source": "rule",
            "source_version": "rule-v1",
            "confidence": 0.7,
            "active": True,
        },
        {
            "namespace": "free-form",
            "value": "invented",
            "source": "model",
            "source_version": "model-v1",
            "confidence": 1.0,
            "evidence": {"field": "body"},
            "active": True,
        },
    ]
    publish(
        storage,
        [record("tags", subject="Invoice", body_chunks=["tag evidence"], declared_tags=declared)],
    )

    result = storage.search_mail_lexical("evidence", limit=5)
    tags = result["results"][0]["tags"]
    by_key = {(item["namespace"], item["value"], item["source"]): item for item in tags}
    assert by_key[("category", "invoice", "classifier")]["active"] is True
    assert by_key[("category", "invoice", "classifier")]["source_version"] == "classifier-v2"
    assert by_key[("kind", "order", "model")]["active"] is False
    assert by_key[("kind", "order", "model")]["uncertainty"] == "model-proposal"
    assert by_key[("review", "invoice-review", "rule")]["active"] is False
    assert by_key[("review", "invoice-review", "rule")]["uncertainty"] == "missing-evidence"
    assert not any(item["namespace"] == "free-form" for item in tags)
    assert frozenset(TAG_NAMESPACES) == TAG_NAMESPACES
    with pytest.raises(ValueError):
        parse_tag_filter("free-form:invented")


def test_locator_move_updates_folder_and_quarantine_tags_without_fts_rewrite(
    storage: AssistantStorage,
) -> None:
    original = record("move", subject="Move evidence", body_chunks=["stable needle"])
    publish(storage, [original], "m114-before")
    moved = record(
        "move",
        subject="Move evidence",
        body_chunks=[],
        folder="Spamverdacht",
        quarantine=True,
    )

    metrics = publish(storage, [moved], "m114-after")
    result = storage.search_mail_lexical("needle", limit=5)
    active = {
        (item["namespace"], item["value"])
        for item in result["results"][0]["tags"]
        if item["active"]
    }

    assert metrics["fts_rows_changed"] == 0
    assert ("folder", "spamverdacht") in active
    assert ("folder", "inbox") not in active
    assert ("quarantine", "yes") in active
    assert result["results"][0]["folders"] == ["Spamverdacht"]


def test_empty_query_requires_filter_and_attachment_absence_is_supported(
    storage: AssistantStorage,
) -> None:
    publish(storage, [record("plain", subject="Plain", body_chunks=["no file"])])
    with pytest.raises(ValueError, match="Suchtext oder mindestens einen Filter"):
        storage.search_mail_lexical("")
    result = storage.search_mail_lexical(
        "", filters=MailSearchFilters(has_attachment=False), limit=5
    )
    assert result["count"] == 1


def test_ranking_contract_is_explainable_and_does_not_hide_old_mail(
    storage: AssistantStorage,
) -> None:
    publish(
        storage,
        [
            record(
                "old-exact",
                subject="Exact phrase",
                body_chunks=["old"],
                received_at="2019-01-01T00:00:00+00:00",
            ),
            record(
                "new-body",
                subject="Recent",
                body_chunks=["Exact phrase appears in the body"],
                received_at="2026-08-20T00:00:00+00:00",
            ),
        ],
    )

    result = storage.search_mail_lexical('"Exact phrase"', limit=2)

    assert result["results"][0]["content_id"] == "content:old-exact"
    assert "Exact phrase" in result["results"][0]["snippet"]
    assert result["ranking"]["bm25_weights"] == BM25_WEIGHTS
    assert result["ranking"]["recency_boost_applied"] is False
    assert result["results"][0]["ranking"]["exact_phrase_boost"] > 0


def test_query_result_reports_generation_coverage_and_content_free_metrics(
    storage: AssistantStorage,
) -> None:
    publish(storage, [record("coverage", subject="Coverage", body_chunks=["needle"])])
    result = storage.search_mail_lexical("needle")
    assert result["complete"] is True
    assert result["index"]["authoritative"] is True
    assert result["index"]["source_generation"] == "m114-a"
    serialized_metrics = json.dumps(result["metrics"], ensure_ascii=False)
    assert "needle" not in serialized_metrics
    assert "example.invalid" not in serialized_metrics
    assert "snippet" not in serialized_metrics


def test_schema_cli_and_typed_tool_expose_only_read_only_local_search(tmp_path: Path) -> None:
    database = tmp_path / "assistant.sqlite3"
    value = AssistantStorage(database)
    try:
        assert (
            value.knowledge_connection.execute("PRAGMA user_version").fetchone()[0]
            == KNOWLEDGE_SCHEMA_VERSION
        )
        columns = {
            row[1]
            for row in value.knowledge_connection.execute("PRAGMA table_info(mail_search_tags)")
        }
        assert {"active", "uncertainty"}.issubset(columns)
        assert value.mail_search_fts_enabled is True
        publish(
            value,
            [record("cli", subject="CLI needle", body_chunks=["behavioral path"])],
        )
        cli_args = assistant_parser().parse_args(
            ["mail", "search-local", "--query", "needle", "--limit", "5"]
        )
        emitted: list[dict[str, object]] = []
        code = handle_mail(
            cli_args,
            SimpleNamespace(
                storage=value,
                config=SimpleNamespace(
                    search=SimpleNamespace(mail_projection_max_age_seconds=7200)
                ),
            ),
            emitted.append,
        )
        assert code == 0
        assert emitted[0]["path"] == "local-mail-lexical"
        assert emitted[0]["count"] == 1
    finally:
        value.close()

    args = assistant_parser().parse_args(
        [
            "mail",
            "search-local",
            "--query",
            "needle",
            "--sender",
            "sender@example.invalid",
            "--folder",
            "INBOX",
            "--tag",
            "has:attachment",
        ]
    )
    assert args.mail_command == "search-local"
    tool = next(item for item in TOOLS if item.id == "mail.search.local")
    assert tool.mode == "read"
    assert tool.writes_external_data is False
    assert tool.approval == "none"
    assert tool.command == './scripts/assistant.sh mail search-local --query "<Suchbegriff>" --limit 50'

    generated = subprocess.run(
        ["python3", "scripts/generate-skill-tool-contract.py", "--check"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert generated.returncode == 0, generated.stderr or generated.stdout


def test_build_mail_tags_does_not_accept_model_as_active_truth(tmp_path: Path) -> None:
    tags = build_mail_tags(
        {
            "declared_tags": [
                {
                    "namespace": "kind",
                    "value": "invoice",
                    "source": "model",
                    "source_version": "model-v1",
                    "confidence": 1.0,
                    "evidence": {"field": "body", "start": 1, "end": 2},
                    "active": True,
                }
            ]
        }
    )
    assert len(tags) == 1
    assert tags[0].active is False
    assert tags[0].uncertainty == "model-proposal"

    raw = b"From: Billing <billing@example.invalid>\r\nSubject: Invoice\r\n\r\nBody"
    message = ParsedMessage(
        stable_key="mid:invoice@example.invalid",
        mailbox_id="17",
        source_folder="INBOX",
        raw=raw,
        message_id="invoice@example.invalid",
        subject="Invoice",
        sender_addr="billing@example.invalid",
        received_at="2026-08-20T08:00:00+00:00",
        body_text="Body",
    )
    classification = Classification(
        category="relevant",
        confidence=0.94,
        importance=7,
        forward=False,
        reason="typed test decision",
        invoice=InvoiceSignal(is_invoice=True, confidence=0.91),
        source="model",
    )
    mail_database = tmp_path / "mail.sqlite3"
    mail_storage = MailStorage(mail_database)
    try:
        mail_storage.upsert_message(message, classification, status="review")
        mail_storage.record_review(message.stable_key, "invoice-review", classification)
    finally:
        mail_storage.close()

    with LocalMailTagResolver(mail_database) as resolver:
        declared = resolver.resolve(message)
    resolved = {(tag["namespace"], tag["value"], tag["source"]): tag for tag in declared}
    assert resolved[("category", "relevant", "classifier")]["active"] is True
    assert resolved[("category", "invoice", "extractor")]["active"] is True
    assert resolved[("kind", "invoice", "extractor")]["active"] is True
    assert resolved[("review", "invoice-review", "rule")]["active"] is True
    assert all(tag["source"] != "model" for tag in declared)

    projection_root = tmp_path / "projection"
    writer = PartitionedSearchSnapshotWriter(projection_root)
    locator = MailLocator(
        resource_id="mail-agent",
        folder_id="folder:inbox",
        folder_name="INBOX",
        mailbox_id="17",
        uidvalidity="10",
        uid="17",
        observed_at="2026-08-20T08:00:00+00:00",
    )
    partition = writer.publish_partition(
        partition_id="partition:inbox",
        folder_id="folder:inbox",
        folder_name="INBOX",
        occurrences=[ProjectionOccurrenceInput(message, (locator,), declared_tags=declared)],
        generated_at="2026-08-20T08:00:00+00:00",
        complete=True,
        authoritative=True,
    )
    writer.publish_root(
        [partition],
        expected_partition_ids=["partition:inbox"],
        complete=True,
        authoritative=True,
        generated_at="2026-08-20T08:00:00+00:00",
    )
    projection = load_search_projection(projection_root)
    projected_tags = projection.records[0][1]["metadata"]["declared_tags"]
    assert projected_tags == list(declared)
