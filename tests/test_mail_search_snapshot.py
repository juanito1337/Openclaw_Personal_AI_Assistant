from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from mail_agent.models import AttachmentInfo, ParsedMessage
from mail_agent.search_snapshot import SearchSnapshotWriter


class MailSearchSnapshotTests(unittest.TestCase):
    def test_snapshot_contains_searchable_text_without_raw_eml(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            writer = SearchSnapshotWriter(Path(temp))
            message = ParsedMessage(
                stable_key="mid:test@example.org",
                mailbox_id="42",
                message_id="test@example.org",
                source_folder="INBOX",
                sender_name="Firma",
                sender_addr="firma@example.org",
                subject="Rechnung 123",
                date="2026-07-20",
                body_text="Bitte Rechnung bezahlen.",
                attachments=[AttachmentInfo("rechnung.pdf", "application/pdf", 1234)],
                calendar_invites=[],
                raw=b"raw eml bytes",
            )
            path = writer.write(message)
            assert path is not None
            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(payload["subject"], "Rechnung 123")
            self.assertEqual(payload["metadata"]["attachments"][0]["filename"], "rechnung.pdf")
            self.assertNotIn("raw", payload)


if __name__ == "__main__":
    unittest.main()
