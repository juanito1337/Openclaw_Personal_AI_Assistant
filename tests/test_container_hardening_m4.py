from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest import mock

from personal_assistant import cli as assistant_cli
from personal_assistant import container_entrypoint
from personal_assistant.clamav_health import inspect_database
from personal_assistant.clamav_transport import CURLOPT_URL, LIBCURL, probe_tls_transport
from personal_assistant.container_health import evaluate

ROOT = Path(__file__).resolve().parents[1]


class ContainerHardeningM4Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        rendered = subprocess.run(
            [
                "docker", "compose", "--profile", "tools", "--profile", "maintenance",
                "--env-file", "docker/deployment.env.example", "-f", "compose.yaml",
                "config", "--format", "json",
            ],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=True,
        )
        cls.compose = json.loads(rendered.stdout)
        cls.contract = json.loads(
            (ROOT / "docs/architecture/runtime-hardening.json").read_text(encoding="utf-8")
        )

    def test_rendered_compose_matches_hardening_contract(self) -> None:
        defaults = self.contract["defaults"]
        for role, expected in self.contract["roles"].items():
            service = self.compose["services"][role]
            self.assertEqual(service["user"], expected["user"], role)
            self.assertEqual(sorted(service.get("networks", {})), sorted(expected["networks"]), role)
            self.assertEqual(service["pids_limit"], expected["pids"], role)
            self.assertEqual(int(service["mem_limit"]), expected["memory"], role)
            self.assertEqual(float(service["cpus"]), expected["cpus"], role)
            self.assertTrue(service["read_only"], role)
            self.assertEqual(service["cap_drop"], defaults["cap_drop"], role)
            self.assertEqual(service["security_opt"], defaults["security_opt"], role)
            self.assertEqual(service["logging"]["driver"], defaults["logging_driver"], role)

    def test_dynamic_fixture_is_uid_portable_and_oom_failure_specific(self) -> None:
        checker = ROOT / "scripts/check-container-hardening.sh"
        fake_docker = r'''#!/usr/bin/env python3
import os
import stat
import sys
from pathlib import Path

arguments = sys.argv[1:]
if arguments == ["info"] or arguments[:2] == ["image", "inspect"]:
    raise SystemExit(0)
if arguments[:2] == ["network", "create"]:
    raise SystemExit(0)
if arguments and arguments[0] == "run":
    signal_name = next(
        (item for item in arguments if item.startswith("openclaw-m4-signal-")),
        None,
    )
    if signal_name is None:
        oom_name = next(
            (item for item in arguments if item.startswith("openclaw-m4-oom-")),
            None,
        )
        if oom_name is not None:
            if os.environ.get("FAKE_OOM_MODE") == "unexpected":
                print("unrelated allocator failure", file=sys.stderr)
                raise SystemExit(7)
            if os.environ.get("FAKE_OOM_MODE") == "sigkill":
                print("OPENCLAW_MEMORY_PRESSURE: 32 MiB")
                raise SystemExit(137)
            print("OPENCLAW_MEMORY_LIMIT_ENFORCED: MemoryError", file=sys.stderr)
            raise SystemExit(42)
        raise SystemExit(0)
    volume = next(item for item in arguments if item.endswith(":/workspace:ro"))
    workspace = Path(volume.removesuffix(":/workspace:ro"))
    marker = workspace / ".layout-version.json"
    directory_mode = stat.S_IMODE(workspace.stat().st_mode)
    marker_mode = stat.S_IMODE(marker.stat().st_mode)
    if directory_mode & 0o005 != 0o005 or marker_mode & 0o004 != 0o004:
        print("signal fixture is not readable by a different UID", file=sys.stderr)
        raise SystemExit(92)
    print("signal fixture permissions ok", file=sys.stderr)
    raise SystemExit(0)
if arguments and arguments[0] == "logs":
    print("READY")
    raise SystemExit(0)
if arguments and arguments[0] == "stop":
    raise SystemExit(0)
if arguments and arguments[0] == "inspect":
    template = arguments[arguments.index("--format") + 1]
    container = arguments[-1]
    if "PidsLimit" in template:
        print("32")
    elif "NanoCpus" in template:
        print("250000000")
    elif "HostConfig.Memory" in template:
        print("67108864")
    elif "OOMKilled" in template:
        print("false")
    elif "State.Error" in template:
        print("")
    elif "ExitCode" in template:
        if container.startswith("openclaw-m4-oom-"):
            mode = os.environ.get("FAKE_OOM_MODE")
            print("137" if mode == "sigkill" else "7" if mode == "unexpected" else "42")
        else:
            print("0")
    raise SystemExit(0)
if arguments and arguments[0] == "create":
    raise SystemExit(0)
if arguments and arguments[0] == "rm":
    raise SystemExit(0)
if arguments[:2] == ["network", "rm"]:
    raise SystemExit(0)
print("unexpected fake docker call: " + " ".join(arguments), file=sys.stderr)
raise SystemExit(86)
'''
        with tempfile.TemporaryDirectory() as folder:
            binary = Path(folder) / "docker"
            binary.write_text(fake_docker, encoding="utf-8")
            binary.chmod(0o755)
            environment = os.environ.copy()
            environment["PATH"] = f"{folder}:{environment['PATH']}"
            result = subprocess.run(
                [str(checker), "fixture"],
                cwd=ROOT,
                env=environment,
                text=True,
                capture_output=True,
                check=False,
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("signal fixture permissions ok", result.stderr)
        self.assertIn("Dynamische M4-Haertung erfolgreich", result.stdout)

        with tempfile.TemporaryDirectory() as folder:
            binary = Path(folder) / "docker"
            binary.write_text(fake_docker, encoding="utf-8")
            binary.chmod(0o755)
            environment = os.environ.copy()
            environment["FAKE_OOM_MODE"] = "sigkill"
            environment["PATH"] = f"{folder}:{environment['PATH']}"
            result = subprocess.run(
                [str(checker), "fixture"],
                cwd=ROOT,
                env=environment,
                text=True,
                capture_output=True,
                check=False,
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Dynamische M4-Haertung erfolgreich", result.stdout)

        with tempfile.TemporaryDirectory() as folder:
            binary = Path(folder) / "docker"
            binary.write_text(fake_docker, encoding="utf-8")
            binary.chmod(0o755)
            environment = os.environ.copy()
            environment["FAKE_OOM_MODE"] = "unexpected"
            environment["PATH"] = f"{folder}:{environment['PATH']}"
            result = subprocess.run(
                [str(checker), "fixture"],
                cwd=ROOT,
                env=environment,
                text=True,
                capture_output=True,
                check=False,
            )
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("Speicherlimit nicht nachgewiesen", result.stderr)
        self.assertIn("Exit=7", result.stderr)

    def test_only_gateway_publishes_loopback_port(self) -> None:
        published = {name: value.get("ports", []) for name, value in self.compose["services"].items()}
        self.assertEqual(published["gateway"], self.contract["published_ports"]["gateway"])
        self.assertTrue(all(not ports for name, ports in published.items() if name != "gateway"))
        self.assertFalse(self.contract["exceptions"]["host_network_roles"])
        self.assertEqual(self.contract["exceptions"]["host_gateway_roles"], ["ollama-proxy"])
        self.assertIn(
            "host.docker.internal=host-gateway",
            self.compose["services"]["ollama-proxy"]["extra_hosts"],
        )

    def test_gateway_disables_runtime_plugin_mutation(self) -> None:
        environment = self.compose["services"]["gateway"]["environment"]
        self.assertEqual(environment["OPENCLAW_NIX_MODE"], "1")
        self.assertTrue(self.compose["services"]["gateway"]["read_only"])

    def test_gateway_configuration_is_read_only_but_agent_cli_can_run_setup(self) -> None:
        protected = {
            "/home/node/.openclaw/workspace/mail_agent",
            "/home/node/.openclaw/workspace/personal_assistant",
        }
        gateway_mounts = {
            item["target"]: item for item in self.compose["services"]["gateway"]["volumes"]
        }
        agent_cli_mounts = {
            item["target"]: item
            for item in self.compose["services"]["agent-cli"]["volumes"]
        }

        self.assertTrue(protected.issubset(gateway_mounts))
        for target in protected:
            self.assertTrue(gateway_mounts[target]["read_only"], target)
            self.assertNotIn(target, agent_cli_mounts)
        self.assertFalse(
            agent_cli_mounts["/home/node/.openclaw/workspace"].get("read_only", False)
        )

    def test_roles_mount_exact_env_and_secret_files(self) -> None:
        forbidden_targets = {"/etc/openclaw-agent", "/run/openclaw-secrets"}
        for role, expected in self.contract["roles"].items():
            volumes = self.compose["services"][role].get("volumes", [])
            targets = {item["target"] for item in volumes}
            self.assertFalse(targets & forbidden_targets, role)
            env_files = sorted(target for target in targets if target.startswith("/etc/openclaw-env/"))
            secret_files = sorted(
                Path(target).name for target in targets
                if target.startswith("/run/openclaw-env/") or target.startswith("/run/openclaw-secrets/")
            )
            self.assertEqual(env_files, sorted(expected["env_files"]), role)
            self.assertEqual(secret_files, sorted(expected["secret_files"]), role)

    def test_env_parser_treats_shell_syntax_as_literal_data(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            env_file = root / "mail-agent.env"
            marker = root / "executed"
            env_file.write_text(
                f"NEXTCLOUD_TOKEN='$(touch {marker})'\nNEXTCLOUD_URL=https://example.invalid\n",
                encoding="utf-8",
            )
            with mock.patch.object(container_entrypoint, "ENV_ROOTS", (root,)):
                values = container_entrypoint.parse_env_file(env_file)
            self.assertEqual(values["NEXTCLOUD_TOKEN"], f"$(touch {marker})")
            self.assertFalse(marker.exists())

    def test_env_parser_rejects_unapproved_keys_and_locations(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            env_file = root / "foreign.env"
            env_file.write_text("LD_PRELOAD=/tmp/evil.so\n", encoding="utf-8")
            with (
                mock.patch.object(container_entrypoint, "ENV_ROOTS", (root,)),
                self.assertRaisesRegex(ValueError, "Nicht freigegebener"),
            ):
                container_entrypoint.parse_env_file(env_file)
            with self.assertRaisesRegex(ValueError, "Mountwurzeln"):
                container_entrypoint.parse_env_file(env_file)

    def test_assistant_cli_reloads_mounted_gateway_environment_for_docker_exec(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            marker = root / "executed"
            files = tuple(root / name for name in ("mail.env", "assistant.env", "gateway.env"))
            files[0].write_text("HIMALAYA_CONFIG=/fixture/himalaya.toml\n", encoding="utf-8")
            files[1].write_text(
                "NEXTCLOUD_URL=https://cloud.example.invalid\n"
                "NEXTCLOUD_USER=openclaw\n"
                f"NEXTCLOUD_TOKEN='$(touch {marker})'\n",
                encoding="utf-8",
            )
            files[2].write_text("OPENCLAW_GATEWAY_TOKEN=fixture-token\n", encoding="utf-8")
            role_files = {**container_entrypoint.ROLE_ENV_FILES, "gateway": tuple(map(str, files))}
            with (
                mock.patch.object(container_entrypoint, "ENV_ROOTS", (root,)),
                mock.patch.object(container_entrypoint, "ROLE_ENV_FILES", role_files),
                mock.patch.object(assistant_cli, "DEFAULT_SECRETS", root / "missing.env"),
                mock.patch.dict(
                    os.environ,
                    {
                        "HOME": str(root),
                        "OPENCLAW_RUNTIME": "container",
                        "OPENCLAW_ROLE": "gateway",
                    },
                    clear=True,
                ),
            ):
                assistant_cli._load_secrets()
                self.assertEqual(os.environ["NEXTCLOUD_URL"], "https://cloud.example.invalid")
                self.assertEqual(os.environ["NEXTCLOUD_USER"], "openclaw")
                self.assertEqual(os.environ["NEXTCLOUD_TOKEN"], f"$(touch {marker})")
                self.assertEqual(os.environ["OPENCLAW_GATEWAY_TOKEN"], "fixture-token")
            self.assertFalse(marker.exists())

    def test_assistant_cli_without_mounted_role_files_keeps_image_smoke_usable(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            role_files = {
                **container_entrypoint.ROLE_ENV_FILES,
                "gateway": (str(root / "mail.env"), str(root / "assistant.env")),
            }
            with (
                mock.patch.object(container_entrypoint, "ENV_ROOTS", (root,)),
                mock.patch.object(container_entrypoint, "ROLE_ENV_FILES", role_files),
                mock.patch.object(assistant_cli, "DEFAULT_SECRETS", root / "missing.env"),
                mock.patch.dict(
                    os.environ,
                    {
                        "HOME": str(root),
                        "OPENCLAW_RUNTIME": "container",
                        "OPENCLAW_ROLE": "gateway",
                    },
                    clear=True,
                ),
            ):
                assistant_cli._load_secrets()
                self.assertNotIn("NEXTCLOUD_URL", os.environ)

    def test_assistant_cli_rejects_partially_mounted_role_environment(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            present = root / "assistant.env"
            missing = root / "gateway.env"
            present.write_text("NEXTCLOUD_URL=https://cloud.example.invalid\n", encoding="utf-8")
            role_files = {
                **container_entrypoint.ROLE_ENV_FILES,
                "gateway": (str(present), str(missing)),
            }
            with (
                mock.patch.object(container_entrypoint, "ENV_ROOTS", (root,)),
                mock.patch.object(container_entrypoint, "ROLE_ENV_FILES", role_files),
                mock.patch.object(assistant_cli, "DEFAULT_SECRETS", root / "missing.env"),
                mock.patch.dict(
                    os.environ,
                    {
                        "HOME": str(root),
                        "OPENCLAW_RUNTIME": "container",
                        "OPENCLAW_ROLE": "gateway",
                    },
                    clear=True,
                ),
                self.assertRaisesRegex(ValueError, "Erforderliche Env-Datei fehlt"),
            ):
                assistant_cli._load_secrets()

    def test_proxy_loopback_is_translated_and_listener_is_pinned(self) -> None:
        environment = {"OLLAMA_PRIORITY_UPSTREAM": "http://127.0.0.1:11434"}
        container_entrypoint.normalize_proxy_network(environment)
        self.assertEqual(environment["OLLAMA_PRIORITY_UPSTREAM"], "http://host.docker.internal:11434")
        self.assertEqual(environment["OLLAMA_PRIORITY_LISTEN_HOST"], "0.0.0.0")
        self.assertEqual(environment["OLLAMA_PRIORITY_LISTEN_PORT"], "11435")

    def test_worker_liveness_does_not_mask_business_failure(self) -> None:
        current = datetime.now(UTC)
        payload = {
            "updated_at": current.isoformat(),
            "state": "waiting",
            "business_status": "failed",
            "consecutive_failures": 3,
        }
        self.assertTrue(evaluate("", payload, current=current)["ok"])
        self.assertTrue(evaluate("-readiness", payload, current=current)["ok"])
        with self.assertRaisesRegex(ValueError, "business unhealthy"):
            evaluate("-business", payload, current=current)

    def test_disabled_worker_is_ready_and_business_healthy(self) -> None:
        current = datetime.now(UTC)
        payload = {
            "updated_at": current.isoformat(),
            "state": "disabled",
            "business_status": "disabled",
            "consecutive_failures": 2,
        }
        self.assertTrue(evaluate("-readiness", payload, current=current)["ok"])
        self.assertTrue(evaluate("-business", payload, current=current)["ok"])

    def test_stale_or_stopped_heartbeat_fails_liveness(self) -> None:
        current = datetime.now(UTC)
        base = {"state": "waiting", "business_status": "healthy"}
        with self.assertRaisesRegex(ValueError, "stale heartbeat"):
            evaluate(
                "",
                {**base, "updated_at": (current - timedelta(seconds=181)).isoformat()},
                current=current,
            )
        with self.assertRaisesRegex(ValueError, "worker stopped"):
            evaluate("", {**base, "state": "stopped", "updated_at": current.isoformat()}, current=current)

    def test_clamav_health_requires_complete_fresh_signatures_and_identity(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            database = Path(folder)
            current = 2_000_000_000.0
            for name in ("main.cvd", "daily.cld", "bytecode.cvd"):
                path = database / name
                path.write_bytes(b"signature")
                # Explicit timestamps make this test independent of wall time.
                os.utime(path, (current - 60, current - 60))
            completed = subprocess.CompletedProcess(
                ["clamscan", "--version"], 0, "ClamAV 1.4.3/27888/Tue Aug 5 00:00:00 2026\n", ""
            )
            report = inspect_database(
                database, max_age_seconds=120, now=current, run=lambda *args, **kwargs: completed
            )
            self.assertTrue(report["ok"])
            (database / "main.cvd").unlink()
            with self.assertRaisesRegex(ValueError, "Signatur fehlt"):
                inspect_database(
                    database,
                    max_age_seconds=120,
                    now=current,
                    run=lambda *args, **kwargs: completed,
                )

    def test_clamav_health_rejects_old_database_and_unverifiable_scanner(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            database = Path(folder)
            current = 2_000_000_000.0
            for name in ("main.cvd", "daily.cvd", "bytecode.cvd"):
                path = database / name
                path.write_bytes(b"signature")
                os.utime(path, (current - 500, current - 500))
            good = subprocess.CompletedProcess(["clamscan", "--version"], 0, "ClamAV 1.4/42/date\n", "")
            with self.assertRaisesRegex(ValueError, "zu alt"):
                inspect_database(database, max_age_seconds=120, now=current, run=lambda *args, **kwargs: good)
            os.utime(database / "daily.cvd", (current, current))
            bad = subprocess.CompletedProcess(["clamscan", "--version"], 0, "ClamAV 1.4\n", "")
            with self.assertRaisesRegex(ValueError, "Scanneridentitaet"):
                inspect_database(database, max_age_seconds=120, now=current, run=lambda *args, **kwargs: bad)

    def test_clamav_transport_exercises_libcurl_https_and_fails_closed(self) -> None:
        class FakeFunction:
            def __init__(self, result: object = 0) -> None:
                self.result = result
                self.calls: list[tuple[object, ...]] = []
                self.argtypes: object = None
                self.restype: object = None

            def __call__(self, *args: object) -> object:
                self.calls.append(args)
                return self.result

        class FakeCurl:
            def __init__(self, perform_code: int = 0) -> None:
                self.curl_global_init = FakeFunction(0)
                self.curl_global_cleanup = FakeFunction(None)
                self.curl_easy_init = FakeFunction(1234)
                self.curl_easy_setopt = FakeFunction(0)
                self.curl_easy_perform = FakeFunction(perform_code)
                self.curl_easy_cleanup = FakeFunction(None)

        curl = FakeCurl()
        report = probe_tls_transport(loader=lambda name: curl)
        self.assertTrue(report["ok"])
        self.assertEqual(report["library"], LIBCURL)
        url_calls = [
            call
            for call in curl.curl_easy_setopt.calls
            if len(call) == 3 and getattr(call[1], "value", None) == CURLOPT_URL
        ]
        self.assertEqual(len(url_calls), 1)
        self.assertTrue(getattr(url_calls[0][2], "value", b"").startswith(b"https://"))
        self.assertEqual(len(curl.curl_easy_perform.calls), 1)
        self.assertEqual(len(curl.curl_easy_cleanup.calls), 1)
        self.assertEqual(len(curl.curl_global_cleanup.calls), 1)

        with self.assertRaisesRegex(RuntimeError, "TLS-Handshake"):
            probe_tls_transport(loader=lambda name: FakeCurl(35))
        with self.assertRaisesRegex(RuntimeError, "konnte nicht geladen"):
            probe_tls_transport(
                loader=lambda name: (_ for _ in ()).throw(OSError("symbol not found"))
            )


if __name__ == "__main__":
    unittest.main()
