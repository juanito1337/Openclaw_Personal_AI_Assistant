from __future__ import annotations

import hashlib
import io
import json
import subprocess
import urllib.error
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from personal_assistant.mail_embeddings import (
    MAIL_EMBEDDING_CONTRACT_VERSION,
    MAIL_SEMANTIC_RANKING_VERSION,
    EmbeddingModel,
    EmbeddingQueueFullError,
    EmbeddingTimeoutError,
    EmbeddingUnavailableError,
    MailEmbeddingIndex,
    OllamaCoordinatorEmbeddingClient,
    embedding_key,
    pack_vector,
)
from personal_assistant.storage import KNOWLEDGE_SCHEMA_VERSION, AssistantStorage

MODEL_A = EmbeddingModel(
    "synthetic-multilingual-a",
    "sha256:" + "a" * 64,
    3,
    4096,
)
MODEL_B = EmbeddingModel(
    "synthetic-multilingual-b",
    "sha256:" + "b" * 64,
    4,
    8192,
)


class FakeEmbeddingProvider:
    def __init__(self, dimension: int, *, failure: Exception | None = None) -> None:
        self.dimension = dimension
        self.failure = failure
        self.calls: list[tuple[list[str], str]] = []

    def embed(
        self,
        texts: Sequence[str],
        *,
        priority: str,
    ) -> tuple[list[list[float]], dict[str, Any]]:
        self.calls.append(([str(item) for item in texts], priority))
        if self.failure is not None:
            raise self.failure
        vectors: list[list[float]] = []
        for text in texts:
            folded = text.casefold()
            base = [
                3.0 if any(word in folded for word in ("dach", "roof", "gebäude")) else 0.2,
                3.0 if any(word in folded for word in ("pumpe", "pump")) else 0.2,
                3.0 if any(word in folded for word in ("garantie", "warranty")) else 0.2,
                0.5,
            ]
            vectors.append(base[: self.dimension])
        return vectors, {"latency_ms": 2.5, "queue_wait_ms": 0.5, "priority": priority}


def _record(
    key: str,
    body: str,
    *,
    folder: str = "INBOX",
    occurrences: tuple[str, ...] | None = None,
    quarantine: bool = False,
) -> dict[str, Any]:
    digest = hashlib.sha256(f"{key}:{body}".encode()).hexdigest()
    occurrence_names = occurrences or (key,)
    occurrence_ids = [f"occurrence:{value}" for value in occurrence_names]
    locators = [
        {
            "occurrence_id": occurrence_id,
            "locator_id": f"locator:{name}:{folder}",
            "folder_id": f"folder:{folder}",
            "folder_name": folder,
            "mailbox_id": name,
            "uidvalidity": "1",
            "uid": name,
            "observed_at": "2026-08-20T08:00:00+00:00",
            "is_current": True,
            "quarantine": quarantine,
        }
        for name, occurrence_id in zip(occurrence_names, occurrence_ids, strict=True)
    ]
    return {
        "content_id": f"content:{key}",
        "message_id": f"{key}@example.invalid",
        "sha256": digest,
        "title": f"Synthetic {key}",
        "modified_at": "2026-08-20T08:00:00+00:00",
        "occurrence_ids": occurrence_ids,
        "chunks": [body],
        "metadata": {
            "sender_addr": "sender@example.invalid",
            "sender_name": "Synthetic Sender",
            "recipients": ["owner@example.invalid"],
            "received_at": "2026-08-20T08:00:00+00:00",
            "date": "2026-08-20T08:00:00+00:00",
            "attachments": [],
            "parser_version": "mail-parser-v1",
            "normalization_version": "mail-normalization-v1",
            "tag_version": "mail-tags-v1",
            "source_status": "quarantine-untrusted" if quarantine else "active",
            "occurrence_ids": occurrence_ids,
            "locators": locators,
        },
    }


def _publish(storage: AssistantStorage, records: list[dict[str, Any]], generation: str) -> None:
    storage.apply_mail_projection(
        generation=generation,
        generated_at=datetime.now(UTC).replace(microsecond=0).isoformat(),
        coverage={"resource_id": "mail-agent", "authoritative": True},
        records=records,
    )


@pytest.fixture
def storage(tmp_path: Path):
    value = AssistantStorage(tmp_path / "assistant.sqlite3")
    _publish(
        value,
        [
            _record("roof", "Die Dachreparatur kostet 8200 Euro."),
            _record("pump", "The circulation pump repair is complete."),
        ],
        "m116-a",
    )
    try:
        yield value
    finally:
        value.close()


