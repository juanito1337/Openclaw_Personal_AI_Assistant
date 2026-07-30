from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from personal_assistant.cli import _handle_ollama, _handle_performance, parser


class Completed:
    def __init__(self, returncode: int = 0, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class AgentOperationalToolTests(unittest.TestCase):
    def test_parser_exposes_ollama_and_mail_performance(self) -> None:
        self.assertEqual(parser().parse_args(["ollama", "status"]).ollama_command, "status")
        args = parser().parse_args(["performance", "mail", "--limit", "7"])
        self.assertEqual(args.performance_command, "mail")
        self.assertEqual(args.limit, 7)
        compose = parser().parse_args([
            "mail", "compose-draft",
            "--to", "jonas@example.de",
            "--subject", "Vorstellung",
            "--body", "Hallo Jonas",
        ])
        self.assertEqual(compose.mail_command, "compose-draft")
        self.assertEqual(compose.to, "jonas@example.de")
        send = parser().parse_args(["mail", "compose-send", "--draft-id", "draft-1", "--yes"])
        self.assertTrue(send.yes)
        portfolio = parser().parse_args(
            ["portfolio", "analyze", "--isin", "DE000BASF111"]
        )
        self.assertEqual(portfolio.portfolio_command, "analyze")
        portfolio_job = parser().parse_args(["jobs", "status", "--target", "portfolio"])
        self.assertEqual(portfolio_job.target, "portfolio")
        portfolio_setup = parser().parse_args(
            [
                "setup", "portfolio", "--provider", "twelve-data",
                "--interval-minutes", "15", "--approve-permissions",
            ]
        )
        self.assertEqual(portfolio_setup.interval_minutes, 15)
        self.assertTrue(portfolio_setup.approve_permissions)

    @patch("personal_assistant.cli.subprocess.run")
    def test_ollama_restart_restarts_and_verifies(self, run) -> None:
        healthy = json.dumps({"ok": True, "queue": {"pending": 0}, "stats": {}})
        run.side_effect = [
            Completed(0, "", ""),
            Completed(0, healthy, ""),
            Completed(0, json.dumps({"ok": True, "detail": "Ollama"}), ""),
        ]
        args = parser().parse_args(["ollama", "restart"])
        self.assertEqual(_handle_ollama(args), 0)
        self.assertEqual(run.call_args_list[0].args[0][:4], ["systemctl", "--user", "restart", "ollama-priority-proxy.service"])

    @patch("personal_assistant.cli.subprocess.run")
    def test_performance_mail_delegates_to_mail_interface(self, run) -> None:
        run.return_value = Completed(0, "", "")
        args = parser().parse_args(["performance", "mail", "--limit", "12", "--raw"])
        self.assertEqual(_handle_performance(args), 0)
        command = run.call_args.args[0]
        self.assertIn("mail_agent", command)
        self.assertIn("performance", command)
        self.assertIn("--raw", command)


if __name__ == "__main__":
    unittest.main()
