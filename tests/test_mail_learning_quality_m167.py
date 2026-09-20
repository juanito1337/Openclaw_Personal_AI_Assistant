from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from mail_agent.learning_quality import LearningQualityAnalyzer
from mail_agent.models import Classification, Envelope
from mail_agent.parser import parse_eml
from mail_agent.review import ReviewReason
from mail_agent.storage import Storage
from personal_assistant.agent_tool_orchestration import guard_claims, route_intent

ROOT = Path(__file__).resolve().parents[1]
CORPUS = ROOT / "tests" / "fixtures" / "m16" / "mail-learning-quality.json"


def _message(case_id: str, sender: str, thread: str, *, subject: str | None = None):
    raw = (
        f"From: Synthetic <{sender}@example.invalid>\r\n"
        "To: Recipient <recipient@example.invalid>\r\n"
        f"Subject: {subject or 'Synthetic status 1001'}\r\n"
        f"Message-ID: <{case_id}@example.invalid>\r\n"
        f"References: <{thread}@example.invalid>\r\n"
        "Content-Type: text/plain; charset=utf-8\r\n\r\n"
        "Synthetic body that must not enter the decision snapshot.\r\n"
    ).encode()
    return parse_eml(raw, Envelope(case_id), "INBOX")


def _classification(case: dict[str, str]) -> Classification:
    return Classification(
        case["combined"],
        0.91,
        7,
        case["combined"] == "relevant",
        "Synthetic decision reason",
        source="synthetic-combined",
        decision_evidence={
            "rule": {"category": case["rule"], "confidence": 0.9, "source": "rule"},
            "model": {"category": case["model"], "confidence": 0.8, "source": "model"},
        },
    )


def _seed(storage: Storage) -> list[dict[str, str]]:
    payload = json.loads(CORPUS.read_text(encoding="utf-8"))
    cases = payload["cases"]
    for index, case in enumerate(cases, start=1):
        message = _message(case["id"], case["sender"], case["thread"])
        storage.upsert_message(message, _classification(case), status="classified")
        storage.record_feedback(message, case["actual"], "Agent/Korrektur")
        storage.connection.execute(
            "UPDATE feedback SET created_at=? WHERE stable_key=?",
            (f"2026-09-{index:02d}T10:00:00+00:00", message.stable_key),
        )
    storage.connection.commit()
    return cases


def test_corpus_is_content_free_and_immutable_fixture() -> None:
    payload = json.loads(CORPUS.read_text(encoding="utf-8"))
    assert payload["privacy"] == "synthetic-content-free"
    assert len(payload["cases"]) == 10
    serialized = json.dumps(payload, ensure_ascii=False)
    assert "@" not in serialized
    assert "Subject:" not in serialized
    assert "body" not in serialized.casefold()


def test_first_decision_snapshot_is_append_only_and_content_free(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "mail.sqlite3")
    try:
        case = json.loads(CORPUS.read_text(encoding="utf-8"))["cases"][0]
        message = _message(case["id"], case["sender"], case["thread"], subject="Private marker 4711")
        storage.upsert_message(message, _classification(case), status="classified")
        row = storage.connection.execute("SELECT * FROM decision_snapshots").fetchone()
        assert row is not None
        serialized = json.dumps(dict(row), ensure_ascii=False)
        assert "Private marker" not in serialized
        assert "example.invalid" not in serialized
        assert "Synthetic body" not in serialized
        original_digest = row["snapshot_sha256"]

        changed = Classification("spam", 1.0, 1, False, "Changed", source="rule")
        storage.upsert_message(message, changed, status="spam")
        assert storage.connection.execute("SELECT COUNT(*) FROM decision_snapshots").fetchone()[0] == 1
        assert storage.connection.execute(
            "SELECT snapshot_sha256 FROM decision_snapshots"
        ).fetchone()[0] == original_digest
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            storage.connection.execute("UPDATE decision_snapshots SET source_type='rule'")
    finally:
        storage.close()


def test_feedback_binds_exact_snapshot_and_legacy_remains_separate(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "mail.sqlite3")
    try:
        case = json.loads(CORPUS.read_text(encoding="utf-8"))["cases"][0]
        message = _message(case["id"], case["sender"], case["thread"])
        storage.upsert_message(message, _classification(case), status="classified")
        storage.record_feedback(message, case["actual"], "Agent/Korrektur")
        linked = storage.connection.execute(
            "SELECT decision_snapshot_id FROM feedback WHERE stable_key=?", (message.stable_key,)
        ).fetchone()[0]
        assert linked is not None

        storage.connection.execute(
            """
            INSERT INTO feedback (
                stable_key, verdict, sender_addr, sender_domain, subject,
                subject_signature, subject_pattern, pattern_version, source_folder,
                correction_folder, feature_json, original_snapshot_valid, created_at, metadata_json
            ) VALUES ('legacy-only', 'routine', '', '', '', '', '', 1,
                      'legacy', 'legacy', '{}', 0, '2026-01-01T00:00:00+00:00', '{}')
            """
        )
        storage.connection.commit()
        report = LearningQualityAnalyzer(storage).report(limit=100)
        assert report["data_quality"]["rows_with_immutable_original_decision"] == 1
        assert report["data_quality"]["legacy_rows_without_original_decision"] == 1
        assert report["evaluation"]["combined_decision"]["samples"] == 1
    finally:
        storage.close()