def test_schema_v5_has_content_keyed_embedding_table(storage: AssistantStorage) -> None:
    assert KNOWLEDGE_SCHEMA_VERSION == 5
    assert storage.knowledge_connection.execute("PRAGMA user_version").fetchone()[0] == 5
    columns = {
        str(row[1])
        for row in storage.knowledge_connection.execute(
            "PRAGMA table_info(mail_search_embeddings)"
        ).fetchall()
    }
    assert {
        "embedding_key",
        "raw_sha256",
        "retrieval_sha256",
        "retrieval_text_version",
        "model_digest",
        "dimension",
        "vector",
    } <= columns


def test_schema_four_migrates_additively_to_embedding_schema(tmp_path: Path) -> None:
    database = tmp_path / "knowledge-v4.sqlite3"
    first = AssistantStorage(database)
    first.knowledge_connection.execute("DROP TABLE mail_search_embeddings")
    first.knowledge_connection.execute("PRAGMA user_version=4")
    first.knowledge_connection.commit()
    first.close()

    upgraded = AssistantStorage(database)
    try:
        assert upgraded.knowledge_connection.execute("PRAGMA user_version").fetchone()[0] == 5
        assert upgraded.knowledge_connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='mail_search_embeddings'"
        ).fetchone()[0] == 1
        assert upgraded.integrity() == "ok"
    finally:
        upgraded.close()


def test_embedding_key_has_no_locator_folder_or_quarantine_input() -> None:
    values = {
        "raw_sha256": "1" * 64,
        "retrieval_text": "same normalized text",
        "retrieval_text_version": "mail-retrieval-text-v1",
        "chunk_index": 0,
        "model": MODEL_A,
    }
    before = embedding_key(**values)
    after = embedding_key(**values)
    assert before == after
    assert len(before) == 64


def test_build_stores_vectors_and_reuses_complete_cache(storage: AssistantStorage) -> None:
    provider = FakeEmbeddingProvider(3)
    first = storage.build_mail_embeddings(model=MODEL_A, provider=provider, batch_size=2)
    second = storage.build_mail_embeddings(model=MODEL_A, provider=provider, batch_size=2)

    assert first["ok"] is True
    assert first["stored"] == 2
    assert first["requested"] == 2
    assert second["stored"] == 0
    assert second["cache_hits"] == 2
    assert len(provider.calls) == 1
    assert provider.calls[0][1] == "background"
    row = storage.knowledge_connection.execute(
        "SELECT contract_version,model_digest,dimension,length(vector) AS bytes "
        "FROM mail_search_embeddings LIMIT 1"
    ).fetchone()
    assert row["contract_version"] == MAIL_EMBEDDING_CONTRACT_VERSION
    assert row["model_digest"] == MODEL_A.digest
    assert row["dimension"] == 3
    assert row["bytes"] == 12


def test_partial_build_resumes_without_repeating_cached_chunk(storage: AssistantStorage) -> None:
    provider = FakeEmbeddingProvider(3)
    partial = storage.build_mail_embeddings(
        model=MODEL_A, provider=provider, max_chunks=1, batch_size=1
    )
    resumed = storage.build_mail_embeddings(
        model=MODEL_A, provider=provider, max_chunks=10, batch_size=1
    )

    assert partial["state"] == "partial"
    assert partial["remaining"] == 1
    assert resumed["state"] == "complete"
    assert resumed["cache_hits"] == 1
    assert resumed["stored"] == 1
    assert sum(len(call[0]) for call in provider.calls) == 2


def test_model_change_creates_separate_vectors(storage: AssistantStorage) -> None:
    first = FakeEmbeddingProvider(3)
    second = FakeEmbeddingProvider(4)
    storage.build_mail_embeddings(model=MODEL_A, provider=first)
    result = storage.build_mail_embeddings(model=MODEL_B, provider=second)

    assert result["stored"] == 2
    assert storage.knowledge_connection.execute(
        "SELECT COUNT(DISTINCT model_digest) FROM mail_search_embeddings"
    ).fetchone()[0] == 2


def test_changed_chunk_invalidates_old_vector_and_requests_one_new(
    storage: AssistantStorage,
) -> None:
    provider = FakeEmbeddingProvider(3)
    storage.build_mail_embeddings(model=MODEL_A, provider=provider)
    changed = _record("roof", "Das Gebäudedach kostet nun 8300 Euro.")
    unchanged = _record("pump", "The circulation pump repair is complete.")

    _publish(storage, [changed, unchanged], "m116-changed")
    result = storage.build_mail_embeddings(model=MODEL_A, provider=provider)

    assert result["cache_hits"] == 1
    assert result["stored"] == 1
    assert storage.knowledge_connection.execute(
        "SELECT COUNT(*) FROM mail_search_embeddings WHERE model_digest=?",
        (MODEL_A.digest,),
    ).fetchone()[0] == 2


