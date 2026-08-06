from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from mail_agent.config import load_config
from mail_agent.models import Classification, Envelope
from mail_agent.parser import parse_eml
from mail_agent.rules import RuleEngine
from mail_agent.storage import Storage
from personal_assistant.cli import parser as assistant_parser
from personal_assistant.tool_registry import build_tool_registry
from personal_assistant.tool_settings import ToolSettings

BASE = b"""From: Shop <news@example.test>
To: Jan <jan@example.test>
Subject: Produktinfo 4711
Message-ID: <base@example.test>
Date: Fri, 24 Jul 2026 10:00:00 +0200
Content-Type: text/plain; charset=utf-8

Allgemeine Information.
"""


def message(mid: str, subject: bytes = b"Produktinfo 4711"):
    raw = BASE.replace(b"<base@example.test>", f"<{mid}@example.test>".encode()).replace(
        b"Produktinfo 4711", subject
    )
    return parse_eml(raw, Envelope(mid), "INBOX")


class NotSpamLearningTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        text = Path("mail_agent/config.example.toml").read_text(encoding="utf-8")
        text = text.replace("mail_agent/data/", str(root / "data") + "/")
        text = text.replace('rules_file = "mail_agent/rules.toml"', f'rules_file = "{root / "rules.toml"}"')
        text = text.replace('log_file = "mail_agent/data/mail_agent.log"', f'log_file = "{root / "mail.log"}"')
        config = root / "config.toml"
        config.write_text(text, encoding="utf-8")
        self.config_path = config
        (root / "rules.toml").write_text(
            """[spam]
addresses=["news@example.test"]
domains=[]
sender_names=[]
subject_phrases=[]
[important]
addresses=[]
domains=[]
[routine]
addresses=[]
domains=[]
""",
            encoding="utf-8",
        )
        self.config = load_config(config)
        self.storage = Storage(self.config.runtime.database)

    def tearDown(self) -> None:
        self.storage.close()
        self.temp.cleanup()

    def test_not_spam_blocks_spam_for_same_pattern(self) -> None:
        first = message("spam-one")
        second = message("spam-two")
        restored = message("restored")
        candidate = message("candidate")
        self.storage.record_feedback(first, "spam", "Agent/Korrektur-Spam")
        self.storage.record_feedback(second, "spam", "Agent/Korrektur-Spam")
        self.storage.upsert_message(restored, Classification("spam", .99, 1, False, "spam"), status="spam")
        self.storage.record_feedback(
            restored,
            "not_spam",
            "INBOX-Restore",
            metadata={"origin": "inbox-restore", "previous_status": "spam"},
        )
        decision = self.storage.pattern_feedback_decision(candidate)
        self.assertEqual(decision["verdict"], "not_spam")
        self.assertEqual(decision["countered_verdict"], "spam")
        context = RuleEngine(self.config.runtime.rules_file, self.storage).evaluate(candidate)
        self.assertIsNone(context.forced)
        self.assertTrue(context.prevent_spam)
        self.assertTrue(any("inbox-restore" in note for note in context.notes or []))

    def test_not_spam_does_not_override_consistent_routine(self) -> None:
        self.storage.record_feedback(message("routine-one"), "routine", "Agent/Korrektur-Unwichtig")
        self.storage.record_feedback(message("routine-two"), "routine", "Agent/Korrektur-Unwichtig")
        self.storage.record_feedback(
            message("notspam"), "not_spam", "INBOX-Restore", metadata={"origin": "inbox-restore"}
        )
        context = RuleEngine(self.config.runtime.rules_file, self.storage).evaluate(message("candidate"))
        self.assertIsNotNone(context.forced)
        self.assertEqual(context.forced.category, "routine")

    def test_not_spam_does_not_override_relevant_pattern(self) -> None:
        self.storage.record_feedback(message("important"), "relevant", "Agent/Korrektur-Wichtig")
        self.storage.record_feedback(
            message("notspam"), "not_spam", "INBOX-Restore", metadata={"origin": "inbox-restore"}
        )
        context = RuleEngine(self.config.runtime.rules_file, self.storage).evaluate(message("candidate"))
        self.assertTrue(context.important_sender)
        self.assertTrue(context.prevent_spam)

    def test_not_spam_is_not_sender_wide_for_unrelated_subject(self) -> None:
        self.storage.record_feedback(
            message("notspam"), "not_spam", "INBOX-Restore", metadata={"origin": "inbox-restore"}
        )
        unrelated = message("other", b"Gewinnspiel Sonderangebot")
        context = RuleEngine(self.config.runtime.rules_file, self.storage).evaluate(unrelated)
        self.assertIsNotNone(context.forced)
        self.assertEqual(context.forced.category, "spam")

    def test_not_spam_report_exposes_origin_without_identity(self) -> None:
        item = message("notspam")
        self.storage.upsert_message(item, Classification("spam", .99, 1, False, "spam"), status="spam")
        self.storage.record_feedback(
            item,
            "not_spam",
            "INBOX-Restore",
            metadata={"origin": "inbox-restore", "previous_status": "quarantine-reviewed"},
        )
        report = self.storage.list_not_spam_feedback(limit=10)
        self.assertEqual(report[0]["origin"], "inbox-restore")
        self.assertEqual(report[0]["previous_status"], "quarantine-reviewed")
        self.assertNotIn("sender_addr", report[0])
        self.assertNotIn("subject", report[0])
        self.assertEqual(self.storage.not_spam_feedback_summary()["origins"]["inbox-restore"], 1)

    def test_agent_and_mail_clis_expose_not_spam_origin(self) -> None:
        item = message("notspam")
        self.storage.record_feedback(
            item,
            "not_spam",
            "INBOX-Restore",
            metadata={"origin": "inbox-restore", "previous_status": "spam"},
        )
        completed = subprocess.run(
            [sys.executable, "-m", "mail_agent", "--config", str(self.config_path), "training", "not-spam", "--limit", "10"],
            cwd=Path(__file__).parents[1],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        payload = json.loads(completed.stdout)
        self.assertEqual(payload["records"][0]["origin"], "inbox-restore")
        args = assistant_parser().parse_args(["mail", "learning", "not-spam", "--limit", "7"])
        self.assertEqual(args.learning_command, "not-spam")
        self.assertEqual(args.limit, 7)
        settings = ToolSettings(path=Path(self.temp.name) / "tools.toml")
        ids = {tool.id for tool in build_tool_registry(settings)}
        self.assertIn("mail.learning.not-spam", ids)


if __name__ == "__main__":
    unittest.main()
