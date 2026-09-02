from __future__ import annotations

import json
import os
import sqlite3
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
                "[accounts.gmx]\n"
                'backend.auth.command = "secret-tool lookup account gmx service himalaya-imap"\n'
                "message.send.backend.auth.command = "
                '"secret-tool lookup account gmx service himalaya-smtp"\n',
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
                "  *) exit 2 ;;\n"
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
            self.assertEqual(
                (secrets / "himalaya-imap-password").read_text(encoding="utf-8"), "imap-password\n"
            )
            self.assertEqual(
                (secrets / "himalaya-smtp-password").read_text(encoding="utf-8"), "smtp-password\n"
            )
            self.assertEqual((secrets / "himalaya-imap-password").stat().st_mode & 0o777, 0o600)

            second = subprocess.run(command, env=environment, text=True, capture_output=True, check=True)
            second_report = json.loads(second.stdout)
            self.assertEqual(second_report["path_changes"], [])
            self.assertEqual(second_report["himalaya_secrets"], [])
            self.assertFalse(second_report["nextcloud_section_added"])

    def test_moves_managed_plugins_into_immutable_image_contract(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            state = root / "state"
            config = root / "config"
            secrets = root / "secrets"
            (state / "state").mkdir(parents=True)
            config.mkdir()
            secrets.mkdir()

            old_root = "/home/jan/.openclaw"
            (state / "openclaw.json").write_text(
                json.dumps(
                    {
                        "agents": {"defaults": {"workspace": f"{old_root}/workspace"}},
                        "plugins": {"load": {"paths": [f"{old_root}/npm/projects/old-plugin"]}},
                    }
                ),
                encoding="utf-8",
            )
            projects = {
                "signal": "@openclaw/signal",
                "brave": "@openclaw/brave-plugin",
            }
            install_paths: dict[str, str] = {}
            for project, package in projects.items():
                package_path = state / "npm/projects" / project / "node_modules" / package
                package_path.mkdir(parents=True)
                (package_path / "package.json").write_text(
                    json.dumps({"name": package}),
                    encoding="utf-8",
                )
                install_paths[project] = str(package_path).replace(str(state), old_root, 1)

            database = state / "state/openclaw.sqlite"
            connection = sqlite3.connect(database)
            connection.execute(
                "CREATE TABLE installed_plugin_index ("
                "index_key TEXT PRIMARY KEY, install_records_json TEXT NOT NULL, "
                "plugins_json TEXT NOT NULL, diagnostics_json TEXT NOT NULL)"
            )
            connection.execute(
                "INSERT INTO installed_plugin_index VALUES (?, ?, ?, ?)",
                (
                    "default",
                    json.dumps(
                        {
                            "signal": {
                                "installPath": install_paths["signal"],
                                "resolvedName": "@openclaw/signal",
                            },
                            "brave": {
                                "installPath": install_paths["brave"],
                                "resolvedName": "@openclaw/brave-plugin",
                            },
                        }
                    ),
                    "[]",
                    "{}",
                ),
            )
            connection.commit()
            connection.close()

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
                f"{old_root}/workspace",
                "--target-workspace",
                "/home/node/.openclaw/workspace",
            ]
            result = subprocess.run(command, text=True, capture_output=True, check=True)
            report = json.loads(result.stdout)
            self.assertEqual(report["managed_plugin_state"]["registry_rows_changed"], 1)
            self.assertEqual(report["managed_plugin_state"]["managed_records_checked"], 2)
            self.assertEqual(report["managed_plugin_state"]["managed_records_changed"], 2)
            self.assertEqual(report["managed_plugin_state"]["payload_projects_removed"], 2)

            connection = sqlite3.connect(database)
            raw_records = connection.execute(
                "SELECT install_records_json FROM installed_plugin_index"
            ).fetchone()
            self.assertIsNotNone(raw_records)
            assert raw_records is not None
            records = json.loads(raw_records[0])
            self.assertEqual(
                records["brave"]["installPath"],
                "/opt/openclaw-plugins/node_modules/@openclaw/brave-plugin",
            )
            self.assertEqual(records["brave"]["resolvedVersion"], "2026.7.1")
            self.assertEqual(
                records["signal"]["installPath"],
                "/opt/openclaw-plugins/node_modules/@openclaw/signal",
            )
            self.assertEqual(records["signal"]["resolvedVersion"], "2026.7.1")
            self.assertEqual(connection.execute("PRAGMA quick_check").fetchone(), ("ok",))
            connection.close()
            self.assertFalse((state / "npm/projects").exists())
            migrated = json.loads((state / "openclaw.json").read_text(encoding="utf-8"))
            self.assertEqual(
                migrated["plugins"]["load"]["paths"],
                [
                    "/opt/openclaw-plugins/node_modules/@openclaw/brave-plugin",
                    "/opt/openclaw-plugins/node_modules/@openclaw/signal",
                    "/opt/openclaw-plugins/personal-assistant-tools",
                ],
            )
            self.assertEqual(
                migrated["plugins"]["entries"]["personal-assistant-tools"],
                {
                    "enabled": True,
                    "hooks": {
                        "allowConversationAccess": True,
                        "allowPromptInjection": True,
                    },
                },
            )
            self.assertEqual(
                migrated["tools"]["alsoAllow"],
                ["personal-assistant-tools"],
            )

            second = subprocess.run(command, text=True, capture_output=True, check=True)
            second_report = json.loads(second.stdout)
            self.assertEqual(
                second_report["managed_plugin_state"]["registry_rows_changed"],
                0,
            )
            self.assertEqual(
                second_report["managed_plugin_state"]["managed_records_changed"],
                0,
            )
            self.assertFalse(second_report["immutable_plugin_config"]["changed"])

    def test_rejects_plugin_missing_from_immutable_image_contract(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            state = root / "state"
            config = root / "config"
            secrets = root / "secrets"
            (state / "state").mkdir(parents=True)
            config.mkdir()
            secrets.mkdir()
            database = state / "state/openclaw.sqlite"
            old_root = "/home/jan/.openclaw"
            (state / "openclaw.json").write_text("{}\n", encoding="utf-8")
            package_path = state / "npm/projects/calendar/node_modules/@third-party/calendar"
            package_path.mkdir(parents=True)
            (package_path / "package.json").write_text(
                json.dumps({"name": "@third-party/calendar"}),
                encoding="utf-8",
            )
            connection = sqlite3.connect(database)
            connection.execute(
                "CREATE TABLE installed_plugin_index ("
                "index_key TEXT PRIMARY KEY, install_records_json TEXT NOT NULL, "
                "plugins_json TEXT NOT NULL, diagnostics_json TEXT NOT NULL)"
            )
            connection.execute(
                "INSERT INTO installed_plugin_index VALUES (?, ?, ?, ?)",
                (
                    "default",
                    json.dumps(
                        {
                            "calendar": {
                                "installPath": (
                                    f"{old_root}/npm/projects/calendar/node_modules/@third-party/calendar"
                                ),
                                "resolvedName": "@third-party/calendar",
                            }
                        }
                    ),
                    "[]",
                    "{}",
                ),
            )
            connection.commit()
            connection.close()

            result = subprocess.run(
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
                    f"{old_root}/workspace",
                    "--target-workspace",
                    "/home/node/.openclaw/workspace",
                ],
                text=True,
                capture_output=True,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("nicht im immutable Imagevertrag", result.stderr)
            connection = sqlite3.connect(database)
            raw = connection.execute("SELECT install_records_json FROM installed_plugin_index").fetchone()
            connection.close()
            self.assertIsNotNone(raw)
            assert raw is not None
            self.assertIn(old_root, raw[0])
            self.assertTrue(package_path.is_dir())

    def test_rejects_configured_plugin_path_outside_immutable_image_contract(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            state = root / "state"
            config = root / "config"
            secrets = root / "secrets"
            state.mkdir()
            config.mkdir()
            secrets.mkdir()
            original = {
                "plugins": {
                    "load": {
                        "paths": ["/tmp/untrusted-plugin"],
                    }
                }
            }
            (state / "openclaw.json").write_text(
                json.dumps(original),
                encoding="utf-8",
            )

            result = subprocess.run(
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
                ],
                text=True,
                capture_output=True,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("ausserhalb des immutable Imagevertrags", result.stderr)
            self.assertEqual(
                json.loads((state / "openclaw.json").read_text(encoding="utf-8")),
                original,
            )

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
            (config / "mail-agent.env").write_text(
                "NEXTCLOUD_URL=https://example.invalid\n", encoding="utf-8"
            )

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

    def test_migrates_gateway_auth_and_normalizes_mail_ollama_proxy(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            state = root / "state"
            config = root / "config"
            secrets = root / "secrets"
            workspace = state / "workspace"
            (workspace / "mail_agent").mkdir(parents=True)
            (workspace / "personal_assistant").mkdir(parents=True)
            (state / "agents/main/agent").mkdir(parents=True)
            config.mkdir()
            secrets.mkdir()

            (state / "openclaw.json").write_text(
                json.dumps(
                    {
                        "gateway": {"mode": "local"},
                        "models": {
                            "providers": {
                                "ollama": {"baseUrl": "http://127.0.0.1:11435"}
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )
            (workspace / "mail_agent/config.toml").write_text(
                '[ollama]\nbase_url = "http://127.0.0.1:11434"\nmodel = "test"\n',
                encoding="utf-8",
            )
            (state / "agents/main/agent/models.json").write_text(
                json.dumps({"providers": {"ollama": {"baseUrl": "http://127.0.0.1:11435"}}}),
                encoding="utf-8",
            )
            (config / "ollama-priority.env").write_text(
                "OLLAMA_PRIORITY_LISTEN_PORT=12435\n",
                encoding="utf-8",
            )
            legacy_environment = root / "legacy-gateway-environment.txt"
            legacy_environment.write_text(
                "PATH=/usr/bin OPENCLAW_GATEWAY_TOKEN=legacy-token\n",
                encoding="utf-8",
            )

            result = subprocess.run(
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
                    "--ensure-gateway-auth",
                    "--normalize-ollama-proxy",
                    "--legacy-gateway-environment-file",
                    str(legacy_environment),
                ],
                text=True,
                capture_output=True,
                check=True,
            )
            report = json.loads(result.stdout)
            self.assertEqual(report["gateway_auth"]["mode"], "token")
            self.assertEqual(report["gateway_auth"]["source"], "systemd")
            self.assertEqual(
                (secrets / "gateway.env").read_text(encoding="utf-8"),
                "OPENCLAW_GATEWAY_TOKEN=legacy-token\n",
            )
            if os.name != "nt":
                self.assertEqual((secrets / "gateway.env").stat().st_mode & 0o777, 0o600)
            migrated = json.loads((state / "openclaw.json").read_text(encoding="utf-8"))
            self.assertEqual(migrated["gateway"]["auth"]["mode"], "token")
            self.assertEqual(
                migrated["models"]["providers"]["ollama"]["baseUrl"],
                "http://ollama-proxy:11435",
            )
            self.assertTrue(report["ollama_proxy"]["gateway_config_changed"])
            self.assertIn(
                'base_url = "http://ollama-proxy:11435"',
                (workspace / "mail_agent/config.toml").read_text(encoding="utf-8"),
            )
            models = json.loads((state / "agents/main/agent/models.json").read_text(encoding="utf-8"))
            self.assertEqual(models["providers"]["ollama"]["baseUrl"], "http://ollama-proxy:11435")

    def test_generates_gateway_token_once_when_legacy_had_no_auth(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            state = root / "state"
            config = root / "config"
            secrets = root / "secrets"
            workspace = state / "workspace"
            (workspace / "mail_agent").mkdir(parents=True)
            config.mkdir()
            secrets.mkdir()

            (state / "openclaw.json").write_text(
                json.dumps({"gateway": {"mode": "local"}}),
                encoding="utf-8",
            )
            (workspace / "mail_agent/config.toml").write_text(
                '[ollama]\nbase_url = "http://127.0.0.1:11434"\n',
                encoding="utf-8",
            )

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
                "/home/jan/.openclaw/workspace",
                "--target-workspace",
                "/home/node/.openclaw/workspace",
                "--ensure-gateway-auth",
            ]
            first = subprocess.run(command, text=True, capture_output=True, check=True)
            first_report = json.loads(first.stdout)
            self.assertEqual(first_report["gateway_auth"]["source"], "neu erzeugt")
            token_file = secrets / "gateway.env"
            first_value = token_file.read_text(encoding="utf-8")
            self.assertRegex(first_value, r"^OPENCLAW_GATEWAY_TOKEN=[0-9a-f]{64}\n$")

            second = subprocess.run(command, text=True, capture_output=True, check=True)
            second_report = json.loads(second.stdout)
            self.assertEqual(
                second_report["gateway_auth"]["source"],
                "bestehendes Container-Secret",
            )
            self.assertEqual(token_file.read_text(encoding="utf-8"), first_value)

    def test_replaces_incompatible_container_token_with_legacy_password(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            state = root / "state"
            config = root / "config"
            secrets = root / "secrets"
            workspace = state / "workspace"
            (workspace / "mail_agent").mkdir(parents=True)
            config.mkdir()
            secrets.mkdir()

            (state / "openclaw.json").write_text(
                json.dumps(
                    {
                        "gateway": {
                            "mode": "local",
                            "auth": {"mode": "password"},
                        }
                    }
                ),
                encoding="utf-8",
            )
            (workspace / "mail_agent/config.toml").write_text(
                '[ollama]\nbase_url = "http://127.0.0.1:11434"\n',
                encoding="utf-8",
            )
            (secrets / "gateway.env").write_text(
                "OPENCLAW_GATEWAY_TOKEN=stale-container-token\n",
                encoding="utf-8",
            )
            legacy_environment = root / "legacy-gateway-environment.txt"
            legacy_environment.write_text(
                "PATH=/usr/bin OPENCLAW_GATEWAY_PASSWORD=legacy-password\n",
                encoding="utf-8",
            )

            result = subprocess.run(
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
                    "--ensure-gateway-auth",
                    "--legacy-gateway-environment-file",
                    str(legacy_environment),
                ],
                text=True,
                capture_output=True,
                check=True,
            )

            report = json.loads(result.stdout)
            self.assertEqual(report["gateway_auth"]["mode"], "password")
            self.assertEqual(report["gateway_auth"]["source"], "systemd")
            self.assertTrue(report["gateway_auth"]["replaced_incompatible_existing"])
            self.assertIn(
                "bestehendes Container-Secret",
                report["gateway_auth"]["ignored_incompatible_sources"],
            )
            self.assertEqual(
                (secrets / "gateway.env").read_text(encoding="utf-8"),
                "OPENCLAW_GATEWAY_PASSWORD=legacy-password\n",
            )

    def test_password_mode_without_password_secret_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            state = root / "state"
            config = root / "config"
            secrets = root / "secrets"
            workspace = state / "workspace"
            (workspace / "mail_agent").mkdir(parents=True)
            config.mkdir()
            secrets.mkdir()
            (state / "openclaw.json").write_text(
                json.dumps(
                    {
                        "gateway": {
                            "mode": "local",
                            "auth": {"mode": "password"},
                        }
                    }
                ),
                encoding="utf-8",
            )
            (workspace / "mail_agent/config.toml").write_text(
                '[ollama]\nbase_url = "http://127.0.0.1:11434"\n',
                encoding="utf-8",
            )
            (secrets / "gateway.env").write_text(
                "OPENCLAW_GATEWAY_TOKEN=stale-container-token\n",
                encoding="utf-8",
            )

            result = subprocess.run(
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
                    "--ensure-gateway-auth",
                ],
                text=True,
                capture_output=True,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("Kein password-Secret", result.stderr)


if __name__ == "__main__":
    unittest.main()
