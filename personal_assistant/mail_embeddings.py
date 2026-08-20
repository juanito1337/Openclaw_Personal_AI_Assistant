from __future__ import annotations

import hashlib
import json
import math
import sqlite3
import struct
import time
import urllib.error
import urllib.request
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import urlsplit

from .contracts.time import now_utc_iso
from .mail_threads import MAIL_RETRIEVAL_TEXT_VERSION, normalize_retrieval_text

MAIL_EMBEDDING_CONTRACT_VERSION = "mail-embedding-v1"
MAIL_SEMANTIC_RANKING_VERSION = "mail-semantic-cosine-v1"
MAX_EMBEDDING_DIMENSION = 8192
MAX_EMBEDDING_BATCH = 64


class EmbeddingProvider(Protocol):
    def embed(self, texts: Sequence[str], *, priority: str) -> tuple[list[list[float]], dict[str, Any]]:
        """Return one vector per input and bounded technical request metadata."""


@dataclass(frozen=True, slots=True)
class EmbeddingModel:
    name: str
    digest: str
    dimension: int
    context_limit: int

    def __post_init__(self) -> None:
        name = self.name.strip()
        digest = self.digest.strip().casefold()
        if not name or len(name) > 200:
            raise ValueError("Embedding-Modellname fehlt oder ist zu lang")
        if not digest.startswith("sha256:") or len(digest) != 71:
            raise ValueError("Embedding-Modelldigest muss sha256:<64 Hexzeichen> sein")
        if any(char not in "0123456789abcdef" for char in digest[7:]):
            raise ValueError("Embedding-Modelldigest enthaelt ungueltige Zeichen")
        if not 1 <= int(self.dimension) <= MAX_EMBEDDING_DIMENSION:
            raise ValueError("Embedding-Dimension ist ausserhalb des erlaubten Bereichs")
        if int(self.context_limit) < 256:
            raise ValueError("Embedding-Kontextgrenze muss mindestens 256 Zeichen betragen")


class EmbeddingError(RuntimeError):
    category = "provider-error"


class EmbeddingQueueFullError(EmbeddingError):
    category = "queue-full"


class EmbeddingQueueTimeoutError(EmbeddingError):
    category = "queue-timeout"


class EmbeddingTimeoutError(EmbeddingError):
    category = "provider-timeout"


class EmbeddingUnavailableError(EmbeddingError):
    category = "proxy-unavailable"


class EmbeddingValidationError(EmbeddingError):
    category = "invalid-vector"


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def embedding_key(
    *,
    raw_sha256: str,
    retrieval_text: str,
    retrieval_text_version: str,
    chunk_index: int,
    model: EmbeddingModel,
) -> str:
    """Build the content-only cache key; locator state is deliberately absent."""

    payload = {
        "contract": MAIL_EMBEDDING_CONTRACT_VERSION,
        "raw_sha256": str(raw_sha256).strip().casefold(),
        "retrieval_sha256": _sha256_text(retrieval_text),
        "retrieval_text_version": str(retrieval_text_version),
        "chunk_index": int(chunk_index),
        "model": model.name,
        "model_digest": model.digest.casefold(),
        "dimension": int(model.dimension),
    }
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def validate_vector(vector: Sequence[float], *, dimension: int) -> list[float]:
    if len(vector) != dimension:
        raise EmbeddingValidationError(
            f"Embedding-Dimension {len(vector)} stimmt nicht mit {dimension} ueberein"
        )
    values: list[float] = []
    norm = 0.0
    for item in vector:
        try:
            value = float(item)
        except (TypeError, ValueError) as exc:
            raise EmbeddingValidationError("Embedding enthaelt keine gueltige Zahl") from exc
        if not math.isfinite(value):
            raise EmbeddingValidationError("Embedding enthaelt NaN oder Infinity")
        values.append(value)
        norm += value * value
    if norm <= 0.0:
        raise EmbeddingValidationError("Embedding besitzt keine Richtung")
    return values


