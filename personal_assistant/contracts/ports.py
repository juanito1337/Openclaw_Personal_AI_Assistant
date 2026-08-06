from __future__ import annotations

from typing import Any, Protocol


class MailMessagePort(Protocol):
    """Narrow read-only message view consumed by non-mail core domains."""

    sender_addr: str
    sender_name: str
    body_text: str
    stable_key: str
    message_id: str
    subject: str
    raw: bytes
    source_folder: str
    mailbox_id: str
    received_at: str


class MailOperationsPort(Protocol):
    """Operations required by the assistant core from a concrete mail adapter."""

    def status(self) -> dict[str, Any]: ...
    def list_messages(self, folder: str, *, limit: int = 50) -> dict[str, Any]: ...
    def search_messages(self, query: str, *, limit: int = 50) -> dict[str, Any]: ...
    def read_message(
        self, folder: str, message_id: str, *, expected_subject: str = ""
    ) -> MailMessagePort: ...
    def read(self, folder: str, message_id: str, *, expected_subject: str = "") -> dict[str, Any]: ...
    def draft_reply(
        self, folder: str, message_id: str, body: str, *, expected_subject: str = ""
    ) -> dict[str, Any]: ...
    def send_reply(self, draft_id: str, *, approved: bool) -> dict[str, Any]: ...
    def draft_message(self, recipient: str, subject: str, body: str) -> dict[str, Any]: ...
    def send_message(self, draft_id: str, *, approved: bool) -> dict[str, Any]: ...
    def move(
        self,
        *,
        source: str,
        destination: str,
        message_id: str,
        expected_subject: str = "",
        dry_run: bool = False,
    ) -> dict[str, Any]: ...
