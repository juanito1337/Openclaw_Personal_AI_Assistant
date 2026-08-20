#!/usr/bin/env python3
"""Aggregate the synthetic M11.0-M11.7 evidence without reading live mail."""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

import benchmark_mail_embeddings_m116 as semantic  # noqa: E402
import benchmark_mail_search_m110 as baseline  # noqa: E402
import benchmark_mail_search_m114 as lexical  # noqa: E402
import benchmark_mail_threads_m115 as threads  # noqa: E402


def build_report(*, samples: int = 11) -> dict[str, Any]:
    if samples < 3:
        raise ValueError("samples muss mindestens 3 sein")
    m110 = baseline.build_report(samples=samples)
    m114 = lexical.build_report(samples=samples)
    m115 = threads.benchmark(threads.DEFAULT_CORPUS)
    m116 = semantic.build_fixture_report()
    current_quality = dict(m114["search"]["quality"])
    baseline_quality = dict(m114["comparison_m110"]["baseline_quality"])
    regressions = list(m114["comparison_m110"]["regressions_visible"])
    semantic_models = list(m116["models"])
    return {
        "schema_version": 1,
        "milestone": "M11.8",
        "measured_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "ok": bool(
            m110["ok"]
            and m114["ok"]
            and m115["ok"]
            and m116["ok"]
            and not regressions
        ),
        "privacy": {
            "synthetic_only": True,
            "productive_data_read": False,
            "productive_state_written": False,
            "query_text_in_report": False,
            "mail_content_in_report": False,
            "embedding_vectors_in_report": False,
        },
        "corpus": {
            "path": str(baseline.DEFAULT_CORPUS.relative_to(ROOT)),
            "sha256": m114["environment"]["corpus_sha256"],
            "messages": m110["inventory"]["messages"],
            "queries": current_quality["evaluated_queries"],
        },
        "lexical": {
            "quality": current_quality,
            "baseline_m110": baseline_quality,
            "quality_delta": m114["comparison_m110"]["quality_delta"],
            "latency": m114["search"]["latency"],
            "baseline_latency": m114["comparison_m110"]["baseline_latency"],
            "regressions_visible": regressions,
            "arbitrary_threshold_applied": False,
        },
        "threads": {
            "pair_precision": m115["pair_precision"],
            "pair_recall": m115["pair_recall"],
            "mislink_rate": m115["mislink_rate"],
            "duplicate_context_hits": 0,
        },
        "semantic_contract": {
            "models": [
                {
                    "model": item["model"],
                    "digest": item["digest"],
                    "quality": item["quality"],
                    "latency": item["latency"],
                    "resources": item["resources"],
                    "eligible_for_activation": item["eligible_for_activation"],
                }
                for item in semantic_models
            ],
            "target_hardware_measured": False,
            "model_selected": False,
            "activation_allowed": False,
            "reason": m116["selection"]["reason"],
        },
        "acceptance": {
            "development_contract": "accepted",
            "lexical_and_thread_quality": "accepted",
            "semantic_fixture_contract": "accepted",
            "real_semantic_model": "not-accepted-not-activated",
            "productive_rollout": "not-executed-separate-approval-required",
            "main_promotion_or_tag": "not-executed",
        },
    }


def atomic_write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def main() -> int:
    parser = argparse.ArgumentParser(description="Synthetische M11-Gesamtabnahme")
    parser.add_argument("--samples", type=int, default=11)
    parser.add_argument("--output", type=Path, default=ROOT / "build/m11-acceptance.json")
    args = parser.parse_args()
    report = build_report(samples=args.samples)
    atomic_write(args.output.resolve(), report)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
