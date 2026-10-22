from __future__ import annotations

import json
import os
import shutil
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from personal_assistant.nextcloud_talk import (
    PLUGIN_ID,
    PLUGIN_PATH,
    WEBHOOK_PATH,
    _resolve_ips,
    _tls_probe,
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
    def _configuration_args(
        webhook_url: str = f"https://home-agent.home.arpa{WEBHOOK_PATH}",
        webhook_ip: str = "192.168.2.38",
    ) -> list[str]:
        return [
            "--base-url",
            "https://cloud.example.test:9001",
            "--webhook-public-url",
            webhook_url,
            "--expected-nextcloud-ip",
            "192.168.2.3",
            "--expected-webhook-ip",
            webhook_ip,
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
        for hostname, webhook_ip in (
            ("home-agent.home.arpa", "192.168.2.38"),
            ("192.168.2.38", "192.168.2.38"),
            ("[fd12:3456::38]", "fd12:3456::38"),
        ):
            with self.subTest(hostname=hostname):
                self._check_prepare_lifecycle(f"https://{hostname}{WEBHOOK_PATH}", webhook_ip)

    def _check_prepare_lifecycle(self, webhook_url: str, webhook_ip: str) -> None:
        with tempfile.TemporaryDirectory() as folder:
            config, policy, secret = self._fixture(folder)
            original = config.read_bytes()
            base = self._base_command(config, policy, secret)
            configuration_args = self._configuration_args(webhook_url, webhook_ip)
            preview = subprocess.run(
                [*base, "plan", *configuration_args],
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
                    *configuration_args,
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
            self.assertEqual(active["channels"][PLUGIN_ID]["webhookPublicUrl"], webhook_url)
            prepared_policy = validate_policy(json.loads(policy.read_text(encoding="utf-8")))
            self.assertEqual(prepared_policy["expected_webhook_ips"], [webhook_ip])
            readback = status(
                config_path=config,
                policy_path=policy,
                secret_path=secret,
                contract_path=self.contract,
            )
            self.assertTrue(readback["ok"])
            self.assertEqual(readback["state"], "prepared-disabled")
            self.assertEqual(
                active["channels"][PLUGIN_ID]["botSecretFile"],
                "/run/openclaw-secrets/nextcloud-talk-bot-secret",
            )
            self.assertNotIn("botSecret", active["channels"][PLUGIN_ID])
            self.assertEqual(config.stat().st_mode & 0o777, 0o600)
            self.assertEqual(policy.stat().st_mode & 0o777, 0o600)

            second_preview = json.loads(
                subprocess.run(
                    [*base, "plan", *configuration_args],
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
                        *configuration_args,
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

    def test_private_literal_policy_requires_exactly_matching_bound_ip(self) -> None:
        for hostname, address in (
            ("10.0.0.38", "10.0.0.38"),
            ("172.16.0.38", "172.16.0.38"),
            ("192.168.2.38", "192.168.2.38"),
            ("[fd12:3456::38]", "fd12:3456::38"),
        ):
            with self.subTest(hostname=hostname):
                policy = build_prepared_policy(
                    base_url="https://cloud.example.test:9001",
                    webhook_public_url=f"https://{hostname}{WEBHOOK_PATH}",
                    expected_nextcloud_ips=["192.168.2.3"],
                    expected_webhook_ips=[address],
                )
                self.assertEqual(validate_policy(policy), policy)
                for unexpected in (["192.168.2.99"], [address, "192.168.2.99"], []):
                    with self.subTest(expected_ips=unexpected), self.assertRaises(ValueError):
                        build_prepared_policy(
                            base_url="https://cloud.example.test:9001",
                            webhook_public_url=f"https://{hostname}{WEBHOOK_PATH}",
                            expected_nextcloud_ips=["192.168.2.3"],
                            expected_webhook_ips=unexpected,
                        )

    def test_literal_webhook_does_not_weaken_url_or_address_guards(self) -> None:
        forbidden_hosts = (
            "8.8.8.8", "127.0.0.1", "169.254.1.2", "0.0.0.0", "224.0.0.1",
            "240.0.0.1", "192.0.2.38", "198.51.100.38", "203.0.113.38",
            "100.64.0.38", "[2001:4860:4860::8888]", "[::1]", "[::]",
            "[fe80::38]", "[ff02::1]", "[2001:db8::38]", "[::ffff:192.168.2.38]",
            "[fd12:3456::38%25eth0]",
        )
        for hostname in forbidden_hosts:
            with self.subTest(hostname=hostname), self.assertRaises(ValueError):
                prepared_channel_config(
                    base_url="https://cloud.example.test:9001",
                    webhook_public_url=f"https://{hostname}{WEBHOOK_PATH}",
                )
        for url in (
            f"http://192.168.2.38{WEBHOOK_PATH}",
            f"https://192.168.2.38:8788{WEBHOOK_PATH}",
            f"https://user:password@192.168.2.38{WEBHOOK_PATH}",
            f"https://@192.168.2.38{WEBHOOK_PATH}",
            f"https://192.168.2.38{WEBHOOK_PATH}?token=invalid",
            f"https://192.168.2.38{WEBHOOK_PATH}#fragment",
            f"https://192.168.2.38{WEBHOOK_PATH}?",
            f"https://192.168.2.38{WEBHOOK_PATH}#",
            "https://192.168.2.38/wrong",
            f"https://192.168.2.38{WEBHOOK_PATH}/",
        ):
            with self.subTest(url=url), self.assertRaises(ValueError):
                prepared_channel_config(
                    base_url="https://cloud.example.test:9001", webhook_public_url=url,
                )
        for base_url in ("https://192.168.2.3:9001", "https://[fd12:3456::3]:9001"):
            with self.subTest(base_url=base_url), self.assertRaises(ValueError):
                prepared_channel_config(
                    base_url=base_url,
                    webhook_public_url=f"https://192.168.2.38{WEBHOOK_PATH}",
                )

    def test_bound_ips_also_reject_reserved_documentation_and_scoped_addresses(self) -> None:
        for address in ("192.0.2.3", "240.0.0.3", "2001:db8::3", "::ffff:192.168.2.3", "fd12::3%eth0"):
            for field in ("expected_nextcloud_ips", "expected_webhook_ips"):
                with self.subTest(address=address, field=field), self.assertRaises(ValueError):
                    build_prepared_policy(
                        base_url="https://cloud.example.test:9001",
                        webhook_public_url=f"https://home-agent.home.arpa{WEBHOOK_PATH}",
                        expected_nextcloud_ips=(
                            [address] if field == "expected_nextcloud_ips" else ["192.168.2.3"]
                        ),
                        expected_webhook_ips=(
                            [address] if field == "expected_webhook_ips" else ["192.168.2.38"]
                        ),
                    )

    def test_literal_resolution_bypasses_dns_but_dns_names_keep_resolver_checks(self) -> None:
        with patch("personal_assistant.nextcloud_talk.socket.getaddrinfo") as resolver:
            self.assertEqual(_resolve_ips("192.168.2.38"), ["192.168.2.38"])
            self.assertEqual(_resolve_ips("FD12:3456:0:0:0:0:0:38"), ["fd12:3456::38"])
            resolver.assert_not_called()
            resolver.return_value = [
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.168.2.38", 0)),
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.168.2.38", 0)),
            ]
            self.assertEqual(_resolve_ips("home-agent.home.arpa"), ["192.168.2.38"])
            resolver.assert_called_once_with("home-agent.home.arpa", None, type=socket.SOCK_STREAM)

    def test_switching_dns_to_literal_invalidates_preview_without_writing(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            config, policy, secret = self._fixture(folder)
            before = config.read_bytes()
            base = self._base_command(config, policy, secret)
            dns_args = self._configuration_args()
            ip_args = self._configuration_args(f"https://192.168.2.38{WEBHOOK_PATH}")
            for preview_args, apply_args in ((dns_args, ip_args), (ip_args, dns_args)):
                with self.subTest(preview_args=preview_args):
                    preview = json.loads(subprocess.run(
                        [*base, "plan", *preview_args], check=True, capture_output=True, text=True,
                    ).stdout)
                    applied = subprocess.run(
                        [*base, "apply", *apply_args,
                         "--expected-preview-sha256", preview["preview_sha256"], "--yes"],
                        env=self._agent_cli_environment(), check=False, capture_output=True, text=True,
                    )
                    self.assertNotEqual(applied.returncode, 0)
                    self.assertEqual(config.read_bytes(), before)
                    self.assertFalse(policy.exists())

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

    def test_ip_doctor_checks_nextcloud_dns_and_verifies_literal_tls_fail_closed(self) -> None:
        for hostname, address in (("192.168.2.38", "192.168.2.38"), ("[fd12:3456::38]", "fd12:3456::38")):
            with self.subTest(hostname=hostname):
                self._check_ip_doctor(hostname, address)

    def _check_ip_doctor(self, hostname: str, address: str) -> None:
        with tempfile.TemporaryDirectory() as folder:
            config, policy_path, secret = self._fixture(folder)
            webhook_url = f"https://{hostname}{WEBHOOK_PATH}"
            payload = json.loads(config.read_text(encoding="utf-8"))
            payload["plugins"]["entries"][PLUGIN_ID] = {"enabled": False}
            payload["channels"] = {PLUGIN_ID: prepared_channel_config(
                base_url="https://cloud.example.test:9001", webhook_public_url=webhook_url,
            )}
            config.write_text(json.dumps(payload) + "\n", encoding="utf-8")
            policy_path.parent.mkdir(parents=True)
            policy_path.write_text(json.dumps(build_prepared_policy(
                base_url="https://cloud.example.test:9001",
                webhook_public_url=webhook_url,
                expected_nextcloud_ips=["192.168.2.3"],
                expected_webhook_ips=[address],
            )) + "\n", encoding="utf-8")
            for tls_error in (None, ssl.SSLCertVerificationError("IP address mismatch")):
                with (
                    self.subTest(tls_error=tls_error),
                    patch("personal_assistant.nextcloud_talk.socket.getaddrinfo", return_value=[
                        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.168.2.3", 0)),
                    ]) as resolver,
                    patch("personal_assistant.nextcloud_talk._tls_probe", side_effect=[
                        {"ok": True}, tls_error if tls_error is not None else {"ok": True},
                    ]) as tls_probe,
                    patch(
                        "personal_assistant.nextcloud_talk._nextcloud_status", return_value={"ok": True},
                    ) as cloud_status,
                ):
                    report = doctor(
                        config_path=config, policy_path=policy_path, secret_path=secret,
                        contract_path=self.contract, live=True,
                    )
                    resolver.assert_called_once_with("cloud.example.test", None, type=socket.SOCK_STREAM)
                    self.assertEqual(tls_probe.call_args_list[1].args, (address, 443))
                    if tls_error is None:
                        self.assertTrue(report["ok"])
                        self.assertEqual(report["live"]["webhook_dns"]["mode"], "ip-literal")
                        cloud_status.assert_called_once()
                    else:
                        self.assertFalse(report["ok"])
                        self.assertFalse(report["live"]["ok"])
                        self.assertIn("IP address mismatch", report["live"]["error"])
                        cloud_status.assert_not_called()

    @unittest.skipUnless(shutil.which("openssl"), "OpenSSL is required for the local TLS fixture")
    def test_tls_probe_real_ip_san_trust_and_hostname_verification(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            certificate = root / "test-ca.pem"
            key = root / "test-key.pem"
            subprocess.run(
                ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
                 "-keyout", str(key), "-out", str(certificate), "-subj", "/CN=local-test-only",
                 "-addext", "subjectAltName=IP:192.168.2.38"],
                check=True, capture_output=True, timeout=15,
            )
            server_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            server_context.load_cert_chain(str(certificate), str(key))
            for hostname, trusted in (
                ("192.168.2.38", True),
                ("192.168.2.39", True),
                ("192.168.2.38", False),
            ):
                with self.subTest(hostname=hostname, trusted=trusted):
                    # Only the transport is replaced; certificate verification uses the
                    # production default SSL context and real local TLS handshakes.
                    server_side, client_side = socket.socketpair()
                    server_side.settimeout(3)
                    client_side.settimeout(3)
                    server_errors: list[OSError] = []

                    def serve(connection: socket.socket, errors: list[OSError]) -> None:
                        try:
                            with server_context.wrap_socket(connection, server_side=True):
                                pass
                        except OSError as exc:
                            errors.append(exc)

                    worker = threading.Thread(target=serve, args=(server_side, server_errors), daemon=True)
                    worker.start()
                    try:
                        with (
                            patch.dict(os.environ, {
                                "SSL_CERT_FILE": str(certificate) if trusted else os.devnull,
                                "SSL_CERT_DIR": folder,
                            }),
                            patch(
                                "personal_assistant.nextcloud_talk.socket.create_connection",
                                return_value=client_side,
                            ),
                        ):
                            if trusted and hostname == "192.168.2.38":
                                result = _tls_probe(hostname, 443, timeout=3)
                                self.assertTrue(result["ok"])
                                self.assertTrue(result["peer_certificate_present"])
                            else:
                                with self.assertRaises(ssl.SSLCertVerificationError):
                                    _tls_probe(hostname, 443, timeout=3)
                    finally:
                        client_side.close()
                        worker.join(timeout=4)
                        server_side.close()
                    self.assertFalse(worker.is_alive())
                    if trusted and hostname == "192.168.2.38":
                        self.assertEqual(server_errors, [])


if __name__ == "__main__":
    unittest.main()
