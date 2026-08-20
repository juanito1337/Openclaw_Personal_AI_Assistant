#!/usr/bin/env python3
"""M11.6 semantic benchmark; real models run only through the priority proxy."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import resource
import statistics
import sys
import tempfile
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

import benchmark_mail_search_m110 as baseline  # noqa: E402
import benchmark_mail_search_m114 as lexical  # noqa: E402

from personal_assistant.mail_embeddings import (  # noqa: E402
    MAIL_EMBEDDING_CONTRACT_VERSION,
    EmbeddingModel,
    OllamaCoordinatorEmbeddingClient,
)


class FixtureProvider:
    """Deterministic vectors for contract tests, never a claimed real model result."""

    def __init__(self, dimension: int, profile: str) -> None:
        self.dimension = dimension
        self.profile = profile

    @staticmethod
    def _features(value: str) -> list[float]:
        text = value.casefold()
        groups = (
            ("dach", "roof", "gebäudedach", "abdichtung", "gerüst"),
            ("pumpe", "pump", "circulation"),
            ("garantie", "warranty", "certificate", "coverage"),
            ("wartung", "maintenance", "tankprüfung", "termin"),
            ("board", "meeting", "cedar", "budget"),
            ("rechnung", "invoice", "kosten", "cost", "price", "eur"),
            ("bahn", "rail", "fahrplan", "train"),
            ("übergabe", "handover", "aurora"),
        )
        return [1.0 + sum(text.count(token) for token in tokens) * 3.0 for tokens in groups]

    def embed(
        self,
        texts: Sequence[str],
        *,
        priority: str,
    ) -> tuple[list[list[float]], dict[str, Any]]:
        started = time.perf_counter()
        vectors: list[list[float]] = []
        for text in texts:
            values = self._features(text)
            if self.profile == "reduced":
                values = [values[0] + values[1], values[2] + values[3], *values[4:]]
            values = (values + [0.25] * self.dimension)[: self.dimension]
            vectors.append(values)
        return vectors, {
            "latency_ms": round((time.perf_counter() - started) * 1000.0, 4),
            "queue_wait_ms": 0.0,
            "priority": priority,
        }


def _parse_model(value: str) -> EmbeddingModel:
    parts = value.split("|")
    if len(parts) != 4:
        raise argparse.ArgumentTypeError(
            "Modell muss NAME|sha256:DIGEST|DIMENSION|KONTEXTZEICHEN verwenden"
        )
    try:
        return EmbeddingModel(parts[0], parts[1], int(parts[2]), int(parts[3]))
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, math.ceil(percentile * len(ordered)) - 1)
    return round(ordered[index], 4)


def _evaluate_model(
    *,
    model: EmbeddingModel,
    provider: Any,
    measurement_kind: str,
    inventory: dict[str, Any],
) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="m116-embedding-") as temporary:
        corpus, storage = lexical.build_runtime(Path(temporary))
        try:
            rss_before = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
            cold_started = time.perf_counter()
            build = storage.build_mail_embeddings(
                model=model,
                provider=provider,
                max_chunks=100_000,
                batch_size=8,
            )
            cold_ms = (time.perf_counter() - cold_started) * 1000.0
            query_rows: list[dict[str, Any]] = []
            latencies: list[float] = []
            queue_wait: list[float] = []
            for query in corpus["queries"]:
                if str(query["kind"]) in {"date-range", "attachment", "negative"}:
                    continue
                started = time.perf_counter()
                result = storage.search_mail_semantic(
                    str(query["query"]),
                    model=model,
                    provider=provider,
                    limit=10,
                )
                latencies.append((time.perf_counter() - started) * 1000.0)
                queue_wait.append(float(result["metrics"]["queue_wait_ms"]))
                returned = [
                    str(item["content_id"]).removeprefix("content:")
                    for item in result["results"]
                ]
                query_rows.append(
                    {
                        "query_id": str(query["id"]),
                        "kind": str(query["kind"]),
                        "returned_ids": returned,
                        "metrics": baseline.ranking_metrics(
                            dict(query["relevance"]), returned
                        ),
                    }
                )
            storage.knowledge_connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            database = storage.knowledge_path
            vector_bytes = int(
                storage.knowledge_connection.execute(
                    "SELECT COALESCE(SUM(length(vector)),0) FROM mail_search_embeddings "
                    "WHERE model_digest=?",
                    (model.digest,),
                ).fetchone()[0]
            )
            rss_after = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
            quality = baseline.aggregate_quality(query_rows)
            return {
                "measurement_kind": measurement_kind,
                "eligible_for_activation": measurement_kind == "target-hardware",
                "model": model.name,
                "digest": model.digest,
                "dimension": model.dimension,
                "context_limit_chars": model.context_limit,
                "model_size_bytes": int(inventory.get("size_bytes") or 0),
                "installed_digest_verified": bool(inventory.get("verified")),
                "build": build,
                "quality": quality,
                "quality_by_kind": baseline.quality_by_kind(query_rows),
                "latency": {
                    "samples": len(latencies),
                    "cold_index_ms": round(cold_ms, 4),
                    "warm_p50_ms": round(statistics.median(latencies), 4),
                    "p50_ms": round(statistics.median(latencies), 4),
                    "p95_ms": _percentile(latencies, 0.95),
                    "queue_wait_p50_ms": round(statistics.median(queue_wait), 4),
                    "queue_wait_p95_ms": _percentile(queue_wait, 0.95),
                },
                "resources": {
                    "database_bytes": database.stat().st_size if database.exists() else 0,
                    "vector_bytes": vector_bytes,
                    "estimated_vector_bytes_at_100k_chunks": model.dimension * 4 * 100_000,
                    "process_peak_rss_delta_kib": max(0, int(rss_after - rss_before)),
                },
                "queries": query_rows,
            }
        finally:
            storage.close()


def build_fixture_report() -> dict[str, Any]:
    models = (
        EmbeddingModel("synthetic-contract-8d", "sha256:" + "1" * 64, 8, 4096),
        EmbeddingModel("synthetic-contract-6d", "sha256:" + "2" * 64, 6, 4096),
    )
    results = [
        _evaluate_model(
            model=model,
            provider=FixtureProvider(model.dimension, profile),
            measurement_kind="synthetic-contract",
            inventory={"verified": False, "size_bytes": 0},
        )
        for model, profile in zip(models, ("full", "reduced"), strict=True)
    ]
    return _report(results, mode="fixture")


def build_live_report(*, base_url: str, models: list[EmbeddingModel]) -> dict[str, Any]:
    if len(models) < 2:
        raise ValueError("Der reale M11.6-Vergleich benoetigt mindestens zwei Modelle")
    results: list[dict[str, Any]] = []
    for model in models:
        client = OllamaCoordinatorEmbeddingClient(base_url=base_url, model=model)
        inventory = client.verify_installed_model()
        results.append(
            _evaluate_model(
                model=model,
                provider=client,
                measurement_kind="target-hardware",
                inventory=inventory,
            )
        )
    return _report(results, mode="target-hardware")


def _report(results: list[dict[str, Any]], *, mode: str) -> dict[str, Any]:
    is_live = mode == "target-hardware"
    return {
        "schema_version": 1,
        "milestone": "M11.6",
        "ok": all(item["build"]["ok"] for item in results),
        "mode": mode,
        "contract_version": MAIL_EMBEDDING_CONTRACT_VERSION,
        "privacy": {
            "synthetic_only": True,
            "productive_data_read": False,
            "productive_state_written": False,
            "query_text_in_report": False,
            "mail_content_in_report": False,
        },
        "environment": {
            "python": sys.version.split()[0],
            "corpus_sha256": hashlib.sha256(baseline.DEFAULT_CORPUS.read_bytes()).hexdigest(),
            "ollama_coordinator_required": True,
        },
        "model_count": len(results),
        "models": results,
        "selection": {
            "selected": None,
            "activation_allowed": False,
            "reason": (
                "Zielhardwarewerte liegen vor; separate fachliche Freigabe bleibt erforderlich"
                if is_live
                else "Nur synthetischer Vertragsbenchmark; keine Zielhardware- oder echte Modellmessung"
            ),
            "requires_explicit_user_approval": True,
        },
    }


def _atomic_write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="M11.6-Embeddingvergleich auf dem synthetischen Goldkorpus"
    )
    parser.add_argument("--base-url", default="")
    parser.add_argument("--model", action="append", type=_parse_model, default=[])
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.model or args.base_url:
        if not args.base_url:
            parser.error("--base-url ist fuer reale Modelle erforderlich")
        report = build_live_report(base_url=args.base_url, models=args.model)
    else:
        report = build_fixture_report()
    if args.output:
        _atomic_write(args.output.resolve(), report)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
