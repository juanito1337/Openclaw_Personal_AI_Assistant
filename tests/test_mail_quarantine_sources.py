from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from mail_agent.app import MailAgent, RunSummary
from mail_agent.config import load_config
from mail_agent.models import Classification, OperationResult, ParsedMessage
from mail_agent.storage import Storage
from personal_assistant.tool_registry import build_tool_registry
from personal_assistant.mail_source_setup import configure_mail_sources
from personal_assistant.tool_settings import MailToolSettings, ToolSettings


class NoInvoice:
    @staticmethod
    def process(message, classification):
        return OperationResult(True, "invoice-not-detected", "Kein Rechnungs-PDF")


class ArchivedInvoice:
    @staticmethod
    def process(message, classification):
        return OperationResult(
            True,
            "invoice-archived",
            "Rechnung archiviert",
            destination="Assistent/Rechnungen",
            path="Assistent/Rechnungen/2026/07/rechnung.pdf",
        )


class MailQuarantineSourceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        source = Path(__file__).parents[1] / "mail_agent/config.example.toml"
        text = source.read_text(encoding="utf-8")
        text = text.replace("mail_agent/data/", str(self.root / "data") + "/")
        text = text.replace(
            'rules_file = "mail_agent/rules.toml"',
            f'rules_file = "{self.root / "rules.toml"}"',
        )
        text = text.replace(
            'log_file = "mail_agent/data/mail_agent.log"',
            f'log_file = "{self.root / "mail_agent.log"}',
        )
        # Repair the intentionally replaced closing quote above without relying on
        # platform-specific path escaping.
        text = text.replace(f'log_file = "{self.root / "mail_agent.log"}\n', f'log_file = "{self.root / "mail_agent.log"}"\n')
        self.config_path = self.root / "config.toml"
        self.config_path.write_text(text, encoding="utf-8")
        (self.root / "rules.toml").write_text(
            "[spam]\naddresses=[]\ndomains=[]\nsender_names=[]\nsubject_phrases=[]\n"
            "[important]\naddresses=[]\ndomains=[]\n"
            "[routine]\naddresses=[]\ndomains=[]\n",
            encoding="utf-8",
        )
        self.config = load_config(self.config_path)
        self.storage = Storage(self.config.runtime.database)

    def tearDown(self) -> None:
        self.storage.close()
        self.temp.cleanup()

    def _agent(self, *, dry_run: bool) -> MailAgent:
        agent = object.__new__(MailAgent)
        agent.config = self.config
        agent.dry_run = dry_run
        agent.storage = self.storage
        agent.invoices = NoInvoice()
        return agent

    @staticmethod
    def _message(stable_key: str = "mid:quarantine") -> ParsedMessage:
        return ParsedMessage(
            stable_key=stable_key,
            mailbox_id="7",
            source_folder="Spam",
            raw=b"From: spam@example.test\nSubject: Test\n\nBody",
            subject="Test",
            sender_addr="spam@example.test",
        )

    def test_config_contains_primary_and_provider_quarantine(self) -> None:
        self.assertEqual(self.config.mailbox.source_folder, "INBOX")
        self.assertEqual(self.config.mailbox.quarantine_folders, ["Spam"])
        self.assertEqual(self.config.mailbox.all_source_folders(), ["INBOX", "Spam"])
        self.assertTrue(self.config.mailbox.quarantine_rescue_only)

    def test_each_batch_reserves_work_for_quarantine_before_inbox(self) -> None:
        agent = self._agent(dry_run=True)
        calls: list[tuple[str, int]] = []
        agent._process_feedback = lambda summary, limit: None
        agent._available_quarantine_folders = lambda: ["Spam"]

        def process_quarantine(folder, summary, limit):
            calls.append((folder, limit))
            summary.processed += limit

        def process_primary(folder, summary, limit):
            calls.append((folder, limit))
            summary.processed += limit

        agent._process_quarantine_folder = process_quarantine
        agent._process_folder = process_primary
        summary = agent._process_batch(20)
        self.assertEqual(calls, [("Spam", 4), ("INBOX", 16)])
        self.assertEqual(summary.processed, 20)

    def test_obvious_spam_stays_in_provider_folder_and_becomes_final(self) -> None:
        agent = self._agent(dry_run=False)
        message = self._message()
        classification = Classification("spam", 0.99, 1, False, "Spam")
        result = agent._route_quarantine(message, classification, "Spam")
        self.assertEqual(result.status, "quarantine-kept")
        self.assertEqual(result.destination, "Spam")
        row = self.storage.get_message(message.stable_key)
        self.assertIsNotNone(row)
        self.assertEqual(row["status"], "quarantine-reviewed")
        self.assertTrue(self.storage.is_final(message.stable_key))

    def test_routine_invoice_is_rescued_in_dry_run(self) -> None:
        agent = self._agent(dry_run=True)
        agent.invoices = ArchivedInvoice()
        message = self._message("mid:invoice-quarantine")
        classification = Classification("routine", 0.99, 3, False, "Rechnung")
        result = agent._route_quarantine(message, classification, "Spam")
        self.assertEqual(result.status, "dry-run-quarantine-invoice")
        self.assertEqual(result.destination, self.config.folders.routine)
        self.assertIn("Assistent/Rechnungen", result.path)

    def test_relevant_mail_uses_normal_rescue_route(self) -> None:
        agent = self._agent(dry_run=True)
        expected = OperationResult(True, "dry-run", destination=self.config.folders.review)
        calls: list[tuple[str, str]] = []

        def route(message, classification, source):
            calls.append((classification.category, source))
            return expected

        agent._route = route
        result = agent._route_quarantine(
            self._message("mid:relevant-quarantine"),
            Classification("relevant", 0.98, 8, True, "Wichtig"),
            "Spam",
        )
        self.assertIs(result, expected)
        self.assertEqual(calls, [("relevant", "Spam")])

    def test_spam_review_is_visible_in_tool_registry(self) -> None:
        settings = ToolSettings(path=self.root / "tools.toml", mail=MailToolSettings())
        tools = {tool.id: tool for tool in build_tool_registry(settings)}
        self.assertIn("mail.spam-review", tools)
        self.assertEqual(tools["mail.spam-review"].approval, "quarantine-rescue-policy")
        self.assertIn("mail.sources.configure", tools)
        self.assertEqual(tools["mail.sources.configure"].approval, "safe-settings")

    def test_source_setup_is_idempotent_and_validated(self) -> None:
        first = configure_mail_sources(
            config_path=self.config_path,
            primary="INBOX",
            quarantine_folders=("Spam", "Junk"),
            max_per_run=12,
        )
        second = configure_mail_sources(
            config_path=self.config_path,
            primary="INBOX",
            quarantine_folders=("Spam", "Junk"),
            max_per_run=12,
        )
        self.assertEqual(first["quarantine_folders"], ["Spam", "Junk"])
        self.assertEqual(first["quarantine_max_per_run"], 12)
        self.assertEqual(second["backup"], "")
        loaded = load_config(self.config_path)
        self.assertEqual(loaded.mailbox.all_source_folders(), ["INBOX", "Spam", "Junk"])


if __name__ == "__main__":
    unittest.main()
