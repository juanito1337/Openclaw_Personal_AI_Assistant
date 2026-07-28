from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from personal_assistant.job_control import CommandResult, JobController


class ContainerWorkspaceTests(unittest.TestCase):
    def test_workspace_environment_controls_both_packages(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            environment = os.environ.copy()
            environment["OPENCLAW_WORKSPACE"] = folder
            code = (
                "from mail_agent.config import WORKSPACE_ROOT as a; "
                "from personal_assistant.config import WORKSPACE_ROOT as b; "
                "print(a); print(b)"
            )
            result = subprocess.run(
                [sys.executable, "-c", code],
                cwd=Path(__file__).resolve().parents[1],
                env=environment,
                text=True,
                capture_output=True,
                check=True,
            )
            self.assertEqual(result.stdout.splitlines(), [folder, folder])

    def test_container_job_status_uses_worker_heartbeat(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            status_dir = root / "personal_assistant/data/container_jobs"
            status_dir.mkdir(parents=True)
            (status_dir / "mail.json").write_text(
                json.dumps(
                    {
                        "updated_at": datetime.now(timezone.utc).isoformat(),
                        "state": "waiting",
                        "result": "success",
                        "last_exit_code": 0,
                    }
                ),
                encoding="utf-8",
            )

            def runner(command: list[str], timeout: int) -> CommandResult:
                del command, timeout
                return CommandResult(0, '{"ok": true}', "")

            with patch.dict(
                os.environ,
                {
                    "OPENCLAW_RUNTIME": "container",
                    "OPENCLAW_JOB_STATUS_DIR": str(status_dir),
                },
            ):
                controller = JobController(
                    workspace_root=root,
                    state_path=root / "personal_assistant/data/job_control.json",
                    runner=runner,
                )
                report = controller.status(target="mail")
                self.assertTrue(report["ok"])
                self.assertEqual(report["jobs"][0]["state"], "on")
                stopped = controller.off(target="mail")
                self.assertTrue(stopped["ok"])
                self.assertEqual(stopped["status"]["jobs"][1]["desired"], "off")

    def test_clamav_updater_has_its_own_database_healthcheck(self) -> None:
        compose = (Path(__file__).resolve().parents[1] / "compose.yaml").read_text(encoding="utf-8")
        clamav = compose.split("  clamav-update:\n", 1)[1].split("\nvolumes:\n", 1)[0]
        self.assertIn("/var/lib/clamav/main.cvd", clamav)
        self.assertIn("/var/lib/clamav/daily.cvd", clamav)
        self.assertIn("/var/lib/clamav/bytecode.cvd", clamav)
        self.assertNotIn("127.0.0.1:18789", clamav)

    def test_entrypoint_builds_runtime_ca_bundle_from_public_crt_files(self) -> None:
        entrypoint = (Path(__file__).resolve().parents[1] / "docker/entrypoint.sh").read_text(encoding="utf-8")
        self.assertIn("configure_custom_ca", entrypoint)
        self.assertIn("SSL_CERT_FILE", entrypoint)
        self.assertIn("REQUESTS_CA_BUNDLE", entrypoint)
        self.assertIn("NODE_EXTRA_CA_CERTS", entrypoint)
        self.assertIn("-name '*.crt'", entrypoint)


if __name__ == "__main__":
    unittest.main()
