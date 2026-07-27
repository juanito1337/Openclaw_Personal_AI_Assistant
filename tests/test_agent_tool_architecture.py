from __future__ import annotations

import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from datetime import datetime, timedelta
from pathlib import Path

from mail_agent.assistant_bridge import PersonalAssistantActionBridge
from mail_agent.calendar import CalendarManager
from mail_agent.command import CommandRunner
from mail_agent.config import load_config
from mail_agent.models import CalendarEvent, Classification, OperationResult, ParsedMessage
from mail_agent.storage import Storage
from personal_assistant.models import Resource
from personal_assistant.service import _replace_discovered_nextcloud_resources
from personal_assistant.tool_registry import build_tool_registry
from personal_assistant.tool_settings import (
    CalendarMailToolSettings,
    InvoiceToolSettings,
    MailToolSettings,
    ToolSettings,
)


class FakeBridge:
    def __init__(self) -> None:
        self.calls: list[dict[str, str]] = []

    def create_calendar_event(self, **kwargs) -> OperationResult:
        self.calls.append(kwargs)
        return OperationResult(True, "created", destination=str(kwargs["resource_id"]))


class AgentToolArchitectureTests(unittest.TestCase):
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
            f'log_file = "{self.root / "mail_agent.log"}"',
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

    def test_mail_is_exposed_as_assistant_tool(self) -> None:
        settings = ToolSettings(
            path=self.root / "tools.toml",
            mail=MailToolSettings(
                invoices=InvoiceToolSettings(enabled=True),
                calendar_mail=CalendarMailToolSettings(
                    enabled=True,
                    sender_addresses=("owner@example.test",),
                    calendar_resource_id="calendar-personal",
                ),
            ),
        )
        tools = {item.id: item for item in build_tool_registry(settings)}
        self.assertIn("mail.status", tools)
        self.assertIn("mail.run", tools)
        self.assertIn("mail.invoice-archive", tools)
        self.assertIn("mail.calendar-command", tools)
        self.assertEqual(tools["mail.invoice-archive"].approval, "automatic-create-only")

    def test_owner_command_mail_uses_personal_assistant_action_bridge(self) -> None:
        bridge = FakeBridge()
        settings = CalendarMailToolSettings(
            enabled=True,
            subject_prefix="[ASSISTENT TERMIN]",
            sender_addresses=("owner@example.test",),
            calendar_resource_id="calendar-personal",
        )
        manager = CalendarManager(
            self.config,
            self.storage,
            CommandRunner(),
            assistant_bridge=bridge,
            command_settings=settings,
        )
        start = (datetime.now().astimezone() + timedelta(days=2)).replace(microsecond=0)
        message = ParsedMessage(
            stable_key="mid:owner-command",
            mailbox_id="1",
            source_folder="INBOX",
            raw=b"",
            subject="[ASSISTENT TERMIN] Zahnarzt",
            sender_addr="owner@example.test",
            body_text="Bitte am Freitag um 10 Uhr eintragen.",
        )
        classification = Classification(
            "appointment",
            0.99,
            9,
            False,
            "Expliziter Terminbefehl",
            calendar_event=CalendarEvent(
                title="Zahnarzt",
                start=start.isoformat(),
                end=(start + timedelta(hours=1)).isoformat(),
                timezone=str(start.tzinfo),
                confidence=0.99,
            ),
        )
        result = manager.process_command_mail(message, classification)
        self.assertIsNotNone(result)
        self.assertEqual(result.status, "created")
        self.assertEqual(len(bridge.calls), 1)
        self.assertEqual(bridge.calls[0]["resource_id"], "calendar-personal")

    def test_untrusted_command_sender_is_rejected(self) -> None:
        bridge = FakeBridge()
        manager = CalendarManager(
            self.config,
            self.storage,
            CommandRunner(),
            assistant_bridge=bridge,
            command_settings=CalendarMailToolSettings(
                enabled=True,
                sender_addresses=("owner@example.test",),
                calendar_resource_id="calendar-personal",
            ),
        )
        message = ParsedMessage(
            stable_key="mid:attacker",
            mailbox_id="2",
            source_folder="INBOX",
            raw=b"",
            subject="[ASSISTENT TERMIN] Unerlaubt",
            sender_addr="attacker@example.test",
        )
        classification = Classification("appointment", 0.99, 9, False, "Termin")
        result = manager.process_command_mail(message, classification)
        self.assertIsNotNone(result)
        self.assertFalse(result.ok)
        self.assertEqual(result.status, "calendar-command-sender-rejected")
        self.assertEqual(bridge.calls, [])


    def test_invoice_bridge_uses_create_only_actionplan(self) -> None:
        payload_root = self.root / "assistant-data"
        calls: dict[str, object] = {}

        class Actions:
            def plan(self, action_type, resource_id, payload, idempotency_key=""):
                calls["plan"] = (action_type, resource_id, payload, idempotency_key)
                return SimpleNamespace(id="a1", status="approved")

            @staticmethod
            def execute(action_id):
                calls["execute"] = action_id
                return SimpleNamespace(status="completed", error="")

        fake = SimpleNamespace(
            config=SimpleNamespace(runtime=SimpleNamespace(database=payload_root / "assistant.sqlite3")),
            actions=Actions(),
            close=lambda: calls.setdefault("closed", True),
        )
        bridge = PersonalAssistantActionBridge()
        message = ParsedMessage(
            stable_key="mid:invoice-bridge",
            mailbox_id="1",
            source_folder="INBOX",
            raw=b"",
            subject="Rechnung",
        )
        with patch.object(bridge, "_open", return_value=fake):
            result = bridge.archive_invoice(
                message=message,
                attachment_hash="a" * 64,
                data=b"%PDF-1.7 test",
                remote_path="Assistent/Rechnungen/2026/07/test.pdf",
                resource_id="nextcloud-files-main",
            )
        self.assertEqual(result.status, "invoice-archived")
        action_type, resource_id, payload, key = calls["plan"]
        self.assertEqual(action_type, "files.create")
        self.assertEqual(resource_id, "nextcloud-files-main")
        self.assertFalse(payload["overwrite"])
        self.assertTrue(str(key).startswith("invoice-upload:"))
        self.assertEqual(calls["execute"], "a1")

    def test_calendar_bridge_uses_trusted_command_approval(self) -> None:
        calls: dict[str, object] = {}

        class Actions:
            def plan(self, action_type, resource_id, payload, idempotency_key=""):
                calls["plan"] = (action_type, resource_id, payload, idempotency_key)
                return SimpleNamespace(id="c1", status="proposed")

            def approve_trusted_command(self, action_id, *, actor, evidence):
                calls["approve"] = (action_id, actor, evidence)
                return SimpleNamespace(id=action_id, status="approved")

            @staticmethod
            def execute(action_id):
                calls["execute"] = action_id
                return SimpleNamespace(status="completed", error="")

        fake = SimpleNamespace(actions=Actions(), close=lambda: None)
        bridge = PersonalAssistantActionBridge()
        message = ParsedMessage(
            stable_key="mid:calendar-bridge",
            mailbox_id="1",
            source_folder="INBOX",
            raw=b"",
            subject="[ASSISTENT TERMIN] Test",
        )
        with patch.object(bridge, "_open", return_value=fake):
            result = bridge.create_calendar_event(
                message=message,
                resource_id="calendar-personal",
                ics="BEGIN:VCALENDAR\r\nEND:VCALENDAR\r\n",
                uid="uid-1",
                fingerprint="f" * 64,
                sender="owner@example.test",
            )
        self.assertEqual(result.status, "created")
        self.assertEqual(calls["plan"][0], "calendar.create")
        self.assertEqual(calls["approve"][0], "c1")
        self.assertEqual(calls["execute"], "c1")

    def test_discovery_preserves_explicit_calendar_create_permission(self) -> None:
        existing = [
            Resource(
                id="calendar-personal",
                kind="calendar",
                connector="nextcloud",
                enabled=True,
                remote_id="/old/",
                permissions=("read", "create"),
                metadata={"name": "Personal"},
            )
        ]
        discovered = [
            Resource(
                id="calendar-personal",
                kind="calendar",
                connector="nextcloud",
                enabled=True,
                remote_id="/new/",
                permissions=("read",),
                metadata={"name": "Personal"},
            )
        ]
        merged = _replace_discovered_nextcloud_resources(
            existing,
            discovered,
            instance_resource_id="nextcloud-main",
        )
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0].remote_id, "/new/")
        self.assertEqual(merged[0].permissions, ("read", "create"))


if __name__ == "__main__":
    unittest.main()
