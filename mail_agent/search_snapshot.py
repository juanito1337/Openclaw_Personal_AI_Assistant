from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .models import ParsedMessage
from .utils import atomic_write_bytes, now_utc_iso, safe_filename


class SearchSnapshotWriter:
    """Persist a compact, searchable mail representation for the assistant index.

    The original EML is not duplicated here. The snapshot contains normalized
    body text, source metadata, and attachment names. It is local runtime data,
    ignored by Git, and can be rebuilt from future mailbox processing.
    """

    def __init__(self, root: Path, *, enabled: bool = True, max_body_chars: int = 200_000) -> None:
        self.root = root
        self.enabled = enabled
        self.max_body_chars = max_body_chars
        if enabled:
            root.mkdir(parents=True, exist_ok=True)

    def write(self, message: ParsedMessage) -> Path | None:
        if not self.enabled:
            return None
        filename = safe_filename(message.stable_key.replace(":", "-"), "message") + ".json"
        path = self.root / filename
        payload = {
            "schema": 1,
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
        atomic_write_bytes(path, (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
        return path
