from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from mail_agent.cli import _productive_checks_with_folder_self_heal
from personal_assistant.job_control import CommandResult, JobController, JobSpec
from personal_assistant.tool_registry import build_tool_registry
from personal_assistant.tool_settings import ToolSettings


class FakeSystem:
    def __init__(self) -> None:
        self.units: dict[str, dict[str, str]] = {}
        self.mail_missing = True
        self.mail_gate_stale = True
        self.dry_run_fail = False
        self.proxy_ok = True
        self.lock_held = False
        self.lock_checks = 0
        self.dry_run_lock_failures = 0
        self.reset_failed_returncode = 0
        self.openclaw_config = {
            "agents.defaults.heartbeat.target": "none",
            "agents.defaults.heartbeat.every": "30m",
        }
        self.commands: list[list[str]] = []

    def add_unit(self, name: str, *, active: str = "inactive", enabled: str = "disabled", result: str = "success", exec_status: str = "0") -> None:
        self.units[name] = {
            "LoadState": "loaded",
            "ActiveState": active,
            "SubState": "waiting" if name.endswith(".timer") and active == "active" else "dead",
            "UnitFileState": enabled,
            "Result": result,
            "ExecMainStatus": exec_status,
            "ExecMainStartTimestamp": "",
            "ExecMainExitTimestamp": "",
        }

    def __call__(self, command, timeout: int) -> CommandResult:
        args = list(command)
        self.commands.append(args)
        if args and args[0] == "env":
            index = 1
            while index < len(args) and "=" in args[index] and not args[index].startswith("/"):
                index += 1
            args = args[index:]
        if args[:3] == ["systemctl", "--user", "show"]:
            unit = args[3]
            data = self.units.get(unit)
            if data is None:
                return CommandResult(1, "LoadState=not-found\n", "Unit not found")
            return CommandResult(0, "".join(f"{key}={value}\n" for key, value in data.items()), "")
        if args[:3] == ["systemctl", "--user", "daemon-reload"]:
            return CommandResult(0, "", "")
        if args[:3] == ["systemctl", "--user", "reset-failed"]:
            unit = args[3]
            if unit in self.units:
                self.units[unit]["Result"] = "success"
                self.units[unit]["ActiveState"] = "inactive" if unit.endswith(".service") else self.units[unit]["ActiveState"]
            return CommandResult(
                self.reset_failed_returncode,
                "",
                "Unit not loaded" if self.reset_failed_returncode else "",
            )
        if args[:4] == ["systemctl", "--user", "enable", "--now"]:
            unit = args[4]
            self.add_unit(unit, active="active", enabled="enabled")
            return CommandResult(0, "", "")
        if args[:3] == ["systemctl", "--user", "restart"]:
            unit = args[3]
            self.add_unit(unit, active="active", enabled="enabled")
            return CommandResult(0, "", "")
        if args[:4] == ["systemctl", "--user", "start", "--no-block"]:
            unit = args[4]
            self.add_unit(unit, active="inactive", enabled="static")
            return CommandResult(0, "", "")
        if args[:3] == ["systemctl", "--user", "stop"]:
            unit = args[3]
            self.add_unit(unit, active="inactive", enabled=self.units.get(unit, {}).get("UnitFileState", "static"))
            return CommandResult(0, "", "")
        if args[:4] == ["systemctl", "--user", "disable", "--now"]:
            unit = args[4]
            self.add_unit(unit, active="inactive", enabled="disabled")
            return CommandResult(0, "", "")
        if args[:3] == ["openclaw", "config", "get"]:
            path = args[3]
            if path not in self.openclaw_config:
                return CommandResult(1, json.dumps({"error": "not found"}), "")
            return CommandResult(0, json.dumps(self.openclaw_config[path]), "")
        if args[:3] == ["openclaw", "config", "set"]:
            path = args[3]
            self.openclaw_config[path] = json.loads(args[4])
            return CommandResult(0, "updated", "")
        if args[:3] == ["openclaw", "config", "validate"]:
            return CommandResult(0, json.dumps({"ok": True}), "")
        if args[:3] == ["openclaw", "system", "event"]:
            return CommandResult(0, json.dumps({"ok": True}), "")
        if args and args[0].endswith("scripts/mail-agent.sh") and args[1:] == ["production-check"]:
            payload = {
                "ok": not self.mail_gate_stale,
                "blockers": (["Konfiguration oder Regeln wurden seit dem letzten Dry-Run geaendert"] if self.mail_gate_stale else []),
                "auto_recoverable": self.mail_gate_stale,
                "gate": {
                    "last_dry_run_ok": True,
                    "stored_fingerprint": "old" if self.mail_gate_stale else "current",
                    "current_fingerprint": "current",
                    "fingerprint_matches": not self.mail_gate_stale,
                },
            }
            return CommandResult(4 if self.mail_gate_stale else 0, json.dumps(payload), "")
        if args and args[0].endswith("scripts/mail-agent.sh") and args[1:] == ["lock-status"]:
            self.lock_checks += 1
            payload = {
                "ok": True,
                "locked": self.lock_held,
                "pid": 1234 if self.lock_held else None,
                "process_alive": self.lock_held,
                "detail": "locked" if self.lock_held else "free",
            }
            return CommandResult(3 if self.lock_held else 0, json.dumps(payload), "")
        if args and args[0].endswith("scripts/mail-agent.sh") and args[1:] == ["run", "--dry-run", "--no-digest", "--limit", "5"]:
            if self.dry_run_lock_failures > 0:
                self.dry_run_lock_failures -= 1
                return CommandResult(3, "", "Ein anderer Mail-Interface-Lauf ist bereits aktiv: lock")
            if self.dry_run_fail:
                return CommandResult(1, json.dumps({"processed": 1, "actions": [], "errors": ["test failure"]}), "")
            self.mail_gate_stale = False
            return CommandResult(0, json.dumps({
                "processed": 5,
                "actions": [{"ok": True, "status": "dry-run"}],
                "errors": [],
            }), "")
        if args and args[0].endswith("scripts/mail-agent.sh") and args[1:] == ["doctor"]:
            payload = {
                "himalaya": {"ok": True},
                "folders": {"ok": not self.mail_missing, "missing": ["Agent/Korrektur-Spam"] if self.mail_missing else []},
            }
            return CommandResult(1 if self.mail_missing else 0, json.dumps(payload), "")
        if args and args[0].endswith("scripts/mail-agent.sh") and args[1:] == ["setup"]:
            self.mail_missing = False
            return CommandResult(0, "[]", "")
        if args and args[0].endswith("scripts/ollama-priority-proxy.sh") and args[1:] == ["status"]:
            payload = {"ok": self.proxy_ok, "detail": "proxy ok" if self.proxy_ok else "proxy unavailable"}
            return CommandResult(0 if self.proxy_ok else 1, json.dumps(payload), "")
        if args[:2] == ["journalctl", "--user"]:
            return CommandResult(0, "test journal", "")
        return CommandResult(0, "", "")


