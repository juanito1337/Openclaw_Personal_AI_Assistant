#!/usr/bin/env python3
"""Hermetic M11.4 lexical mail-search benchmark against the M11.0 corpus."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

import benchmark_mail_search_m110 as baseline  # noqa: E402

from personal_assistant.mail_search import MailSearchFilters  # noqa: E402
from personal_assistant.storage import AssistantStorage  # noqa: E402


def _record(item: baseline.SyntheticMail) -> dict[str, Any]:
    message = item.parsed
    digest = hashlib.sha256(message.raw).hexdigest()
    content_id = f"content:{item.fixture_id}"
    occurrence_id = f"occurrence:{item.fixture_id}"
    folder = message.source_folder
    quarantine = folder == "Spamverdacht"
    return {
        "content_id": content_id,
        "message_id": message.message_id,
        "sha256": digest,
        "title": message.subject,
        "modified_at": message.received_at or message.date,
        "occurrence_ids": [occurrence_id],
        "chunks": [message.body_text],
        "metadata": {
            "sender_addr": message.sender_addr,
            "sender_name": message.sender_name,
            "recipients": list(message.recipients),
            "received_at": message.received_at or message.date,
            "date": message.date,
            "attachments": [
                {
                    "filename": attachment.filename,
                    "content_type": attachment.content_type,
                    "size": attachment.size,
                }
                for attachment in message.attachments
            ],
            "declared_tags": [],
            "parser_version": "mail-parser-v1",
            "normalization_version": "mail-normalization-v1",
            "tag_version": "mail-tags-v1",
            "source_status": "quarantine-untrusted" if quarantine else "active",
            "occurrence_ids": [occurrence_id],
            "locators": [
                {
                    "occurrence_id": occurrence_id,
                    "locator_id": f"locator:{item.fixture_id}",
                    "folder_id": f"folder:{hashlib.sha256(folder.encode()).hexdigest()[:16]}",
                    "folder_name": folder,
                    "mailbox_id": message.mailbox_id,
                    "uidvalidity": "synthetic-1",
                    "uid": message.mailbox_id,
                    "observed_at": message.received_at or message.date,
                    "is_current": True,
                    "quarantine": quarantine,
                }
            ],
        },
    }


def build_runtime(root: Path) -> tuple[dict[str, Any], AssistantStorage]:
    corpus = baseline.load_corpus()
    messages = baseline.materialize_messages(corpus)
    storage = AssistantStorage(root / "assistant.sqlite3")
    records = [_record(item) for item in messages if item.projected]
    storage.apply_mail_projection(
        generation="m114-synthetic-generation",
        generated_at="2026-08-20T12:00:00+00:00",
        coverage={
            "resource_id": "mail-agent",
            "authoritative": True,
            "expected_partition_ids": ["synthetic-account"],
            "complete_partition_ids": ["synthetic-account"],
            "incomplete_partition_ids": [],
        },
        records=records,
    )
    return corpus, storage


def _search_input(query: dict[str, Any]) -> tuple[str, MailSearchFilters]:
    kind = str(query["kind"])
    if kind == "date-range":
        return "", MailSearchFilters(after="2026-04-09", before="2026-04-10")
    if kind == "attachment":
        return "", MailSearchFilters(has_attachment=True, attachment_type="pdf")
    return str(query["query"]), MailSearchFilters()


def evaluate(corpus: dict[str, Any], storage: AssistantStorage, *, samples: int) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    latencies: list[float] = []
    first_ms = 0.0
    for query_index, raw in enumerate(corpus["queries"]):
        query = dict(raw)
        text, filters = _search_input(query)
        result: dict[str, Any] = {}
        for sample in range(samples):
            started = time.perf_counter_ns()
            result = storage.search_mail_lexical(
                text,
                filters=filters,
                limit=10,
                max_age_seconds=10**9,
            )
            elapsed = (time.perf_counter_ns() - started) / 1_000_000
            if query_index == 0 and sample == 0:
                first_ms = elapsed
            latencies.append(elapsed)
        returned = [str(item["content_id"]).removeprefix("content:") for item in result["results"]]
        rows.append(
            {
                "query_id": str(query["id"]),
                "kind": str(query["kind"]),
                "returned_ids": returned,
                "metrics": baseline.ranking_metrics(dict(query["relevance"]), returned),
            }
        )
    return {
        "quality": baseline.aggregate_quality(rows),
        "quality_by_kind": baseline.quality_by_kind(rows),
        "latency": baseline.latency_summary(latencies, first_ms),
        "queries": rows,
    }


def _deltas(current: dict[str, Any], previous: dict[str, Any]) -> dict[str, float]:
    fields = ("mean_recall_at_5", "mean_recall_at_10", "mrr", "mean_ndcg_at_10")
    return {
        field: round(float(current[field]) - float(previous[field]), 4)
        for field in fields
    }


def build_report(*, samples: int = 7) -> dict[str, Any]:
    if samples < 3:
        raise ValueError("samples muss mindestens 3 sein")
    baseline_report = baseline.build_report(samples=samples)
    with tempfile.TemporaryDirectory(prefix="m114-mail-search-") as temp:
        corpus, storage = build_runtime(Path(temp))
        try:
            current = evaluate(corpus, storage, samples=samples)
            counts = {
                "documents": int(
                    storage.knowledge_connection.execute(
                        "SELECT COUNT(*) FROM documents WHERE source_type='email'"
                    ).fetchone()[0]
                ),
                "chunks": int(
                    storage.knowledge_connection.execute(
                        "SELECT COUNT(*) FROM mail_search_fts"
                    ).fetchone()[0]
                ),
                "active_tags": int(
                    storage.knowledge_connection.execute(
                        "SELECT COUNT(*) FROM mail_search_tags WHERE active=1"
                    ).fetchone()[0]
                ),
                "inactive_tag_proposals": int(
                    storage.knowledge_connection.execute(
                        "SELECT COUNT(*) FROM mail_search_tags WHERE active=0"
                    ).fetchone()[0]
                ),
            }
        finally:
            storage.close()
    previous = baseline_report["search"]["local_fts"]
    quality_delta = _deltas(current["quality"], previous["quality"])
    return {
        "schema_version": 1,
        "milestone": "M11.4",
        "ok": True,
        "privacy": {
            "synthetic_only": True,
            "productive_data_read": False,
            "productive_state_written": False,
            "query_text_in_report": False,
            "mail_content_in_report": False,
            "snippet_in_report": False,
        },
        "environment": {
            "python": sys.version.split()[0],
            "sqlite": __import__("sqlite3").sqlite_version,
            "samples_per_query": samples,
            "corpus_sha256": hashlib.sha256(baseline.DEFAULT_CORPUS.read_bytes()).hexdigest(),
        },
        "inventory": counts,
        "search": current,
        "comparison_m110": {
            "baseline_quality": previous["quality"],
            "baseline_latency": previous["latency"],
            "quality_delta": quality_delta,
            "latency_delta_ms": {
                field: round(
                    float(current["latency"][field]) - float(previous["latency"][field]),
                    4,
                )
                for field in ("p50_ms", "p95_ms", "p99_ms")
            },
            "regressions_visible": [
                field for field, delta in quality_delta.items() if delta < 0
            ],
            "arbitrary_acceptance_threshold_applied": False,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Reproduzierbarer synthetischer M11.4-Mail-Suchvergleich"
    )
    parser.add_argument("--samples", type=int, default=7)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = build_report(samples=args.samples)
    if args.output:
        baseline.atomic_write(args.output.resolve(), report)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
