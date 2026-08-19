from __future__ import annotations

import json
import os
import sqlite3
import uuid
from pathlib import Path
from typing import Any

from .contracts.time import now_utc_iso
from .models import ActionPlan, SearchResult

CORE_SCHEMA_VERSION = 1
KNOWLEDGE_SCHEMA_VERSION = 2
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
        else:
            self.fts_enabled = False
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
            self.connection.execute(f"PRAGMA user_version={core_target}")
            self.connection.commit()
        if not self.enable_knowledge or self.knowledge_read_only:
            return

        knowledge_version = int(
            self.knowledge_connection.execute("PRAGMA user_version").fetchone()[0]
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
                index_generation TEXT NOT NULL,
                PRIMARY KEY(content_id, edge_type, relation_message_id)
            );
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
        try:
            self.knowledge_connection.execute(
                "CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_fts USING fts5(title, text, source_type, resource_id, tokenize='unicode61 remove_diacritics 2')"
            )
            self.fts_enabled = True
        except sqlite3.OperationalError:
            self.fts_enabled = False
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

    def audit(self, event_type: str, detail: dict[str, Any], *, resource_id: str = "", actor: str = "assistant") -> None:
        self.connection.execute(
            "INSERT INTO audit_log(actor,event_type,resource_id,detail_json,created_at) VALUES(?,?,?,?,?)",
            (actor, event_type, resource_id, json.dumps(detail, ensure_ascii=False), now_utc_iso()),
        )
        self.connection.commit()

    def set_sync_state(self, resource_id: str, scope: str, *, cursor: str = "", etag: str = "", status: str, detail: str = "") -> None:
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
        self.knowledge_connection.execute(
            "DELETE FROM chunks WHERE document_id=?", (document_id,)
        )
        for index, text in enumerate(chunks):
            cursor = self.knowledge_connection.execute(
                "INSERT INTO chunks(document_id,chunk_index,text) VALUES(?,?,?)",
                (document_id, index, text),
            )
            chunk_id = int(cursor.lastrowid)
            if self.fts_enabled:
                self.knowledge_connection.execute(
                    "INSERT INTO knowledge_fts(rowid,title,text,source_type,resource_id) VALUES(?,?,?,?,?)",
                    (chunk_id, title, text, source_type, resource_id),
                )
        self.knowledge_connection.commit()
        return document_id

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
