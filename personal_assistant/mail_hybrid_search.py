from __future__ import annotations

import json
import sqlite3
import time
from collections.abc import Callable
from typing import Any, Protocol

from .mail_embeddings import EmbeddingModel, OllamaCoordinatorEmbeddingClient
from .mail_search import (
    MAX_THREAD_CONTEXT,
    MailLexicalSearch,
    MailSearchFilters,
    normalize_tag_value,
    parse_mail_query,
    parse_tag_filter,
    query_centered_snippet,
)

MAIL_HYBRID_RANKING_VERSION = "mail-hybrid-rrf-v1"
RRF_K = 60
LEXICAL_WEIGHT = 1.0
SEMANTIC_WEIGHT = 0.7
STRUCTURED_WEIGHT = 0.10
THREAD_WEIGHT = 0.05
SEARCH_MODES = frozenset({"auto", "local", "server"})


class LiveMailSearch(Protocol):
    def search_messages(self, query: str, *, limit: int = 50) -> dict[str, Any]: ...

    def resolve_live_locators(self, candidates: list[dict[str, Any]]) -> dict[str, Any]: ...


SemanticProviderFactory = Callable[[EmbeddingModel], Any]


def _configured_model(config: Any) -> tuple[EmbeddingModel | None, str]:
    provider = str(getattr(config, "semantic_provider", "disabled") or "disabled")
    if provider == "disabled":
        return None, "disabled"
    try:
        return (
            EmbeddingModel(
                name=str(getattr(config, "semantic_model", "")),
                digest=str(getattr(config, "semantic_model_digest", "")),
                dimension=int(getattr(config, "semantic_dimension", 0)),
                context_limit=int(getattr(config, "semantic_context_limit", 8192)),
            ),
            "configured",
        )
    except (TypeError, ValueError):
        return None, "misconfigured"


def _server_filter_limitations(filters: MailSearchFilters) -> list[str]:
    limitations: list[str] = []
    if filters.participant:
        limitations.append("participant")
    if filters.category:
        limitations.append("category")
    if filters.review_reason:
        limitations.append("review-reason")
    if filters.has_attachment is not None:
        limitations.append("has-attachment")
    if filters.attachment_type:
        limitations.append("attachment-type")
    if filters.tags:
        limitations.append("tag")
    return limitations