class FakeMailAgent:
    def __init__(self) -> None:
        self.missing = True
        self.setup_calls = 0

    def doctor(self):
        return {"folders": {"ok": not self.missing, "missing": ["Agent/Korrektur-Spam"] if self.missing else []}}

    def setup(self):
        self.setup_calls += 1
        self.missing = False
        return []


class JobControlTests(unittest.TestCase):
    def setUp(self) -> None:
        self.legacy_environment = patch.dict(
            "os.environ", {"OPENCLAW_ENABLE_LEGACY_SYSTEMD": "YES"}, clear=False
        )
        self.legacy_environment.start()
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.workspace = self.root / "workspace"
        self.unit_dir = self.root / "units"
        (self.workspace / "legacy/systemd/units").mkdir(parents=True)
        for unit in ("mail-agent.service", "mail-agent.timer"):
            (self.workspace / "legacy/systemd/units" / unit).write_text(
                "[Unit]\nDescription=Test\n", encoding="utf-8"
            )
        self.spec = JobSpec(
            name="mail",
            description="Mail",
            timer_unit="mail-agent.timer",
            service_unit="mail-agent.service",
            default_on=True,
            standard=True,
            health_command=("scripts/mail-agent.sh", "doctor"),
            repair_command=("scripts/mail-agent.sh", "setup"),
            readiness_command=("scripts/mail-agent.sh", "production-check"),
            automatic_recovery_command=(
                "scripts/mail-agent.sh", "run", "--dry-run", "--no-digest", "--limit", "5",
            ),
        )
        self.system = FakeSystem()
        self.system.add_unit("mail-agent.timer", active="inactive", enabled="disabled")
        self.system.add_unit("mail-agent.service", active="inactive", enabled="static")
        self.controller = JobController(
            state_path=self.root / "job-control.json",
            workspace_root=self.workspace,
            unit_dir=self.unit_dir,
            runner=self.system,
            specs=(self.spec,),
            sleeper=lambda _seconds: None,
        )

    def tearDown(self) -> None:
        self.temp.cleanup()
        self.legacy_environment.stop()

    def test_always_health_reports_priority_proxy_failure(self) -> None:
        spec = JobSpec(
            name="supervisor",
            description="Supervisor",
            timer_unit="personal-assistant-supervisor.timer",
            service_unit="personal-assistant-supervisor.service",
            default_on=True,
            standard=True,
            health_command=("scripts/ollama-priority-proxy.sh", "status"),
            always_health=True,
        )
        self.system.add_unit("personal-assistant-supervisor.timer", active="active", enabled="enabled")
        self.system.add_unit("personal-assistant-supervisor.service", active="inactive", enabled="disabled")
        self.system.proxy_ok = False
        controller = JobController(
            state_path=self.root / "supervisor-control.json",
            workspace_root=self.workspace,
            unit_dir=self.unit_dir,
            runner=self.system,
            specs=(spec,),
            sleeper=lambda _seconds: None,
        )
        report = controller.status(target="supervisor")
        self.assertFalse(report["ok"])
        codes = {item["code"] for item in report["jobs"][0]["issues"]}
        self.assertIn("health-check-failed", codes)

    def test_container_health_resolves_release_script_outside_workspace(self) -> None:
        spec = JobSpec(
            name="supervisor",
            description="Supervisor",
            timer_unit="personal-assistant-supervisor.timer",
            service_unit="personal-assistant-supervisor.service",
            default_on=True,
            standard=True,
            health_command=("scripts/ollama-priority-proxy.sh", "status"),
            always_health=True,
        )
        image_root = self.root / "immutable-image"
        writable_workspace = self.root / "writable-state" / "workspace"
        with patch.dict(
            "os.environ",
            {
                "OPENCLAW_RUNTIME": "container",
                "OPENCLAW_IMAGE_ROOT": str(image_root),
            },
            clear=False,
        ):
            controller = JobController(
                state_path=self.root / "container-supervisor-control.json",
                workspace_root=writable_workspace,
                unit_dir=self.unit_dir,
                runner=self.system,
                specs=(spec,),
                sleeper=lambda _seconds: None,
            )
            health = controller._health(spec)
        self.assertTrue(health["ok"])
        self.assertEqual(
            self.system.commands[-1],
            [str(image_root / "scripts/ollama-priority-proxy.sh"), "status"],
        )
        self.assertNotIn(str(writable_workspace), self.system.commands[-1][0])

    def test_supervisor_reports_scheduler_database_failure(self) -> None:
        spec = JobSpec(
            name="supervisor",
            description="Supervisor",
            timer_unit="personal-assistant-supervisor.timer",
            service_unit="personal-assistant-supervisor.service",
            default_on=True,
            standard=True,
        )
        self.system.add_unit(spec.timer_unit, active="active", enabled="enabled")
        self.system.add_unit(spec.service_unit, active="inactive", enabled="static")
        self.system.openclaw_config["agents.defaults.heartbeat.target"] = "last"
        scheduler_path = self.workspace / "personal_assistant/data/work_scheduler.sqlite3"
        scheduler_path.parent.mkdir(parents=True)
        scheduler_path.write_bytes(b"not a sqlite database")
        controller = JobController(
            state_path=self.root / "supervisor-scheduler-control.json",
            workspace_root=self.workspace,
            unit_dir=self.unit_dir,
            runner=self.system,
            specs=(spec,),
            sleeper=lambda _seconds: None,
        )

        report = controller.status(target="supervisor")

        self.assertFalse(report["ok"])
        issues = report["jobs"][0]["issues"]
        self.assertIn("scheduler-health-failed", {item["code"] for item in issues})

    def test_check_records_unexpected_off_state(self) -> None:
        report = self.controller.status(target="all", record=True)
        self.assertFalse(report["ok"])
        codes = {item["code"] for item in report["jobs"][0]["issues"]}
        self.assertIn("timer-disabled", codes)
        self.assertIn("timer-inactive", codes)
        self.assertTrue(report["new_alerts"])
        self.assertTrue(report["notification"]["attempted"])
        event_calls = [cmd for cmd in self.system.commands if cmd[:3] == ["openclaw", "system", "event"]]
        self.assertEqual(len(event_calls), 1)

    def test_repeated_same_alert_does_not_wake_openclaw_again(self) -> None:
        self.controller.status(target="all", record=True)
        first_count = len([cmd for cmd in self.system.commands if cmd[:3] == ["openclaw", "system", "event"]])
        report = self.controller.status(target="all", record=True)
        second_count = len([cmd for cmd in self.system.commands if cmd[:3] == ["openclaw", "system", "event"]])
        self.assertEqual(first_count, 1)
        self.assertEqual(second_count, 1)
        self.assertFalse(report["notification"]["attempted"])

    def test_on_repairs_missing_mail_folder_and_enables_timer(self) -> None:
        result = self.controller.on(target="mail", run_now=True)
        self.assertTrue(result["ok"])
        action = result["actions"][0]
        self.assertTrue(action["repair"]["attempted"])
        self.assertFalse(self.system.mail_missing)
        timer = self.system.units["mail-agent.timer"]
        self.assertEqual(timer["ActiveState"], "active")
        self.assertEqual(timer["UnitFileState"], "enabled")

    def test_on_ignores_reset_failed_for_not_yet_loaded_units(self) -> None:
        self.system.reset_failed_returncode = 1
        result = self.controller.on(target="mail", run_now=True)
        self.assertTrue(result["ok"])
        resets = [
            item
            for item in result["actions"][0]["commands"]
            if item["command"].startswith("reset-failed ")
        ]
        self.assertEqual(len(resets), 2)
        self.assertTrue(all(item["required"] is False for item in resets))
        self.assertTrue(all(item["returncode"] == 1 for item in resets))


    def test_check_auto_recovers_stale_dry_run_and_restarts_mail(self) -> None:
        self.system.mail_missing = False
        self.system.add_unit("mail-agent.timer", active="active", enabled="enabled")
        self.system.add_unit(
            "mail-agent.service", active="failed", enabled="disabled", result="exit-code", exec_status="4",
        )
        result = self.controller.check(target="mail", deep=True)
        self.assertTrue(result["ok"])
        self.assertFalse(self.system.mail_gate_stale)
        self.assertEqual(len(result["automatic_recoveries"]), 1)
        recovery = result["automatic_recoveries"][0]
        self.assertTrue(recovery["recovery"]["attempted"])
        self.assertTrue(recovery["recovery"]["ok"])
        self.assertTrue(recovery["restart"]["ok"])
        dry_runs = [
            command for command in self.system.commands
            if any(item.endswith("scripts/mail-agent.sh") for item in command) and "--dry-run" in command
        ]
        self.assertEqual(len(dry_runs), 1)
        self.assertIn("OPENCLAW_OLLAMA_PRIORITY=maintenance", dry_runs[0])
        self.assertIn("OPENCLAW_OLLAMA_SOURCE=supervisor-recovery", dry_runs[0])

    def test_failed_automatic_dry_run_is_not_repeated_during_cooldown(self) -> None:
        self.system.mail_missing = False
        self.system.dry_run_fail = True
        self.system.add_unit("mail-agent.timer", active="active", enabled="enabled")
        self.system.add_unit(
            "mail-agent.service", active="failed", enabled="disabled", result="exit-code", exec_status="4",
        )
        first = self.controller.check(target="mail", deep=True)
        second = self.controller.check(target="mail", deep=True)
        self.assertFalse(first["ok"])
        self.assertFalse(second["ok"])
        dry_runs = [
            command for command in self.system.commands
            if any(item.endswith("scripts/mail-agent.sh") for item in command) and "--dry-run" in command
        ]
        self.assertEqual(len(dry_runs), 1)
        self.assertTrue(second["automatic_recoveries"][0]["recovery"].get("cooldown"))

    def test_recovery_stops_timer_and_service_before_dry_run(self) -> None:
        self.system.mail_missing = False
        self.system.add_unit("mail-agent.timer", active="active", enabled="enabled")
        self.system.add_unit(
            "mail-agent.service", active="failed", enabled="disabled", result="exit-code", exec_status="4",
        )
        result = self.controller.check(target="mail", deep=True)
        self.assertTrue(result["ok"])
        dry_index = next(
            index for index, command in enumerate(self.system.commands)
            if any(item.endswith("scripts/mail-agent.sh") for item in command) and "--dry-run" in command
        )
        timer_stop = next(index for index, command in enumerate(self.system.commands) if command[:4] == ["systemctl", "--user", "stop", "mail-agent.timer"])
        service_stop = next(index for index, command in enumerate(self.system.commands) if command[:4] == ["systemctl", "--user", "stop", "mail-agent.service"])
        self.assertLess(timer_stop, dry_index)
        self.assertLess(service_stop, dry_index)
        self.assertGreaterEqual(self.system.lock_checks, 2)

    def test_transient_lock_does_not_create_cooldown(self) -> None:
        self.system.mail_missing = False
        self.system.lock_held = True
        self.system.add_unit("mail-agent.timer", active="active", enabled="enabled")
        self.system.add_unit(
            "mail-agent.service", active="failed", enabled="disabled", result="exit-code", exec_status="4",
        )
        first = self.controller.check(target="mail", deep=True)
        recovery = first["automatic_recoveries"][0]["recovery"]
        self.assertTrue(recovery.get("transient"))
        self.assertNotIn("mail-production-gate", self.controller.state.get("recovery", {}))
        self.system.lock_held = False
        second = self.controller.check(target="mail", deep=True)
        self.assertTrue(second["ok"])
        self.assertFalse(second["automatic_recoveries"][0]["recovery"].get("cooldown", False))

    def test_dry_run_lock_race_is_retried_without_cooldown(self) -> None:
        self.system.mail_missing = False
        self.system.dry_run_lock_failures = 1
        self.system.add_unit("mail-agent.timer", active="active", enabled="enabled")
        self.system.add_unit(
            "mail-agent.service", active="failed", enabled="disabled", result="exit-code", exec_status="4",
        )
        result = self.controller.check(target="mail", deep=True)
        self.assertTrue(result["ok"])
        dry_runs = [
            command for command in self.system.commands
            if any(item.endswith("scripts/mail-agent.sh") for item in command) and "--dry-run" in command
        ]
        self.assertEqual(len(dry_runs), 2)
        self.assertTrue(result["automatic_recoveries"][0]["recovery"]["ok"])

    def test_deliberate_off_is_not_an_active_failure_alert(self) -> None:
        self.controller.on(target="mail", run_now=False)
        result = self.controller.off(target="mail")
        self.assertTrue(result["ok"])
        self.assertEqual(self.controller.state["desired"]["mail"], False)
        self.assertEqual(self.controller.alerts()["active_alerts"], [])

    def test_productive_mail_preflight_self_heals_only_folders(self) -> None:
        agent = FakeMailAgent()
        checks = _productive_checks_with_folder_self_heal(agent)
        self.assertEqual(agent.setup_calls, 1)
        self.assertTrue(checks["folders"]["ok"])

    def test_supervisor_on_enables_heartbeat_delivery(self) -> None:
        for unit in ("personal-assistant-supervisor.service", "personal-assistant-supervisor.timer"):
            (self.workspace / "legacy/systemd/units" / unit).write_text(
                "[Unit]\nDescription=Test\n", encoding="utf-8"
            )
        spec = JobSpec(
            name="supervisor",
            description="Supervisor",
            timer_unit="personal-assistant-supervisor.timer",
            service_unit="personal-assistant-supervisor.service",
            default_on=True,
            standard=True,
        )
        self.system.add_unit(spec.timer_unit, active="inactive", enabled="disabled")
        self.system.add_unit(spec.service_unit, active="inactive", enabled="static")
        controller = JobController(
            state_path=self.root / "supervisor-state.json",
            workspace_root=self.workspace,
            unit_dir=self.unit_dir,
            runner=self.system,
            specs=(spec,),
            sleeper=lambda _seconds: None,
        )
        result = controller.on(target="supervisor", run_now=False)
        self.assertTrue(result["ok"])
        self.assertEqual(self.system.openclaw_config["agents.defaults.heartbeat.target"], "last")
        self.assertTrue(self.system.openclaw_config["agents.defaults.heartbeat.lightContext"])
        self.assertTrue(self.system.openclaw_config["agents.defaults.heartbeat.isolatedSession"])

    def test_registry_exposes_job_switch_and_diagnostics(self) -> None:
        ids = {item.id for item in build_tool_registry(ToolSettings(path=self.root / "tools.toml"))}
        for expected in (
            "assistant.jobs.status",
            "assistant.jobs.check",
            "assistant.jobs.alerts",
            "assistant.jobs.on",
            "assistant.jobs.restart",
            "assistant.jobs.off",
            "assistant.ollama.status",
            "assistant.ollama.check",
            "assistant.ollama.queue",
            "assistant.ollama.start",
            "assistant.ollama.restart",
            "assistant.performance.mail",
        ):
            self.assertIn(expected, ids)


if __name__ == "__main__":
    unittest.main()
