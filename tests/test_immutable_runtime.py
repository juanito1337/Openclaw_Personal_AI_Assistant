from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tarfile
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from personal_assistant.config import (
    AssistantConfig,
    RuntimeConfig,
    SearchConfig,
    load_config,
)
from personal_assistant.runtime_identity import runtime_identity
from personal_assistant.runtime_layout import LAYOUT_VERSION, migrate_layout


class ImmutableRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(__file__).resolve().parents[1]

    def _legacy_fixture(self, folder: str) -> tuple[Path, Path]:
        state = Path(folder) / "state"
        workspace = state / "workspace"
        (workspace / "scripts").mkdir(parents=True)
        (workspace / "mail_agent/data").mkdir(parents=True)
        (workspace / "personal_assistant/data").mkdir(parents=True)
        (state / "agents/session-1").mkdir(parents=True)
        (workspace / "scripts/assistant.sh").write_text(
            "#!/bin/sh\nprintf tampered > /tmp/openclaw-tampered\n",
            encoding="utf-8",
        )
        (workspace / "mail_agent/__init__.py").write_text(
            "raise RuntimeError('state code loaded')\n",
            encoding="utf-8",
        )
        (workspace / "mail_agent/config.toml").write_text(
            "[mail]\naccount = 'fixture'\n",
            encoding="utf-8",
        )
        (workspace / "personal_assistant/tools.toml").write_text(
            "[nextcloud]\nenabled = false\n",
            encoding="utf-8",
        )
        (workspace / "mail_agent/data/mail_agent.sqlite3").write_bytes(b"fixture-db")
        (workspace / "mail_agent/data/corrections.json").write_text(
            "[]\n", encoding="utf-8"
        )
        (state / "agents/session-1/transcript.jsonl").write_text(
            "{}\n", encoding="utf-8"
        )
        (workspace / "LOCAL_NOTES.md").write_text("keep\n", encoding="utf-8")
        (state / "openclaw.json").write_text(
            '{"gateway":{"mode":"local"}}\n',
            encoding="utf-8",
        )
        return state, workspace

    def test_layout_migration_removes_runtime_code_and_preserves_instance_data(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            state, workspace = self._legacy_fixture(folder)
            report = migrate_layout(self.root, state, workspace)

            self.assertEqual(report.previous_layout, 1)
            self.assertEqual(report.layout, LAYOUT_VERSION)
            self.assertFalse((workspace / "scripts").exists())
            self.assertFalse((workspace / "mail_agent/__init__.py").exists())
            self.assertEqual(
                (workspace / "mail_agent/config.toml").read_text(encoding="utf-8"),
                "[mail]\naccount = 'fixture'\n",
            )
            self.assertEqual(
                (workspace / "personal_assistant/tools.toml").read_text(encoding="utf-8"),
                "[nextcloud]\nenabled = false\n",
            )
            self.assertEqual(
                (workspace / "mail_agent/data/mail_agent.sqlite3").read_bytes(),
                b"fixture-db",
            )
            self.assertTrue((state / "agents/session-1/transcript.jsonl").is_file())
            self.assertEqual(
                (workspace / "LOCAL_NOTES.md").read_text(encoding="utf-8"), "keep\n"
            )
            active = state / "v3/instance"
            self.assertFalse((workspace / "AGENTS.md").exists())
            self.assertEqual(
                os.readlink(active / "AGENTS.md"),
                str(self.root / "AGENTS.md"),
            )
            self.assertFalse((active / "skills/personal-assistant").exists())
            gateway = json.loads(
                (state / "v3/gateway/openclaw.json").read_text(encoding="utf-8")
            )
            self.assertEqual(
                gateway["skills"]["load"]["extraDirs"],
                ["/opt/openclaw-agent/skills"],
            )
            self.assertIsNotNone(report.backup)
            archive = Path(str(report.backup))
            digest = hashlib.sha256(archive.read_bytes()).hexdigest()
            self.assertEqual(digest, report.backup_sha256)
            self.assertIn(digest, archive.with_suffix(archive.suffix + ".sha256").read_text())
            with tarfile.open(archive, "r:gz") as handle:
                self.assertIn("MIGRATION_MANIFEST.json", handle.getnames())
                self.assertIn("workspace/scripts", handle.getnames())

    def test_restart_is_idempotent_and_does_not_change_configuration(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            state, workspace = self._legacy_fixture(folder)
            first = migrate_layout(self.root, state, workspace)
            config = workspace / "mail_agent/config.toml"
            before = (config.read_bytes(), config.stat().st_mtime_ns)
            second = migrate_layout(self.root, state, workspace)

            self.assertIsNotNone(first.backup)
            self.assertIsNone(second.backup)
            self.assertFalse(second.changed)
            self.assertEqual((config.read_bytes(), config.stat().st_mtime_ns), before)
            backups = list((state / ".layout-migrations/backups").glob("*.tar.gz"))
            self.assertEqual(len(backups), 1)

    def test_layout_normalizes_ollama_in_active_v3_instance_and_on_restart(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            state, workspace = self._legacy_fixture(folder)
            gateway_config = state / "openclaw.json"
            gateway_config.write_text(
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
            model_override = state / "agents/main/agent/models.json"
            model_override.parent.mkdir(parents=True)
            model_override.write_text(
                json.dumps(
                    {"providers": {"ollama": {"baseUrl": "http://127.0.0.1:11435"}}}
                ),
                encoding="utf-8",
            )
            legacy_config = workspace / "mail_agent/config.toml"
            legacy_config.write_text(
                '[ollama]\nbase_url = "http://127.0.0.1:11435"\nmodel = "fixture"\n',
                encoding="utf-8",
            )

            first = migrate_layout(self.root, state, workspace)
            active_config = state / "v3/instance/mail_agent/config.toml"
            active_gateway = state / "v3/gateway/openclaw.json"
            active_override = state / "v3/gateway/agents/main/agent/models.json"

            self.assertTrue(first.changed)
            self.assertIn(
                'base_url = "http://ollama-proxy:11435"',
                active_config.read_text(encoding="utf-8"),
            )
            self.assertIn(
                'base_url = "http://127.0.0.1:11435"',
                legacy_config.read_text(encoding="utf-8"),
            )
            self.assertEqual(
                json.loads(active_gateway.read_text(encoding="utf-8"))["models"]
                ["providers"]["ollama"]["baseUrl"],
                "http://ollama-proxy:11435",
            )
            self.assertEqual(
                json.loads(active_gateway.read_text(encoding="utf-8"))["models"]
                ["providers"]["ollama"]["timeoutSeconds"],
                1800,
            )
            self.assertEqual(
                json.loads(active_gateway.read_text(encoding="utf-8"))["agents"]
                ["defaults"]["timeoutSeconds"],
                3600,
            )
            self.assertEqual(
                json.loads(active_override.read_text(encoding="utf-8"))["providers"]
                ["ollama"]["baseUrl"],
                "http://ollama-proxy:11435",
            )
            self.assertEqual(
                json.loads(active_override.read_text(encoding="utf-8"))["providers"]
                ["ollama"]["timeoutSeconds"],
                1800,
            )

            active_config.write_text(
                active_config.read_text(encoding="utf-8").replace(
                    "http://ollama-proxy:11435",
                    "http://127.0.0.1:11435",
                ),
                encoding="utf-8",
            )
            gateway_payload = json.loads(active_gateway.read_text(encoding="utf-8"))
            gateway_payload["models"]["providers"]["ollama"]["baseUrl"] = (
                "http://127.0.0.1:11435"
            )
            active_gateway.write_text(json.dumps(gateway_payload), encoding="utf-8")
            override_payload = json.loads(active_override.read_text(encoding="utf-8"))
            override_payload["providers"]["ollama"]["baseUrl"] = (
                "http://127.0.0.1:11435"
            )
            active_override.write_text(json.dumps(override_payload), encoding="utf-8")
            second = migrate_layout(self.root, state, workspace)

            self.assertTrue(second.changed)
            self.assertIn(
                'base_url = "http://ollama-proxy:11435"',
                active_config.read_text(encoding="utf-8"),
            )
            self.assertEqual(
                json.loads(active_gateway.read_text(encoding="utf-8"))["models"]
                ["providers"]["ollama"]["baseUrl"],
                "http://ollama-proxy:11435",
            )
            self.assertEqual(
                json.loads(active_override.read_text(encoding="utf-8"))["providers"]
                ["ollama"]["baseUrl"],
                "http://ollama-proxy:11435",
            )

            third = migrate_layout(self.root, state, workspace)
            self.assertFalse(third.changed)

    def test_missing_release_document_fails_before_workspace_changes(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            state, workspace = self._legacy_fixture(folder)
            image = Path(folder) / "incomplete-image"
            image.mkdir()
            before = (workspace / "scripts/assistant.sh").read_bytes()

            with self.assertRaisesRegex(RuntimeError, "Release-Dokument fehlt"):
                migrate_layout(image, state, workspace)

            self.assertEqual((workspace / "scripts/assistant.sh").read_bytes(), before)
            self.assertFalse((state / ".container-layout.json").exists())
            self.assertFalse((state / ".layout-migrations").exists())

    def test_parallel_container_migrations_serialize_and_create_one_backup(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            state, workspace = self._legacy_fixture(folder)
            command = [
                sys.executable,
                "-m",
                "personal_assistant.runtime_layout",
                "migrate",
                "--image-root",
                str(self.root),
                "--state-root",
                str(state),
                "--workspace",
                str(workspace),
            ]

            def run() -> subprocess.CompletedProcess[str]:
                return subprocess.run(
                    command,
                    cwd=self.root,
                    text=True,
                    capture_output=True,
                    check=False,
                )

            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(lambda _: run(), range(2)))

            self.assertEqual([result.returncode for result in results], [0, 0])
            self.assertEqual(
                len(list((state / ".layout-migrations/backups").glob("*.tar.gz"))), 1
            )
            marker = json.loads(
                (state / ".container-layout.json").read_text(encoding="utf-8")
            )
            self.assertEqual(marker["layout"], LAYOUT_VERSION)

    def test_downgrade_to_unlabelled_pre_m2_image_fails_before_stop(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            state = Path(folder) / "state"
            state.mkdir()
            (state / ".container-layout.json").write_text(
                '{"schema": 1, "layout": 2}\n', encoding="utf-8"
            )
            result = subprocess.run(
                [
                    sys.executable,
                    str(self.root / "docker/scripts/check-layout-compatibility.py"),
                    "--state-dir",
                    str(state),
                    "--target-image",
                    "legacy:test",
                    "--target-min",
                    "1",
                    "--target-max",
                    "1",
                ],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(result.returncode, 1)
            self.assertIn("vor dem Stoppen", result.stdout)

    def test_m2_image_accepts_legacy_and_current_layouts(self) -> None:
        checker = self.root / "docker/scripts/check-layout-compatibility.py"
        with tempfile.TemporaryDirectory() as folder:
            state = Path(folder) / "state"
            state.mkdir()
            for layout in (1, 2):
                marker = state / ".container-layout.json"
                if layout == 1:
                    marker.unlink(missing_ok=True)
                else:
                    marker.write_text('{"layout": 2}\n', encoding="utf-8")
                result = subprocess.run(
                    [
                        sys.executable,
                        str(checker),
                        "--state-dir",
                        str(state),
                        "--target-image",
                        "m2:test",
                        "--target-min",
                        "1",
                        "--target-max",
                        "2",
                    ],
                    text=True,
                    capture_output=True,
                    check=False,
                )
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_worker_commands_use_only_image_scripts(self) -> None:
        code = (
            "from pathlib import Path; "
            "from docker.job_loop import config; "
            "import json; "
            "print(json.dumps(config('mail', Path('/state/workspace'), Path('/image'))[0]))"
        )
        result = subprocess.run(
            [sys.executable, "-c", code],
            cwd=self.root,
            text=True,
            capture_output=True,
            check=True,
        )
        command = json.loads(result.stdout)
        self.assertEqual(command[0], "/image/scripts/mail-agent.sh")
        self.assertNotIn("/state/workspace", " ".join(command))

    def test_runtime_identity_rejects_revision_and_state_code_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            image = root / "image"
            state = root / "state"
            workspace = state / "workspace"
            image.mkdir()
            workspace.mkdir(parents=True)
            (image / "SOURCE_REVISION").write_text("source-a\n", encoding="utf-8")
            (image / "VERSION").write_text("test\n", encoding="utf-8")
            (image / "RELEASE.json").write_text('{"version": "test"}\n', encoding="utf-8")
            with patch.dict(
                os.environ,
                {
                    "OPENCLAW_RUNTIME": "container",
                    "OPENCLAW_IMAGE_ROOT": str(image),
                    "OPENCLAW_CODE_ROOT": str(image),
                    "OPENCLAW_RELEASE_ROOT": str(image),
                    "OPENCLAW_STATE_ROOT": str(state),
                    "OPENCLAW_WORKSPACE": str(workspace),
                    "OPENCLAW_IMAGE_REVISION": "source-b",
                },
                clear=False,
            ):
                report = runtime_identity()

            self.assertFalse(report["ok"])
            self.assertEqual(report["layout"], 3)
            self.assertTrue(
                any("OCI-Revision" in issue for issue in report["issues"]), report
            )

    def test_container_config_does_not_create_secret_directory_in_read_only_home(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            secret = root / "read-only-home/.config/personal-assistant/secrets.env"
            config = AssistantConfig(
                runtime=RuntimeConfig(
                    database=root / "state/assistant.sqlite3",
                    log_file=root / "state/assistant.log",
                    secrets_file=secret,
                ),
                search=SearchConfig(mail_snapshot_dir=root / "state/search"),
            )

            with patch.dict(os.environ, {"OPENCLAW_RUNTIME": "container"}):
                config.ensure_dirs()

            self.assertTrue((root / "state").is_dir())
            self.assertTrue((root / "state/search").is_dir())
            self.assertFalse(secret.parent.exists())

    def test_container_workers_log_to_writable_coordination_directory(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            config_file = root / "config.toml"
            config_file.write_text("[runtime]\n", encoding="utf-8")
            core = root / "read-only-core"
            logs = root / "coordination/container_logs"
            for role in (
                "sync-worker",
                "supervisor-worker",
                "portfolio-worker",
                "monitor-worker",
            ):
                with patch.dict(
                    os.environ,
                    {
                        "OPENCLAW_RUNTIME": "container",
                        "OPENCLAW_ROLE": role,
                        "OPENCLAW_CORE_DATA_DIR": str(core),
                        "OPENCLAW_LOG_DIR": str(logs),
                    },
                    clear=False,
                ):
                    config = load_config(config_file)

                self.assertEqual(config.runtime.database, core / "assistant.sqlite3")
                self.assertEqual(config.runtime.resources_file, core / "resources.toml")
                self.assertEqual(
                    config.runtime.log_file,
                    logs / f"assistant-{role}.log",
                )
            self.assertFalse(core.exists())

    def test_compose_enforces_read_only_image_and_image_commands(self) -> None:
        compose = (self.root / "compose.yaml").read_text(encoding="utf-8")
        self.assertIn("read_only: true", compose)
        self.assertIn("/tmp:rw,nosuid,nodev,noexec", compose)
        self.assertNotIn(
            'command: ["/home/node/.openclaw/workspace/scripts/', compose
        )
        self.assertIn(
            'command: ["/opt/openclaw-agent/scripts/assistant.sh", "status"]', compose
        )


if __name__ == "__main__":
    unittest.main()
