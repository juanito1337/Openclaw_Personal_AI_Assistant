from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from .config import AssistantConfig
from .contracts.mail_projection import SearchProjectionError, load_search_projection
from .extractors import chunks, extract_text, sha256_bytes
from .storage import AssistantStorage


class KnowledgeIndexer:
    def __init__(self, config: AssistantConfig, storage: AssistantStorage) -> None:
        self.config = config
        self.storage = storage

    def index_mail_database(self, mail_db: Path) -> dict[str, int]:
        stats = {"seen": 0, "indexed": 0, "unchanged": 0}
        if not mail_db.exists():
            return stats
        connection = sqlite3.connect(f"file:{mail_db.resolve()}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        try:
            rows = connection.execute(
                "SELECT stable_key,sender_addr,sender_name,subject,received_at,category,reason,summary,expected_action,status,updated_at FROM messages ORDER BY updated_at"
            ).fetchall()
        finally:
            connection.close()
        for row in rows:
            stats["seen"] += 1
            source_id = str(row["stable_key"])
            existing = self.storage.get_document("mail-agent", source_id)
            modified = str(row["updated_at"] or "")
            if existing and str(existing["modified_at"] or "") == modified:
                stats["unchanged"] += 1
                continue
            text = "\n".join(
                value for value in (
                    f"Von: {row['sender_name'] or ''} <{row['sender_addr'] or ''}>",
                    f"Betreff: {row['subject'] or ''}",
                    f"Kategorie: {row['category'] or ''}",
                    f"Status: {row['status'] or ''}",
                    str(row["summary"] or ""),
                    str(row["reason"] or ""),
                    str(row["expected_action"] or ""),
                ) if value
            )
            self.storage.index_document(
                source_type="email",
                resource_id="mail-agent",
                source_id=source_id,
                uri=f"mail-agent://{source_id}",
                title=str(row["subject"] or "(ohne Betreff)"),
                mime_type="message/rfc822-metadata",
                modified_at=modified,
                metadata={
                    "sender_addr": row["sender_addr"],
                    "sender_name": row["sender_name"],
                    "received_at": row["received_at"],
                    "category": row["category"],
                    "status": row["status"],
                },
                chunks=chunks(
                    text,
                    size=self.config.search.chunk_chars,
                    overlap=self.config.search.chunk_overlap_chars,
                ),
            )
            stats["indexed"] += 1
        return stats

    def _mail_projection_failure(self, state: str, detail: str) -> dict[str, Any]:
        previous = self.storage.get_sync_state("mail-agent", "projection")
        last_generation = str(previous["cursor"] or "") if previous else ""
        status_detail = {
            "state": state,
            "error": detail,
            "last_complete_source_generation": last_generation,
        }
        self.storage.set_sync_state(
            "mail-agent",
            "projection",
            cursor=last_generation,
            status=state,
            detail=json.dumps(status_detail, ensure_ascii=False),
        )
        return {
            "seen": 0,
            "indexed": 0,
            "unchanged": 0,
            "published": False,
            **status_detail,
        }

    def index_mail_snapshots(self) -> dict[str, Any]:
        root = self.config.search.mail_snapshot_dir
        if not root.exists():
            return self._mail_projection_failure(
                "missing", "Mail-Suchprojektionsverzeichnis fehlt"
            )
        try:
            projection = load_search_projection(
                root,
                max_age_seconds=self.config.search.mail_projection_max_age_seconds,
            )
        except SearchProjectionError as exc:
            state = "stale" if "veraltet" in str(exc).casefold() else "invalid"
            return self._mail_projection_failure(state, str(exc))
        if projection.schema >= 2 and not projection.complete:
            return self._mail_projection_failure(
                "partial",
                "Mail-Suchprojektion v2 besitzt keinen vollstaendigen Coverage-Nachweis",
            )

        # Validate the complete source generation before the first knowledge
        # write; incomplete or corrupt projections never become index input.
        stats: dict[str, Any] = {
            "seen": 0,
            "indexed": 0,
            "unchanged": 0,
            "published": False,
            "state": "validated",
            "source_generation": projection.generation,
            "generated_at": projection.generated_at,
            "age_seconds": projection.age_seconds,
        }
        for _path, payload in projection.records:
            stats["seen"] += 1
            source_id = str(payload["stable_key"])
            modified = str(payload["indexed_source_at"])
            existing = self.storage.get_document("mail-agent", source_id)
            if existing and str(existing["modified_at"] or "") == modified:
                stats["unchanged"] += 1
                continue
            body = str(payload.get("body_text") or "")
            metadata = dict(payload.get("metadata") or {})
            title = str(payload.get("subject") or "(ohne Betreff)")
            text = "\n".join([
                f"Von: {payload.get('sender_name','')} <{payload.get('sender_addr','')}>",
                f"Betreff: {title}",
                body,
            ])
            self.storage.index_document(
                source_type="email",
                resource_id="mail-agent",
                source_id=source_id,
                uri=f"mail-agent://{source_id}",
                title=title,
                mime_type="message/rfc822",
                modified_at=modified,
                sha256=str(payload.get("sha256") or ""),
                metadata=metadata,
                content_id=str(payload.get("content_id") or ""),
                index_generation=projection.generation,
                source_status=str(metadata.get("source_status") or "active"),
                embedding_version=str(metadata.get("embedding_version") or ""),
                chunks=chunks(
                    text,
                    size=self.config.search.chunk_chars,
                    overlap=self.config.search.chunk_overlap_chars,
                ),
            )
            stats["indexed"] += 1
        detail = {
            "state": "complete",
            "source_generation": projection.generation,
            "generated_at": projection.generated_at,
            "age_seconds": projection.age_seconds,
            "record_count": len(projection.records),
        }
        self.storage.set_sync_state(
            "mail-agent",
            "projection",
            cursor=projection.generation,
            etag=projection.generation,
            status="ok",
            detail=json.dumps(detail, ensure_ascii=False),
        )
        stats.update(
            {
                "published": True,
                "state": "complete",
                "last_complete_source_generation": projection.generation,
            }
        )
        return stats

    def index_binary_document(
        self,
        *,
        resource_id: str,
        source_type: str,
        source_id: str,
        uri: str,
        title: str,
        filename: str,
        data: bytes,
        mime_type: str = "",
        modified_at: str = "",
        etag: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        existing = self.storage.get_document(resource_id, source_id)
        digest = sha256_bytes(data)
        if existing and str(existing["etag"] or "") == etag and str(existing["sha256"] or "") == digest:
            return False
        text = extract_text(filename, data, max_chars=self.config.search.max_text_chars)
        if not text:
            text = "\n".join(
                value for value in (
                    title,
                    str((metadata or {}).get("description") or ""),
                    f"Dateiname: {filename}",
                ) if value
            )
        self.storage.index_document(
            source_type=source_type,
            resource_id=resource_id,
            source_id=source_id,
            uri=uri,
            title=title,
            mime_type=mime_type,
            modified_at=modified_at,
            etag=etag,
            sha256=digest,
            metadata=metadata or {},
            chunks=chunks(text, size=self.config.search.chunk_chars, overlap=self.config.search.chunk_overlap_chars),
        )
        return True
