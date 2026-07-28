from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


class ContainerMigrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(__file__).resolve().parents[1]
        self.script = self.root / "docker/scripts/migrate-container-state.py"

    def test_rewrites_active_paths_and_migrates_himalaya_secrets(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            state = root / "state"
            config = root / "config"
            secrets = root / "secrets"
            workspace = state / "workspace"
            (workspace / "personal_assistant").mkdir(parents=True)
            (workspace / "mail_agent").mkdir(parents=True)
            (config / "himalaya").mkdir(parents=True)
            secrets.mkdir()

            old = "/home/jan/.openclaw/workspace"
            new = "/home/node/.openclaw/workspace"
            (state / "openclaw.json").write_text(
                json.dumps({"agents": {"defaults": {"workspace": old}}}),
                encoding="utf-8",
            )
            (workspace / "personal_assistant/tools.toml").write_text(
                f'outbox = "{old}/personal_assistant/data/workspace_outbox"\n'
                f'database = "{old}/personal_assistant/data/orders.sqlite3"\n'
                f'temp_dir = "{old}/personal_assistant/data/antivirus_tmp"\n',
                encoding="utf-8",
            )
            (workspace / "mail_agent/config.toml").write_text(
                "[calendar]\nenabled = true\n",
                encoding="utf-8",
            )
            (config / "mail-agent.env").write_text(
                "NEXTCLOUD_URL=https://cloud.example.invalid\n"
                "NEXTCLOUD_USER=openclaw\n"
                "NEXTCLOUD_TOKEN=test-token\n",
                encoding="utf-8",
            )
            (config / "himalaya/config.toml").write_text(
                '[accounts.gmx]\n'
                'backend.auth.command = "secret-tool lookup account gmx service himalaya-imap"\n'
                'message.send.backend.auth.command = "secret-tool lookup account gmx service himalaya-smtp"\n',
                encoding="utf-8",
            )

            fake_bin = root / "bin"
            fake_bin.mkdir()
            secret_tool = fake_bin / "secret-tool"
            secret_tool.write_text(
                "#!/bin/sh\n"
                'case "$*" in\n'
                '  *himalaya-imap*) printf "imap-password" ;;\n'
                '  *himalaya-smtp*) printf "smtp-password" ;;\n'
                '  *) exit 2 ;;\n'
                "esac\n",
                encoding="utf-8",
            )
            secret_tool.chmod(0o700)
            environment = os.environ.copy()
            environment["PATH"] = f"{fake_bin}:{environment.get('PATH', '')}"

            command = [
                sys.executable,
                str(self.script),
                "--state-dir",
                str(state),
                "--config-dir",
                str(config),
                "--secrets-dir",
                str(secrets),
                "--source-workspace",
                old,
                "--target-workspace",
                new,
                "--enable-nextcloud-if-configured",
            ]
            result = subprocess.run(command, env=environment, text=True, capture_output=True, check=True)
            report = json.loads(result.stdout)
            self.assertTrue(report["ok"])
            self.assertTrue(report["nextcloud_section_added"])
            self.assertEqual(report["himalaya_secrets"], ["imap", "smtp"])

            active = json.loads((state / "openclaw.json").read_text(encoding="utf-8"))
            self.assertEqual(active["agents"]["defaults"]["workspace"], new)
            tools = (workspace / "personal_assistant/tools.toml").read_text(encoding="utf-8")
            self.assertNotIn(old, tools)
            self.assertEqual(tools.count(new), 3)

            mail_config = (workspace / "mail_agent/config.toml").read_text(encoding="utf-8")
            self.assertIn("[nextcloud]", mail_config)
            self.assertIn("enabled = true", mail_config)

            himalaya = (config / "himalaya/config.toml").read_text(encoding="utf-8")
            self.assertNotIn("secret-tool", himalaya)
            self.assertIn("cat /run/openclaw-secrets/himalaya-imap-password", himalaya)
            self.assertIn("cat /run/openclaw-secrets/himalaya-smtp-password", himalaya)
            self.assertEqual((secrets / "himalaya-imap-password").read_text(encoding="utf-8"), "imap-password\n")
            self.assertEqual((secrets / "himalaya-smtp-password").read_text(encoding="utf-8"), "smtp-password\n")
            self.assertEqual((secrets / "himalaya-imap-password").stat().st_mode & 0o777, 0o600)

            second = subprocess.run(command, env=environment, text=True, capture_output=True, check=True)
            second_report = json.loads(second.stdout)
            self.assertEqual(second_report["path_changes"], [])
            self.assertEqual(second_report["himalaya_secrets"], [])
            self.assertFalse(second_report["nextcloud_section_added"])

    def test_does_not_enable_nextcloud_without_complete_credentials(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            state = root / "state"
            config = root / "config"
            secrets = root / "secrets"
            (state / "workspace/mail_agent").mkdir(parents=True)
            config.mkdir()
            secrets.mkdir()
            mail_config = state / "workspace/mail_agent/config.toml"
            mail_config.write_text("[calendar]\nenabled = true\n", encoding="utf-8")
            (config / "mail-agent.env").write_text("NEXTCLOUD_URL=https://example.invalid\n", encoding="utf-8")

            subprocess.run(
                [
                    sys.executable,
                    str(self.script),
                    "--state-dir",
                    str(state),
                    "--config-dir",
                    str(config),
                    "--secrets-dir",
                    str(secrets),
                    "--source-workspace",
                    "/home/jan/.openclaw/workspace",
                    "--target-workspace",
                    "/home/node/.openclaw/workspace",
                    "--enable-nextcloud-if-configured",
                ],
                text=True,
                capture_output=True,
                check=True,
            )
            self.assertNotIn("[nextcloud]", mail_config.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
