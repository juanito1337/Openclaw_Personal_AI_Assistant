from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from personal_assistant.cli import parser as assistant_parser
from personal_assistant.mail_threads import (
    MAIL_RETRIEVAL_TEXT_VERSION,
    MAIL_THREAD_VERSION,
    build_mail_threads,
    normalize_reply_subject,
    normalize_retrieval_text,
)
from personal_assistant.storage import KNOWLEDGE_SCHEMA_VERSION, AssistantStorage


def _record(
    key: str,
    *,
    subject: str,
    body: str = "body",
    sender: str = "alice@example.invalid",
    recipients: list[str] | None = None,
    received_at: str = "2026-08-20T08:00:00+00:00",
    in_reply_to: list[str] | None = None,
    references: list[str] | None = None,
    folder: str = "INBOX",
) -> dict[str, object]:
    content_id = f"content:{key}"
    occurrence_id = f"occurrence:{key}"
    digest = hashlib.sha256(f"{key}:{subject}:{body}".encode()).hexdigest()
    return {
        "content_id": content_id,
        "message_id": f"{key}@example.invalid",
        "sha256": digest,
        "title": subject,
        "modified_at": received_at,
        "metadata": {
            "sender_addr": sender,
            "sender_name": sender.split("@", 1)[0],
            "recipients": recipients or ["bob@example.invalid"],
            "received_at": received_at,
            "date": received_at,
            "in_reply_to": in_reply_to or [],
            "references": references or [],
            "attachments": [],
            "parser_version": "mail-parser-v1",
            "normalization_version": "mail-normalization-v1",
            "tag_version": "mail-tags-v1",
            "source_status": "active",
            "occurrence_ids": [occurrence_id],
            "locators": [
                {
                    "occurrence_id": occurrence_id,
                    "locator_id": f"locator:{key}:{folder.casefold()}",
                    "folder_id": f"folder:{folder.casefold()}",
                    "folder_name": folder,
                    "mailbox_id": key,
                    "uidvalidity": "10",
                    "uid": key,
                    "observed_at": received_at,
                    "is_current": True,
                    "quarantine": False,
                }
            ],
        },
        "occurrence_ids": [occurrence_id],
        "chunks": [body],
    }


def _publish(
    storage: AssistantStorage,
    records: list[dict[str, object]],
    generation: str = "m115-a",
) -> dict[str, int]:
    return storage.apply_mail_projection(
        generation=generation,
        generated_at=datetime.now(UTC).replace(microsecond=0).isoformat(),
        coverage={"resource_id": "mail-agent", "authoritative": True},
        records=records,
    )


@pytest.fixture
def storage(tmp_path: Path):
    value = AssistantStorage(tmp_path / "assistant.sqlite3")
    try:
        yield value
    finally:
        value.close()


def test_primary_headers_link_changed_subject_and_choose_direct_reply() -> None:
    root = _record("root", subject="Original project")
    middle = _record(
        "middle",
        subject="Completely changed subject",
        sender="bob@example.invalid",
        recipients=["alice@example.invalid"],
        received_at="2026-08-20T09:00:00+00:00",
        in_reply_to=["root@example.invalid"],
        references=["root@example.invalid"],
    )
    child = _record(
        "child",
        subject="Noch einmal anders",
        received_at="2026-08-20T10:00:00+00:00",
        in_reply_to=["middle@example.invalid"],
        references=["root@example.invalid", "middle@example.invalid"],
    )

    result = build_mail_threads([child, root, middle], generation="g")
    members = {str(item["content_id"]): item for item in result.members}

    assert members["content:child"]["parent_content_id"] == "content:middle"
    assert members["content:child"]["evidence_type"] == "in-reply-to"
    assert len({item["thread_id"] for item in result.members}) == 1
    assert result.diagnostics["header_links"] == 2


def test_missing_broken_and_extremely_long_references_fail_closed() -> None:
    broken = _record(
        "broken",
        subject="Re: Safe project subject",
        sender="bob@example.invalid",
        recipients=["alice@example.invalid"],
        references=["not-a-message-id", *[f"missing-{index}@example.invalid" for index in range(500)]],
    )
    root = _record("root", subject="Safe project subject")

    result = build_mail_threads([root, broken], generation="g")
    member = next(item for item in result.members if item["content_id"] == "content:broken")

    assert member["parent_content_id"] is None
    assert member["evidence_type"] == "root"
    assert len(result.edges) == 99
    assert result.diagnostics["invalid_relations"] == 1
    assert result.diagnostics["fallback_links"] == 0


