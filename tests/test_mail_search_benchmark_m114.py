from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import benchmark_mail_search_m110 as baseline  # noqa: E402
import benchmark_mail_search_m114 as benchmark  # noqa: E402


def test_m114_benchmark_reports_latency_quality_and_m110_comparison() -> None:
    report = benchmark.build_report(samples=3)
    assert report["ok"] is True
    assert report["milestone"] == "M11.4"
    assert report["inventory"]["documents"] == 11
    assert report["search"]["quality"]["duplicate_hits"] == 0
    assert report["search"]["quality_by_kind"]["date-range"]["mean_recall_at_10"] == 1.0
    assert report["search"]["quality_by_kind"]["attachment"]["mean_recall_at_10"] == 1.0
    assert report["search"]["latency"]["p50_ms"] >= 0
    assert report["search"]["latency"]["p95_ms"] >= report["search"]["latency"]["p50_ms"]
    assert report["search"]["latency"]["p99_ms"] >= report["search"]["latency"]["p95_ms"]
    assert report["comparison_m110"]["arbitrary_acceptance_threshold_applied"] is False


def test_m114_report_contains_no_queries_addresses_bodies_or_snippets() -> None:
    corpus = baseline.load_corpus()
    report = benchmark.build_report(samples=3)
    serialized = json.dumps(report, ensure_ascii=False)
    for query in corpus["queries"]:
        assert str(query["query"]) not in serialized
    for message in corpus["messages"]:
        assert str(message["from_addr"]) not in serialized
        assert str(message["body"]) not in serialized
    assert '"snippet"' not in serialized
    assert report["privacy"]["productive_data_read"] is False
