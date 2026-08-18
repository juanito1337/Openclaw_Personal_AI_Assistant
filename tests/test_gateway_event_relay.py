from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

from personal_assistant.cli import _jobs_result_ok
from personal_assistant.gateway_events import (
    enqueue_event,
    event_command,
    recover_processing,
    relay_once,
    relay_status,
)
from personal_assistant.job_control import CommandResult, JobController, JobSpec
from personal_assistant.mail_worker import run_cycle


class GatewayEventRelayTests(unittest.TestCase):
    def test_worker_command_queues_without_gateway_url_or_credential(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            environment = {
                "OPENCLAW_EVENT_QUEUE_DIR": folder,
                "OPENCLAW_GATEWAY_PASSWORD": "must-not-appear",
                "OPENCLAW_GATEWAY_URL": "ws://gateway:18789",
            }
            with patch.dict(os.environ, environment, clear=False):
                command = event_command("Statuswechsel")
            self.assertEqual(
                command[:5],
                ["python3", "-P", "-m", "personal_assistant.gateway_events", "enqueue"],
            )
            self.assertNotIn("--url", command)
            self.assertNotIn(environment["OPENCLAW_GATEWAY_PASSWORD"], command)

    def test_relay_delivers_only_through_loopback_and_removes_claim(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            enqueue_event("Technischer Test", source="supervisor-worker", root=root)
            calls: list[tuple[list[str], dict[str, str]]] = []

            def runner(command: Sequence[str], environment: dict[str, str]) -> int:
                calls.append((list(command), environment))
                return 0

            report = relay_once(root, runner=runner)

            self.assertTrue(report["ok"])
            self.assertEqual(report["delivered"], 1)
            self.assertEqual(calls[0][1]["OPENCLAW_GATEWAY_URL"], "ws://127.0.0.1:18789")
            self.assertNotIn("OPENCLAW_ALLOW_INSECURE_PRIVATE_WS", calls[0][1])
            self.assertNotIn("--url", calls[0][0])
            self.assertEqual(list((root / "pending").glob("*.json")), [])
            self.assertTrue(relay_status(root)["ok"])

    def test_tampered_entry_is_failed_without_invoking_gateway(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            pending = root / "pending"
            pending.mkdir()
            (pending / "wrong.json").write_text(
                json.dumps(
                    {
                        "schema": 1,
                        "id": "different",
                        "created_at": datetime.now(UTC).isoformat(),
                        "source": "test",
                        "text": "payload",
                        "attempts": 0,
                    }
                ),
                encoding="utf-8",
            )
            invoked = False

            def runner(_command: Sequence[str], _environment: dict[str, str]) -> int:
                nonlocal invoked
                invoked = True
                return 0

            report = relay_once(root, runner=runner)

            self.assertFalse(report["ok"])
            self.assertFalse(invoked)
            self.assertTrue((root / "failed/wrong.json").is_file())

    def test_queue_is_bounded(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            with patch("personal_assistant.gateway_events.MAX_PENDING_EVENTS", 1):
                enqueue_event("eins", root=root)
                with self.assertRaisesRegex(RuntimeError, "voll"):
                    enqueue_event("zwei", root=root)

    def test_relay_restart_recovers_an_event_left_in_processing(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            enqueue_event("Nach Neustart zustellen", root=root)
            pending = next((root / "pending").glob("*.json"))
            processing = root / "processing"
            processing.mkdir()
            pending.replace(processing / pending.name)

            self.assertEqual(recover_processing(root), 1)
            report = relay_once(root, runner=lambda _command, _environment: 0)

            self.assertTrue(report["ok"])
            self.assertEqual(report["delivered"], 1)
            self.assertEqual(list(processing.glob("*.json")), [])


class MailWorkerRecoveryTests(unittest.TestCase):
    @staticmethod
    def _completed(returncode: int, payload: dict[str, object]) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess([], returncode, json.dumps(payload), "")

    def test_mail_worker_recovers_gate_before_productive_command(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            stale = {
                "ok": False,
                "auto_recoverable": True,
                "blockers": ["Konfiguration oder Regeln wurden seit dem letzten Dry-Run geaendert"],
                "gate": {"current_fingerprint": "new", "stored_fingerprint": "old"},
            }
            sequence = [
                self._completed(4, stale),
                self._completed(0, {"processed": 5, "errors": [], "actions": [{"ok": True}]}),
                self._completed(0, {"ok": True, "blockers": [], "gate": {}}),
            ]
            calls: list[tuple[list[str], dict[str, str]]] = []

            def capture(
                command: Sequence[str], *, environment: dict[str, str]
            ) -> subprocess.CompletedProcess[str]:
                calls.append((list(command), environment))
                return sequence.pop(0)

            with (
                patch("personal_assistant.mail_worker._run_capture", side_effect=capture),
                patch("personal_assistant.mail_worker._run_productive", return_value=0) as productive,
            ):
                result = run_cycle(
                    ["mail-agent", "run"],
                    mail_agent="/image/scripts/mail-agent.sh",
                    environment={"OPENCLAW_ROLE": "mail-worker"},
                    state_path=Path(folder) / "recovery.json",
                )

            self.assertEqual(result, 0)
            productive.assert_called_once()
            self.assertEqual(calls[1][0][-5:], ["run", "--dry-run", "--no-digest", "--limit", "5"])
            self.assertEqual(calls[1][1]["OPENCLAW_OLLAMA_PRIORITY"], "maintenance")
            self.assertEqual(calls[1][1]["OPENCLAW_OLLAMA_SOURCE"], "mail-worker-recovery")

    def test_non_allowlisted_gate_never_runs_recovery_or_productive_mail(self) -> None:
        blocked = self._completed(
            4,
            {
                "ok": False,
                "auto_recoverable": False,
                "blockers": ["Antivirus nicht verfuegbar"],
                "gate": {},
            },
        )
        with (
            patch("personal_assistant.mail_worker._run_capture", return_value=blocked) as capture,
            patch("personal_assistant.mail_worker._run_productive") as productive,
        ):
            result = run_cycle(
                ["mail-agent", "run"],
                mail_agent="/image/scripts/mail-agent.sh",
                environment={},
            )
        self.assertEqual(result, 4)
        self.assertEqual(capture.call_count, 1)
        productive.assert_not_called()


class SupervisorOwnershipTests(unittest.TestCase):
    def test_container_supervisor_observes_mail_without_opening_mail_state(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            status_dir = root / "container_jobs"
            status_dir.mkdir()
            now = datetime.now(UTC).isoformat()
            for name in ("supervisor", "mail"):
                (status_dir / f"{name}.json").write_text(
                    json.dumps(
                        {
                            "state": "running" if name == "supervisor" else "waiting",
                            "updated_at": now,
                            "result": "success",
                            "last_exit_code": 0,
                        }
                    ),
                    encoding="utf-8",
                )
            queue = root / "events"
            queue.mkdir()
            (queue / "relay-status.json").write_text(
                json.dumps(
                    {
                        "schema": 1,
                        "ok": True,
                        "state": "running",
                        "updated_at": now,
                        "pending": 0,
                        "failed": 0,
                    }
                ),
                encoding="utf-8",
            )
            commands: list[list[str]] = []

            def runner(command: Sequence[str], _timeout: int) -> CommandResult:
                commands.append(list(command))
                return CommandResult(0, '{"ok": true}', "")

            specs = (
                JobSpec(
                    name="supervisor",
                    description="Supervisor",
                    timer_unit="personal-assistant-supervisor.timer",
                    service_unit="personal-assistant-supervisor.service",
                    default_on=True,
                    standard=True,
                ),
                JobSpec(
                    name="mail",
                    description="Mail",
                    timer_unit="mail-agent.timer",
                    service_unit="mail-agent.service",
                    default_on=True,
                    standard=True,
                    health_command=("scripts/mail-agent.sh", "doctor"),
                    readiness_command=("scripts/mail-agent.sh", "production-check"),
                    automatic_recovery_command=(
                        "scripts/mail-agent.sh",
                        "run",
                        "--dry-run",
                        "--no-digest",
                        "--limit",
                        "5",
                    ),
                ),
            )
            environment = {
                "OPENCLAW_RUNTIME": "container",
                "OPENCLAW_ROLE": "supervisor-worker",
                "OPENCLAW_JOB_STATUS_DIR": str(status_dir),
                "OPENCLAW_COORDINATION_DATA_DIR": str(root),
                "OPENCLAW_EVENT_QUEUE_DIR": str(queue),
            }
            with patch.dict(os.environ, environment, clear=False):
                controller = JobController(
                    state_path=root / "job_control.json",
                    workspace_root=root / "workspace",
                    runner=runner,
                    specs=specs,
                )
                report = controller.check(target="all", deep=True)

            self.assertTrue(report["ok"], report)
            self.assertEqual(report["automatic_recovery_owner"], "mail-worker")
            self.assertFalse(any("mail-agent.sh" in " ".join(command) for command in commands))
            self.assertFalse(any(command[:3] == ["openclaw", "config", "get"] for command in commands))

    def test_business_degradation_does_not_fail_the_observer_cycle(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            status_dir = root / "container_jobs"
            status_dir.mkdir()
            now = datetime.now(UTC).isoformat()
            for name, result, returncode in (
                ("supervisor", "success", 0),
                ("portfolio", "degraded", 1),
            ):
                (status_dir / f"{name}.json").write_text(
                    json.dumps(
                        {
                            "state": "running" if name == "supervisor" else "waiting",
                            "updated_at": now,
                            "result": result,
                            "last_exit_code": returncode,
                        }
                    ),
                    encoding="utf-8",
                )
            queue = root / "events"
            queue.mkdir()
            (queue / "relay-status.json").write_text(
                json.dumps(
                    {
                        "schema": 1,
                        "ok": True,
                        "state": "running",
                        "updated_at": now,
                        "pending": 0,
                        "failed": 0,
                    }
                ),
                encoding="utf-8",
            )
            specs = (
                JobSpec(
                    name="supervisor",
                    description="Supervisor",
                    timer_unit="personal-assistant-supervisor.timer",
                    service_unit="personal-assistant-supervisor.service",
                    default_on=True,
                    standard=True,
                ),
                JobSpec(
                    name="portfolio",
                    description="Portfolio",
                    timer_unit="personal-assistant-portfolio.timer",
                    service_unit="personal-assistant-portfolio.service",
                    default_on=True,
                    standard=False,
                ),
            )

            def runner(_command: Sequence[str], _timeout: int) -> CommandResult:
                return CommandResult(0, '{"ok": true}', "")

            environment = {
                "OPENCLAW_RUNTIME": "container",
                "OPENCLAW_ROLE": "supervisor-worker",
                "OPENCLAW_JOB_STATUS_DIR": str(status_dir),
                "OPENCLAW_COORDINATION_DATA_DIR": str(root),
                "OPENCLAW_EVENT_QUEUE_DIR": str(queue),
            }
            with patch.dict(os.environ, environment, clear=False):
                controller = JobController(
                    state_path=root / "job_control.json",
                    workspace_root=root / "workspace",
                    runner=runner,
                    specs=specs,
                )
                report = controller.check(target="all")

            self.assertFalse(report["ok"])
            self.assertTrue(report["observer_cycle"]["ok"])
            self.assertFalse(report["observer_cycle"]["observed_jobs_ok"])
            self.assertTrue(_jobs_result_ok(report))
            self.assertEqual(
                {alert["id"] for alert in report["new_alerts"]},
                {"portfolio:service-degraded"},
            )
            broken_observer = {
                **report,
                "observer_cycle": {"ok": False},
            }
            self.assertFalse(_jobs_result_ok(broken_observer))


if __name__ == "__main__":
    unittest.main()