def pack_vector(vector: Sequence[float], *, dimension: int) -> bytes:
    values = validate_vector(vector, dimension=dimension)
    return struct.pack(f"<{dimension}f", *values)


def unpack_vector(payload: bytes, *, dimension: int) -> tuple[float, ...]:
    expected = dimension * 4
    if len(payload) != expected:
        raise EmbeddingValidationError(
            f"Gespeicherter Vektor besitzt {len(payload)} statt {expected} Bytes"
        )
    values = struct.unpack(f"<{dimension}f", payload)
    validate_vector(values, dimension=dimension)
    return values


def cosine_similarity(left: Sequence[float], right: Sequence[float]) -> float:
    if len(left) != len(right):
        raise EmbeddingValidationError("Vektordimensionen stimmen nicht ueberein")
    numerator = sum(a * b for a, b in zip(left, right, strict=True))
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm <= 0.0 or right_norm <= 0.0:
        raise EmbeddingValidationError("Kosinusdistanz ist fuer Nullvektoren nicht definiert")
    return max(-1.0, min(1.0, numerator / (left_norm * right_norm)))


class OllamaCoordinatorEmbeddingClient:
    """Embedding client for the existing priority proxy, never the upstream."""

    def __init__(
        self,
        *,
        base_url: str,
        model: EmbeddingModel,
        timeout_seconds: float = 120.0,
        queue_timeout_seconds: float = 120.0,
    ) -> None:
        value = str(base_url or "").strip().rstrip("/")
        if not value.startswith(("http://", "https://")):
            raise ValueError("Ollama-Koordinator benoetigt eine HTTP(S)-Basis-URL")
        parsed = urlsplit(value)
        if (
            parsed.username
            or parsed.password
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
            or parsed.hostname not in {"127.0.0.1", "localhost", "::1", "ollama-proxy"}
            or parsed.port != 11435
        ):
            raise ValueError(
                "Embeddingzugriff ist nur ueber den bekannten Ollama-Prioritaetsproxy auf Port 11435 erlaubt"
            )
        self.base_url = value
        self.model = model
        self.timeout_seconds = max(1.0, float(timeout_seconds))
        self.queue_timeout_seconds = max(1.0, float(queue_timeout_seconds))

    def verify_installed_model(self) -> dict[str, Any]:
        """Verify name and immutable digest via the coordinator's read-only tags route."""

        request = urllib.request.Request(
            self.base_url + "/api/tags",
            method="GET",
            headers={"X-OpenClaw-Source": "mail-semantic-benchmark"},
        )
        try:
            with urllib.request.urlopen(request, timeout=min(self.timeout_seconds, 30.0)) as response:
                raw = response.read(4 * 1024 * 1024)
        except TimeoutError as exc:
            raise EmbeddingTimeoutError("Ollama-Modellinventar hat Timeout") from exc
        except urllib.error.URLError as exc:
            raise EmbeddingUnavailableError(
                f"Ollama-Prioritaetsproxy nicht erreichbar: {type(exc.reason).__name__}"
            ) from exc
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise EmbeddingValidationError("Ollama-Modellinventar ist kein JSON") from exc
        models = payload.get("models") if isinstance(payload, dict) else None
        if not isinstance(models, list):
            raise EmbeddingValidationError("Ollama-Modellinventar enthaelt keine Modellliste")
        for row in models:
            if not isinstance(row, dict) or str(row.get("name") or "") != self.model.name:
                continue
            actual_digest = str(row.get("digest") or "").casefold()
            if actual_digest != self.model.digest.casefold():
                raise EmbeddingValidationError(
                    "Installierter Modelldigest stimmt nicht mit dem Benchmarkvertrag ueberein"
                )
            return {
                "name": self.model.name,
                "digest": actual_digest,
                "size_bytes": int(row.get("size") or 0),
                "verified": True,
            }
        raise EmbeddingUnavailableError(
            f"Embeddingmodell {self.model.name!r} ist am Koordinator nicht installiert"
        )

    @staticmethod
    def _error(payload: bytes, status: int) -> EmbeddingError:
        detail = payload.decode("utf-8", errors="replace")[:500]
        error_type = ""
        try:
            decoded = json.loads(detail)
            if isinstance(decoded, dict):
                error_type = str(decoded.get("error_type") or "")
                detail = str(decoded.get("error") or detail)[:500]
        except json.JSONDecodeError:
            pass
        if error_type == "queue_full":
            return EmbeddingQueueFullError(detail or "Ollama-Warteschlange ist voll")
        if error_type == "queue_timeout":
            return EmbeddingQueueTimeoutError(detail or "Ollama-Warteschlange hat Timeout")
        if status == 504:
            return EmbeddingTimeoutError(detail or "Ollama-Embedding hat Timeout")
        return EmbeddingError(f"Ollama-Embedding HTTP {status}: {detail}")

    def embed(self, texts: Sequence[str], *, priority: str) -> tuple[list[list[float]], dict[str, Any]]:
        if not texts or len(texts) > MAX_EMBEDDING_BATCH:
            raise ValueError(f"Embedding-Batch muss 1 bis {MAX_EMBEDDING_BATCH} Texte enthalten")
        if priority not in {"interactive", "normal", "maintenance", "background"}:
            raise ValueError("Ungueltige Ollama-Prioritaet")
        payload = json.dumps(
            {"model": self.model.name, "input": [str(item) for item in texts], "truncate": True},
            ensure_ascii=False,
        ).encode("utf-8")
        request = urllib.request.Request(
            self.base_url + "/api/embed",
            data=payload,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "X-OpenClaw-Priority": priority,
                "X-OpenClaw-Source": "mail-semantic-index",
                "X-OpenClaw-Queue-Timeout-Seconds": str(self.queue_timeout_seconds),
                "X-OpenClaw-Upstream-Timeout-Seconds": str(self.timeout_seconds),
            },
        )
        started = time.perf_counter()
        try:
            with urllib.request.urlopen(
                request,
                timeout=self.timeout_seconds + self.queue_timeout_seconds + 5.0,
            ) as response:
                raw = response.read(64 * 1024 * 1024)
                queue_wait_ms = float(response.headers.get("X-Ollama-Queue-Wait-Ms", "0") or 0.0)
        except urllib.error.HTTPError as exc:
            raise self._error(exc.read(4096), int(exc.code)) from None
        except TimeoutError as exc:
            raise EmbeddingTimeoutError("Ollama-Embedding hat Timeout") from exc
        except urllib.error.URLError as exc:
            reason = exc.reason
            if isinstance(reason, TimeoutError):
                raise EmbeddingTimeoutError("Ollama-Embedding hat Timeout") from exc
            raise EmbeddingUnavailableError(
                f"Ollama-Prioritaetsproxy nicht erreichbar: {type(reason).__name__}"
            ) from exc
        try:
            decoded = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise EmbeddingValidationError("Ollama-Embeddingantwort ist kein JSON") from exc
        embeddings = decoded.get("embeddings") if isinstance(decoded, dict) else None
        if not isinstance(embeddings, list) or len(embeddings) != len(texts):
            raise EmbeddingValidationError("Ollama lieferte nicht genau einen Vektor pro Text")
        vectors = [validate_vector(item, dimension=self.model.dimension) for item in embeddings]
        return vectors, {
            "latency_ms": round((time.perf_counter() - started) * 1000.0, 3),
            "queue_wait_ms": round(max(0.0, queue_wait_ms), 3),
            "priority": priority,
        }