def test_every_predictor_reports_coverage_errors_and_abstention(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "mail.sqlite3")
    try:
        _seed(storage)
        report = LearningQualityAnalyzer(storage).report(limit=100)
        evaluation = report["evaluation"]
        for name in (
            "sender_only_baseline",
            "pattern_learning",
            "rule_decision",
            "model_decision",
            "combined_decision",
        ):
            metrics = evaluation[name]
            assert "coverage" in metrics
            assert "accuracy" in metrics
            assert "false_positive_total" in metrics
            assert "false_negative_total" in metrics
            assert "abstentions" in metrics
            assert set(metrics["confusion_matrix"]) == {"relevant", "routine", "spam"}
        assert evaluation["combined_decision"]["relevant_missed"] == 1
        assert evaluation["combined_decision"]["spam_forward_risk"] == 1
    finally:
        storage.close()


def test_temporal_holdout_has_no_sender_or_thread_leakage(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "mail.sqlite3")
    try:
        _seed(storage)
        split = LearningQualityAnalyzer(storage).report(limit=100)["evaluation"][
            "grouped_temporal_holdout"
        ]
        assert split["method"] == "chronological-70-30-purged-by-sender-and-thread"
        assert split["train_samples"] > 0
        assert split["eval_samples"] > 0
        assert split["sender_overlap"] == 0
        assert split["thread_overlap"] == 0
        assert set(split["predictors"]) == {"sender", "pattern", "rule", "model", "combined"}
    finally:
        storage.close()


def test_safety_errors_are_prioritized_without_content(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "mail.sqlite3")
    try:
        _seed(storage)
        priority = LearningQualityAnalyzer(storage).report(limit=100)["review_priority"]
        assert priority["by_risk"] == {
            "relevant-not-forwarded": 1,
            "spam-forward-risk": 1,
        }
        assert all(item["priority"] == 100 for item in priority["cases"])
        serialized = json.dumps(priority, ensure_ascii=False)
        assert "example.invalid" not in serialized
        assert "Synthetic status" not in serialized
        assert priority["automatic_activation"] is False
    finally:
        storage.close()


def test_review_list_exposes_impact_priority(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "mail.sqlite3")
    try:
        case = json.loads(CORPUS.read_text(encoding="utf-8"))["cases"][0]
        message = _message(case["id"], case["sender"], case["thread"])
        classification = _classification(case)
        storage.upsert_message(message, classification, status="review")
        storage.record_review(
            message.stable_key,
            ReviewReason.RELEVANT_NOT_FORWARDED,
            classification,
        )
        listed = storage.review_items(ReviewReason.RELEVANT_NOT_FORWARDED, limit=10)
        assert listed["messages"][0]["impact_priority"] == 100
        assert listed["messages"][0]["impact_reason"] == "false-negative-or-forwarding-risk"
    finally:
        storage.close()


@pytest.mark.parametrize(
    "answer",
    [
        "Ich konnte keine entsprechende E-Mail finden.",
        "No message exists in the mailbox.",
        "No existe ningún correo en el buzón.",
    ],
)
def test_incomplete_mail_evidence_blocks_multilingual_negative_claims(answer: str) -> None:
    route = route_intent("Suche eine synthetische Mail")
    verdict = guard_claims(
        route=route,
        answer=answer,
        evidence=[{
            "tool_id": "mail.search",
            "domain": "mail",
            "ok": True,
            "complete": False,
            "folder_errors": [{"folder": "Archive", "error": "timeout"}],
            "results_may_be_truncated": True,
            "allowed_claims": ["tool-status", "positive-evidence"],
        }],
    )
    assert verdict["ok"] is False
    assert "negative-claim-not-authorized" in verdict["issues"]


def test_pattern_activation_policy_remains_bounded_and_non_automatic(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "mail.sqlite3")
    try:
        _seed(storage)
        gate = LearningQualityAnalyzer(storage).report(limit=100)["evaluation"][
            "subject_pattern_versions"
        ]["activation_gate"]
        assert gate["minimum_evidence"] == {"relevant": 1, "routine": 2, "spam": 2}
        assert gate["conflict_free_required"] is True
        assert gate["automatic_activation"] is False
    finally:
        storage.close()
