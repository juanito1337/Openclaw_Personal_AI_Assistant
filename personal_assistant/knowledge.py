from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from .config import AssistantConfig
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
                chunks=chunks(text, size=self.config.search.chunk_chars, overlap=self.config.search.chunk_overlap_chars),
            )
            stats["indexed"] += 1
        return stats

    def index_mail_snapshots(self) -> dict[str, int]:
        stats = {"seen": 0, "indexed": 0, "unchanged": 0, "invalid": 0}
        root = self.config.search.mail_snapshot_dir
        if not root.exists():
            return stats
        for path in sorted(root.glob("*.json")):
            stats["seen"] += 1
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                source_id = str(payload["stable_key"])
                modified = str(payload.get("indexed_source_at") or path.stat().st_mtime_ns)
            except (OSError, ValueError, KeyError, json.JSONDecodeError):
                stats["invalid"] += 1
                continue
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
                chunks=chunks(text, size=self.config.search.chunk_chars, overlap=self.config.search.chunk_overlap_chars),
            )
            stats["indexed"] += 1
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
