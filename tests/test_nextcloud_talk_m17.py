from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from personal_assistant.nextcloud_talk import (
    PLUGIN_ID,
    PLUGIN_PATH,
    WEBHOOK_PATH,
    build_prepared_policy,
    doctor,
    inspect_secret,
    prepared_channel_config,
    status,
    validate_policy,
)


class NextcloudTalkM17Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(__file__).resolve().parents[1]
        self.script = self.root / "docker/scripts/nextcloud-talk-config.py"
        self.contract = self.root / "docker/openclaw-plugins/contract.json"

    def _fixture(self, folder: str) -> tuple[Path, Path, Path]:
        root = Path(folder)
        config = root / "openclaw.json"
        policy = root / ".nextcloud-talk/policy.json"
        secret = root / "nextcloud-talk-bot-secret"
        config.write_text(
            json.dumps(
                {
                    "gateway": {"mode": "local"},
                    "plugins": {
                        "load": {"paths": [PLUGIN_PATH]},
                        "entries": {"personal-assistant-tools": {"enabled": True}},
                    },
                }
            )
            + "\n",
            encoding="utf-8",
        )
        config.chmod(0o600)
        secret.write_text("", encoding="utf-8")
        secret.chmod(0o600)
        return config, policy, secret

    def _base_command(self, config: Path, policy: Path, secret: Path) -> list[str]:
        return [
            sys.executable,
            str(self.script),
            "--config-path",
            str(config),
            "--policy-path",
            str(policy),
            "--secret-path",
            str(secret),
            "--contract-path",
            str(self.contract),
        ]

    @staticmethod
    def _agent_cli_environment() -> dict[str, str]:
        environment = os.environ.copy()
        environment.update({"OPENCLAW_RUNTIME": "container", "OPENCLAW_ROLE": "agent-cli"})
        return environment

    @staticmethod
    def _configuration_args() -> list[str]:
        return [
            "--base-url",
            "https://cloud.example.test:9001",
            "--webhook-public-url",
            f"https://home-agent.home.arpa{WEBHOOK_PATH}",
            "--expected-nextcloud-ip",
            "192.168.2.3",
            "--expected-webhook-ip",
            "192.168.2.38",
        ]

    def test_safe_unconfigured_state_is_reported_without_activation(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            config, policy, secret = self._fixture(folder)
            report = status(
                config_path=config,
                policy_path=policy,
                secret_path=secret,
                contract_path=self.contract,
            )
            self.assertTrue(report["ok"])
            self.assertEqual(report["state"], "disabled")
            self.assertFalse(report["configured"])
            self.assertFalse(report["enabled"])

    def test_prepare_preview_apply_readback_idempotency_and_rollback(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            config, policy, secret = self._fixture(folder)
            original = config.read_bytes()
            base = self._base_command(config, policy, secret)
            preview = subprocess.run(
                [*base, "plan", *self._configuration_args()],
                check=True,
                capture_output=True,
                text=True,
            )
            planned = json.loads(preview.stdout)
            self.assertTrue(planned["ok"])
            self.assertTrue(planned["changed"])
            self.assertFalse(policy.exists())
            self.assertEqual(config.read_bytes(), original)

            applied = subprocess.run(
                [
                    *base,
                    "apply",
                    *self._configuration_args(),
                    "--expected-preview-sha256",
                    planned["preview_sha256"],
                    "--yes",
                ],
                env=self._agent_cli_environment(),
                check=True,
                capture_output=True,
                text=True,
            )
            result = json.loads(applied.stdout)
            self.assertTrue(result["ok"])
            self.assertTrue(result["changed"])
            self.assertRegex(result["backup_id"], r"^[0-9]{8}T[0-9]{6}Z-[a-f0-9]{12}$")
            active = json.loads(config.read_text(encoding="utf-8"))
            self.assertIs(active["plugins"]["entries"][PLUGIN_ID]["enabled"], False)
            self.assertIs(active["channels"][PLUGIN_ID]["enabled"], False)
            self.assertEqual(active["channels"][PLUGIN_ID]["dmPolicy"], "disabled")
            self.assertEqual(active["channels"][PLUGIN_ID]["groupPolicy"], "disabled")
            self.assertEqual(
                active["channels"][PLUGIN_ID]["botSecretFile"],
                "/run/openclaw-secrets/nextcloud-talk-bot-secret",
            )
            self.assertNotIn("botSecret", active["channels"][PLUGIN_ID])
            self.assertEqual(config.stat().st_mode & 0o777, 0o600)
            self.assertEqual(policy.stat().st_mode & 0o777, 0o600)

            second_preview = json.loads(
                subprocess.run(
                    [*base, "plan", *self._configuration_args()],
                    check=True,
                    capture_output=True,
                    text=True,
                ).stdout
            )
            self.assertFalse(second_preview["changed"])
            second = json.loads(
                subprocess.run(
                    [
                        *base,
                        "apply",
                        *self._configuration_args(),
                        "--expected-preview-sha256",
                        second_preview["preview_sha256"],
                        "--yes",
                    ],
                    env=self._agent_cli_environment(),
                    check=True,
                    capture_output=True,
                    text=True,
                ).stdout
            )
            self.assertFalse(second["changed"])
            self.assertIsNone(second["backup_id"])

            rollback = subprocess.run(
                [*base, "rollback", "--backup-id", result["backup_id"], "--yes"],
                env=self._agent_cli_environment(),
                check=True,
                capture_output=True,
                text=True,
            )
            self.assertTrue(json.loads(rollback.stdout)["ok"])
            self.assertEqual(config.read_bytes(), original)
            self.assertFalse(policy.exists())

    def test_apply_is_preview_bound_and_requires_agent_cli_role_in_container(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            config, policy, secret = self._fixture(folder)
            base = self._base_command(config, policy, secret)
            before = config.read_bytes()
            stale = subprocess.run(
                [
                    *base,
                    "apply",
                    *self._configuration_args(),
                    "--expected-preview-sha256",
                    "0" * 64,
                    "--yes",
                ],
                env=self._agent_cli_environment(),
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(stale.returncode, 0)
            self.assertEqual(config.read_bytes(), before)

            plan = json.loads(
                subprocess.run(
                    [*base, "plan", *self._configuration_args()],
                    check=True,
                    capture_output=True,
                    text=True,
                ).stdout
            )
            environment = os.environ.copy()
            environment.update({"OPENCLAW_RUNTIME": "container", "OPENCLAW_ROLE": "gateway"})
            denied = subprocess.run(
                [
                    *base,
                    "apply",
                    *self._configuration_args(),
                    "--expected-preview-sha256",
                    plan["preview_sha256"],
                    "--yes",
                ],
                env=environment,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(denied.returncode, 0)
            self.assertIn("agent-cli", denied.stdout)
            self.assertEqual(config.read_bytes(), before)

            outside_container = subprocess.run(
                [
                    *base,
                    "apply",
                    *self._configuration_args(),
                    "--expected-preview-sha256",
                    plan["preview_sha256"],
                    "--yes",
                ],
                env={
                    key: value
                    for key, value in os.environ.items()
                    if key not in {"OPENCLAW_RUNTIME", "OPENCLAW_ROLE"}
                },
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(outside_container.returncode, 0)
            self.assertIn("agent-cli", outside_container.stdout)
            self.assertEqual(config.read_bytes(), before)

    def test_secret_checks_are_fail_closed_and_never_expose_value(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            secret = root / "secret"
            self.assertFalse(inspect_secret(secret)["ok"])
            secret.write_text("top-secret-value\n", encoding="utf-8")
            secret.chmod(0o644)
            self.assertFalse(inspect_secret(secret)["ok"])
            secret.chmod(0o600)
            self.assertTrue(inspect_secret(secret)["ok"])
            linked = root / "linked"
            linked.symlink_to(secret)
            self.assertFalse(inspect_secret(linked)["ok"])
            self.assertNotIn("top-secret-value", json.dumps(inspect_secret(secret)))

    def test_policy_rejects_unsafe_urls_public_ips_and_unknown_fields(self) -> None:
        with self.assertRaisesRegex(ValueError, "HTTPS"):
            build_prepared_policy(
                base_url="http://cloud.example.test:9001",
                webhook_public_url=f"https://home-agent.home.arpa{WEBHOOK_PATH}",
                expected_nextcloud_ips=["192.168.2.3"],
                expected_webhook_ips=["192.168.2.38"],
            )
        with self.assertRaisesRegex(ValueError, "exakt den Pfad"):
            build_prepared_policy(
                base_url="https://cloud.example.test:9001",
                webhook_public_url="https://home-agent.home.arpa/wrong",
                expected_nextcloud_ips=["192.168.2.3"],
                expected_webhook_ips=["192.168.2.38"],
            )
        with self.assertRaisesRegex(ValueError, "private LAN-Adresse"):
            build_prepared_policy(
                base_url="https://cloud.example.test:9001",
                webhook_public_url=f"https://home-agent.home.arpa{WEBHOOK_PATH}",
                expected_nextcloud_ips=["8.8.8.8"],
                expected_webhook_ips=["192.168.2.38"],
            )
        policy = build_prepared_policy(
            base_url="https://cloud.example.test:9001",
            webhook_public_url=f"https://home-agent.home.arpa{WEBHOOK_PATH}",
            expected_nextcloud_ips=["192.168.2.3"],
            expected_webhook_ips=["192.168.2.38"],
        )
        policy["unexpected"] = True
        with self.assertRaisesRegex(ValueError, "unbekannte Felder"):
            validate_policy(policy)

    def test_status_rejects_config_drift_and_doctor_can_remain_offline(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            config, policy_path, secret = self._fixture(folder)
            channel = prepared_channel_config(
                base_url="https://cloud.example.test:9001",
                webhook_public_url=f"https://home-agent.home.arpa{WEBHOOK_PATH}",
            )
            payload = json.loads(config.read_text(encoding="utf-8"))
            payload["plugins"]["entries"][PLUGIN_ID] = {"enabled": False}
            payload["channels"] = {PLUGIN_ID: channel}
            config.write_text(json.dumps(payload) + "\n", encoding="utf-8")
            policy = build_prepared_policy(
                base_url="https://cloud.example.test:9001",
                webhook_public_url=f"https://home-agent.home.arpa{WEBHOOK_PATH}",
                expected_nextcloud_ips=["192.168.2.3"],
                expected_webhook_ips=["192.168.2.38"],
            )
            policy_path.parent.mkdir(parents=True)
            policy_path.write_text(json.dumps(policy) + "\n", encoding="utf-8")
            secret.write_text("fixture-secret\n", encoding="utf-8")
            report = doctor(
                config_path=config,
                policy_path=policy_path,
                secret_path=secret,
                contract_path=self.contract,
                live=False,
            )
            self.assertTrue(report["ok"])
            self.assertTrue(report["ready_for_activation"])
            self.assertIs(report["live"]["requested"], False)

            payload["channels"][PLUGIN_ID]["unknown"] = True
            config.write_text(json.dumps(payload) + "\n", encoding="utf-8")
            drift = status(
                config_path=config,
                policy_path=policy_path,
                secret_path=secret,
                contract_path=self.contract,
            )
            self.assertFalse(drift["ok"])
            self.assertEqual(drift["state"], "invalid")

    def test_doctor_stops_before_network_connection_on_dns_drift(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            config, policy_path, secret = self._fixture(folder)
            channel = prepared_channel_config(
                base_url="https://cloud.example.test:9001",
                webhook_public_url=f"https://home-agent.home.arpa{WEBHOOK_PATH}",
            )
            payload = json.loads(config.read_text(encoding="utf-8"))
            payload["plugins"]["entries"][PLUGIN_ID] = {"enabled": False}
            payload["channels"] = {PLUGIN_ID: channel}
            config.write_text(json.dumps(payload) + "\n", encoding="utf-8")
            policy = build_prepared_policy(
                base_url="https://cloud.example.test:9001",
                webhook_public_url=f"https://home-agent.home.arpa{WEBHOOK_PATH}",
                expected_nextcloud_ips=["192.168.2.3"],
                expected_webhook_ips=["192.168.2.38"],
            )
            policy_path.parent.mkdir(parents=True)
            policy_path.write_text(json.dumps(policy) + "\n", encoding="utf-8")

            with (
                patch(
                    "personal_assistant.nextcloud_talk._resolve_ips",
                    side_effect=[["192.168.2.99"], ["192.168.2.38"]],
                ),
                patch("personal_assistant.nextcloud_talk._tls_probe") as tls_probe,
                patch("personal_assistant.nextcloud_talk._nextcloud_status") as cloud_status,
            ):
                report = doctor(
                    config_path=config,
                    policy_path=policy_path,
                    secret_path=secret,
                    contract_path=self.contract,
                    live=True,
                )

            self.assertFalse(report["ok"])
            self.assertIn("Live-Verbindungen wurden nicht gestartet", report["live"]["error"])
            tls_probe.assert_not_called()
            cloud_status.assert_not_called()


if __name__ == "__main__":
    unittest.main()