def test_cyclic_and_self_references_never_create_self_ancestor() -> None:
    first = _record("a", subject="A", in_reply_to=["b@example.invalid"])
    second = _record("b", subject="B", in_reply_to=["a@example.invalid"])
    self_ref = _record("self", subject="Self", in_reply_to=["self@example.invalid"])

    result = build_mail_threads([first, second, self_ref], generation="g")
    parents = {
        str(item["content_id"]): str(item["parent_content_id"] or "")
        for item in result.members
    }
    for start in parents:
        seen: set[str] = set()
        current = start
        while parents.get(current):
            assert current not in seen
            seen.add(current)
            current = parents[current]
        assert current not in seen
    assert parents["content:self"] == ""
    assert result.diagnostics["cycle_rejections"] == 1


@pytest.mark.parametrize("prefix", ["Re:", "AW:", "Antwort:", "Fwd:", "FW:", "WG:"])
def test_de_en_reply_and_forward_prefix_fallback_is_marked_uncertain(prefix: str) -> None:
    root = _record("root", subject="Project Aurora handover")
    reply = _record(
        "reply",
        subject=f"{prefix} Project Aurora handover",
        sender="bob@example.invalid",
        recipients=["alice@example.invalid", "team@example.invalid"],
        received_at="2026-08-21T08:00:00+00:00",
    )

    result = build_mail_threads([root, reply], generation="g")
    member = next(item for item in result.members if item["content_id"] == "content:reply")

    assert member["parent_content_id"] == "content:root"
    assert member["evidence_type"] == "subject-participant-time"
    assert member["certainty"] == "uncertain"
    assert result.threads[0]["uncertain"] is True


def test_fallback_requires_known_reciprocal_participants_and_time_window() -> None:
    root = _record("root", subject="Project Aurora handover")
    missing_bcc = _record(
        "missing-bcc",
        subject="Re: Project Aurora handover",
        sender="bob@example.invalid",
        recipients=["unknown@example.invalid"],
        received_at="2026-08-21T08:00:00+00:00",
    )
    too_late = _record(
        "too-late",
        subject="Re: Project Aurora handover",
        sender="bob@example.invalid",
        recipients=["alice@example.invalid"],
        received_at=(datetime(2026, 8, 20, tzinfo=UTC) + timedelta(days=22)).isoformat(),
    )

    result = build_mail_threads([root, missing_bcc, too_late], generation="g")

    assert all(item["parent_content_id"] is None for item in result.members)


@pytest.mark.parametrize(
    "subject",
    ["Newsletter August", "Rechnung August 2026", "Invoice August 2026", ""],
)
def test_identical_newsletter_invoice_and_empty_subjects_are_not_merged(subject: str) -> None:
    first = _record("one", subject=subject)
    second = _record(
        "two",
        subject=f"Re: {subject}" if subject else "Re:",
        sender="bob@example.invalid",
        recipients=["alice@example.invalid"],
        received_at="2026-08-20T09:00:00+00:00",
    )

    result = build_mail_threads([first, second], generation="g")

    assert len(result.threads) == 2
    assert result.diagnostics["fallback_links"] == 0


def test_thread_identity_survives_locator_move_without_source_rewrite(
    storage: AssistantStorage,
) -> None:
    root = _record("root", subject="Move stable", body="stable source")
    reply = _record(
        "reply",
        subject="Re: Move stable",
        body="stable reply",
        sender="bob@example.invalid",
        recipients=["alice@example.invalid"],
        in_reply_to=["root@example.invalid"],
        references=["root@example.invalid"],
    )
    _publish(storage, [root, reply], "before")
    before = storage.knowledge_connection.execute(
        "SELECT thread_id FROM mail_search_thread_members WHERE content_id='content:reply'"
    ).fetchone()[0]
    moved = _record(
        "reply",
        subject="Re: Move stable",
        body="stable reply",
        sender="bob@example.invalid",
        recipients=["alice@example.invalid"],
        in_reply_to=["root@example.invalid"],
        references=["root@example.invalid"],
        folder="Archive",
    )
    moved["sha256"] = reply["sha256"]

    metrics = _publish(storage, [root, moved], "after")
    after = storage.knowledge_connection.execute(
        "SELECT thread_id FROM mail_search_thread_members WHERE content_id='content:reply'"
    ).fetchone()[0]

    assert before == after
    assert metrics["fts_rows_changed"] == 0
    assert storage.knowledge_connection.execute(
        "SELECT text FROM chunks c JOIN documents d ON d.id=c.document_id "
        "WHERE d.content_id='content:reply'"
    ).fetchone()[0] == "stable reply"