class MailEmbeddingIndex:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def _candidates(self, model: EmbeddingModel) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            """
            SELECT d.content_id,d.id AS document_id,d.sha256,c.id AS chunk_id,
                   c.chunk_index,c.text
            FROM documents d JOIN chunks c ON c.document_id=d.id
            WHERE d.source_type='email' AND d.resource_id='mail-agent'
              AND d.content_id IS NOT NULL
            ORDER BY d.content_id,c.chunk_index,c.id
            """
        ).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            retrieval = normalize_retrieval_text(str(row["text"] or "")).text
            retrieval = retrieval[: model.context_limit]
            key = embedding_key(
                raw_sha256=str(row["sha256"] or ""),
                retrieval_text=retrieval,
                retrieval_text_version=MAIL_RETRIEVAL_TEXT_VERSION,
                chunk_index=int(row["chunk_index"]),
                model=model,
            )
            result.append(
                {
                    "key": key,
                    "content_id": str(row["content_id"]),
                    "document_id": int(row["document_id"]),
                    "chunk_id": int(row["chunk_id"]),
                    "chunk_index": int(row["chunk_index"]),
                    "raw_sha256": str(row["sha256"] or ""),
                    "retrieval_sha256": _sha256_text(retrieval),
                    "text": retrieval,
                }
            )
        return result

    def build(
        self,
        *,
        model: EmbeddingModel,
        provider: EmbeddingProvider,
        max_chunks: int = 1000,
        batch_size: int = 8,
    ) -> dict[str, Any]:
        max_chunks = max(1, int(max_chunks))
        batch_size = max(1, min(int(batch_size), MAX_EMBEDDING_BATCH))
        candidates = self._candidates(model)
        known = {
            str(row[0])
            for row in self.connection.execute(
                "SELECT embedding_key FROM mail_search_embeddings WHERE model_digest=?",
                (model.digest,),
            ).fetchall()
        }
        pending = [item for item in candidates if item["key"] not in known]
        selected = pending[:max_chunks]
        metrics: dict[str, Any] = {
            "ok": True,
            "state": "complete" if len(selected) == len(pending) else "partial",
            "contract_version": MAIL_EMBEDDING_CONTRACT_VERSION,
            "model": model.name,
            "model_digest": model.digest,
            "dimension": model.dimension,
            "candidate_chunks": len(candidates),
            "cache_hits": len(candidates) - len(pending),
            "requested": 0,
            "stored": 0,
            "remaining": max(0, len(pending) - len(selected)),
            "resumable": True,
            "queue_wait_ms": 0.0,
            "latency_ms": 0.0,
            "error": None,
        }
        for offset in range(0, len(selected), batch_size):
            batch = selected[offset : offset + batch_size]
            metrics["requested"] += len(batch)
            try:
                vectors, request_metrics = provider.embed(
                    [str(item["text"]) for item in batch], priority="background"
                )
                if len(vectors) != len(batch):
                    raise EmbeddingValidationError("Provider lieferte eine unvollstaendige Batchantwort")
                packed = [pack_vector(vector, dimension=model.dimension) for vector in vectors]
            except Exception as exc:
                category = exc.category if isinstance(exc, EmbeddingError) else "provider-error"
                metrics.update(
                    {
                        "ok": False,
                        "state": "degraded-lexical-only",
                        "error": {"category": category, "detail": str(exc)[:500]},
                        "remaining": len(pending) - int(metrics["stored"]),
                    }
                )
                break
            metrics["queue_wait_ms"] += float(request_metrics.get("queue_wait_ms") or 0.0)
            metrics["latency_ms"] += float(request_metrics.get("latency_ms") or 0.0)
            with self.connection:
                for item, payload in zip(batch, packed, strict=True):
                    self.connection.execute(
                        """
                        INSERT OR IGNORE INTO mail_search_embeddings(
                            embedding_key,content_id,document_id,chunk_id,chunk_index,
                            raw_sha256,retrieval_sha256,retrieval_text_version,
                            contract_version,model_name,model_digest,dimension,vector,
                            created_at
                        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                        """,
                        (
                            item["key"], item["content_id"], item["document_id"],
                            item["chunk_id"], item["chunk_index"], item["raw_sha256"],
                            item["retrieval_sha256"], MAIL_RETRIEVAL_TEXT_VERSION,
                            MAIL_EMBEDDING_CONTRACT_VERSION, model.name, model.digest,
                            model.dimension, payload, now_utc_iso(),
                        ),
                    )
                    metrics["stored"] += 1
        if metrics["ok"] and metrics["remaining"]:
            metrics["state"] = "partial"
        return metrics

    def search(
        self,
        query: str,
        *,
        model: EmbeddingModel,
        provider: EmbeddingProvider,
        limit: int = 20,
    ) -> dict[str, Any]:
        started = time.perf_counter()
        lexical_available = bool(
            self.connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='mail_search_fts'"
            ).fetchone()
        )
        value = str(query or "").strip()
        if not value:
            raise ValueError("Semantische Mail-Suche benoetigt einen Suchtext")
        try:
            vectors, request_metrics = provider.embed(
                [value[: model.context_limit]], priority="interactive"
            )
            query_vector = validate_vector(vectors[0], dimension=model.dimension)
            rows = self.connection.execute(
                """
                SELECT e.*,d.title,d.uri,d.modified_at,d.metadata_json,c.text
                FROM mail_search_embeddings e
                JOIN documents d ON d.id=e.document_id
                JOIN chunks c ON c.id=e.chunk_id
                WHERE e.contract_version=? AND e.model_digest=? AND e.dimension=?
                  AND d.source_type='email' AND d.resource_id='mail-agent'
                ORDER BY e.content_id,e.chunk_index
                """,
                (MAIL_EMBEDDING_CONTRACT_VERSION, model.digest, model.dimension),
            ).fetchall()
            best: dict[str, tuple[float, sqlite3.Row]] = {}
            for row in rows:
                stored = unpack_vector(bytes(row["vector"]), dimension=model.dimension)
                score = cosine_similarity(query_vector, stored)
                content_id = str(row["content_id"])
                if content_id not in best or score > best[content_id][0]:
                    best[content_id] = (score, row)
            ranked = sorted(best.values(), key=lambda item: (-item[0], str(item[1]["content_id"])))
            results = []
            for score, row in ranked[: max(1, min(int(limit), 200))]:
                try:
                    metadata = json.loads(str(row["metadata_json"] or "{}"))
                except json.JSONDecodeError:
                    metadata = {}
                results.append(
                    {
                        "role": "semantic-candidate",
                        "evidence_for_query": False,
                        "content_id": str(row["content_id"]),
                        "document_id": int(row["document_id"]),
                        "chunk_id": int(row["chunk_id"]),
                        "title": str(row["title"]),
                        "uri": str(row["uri"]),
                        "date": str(
                            metadata.get("received_at")
                            or metadata.get("date")
                            or row["modified_at"]
                            or ""
                        ),
                        "snippet": str(row["text"] or "")[:320],
                        "semantic": {
                            "score": round(score, 8),
                            "distance": round(1.0 - score, 8),
                            "ranking_version": MAIL_SEMANTIC_RANKING_VERSION,
                            "model": model.name,
                            "model_digest": model.digest,
                            "dimension": model.dimension,
                        },
                    }
                )
            return {
                "ok": True,
                "state": "ready" if rows else "missing-embeddings",
                "semantic_available": bool(rows),
                "lexical_available": lexical_available,
                "results": results,
                "result_count": len(results),
                "candidate_chunks": len(rows),
                "ranking_version": MAIL_SEMANTIC_RANKING_VERSION,
                "model": model.name,
                "model_digest": model.digest,
                "query": {"logged": False, "stored": False},
                "metrics": {
                    "latency_ms": round((time.perf_counter() - started) * 1000.0, 3),
                    "queue_wait_ms": float(request_metrics.get("queue_wait_ms") or 0.0),
                },
            }
        except Exception as exc:
            category = exc.category if isinstance(exc, EmbeddingError) else "provider-error"
            return {
                "ok": False,
                "state": "degraded-lexical-only",
                "semantic_available": False,
                "lexical_available": lexical_available,
                "results": [],
                "result_count": 0,
                "model": model.name,
                "model_digest": model.digest,
                "error": {"category": category, "detail": str(exc)[:500]},
                "query": {"logged": False, "stored": False},
                "metrics": {
                    "latency_ms": round((time.perf_counter() - started) * 1000.0, 3),
                    "queue_wait_ms": 0.0,
                },
            }
