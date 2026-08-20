from __future__ import annotations

import json
import os
import sqlite3
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .contracts.time import now_utc_iso
from .mail_embeddings import EmbeddingModel, EmbeddingProvider, MailEmbeddingIndex
from .mail_index_diagnostics import MailIndexDiagnostics
from .mail_search import MailLexicalSearch, MailSearchFilters, build_mail_tags
from .mail_threads import (
    MAIL_RETRIEVAL_TEXT_VERSION,
    build_mail_threads,
    normalize_retrieval_text,
)
from .models import ActionPlan, SearchResult

CORE_SCHEMA_VERSION = 1
KNOWLEDGE_SCHEMA_VERSION = 5
# Compatibility export for callers that historically treated the combined
# development database as the knowledge schema.
SCHEMA_VERSION = KNOWLEDGE_SCHEMA_VERSION


def read_only_sqlite_uri(path: Path) -> str:
    """Return a side-effect-free SQLite URI without hiding an active WAL.

    SQLite databases keep WAL mode in their header. Even when no WAL exists, a
    normal ``mode=ro`` connection may therefore try to create ``-shm`` beside
    the database and fail on an intentionally read-only container mount. A
    quiescent database can safely be opened as immutable. If a WAL exists we
    retain SQLite's normal read-only path so committed WAL content is never
    ignored; missing or unreadable sidecars then fail closed.
    """

    resolved = path.expanduser().resolve()
    wal_path = resolved.with_name(resolved.name + "-wal")
    suffix = "?mode=ro" if wal_path.exists() else "?mode=ro&immutable=1"
    return resolved.as_uri() + suffix


