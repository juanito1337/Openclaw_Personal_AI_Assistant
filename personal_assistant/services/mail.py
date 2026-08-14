from __future__ import annotations

from typing import Any


class MailApplicationMixin:
    mail_move_service: Any

    def mail_move_status(self) -> dict[str, Any]:
        return self.mail_move_service.status()

    def mail_list_messages(self, folder: str, *, limit: int = 50) -> dict[str, Any]:
        return self.mail_move_service.list_messages(folder, limit=limit)

    def mail_search_messages(self, query: str, *, limit: int = 50) -> dict[str, Any]:
        return self.mail_move_service.search_messages(query, limit=limit)

    def mail_read_message(
        self, folder: str, message_id: str, *, expected_subject: str = ""
    ) -> dict[str, Any]:
        return self.mail_move_service.read(folder, message_id, expected_subject=expected_subject)

    def mail_draft_reply(
        self, folder: str, message_id: str, body: str, *, expected_subject: str = ""
    ) -> dict[str, Any]:
        return self.mail_move_service.draft_reply(folder, message_id, body, expected_subject=expected_subject)

    def mail_send_reply(self, draft_id: str, *, approved: bool = False) -> dict[str, Any]:
        return self.mail_move_service.send_reply(draft_id, approved=approved)

    def mail_draft_message(self, recipient: str, subject: str, body: str) -> dict[str, Any]:
        return self.mail_move_service.draft_message(recipient, subject, body)

    def mail_send_message(self, draft_id: str, *, approved: bool = False) -> dict[str, Any]:
        return self.mail_move_service.send_message(draft_id, approved=approved)

    def mail_move_message(
        self,
        *,
        source: str,
        destination: str,
        message_id: str,
        expected_subject: str = "",
        dry_run: bool = False,
    ) -> dict[str, Any]:
        return self.mail_move_service.move(
            source=source,
            destination=destination,
            message_id=message_id,
            expected_subject=expected_subject,
            dry_run=dry_run,
        )

    def mail_correct_review(
        self,
        *,
        source: str,
        message_id: str,
        expected_subject: str,
        verdict: str,
        label: str = "",
        approved: bool = False,
    ) -> dict[str, Any]:
        return self.mail_move_service.review_correct(
            source=source,
            message_id=message_id,
            expected_subject=expected_subject,
            verdict=verdict,
            label=label,
            approved=approved,
        )
