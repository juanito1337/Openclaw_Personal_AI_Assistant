from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from personal_assistant.contracts.mail_projection import (
    PROJECTION_MANIFEST,
    PROJECTION_SCHEMA,
    SearchProjectionError,
    canonical_projection_generation,
    load_projection_record,
    load_search_projection,
)

from .models import ParsedMessage
from .utils import atomic_write_bytes, now_utc_iso, safe_filename


class SearchSnapshotWriter:
    """Publish an atomic generation of immutable, searchable mail records.

    The original EML is never duplicated. A reader consumes only files referenced
    by the atomically replaced manifest, so a crash before publication leaves the
    previous complete generation readable.
    """

    def __init__(self, root: Path, *, enabled: bool = True, max_body_chars: int = 200_000) -> None:
        self.root = root
        self.enabled = enabled
        self.max_body_chars = max_body_chars
        if enabled:
            root.mkdir(parents=True, exist_ok=True)

    def _existing_references(self) -> dict[str, dict[str, Any]]:
        manifest = self.root / PROJECTION_MANIFEST
        if manifest.exists():
            projection = load_search_projection(self.root)
            result: dict[str, dict[str, Any]] = {}
            for path, payload in projection.records:
                raw = path.read_bytes()
                result[str(payload["stable_key"])] = {
                    "filename": path.name,
                    "stable_key": str(payload["stable_key"]),
                    "sha256": hashlib.sha256(raw).hexdigest(),
                    "indexed_source_at": str(payload["indexed_source_at"]),
                }
            return result

        # One-time adoption of valid pre-manifest snapshots. Invalid legacy files
        # are not published and cannot poison the new complete generation.
        result = {}
        for path in sorted(self.root.glob("*.json")):
            if path.name == PROJECTION_MANIFEST:
                continue
            try:
                payload, digest = load_projection_record(path)
            except SearchProjectionError:
                continue
            key = str(payload["stable_key"])
            candidate = {
                "filename": path.name,
                "stable_key": key,
                "sha256": digest,
                "indexed_source_at": str(payload["indexed_source_at"]),
            }
            current = result.get(key)
            if current is None or (candidate["indexed_source_at"], candidate["filename"]) > (
                current["indexed_source_at"], current["filename"]
            ):
                result[key] = candidate
        return result

    def write(self, message: ParsedMessage) -> Path | None:
        if not self.enabled:
            return None
        payload = {
            "schema": PROJECTION_SCHEMA,
            "stable_key": message.stable_key,
            "message_id": message.message_id,
            "subject": message.subject,
            "sender_addr": message.sender_addr,
            "sender_name": message.sender_name,
            "body_text": message.body_text[: self.max_body_chars],
            "sha256": hashlib.sha256(message.raw).hexdigest(),
            "indexed_source_at": now_utc_iso(),
            "metadata": {
                "date": message.date,
                "received_at": message.received_at or message.date,
                "source_folder": message.source_folder,
                "attachments": [
                    {
                        "filename": item.filename,
                        "content_type": item.content_type,
                        "size": item.size,
                    }
                    for item in message.attachments
                ],
            },
        }
        data = (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        digest = hashlib.sha256(data).hexdigest()
        stem = safe_filename(message.stable_key.replace(":", "-"), "message")
        path = self.root / f"{stem}-{digest[:16]}.json"
        atomic_write_bytes(path, data)

        references = self._existing_references()
        references[message.stable_key] = {
            "filename": path.name,
            "stable_key": message.stable_key,
            "sha256": digest,
            "indexed_source_at": str(payload["indexed_source_at"]),
        }
        self._publish(references)
        return path

    def _publish(self, references: dict[str, dict[str, Any]]) -> None:
        records = sorted(references.values(), key=lambda row: (row["stable_key"], row["filename"]))
        manifest = {
            "schema": PROJECTION_SCHEMA,
            "generated_at": now_utc_iso(),
            "source_generation": canonical_projection_generation(records),
            "record_count": len(records),
            "records": records,
        }
        atomic_write_bytes(
            self.root / PROJECTION_MANIFEST,
            (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8"),
        )

    def refresh(self) -> Path | None:
        """Republish the same verified source generation with a fresh timestamp."""
        if not self.enabled:
            return None
        self._publish(self._existing_references())
        return self.root / PROJECTION_MANIFEST