def test_context_is_bounded_chronological_deduplicated_and_not_query_evidence(
    storage: AssistantStorage,
) -> None:
    records: list[dict[str, object]] = []
    for index in range(5):
        key = f"message-{index}"
        records.append(
            _record(
                key,
                subject=f"Thread message {index}",
                body="queryneedle" if index == 2 else f"context body {index}",
                sender="alice@example.invalid" if index % 2 == 0 else "bob@example.invalid",
                recipients=["bob@example.invalid" if index % 2 == 0 else "alice@example.invalid"],
                received_at=f"2026-08-20T{8 + index:02d}:00:00+00:00",
                in_reply_to=[f"message-{index - 1}@example.invalid"] if index else [],
                references=[f"message-{item}@example.invalid" for item in range(index)],
            )
        )
    _publish(storage, records)

    result = storage.search_mail_lexical("queryneedle", context_limit=2)
    hit = result["results"][0]
    context = hit["context"]

    assert result["count"] == 1
    assert len(context) == 2
    assert [item["thread_position"] for item in context] == [1, 3]
    assert len({item["content_id"] for item in context}) == 2
    assert all(item["role"] == "thread-context" for item in context)
    assert all(item["query_match"] is False for item in context)
    assert all(item["evidence_for_query"] is False for item in context)
    assert hit["role"] == "query-hit"
    assert hit["thread"]["version"] == MAIL_THREAD_VERSION
    assert result["thread_context"]["context_is_query_evidence"] is False
    with pytest.raises(ValueError, match="zwischen 0 und 6"):
        storage.search_mail_lexical("queryneedle", context_limit=7)


def test_retrieval_normalization_is_versioned_and_preserves_citable_source(
    storage: AssistantStorage,
) -> None:
    original = (
        "Eigene belastbare Antwort\n\n"
        "On Thu, Alice wrote:\n"
        "> wiederholtesgeheimnis aus einer alten Nachricht\n"
        "-- \nAlice"
    )
    first = normalize_retrieval_text(original)
    second = normalize_retrieval_text(original)
    assert first == second
    assert first.version == MAIL_RETRIEVAL_TEXT_VERSION
    assert first.text == "Eigene belastbare Antwort"
    _publish(storage, [_record("source", subject="Source", body=original)])

    own = storage.search_mail_lexical("belastbare")
    quoted = storage.search_mail_lexical("wiederholtesgeheimnis")
    stored = storage.knowledge_connection.execute(
        "SELECT text FROM chunks c JOIN documents d ON d.id=c.document_id "
        "WHERE d.content_id='content:source'"
    ).fetchone()[0]

    assert own["count"] == 1
    assert own["results"][0]["retrieval_text"] == {
        "version": MAIL_RETRIEVAL_TEXT_VERSION,
        "source_body_preserved": True,
    }
    assert quoted["count"] == 0
    assert stored == original


def test_schema_four_has_separate_thread_metadata_tables(
    storage: AssistantStorage, tmp_path: Path
) -> None:
    assert KNOWLEDGE_SCHEMA_VERSION == 4
    assert storage.knowledge_connection.execute("PRAGMA user_version").fetchone()[0] == 4
    names = {
        row[0]
        for row in storage.knowledge_connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    assert {"mail_search_threads", "mail_search_thread_members"}.issubset(names)
    assert normalize_reply_subject("AW: Re: Projekt Nord") == ("projekt nord", True)
    args = assistant_parser().parse_args(
        ["mail", "search-local", "--query", "Projekt", "--context-limit", "3"]
    )
    assert args.context_limit == 3

    migration_path = tmp_path / "migration.sqlite3"
    old = AssistantStorage(migration_path)
    original = "Neue Antwort\nOn Friday, Bob wrote:\n> alter Text"
    _publish(old, [_record("migration", subject="Migration", body=original)])
    chunk_id = old.knowledge_connection.execute("SELECT id FROM chunks").fetchone()[0]
    old.knowledge_connection.execute(
        "UPDATE mail_search_fts SET body=? WHERE rowid=?", (original, chunk_id)
    )
    old.knowledge_connection.execute(
        "UPDATE mail_search_contents SET retrieval_text_version=''"
    )
    old.knowledge_connection.execute("PRAGMA user_version=3")
    old.knowledge_connection.commit()
    old.close()
    upgraded = AssistantStorage(migration_path)
    try:
        assert upgraded.knowledge_connection.execute(
            "SELECT body FROM mail_search_fts WHERE rowid=?", (chunk_id,)
        ).fetchone()[0] == "Neue Antwort"
        assert upgraded.knowledge_connection.execute(
            "SELECT text FROM chunks WHERE id=?", (chunk_id,)
        ).fetchone()[0] == original
        assert upgraded.knowledge_connection.execute(
            "SELECT retrieval_text_version FROM mail_search_contents"
        ).fetchone()[0] == MAIL_RETRIEVAL_TEXT_VERSION
    finally:
        upgraded.close()


def test_m110_gold_corpus_thread_benchmark_has_no_mislinks() -> None:
    completed = subprocess.run(
        ["python3", "scripts/benchmark_mail_threads_m115.py"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr or completed.stdout
    payload = json.loads(completed.stdout)
    assert payload["messages"] == 13
    assert payload["pair_precision"] == 1.0
    assert payload["pair_recall"] == 1.0
    assert payload["mislink_rate"] == 0.0
    assert payload["false_linked_pairs"] == []
    assert payload["missed_linked_pairs"] == []
