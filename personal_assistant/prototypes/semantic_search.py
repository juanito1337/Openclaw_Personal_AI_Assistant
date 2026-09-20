from __future__ import annotations

import hashlib
import json
import math
import os
import re
import statistics
import time
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

TOKEN_RE = re.compile(r"[a-z0-9äöüß]+", re.IGNORECASE)
MODEL_NAME = "m16-synthetic-local-semantic-v1"
MODEL_DIMENSION = 64
MODEL_ALIASES = (
    ("dach", "roof", "abdichtung", "sealing"),
    ("pumpe", "pump", "zirkulation", "circulation"),
    ("garantie", "warranty", "gewährleistung", "coverage"),
    ("wartung", "maintenance", "service", "inspektion"),
    ("rechnung", "invoice", "kostenbeleg", "bill"),
    ("bahn", "rail", "zug", "train"),
    ("übergabe", "handover", "abnahme", "acceptance"),
    ("termin", "appointment", "meeting", "besprechung"),
)
_MODEL_SPEC = json.dumps(
    {"aliases": MODEL_ALIASES, "dimension": MODEL_DIMENSION, "name": MODEL_NAME},
    ensure_ascii=False,
    separators=(",", ":"),
    sort_keys=True,
)
MODEL_DIGEST = "sha256:" + hashlib.sha256(_MODEL_SPEC.encode()).hexdigest()
MODES = ("lexical", "thread-tags", "local-embedding", "hybrid")


def _tokens(value: str) -> list[str]:
    return TOKEN_RE.findall(value.casefold())