def test_move_copy_and_quarantine_change_make_zero_embedding_requests(
    storage: AssistantStorage,
) -> None:
    provider = FakeEmbeddingProvider(3)
    storage.build_mail_embeddings(model=MODEL_A, provider=provider)
    calls = len(provider.calls)
    roof = _record(
        "roof",
        "Die Dachreparatur kostet 8200 Euro.",
        folder="Archiv",
        occurrences=("roof", "roof-copy"),
        quarantine=True,
    )
    pump = _record("pump", "The circulation pump repair is complete.", folder="Gesendet")

    _publish(storage, [roof, pump], "m116-locator-only")
    result = storage.build_mail_embeddings(model=MODEL_A, provider=provider)

    assert result["cache_hits"] == 2
    assert result["requested"] == 0
    assert len(provider.calls) == calls


@pytest.mark.parametrize(
    ("vector", "detail"),
    [
        ([1.0, 2.0], "Dimension"),
        ([1.0, float("nan"), 2.0], "NaN"),
        ([1.0, float("inf"), 2.0], "Infinity"),
        ([0.0, 0.0, 0.0], "Richtung"),
    ],
)
def test_invalid_vectors_degrade_without_writing(
    storage: AssistantStorage,
    vector: list[float],
    detail: str,
) -> None:
    class InvalidProvider:
        def embed(self, texts: Sequence[str], *, priority: str):
            return [vector for _item in texts], {}

    result = storage.build_mail_embeddings(model=MODEL_A, provider=InvalidProvider())

    assert result["ok"] is False
    assert result["state"] == "degraded-lexical-only"
    assert detail in result["error"]["detail"]
    assert storage.knowledge_connection.execute(
        "SELECT COUNT(*) FROM mail_search_embeddings"
    ).fetchone()[0] == 0
    lexical = storage.search_mail_lexical("Dachreparatur", max_age_seconds=10**9)
    assert len(lexical["results"]) == 1


@pytest.mark.parametrize(
    ("failure", "category"),
    [
        (EmbeddingTimeoutError("timeout"), "provider-timeout"),
        (EmbeddingQueueFullError("full"), "queue-full"),
        (EmbeddingUnavailableError("offline"), "proxy-unavailable"),
    ],
)
def test_provider_failures_are_visible_and_fts_remains_available(
    storage: AssistantStorage,
    failure: Exception,
    category: str,
) -> None:
    result = storage.build_mail_embeddings(
        model=MODEL_A,
        provider=FakeEmbeddingProvider(3, failure=failure),
    )

    assert result["state"] == "degraded-lexical-only"
    assert result["error"]["category"] == category
    assert storage.search_mail_lexical("pump", max_age_seconds=10**9)["ok"] is True


def test_semantic_search_reports_score_distance_and_non_factual_role(
    storage: AssistantStorage,
) -> None:
    provider = FakeEmbeddingProvider(3)
    storage.build_mail_embeddings(model=MODEL_A, provider=provider)

    result = storage.search_mail_semantic(
        "Was kostet die Reparatur des Gebäudedachs?",
        model=MODEL_A,
        provider=provider,
        limit=2,
    )

    assert result["ok"] is True
    assert result["results"][0]["content_id"] == "content:roof"
    assert result["results"][0]["role"] == "semantic-candidate"
    assert result["results"][0]["evidence_for_query"] is False
    semantic = result["results"][0]["semantic"]
    assert semantic["ranking_version"] == MAIL_SEMANTIC_RANKING_VERSION
    assert semantic["distance"] == pytest.approx(1.0 - semantic["score"])
    assert provider.calls[-1][1] == "interactive"
    assert result["query"] == {"logged": False, "stored": False}


def test_semantic_query_failure_explicitly_degrades_to_lexical_state(
    storage: AssistantStorage,
) -> None:
    builder = FakeEmbeddingProvider(3)
    storage.build_mail_embeddings(model=MODEL_A, provider=builder)

    result = storage.search_mail_semantic(
        "roof",
        model=MODEL_A,
        provider=FakeEmbeddingProvider(3, failure=EmbeddingTimeoutError("timeout")),
    )

    assert result["ok"] is False
    assert result["state"] == "degraded-lexical-only"
    assert result["lexical_available"] is True
    assert result["results"] == []


def test_corrupt_stored_dimension_fails_closed_but_does_not_break_fts(
    storage: AssistantStorage,
) -> None:
    provider = FakeEmbeddingProvider(3)
    storage.build_mail_embeddings(model=MODEL_A, provider=provider)
    storage.knowledge_connection.execute(
        "UPDATE mail_search_embeddings SET vector=? WHERE embedding_key=("
        "SELECT embedding_key FROM mail_search_embeddings LIMIT 1)",
        (b"broken",),
    )
    storage.knowledge_connection.commit()

    result = storage.search_mail_semantic("roof", model=MODEL_A, provider=provider)

    assert result["ok"] is False
    assert result["error"]["category"] == "invalid-vector"
    assert storage.search_mail_lexical("pump", max_age_seconds=10**9)["ok"] is True


