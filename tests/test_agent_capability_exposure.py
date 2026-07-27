from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from personal_assistant.tool_registry import build_tool_registry
from personal_assistant.tool_settings import (
    DirectCalendarToolSettings,
    DirectContactsToolSettings,
    DirectTasksToolSettings,
    NextcloudToolSettings,
    ToolSettings,
)


class AgentCapabilityExposureTests(unittest.TestCase):
    def test_personal_assistant_skill_matches_current_release_and_capabilities(self) -> None:
        root = Path(__file__).parents[1]
        skill = (root / "skills/personal-assistant/SKILL.md").read_text(encoding="utf-8")
        agents = (root / "AGENTS.md").read_text(encoding="utf-8")

        self.assertIn("version: 3.4.0-r26.4", skill)
        for command in (
            "calendar list --limit 100",
            "calendar search --query",
            "calendar update --uid",
            "tasks list --include-completed",
            "tasks update --uid",
            "contacts update --uid",
        ):
            self.assertIn(command, skill)

        stale_claims = (
            "It may not update, overwrite or delete an existing event",
            "Bearbeiten und Erledigt-Markieren sind in r13 weiterhin nicht freigegeben",
            "update and delete remain prohibited",
        )
        for claim in stale_claims:
            self.assertNotIn(claim, skill)
            self.assertNotIn(claim, agents)
        self.assertIn("Do not describe the calendar integration as create-only", agents)

    def test_registry_exposes_calendar_task_and_contact_read_update_tools(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            settings = ToolSettings(
                path=Path(tmp) / "tools.toml",
                nextcloud=NextcloudToolSettings(
                    calendar=DirectCalendarToolSettings(
                        enabled=True,
                        resource_id="calendar-1",
                        allow_create=True,
                        allow_list=True,
                        allow_update=True,
                    ),
                    tasks=DirectTasksToolSettings(
                        enabled=True,
                        resource_id="tasks-1",
                        allow_create=True,
                        allow_list=True,
                        allow_update=True,
                    ),
                    contacts=DirectContactsToolSettings(
                        enabled=True,
                        resource_id="contacts-1",
                        allow_create=True,
                        allow_list=True,
                        allow_update=True,
                    ),
                ),
            )
            tools = {item.id: item for item in build_tool_registry(settings)}

        expected = {
            "nextcloud.calendar.list",
            "nextcloud.calendar.search",
            "nextcloud.calendar.update",
            "nextcloud.tasks.list",
            "nextcloud.tasks.update",
            "nextcloud.contacts.list",
            "nextcloud.contacts.search",
            "nextcloud.contacts.update",
        }
        self.assertTrue(expected.issubset(tools), expected - set(tools))
        self.assertIn("--allow-update", tools["nextcloud.calendar.configure"].command)
        self.assertIn("--allow-update", tools["nextcloud.tasks.configure"].command)


if __name__ == "__main__":
    unittest.main()