def _percentile(values: Sequence[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, math.ceil(len(ordered) * fraction) - 1)
    return round(ordered[index], 4)


@dataclass(frozen=True, slots=True)
class LocalDigestEmbeddingModel:
    """Deterministic local eval model; it is never an activation candidate."""

    name: str = MODEL_NAME
    digest: str = MODEL_DIGEST
    dimension: int = MODEL_DIMENSION

    def __post_init__(self) -> None:
        if self.name != MODEL_NAME or self.digest != MODEL_DIGEST:
            raise ValueError("Lokales Evalmodell stimmt nicht mit dem gebundenen Digest ueberein")
        if self.dimension != MODEL_DIMENSION:
            raise ValueError("Lokales Evalmodell besitzt eine fremde Dimension")

    @staticmethod
    def _canonical_token(token: str) -> str:
        for index, aliases in enumerate(MODEL_ALIASES):
            if token in aliases:
                return f"concept-{index}"
        return token

    def embed(self, text: str) -> tuple[float, ...]:
        vector = [0.0] * self.dimension
        for token in _tokens(text):
            canonical = self._canonical_token(token)
            digest = hashlib.sha256(canonical.encode()).digest()
            slot = int.from_bytes(digest[:2], "big") % self.dimension
            sign = 1.0 if digest[2] & 1 else -1.0
            vector[slot] += sign
        norm = math.sqrt(sum(value * value for value in vector))
        if norm == 0:
            return tuple(vector)
        return tuple(value / norm for value in vector)


class EphemeralEmbeddingIndex:
    """Dedicated removable prototype index; source and lexical state stay external."""

    FILE_NAME = "semantic-index-v1.json"

    def __init__(self, root: Path, model: LocalDigestEmbeddingModel) -> None:
        self.root = root.resolve()
        self.model = model
        self.path = self.root / self.FILE_NAME

    def rebuild(self, documents: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
        self.root.mkdir(parents=True, exist_ok=True)
        rows = [
            {
                "document_id": str(document["id"]),
                "vector": self.model.embed(_document_text(document, contextual=True)),
            }
            for document in sorted(documents, key=lambda item: str(item["id"]))
        ]
        payload = {
            "schema_version": 1,
            "derived_untrusted_content": True,
            "authorizes_actions": False,
            "model": {
                "name": self.model.name,
                "digest": self.model.digest,
                "dimension": self.model.dimension,
            },
            "rows": rows,
        }
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
            encoding="utf-8",
        )
        os.replace(temporary, self.path)
        return {
            "rows": len(rows),
            "bytes": self.path.stat().st_size,
            "sha256": hashlib.sha256(self.path.read_bytes()).hexdigest(),
        }

    def remove(self) -> bool:
        try:
            self.path.unlink()
        except FileNotFoundError:
            return False
        return True


def _document_text(document: Mapping[str, Any], *, contextual: bool) -> str:
    fields = [str(document.get("subject") or ""), str(document.get("body") or "")]
    if contextual:
        fields.extend(str(item) for item in document.get("tags") or [])
        fields.append(str(document.get("thread_context") or ""))
    return " ".join(fields)


def _overlap_score(query: str, text: str) -> float:
    query_tokens = set(_tokens(query))
    if not query_tokens:
        return 0.0
    return len(query_tokens & set(_tokens(text))) / len(query_tokens)


def _cosine(left: Sequence[float], right: Sequence[float]) -> float:
    return sum(a * b for a, b in zip(left, right, strict=True))


def _rank(
    mode: str,
    query: str,
    documents: Sequence[Mapping[str, Any]],
    model: LocalDigestEmbeddingModel,
    *,
    limit: int,
) -> tuple[list[str], int]:
    query_vector = model.embed(query)
    scored: list[tuple[float, str]] = []
    operations = 0
    for document in documents:
        lexical = _overlap_score(query, _document_text(document, contextual=False))
        contextual = _overlap_score(query, _document_text(document, contextual=True))
        semantic = _cosine(query_vector, model.embed(_document_text(document, contextual=True)))
        operations += model.dimension + len(_tokens(query))
        if mode == "lexical":
            score, threshold = lexical, 0.0
        elif mode == "thread-tags":
            score, threshold = contextual, 0.0
        elif mode == "local-embedding":
            score, threshold = semantic, 0.24
        elif mode == "hybrid":
            score, threshold = max(lexical, contextual * 0.9) + max(0.0, semantic) * 0.45, 0.20
        else:
            raise ValueError(f"Unbekannter Evalmodus: {mode}")
        if score > threshold:
            scored.append((score, str(document["id"])))
    scored.sort(key=lambda item: (-item[0], item[1]))
    return [document_id for _, document_id in scored[:limit]], operations


def _evaluate_mode(
    mode: str,
    corpus: Mapping[str, Any],
    model: LocalDigestEmbeddingModel,
    *,
    iterations: int,
) -> dict[str, Any]:
    documents = list(corpus["documents"])
    queries = list(corpus["queries"])
    latencies: list[float] = []
    rows: list[dict[str, Any]] = []
    operation_count = 0
    for iteration in range(iterations):
        current: list[dict[str, Any]] = []
        for query in queries:
            started = time.perf_counter()
            returned, operations = _rank(
                mode,
                str(query["query"]),
                documents,
                model,
                limit=int(query.get("limit") or 3),
            )
            latencies.append((time.perf_counter() - started) * 1000.0)
            operation_count += operations
            if iteration == 0:
                expected = {str(item) for item in query.get("relevant") or []}
                predicted = set(returned)
                current.append(
                    {
                        "query_id": str(query["id"]),
                        "kind": str(query["kind"]),
                        "expected_count": len(expected),
                        "returned_ids": returned,
                        "true_positive": len(expected & predicted),
                        "false_positive": len(predicted - expected),
                        "false_negative": len(expected - predicted),
                        "abstained": not returned,
                    }
                )
        if iteration == 0:
            rows = current
    true_positive = sum(int(row["true_positive"]) for row in rows)
    false_positive = sum(int(row["false_positive"]) for row in rows)
    false_negative = sum(int(row["false_negative"]) for row in rows)
    negative = [row for row in rows if int(row["expected_count"]) == 0]
    precision = true_positive / max(1, true_positive + false_positive)
    recall = true_positive / max(1, true_positive + false_negative)
    abstention = sum(bool(row["abstained"]) for row in negative) / max(1, len(negative))
    return {
        "mode": mode,
        "quality": {
            "precision": round(precision, 6),
            "recall": round(recall, 6),
            "negative_abstention": round(abstention, 6),
            "misclassification_count": sum(
                int(row["false_positive"]) + int(row["false_negative"]) for row in rows
            ),
        },
        "latency_ms": {
            "samples": len(latencies),
            "p50": round(statistics.median(latencies), 4),
            "p95": _percentile(latencies, 0.95),
        },
        "compute": {"deterministic_feature_operations": operation_count},
        "queries": rows,
    }


def evaluate_semantic_architecture(
    corpus: Mapping[str, Any],
    *,
    iterations: int = 7,
) -> dict[str, Any]:
    if iterations < 3:
        raise ValueError("Semantic-Eval benoetigt mindestens drei Messwiederholungen")
    privacy = corpus.get("privacy")
    if not isinstance(privacy, Mapping) or privacy.get("synthetic") is not True:
        raise ValueError("Semantic-Eval akzeptiert nur explizit synthetische Daten")
    model = LocalDigestEmbeddingModel()
    modes = [_evaluate_mode(mode, corpus, model, iterations=iterations) for mode in MODES]
    by_mode = {str(item["mode"]): item for item in modes}
    lexical = by_mode["lexical"]["quality"]
    hybrid = by_mode["hybrid"]["quality"]
    measurable_benefit = (
        float(hybrid["recall"]) >= float(lexical["recall"]) + 0.10
        and float(hybrid["precision"]) >= float(lexical["precision"])
        and float(hybrid["negative_abstention"]) >= float(lexical["negative_abstention"])
    )
    return {
        "schema_version": 1,
        "milestone": "M16.9",
        "measurement_kind": "synthetic-contract",
        "privacy": {
            "synthetic_only": True,
            "productive_data_read": False,
            "content_exfiltration": False,
        },
        "model": {
            "name": model.name,
            "digest": model.digest,
            "dimension": model.dimension,
            "local_only": True,
            "digest_verified": True,
        },
        "retrieval_modes": modes,
        "resources": {
            "document_count": len(corpus["documents"]),
            "query_count": len(corpus["queries"]),
            "estimated_vector_bytes": len(corpus["documents"]) * model.dimension * 4,
        },
        "decision": {
            "measurable_benefit": measurable_benefit,
            "activation_allowed": False,
            "state": "disabled",
            "reason": (
                "synthetic benefit requires target-hardware canary and separate approval"
                if measurable_benefit
                else "no measurable quality benefit over lexical retrieval"
            ),
            "requires_target_hardware_measurement": True,
            "requires_separate_canary_rebuild_rollback_plan": True,
        },
        "safety": {
            "derived_untrusted_content": True,
            "authorizes_actions": False,
            "server_revalidation_required": True,
            "production_registered": False,
        },
    }


def corpus_digest(corpus: Mapping[str, Any]) -> str:
    canonical = json.dumps(corpus, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def forbidden_content_fields(value: Mapping[str, Any]) -> set[str]:
    forbidden = {"body", "subject", "query", "sender", "recipient"}
    found: set[str] = set()

    def visit(item: Any) -> None:
        if isinstance(item, Mapping):
            for key, child in item.items():
                if str(key).casefold() in forbidden:
                    found.add(str(key))
                visit(child)
        elif isinstance(item, Iterable) and not isinstance(item, (str, bytes)):
            for child in item:
                visit(child)

    visit(value)
    return found