def test_ollama_client_uses_embed_route_priority_and_bounded_timeout() -> None:
    captured: dict[str, Any] = {}

    class Response:
        headers = {"X-Ollama-Queue-Wait-Ms": "12.5"}

        def __enter__(self):
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def read(self, limit: int) -> bytes:
            return json.dumps({"embeddings": [[1.0, 0.0, 0.0]]}).encode()

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["priority"] = request.get_header("X-openclaw-priority")
        captured["source"] = request.get_header("X-openclaw-source")
        captured["timeout"] = timeout
        return Response()

    client = OllamaCoordinatorEmbeddingClient(
        base_url="http://ollama-proxy:11435",
        model=MODEL_A,
        timeout_seconds=20,
        queue_timeout_seconds=10,
    )
    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        vectors, metrics = client.embed(["synthetic text"], priority="background")

    assert captured == {
        "url": "http://ollama-proxy:11435/api/embed",
        "priority": "background",
        "source": "mail-semantic-index",
        "timeout": 35.0,
    }
    assert vectors == [[1.0, 0.0, 0.0]]
    assert metrics["queue_wait_ms"] == 12.5


def test_ollama_inventory_must_match_full_model_digest() -> None:
    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def read(self, limit: int) -> bytes:
            return json.dumps(
                {"models": [{"name": MODEL_A.name, "digest": MODEL_A.digest, "size": 1234}]}
            ).encode()

    client = OllamaCoordinatorEmbeddingClient(
        base_url="http://ollama-proxy:11435", model=MODEL_A
    )
    with patch("urllib.request.urlopen", return_value=Response()):
        result = client.verify_installed_model()

    assert result == {
        "name": MODEL_A.name,
        "digest": MODEL_A.digest,
        "size_bytes": 1234,
        "verified": True,
    }


def test_ollama_queue_full_response_has_typed_error() -> None:
    client = OllamaCoordinatorEmbeddingClient(
        base_url="http://ollama-proxy:11435", model=MODEL_A
    )
    error = urllib.error.HTTPError(
        client.base_url + "/api/embed",
        503,
        "Service unavailable",
        {},
        io.BytesIO(json.dumps({"error_type": "queue_full", "error": "full"}).encode()),
    )
    with (
        patch("urllib.request.urlopen", side_effect=error),
        pytest.raises(EmbeddingQueueFullError, match="full"),
    ):
        client.embed(["synthetic"], priority="background")


def test_model_contract_rejects_missing_digest_and_invalid_dimension() -> None:
    with pytest.raises(ValueError, match="digest"):
        EmbeddingModel("model", "latest", 3, 4096)
    with pytest.raises(ValueError, match="Dimension"):
        EmbeddingModel("model", "sha256:" + "c" * 64, 0, 4096)


def test_embedding_client_rejects_direct_upstream_bypass() -> None:
    with pytest.raises(ValueError, match="Prioritaetsproxy"):
        OllamaCoordinatorEmbeddingClient(
            base_url="http://127.0.0.1:11434",
            model=MODEL_A,
        )


def test_vector_payload_is_fixed_float32_not_json() -> None:
    payload = pack_vector([1.0, 2.0, 3.0], dimension=3)
    assert len(payload) == 12
    assert not payload.startswith(b"[")


def test_direct_index_class_uses_same_storage_contract(storage: AssistantStorage) -> None:
    result = MailEmbeddingIndex(storage.knowledge_connection).build(
        model=MODEL_A,
        provider=FakeEmbeddingProvider(3),
    )
    assert result["contract_version"] == MAIL_EMBEDDING_CONTRACT_VERSION


def test_hermetic_two_model_benchmark_is_not_activation_evidence() -> None:
    completed = subprocess.run(
        ["python3", "scripts/benchmark_mail_embeddings_m116.py"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr or completed.stdout
    payload = json.loads(completed.stdout)
    assert payload["model_count"] == 2
    assert payload["mode"] == "fixture"
    assert payload["selection"]["selected"] is None
    assert payload["selection"]["activation_allowed"] is False
    for model in payload["models"]:
        assert model["measurement_kind"] == "synthetic-contract"
        assert model["eligible_for_activation"] is False
        assert {"mean_recall_at_5", "mean_recall_at_10", "mrr", "mean_ndcg_at_10"} <= set(
            model["quality"]
        )
        assert {"p50_ms", "p95_ms", "cold_index_ms", "warm_p50_ms"} <= set(
            model["latency"]
        )
        assert {"model_size_bytes", "dimension", "context_limit_chars", "digest"} <= set(model)
