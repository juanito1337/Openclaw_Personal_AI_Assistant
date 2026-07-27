from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

from mail_agent.config import load_config
from mail_agent.learning import LearningFolderRegistry
from mail_agent.models import Envelope
from mail_agent.parser import parse_eml
from mail_agent.rules import RuleEngine
from mail_agent.storage import Storage
from personal_assistant.tool_registry import build_tool_registry
from personal_assistant.cli import parser as assistant_parser
from mail_agent.cli import build_parser as mail_parser
from personal_assistant.tool_settings import ToolSettings


WORKSPACE = Path(__file__).resolve().parents[1]


def message(*, sender: str, subject: str, message_id: str):
    raw = (
        f"From: Sender <{sender}>\r\n"
        "To: Jan <jan@example.test>\r\n"
        f"Subject: {subject}\r\n"
        f"Message-ID: <{message_id}>\r\n"
        "Content-Type: text/plain; charset=utf-8\r\n\r\n"
        "Automatische Information.\r\n"
    ).encode()
    return parse_eml(raw, Envelope(message_id), "INBOX")


class LearningPatternTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        text = (WORKSPACE / "mail_agent/config.example.toml").read_text(encoding="utf-8")
        text = text.replace("mail_agent/data/", str(self.root / "data") + "/")
        text = text.replace(
            'rules_file = "mail_agent/rules.toml"',
            f'rules_file = "{self.root / "rules.toml"}"',
        )
        text = text.replace(
            'learning_folders_file = "mail_agent/learning_folders.json"',
            f'learning_folders_file = "{self.root / "learning_folders.json"}"',
        )
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

    def test_same_sender_and_normalized_subject_pattern_is_reused(self) -> None:
        corrected = message(
            sender="shop@example.test",
            subject="Interner Statuscode 4711 vom 23.07.2026",
            message_id="one@example.test",
        )
        candidate = message(
            sender="shop@example.test",
            subject="Interner Statuscode 9988 vom 24.07.2026",
            message_id="two@example.test",
        )
        self.storage.record_feedback(corrected, "routine", "Agent/Korrektur-Unwichtig")
        first_result = RuleEngine(self.config.runtime.rules_file, self.storage).evaluate(candidate)
        self.assertIsNone(first_result.forced)
        self.assertTrue(any("Erst eine Nutzerkorrektur" in note for note in (first_result.notes or [])))
        second = message(
            sender="shop@example.test",
            subject="Interner Statuscode 7711 vom 24.07.2026",
            message_id="second-example@example.test",
        )
        self.storage.record_feedback(second, "routine", "Agent/Korrektur-Unwichtig")
        result = RuleEngine(self.config.runtime.rules_file, self.storage).evaluate(candidate)
        self.assertIsNotNone(result.forced)
        self.assertEqual(result.forced.category, "routine")
        self.assertEqual(result.forced.source, "feedback-pattern")


    def test_one_relevant_pattern_correction_protects_later_mail(self) -> None:
        corrected = message(
            sender="bank@example.test",
            subject="Sicherheitswarnung fuer Konto 4711",
            message_id="relevant-one@example.test",
        )
        candidate = message(
            sender="bank@example.test",
            subject="Sicherheitswarnung fuer Konto 9988",
            message_id="relevant-two@example.test",
        )
        self.storage.record_feedback(corrected, "relevant", "Agent/Korrektur-Wichtig")
        result = RuleEngine(self.config.runtime.rules_file, self.storage).evaluate(candidate)
        self.assertTrue(result.prevent_spam)
        self.assertTrue(result.important_sender)

    def test_mixed_sender_is_not_forced_by_sender_only(self) -> None:
        sender = "mixed@example.test"
        self.storage.record_feedback(
            message(sender=sender, subject="Newsletter Juli", message_id="mixed-1@example.test"),
            "routine",
            "Agent/Korrektur-Unwichtig",
        )
        self.storage.record_feedback(
            message(sender=sender, subject="Sicherheitswarnung fuer Ihr Konto", message_id="mixed-2@example.test"),
            "relevant",
            "Agent/Korrektur-Wichtig",
        )
        candidate = message(sender=sender, subject="Neue Rechnung", message_id="mixed-3@example.test")
        result = RuleEngine(self.config.runtime.rules_file, self.storage).evaluate(candidate)
        self.assertIsNone(result.forced)
        self.assertTrue(any("Gemischter Absender" in note for note in (result.notes or [])))
        self.assertTrue(self.storage.sender_feedback_profile(sender)["mixed"])

    def test_conflicting_same_pattern_abstains(self) -> None:
        sender = "conflict@example.test"
        self.storage.record_feedback(
            message(sender=sender, subject="Status 1001", message_id="conflict-1@example.test"),
            "routine",
            "Agent/Korrektur-Unwichtig",
        )
        self.storage.record_feedback(
            message(sender=sender, subject="Status 1002", message_id="conflict-2@example.test"),
            "relevant",
            "Agent/Korrektur-Wichtig",
        )
        candidate = message(sender=sender, subject="Status 1003", message_id="conflict-3@example.test")
        decision = self.storage.pattern_feedback_decision(candidate)
        self.assertTrue(decision["conflict"])
        conflicts = self.storage.pattern_conflicts(limit=10)
        self.assertEqual(len(conflicts), 1)
        self.assertEqual(len(conflicts[0]["conflict_id"]), 16)
        self.assertEqual(len(conflicts[0]["feedback_ids"]), 2)
        self.assertEqual(
            self.storage.pattern_conflicts(limit=10, conflict_id=conflicts[0]["conflict_id"])[0]["conflict_id"],
            conflicts[0]["conflict_id"],
        )
        result = RuleEngine(self.config.runtime.rules_file, self.storage).evaluate(candidate)
        self.assertIsNone(result.forced)
        self.assertTrue(any("widerspruechliche" in note for note in (result.notes or [])))

    def test_dynamic_learning_folder_is_restricted_and_persistent(self) -> None:
        registry = LearningFolderRegistry(self.config)
        item = registry.create(parent="routine", name="Versand", label="shipping")
        self.assertEqual(item.folder, "Agent/Korrektur-Unwichtig/Versand")
        self.assertEqual(item.verdict, "routine")
        self.assertEqual(registry.feedback_mappings(), [(item.folder, "routine", "shipping")])
        self.assertEqual(registry.path.stat().st_mode & 0o777, 0o600)
        with self.assertRaises(ValueError):
            registry.create(parent="routine", name="../Unsafe")
        self.assertTrue(registry.disable(item.folder))
        self.assertEqual(registry.feedback_mappings(), [])

    def test_conflict_id_is_exposed_through_both_clis(self) -> None:
        args = assistant_parser().parse_args([
            "mail", "learning", "conflicts", "--id", "abc123", "--limit", "5"
        ])
        self.assertEqual(args.id, "abc123")
        raw = mail_parser().parse_args(["training", "conflicts", "--id", "abc123", "--limit", "5"])
        self.assertEqual(raw.id, "abc123")

    def test_agent_registry_exposes_learning_tools(self) -> None:
        ids = {tool.id for tool in build_tool_registry(ToolSettings(path=Path("test-tools.toml")))}
        self.assertIn("mail.learning.status", ids)
        self.assertIn("mail.learning.folder-create", ids)
        self.assertIn("mail.learning.conflicts", ids)

    def test_folder_cli_creates_and_disables_mapping_without_deleting_imap_folder(self) -> None:
        fake = self.root / "himalaya"
        log = self.root / "himalaya.log"
        fake.write_text(
            textwrap.dedent(
                f"""#!/bin/sh
set -eu
if [ "$1" = folder ] && [ "$2" = list ]; then
  printf '%s\n' '["INBOX","Agent","Agent/Korrektur-Unwichtig"]'
  exit 0
fi
printf '%s\n' "$*" >> {log}
if [ "$1" = folder ] && {{ [ "$2" = add ] || [ "$2" = create ]; }}; then exit 0; fi
exit 2
"""
            ),
            encoding="utf-8",
        )
        fake.chmod(0o755)
        config_text = self.config_path.read_text(encoding="utf-8")
        config_text = config_text.replace('himalaya_binary = "himalaya"', f'himalaya_binary = "{fake}"')
        self.config_path.write_text(config_text, encoding="utf-8")
        environment = os.environ.copy()
        environment["PYTHONPATH"] = str(WORKSPACE)
        create = subprocess.run(
            [
                sys.executable, "-m", "mail_agent", "--config", str(self.config_path),
                "training", "folder-create", "--parent", "routine",
                "--name", "Versand", "--label", "shipping", "--yes",
            ],
            cwd=WORKSPACE, env=environment, capture_output=True, text=True, check=False,
        )
        self.assertEqual(create.returncode, 0, create.stderr + create.stdout)
        payload = json.loads(create.stdout)
        folder = "Agent/Korrektur-Unwichtig/Versand"
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["folder"]["folder"], folder)
        self.assertIn(f"folder add {folder}", log.read_text(encoding="utf-8"))

        disable = subprocess.run(
            [
                sys.executable, "-m", "mail_agent", "--config", str(self.config_path),
                "training", "folder-disable", "--folder", folder, "--yes",
            ],
            cwd=WORKSPACE, env=environment, capture_output=True, text=True, check=False,
        )
        self.assertEqual(disable.returncode, 0, disable.stderr + disable.stdout)
        disabled = json.loads(disable.stdout)
        self.assertFalse(disabled["imap_folder_deleted"])
        self.assertNotIn("folder delete", log.read_text(encoding="utf-8"))
        registry = LearningFolderRegistry(load_config(self.config_path))
        self.assertEqual(registry.feedback_mappings(), [])


if __name__ == "__main__":
    unittest.main()
