from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from mail_agent.config import load_config
from mail_agent.models import Envelope
from mail_agent.parser import parse_eml
from mail_agent.rules import RuleEngine
from mail_agent.storage import Storage

POSTMASTER_MARKETING = b"""From: bike-angebot.de <postmaster@xmail.bike-angebot.de>\r
To: Jan <jan@example.test>\r
Subject: Neu: Wir kaufen Dein Fahrrad!\r
Message-ID: <bike-postmaster-ad@example.test>\r
Date: Wed, 02 Oct 2024 18:01:13 +0200\r
Content-Type: text/plain; charset=utf-8\r
\r
Jetzt Fahrrad verkaufen und Angebot sichern.\r
Falls diese Nachricht nicht zugestellt werden kann, wenden Sie sich an den Support.\r
Newsletter abbestellen.\r
"""

REAL_DSN = b"""From: Mail Delivery System <mailer-daemon@example.test>\r
To: Jan <jan@example.test>\r
Subject: Zustellbericht\r
Message-ID: <real-dsn@example.test>\r
MIME-Version: 1.0\r
Content-Type: multipart/report; report-type=delivery-status; boundary=dsn\r
\r
--dsn\r
Content-Type: text/plain; charset=utf-8\r
\r
Eine Nachricht konnte nicht zugestellt werden.\r
--dsn\r
Content-Type: message/delivery-status\r
\r
Final-Recipient: rfc822; missing@example.test\r
Action: failed\r
Status: 5.1.1\r
Diagnostic-Code: smtp; 550 mailbox unavailable\r
--dsn--\r
"""


class BounceGuard321Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        source_config = Path(__file__).parents[1] / "mail_agent/config.example.toml"
        config_text = source_config.read_text(encoding="utf-8")
        config_text = config_text.replace("mail_agent/data/", str(self.root / "data") + "/")
        config_text = config_text.replace(
            'rules_file = "mail_agent/rules.toml"',
            f'rules_file = "{self.root / "rules.toml"}"',
        )
        config_text = config_text.replace(
            'log_file = "mail_agent/data/mail_agent.log"',
            f'log_file = "{self.root / "mail_agent.log"}"',
        )
        self.config_path = self.root / "config.toml"
        self.config_path.write_text(config_text, encoding="utf-8")
        self.rules_path = self.root / "rules.toml"
        self._write_rules(spam_domains=[])
        self.config = load_config(self.config_path)
        self.storage = Storage(self.config.runtime.database)

    def tearDown(self) -> None:
        self.storage.close()
        self.temp.cleanup()

    def _write_rules(self, *, spam_domains: list[str]) -> None:
        domain_values = ", ".join(f'"{item}"' for item in spam_domains)
        self.rules_path.write_text(
            "[spam]\n"
            "addresses=[]\n"
            f"domains=[{domain_values}]\n"
            "sender_names=[]\n"
            "subject_phrases=[]\n"
            "[important]\naddresses=[]\ndomains=[]\n"
            "[routine]\naddresses=[]\ndomains=[]\n",
            encoding="utf-8",
        )

    def test_postmaster_plus_generic_delivery_footer_is_not_a_bounce(self) -> None:
        message = parse_eml(POSTMASTER_MARKETING, Envelope("1"), "INBOX")
        result = RuleEngine(self.rules_path, self.storage).evaluate(message)
        self.assertTrue(result.forced is None or result.forced.category != "relevant")

    def test_hard_spam_domain_wins_for_postmaster_subdomain(self) -> None:
        self._write_rules(spam_domains=["bike-angebot.de"])
        message = parse_eml(POSTMASTER_MARKETING, Envelope("2"), "INBOX")
        result = RuleEngine(self.rules_path, self.storage).evaluate(message)
        self.assertIsNotNone(result.forced)
        self.assertEqual(result.forced.category, "spam")
        self.assertFalse(result.forced.forward)

    def test_standards_compliant_delivery_status_is_relevant(self) -> None:
        message = parse_eml(REAL_DSN, Envelope("3"), "INBOX")
        result = RuleEngine(self.rules_path, self.storage).evaluate(message)
        self.assertIsNotNone(result.forced)
        self.assertEqual(result.forced.category, "relevant")
        self.assertTrue(result.forced.forward)


if __name__ == "__main__":
    unittest.main()
