from __future__ import annotations

import os
import unittest
from pathlib import Path
from unittest.mock import patch

from personal_assistant.connectors.nextcloud.tasks import NextcloudTasks
from personal_assistant.models import ActionPlan, PolicyDecision, Resource
from personal_assistant.service import PersonalAssistant
from personal_assistant.tool_registry import build_tool_registry
from personal_assistant.tool_settings import DirectTasksToolSettings, ToolSettings

RESOURCE_ID = "nextcloud-calendar-test"


def plan(status: str, payload: dict, *, action_id: str = "action-1") -> ActionPlan:
    return ActionPlan(
        id=action_id,
        idempotency_key="key",
        action_type="tasks.create",
        resource_id=RESOURCE_ID,
        payload=payload,
        status=status,
        requires_approval=True,
        created_at="2026-07-22T00:00:00+00:00",
        updated_at="2026-07-22T00:00:00+00:00",
        error="",
    )


class FakeRegistry:
    def __init__(self) -> None:
        self.resource = Resource(
            id=RESOURCE_ID,
            kind="calendar",
            connector="nextcloud",
            enabled=True,
            remote_id="/remote.php/dav/calendars/openclaw/personal/",
            permissions=("read", "create"),
            metadata={"href": "/remote.php/dav/calendars/openclaw/personal/", "name": "Personal"},
        )

    def get(self, resource_id: str) -> Resource:
        assert resource_id == RESOURCE_ID
        return self.resource


class FakePolicy:
    def decide(self, resource_id: str, action: str, payload: dict) -> PolicyDecision:
        assert resource_id == RESOURCE_ID
        assert action in {"tasks.read", "tasks.create"}
        return PolicyDecision(True, action == "tasks.create", "ok")


class FakeActions:
    def __init__(self) -> None:
        self.payload: dict = {}
        self.key = ""

    def plan(self, action_type: str, resource_id: str, payload: dict, idempotency_key: str = "") -> ActionPlan:
        assert action_type == "tasks.create"
        assert resource_id == RESOURCE_ID
        self.payload = payload
        self.key = idempotency_key
        return plan("proposed", payload)

    def approve_configured_tasks_tool(self, action_id: str, *, evidence: dict) -> ActionPlan:
        assert evidence["tool_enabled"] is True
        return plan("approved", self.payload, action_id=action_id)

    def execute_task_create(self, action_id: str):
        return plan("completed", self.payload, action_id=action_id), False


class FakeTasks:
    def supports_vtodo(self, collection) -> bool:
        return True

    def list_tasks(self, collection, *, include_completed: bool, limit: int):
        return [{"uid": "1", "title": "Test", "status": "NEEDS-ACTION", "due": "20260730"}]


class DirectTasksTests(unittest.TestCase):
    def assistant(self) -> PersonalAssistant:
        value = PersonalAssistant.__new__(PersonalAssistant)
        settings = ToolSettings(path=Path("/tmp/tools.toml"))
        settings.nextcloud.tasks = DirectTasksToolSettings(
            enabled=True,
            resource_id=RESOURCE_ID,
            allow_create=True,
            allow_list=True,
            timezone="Europe/Berlin",
            max_future_days=3650,
        )
        value.tool_settings = settings
        value.registry = FakeRegistry()
        value.policy = FakePolicy()
        value.actions = FakeActions()
        value.nextcloud_tasks = FakeTasks()
        return value

    def test_status_and_registry(self) -> None:
        assistant = self.assistant()
        status = assistant.direct_tasks_status(live=True)
        self.assertTrue(status["ok"])
        self.assertTrue(status["supports_vtodo"])
        ids = {item.id for item in build_tool_registry(assistant.tool_settings)}
        self.assertIn("nextcloud.tasks.status", ids)
        self.assertIn("nextcloud.tasks.list", ids)
        self.assertIn("nextcloud.tasks.create", ids)

    def test_status_reports_separate_agent_cli_setup_when_update_disabled(self) -> None:
        assistant = self.assistant()
        assistant.role = "gateway"

        with patch.dict(os.environ, {"OPENCLAW_RUNTIME": "container"}):
            status = assistant.direct_tasks_status(live=True)

        self.assertTrue(status["ok"])
        self.assertFalse(status["update_allowed"])
        self.assertTrue(status["update_setup_required"])
        self.assertTrue(status["workspace_read_only_expected"])
        self.assertFalse(status["configuration_write_available_here"])
        self.assertEqual(status["update_setup"]["container_role"], "agent-cli")
        self.assertTrue(status["update_setup"]["operator_only"])
        self.assertFalse(status["update_setup"]["change_gateway_mounts"])
        self.assertEqual(
            status["update_setup"]["command"],
            "./scripts/assistant.sh setup standard-operations --yes",
        )
        self.assertIn(
            "--resource nextcloud-calendar-test",
            status["update_setup"]["domain_fallback_command"],
        )
        self.assertIn(
            "--allow-update --yes",
            status["update_setup"]["domain_fallback_command"],
        )

    def test_gateway_configuration_fails_before_readonly_workspace_write(self) -> None:
        assistant = self.assistant()
        assistant.role = "gateway"

        with (
            patch.dict(os.environ, {"OPENCLAW_RUNTIME": "container"}),
            self.assertRaisesRegex(PermissionError, "agent-cli-Rolle") as raised,
        ):
            assistant.tasks_configure(resource_id=RESOURCE_ID, allow_update=True)

        detail = str(raised.exception)
        self.assertIn("schreibgeschuetzt", detail)
        self.assertIn("Keine Rechte oder Mounts aendern", detail)

    def test_create_all_day_task(self) -> None:
        assistant = self.assistant()
        result = assistant.task_create(
            title="Rechnung pruefen",
            due="2026-07-30",
            description="Mit Einkauf abstimmen",
            priority=3,
            categories=("Arbeit", "Finanzen"),
        )
        self.assertTrue(result["ok"])
        self.assertFalse(result["duplicate"])
        payload = assistant.actions.payload
        self.assertIn("BEGIN:VTODO", payload["ics"])
        self.assertIn("DUE;VALUE=DATE:20260730", payload["ics"])
        self.assertIn("PRIORITY:3", payload["ics"])
        self.assertIn("CATEGORIES:Arbeit,Finanzen", payload["ics"])
        self.assertTrue(payload["direct_tasks_tool"])
        self.assertTrue(assistant.actions.key.startswith("direct-tasks:"))

    def test_list_open_tasks(self) -> None:
        result = self.assistant().tasks_list(limit=25)
        self.assertTrue(result["ok"])
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["tasks"][0]["title"], "Test")

    def test_vtodo_parser(self) -> None:
        parsed = NextcloudTasks._parse_vtodo(
            "BEGIN:VCALENDAR\r\nBEGIN:VTODO\r\nUID:abc\r\nSUMMARY:Test\\, Aufgabe\r\n"
            "DUE;VALUE=DATE:20260730\r\nSTATUS:NEEDS-ACTION\r\nPRIORITY:5\r\n"
            "CATEGORIES:Arbeit,Privat\r\nEND:VTODO\r\nEND:VCALENDAR\r\n"
        )
        self.assertEqual(parsed["uid"], "abc")
        self.assertEqual(parsed["title"], "Test, Aufgabe")
        self.assertEqual(parsed["due"], "20260730")
        self.assertEqual(parsed["priority"], 5)
        self.assertEqual(parsed["categories"], ["Arbeit", "Privat"])


if __name__ == "__main__":
    unittest.main()
