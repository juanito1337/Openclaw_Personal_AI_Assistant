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
        references = "\n".join(
            path.read_text(encoding="utf-8")
            for path in sorted((root / "skills/personal-assistant/references").glob("*.md"))
        )
        documented = "\n".join((skill, agents, references))
        normalized_agents = " ".join(agents.split())
        normalized_references = " ".join(references.split())

        self.assertIn("Release identity: `3.4.0-r28`", skill)
        for command in (
            "calendar list --limit 100",
            "calendar search --query",
            "calendar update --uid",
            "tasks list --include-completed",
            "tasks update --uid",
            "contacts update --uid",
            "mail search --query",
            "mail index status",
            "mail index doctor",
            "mail compose-draft --to",
            "mail compose-send --draft-id",
            "portfolio import-csv --file",
            "portfolio import-csv --nextcloud-path",
            "portfolio holdings",
            "portfolio mapping suggest --isin",
            "portfolio mapping suggest --query",
            "portfolio watchlist add --isin",
            "portfolio watchlist disable --isin",
            "portfolio quotes get --isin",
            "portfolio quotes refresh --force",
            "portfolio alerts disable --id",
            "jobs status --target all",
        ):
            self.assertIn(command, documented)

        stale_claims = (
            "It may not update, overwrite or delete an existing event",
            "Bearbeiten und Erledigt-Markieren sind in r13 weiterhin nicht freigegeben",
            "update and delete remain prohibited",
        )
        for claim in stale_claims:
            self.assertNotIn(claim, documented)
        self.assertIn("Do not describe the calendar integration as create-only", agents)
        self.assertIn("`mail search` uses the M11.7 auto path", normalized_references)
        self.assertIn("eligible local Hybridindex first", normalized_references)
        self.assertIn("visible server fallback otherwise", normalized_references)
        self.assertIn(
            "`semantic_state`, `fallback_used`, `folder_errors`", normalized_references
        )
        self.assertIn(
            "`live_locator.folder` and `live_locator.mailbox_id`", normalized_references
        )
        self.assertIn("`--expected-subject` to `mail read`", normalized_references)
        self.assertIn("`complete`,", references)
        self.assertIn("`folder_errors` and `results_may_be_truncated`", references)
        self.assertIn("Bei jedem Suchergebnis `complete`", agents)
        self.assertIn("Do not claim that CSV import is unavailable", references)
        self.assertNotIn("read-only portfolio monitor", documented)

        commands = (root / "skills/personal-assistant/references/tool-contract.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("portfolio import-csv --file", commands)
        self.assertIn("portfolio import-csv --nextcloud-path", commands)
        self.assertIn("setup portfolio --provider eodhd --interval-minutes 15", commands)
        self.assertNotIn("--provider twelve-data", documented)
        self.assertIn("15–20 minutes", references)
        self.assertIn("accepts no `--detailed`", references)
        self.assertIn("Do not inspect SQLite directly", references)
        self.assertIn("`entry_price`", references)
        self.assertIn("entry price but no individual purchase date", references)
        self.assertIn("`quote_currency`", references)
        self.assertIn("do not invent `portfolio setup`", normalized_agents)

        settings = ToolSettings(path=Path("tools.toml"))
        portfolio_tools = {
            item.id: item.command
            for item in build_tool_registry(settings)
            if item.id.startswith("portfolio.")
        }
        expected_portfolio_tools = {
            "portfolio.status",
            "portfolio.setup",
            "portfolio.doctor",
            "portfolio.import.pp",
            "portfolio.import.pp.confirm",
            "portfolio.import.csv",
            "portfolio.import.csv.nextcloud",
            "portfolio.import.csv.confirm",
            "portfolio.import.csv.nextcloud.confirm",
            "portfolio.holdings",
            "portfolio.valuation",
            "portfolio.watchlist",
            "portfolio.mapping.suggest",
            "portfolio.mapping.discover",
            "portfolio.watchlist.add",
            "portfolio.watchlist.disable",
            "portfolio.quotes.status",
            "portfolio.quotes.get",
            "portfolio.quotes.refresh",
            "portfolio.quotes.refresh.force",
            "portfolio.analyze",
            "portfolio.research.status",
            "portfolio.research.models",
            "portfolio.research.screen",
            "portfolio.research.analyze",
            "portfolio.research.history",
            "portfolio.philosophy.show",
            "portfolio.philosophy.review",
            "portfolio.philosophy.history",
            "portfolio.philosophy.set",
            "portfolio.philosophy.feedback",
            "portfolio.alerts",
            "portfolio.alerts.add",
            "portfolio.alerts.disable",
            "portfolio.performance",
            "portfolio.job.on",
            "portfolio.job.restart",
            "portfolio.job.off",
        }
        self.assertEqual(set(portfolio_tools), expected_portfolio_tools)
        for command in portfolio_tools.values():
            normalized = command.replace('./scripts/assistant.sh ', '')
            command_prefix = normalized.split(' "<', 1)[0]
            self.assertTrue(
                command_prefix in documented,
                f"Portfolio-Registry-Befehl fehlt in Skill/AGENTS.md: {command}",
            )

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
        calendar_create = tools["nextcloud.calendar.create"]
        self.assertNotIn("--yes", calendar_create.command)
        self.assertIn("kein --yes", calendar_create.description.lower())


if __name__ == "__main__":
    unittest.main()