class MailHybridSearch:
    """Agent-facing M11.7 routing without granting mail actions."""

    def __init__(
        self,
        storage: Any,
        server: LiveMailSearch,
        config: Any,
        *,
        semantic_provider_factory: SemanticProviderFactory | None = None,
    ) -> None:
        self.storage = storage
        self.server = server
        self.config = config
        self.semantic_provider_factory = semantic_provider_factory

    def _provider(self, model: EmbeddingModel) -> Any:
        if self.semantic_provider_factory is not None:
            return self.semantic_provider_factory(model)
        return OllamaCoordinatorEmbeddingClient(
            base_url=str(
                getattr(
                    self.config,
                    "ollama_coordinator_url",
                    "http://127.0.0.1:11435",
                )
            ),
            model=model,
        )

    @staticmethod
    def _known_server_filter(item: dict[str, Any], filters: MailSearchFilters) -> bool:
        if filters.folder and normalize_tag_value(str(item.get("folder") or "")) != filters.folder:
            return False
        if filters.sender and normalize_tag_value(str(item.get("sender_addr") or "")) != filters.sender:
            return False
        timestamp = str(item.get("received_at") or item.get("date") or "")
        if filters.after and timestamp and timestamp < filters.after:
            return False
        return not (filters.before and timestamp and timestamp >= filters.before)

    def _server_result(
        self,
        query: str,
        *,
        filters: MailSearchFilters,
        limit: int,
        mode: str,
        fallback_reason: list[str],
        index_status: dict[str, Any] | None,
    ) -> dict[str, Any]:
        started = time.perf_counter()
        base = self.server.search_messages(query, limit=limit)
        limitations = list(
            dict.fromkeys(
                [
                    *_server_filter_limitations(filters),
                    *(str(item) for item in base.get("filter_limitations") or []),
                ]
            )
        )
        messages = [
            dict(item)
            for item in base.get("messages") or []
            if self._known_server_filter(dict(item), filters)
        ][:limit]
        results: list[dict[str, Any]] = []
        for item in messages:
            folder = str(item.get("folder") or "")
            mailbox_id = str(item.get("mailbox_id") or "")
            locator = {
                "occurrence_id": "",
                "locator_id": "",
                "resource_id": "mail-agent",
                "folder_id": "",
                "folder": folder,
                "mailbox_id": mailbox_id,
                "uidvalidity": "",
                "uid": "",
                "observed_at": "live",
                "current_in_index": False,
                "stale": False,
                "quarantine": False,
                "source_status": "live-server",
                "source_generation": "",
                "conflict_code": "",
                "live_state": "server-result",
                "selected": True,
                "selection": "server-search",
            }
            result = {
                **item,
                "role": "query-hit",
                "query_match": True,
                "evidence_for_query": True,
                "content_id": "",
                "occurrence_ids": [],
                "locators": [locator],
                "live_locator": locator,
                "snippet": "",
                "tags": [],
                "thread": None,
                "context": [],
                "ranking": {
                    "hybrid_version": MAIL_HYBRID_RANKING_VERSION,
                    "backend": "server",
                    "score": None,
                },
                "match": {
                    "reasons": [str(item.get("match_source") or "server-query")],
                    "fields": list(item.get("match_fields") or []),
                    "body_verified": item.get("body_match_verified") is True,
                },
                "source_reference": {
                    "resource_id": "mail-agent",
                    "folder": folder,
                    "mailbox_id": mailbox_id,
                    "expected_subject": str(item.get("subject") or ""),
                    "locator_validation": "server-search",
                },
            }
            results.append(result)
        complete = bool(base.get("complete")) and not limitations
        folder_errors = list(base.get("folder_errors") or [])
        return {
            "ok": bool(base.get("ok")),
            "path": "server-mail-search",
            "backend": "server",
            "mode": mode,
            "read_only": True,
            "complete": complete,
            "coverage": {
                "authoritative": bool(base.get("complete")),
                "searched_folders": int(base.get("searched_folders") or 0),
                "total_folders": int(base.get("total_folders") or 0),
                "ratio": round(
                    int(base.get("searched_folders") or 0)
                    / max(1, int(base.get("total_folders") or 0)),
                    6,
                ),
            },
            "freshness": {"fresh": True, "source": "live-server"},
            "index_generation": str((index_status or {}).get("generation") or ""),
            "semantic_state": "not-used-server-fallback",
            "fallback_used": bool(fallback_reason),
            "fallback_reason": fallback_reason,
            "folder_errors": folder_errors,
            "filter_limitations": limitations,
            "results_may_be_truncated": bool(base.get("results_may_be_truncated")),
            "search_scope": dict(base.get("search_scope") or {}),
            "metadata_fallback": dict(base.get("metadata_fallback") or {}),
            "count": len(results),
            "results": results,
            "messages": results,
            "query": {"stored": False, "logged": False},
            "index": index_status or {},
            "metrics": {
                "latency_ms": round((time.perf_counter() - started) * 1000.0, 3),
                "local_backend_calls": 0,
                "server_search_calls": 1,
            },
            "read_contract": {
                "server_revalidation_required": True,
                "required_fields": ["folder", "mailbox_id", "expected_subject"],
                "index_authorizes_actions": False,
            },
        }

    def _active_tags(self, content_id: str) -> list[dict[str, Any]]:
        rows = self.storage.knowledge_connection.execute(
            """
            SELECT namespace,value,source,source_version,confidence,evidence_json,
                   active,uncertainty
            FROM mail_search_tags WHERE content_id=?
            ORDER BY active DESC,namespace,value,source,source_version
            """,
            (content_id,),
        ).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            try:
                evidence = json.loads(str(row["evidence_json"] or "{}"))
            except json.JSONDecodeError:
                evidence = {}
            result.append(
                {
                    "namespace": str(row["namespace"]),
                    "value": str(row["value"]),
                    "source": str(row["source"]),
                    "source_version": str(row["source_version"]),
                    "confidence": row["confidence"],
                    "evidence": evidence,
                    "active": bool(row["active"]),
                    "uncertainty": str(row["uncertainty"] or ""),
                }
            )
        return result

    @staticmethod
    def _tag_set(tags: list[dict[str, Any]]) -> set[tuple[str, str]]:
        return {
            (str(item["namespace"]), str(item["value"]))
            for item in tags
            if bool(item.get("active"))
        }

    def _semantic_details(
        self,
        item: dict[str, Any],
        *,
        query: str,
        filters: MailSearchFilters,
    ) -> dict[str, Any] | None:
        content_id = str(item.get("content_id") or "")
        row = self.storage.knowledge_connection.execute(
            """
            SELECT d.*,c.text AS source_text FROM documents d
            LEFT JOIN chunks c ON c.document_id=d.id AND c.chunk_index=0
            WHERE d.content_id=? AND d.source_type='email' AND d.resource_id='mail-agent'
            ORDER BY c.id LIMIT 1
            """,
            (content_id,),
        ).fetchone()
        if row is None:
            return None
        try:
            metadata = json.loads(str(row["metadata_json"] or "{}"))
        except json.JSONDecodeError:
            return None
        tags = self._active_tags(content_id)
        active = self._tag_set(tags)
        required = [
            ("sender", filters.sender),
            ("participant", filters.participant),
            ("folder", filters.folder),
            ("category", filters.category),
            ("review", filters.review_reason),
            ("attachment-type", filters.attachment_type),
        ]
        if any(value and (namespace, value) not in active for namespace, value in required):
            return None
        if filters.has_attachment is not None:
            has_attachment = ("has", "attachment") in active
            if has_attachment != filters.has_attachment:
                return None
        if any(parse_tag_filter(raw) not in active for raw in filters.tags):
            return None
        timestamp = str(
            metadata.get("received_at") or metadata.get("date") or row["modified_at"] or ""
        )
        if filters.after and timestamp < filters.after:
            return None
        if filters.before and timestamp >= filters.before:
            return None
        searcher = MailLexicalSearch(
            self.storage.knowledge_connection,
            fts_enabled=self.storage.mail_search_fts_enabled,
        )
        occurrences, locators = searcher.locators(content_id)
        parsed_query = parse_mail_query(query)
        return {
            "role": "semantic-candidate",
            "query_match": False,
            "evidence_for_query": False,
            "document_id": int(row["id"]),
            "content_id": content_id,
            "source_id": str(row["source_id"]),
            "title": str(row["title"]),
            "subject": str(row["title"]),
            "uri": str(row["uri"]),
            "date": timestamp,
            "sender": {
                "name": str(metadata.get("sender_name") or ""),
                "address": str(metadata.get("sender_addr") or ""),
            },
            "sender_name": str(metadata.get("sender_name") or ""),
            "sender_addr": str(metadata.get("sender_addr") or ""),
            "folders": sorted(
                {
                    str(locator.get("folder") or "")
                    for locator in locators
                    if str(locator.get("folder") or "")
                }
            ),
            "snippet": query_centered_snippet(
                str(item.get("snippet") or row["source_text"] or ""), parsed_query
            ),
            "tags": tags,
            "thread": searcher._thread_metadata(content_id),
            "context": [],
            "occurrence_ids": occurrences,
            "locators": locators,
            "live_locator": None,
            "source_generation": str(row["index_generation"] or ""),
            "source_status": str(row["source_status"] or ""),
            "source_reference": {
                "resource_id": "mail-agent",
                "content_id": content_id,
                "occurrence_ids": occurrences,
                "index_generation": str(row["index_generation"] or ""),
                "locator_validation": "pending",
            },
            "semantic": dict(item.get("semantic") or {}),
        }

    @staticmethod
    def _structured_count(filters: MailSearchFilters) -> int:
        return sum(
            bool(value)
            for value in (
                filters.sender,
                filters.participant,
                filters.after,
                filters.before,
                filters.folder,
                filters.category,
                filters.review_reason,
                filters.attachment_type,
            )
        ) + len(filters.tags) + int(filters.has_attachment is not None)

    def _fuse(
        self,
        lexical: dict[str, Any],
        semantic: dict[str, Any],
        *,
        query: str,
        filters: MailSearchFilters,
        limit: int,
        context_limit: int,
    ) -> list[dict[str, Any]]:
        candidates: dict[str, dict[str, Any]] = {}
        lexical_rank: dict[str, int] = {}
        semantic_rank: dict[str, int] = {}
        for rank, raw in enumerate(lexical.get("results") or [], start=1):
            item = dict(raw)
            content_id = str(item.get("content_id") or "")
            if not content_id:
                continue
            lexical_rank[content_id] = rank
            item["lexical_score"] = item.get("score")
            candidates[content_id] = item
        for rank, raw in enumerate(semantic.get("results") or [], start=1):
            details = self._semantic_details(dict(raw), query=query, filters=filters)
            if details is None:
                continue
            content_id = str(details["content_id"])
            semantic_rank[content_id] = rank
            if content_id in candidates:
                candidates[content_id]["semantic"] = details.get("semantic")
            else:
                candidates[content_id] = details
        structured_count = self._structured_count(filters)
        ranked: list[tuple[float, str, dict[str, Any]]] = []
        for content_id, item in candidates.items():
            lexical_component = (
                LEXICAL_WEIGHT / (RRF_K + lexical_rank[content_id])
                if content_id in lexical_rank
                else 0.0
            )
            semantic_component = (
                SEMANTIC_WEIGHT / (RRF_K + semantic_rank[content_id])
                if content_id in semantic_rank
                else 0.0
            )
            structured_component = (
                STRUCTURED_WEIGHT * structured_count / (RRF_K + 1)
                if structured_count
                else 0.0
            )
            thread_value = item.get("thread")
            thread: dict[str, Any] = thread_value if isinstance(thread_value, dict) else {}
            thread_component = (
                THREAD_WEIGHT / (RRF_K + 1)
                if int(thread.get("member_count") or 0) > 1
                else 0.0
            )
            score = lexical_component + semantic_component + structured_component + thread_component
            reasons = []
            if lexical_component:
                reasons.append("lexical")
            if semantic_component:
                reasons.append("semantic-candidate")
            if structured_component:
                reasons.append("structured-filter")
            if thread_component:
                reasons.append("thread-context-available")
            item["ranking"] = {
                "hybrid_version": MAIL_HYBRID_RANKING_VERSION,
                "rrf_k": RRF_K,
                "lexical_rank": lexical_rank.get(content_id),
                "semantic_rank": semantic_rank.get(content_id),
                "lexical_component": round(lexical_component, 10),
                "semantic_component": round(semantic_component, 10),
                "structured_component": round(structured_component, 10),
                "thread_component": round(thread_component, 10),
                "score": round(score, 10),
            }
            item.setdefault("match", {})["reasons"] = reasons
            item["score"] = round(score, 10)
            ranked.append((score, content_id, item))
        ranked.sort(key=lambda row: (-row[0], row[1]))
        results = [item for _score, _content_id, item in ranked[:limit]]
        if context_limit:
            searcher = MailLexicalSearch(
                self.storage.knowledge_connection,
                fts_enabled=self.storage.mail_search_fts_enabled,
            )
            hit_ids = {str(item["content_id"]) for item in results}
            for item in results:
                if not item.get("context"):
                    item["context"] = searcher._thread_context(
                        str(item["content_id"]),
                        query_hit_ids=hit_ids,
                        limit=context_limit,
                    )
        return results

    @staticmethod
    def _legacy_aliases(item: dict[str, Any]) -> None:
        locator_value = item.get("live_locator")
        sender_value = item.get("sender")
        locator: dict[str, Any] = locator_value if isinstance(locator_value, dict) else {}
        sender: dict[str, Any] = sender_value if isinstance(sender_value, dict) else {}
        item["folder"] = str(locator.get("folder") or "")
        item["mailbox_id"] = str(locator.get("mailbox_id") or "")
        item["subject"] = str(item.get("title") or item.get("subject") or "")
        item["sender_name"] = str(sender.get("name") or item.get("sender_name") or "")
        item["sender_addr"] = str(sender.get("address") or item.get("sender_addr") or "")
        item["received_at"] = str(item.get("date") or item.get("received_at") or "")

    def search(
        self,
        query: str,
        *,
        filters: MailSearchFilters | None = None,
        limit: int = 50,
        mode: str = "auto",
        context_limit: int = 0,
    ) -> dict[str, Any]:
        started = time.perf_counter()
        mode = str(mode or "auto").strip().casefold()
        if mode not in SEARCH_MODES:
            raise ValueError("Mail-Suchmodus muss auto, local oder server sein")
        limit = max(1, min(int(limit), 200))
        context_limit = int(context_limit)
        if not 0 <= context_limit <= MAX_THREAD_CONTEXT:
            raise ValueError(f"Thread-Kontext muss zwischen 0 und {MAX_THREAD_CONTEXT} liegen")
        normalized_filters = (filters or MailSearchFilters()).normalized()
        parsed = parse_mail_query(query)
        if not parsed.match:
            raise ValueError("Mail-Suche benoetigt einen durchsuchbaren Suchtext")
        model, semantic_configuration = _configured_model(self.config)
        max_age = int(getattr(self.config, "mail_projection_max_age_seconds", 7200))
        if mode == "server":
            return self._server_result(
                query,
                filters=normalized_filters,
                limit=limit,
                mode=mode,
                fallback_reason=[],
                index_status=None,
            )
        index_status = self.storage.mail_index_status(
            max_age_seconds=max_age,
            semantic_model=model,
        )
        if mode == "auto" and not bool(index_status.get("search_eligible")):
            return self._server_result(
                query,
                filters=normalized_filters,
                limit=limit,
                mode=mode,
                fallback_reason=list(index_status.get("reasons") or ["index-unavailable"]),
                index_status=index_status,
            )
        try:
            lexical = self.storage.search_mail_lexical(
                query,
                filters=normalized_filters,
                limit=max(limit * 4, limit),
                max_age_seconds=max_age,
                context_limit=context_limit,
            )
        except (RuntimeError, sqlite3.DatabaseError) as exc:
            if mode == "auto":
                return self._server_result(
                    query,
                    filters=normalized_filters,
                    limit=limit,
                    mode=mode,
                    fallback_reason=["fts-failed", type(exc).__name__],
                    index_status=index_status,
                )
            lexical = {
                "ok": False,
                "complete": False,
                "results": [],
                "results_may_be_truncated": False,
                "error": {"category": "fts-failed", "detail": str(exc)[:500]},
                "metrics": {},
            }
        semantic: dict[str, Any] = {
            "ok": True,
            "state": semantic_configuration,
            "semantic_available": False,
            "results": [],
            "metrics": {"latency_ms": 0.0, "queue_wait_ms": 0.0},
        }
        if model is not None and str(index_status.get("semantic", {}).get("state")) in {
            "ready",
            "partial",
        }:
            try:
                provider = self._provider(model)
                verifier = getattr(provider, "verify_installed_model", None)
                if callable(verifier):
                    verifier()
                semantic = self.storage.search_mail_semantic(
                    query,
                    model=model,
                    provider=provider,
                    limit=max(limit * 4, limit),
                )
            except Exception as exc:
                semantic = {
                    "ok": False,
                    "state": "degraded-lexical-only",
                    "semantic_available": False,
                    "results": [],
                    "error": {"category": "semantic-failed", "detail": str(exc)[:500]},
                    "metrics": {"latency_ms": 0.0, "queue_wait_ms": 0.0},
                }
        results = self._fuse(
            lexical,
            semantic,
            query=query,
            filters=normalized_filters,
            limit=limit,
            context_limit=context_limit,
        )
        locator_result: dict[str, Any] = {
            "ok": True,
            "complete": True,
            "results": [],
            "folder_errors": [],
            "backend_calls": {"list_folders": 0, "list_envelopes": 0, "search_envelopes": 0},
        }
        if results:
            locator_result = self.server.resolve_live_locators(results)
            locator_rows = locator_result.get("results")
            if not isinstance(locator_rows, list):
                locator_rows = []
            by_content = {
                str(item.get("content_id") or ""): item
                for item in locator_rows
                if isinstance(item, dict)
            }
            for item in results:
                resolved: dict[str, Any] = by_content.get(
                    str(item.get("content_id") or ""), {}
                )
                locator_values = resolved.get("locators") or item.get("locators") or []
                item["locators"] = list(locator_values) if isinstance(locator_values, list) else []
                item["live_locator"] = resolved.get("live_locator")
                item["locator_state"] = str(resolved.get("state") or "missing")
                source_value = item.get("source_reference")
                source_reference: dict[str, Any] = (
                    dict(source_value) if isinstance(source_value, dict) else {}
                )
                source_reference["locator_validation"] = item["locator_state"]
                item["source_reference"] = source_reference
                self._legacy_aliases(item)
        if mode == "auto" and not bool(locator_result.get("complete")):
            return self._server_result(
                query,
                filters=normalized_filters,
                limit=limit,
                mode=mode,
                fallback_reason=["live-locator-incomplete"],
                index_status=index_status,
            )
        complete = bool(
            lexical.get("complete")
            and index_status.get("search_eligible")
            and locator_result.get("complete")
        )
        semantic_state = str(semantic.get("state") or semantic_configuration)
        folder_error_value = locator_result.get("folder_errors")
        folder_errors = folder_error_value if isinstance(folder_error_value, list) else []
        lexical_metrics_value = lexical.get("metrics")
        semantic_metrics_value = semantic.get("metrics")
        locator_metrics_value = locator_result.get("backend_calls")
        return {
            "ok": bool(lexical.get("ok")),
            "path": "local-mail-hybrid",
            "backend": "local-hybrid",
            "mode": mode,
            "read_only": True,
            "complete": complete,
            "coverage": dict(index_status.get("coverage") or {}),
            "freshness": dict(index_status.get("freshness") or {}),
            "index_generation": str(index_status.get("generation") or ""),
            "semantic_state": semantic_state,
            "fallback_used": False,
            "fallback_reason": [],
            "folder_errors": folder_errors,
            "results_may_be_truncated": bool(lexical.get("results_may_be_truncated")),
            "count": len(results),
            "results": results,
            "messages": results,
            "query": {"stored": False, "logged": False},
            "index": index_status,
            "ranking": {
                "version": MAIL_HYBRID_RANKING_VERSION,
                "rrf_k": RRF_K,
                "weights": {
                    "lexical": LEXICAL_WEIGHT,
                    "semantic": SEMANTIC_WEIGHT,
                    "structured": STRUCTURED_WEIGHT,
                    "thread": THREAD_WEIGHT,
                },
                "semantic_candidates_are_factual_evidence": False,
            },
            "metrics": {
                "latency_ms": round((time.perf_counter() - started) * 1000.0, 3),
                "lexical": (
                    dict(lexical_metrics_value)
                    if isinstance(lexical_metrics_value, dict)
                    else {}
                ),
                "semantic": (
                    dict(semantic_metrics_value)
                    if isinstance(semantic_metrics_value, dict)
                    else {}
                ),
                "live_locator_backend_calls": (
                    dict(locator_metrics_value)
                    if isinstance(locator_metrics_value, dict)
                    else {}
                ),
            },
            "read_contract": {
                "server_revalidation_required": True,
                "required_fields": ["folder", "mailbox_id", "expected_subject"],
                "index_authorizes_actions": False,
            },
        }


__all__ = [
    "MAIL_HYBRID_RANKING_VERSION",
    "MailHybridSearch",
    "SEARCH_MODES",
]
