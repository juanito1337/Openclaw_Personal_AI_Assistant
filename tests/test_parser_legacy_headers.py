from __future__ import annotations

import unittest

from mail_agent.models import Envelope
from mail_agent.parser import parse_eml


class LegacyHeaderParserTests(unittest.TestCase):
    def test_malformed_structured_headers_do_not_abort_mail(self) -> None:
        raw = b"""From: Bonifac Karaka : bonikar@gmail.com\r\nTo: :Foo <foo@example.com> <bar@example.com>\r\nReply-To: \"\"\r\nSubject: Malformed legacy message\r\nMessage-ID: <A@\r\nDate: Fri, 17 Jul 2026 10:00:00 +0200\r\nContent-Type: text/plain; charset=utf-8\r\n\r\nThe body must remain readable.\r\n"""
        message = parse_eml(raw, Envelope("76894", subject="Fallback subject"), "INBOX")
        self.assertEqual(message.mailbox_id, "76894")
        self.assertIn("body must remain readable", message.body_text)
        self.assertTrue(message.stable_key)

    def test_malformed_attachment_metadata_is_isolated(self) -> None:
        raw = b"""From: sender@example.test\r\nTo: jan@example.test\r\nSubject: Broken attachment metadata\r\nMessage-ID: <broken-attachment@example.test>\r\nMIME-Version: 1.0\r\nContent-Type: multipart/mixed; boundary=x\r\n\r\n--x\r\nContent-Type: text/plain; charset=utf-8\r\n\r\nBody text.\r\n--x\r\nContent-Type: application/pdf; name*=\r\nContent-Disposition: attachment; filename*=\r\n\r\nnot-a-real-pdf\r\n--x--\r\n"""
        message = parse_eml(raw, Envelope("76895"), "INBOX")
        self.assertIn("Body text", message.body_text)
        self.assertEqual(len(message.attachments), 1)

    def test_normal_message_keeps_stable_message_id(self) -> None:
        raw = b"""From: Example <example@example.test>\r\nTo: Jan <jan@example.test>\r\nSubject: Normal message\r\nMessage-ID: <normal@example.test>\r\nContent-Type: text/plain; charset=utf-8\r\n\r\nNormal body.\r\n"""
        message = parse_eml(raw, Envelope("1"), "INBOX")
        self.assertEqual(message.stable_key, "mid:normal@example.test")
        self.assertEqual(message.sender_addr, "example@example.test")

    def test_mailbox_arrival_time_is_kept_separate_from_date_header(self) -> None:
        raw = b"From: sender@example.test\r\nSubject: Arrival test\r\nDate: Mon, 20 Jul 2026 08:00:00 +0200\r\n\r\nBody\r\n"
        envelope = Envelope(
            "2", date="Mon, 20 Jul 2026 08:00:00 +0200",
            received_at="Fri, 24 Jul 2026 14:35:00 +0200",
        )
        message = parse_eml(raw, envelope, "INBOX")
        self.assertEqual(message.date, "Mon, 20 Jul 2026 08:00:00 +0200")
        self.assertEqual(message.received_at, "Fri, 24 Jul 2026 14:35:00 +0200")


if __name__ == "__main__":
    unittest.main()