class AssistantStorage:
    def __init__(
        self,
        path: Path,
        *,
        read_only: bool = False,
        enable_knowledge: bool = True,
        knowledge_read_only: bool | None = None,
    ) -> None:
        self.path = path.expanduser().resolve()
        self.enable_knowledge = enable_knowledge
        self.core_read_only = read_only
        self.knowledge_read_only = (
            read_only if knowledge_read_only is None else knowledge_read_only
        )
        knowledge_root = os.environ.get("OPENCLAW_KNOWLEDGE_DATA_DIR")
        self.knowledge_path = (
            Path(knowledge_root).expanduser().resolve() / "knowledge.sqlite3"
            if enable_knowledge and knowledge_root else self.path
        )
        self.connection = self._connect(self.path, read_only=read_only)
        if (
            self.knowledge_path == self.path
            and self.enable_knowledge
            and self.knowledge_read_only != self.core_read_only
        ):
            self.connection.close()
            raise ValueError(
                "Getrennte Lese-/Schreibmodi erfordern eine separate Wissensdatenbank"
            )
        self.knowledge_connection = (
            self.connection
            if self.knowledge_path == self.path
            else self._connect(
                self.knowledge_path, read_only=self.knowledge_read_only
            )
        )
        if self.enable_knowledge and self.knowledge_read_only:
            self.fts_enabled = bool(
                self.knowledge_connection.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='knowledge_fts'"
                ).fetchone()
            )
            self.mail_search_fts_enabled = bool(
                self.knowledge_connection.execute(
                    "SELECT 1 FROM sqlite_master "
                    "WHERE type='table' AND name='mail_search_fts'"
                ).fetchone()
            )
        else:
            self.fts_enabled = False
            self.mail_search_fts_enabled = False
        if not self.core_read_only or (
            self.enable_knowledge and not self.knowledge_read_only
        ):
            self._migrate()

    @staticmethod
    def _connect(path: Path, *, read_only: bool) -> sqlite3.Connection:
        if read_only:
            connection = sqlite3.connect(read_only_sqlite_uri(path), uri=True)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            connection = sqlite3.connect(path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=5000")
        if read_only:
            connection.execute("PRAGMA query_only=ON")
        else:
            connection.execute("PRAGMA journal_mode=WAL")
        return connection

    def close(self) -> None:
        if self.knowledge_connection is not self.connection:
            self.knowledge_connection.close()
        self.connection.close()

    def _migrate(self) -> None:
        combined_knowledge_version = (
            int(self.connection.execute("PRAGMA user_version").fetchone()[0])
            if self.enable_knowledge and self.knowledge_connection is self.connection
            else None
        )
        if not self.core_read_only:
            core_target = (
                KNOWLEDGE_SCHEMA_VERSION
                if self.enable_knowledge
                and self.knowledge_connection is self.connection
                else CORE_SCHEMA_VERSION
            )
            current = int(self.connection.execute("PRAGMA user_version").fetchone()[0])
            if current > core_target:
                raise RuntimeError(
                    f"Assistant-Datenbankschema {current} ist neuer als {core_target}"
                )
            self.connection.executescript(
                """
            CREATE TABLE IF NOT EXISTS resources (
                resource_id TEXT PRIMARY KEY,
                kind TEXT NOT NULL,
                connector TEXT NOT NULL,
                enabled INTEGER NOT NULL,
                remote_id TEXT,
                permissions_json TEXT NOT NULL,
                metadata_json TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS action_plans (
                id TEXT PRIMARY KEY,
                idempotency_key TEXT NOT NULL UNIQUE,
                action_type TEXT NOT NULL,
                resource_id TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                status TEXT NOT NULL,
                requires_approval INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                error TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_action_status ON action_plans(status);
            CREATE TABLE IF NOT EXISTS audit_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                actor TEXT NOT NULL,
                event_type TEXT NOT NULL,
                resource_id TEXT,
                detail_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS settings_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                setting_key TEXT NOT NULL,
                old_value TEXT,
                new_value TEXT,
                actor TEXT NOT NULL,
                approved INTEGER NOT NULL,
                created_at TEXT NOT NULL
            );
                """
            )
            if combined_knowledge_version is None:
                self.connection.execute(f"PRAGMA user_version={core_target}")
            self.connection.commit()
        if not self.enable_knowledge or self.knowledge_read_only:
            return

        knowledge_version = (
            combined_knowledge_version
            if combined_knowledge_version is not None
            else int(
                self.knowledge_connection.execute("PRAGMA user_version").fetchone()[0]
            )
        )
        if knowledge_version > KNOWLEDGE_SCHEMA_VERSION:
            raise RuntimeError(
                "Wissensdatenbankschema "
                f"{knowledge_version} ist neuer als {KNOWLEDGE_SCHEMA_VERSION}"
            )
        self.knowledge_connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS sync_state (
                resource_id TEXT NOT NULL,
                scope TEXT NOT NULL,
                cursor TEXT,
                etag TEXT,
                synced_at TEXT,
                status TEXT NOT NULL,
                detail TEXT,
                PRIMARY KEY(resource_id, scope)
            );
            CREATE TABLE IF NOT EXISTS documents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_type TEXT NOT NULL,
                resource_id TEXT NOT NULL,
                source_id TEXT NOT NULL,
                uri TEXT NOT NULL,
                title TEXT NOT NULL,
                mime_type TEXT,
                modified_at TEXT,
                etag TEXT,
                sha256 TEXT,
                metadata_json TEXT NOT NULL,
                indexed_at TEXT NOT NULL,
                UNIQUE(resource_id, source_id)
            );
            CREATE INDEX IF NOT EXISTS idx_documents_type ON documents(source_type);
            CREATE INDEX IF NOT EXISTS idx_documents_resource ON documents(resource_id);
            CREATE INDEX IF NOT EXISTS idx_documents_modified ON documents(modified_at);
            CREATE TABLE IF NOT EXISTS chunks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
                chunk_index INTEGER NOT NULL,
                text TEXT NOT NULL,
                UNIQUE(document_id, chunk_index)
            );
            CREATE TABLE IF NOT EXISTS mail_search_generations (
                generation TEXT PRIMARY KEY,
                schema_version INTEGER NOT NULL,
                source_generated_at TEXT NOT NULL,
                imported_at TEXT NOT NULL,
                complete INTEGER NOT NULL,
                source_status TEXT NOT NULL,
                coverage_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS mail_search_contents (
                content_id TEXT PRIMARY KEY,
                resource_id TEXT NOT NULL,
                raw_sha256 TEXT NOT NULL,
                canonical_message_id TEXT,
                identity_evidence_json TEXT NOT NULL,
                parser_version TEXT NOT NULL,
                normalization_version TEXT NOT NULL,
                tag_version TEXT NOT NULL,
                retrieval_text_version TEXT NOT NULL DEFAULT '',
                embedding_version TEXT,
                content_digest TEXT NOT NULL,
                indexed_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_mail_search_contents_resource
                ON mail_search_contents(resource_id);
            CREATE INDEX IF NOT EXISTS idx_mail_search_contents_message_id
                ON mail_search_contents(resource_id, canonical_message_id);
            CREATE TABLE IF NOT EXISTS mail_search_occurrences (
                occurrence_id TEXT PRIMARY KEY,
                content_id TEXT NOT NULL REFERENCES mail_search_contents(content_id),
                resource_id TEXT NOT NULL,
                index_generation TEXT NOT NULL,
                source_status TEXT NOT NULL,
                first_seen_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL,
                tombstoned_at TEXT,
                conflict_code TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_mail_search_occurrences_content
                ON mail_search_occurrences(content_id);
            CREATE INDEX IF NOT EXISTS idx_mail_search_occurrences_generation
                ON mail_search_occurrences(index_generation);
            CREATE TABLE IF NOT EXISTS mail_search_locators (
                occurrence_id TEXT NOT NULL
                    REFERENCES mail_search_occurrences(occurrence_id),
                locator_id TEXT NOT NULL,
                resource_id TEXT NOT NULL,
                folder_id TEXT NOT NULL,
                folder_name TEXT NOT NULL,
                mailbox_id TEXT,
                uidvalidity TEXT,
                uid TEXT,
                observed_at TEXT NOT NULL,
                is_current INTEGER NOT NULL,
                quarantine INTEGER NOT NULL,
                PRIMARY KEY(occurrence_id, locator_id)
            );
            CREATE INDEX IF NOT EXISTS idx_mail_search_locators_folder
                ON mail_search_locators(resource_id, folder_id, is_current);
            CREATE TABLE IF NOT EXISTS mail_search_tags (
                content_id TEXT NOT NULL REFERENCES mail_search_contents(content_id),
                namespace TEXT NOT NULL,
                value TEXT NOT NULL,
                source TEXT NOT NULL,
                source_version TEXT NOT NULL,
                confidence REAL,
                evidence_json TEXT NOT NULL,
                index_generation TEXT NOT NULL,
                active INTEGER NOT NULL DEFAULT 1,
                uncertainty TEXT NOT NULL DEFAULT '',
                PRIMARY KEY(content_id, namespace, value, source, source_version)
            );
            CREATE INDEX IF NOT EXISTS idx_mail_search_tags_lookup
                ON mail_search_tags(namespace, value, content_id);
            CREATE TABLE IF NOT EXISTS mail_search_thread_edges (
                content_id TEXT NOT NULL REFERENCES mail_search_contents(content_id),
                edge_type TEXT NOT NULL,
                relation_message_id TEXT NOT NULL,
                related_content_id TEXT,
                evidence_header TEXT NOT NULL,
                selected INTEGER NOT NULL DEFAULT 0,
                certainty TEXT NOT NULL DEFAULT 'certain',
                reason TEXT NOT NULL DEFAULT '',
                index_generation TEXT NOT NULL,
                PRIMARY KEY(content_id, edge_type, relation_message_id)
            );
            CREATE TABLE IF NOT EXISTS mail_search_threads (
                thread_id TEXT PRIMARY KEY,
                root_content_id TEXT NOT NULL REFERENCES mail_search_contents(content_id),
                thread_version TEXT NOT NULL,
                member_count INTEGER NOT NULL,
                first_at TEXT NOT NULL,
                last_at TEXT NOT NULL,
                uncertain INTEGER NOT NULL,
                index_generation TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS mail_search_thread_members (
                content_id TEXT PRIMARY KEY REFERENCES mail_search_contents(content_id),
                thread_id TEXT NOT NULL REFERENCES mail_search_threads(thread_id),
                parent_content_id TEXT,
                evidence_type TEXT NOT NULL,
                certainty TEXT NOT NULL,
                position INTEGER NOT NULL,
                index_generation TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_mail_search_thread_members_thread
                ON mail_search_thread_members(thread_id, position);
            CREATE TABLE IF NOT EXISTS mail_search_embeddings (
                embedding_key TEXT PRIMARY KEY,
                content_id TEXT NOT NULL REFERENCES mail_search_contents(content_id)
                    ON DELETE CASCADE,
                document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
                chunk_id INTEGER NOT NULL REFERENCES chunks(id) ON DELETE CASCADE,
                chunk_index INTEGER NOT NULL,
                raw_sha256 TEXT NOT NULL,
                retrieval_sha256 TEXT NOT NULL,
                retrieval_text_version TEXT NOT NULL,
                contract_version TEXT NOT NULL,
                model_name TEXT NOT NULL,
                model_digest TEXT NOT NULL,
                dimension INTEGER NOT NULL CHECK(dimension > 0 AND dimension <= 8192),
                vector BLOB NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_mail_search_embeddings_model
                ON mail_search_embeddings(model_digest,dimension,content_id,chunk_index);
            CREATE INDEX IF NOT EXISTS idx_mail_search_embeddings_content
                ON mail_search_embeddings(content_id,model_digest);
            """
        )
        document_columns = {
            str(row[1])
            for row in self.knowledge_connection.execute(
                "PRAGMA table_info(documents)"
            ).fetchall()
        }
        additive_columns = {
            "content_id": "TEXT",
            "index_generation": "TEXT",
            "source_status": "TEXT NOT NULL DEFAULT 'legacy'",
            "embedding_version": "TEXT",
        }
        for name, declaration in additive_columns.items():
            if name not in document_columns:
                self.knowledge_connection.execute(
                    f"ALTER TABLE documents ADD COLUMN {name} {declaration}"
                )
        tag_columns = {
            str(row[1])
            for row in self.knowledge_connection.execute(
                "PRAGMA table_info(mail_search_tags)"
            ).fetchall()
        }
        for name, declaration in {
            "active": "INTEGER NOT NULL DEFAULT 1",
            "uncertainty": "TEXT NOT NULL DEFAULT ''",
        }.items():
            if name not in tag_columns:
                self.knowledge_connection.execute(
                    f"ALTER TABLE mail_search_tags ADD COLUMN {name} {declaration}"
                )
        content_columns = {
            str(row[1])
            for row in self.knowledge_connection.execute(
                "PRAGMA table_info(mail_search_contents)"
            ).fetchall()
        }
        if "retrieval_text_version" not in content_columns:
            self.knowledge_connection.execute(
                "ALTER TABLE mail_search_contents "
                "ADD COLUMN retrieval_text_version TEXT NOT NULL DEFAULT ''"
            )
        edge_columns = {
            str(row[1])
            for row in self.knowledge_connection.execute(
                "PRAGMA table_info(mail_search_thread_edges)"
            ).fetchall()
        }
        for name, declaration in {
            "selected": "INTEGER NOT NULL DEFAULT 0",
            "certainty": "TEXT NOT NULL DEFAULT 'certain'",
            "reason": "TEXT NOT NULL DEFAULT ''",
        }.items():
            if name not in edge_columns:
                self.knowledge_connection.execute(
                    f"ALTER TABLE mail_search_thread_edges ADD COLUMN {name} {declaration}"
                )
        try:
            self.knowledge_connection.execute(
                "CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_fts USING fts5("
                "title, text, source_type, resource_id, "
                "tokenize='unicode61 remove_diacritics 2')"
            )
            self.fts_enabled = True
        except sqlite3.OperationalError:
            self.fts_enabled = False
        mail_fts_existed = bool(
            self.knowledge_connection.execute(
                "SELECT 1 FROM sqlite_master "
                "WHERE type='table' AND name='mail_search_fts'"
            ).fetchone()
        )
        try:
            self.knowledge_connection.execute(
                "CREATE VIRTUAL TABLE IF NOT EXISTS mail_search_fts USING fts5("
                "content_id UNINDEXED,document_id UNINDEXED,chunk_id UNINDEXED,"
                "subject,sender,body,tokenize='unicode61 remove_diacritics 2')"
            )
            self.mail_search_fts_enabled = True
            if not mail_fts_existed:
                rows = self.knowledge_connection.execute(
                    """
                    SELECT c.id,c.document_id,c.text,d.source_id,d.content_id,d.title,
                           d.metadata_json
                    FROM chunks c JOIN documents d ON d.id=c.document_id
                    WHERE d.source_type='email' AND d.resource_id='mail-agent'
                    ORDER BY c.id
                    """
                ).fetchall()
                for row in rows:
                    try:
                        metadata = json.loads(str(row["metadata_json"] or "{}"))
                    except json.JSONDecodeError:
                        metadata = {}
                    sender = " ".join(
                        value
                        for value in (
                            str(metadata.get("sender_name") or ""),
                            str(metadata.get("sender_addr") or ""),
                        )
                        if value
                    )
                    self.knowledge_connection.execute(
                        "INSERT INTO mail_search_fts("
                        "rowid,content_id,document_id,chunk_id,subject,sender,body"
                        ") VALUES(?,?,?,?,?,?,?)",
                        (
                            int(row["id"]),
                            str(row["content_id"] or row["source_id"]),
                            int(row["document_id"]),
                            int(row["id"]),
                            str(row["title"] or ""),
                            sender,
                            normalize_retrieval_text(str(row["text"] or "")).text,
                        ),
                    )
            elif knowledge_version < 4:
                rows = self.knowledge_connection.execute(
                    "SELECT id,text FROM chunks ORDER BY id"
                ).fetchall()
                for row in rows:
                    self.knowledge_connection.execute(
                        "UPDATE mail_search_fts SET body=? WHERE rowid=?",
                        (
                            normalize_retrieval_text(str(row["text"] or "")).text,
                            int(row["id"]),
                        ),
                    )
            self.knowledge_connection.execute(
                "UPDATE mail_search_contents SET retrieval_text_version=?",
                (MAIL_RETRIEVAL_TEXT_VERSION,),
            )
        except sqlite3.OperationalError:
            self.mail_search_fts_enabled = False
        self.knowledge_connection.execute(
            f"PRAGMA user_version={KNOWLEDGE_SCHEMA_VERSION}"
        )
        self.knowledge_connection.commit()

    def integrity(self) -> str:
        core = str(self.connection.execute("PRAGMA integrity_check").fetchone()[0])
        if not self.enable_knowledge:
            return core
        knowledge = str(
            self.knowledge_connection.execute("PRAGMA integrity_check").fetchone()[0]
        )
        return "ok" if core == knowledge == "ok" else f"core={core}; knowledge={knowledge}"

    def audit(
        self,
        event_type: str,
        detail: dict[str, Any],
        *,
        resource_id: str = "",
        actor: str = "assistant",
    ) -> None:
        self.connection.execute(
            "INSERT INTO audit_log(actor,event_type,resource_id,detail_json,created_at) VALUES(?,?,?,?,?)",
            (actor, event_type, resource_id, json.dumps(detail, ensure_ascii=False), now_utc_iso()),
        )
        self.connection.commit()

    def set_sync_state(
        self,
        resource_id: str,
        scope: str,
        *,
        cursor: str = "",
        etag: str = "",
        status: str,
        detail: str = "",
    ) -> None:
        self.knowledge_connection.execute(
            """
            INSERT INTO sync_state(resource_id,scope,cursor,etag,synced_at,status,detail)
            VALUES(?,?,?,?,?,?,?)
            ON CONFLICT(resource_id,scope) DO UPDATE SET
              cursor=excluded.cursor,etag=excluded.etag,synced_at=excluded.synced_at,
              status=excluded.status,detail=excluded.detail
            """,
            (resource_id, scope, cursor, etag, now_utc_iso(), status, detail),
        )
        self.knowledge_connection.commit()

    def get_sync_state(self, resource_id: str, scope: str) -> sqlite3.Row | None:
        return self.knowledge_connection.execute(
            "SELECT * FROM sync_state WHERE resource_id=? AND scope=?",
            (resource_id, scope),
        ).fetchone()

    def get_document(self, resource_id: str, source_id: str) -> sqlite3.Row | None:
        return self.knowledge_connection.execute(
            "SELECT * FROM documents WHERE resource_id=? AND source_id=?", (resource_id, source_id)
        ).fetchone()

    def index_document(
        self,
        *,
        source_type: str,
        resource_id: str,
        source_id: str,
        uri: str,
        title: str,
        mime_type: str = "",
        modified_at: str = "",
        etag: str = "",
        sha256: str = "",
        metadata: dict[str, Any] | None = None,
        content_id: str = "",
        index_generation: str = "",
        source_status: str = "legacy",
        embedding_version: str = "",
        chunks: list[str],
    ) -> int:
        timestamp = now_utc_iso()
        metadata_json = json.dumps(metadata or {}, ensure_ascii=False)
        self.knowledge_connection.execute(
            """
            INSERT INTO documents(
                source_type,resource_id,source_id,uri,title,mime_type,modified_at,
                etag,sha256,metadata_json,indexed_at,content_id,index_generation,
                source_status,embedding_version
            )
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(resource_id,source_id) DO UPDATE SET
              source_type=excluded.source_type,uri=excluded.uri,title=excluded.title,
              mime_type=excluded.mime_type,modified_at=excluded.modified_at,etag=excluded.etag,
              sha256=excluded.sha256,metadata_json=excluded.metadata_json,
              indexed_at=excluded.indexed_at,content_id=excluded.content_id,
              index_generation=excluded.index_generation,
              source_status=excluded.source_status,
              embedding_version=excluded.embedding_version
            """,
            (
                source_type,
                resource_id,
                source_id,
                uri,
                title,
                mime_type,
                modified_at,
                etag,
                sha256,
                metadata_json,
                timestamp,
                content_id or None,
                index_generation or None,
                source_status,
                embedding_version or None,
            ),
        )
        row = self.get_document(resource_id, source_id)
        assert row is not None
        document_id = int(row["id"])
        old_rows = self.knowledge_connection.execute(
            "SELECT id FROM chunks WHERE document_id=?", (document_id,)
        ).fetchall()
        if self.fts_enabled:
            for old in old_rows:
                self.knowledge_connection.execute(
                    "DELETE FROM knowledge_fts WHERE rowid=?", (int(old["id"]),)
                )
        if self.mail_search_fts_enabled and source_type == "email":
            for old in old_rows:
                self.knowledge_connection.execute(
                    "DELETE FROM mail_search_fts WHERE rowid=?", (int(old["id"]),)
                )
        self.knowledge_connection.execute(
            "DELETE FROM chunks WHERE document_id=?", (document_id,)
        )
        for index, text in enumerate(chunks):
            cursor = self.knowledge_connection.execute(
                "INSERT INTO chunks(document_id,chunk_index,text) VALUES(?,?,?)",
                (document_id, index, text),
            )
            chunk_rowid = cursor.lastrowid
            if chunk_rowid is None:
                raise RuntimeError("Chunk-Insert lieferte keine ID")
            chunk_id = int(chunk_rowid)
            if self.fts_enabled:
                self.knowledge_connection.execute(
                    "INSERT INTO knowledge_fts(rowid,title,text,source_type,resource_id) VALUES(?,?,?,?,?)",
                    (chunk_id, title, text, source_type, resource_id),
                )
            if self.mail_search_fts_enabled and source_type == "email":
                metadata_values = metadata or {}
                sender = " ".join(
                    value
                    for value in (
                        str(metadata_values.get("sender_name") or ""),
                        str(metadata_values.get("sender_addr") or ""),
                    )
                    if value
                )
                self.knowledge_connection.execute(
                    "INSERT INTO mail_search_fts("
                    "rowid,content_id,document_id,chunk_id,subject,sender,body"
                    ") VALUES(?,?,?,?,?,?,?)",
                    (
                        chunk_id,
                        content_id or source_id,
                        document_id,
                        chunk_id,
                        title,
                        sender,
                        text,
                    ),
                )
        self.knowledge_connection.commit()
        return document_id

    def apply_mail_projection(
        self,
        *,
        generation: str,
        generated_at: str,
        coverage: dict[str, Any],
        records: list[dict[str, Any]],
        before_commit: Callable[[], None] | None = None,
    ) -> dict[str, int]:
        """Atomically apply one complete v2 mail generation and its cursor."""

        connection = self.knowledge_connection
        timestamp = now_utc_iso()
        thread_build = build_mail_threads(records, generation=generation)
        metrics = {
            "indexed": 0,
            "unchanged": 0,
            "metadata_updated": 0,
            "removed": 0,
            "fts_rows_changed": 0,
            "embeddings_reused": 0,
            "embeddings_new": 0,
            "tag_rows_changed": 0,
            "thread_rows_changed": 0,
            "thread_count": len(thread_build.threads),
            "thread_uncertain": sum(
                int(bool(item["uncertain"])) for item in thread_build.threads
            ),
            "thread_cycle_rejections": int(
                thread_build.diagnostics["cycle_rejections"]
            ),
        }

        def remove_chunks(document_id: int) -> int:
            rows = connection.execute(
                "SELECT id FROM chunks WHERE document_id=?", (document_id,)
            ).fetchall()
            if self.fts_enabled:
                for row in rows:
                    connection.execute(
                        "DELETE FROM knowledge_fts WHERE rowid=?", (int(row["id"]),)
                    )
            if self.mail_search_fts_enabled:
                for row in rows:
                    connection.execute(
                        "DELETE FROM mail_search_fts WHERE rowid=?", (int(row["id"]),)
                    )
            connection.execute("DELETE FROM chunks WHERE document_id=?", (document_id,))
            return len(rows)

        current_content_ids = {str(item["content_id"]) for item in records}
        current_occurrence_ids = {
            str(occurrence_id)
            for item in records
            for occurrence_id in item.get("occurrence_ids", [])
        }
        with connection:
            connection.execute(
                "UPDATE mail_search_locators SET is_current=0 WHERE resource_id=?",
                ("mail-agent",),
            )
            for item in records:
                content_id = str(item["content_id"])
                metadata = dict(item.get("metadata") or {})
                metadata["retrieval_text_version"] = MAIL_RETRIEVAL_TEXT_VERSION
                connection.execute(
                    """
                    INSERT INTO mail_search_contents(
                        content_id,resource_id,raw_sha256,canonical_message_id,
                        identity_evidence_json,parser_version,normalization_version,
                        tag_version,retrieval_text_version,embedding_version,
                        content_digest,indexed_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(content_id) DO UPDATE SET
                        retrieval_text_version=excluded.retrieval_text_version,
                        embedding_version=excluded.embedding_version,
                        indexed_at=excluded.indexed_at
                    """,
                    (
                        content_id,
                        "mail-agent",
                        str(item.get("sha256") or ""),
                        str(item.get("message_id") or ""),
                        json.dumps(
                            {"method": "resource+raw-sha256", "version": "mail-identity-v1"},
                            ensure_ascii=False,
                        ),
                        str(metadata.get("parser_version") or ""),
                        str(metadata.get("normalization_version") or ""),
                        str(metadata.get("tag_version") or ""),
                        MAIL_RETRIEVAL_TEXT_VERSION,
                        str(metadata.get("embedding_version") or "") or None,
                        str(item.get("sha256") or ""),
                        timestamp,
                    ),
                )
                locators = [
                    *list(metadata.get("locators") or []),
                    *list(metadata.get("historical_locators") or []),
                ]
                occurrence_ids = [str(value) for value in metadata.get("occurrence_ids", [])]
                for occurrence_id in occurrence_ids:
                    connection.execute(
                        """
                        INSERT INTO mail_search_occurrences(
                            occurrence_id,content_id,resource_id,index_generation,
                            source_status,first_seen_at,last_seen_at,tombstoned_at,conflict_code
                        ) VALUES(?,?,?,?,?,?,?,?,?)
                        ON CONFLICT(occurrence_id) DO UPDATE SET
                            content_id=excluded.content_id,
                            index_generation=excluded.index_generation,
                            source_status=excluded.source_status,
                            last_seen_at=excluded.last_seen_at,
                            tombstoned_at=NULL,
                            conflict_code=''
                        """,
                        (
                            occurrence_id,
                            content_id,
                            "mail-agent",
                            generation,
                            str(metadata.get("source_status") or "active"),
                            timestamp,
                            timestamp,
                            None,
                            "",
                        ),
                    )
                for locator in locators:
                    occurrence_id = str(locator.get("occurrence_id") or "")
                    if occurrence_id not in occurrence_ids:
                        raise ValueError("Locator besitzt keine gueltige Occurrence-Zuordnung")
                    connection.execute(
                        """
                        INSERT INTO mail_search_locators(
                            occurrence_id,locator_id,resource_id,folder_id,folder_name,
                            mailbox_id,uidvalidity,uid,observed_at,is_current,quarantine
                        ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
                        ON CONFLICT(occurrence_id,locator_id) DO UPDATE SET
                            folder_name=excluded.folder_name,
                            mailbox_id=excluded.mailbox_id,
                            uidvalidity=excluded.uidvalidity,
                            uid=excluded.uid,
                            observed_at=excluded.observed_at,
                            is_current=excluded.is_current,
                            quarantine=excluded.quarantine
                        """,
                        (
                            occurrence_id,
                            str(locator.get("locator_id") or ""),
                            "mail-agent",
                            str(locator.get("folder_id") or ""),
                            str(locator.get("folder_name") or ""),
                            str(locator.get("mailbox_id") or ""),
                            str(locator.get("uidvalidity") or ""),
                            str(locator.get("uid") or ""),
                            str(locator.get("observed_at") or generated_at),
                            int(bool(locator.get("is_current", True))),
                            int(bool(locator.get("quarantine"))),
                        ),
                    )

                connection.execute(
                    "DELETE FROM mail_search_tags WHERE content_id=?", (content_id,)
                )
                for tag in build_mail_tags(metadata):
                    connection.execute(
                        """
                        INSERT INTO mail_search_tags(
                            content_id,namespace,value,source,source_version,
                            confidence,evidence_json,index_generation,active,uncertainty
                        ) VALUES(?,?,?,?,?,?,?,?,?,?)
                        """,
                        (
                            content_id,
                            tag.namespace,
                            tag.value,
                            tag.source,
                            tag.source_version,
                            tag.confidence,
                            json.dumps(tag.evidence, ensure_ascii=False, sort_keys=True),
                            generation,
                            int(tag.active),
                            tag.uncertainty,
                        ),
                    )
                    metrics["tag_rows_changed"] += 1

                existing = self.get_document("mail-agent", content_id)
                metadata_json = json.dumps(metadata, ensure_ascii=False)
                if (
                    existing is not None
                    and str(existing["content_id"] or "") == content_id
                    and str(existing["sha256"] or "") == str(item.get("sha256") or "")
                ):
                    connection.execute(
                        """
                        UPDATE documents SET title=?,modified_at=?,metadata_json=?,
                            indexed_at=?,index_generation=?,source_status=?,embedding_version=?
                        WHERE id=?
                        """,
                        (
                            str(item.get("title") or "(ohne Betreff)"),
                            str(item.get("modified_at") or generated_at),
                            metadata_json,
                            timestamp,
                            generation,
                            str(metadata.get("source_status") or "active"),
                            str(metadata.get("embedding_version") or "") or None,
                            int(existing["id"]),
                        ),
                    )
                    if self.mail_search_fts_enabled:
                        sender = " ".join(
                            value
                            for value in (
                                str(metadata.get("sender_name") or ""),
                                str(metadata.get("sender_addr") or ""),
                            )
                            if value
                        )
                        changed = connection.execute(
                            """
                            UPDATE mail_search_fts SET subject=?,sender=?
                            WHERE document_id=? AND (subject<>? OR sender<>?)
                            """,
                            (
                                str(item.get("title") or "(ohne Betreff)"),
                                sender,
                                int(existing["id"]),
                                str(item.get("title") or "(ohne Betreff)"),
                                sender,
                            ),
                        ).rowcount
                        metrics["fts_rows_changed"] += max(0, int(changed))
                        chunk_rows = connection.execute(
                            "SELECT id,text FROM chunks WHERE document_id=? ORDER BY id",
                            (int(existing["id"]),),
                        ).fetchall()
                        for chunk in chunk_rows:
                            normalized_body = normalize_retrieval_text(
                                str(chunk["text"] or "")
                            ).text
                            current_fts = connection.execute(
                                "SELECT body FROM mail_search_fts WHERE rowid=?",
                                (int(chunk["id"]),),
                            ).fetchone()
                            if (
                                current_fts is not None
                                and str(current_fts["body"] or "") != normalized_body
                            ):
                                connection.execute(
                                    "UPDATE mail_search_fts SET body=? WHERE rowid=?",
                                    (normalized_body, int(chunk["id"])),
                                )
                                metrics["fts_rows_changed"] += 1
                    metrics["unchanged"] += 1
                    metrics["metadata_updated"] += 1
                    if existing["embedding_version"]:
                        metrics["embeddings_reused"] += 1
                    continue

                if existing is None:
                    cursor = connection.execute(
                        """
                        INSERT INTO documents(
                            source_type,resource_id,source_id,uri,title,mime_type,
                            modified_at,etag,sha256,metadata_json,indexed_at,content_id,
                            index_generation,source_status,embedding_version
                        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                        """,
                        (
                            "email", "mail-agent", content_id,
                            f"mail-agent://{content_id}",
                            str(item.get("title") or "(ohne Betreff)"),
                            "message/rfc822", str(item.get("modified_at") or generated_at),
                            "", str(item.get("sha256") or ""), metadata_json, timestamp,
                            content_id, generation,
                            str(metadata.get("source_status") or "active"),
                            str(metadata.get("embedding_version") or "") or None,
                        ),
                    )
                    document_rowid = cursor.lastrowid
                    if document_rowid is None:
                        raise RuntimeError("Dokument-Insert lieferte keine ID")
                    document_id = int(document_rowid)
                else:
                    document_id = int(existing["id"])
                    metrics["fts_rows_changed"] += remove_chunks(document_id)
                    connection.execute(
                        """
                        UPDATE documents SET uri=?,title=?,mime_type=?,modified_at=?,
                            sha256=?,metadata_json=?,indexed_at=?,content_id=?,
                            index_generation=?,source_status=?,embedding_version=?
                        WHERE id=?
                        """,
                        (
                            f"mail-agent://{content_id}",
                            str(item.get("title") or "(ohne Betreff)"),
                            "message/rfc822", str(item.get("modified_at") or generated_at),
                            str(item.get("sha256") or ""), metadata_json, timestamp,
                            content_id, generation,
                            str(metadata.get("source_status") or "active"),
                            str(metadata.get("embedding_version") or "") or None,
                            document_id,
                        ),
                    )
                for index, text in enumerate(item.get("chunks") or []):
                    cursor = connection.execute(
                        "INSERT INTO chunks(document_id,chunk_index,text) VALUES(?,?,?)",
                        (document_id, index, str(text)),
                    )
                    if self.fts_enabled:
                        chunk_rowid = cursor.lastrowid
                        if chunk_rowid is None:
                            raise RuntimeError("Chunk-Insert lieferte keine ID")
                        connection.execute(
                            "INSERT INTO knowledge_fts("
                            "rowid,title,text,source_type,resource_id"
                            ") VALUES(?,?,?,?,?)",
                            (
                                int(chunk_rowid),
                                str(item.get("title") or "(ohne Betreff)"),
                                str(text),
                                "email",
                                "mail-agent",
                            ),
                        )
                    if self.mail_search_fts_enabled:
                        chunk_rowid = cursor.lastrowid
                        if chunk_rowid is None:
                            raise RuntimeError("Chunk-Insert lieferte keine ID")
                        sender = " ".join(
                            value
                            for value in (
                                str(metadata.get("sender_name") or ""),
                                str(metadata.get("sender_addr") or ""),
                            )
                            if value
                        )
                        connection.execute(
                            "INSERT INTO mail_search_fts("
                            "rowid,content_id,document_id,chunk_id,subject,sender,body"
                            ") VALUES(?,?,?,?,?,?,?)",
                            (
                                int(chunk_rowid),
                                content_id,
                                document_id,
                                int(chunk_rowid),
                                str(item.get("title") or "(ohne Betreff)"),
                                sender,
                                normalize_retrieval_text(str(text)).text,
                            ),
                        )
                    metrics["fts_rows_changed"] += 1
                metrics["indexed"] += 1
                if metadata.get("embedding_version"):
                    metrics["embeddings_new"] += 1

            stale = connection.execute(
                """
                SELECT id FROM documents
                WHERE resource_id='mail-agent' AND source_type='email'
                  AND content_id IS NOT NULL
                """
            ).fetchall()
            for row in stale:
                document_id = int(row["id"])
                current = connection.execute(
                    "SELECT content_id FROM documents WHERE id=?", (document_id,)
                ).fetchone()
                if current is not None and str(current["content_id"]) in current_content_ids:
                    continue
                metrics["fts_rows_changed"] += remove_chunks(document_id)
                connection.execute("DELETE FROM documents WHERE id=?", (document_id,))
                metrics["removed"] += 1

            connection.execute("DELETE FROM mail_search_thread_members")
            connection.execute("DELETE FROM mail_search_threads")
            connection.execute("DELETE FROM mail_search_thread_edges")
            for edge in thread_build.edges:
                connection.execute(
                    """
                    INSERT INTO mail_search_thread_edges(
                        content_id,edge_type,relation_message_id,related_content_id,
                        evidence_header,selected,certainty,reason,index_generation
                    ) VALUES(?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        edge["content_id"], edge["edge_type"],
                        edge["relation_message_id"], edge["related_content_id"],
                        edge["evidence_header"], int(bool(edge["selected"])),
                        edge["certainty"], edge["reason"], edge["index_generation"],
                    ),
                )
                metrics["thread_rows_changed"] += 1
            for thread in thread_build.threads:
                connection.execute(
                    """
                    INSERT INTO mail_search_threads(
                        thread_id,root_content_id,thread_version,member_count,
                        first_at,last_at,uncertain,index_generation
                    ) VALUES(?,?,?,?,?,?,?,?)
                    """,
                    (
                        thread["thread_id"], thread["root_content_id"],
                        thread["thread_version"], thread["member_count"],
                        thread["first_at"], thread["last_at"],
                        int(bool(thread["uncertain"])), thread["index_generation"],
                    ),
                )
                metrics["thread_rows_changed"] += 1
            for member in thread_build.members:
                connection.execute(
                    """
                    INSERT INTO mail_search_thread_members(
                        content_id,thread_id,parent_content_id,evidence_type,
                        certainty,position,index_generation
                    ) VALUES(?,?,?,?,?,?,?)
                    """,
                    (
                        member["content_id"], member["thread_id"],
                        member["parent_content_id"], member["evidence_type"],
                        member["certainty"], member["position"],
                        member["index_generation"],
                    ),
                )
                metrics["thread_rows_changed"] += 1

            if current_occurrence_ids:
                placeholders = ",".join("?" for _ in current_occurrence_ids)
                connection.execute(
                    f"""
                    UPDATE mail_search_occurrences
                    SET tombstoned_at=?,source_status='tombstoned',index_generation=?
                    WHERE resource_id='mail-agent'
                      AND occurrence_id NOT IN ({placeholders})
                    """,
                    (timestamp, generation, *sorted(current_occurrence_ids)),
                )
            else:
                connection.execute(
                    """
                    UPDATE mail_search_occurrences
                    SET tombstoned_at=?,source_status='tombstoned',index_generation=?
                    WHERE resource_id='mail-agent'
                    """,
                    (timestamp, generation),
                )
            connection.execute(
                """
                INSERT OR REPLACE INTO mail_search_generations(
                    generation,schema_version,source_generated_at,imported_at,
                    complete,source_status,coverage_json
                ) VALUES(?,?,?,?,?,?,?)
                """,
                (
                    generation, 2, generated_at, timestamp, 1, "active",
                    json.dumps(coverage, ensure_ascii=False),
                ),
            )
            detail = json.dumps(
                {
                    "state": "complete",
                    "source_generation": generation,
                    "generated_at": generated_at,
                    "record_count": len(records),
                },
                ensure_ascii=False,
            )
            connection.execute(
                """
                INSERT INTO sync_state(resource_id,scope,cursor,etag,synced_at,status,detail)
                VALUES(?,?,?,?,?,?,?)
                ON CONFLICT(resource_id,scope) DO UPDATE SET
                    cursor=excluded.cursor,etag=excluded.etag,
                    synced_at=excluded.synced_at,status=excluded.status,detail=excluded.detail
                """,
                ("mail-agent", "projection", generation, generation, timestamp, "ok", detail),
            )
            if before_commit is not None:
                before_commit()
        return metrics

    def search_mail_lexical(
        self,
        query: str,
        *,
        filters: MailSearchFilters | None = None,
        limit: int = 20,
        max_age_seconds: int = 7200,
        context_limit: int = 0,
    ) -> dict[str, Any]:
        return MailLexicalSearch(
            self.knowledge_connection,
            fts_enabled=self.mail_search_fts_enabled,
        ).search(
            query,
            filters=filters,
            limit=limit,
            max_age_seconds=max_age_seconds,
            context_limit=context_limit,
        )

    def build_mail_embeddings(
        self,
        *,
        model: EmbeddingModel,
        provider: EmbeddingProvider,
        max_chunks: int = 1000,
        batch_size: int = 8,
    ) -> dict[str, Any]:
        """Populate the content-keyed semantic cache without changing mail state."""

        return MailEmbeddingIndex(self.knowledge_connection).build(
            model=model,
            provider=provider,
            max_chunks=max_chunks,
            batch_size=batch_size,
        )

    def search_mail_semantic(
        self,
        query: str,
        *,
        model: EmbeddingModel,
        provider: EmbeddingProvider,
        limit: int = 20,
    ) -> dict[str, Any]:
        """Return semantic candidates; M11.7 owns later hybrid/live routing."""

        return MailEmbeddingIndex(self.knowledge_connection).search(
            query,
            model=model,
            provider=provider,
            limit=limit,
        )

    def mail_index_status(
        self,
        *,
        max_age_seconds: int = 7200,
        semantic_model: EmbeddingModel | None = None,
    ) -> dict[str, Any]:
        return MailIndexDiagnostics(
            self.knowledge_connection,
            fts_enabled=self.mail_search_fts_enabled,
        ).status(
            max_age_seconds=max_age_seconds,
            semantic_model=semantic_model,
        )

    def mail_index_doctor(
        self,
        *,
        max_age_seconds: int = 7200,
        semantic_model: EmbeddingModel | None = None,
    ) -> dict[str, Any]:
        return MailIndexDiagnostics(
            self.knowledge_connection,
            fts_enabled=self.mail_search_fts_enabled,
        ).doctor(
            max_age_seconds=max_age_seconds,
            semantic_model=semantic_model,
        )

    def search(
        self,
        query: str,
        *,
        limit: int = 20,
        source_type: str = "",
        resource_id: str = "",
    ) -> list[SearchResult]:
        params: list[Any] = []
        filters = []
        if source_type:
            filters.append("d.source_type=?")
            params.append(source_type)
        if resource_id:
            filters.append("d.resource_id=?")
            params.append(resource_id)
        where_extra = (" AND " + " AND ".join(filters)) if filters else ""
        if self.fts_enabled and query.strip():
            sql = f"""
                SELECT d.*, c.text, bm25(knowledge_fts) AS rank
                FROM knowledge_fts
                JOIN chunks c ON c.id=knowledge_fts.rowid
                JOIN documents d ON d.id=c.document_id
                WHERE knowledge_fts MATCH ? {where_extra}
                ORDER BY rank ASC
                LIMIT ?
            """
            values = [query, *params, limit]
            try:
                rows = self.knowledge_connection.execute(sql, values).fetchall()
            except sqlite3.OperationalError:
                rows = self._search_like(query, params, where_extra, limit)
        else:
            rows = self._search_like(query, params, where_extra, limit)
        results: list[SearchResult] = []
        for row in rows:
            text = str(row["text"] or "")
            snippet = text[:500].replace("\n", " ")
            try:
                metadata = json.loads(str(row["metadata_json"] or "{}"))
            except json.JSONDecodeError:
                metadata = {}
            results.append(SearchResult(
                document_id=int(row["id"]),
                source_type=str(row["source_type"]),
                resource_id=str(row["resource_id"]),
                source_id=str(row["source_id"]),
                title=str(row["title"]),
                uri=str(row["uri"]),
                snippet=snippet,
                score=float(-row["rank"] if row["rank"] is not None else 0.0),
                metadata=metadata,
            ))
        return results


    def _search_like(self, query: str, params: list[Any], where_extra: str, limit: int) -> list[sqlite3.Row]:
        pattern = f"%{query}%"
        sql = f"""
            SELECT d.*, c.text, 0.0 AS rank
            FROM chunks c JOIN documents d ON d.id=c.document_id
            WHERE (c.text LIKE ? OR d.title LIKE ?) {where_extra}
            ORDER BY d.modified_at DESC, d.id DESC LIMIT ?
        """
        return self.knowledge_connection.execute(
            sql, [pattern, pattern, *params, limit]
        ).fetchall()

    def create_action(
        self,
        *,
        idempotency_key: str,
        action_type: str,
        resource_id: str,
        payload: dict[str, Any],
        requires_approval: bool,
    ) -> ActionPlan:
        action_id = str(uuid.uuid4())
        timestamp = now_utc_iso()
        status = "proposed" if requires_approval else "approved"
        with self.connection:
            self.connection.execute(
                """
                INSERT OR IGNORE INTO action_plans(
                    id,idempotency_key,action_type,resource_id,payload_json,status,
                    requires_approval,created_at,updated_at,error
                ) VALUES(?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    action_id, idempotency_key, action_type, resource_id,
                    json.dumps(payload, ensure_ascii=False), status,
                    int(requires_approval), timestamp, timestamp, "",
                ),
            )
            row = self.connection.execute(
                "SELECT * FROM action_plans WHERE idempotency_key=?",
                (idempotency_key,),
            ).fetchone()
        if row is None:
            raise RuntimeError("ActionPlan konnte nach idempotentem Insert nicht gelesen werden")
        return self._row_action(row)

    def get_action(self, action_id: str) -> ActionPlan:
        row = self.connection.execute("SELECT * FROM action_plans WHERE id=?", (action_id,)).fetchone()
        if not row:
            raise KeyError(f"Unbekannter ActionPlan: {action_id}")
        return self._row_action(row)

    def list_actions(self, status: str = "", limit: int = 100) -> list[ActionPlan]:
        if status:
            rows = self.connection.execute(
                "SELECT * FROM action_plans WHERE status=? ORDER BY created_at DESC LIMIT ?",
                (status, limit),
            ).fetchall()
        else:
            rows = self.connection.execute(
                "SELECT * FROM action_plans ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [self._row_action(row) for row in rows]

    def update_action(self, action_id: str, status: str, error: str = "") -> ActionPlan:
        self.connection.execute(
            "UPDATE action_plans SET status=?,error=?,updated_at=? WHERE id=?",
            (status, error, now_utc_iso(), action_id),
        )
        self.connection.commit()
        return self.get_action(action_id)

    @staticmethod
    def _row_action(row: sqlite3.Row) -> ActionPlan:
        return ActionPlan(
            id=str(row["id"]),
            idempotency_key=str(row["idempotency_key"]),
            action_type=str(row["action_type"]),
            resource_id=str(row["resource_id"]),
            payload=json.loads(str(row["payload_json"])),
            status=str(row["status"]),
            requires_approval=bool(row["requires_approval"]),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
            error=str(row["error"] or ""),
        )
