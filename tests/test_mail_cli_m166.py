from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from mail_agent.cli import main


def runtime_config(tmp_path: Path) -> SimpleNamespace:
    return SimpleNamespace(
        runtime=SimpleNamespace(
            database=tmp_path / "mail.sqlite3",
            log_file=tmp_path / "mail.log",
        )
    )


def test_help_remains_available_when_configuration_is_broken(capsys) -> None:
    with (
        patch("mail_agent.cli.load_config", side_effect=ValueError("broken fixture")),
        patch("mail_agent.cli.extended_help", return_value="stable help\n"),
    ):
        code = main(["help", "config"])
    captured = capsys.readouterr()
    assert code == 0
    assert captured.out == "stable help\n"
    assert "broken fixture" in captured.err


def test_environment_parse_failure_stops_before_configuration_load(capsys) -> None:
    with (
        patch("mail_agent.cli.load_env_file", side_effect=ValueError("invalid secret file")),
        patch("mail_agent.cli.load_config") as load_config,
    ):
        code = main(["status"])
    assert code == 2
    assert "invalid secret file" in capsys.readouterr().err
    load_config.assert_not_called()


def test_configuration_failure_is_typed_and_does_not_construct_agent(capsys) -> None:
    with (
        patch("mail_agent.cli.load_env_file"),
        patch("mail_agent.cli.load_config", side_effect=RuntimeError("bad config")),
        patch("mail_agent.cli.MailAgent") as agent,
    ):
        code = main(["status"])
    assert code == 2
    assert "Konfigurationsfehler: bad config" in capsys.readouterr().err
    agent.assert_not_called()


def test_force_cannot_bypass_gate_without_interactive_terminal(tmp_path: Path, capsys) -> None:
    with (
        patch("mail_agent.cli.load_env_file"),
        patch("mail_agent.cli.load_config", return_value=runtime_config(tmp_path)),
        patch("mail_agent.cli._configure_logging"),
        patch("mail_agent.cli.sys.stdin.isatty", return_value=False),
        patch.dict("os.environ", {"MAIL_AGENT_ALLOW_FORCE": "YES"}),
        patch("mail_agent.cli.MailAgent") as agent,
    ):
        code = main(["run", "--force"])
    assert code == 4
    assert "nur interaktiv" in capsys.readouterr().err
    agent.assert_not_called()


def test_performance_path_is_read_only_and_does_not_construct_agent(
    tmp_path: Path, capsys
) -> None:
    records = [{"operation": "fixture", "total_ms": 1.0}]
    with (
        patch("mail_agent.cli.load_env_file"),
        patch("mail_agent.cli.load_config", return_value=runtime_config(tmp_path)),
        patch("mail_agent.cli._configure_logging"),
        patch("mail_agent.cli.read_recent_performance", return_value=records),
        patch("mail_agent.cli.summarize_performance", return_value={"ok": True, "runs": 1}),
        patch("mail_agent.cli.MailAgent") as agent,
    ):
        code = main(["performance", "--limit", "1"])
    assert code == 0
    assert json.loads(capsys.readouterr().out) == {"ok": True, "runs": 1}
    agent.assert_not_called()
