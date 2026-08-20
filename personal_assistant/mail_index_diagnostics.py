from __future__ import annotations

import json
import sqlite3
from typing import Any

from .mail_embeddings import MAIL_EMBEDDING_CONTRACT_VERSION, EmbeddingModel
from .mail_search import MailLexicalSearch

REQUIRED_MAIL_INDEX_TABLES = frozenset(
    {
        "chunks",
        "documents",
        "mail_search_contents",
        "mail_search_embeddings",
        "mail_search_generations",
        "mail_search_locators",
        "mail_search_occurrences",
        "mail_search_tags",
        "mail_search_thread_members",
        "mail_search_threads",
    }
)


class MailIndexDiagnostics:
    """Read-only M11.7 status and integrity checks for the local mail index."""

    def __init__(self, connection: sqlite3.Connection, *, fts_enabled: bool) -> None:
        self.connection = connection
        self.fts_enabled = bool(fts_enabled)

    def _tables(self) -> set[str]:
        return {
            str(row[0])
            for row in self.connection.execute(
                "SELECT name FROM sqlite_master WHERE type IN ('table','view')"
            ).fetchall()
        }

    @staticmethod
    def _coverage_summary(index: dict[str, Any]) -> dict[str, Any]:
        value = index.get("coverage")
        raw: dict[str, Any] = value if isinstance(value, dict) else {}
        expected = {
            str(item) for item in raw.get("expected_partition_ids") or [] if str(item)
        }
        complete = {
            str(item) for item in raw.get("complete_partition_ids") or [] if str(item)
        }
        incomplete = {
            str(item) for item in raw.get("incomplete_partition_ids") or [] if str(item)
        }
        ratio = len(complete & expected) / len(expected) if expected else (
            1.0 if bool(raw.get("authoritative")) and bool(index.get("complete")) else 0.0
        )
        return {
            "authoritative": bool(raw.get("authoritative")),
            "expected_partitions": len(expected),
            "complete_partitions": len(complete),
            "incomplete_partitions": len(incomplete),
            "ratio": round(ratio, 6),
        }

    def status(
        self,
        *,
        max_age_seconds: int,
        semantic_model: EmbeddingModel | None = None,
    ) -> dict[str, Any]:
        try:
            tables = self._tables()
            missing_tables = sorted(REQUIRED_MAIL_INDEX_TABLES - tables)
            index = MailLexicalSearch(
                self.connection, fts_enabled=self.fts_enabled
            ).index_state(max_age_seconds=max_age_seconds)
            content_count = int(
                self.connection.execute(
                    "SELECT COUNT(*) FROM mail_search_contents"
                ).fetchone()[0]
            )
            occurrence_count = int(
                self.connection.execute(
                    "SELECT COUNT(*) FROM mail_search_occurrences "
                    "WHERE tombstoned_at IS NULL"
                ).fetchone()[0]
            )
            locator_count = int(
                self.connection.execute(
                    "SELECT COUNT(*) FROM mail_search_locators WHERE is_current=1"
                ).fetchone()[0]
            )
            located_content_count = int(
                self.connection.execute(
                    """
                    SELECT COUNT(DISTINCT o.content_id)
                    FROM mail_search_occurrences o
                    JOIN mail_search_locators l ON l.occurrence_id=o.occurrence_id
                    WHERE o.tombstoned_at IS NULL AND l.is_current=1
                    """
                ).fetchone()[0]
            )
            fts_available = self.fts_enabled and "mail_search_fts" in tables
            fts_rows = (
                int(self.connection.execute("SELECT COUNT(*) FROM mail_search_fts").fetchone()[0])
                if fts_available
                else 0
            )
            semantic: dict[str, Any]
            if semantic_model is None:
                semantic = {
                    "state": "disabled",
                    "configured": False,
                    "ready": False,
                    "model": "",
                    "model_digest": "",
                    "dimension": 0,
                    "embedding_rows": 0,
                    "candidate_chunks": 0,
                }
            else:
                candidate_chunks = int(
                    self.connection.execute(
                        """
                        SELECT COUNT(*) FROM chunks c JOIN documents d ON d.id=c.document_id
                        WHERE d.source_type='email' AND d.resource_id='mail-agent'
                        """
                    ).fetchone()[0]
                )
                embedding_rows = int(
                    self.connection.execute(
                        """
                        SELECT COUNT(*) FROM mail_search_embeddings
                        WHERE contract_version=? AND model_digest=? AND dimension=?
                        """,
                        (
                            MAIL_EMBEDDING_CONTRACT_VERSION,
                            semantic_model.digest,
                            semantic_model.dimension,
                        ),
                    ).fetchone()[0]
                )
                ready = candidate_chunks > 0 and embedding_rows >= candidate_chunks
                semantic = {
                    "state": "ready" if ready else "partial" if embedding_rows else "missing",
                    "configured": True,
                    "ready": ready,
                    "model": semantic_model.name,
                    "model_digest": semantic_model.digest,
                    "dimension": semantic_model.dimension,
                    "embedding_rows": embedding_rows,
                    "candidate_chunks": candidate_chunks,
                }
            coverage = self._coverage_summary(index)
            locator_complete = content_count == located_content_count
            search_eligible = bool(
                not missing_tables
                and fts_available
                and index.get("absence_proven")
                and locator_complete
            )
            reasons: list[str] = []
            if missing_tables:
                reasons.append("missing-schema")
            if not fts_available:
                reasons.append("fts-unavailable")
            if not index.get("complete"):
                reasons.append("partial-generation")
            if not index.get("authoritative"):
                reasons.append("non-authoritative-generation")
            if not index.get("fresh"):
                reasons.append("stale-generation")
            if not locator_complete:
                reasons.append("missing-current-locator")
            return {
                "ok": not missing_tables,
                "state": "ready" if search_eligible else str(index.get("state") or "unavailable"),
                "search_eligible": search_eligible,
                "reasons": reasons,
                "generation": str(index.get("source_generation") or ""),
                "generated_at": str(index.get("source_generated_at") or ""),
                "complete": bool(index.get("complete")),
                "freshness": {
                    "fresh": bool(index.get("fresh")),
                    "age_seconds": index.get("age_seconds"),
                    "max_age_seconds": int(max_age_seconds),
                },
                "coverage": coverage,
                "fts": {"available": fts_available, "rows": fts_rows},
                "locators": {
                    "complete": locator_complete,
                    "current": locator_count,
                    "located_contents": located_content_count,
                    "contents": content_count,
                },
                "occurrences": occurrence_count,
                "semantic": semantic,
                "read_only": True,
            }
        except (sqlite3.DatabaseError, json.JSONDecodeError, TypeError, ValueError) as exc:
            return {
                "ok": False,
                "state": "corrupt",
                "search_eligible": False,
                "reasons": ["index-corrupt"],
                "generation": "",
                "freshness": {"fresh": False, "age_seconds": None, "max_age_seconds": int(max_age_seconds)},
                "coverage": {
                    "authoritative": False,
                    "expected_partitions": 0,
                    "complete_partitions": 0,
                    "incomplete_partitions": 0,
                    "ratio": 0.0,
                },
                "semantic": {
                    "state": "unavailable",
                    "configured": semantic_model is not None,
                    "ready": False,
                },
                "error": {"category": "index-corrupt", "detail": str(exc)[:500]},
                "read_only": True,
            }

    def doctor(
        self,
        *,
        max_age_seconds: int,
        semantic_model: EmbeddingModel | None = None,
    ) -> dict[str, Any]:
        status = self.status(
            max_age_seconds=max_age_seconds,
            semantic_model=semantic_model,
        )
        checks: dict[str, Any] = {}
        try:
            quick = [str(row[0]) for row in self.connection.execute("PRAGMA quick_check").fetchall()]
            foreign = [tuple(row) for row in self.connection.execute("PRAGMA foreign_key_check").fetchall()]
            orphan_locators = int(
                self.connection.execute(
                    """
                    SELECT COUNT(*) FROM mail_search_locators l
                    LEFT JOIN mail_search_occurrences o ON o.occurrence_id=l.occurrence_id
                    WHERE o.occurrence_id IS NULL
                    """
                ).fetchone()[0]
            )
            invalid_vectors = int(
                self.connection.execute(
                    """
                    SELECT COUNT(*) FROM mail_search_embeddings
                    WHERE dimension<=0 OR dimension>8192 OR length(vector)<>dimension*4
                    """
                ).fetchone()[0]
            )
            checks = {
                "sqlite": {"ok": quick == ["ok"], "result": quick[:20]},
                "foreign_keys": {"ok": not foreign, "violations": len(foreign)},
                "fts": {
                    "ok": bool(status.get("fts", {}).get("available")),
                    "rows": status.get("fts", {}).get("rows", 0),
                },
                "locators": {"ok": orphan_locators == 0, "orphan_rows": orphan_locators},
                "embeddings": {
                    "ok": invalid_vectors == 0,
                    "invalid_rows": invalid_vectors,
                    **dict(status.get("semantic") or {}),
                },
                "generation": {
                    "ok": bool(status.get("complete")) and bool(status.get("generation")),
                    "generation": status.get("generation", ""),
                },
            }
        except sqlite3.DatabaseError as exc:
            checks = {"sqlite": {"ok": False, "error": str(exc)[:500]}}
        ok = bool(status.get("ok")) and all(
            bool(value.get("ok")) for value in checks.values()
        )
        return {
            "ok": ok,
            "state": "healthy" if ok else "degraded",
            "status": status,
            "checks": checks,
            "read_only": True,
        }


__all__ = ["MailIndexDiagnostics", "REQUIRED_MAIL_INDEX_TABLES"]
