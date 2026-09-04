from __future__ import annotations

from types import SimpleNamespace

import pytest

from mail_agent.command import CommandResult
from mail_agent.himalaya import HimalayaClient
from mail_agent.models import Envelope
from personal_assistant.adapters.mail import MailMoveService
from personal_assistant.agent_tool_orchestration import build_native_tool_contract, route_intent
from personal_assistant.cli import parser
from personal_assistant.models import Resource
from personal_assistant.policy import PolicyEngine
from personal_assistant.registry import ResourceRegistry
from personal_assistant.storage import AssistantStorage
from personal_assistant.tool_settings import MailMoveToolSettings


class RecentClient:
    def __init__(self) -> None:
        self.folders = ["INBOX", "Agent/Relevant", "Trash", "Sent", "Drafts"]
        self.messages = {
            "INBOX": [
                Envelope("1", "Noch im Eingang", "A", "a@example.invalid", "2026-09-01T08:00:00+00:00")
            ],
            "Agent/Relevant": [
                Envelope("2", "Verschoben neu", "B", "b@example.invalid", "2026-09-03T09:00:00+00:00"),
                Envelope("3", "Verschoben alt", "C", "c@example.invalid", "2026-09-02T09:00:00+00:00"),
            ],
            "Trash": [
                Envelope("4", "Extern verschoben", "D", "d@example.invalid", "2026-09-04T09:00:00+00:00")
            ],
            "Sent": [
                Envelope("5", "Selbst gesendet", "Jan", "jan@example.invalid", "2026-09-05T09:00:00+00:00")
            ],
            "Drafts": [Envelope("6", "Entwurf", "Jan", "jan@example.invalid", "2026-09-06T09:00:00+00:00")],
        }
        self.errors: set[str] = set()
        self.calls: list[tuple[str, int]] = []

    def list_folders(self):
        return list(self.folders), ""

    def list_recent_envelopes(self, folder: str, *, limit: int = 20):
        self.calls.append((folder, limit))
        if folder in self.errors:
            return [], f"synthetic failure: {folder}"
        return list(self.messages.get(folder, []))[:limit], ""


def service(tmp_path, client: RecentClient) -> MailMoveService:
    registry = ResourceRegistry(tmp_path / "resources.toml")
    registry.resources["mail-agent"] = Resource(
        id="mail-agent",
        kind="tool",
        connector="local",
        permissions=("read", "move", "forward"),
    )
    return MailMoveService(
        MailMoveToolSettings(enabled=True),
        registry,
        PolicyEngine(tmp_path / "policies.toml", registry),
        AssistantStorage(tmp_path / "assistant.sqlite3"),
        client,
    )


def test_recent_reads_current_folders_account_wide_and_excludes_outgoing(tmp_path) -> None:
    client = RecentClient()
    result = service(tmp_path, client).recent_messages(limit=3)

    assert result["ok"] is True
    assert result["read_only"] is True
    assert result["scope"] == "account-recent-incoming"
    assert result["complete"] is True
    assert result["results_may_be_truncated"] is True
    assert [item["mailbox_id"] for item in result["messages"]] == ["4", "2", "3"]
    assert [item["folder"] for item in result["messages"]] == [
        "Trash",
        "Agent/Relevant",
        "Agent/Relevant",
    ]
    assert result["excluded_folders"] == ["Sent", "Drafts"]
    assert ("INBOX", 3) in client.calls
    assert not any(folder in {"Sent", "Drafts"} for folder, _limit in client.calls)
    assert result["writes_imap"] is False


def test_recent_reports_partial_folder_failure_without_hiding_positive_rows(tmp_path) -> None:
    client = RecentClient()
    client.errors.add("Agent/Relevant")

    result = service(tmp_path, client).recent_messages(limit=20)

    assert result["ok"] is True
    assert result["complete"] is False
    assert result["folder_errors"] == [
        {"folder": "Agent/Relevant", "error": "synthetic failure: Agent/Relevant"}
    ]
    assert [item["mailbox_id"] for item in result["messages"]] == ["4", "1"]


def test_recent_fails_when_every_incoming_folder_fails(tmp_path) -> None:
    client = RecentClient()
    client.errors.update({"INBOX", "Agent/Relevant", "Trash"})

    with pytest.raises(RuntimeError, match="in allen Ordnern fehlgeschlagen"):
        service(tmp_path, client).recent_messages(limit=20)


def test_himalaya_recent_query_is_bounded_and_explicitly_newest_first() -> None:
    class Runner:
        def __init__(self) -> None:
            self.commands: list[list[str]] = []

        def run(self, command):
            self.commands.append(list(command))
            return CommandResult(
                list(command),
                0,
                '[{"id":"7","subject":"Neu","date":"2026-09-04T09:00:00+00:00"}]',
                "",
            )

    runner = Runner()
    config = SimpleNamespace(mailbox=SimpleNamespace(himalaya_binary="himalaya", account="", page_size=100))
    rows, error = HimalayaClient(config, runner).list_recent_envelopes("Archiv", limit=20)

    assert error == ""
    assert rows[0].mailbox_id == "7"
    assert runner.commands[0][-4:] == ["order", "by", "date", "desc"]
    assert runner.commands[0][runner.commands[0].index("--page-size") + 1] == "20"


def test_recent_cli_and_native_contract_have_no_required_arguments() -> None:
    args = parser().parse_args(["mail", "recent", "--limit", "20"])
    assert args.mail_command == "recent"
    assert args.limit == 20

    contract = build_native_tool_contract()
    operation = next(item for item in contract["operations"] if item["tool_id"] == "mail.recent")
    assert operation["argument_schema"]["required"] == []
    group = next(item for item in contract["native_tools"] if item["name"] == "personal_assistant_mail_read")
    assert "mail.recent" in group["operations"]
    assert "INBOX" in group["description"]
    assert "Verschiebungen" in group["description"]
    assert (
        "mail.recent"
        in route_intent("Welche 20 E-Mails sind zuletzt eingegangen?")["routes"][0]["operations"]
    )
