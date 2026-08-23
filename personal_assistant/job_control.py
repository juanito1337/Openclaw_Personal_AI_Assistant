from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import time
from collections.abc import Callable, Iterable, Sequence
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from .config import WORKSPACE_ROOT
from .gateway_events import event_command, relay_status
from .work_scheduler import AdaptiveWorkScheduler

STATE_VERSION = 2
AUTO_RECOVERY_COOLDOWN = timedelta(minutes=30)
DEFAULT_STATE_PATH = WORKSPACE_ROOT / "personal_assistant/data/job_control.json"
USER_UNIT_DIR = Path("~/.config/systemd/user").expanduser()


def _system_event_command(text: str) -> list[str]:
    return event_command(text)


@dataclass(frozen=True, slots=True)
class JobSpec:
    name: str
    description: str
    timer_unit: str
    service_unit: str
    default_on: bool
    standard: bool
    health_command: tuple[str, ...] = ()
    repair_command: tuple[str, ...] = ()
    readiness_command: tuple[str, ...] = ()
    automatic_recovery_command: tuple[str, ...] = ()
    always_health: bool = False


@dataclass(frozen=True, slots=True)
class CommandResult:
    returncode: int
    stdout: str
    stderr: str


Runner = Callable[[Sequence[str], int], CommandResult]
Sleeper = Callable[[float], None]


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _default_runner(command: Sequence[str], timeout: int) -> CommandResult:
    try:
        completed = subprocess.run(
            list(command),
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return CommandResult(completed.returncode, completed.stdout, completed.stderr)
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout.decode() if isinstance(exc.stdout, bytes) else str(exc.stdout or "")
        stderr = exc.stderr.decode() if isinstance(exc.stderr, bytes) else str(exc.stderr or "")
        return CommandResult(124, stdout, (stderr + "\nZeitlimit ueberschritten").strip())
    except OSError as exc:
        return CommandResult(127, "", str(exc))


def default_job_specs() -> tuple[JobSpec, ...]:
    return (
        JobSpec(
            name="supervisor",
            description="Ueberwacht die freigegebenen Hintergrundjobs und speichert Zustandswechsel.",
            timer_unit="personal-assistant-supervisor.timer",
            service_unit="personal-assistant-supervisor.service",
            default_on=True,
            standard=True,
            health_command=("scripts/ollama-priority-proxy.sh", "status"),
            always_health=True,
        ),
        JobSpec(
            name="mail",
            description="Sortiert und verarbeitet E-Mails automatisch.",
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
        ),
        JobSpec(
            name="mail-index",
            description="Gleicht den autoritativen read-only IMAP-Bestand beim einzigen Mail-Owner ab.",
            timer_unit="mail-agent.timer",
            service_unit="mail-agent.service",
            default_on=False,
            standard=False,
            health_command=("scripts/assistant.sh", "mail", "index", "doctor"),
        ),
        JobSpec(
            name="monitor",
            description="Erfasst technische Performance, Datenfrische und Scheduler-Zustand.",
            timer_unit="personal-assistant-monitor.timer",
            service_unit="personal-assistant-monitor.service",
            default_on=True,
            standard=True,
        ),
        JobSpec(
            name="sync",
            description="Aktualisiert den lokalen Wissensindex aus Nextcloud.",
            timer_unit="personal-assistant-sync.timer",
            service_unit="personal-assistant-sync.service",
            default_on=False,
            standard=False,
            health_command=("scripts/assistant.sh", "nextcloud", "doctor"),
        ),
        JobSpec(
            name="portfolio",
            description="Aktualisiert Depot- und Watchlist-Kurse mit Frische- und Pflichtdatenpruefung.",
            timer_unit="personal-assistant-portfolio.timer",
            service_unit="personal-assistant-portfolio.service",
            default_on=False,
            standard=False,
            health_command=("scripts/assistant.sh", "portfolio", "doctor"),
        ),
    )


class JobController:
    """Narrow controller for a fixed allowlist of user-level systemd jobs.

    The controller separates the desired state from the observed systemd state.
    Generic health failures are reported only. One allowlisted mail safety-gate
    condition may run a bounded dry-run and restart the existing service without
    ``--force``; all other changes require explicit ``on``, ``off`` or ``restart``.
    """

    def __init__(
        self,
        *,
        state_path: Path | None = None,
        workspace_root: Path = WORKSPACE_ROOT,
        code_root: Path | None = None,
        unit_dir: Path | None = None,
        runner: Runner | None = None,
        specs: Iterable[JobSpec] | None = None,
        sleeper: Sleeper | None = None,
    ) -> None:
        self.workspace_root = Path(workspace_root).expanduser().resolve()
        self.container_mode = (
            os.environ.get("OPENCLAW_RUNTIME", "").strip().lower() == "container"
        )
        default_code_root = (
            os.environ.get("OPENCLAW_IMAGE_ROOT")
            if self.container_mode
            else self.workspace_root
        )
        self.code_root = (
            Path(code_root or default_code_root or self.workspace_root)
            .expanduser()
            .resolve()
        )
        default_state = DEFAULT_STATE_PATH
        if root := os.environ.get("OPENCLAW_COORDINATION_DATA_DIR"):
            default_state = Path(root).expanduser().resolve() / "job_control.json"
        self.state_path = Path(state_path or default_state).expanduser().resolve()
        self.unit_dir = Path(unit_dir or USER_UNIT_DIR).expanduser().resolve()
        self.runner = runner or _default_runner
        self.sleeper = sleeper or time.sleep
        self.specs = {item.name: item for item in (specs or default_job_specs())}
        self.legacy_activation_allowed = (
            os.environ.get("OPENCLAW_ENABLE_LEGACY_SYSTEMD", "").strip() == "YES"
        )
        self.container_status_dir = Path(
            os.environ.get("OPENCLAW_JOB_STATUS_DIR")
            or (self.workspace_root / "personal_assistant/data/container_jobs")
        ).expanduser().resolve()
        self.state = self._load_state()

    def _load_state(self) -> dict[str, Any]:
        defaults = {name: spec.default_on for name, spec in self.specs.items()}
        if not self.state_path.exists():
            return {
                "version": STATE_VERSION,
                "desired": defaults,
                "observed": {},
                "active_alerts": {},
                "recovery": {},
                "updated_at": "",
            }
        try:
            value = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            value = {}
        desired = value.get("desired") if isinstance(value.get("desired"), dict) else {}
        merged = {name: bool(desired.get(name, default)) for name, default in defaults.items()}
        return {
            "version": STATE_VERSION,
            "desired": merged,
            "observed": value.get("observed") if isinstance(value.get("observed"), dict) else {},
            "active_alerts": value.get("active_alerts") if isinstance(value.get("active_alerts"), dict) else {},
            "recovery": value.get("recovery") if isinstance(value.get("recovery"), dict) else {},
            "updated_at": str(value.get("updated_at") or ""),
        }

    def _save_state(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state["version"] = STATE_VERSION
        self.state["updated_at"] = _utc_now()
        temporary = self.state_path.with_suffix(self.state_path.suffix + ".tmp")
        temporary.write_text(json.dumps(self.state, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        os.chmod(temporary, 0o600)
        temporary.replace(self.state_path)

    def _run(self, command: Sequence[str], timeout: int = 120) -> CommandResult:
        resolved = list(command)
        for index, value in enumerate(resolved):
            if value.startswith("scripts/"):
                resolved[index] = str((self.code_root / value).resolve())
        if resolved and not Path(resolved[0]).is_absolute() and "/" in resolved[0]:
            resolved[0] = str((self.workspace_root / resolved[0]).resolve())
        return self.runner(resolved, timeout)

    def _select(self, target: str, *, for_off: bool = False) -> list[JobSpec]:
        if target in self.specs:
            return [self.specs[target]]
        if target == "standard":
            selected = [item for item in self.specs.values() if item.standard]
        elif target == "all":
            selected = list(self.specs.values())
        else:
            raise ValueError(f"Unbekannter Job: {target}")
        # The supervisor stays alive when business jobs are switched off so it can
        # distinguish an intentional OFF state from an unexpected failure.
        if for_off:
            selected = [item for item in selected if item.name != "supervisor"]
        return selected

    @staticmethod
    def _parse_properties(text: str) -> dict[str, str]:
        properties: dict[str, str] = {}
        for line in text.splitlines():
            if "=" not in line:
                continue
            key, value = line.split("=", 1)
            properties[key.strip()] = value.strip()
        return properties

    def _container_spec_for_unit(self, unit: str) -> JobSpec | None:
        for spec in self.specs.values():
            if unit in {spec.timer_unit, spec.service_unit}:
                return spec
        return None

    def _container_runtime_status(self, unit: str) -> dict[str, Any]:
        spec = self._container_spec_for_unit(unit)
        if spec is None:
            return {
                "unit": unit,
                "available": False,
                "returncode": 4,
                "error": "Unbekannter Container-Job",
                "LoadState": "not-found",
                "ActiveState": "inactive",
                "SubState": "dead",
                "UnitFileState": "disabled",
                "Result": "success",
            }
        desired = bool(self.state.get("desired", {}).get(spec.name, spec.default_on))
        heartbeat_path = self.container_status_dir / f"{spec.name}.json"
        heartbeat: dict[str, Any] = {}
        try:
            value = json.loads(heartbeat_path.read_text(encoding="utf-8"))
            heartbeat = value if isinstance(value, dict) else {}
        except (FileNotFoundError, OSError, json.JSONDecodeError):
            heartbeat = {}
        updated = str(heartbeat.get("updated_at") or "")
        fresh = False
        if updated:
            try:
                parsed = datetime.fromisoformat(updated.replace("Z", "+00:00"))
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=UTC)
                fresh = datetime.now(UTC) - parsed.astimezone(UTC) < timedelta(minutes=5)
            except ValueError:
                fresh = False
        active = desired and fresh
        is_timer = unit == spec.timer_unit
        heartbeat_state = str(heartbeat.get("state") or "")
        # A worker publishes ``running`` before starting its child. Its previous
        # result remains useful history but must not be treated as the result of
        # the in-flight run. This is essential for the supervisor checking its
        # own status: otherwise one failed run permanently latches the next run
        # into the same failure.
        in_flight = heartbeat_state == "running"
        return {
            "unit": unit,
            "available": True,
            "returncode": 0,
            "error": "" if fresh or not desired else "Container-Worker hat keinen aktuellen Heartbeat",
            "LoadState": "loaded",
            "ActiveState": "active" if active else "inactive",
            "SubState": ("waiting" if is_timer else (heartbeat_state or "running")) if active else "dead",
            "UnitFileState": "enabled" if desired else "disabled",
            "Result": "running" if in_flight else str(heartbeat.get("result") or "success"),
            "ExecMainStatus": "0" if in_flight else str(heartbeat.get("last_exit_code") or 0),
            "ExecMainStartTimestamp": str(heartbeat.get("last_started_at") or ""),
            "ExecMainExitTimestamp": str(heartbeat.get("last_finished_at") or ""),
            "container": True,
            "heartbeat": str(heartbeat_path),
        }

    def _unit_status(self, unit: str) -> dict[str, Any]:
        if self.container_mode:
            return self._container_runtime_status(unit)
        command = [
            "systemctl", "--user", "show", unit, "--no-pager",
            "--property=LoadState,ActiveState,SubState,UnitFileState,Result,ExecMainStatus,ExecMainStartTimestamp,ExecMainExitTimestamp",
        ]
        result = self._run(command, timeout=20)
        properties = self._parse_properties(result.stdout)
        load_state = properties.get("LoadState", "not-found" if result.returncode else "")
        return {
            "unit": unit,
            "available": result.returncode == 0 and load_state != "not-found",
            "returncode": result.returncode,
            "error": result.stderr.strip(),
            **properties,
        }

    @staticmethod
    def _enabled(value: str) -> bool:
        return value in {"enabled", "enabled-runtime", "static"}

    @staticmethod
    def _failed(status: dict[str, Any]) -> bool:
        return status.get("ActiveState") == "failed" or status.get("Result") == "failed"

    def _openclaw_config_get(self, path: str) -> dict[str, Any]:
        result = self._run(["openclaw", "config", "get", path, "--json"], timeout=30)
        value: Any = None
        if result.returncode == 0:
            try:
                value = json.loads(result.stdout)
            except json.JSONDecodeError:
                value = result.stdout.strip()
        return {
            "ok": result.returncode == 0,
            "value": value,
            "returncode": result.returncode,
            "detail": (result.stderr.strip() or result.stdout.strip())[-2000:],
        }

    def _heartbeat_reporting_status(self) -> dict[str, Any]:
        if (
            self.container_mode
            and os.environ.get("OPENCLAW_ROLE", "").strip() == "supervisor-worker"
            and os.environ.get("OPENCLAW_EVENT_QUEUE_DIR", "").strip()
        ):
            status = relay_status()
            return {
                **status,
                "target": "gateway-local-event-relay",
                "every": f"{os.environ.get('SUPERVISOR_INTERVAL_SECONDS', '300')}s",
                "detail": (
                    "Gateway-lokaler Ereignisrelay ist zustellbereit"
                    if status.get("ok")
                    else str(status.get("detail") or "Gateway-Ereignisrelay ist nicht zustellbereit")
                ),
            }
        target = self._openclaw_config_get("agents.defaults.heartbeat.target")
        every = self._openclaw_config_get("agents.defaults.heartbeat.every")
        target_value = str(target.get("value") or "none")
        return {
            "ok": bool(target.get("ok") and target_value != "none"),
            "target": target_value,
            "every": every.get("value") if every.get("ok") else "",
            "detail": (
                "Heartbeat-Meldungen werden an den letzten Kontakt zugestellt"
                if target.get("ok") and target_value == "last"
                else "Heartbeat-Zustellung ist deaktiviert oder nicht lesbar"
            ),
            "target_check": target,
            "every_check": every,
        }

    def _enable_heartbeat_reporting(self) -> dict[str, Any]:
        before = self._heartbeat_reporting_status()
        commands: list[dict[str, Any]] = []
        changes: list[tuple[str, str]] = []
        if before.get("target") in {"", "none", "None"}:
            changes.append(("agents.defaults.heartbeat.target", json.dumps("last")))
        changes.extend((
            ("agents.defaults.heartbeat.directPolicy", json.dumps("allow")),
            ("agents.defaults.heartbeat.lightContext", "true"),
            ("agents.defaults.heartbeat.isolatedSession", "true"),
        ))
        for config_path, value in changes:
            result = self._run(
                ["openclaw", "config", "set", config_path, value, "--strict-json"],
                timeout=60,
            )
            commands.append({
                "command": f"openclaw config set {config_path}",
                "returncode": result.returncode,
                "detail": (result.stderr.strip() or result.stdout.strip())[-2000:],
            })
        validate = self._run(["openclaw", "config", "validate", "--json"], timeout=60)
        commands.append({
            "command": "openclaw config validate",
            "returncode": validate.returncode,
            "detail": (validate.stderr.strip() or validate.stdout.strip())[-3000:],
        })
        after = self._heartbeat_reporting_status()
        return {
            "attempted": True,
            "ok": all(item["returncode"] == 0 for item in commands) and bool(after.get("ok")),
            "before": before,
            "after": after,
            "commands": commands,
        }


    @staticmethod
    def _json_payload(result: CommandResult) -> dict[str, Any] | None:
        try:
            value = json.loads(result.stdout)
        except json.JSONDecodeError:
            return None
        return value if isinstance(value, dict) else None

    def _mail_readiness(self, spec: JobSpec) -> dict[str, Any]:
        if not spec.readiness_command:
            return {"checked": False, "ok": True, "auto_recoverable": False, "detail": "Kein Production-Gate konfiguriert"}
        result = self._run(spec.readiness_command, timeout=180)
        payload = self._json_payload(result)
        return {
            "checked": True,
            "ok": bool(result.returncode == 0 and isinstance(payload, dict) and payload.get("ok")),
            "auto_recoverable": bool(isinstance(payload, dict) and payload.get("auto_recoverable")),
            "returncode": result.returncode,
            "blockers": list(payload.get("blockers") or []) if isinstance(payload, dict) else [],
            "gate": payload.get("gate") if isinstance(payload, dict) else {},
            "result": payload,
            "detail": (result.stderr.strip() or result.stdout.strip())[-4000:],
        }

    @staticmethod
    def _recovery_signature(readiness: dict[str, Any]) -> str:
        value = {
            "blockers": readiness.get("blockers") or [],
            "gate": readiness.get("gate") or {},
        }
        return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)

    def _recovery_in_cooldown(self, key: str, signature: str) -> bool:
        previous = self.state.get("recovery", {}).get(key)
        if not isinstance(previous, dict) or previous.get("signature") != signature or previous.get("ok"):
            return False
        try:
            attempted = datetime.fromisoformat(str(previous.get("attempted_at") or "").replace("Z", "+00:00"))
        except ValueError:
            return False
        if attempted.tzinfo is None:
            attempted = attempted.replace(tzinfo=UTC)
        return datetime.now(UTC) - attempted < AUTO_RECOVERY_COOLDOWN

    def _mail_lock_status(self) -> dict[str, Any]:
        result = self._run(["scripts/mail-agent.sh", "lock-status"], timeout=20)
        payload = self._json_payload(result)
        if not isinstance(payload, dict):
            return {
                "checked": True,
                "ok": False,
                "locked": True,
                "returncode": result.returncode,
                "detail": (result.stderr.strip() or result.stdout.strip())[-2000:],
            }
        return {
            "checked": True,
            "ok": bool(payload.get("ok")),
            "locked": bool(payload.get("locked")),
            "pid": payload.get("pid"),
            "process_alive": payload.get("process_alive"),
            "returncode": result.returncode,
            "detail": str(payload.get("detail") or "")[-2000:],
            "result": payload,
        }

    def _wait_for_mail_idle(self, spec: JobSpec, *, attempts: int = 12, interval_seconds: float = 2.0) -> dict[str, Any]:
        observations: list[dict[str, Any]] = []
        for attempt in range(1, attempts + 1):
            service = self._unit_status(spec.service_unit)
            lock = self._mail_lock_status()
            idle_service = service.get("ActiveState") in {"", "inactive", "failed"}
            lock_free = bool(lock.get("ok") and not lock.get("locked"))
            observations.append({
                "attempt": attempt,
                "service_state": service.get("ActiveState", ""),
                "lock": lock,
            })
            if idle_service and lock_free:
                return {
                    "ok": True,
                    "attempts": attempt,
                    "service": service,
                    "lock": lock,
                    "observations": observations[-3:],
                }
            if attempt < attempts:
                self.sleeper(interval_seconds)
        last = observations[-1] if observations else {}
        return {
            "ok": False,
            "transient": True,
            "attempts": attempts,
            "service": self._unit_status(spec.service_unit),
            "lock": last.get("lock") or self._mail_lock_status(),
            "observations": observations[-3:],
            "detail": "Mail-Interface oder Prozesssperre blieb waehrend der begrenzten Wartezeit aktiv",
        }

    def _quiesce_mail_for_recovery(self, spec: JobSpec) -> dict[str, Any]:
        if self.container_mode:
            self.state["desired"][spec.name] = False
            self._save_state()
            idle = self._wait_for_mail_idle(spec, attempts=60, interval_seconds=2.0)
            return {
                "ok": bool(idle.get("ok")),
                "commands": [{"command": "container desired mail=off", "returncode": 0, "detail": ""}],
                "idle": idle,
                "transient": bool(idle.get("transient")),
                "container": True,
            }
        commands: list[dict[str, Any]] = []
        for unit in (spec.timer_unit, spec.service_unit):
            result = self._run(["systemctl", "--user", "stop", unit], timeout=60)
            commands.append({
                "command": f"stop {unit}",
                "returncode": result.returncode,
                "detail": result.stderr.strip(),
            })
        idle = self._wait_for_mail_idle(spec)
        return {
            "ok": all(item["returncode"] == 0 for item in commands) and bool(idle.get("ok")),
            "commands": commands,
            "idle": idle,
            "transient": bool(idle.get("transient")),
        }

    def _restore_mail_timer(self, spec: JobSpec) -> dict[str, Any]:
        if self.container_mode:
            self.state["desired"][spec.name] = True
            self._save_state()
            self.container_status_dir.mkdir(parents=True, exist_ok=True)
            (self.container_status_dir / f"{spec.name}.wake").touch()
            return {
                "attempted": True,
                "ok": True,
                "command": "container desired mail=on",
                "returncode": 0,
                "detail": "Container-Mailworker wird aufgeweckt",
            }
        result = self._run(["systemctl", "--user", "enable", "--now", spec.timer_unit], timeout=60)
        return {
            "attempted": True,
            "ok": result.returncode == 0,
            "command": f"enable --now {spec.timer_unit}",
            "returncode": result.returncode,
            "detail": result.stderr.strip(),
        }

    @staticmethod
    def _is_transient_lock_failure(result: CommandResult, summary: dict[str, Any] | None) -> bool:
        text = (result.stderr + "\n" + result.stdout).lower()
        return result.returncode == 3 and (
            "prozesssperre" in text
            or "bereits aktiv" in text
            or bool(isinstance(summary, dict) and summary.get("locked"))
        )

    def _notify_recovery(self, *, ok: bool, detail: str) -> dict[str, Any]:
        state = "erfolgreich" if ok else "fehlgeschlagen"
        text = (
            f"Automatische Mail-Interface-Wiederherstellung {state}: {detail[:900]}. "
            "Melde Jan den erkannten Ausfall, die ausgefuehrten sicheren Schritte und den aktuellen Zustand. "
            "Es wurde kein --force verwendet."
        )[:1800]
        result = self._run(_system_event_command(text), timeout=30)
        return {
            "attempted": True,
            "ok": result.returncode == 0,
            "returncode": result.returncode,
            "detail": (result.stderr.strip() or result.stdout.strip())[-2000:],
        }

    def _recover_mail_production_gate(self, spec: JobSpec, *, force_attempt: bool = False) -> dict[str, Any]:
        before = self._mail_readiness(spec)
        if before.get("ok"):
            return {"attempted": False, "ok": True, "before": before, "detail": "Produktionsfreigabe ist gueltig"}
        if not before.get("auto_recoverable"):
            return {
                "attempted": False,
                "ok": False,
                "before": before,
                "detail": "Blockade ist nicht fuer eine automatische Freigabe zugelassen",
            }
        if not spec.automatic_recovery_command:
            return {"attempted": False, "ok": False, "before": before, "detail": "Kein sicherer Dry-Run konfiguriert"}

        key = "mail-production-gate"
        signature = self._recovery_signature(before)
        if not force_attempt and self._recovery_in_cooldown(key, signature):
            return {
                "attempted": False,
                "ok": False,
                "cooldown": True,
                "before": before,
                "detail": "Derselbe fachlich fehlgeschlagene Dry-Run ist kuerzlich fehlgeschlagen; kein Wiederholungsloop",
            }

        quiesce = self._quiesce_mail_for_recovery(spec)
        if not quiesce.get("ok"):
            timer_restore = self._restore_mail_timer(spec)
            return {
                "attempted": True,
                "ok": False,
                "transient": True,
                "before": before,
                "quiesce": quiesce,
                "timer_restore": timer_restore,
                "detail": "Automatischer Dry-Run wurde wegen eines noch aktiven Mail-Laufs nicht gestartet; kein Cooldown gesetzt",
            }

        dry_run: CommandResult | None = None
        summary: dict[str, Any] | None = None
        attempts: list[dict[str, Any]] = []
        for attempt in range(1, 4):
            lock = self._mail_lock_status()
            if lock.get("locked"):
                attempts.append({"attempt": attempt, "started": False, "lock": lock})
                if attempt < 3:
                    self.sleeper(2.0)
                    continue
                timer_restore = self._restore_mail_timer(spec)
                return {
                    "attempted": True,
                    "ok": False,
                    "transient": True,
                    "before": before,
                    "quiesce": quiesce,
                    "attempts": attempts,
                    "timer_restore": timer_restore,
                    "detail": "Prozesssperre blieb belegt; Dry-Run wurde nicht gestartet und kein Cooldown gesetzt",
                }

            dry_run = self._run(
                [
                    "env",
                    "OPENCLAW_OLLAMA_PRIORITY=maintenance",
                    "OPENCLAW_OLLAMA_SOURCE=supervisor-recovery",
                    *spec.automatic_recovery_command,
                ],
                timeout=240,
            )
            summary = self._json_payload(dry_run)
            transient_lock = self._is_transient_lock_failure(dry_run, summary)
            attempts.append({
                "attempt": attempt,
                "started": True,
                "returncode": dry_run.returncode,
                "transient_lock": transient_lock,
            })
            if transient_lock and attempt < 3:
                self.sleeper(2.0)
                continue
            break

        assert dry_run is not None
        if self._is_transient_lock_failure(dry_run, summary):
            timer_restore = self._restore_mail_timer(spec)
            return {
                "attempted": True,
                "ok": False,
                "transient": True,
                "before": before,
                "quiesce": quiesce,
                "attempts": attempts,
                "dry_run": {
                    "returncode": dry_run.returncode,
                    "summary": summary,
                    "stderr": dry_run.stderr[-4000:],
                },
                "timer_restore": timer_restore,
                "detail": "Dry-Run kollidierte nur mit einer kurzfristigen Prozesssperre; kein Cooldown gesetzt",
            }

        errors = list(summary.get("errors") or []) if isinstance(summary, dict) else ["Dry-Run-Ausgabe war nicht als JSON lesbar"]
        actions = list(summary.get("actions") or []) if isinstance(summary, dict) else []
        actions_ok = all(bool(item.get("ok")) for item in actions if isinstance(item, dict))
        dry_ok = dry_run.returncode == 0 and isinstance(summary, dict) and not errors and actions_ok
        after = self._mail_readiness(spec) if dry_ok else before
        ok = bool(dry_ok and after.get("ok"))
        detail = (
            f"Dry-Run verarbeitet={summary.get('processed', 0) if isinstance(summary, dict) else '?'}; "
            f"Fehler={len(errors)}; Production-Gate={'frei' if after.get('ok') else 'blockiert'}"
        )

        record = {
            "signature": signature,
            "attempted_at": _utc_now(),
            "ok": ok,
            "detail": detail,
            "dry_run_returncode": dry_run.returncode,
            "errors": errors[:20],
        }
        self.state.setdefault("recovery", {})[key] = record
        self._save_state()
        timer_restore = {"attempted": False, "ok": True}
        if not ok:
            timer_restore = self._restore_mail_timer(spec)
        return {
            "attempted": True,
            "ok": ok,
            "before": before,
            "quiesce": quiesce,
            "attempts": attempts,
            "dry_run": {
                "returncode": dry_run.returncode,
                "summary": summary,
                "stderr": dry_run.stderr[-4000:],
            },
            "after": after,
            "timer_restore": timer_restore,
            "detail": detail,
        }

    def _restart_mail_after_recovery(self, spec: JobSpec) -> dict[str, Any]:
        if self.container_mode:
            restored = self._restore_mail_timer(spec)
            return {"ok": bool(restored.get("ok")), "commands": [restored], "container": True}
        commands: list[dict[str, Any]] = []
        for unit in (spec.service_unit, spec.timer_unit):
            result = self._run(["systemctl", "--user", "reset-failed", unit], timeout=30)
            commands.append({"command": f"reset-failed {unit}", "returncode": result.returncode, "detail": result.stderr.strip()})
        enable = self._run(["systemctl", "--user", "enable", "--now", spec.timer_unit], timeout=60)
        commands.append({"command": f"enable --now {spec.timer_unit}", "returncode": enable.returncode, "detail": enable.stderr.strip()})
        start = self._run(["systemctl", "--user", "start", "--no-block", spec.service_unit], timeout=30)
        commands.append({"command": f"start --no-block {spec.service_unit}", "returncode": start.returncode, "detail": start.stderr.strip()})
        return {"ok": all(item["returncode"] == 0 for item in commands), "commands": commands}

    def _health(self, spec: JobSpec) -> dict[str, Any]:
        if not spec.health_command:
            return {"checked": False, "ok": True, "detail": "Kein zusaetzlicher Health-Check erforderlich"}
        result = self._run(spec.health_command, timeout=180)
        payload: Any = None
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError:
            payload = None
        detail = result.stderr.strip() or result.stdout.strip()
        response: dict[str, Any] = {
            "checked": True,
            "ok": result.returncode == 0,
            "returncode": result.returncode,
            "detail": detail[-4000:],
        }
        if isinstance(payload, dict):
            response["result"] = payload
            folders = payload.get("folders")
            if isinstance(folders, dict):
                response["missing_folders"] = list(folders.get("missing") or [])
        return response

    def _journal(self, spec: JobSpec) -> str:
        if self.container_mode:
            log_root = Path(
                os.environ.get("OPENCLAW_LOG_DIR")
                or (self.workspace_root / "personal_assistant/data/container_logs")
            ).expanduser().resolve()
            path = log_root / f"{spec.name}.log"
            try:
                return path.read_text(encoding="utf-8", errors="replace")[-8000:]
            except OSError as exc:
                return str(exc)
        result = self._run(
            ["journalctl", "--user", "-u", spec.service_unit, "-n", "30", "--no-pager"],
            timeout=20,
        )
        text = result.stdout.strip() or result.stderr.strip()
        return text[-8000:]

    def _job_status(self, spec: JobSpec, *, deep: bool = False) -> dict[str, Any]:
        desired_on = bool(self.state["desired"].get(spec.name, spec.default_on))
        timer = self._unit_status(spec.timer_unit)
        service = self._unit_status(spec.service_unit)
        issues: list[dict[str, str]] = []

        if not timer.get("available"):
            issues.append({"code": "timer-unavailable", "detail": f"{spec.timer_unit} ist nicht installiert oder nicht ladbar"})
        if not service.get("available"):
            issues.append({"code": "service-unavailable", "detail": f"{spec.service_unit} ist nicht installiert oder nicht ladbar"})

        if desired_on:
            if timer.get("available") and not self._enabled(str(timer.get("UnitFileState") or "")):
                issues.append({"code": "timer-disabled", "detail": f"{spec.timer_unit} ist nicht aktiviert"})
            if timer.get("available") and timer.get("ActiveState") != "active":
                issues.append({"code": "timer-inactive", "detail": f"{spec.timer_unit} ist nicht aktiv"})
            if self._failed(timer):
                issues.append({"code": "timer-failed", "detail": f"{spec.timer_unit} meldet einen Fehler"})
            if self._failed(service):
                issues.append({"code": "service-failed", "detail": f"{spec.service_unit} meldet einen fehlgeschlagenen Lauf"})
            elif (
                str(service.get("Result") or "") == "degraded"
                or str(service.get("ExecMainStatus") or "") == "1"
            ):
                issues.append({
                    "code": "service-degraded",
                    "detail": f"{spec.service_unit} meldet einen eingeschraenkten Lauf",
                })
        else:
            if timer.get("ActiveState") == "active":
                issues.append({"code": "unexpected-on", "detail": f"{spec.timer_unit} laeuft trotz bewusstem OFF-Zustand"})

        reporting: dict[str, Any] = {"checked": False, "ok": True}
        if spec.name == "supervisor":
            reporting = {"checked": True, **self._heartbeat_reporting_status()}
            if desired_on and not reporting.get("ok"):
                issues.append({
                    "code": "heartbeat-delivery-disabled",
                    "detail": "OpenClaw-Heartbeat kann Ausfaelle nicht zustellen; Ziel ist 'none' oder nicht lesbar",
                })
            scheduler_path = (
                Path(os.environ["OPENCLAW_COORDINATION_DATA_DIR"]).expanduser().resolve()
                / "work_scheduler.sqlite3"
                if os.environ.get("OPENCLAW_COORDINATION_DATA_DIR")
                else self.workspace_root / "personal_assistant/data/work_scheduler.sqlite3"
            )
            if scheduler_path.exists():
                try:
                    scheduler = AdaptiveWorkScheduler(scheduler_path)
                    try:
                        scheduler_health = scheduler.health()
                    finally:
                        scheduler.close()
                except (OSError, sqlite3.Error) as exc:
                    scheduler_health = {
                        "enabled": True,
                        "ok": False,
                        "state": "failed",
                        "error": str(exc)[:500],
                    }
                reporting["scheduler"] = scheduler_health
                if desired_on and not scheduler_health.get("ok"):
                    issues.append({
                        "code": "scheduler-health-failed",
                        "detail": (
                            "Adaptive Aufgabensteuerung meldet "
                            f"{scheduler_health.get('state', 'unknown')}; "
                            f"Fristverletzungen={scheduler_health.get('deadline_misses', 0)}, "
                            f"stale Leases={scheduler_health.get('stale_leases', 0)}"
                        ),
                    })

        health: dict[str, Any] = {"checked": False, "ok": True}
        observer_only = (
            self.container_mode
            and os.environ.get("OPENCLAW_ROLE", "").strip() == "supervisor-worker"
            and spec.name != "supervisor"
        )
        if not observer_only and (spec.always_health or deep or any(
            item["code"] in {"service-failed", "timer-failed"} for item in issues
        )):
            health = self._health(spec)
            if desired_on and health.get("checked") and not health.get("ok"):
                missing = health.get("missing_folders") or []
                if missing:
                    issues.append({"code": "mail-folders-missing", "detail": "Fehlende Mailordner: " + ", ".join(missing)})
                else:
                    issues.append({"code": "health-check-failed", "detail": str(health.get("detail") or "Health-Check fehlgeschlagen")[-1000:]})

        state = "on"
        if not desired_on:
            state = "off" if not issues else "degraded"
        elif issues:
            state = "failed" if any("failed" in item["code"] or "unavailable" in item["code"] for item in issues) else "degraded"

        response: dict[str, Any] = {
            "name": spec.name,
            "description": spec.description,
            "desired": "on" if desired_on else "off",
            "state": state,
            "ok": not issues,
            "timer": timer,
            "service": service,
            "health": health,
            "reporting": reporting,
            "issues": issues,
        }
        if issues:
            response["journal"] = self._journal(spec)
        return response

    def status(self, *, target: str = "all", deep: bool = False, record: bool = False) -> dict[str, Any]:
        selected = self._select(target)
        jobs = [self._job_status(item, deep=deep) for item in selected]
        result = {
            "ok": all(item["ok"] for item in jobs),
            "checked_at": _utc_now(),
            "target": target,
            "jobs": jobs,
            "active_alerts": [],
            "new_alerts": [],
            "resolved_alerts": [],
        }
        if record:
            self._record(result)
        else:
            result["active_alerts"] = list(self.state.get("active_alerts", {}).values())
        return result

    def _notify_openclaw(
        self,
        *,
        new_alerts: Sequence[dict[str, Any]],
        resolved_alerts: Sequence[dict[str, Any]],
    ) -> dict[str, Any]:
        """Wake OpenClaw for a newly detected or resolved operational alert.

        The notification is deliberately best-effort: monitoring state is still
        persisted even when the gateway is unavailable. Repeated checks of the
        same active alert do not enqueue another event.
        """
        if not new_alerts and not resolved_alerts:
            return {"attempted": False, "ok": True, "detail": "Keine Zustandsaenderung"}

        parts: list[str] = []
        for alert in new_alerts[:8]:
            parts.append(
                f"FEHLER {alert.get('job', '?')}/{alert.get('code', '?')}: "
                f"{str(alert.get('detail') or '')[:300]}"
            )
        for alert in resolved_alerts[:8]:
            parts.append(
                f"ENTWARNUNG {alert.get('job', '?')}/{alert.get('code', '?')}: "
                "Der zuvor gemeldete Zustand ist nicht mehr aktiv."
            )
        text = (
            "Betriebszustand des lokalen Assistenten hat sich geaendert. "
            + " | ".join(parts)
            + " Fuehre die Prueflogik aus HEARTBEAT.md aus und melde Jan den "
              "konkreten Zustand. Starte oder repariere Jobs nicht ohne seinen "
              "ausdruecklichen Auftrag."
        )[:1800]
        result = self._run(_system_event_command(text), timeout=30)
        return {
            "attempted": True,
            "ok": result.returncode == 0,
            "returncode": result.returncode,
            "detail": (result.stderr.strip() or result.stdout.strip())[-2000:],
        }

    def _record(self, report: dict[str, Any]) -> None:
        previous = self.state.get("active_alerts", {})
        checked_names = {str(job["name"]) for job in report["jobs"]}
        active: dict[str, dict[str, Any]] = {
            key: value for key, value in previous.items()
            if str(value.get("job") or "") not in checked_names
        }
        new_alerts: list[dict[str, Any]] = []
        now = str(report["checked_at"])
        observed: dict[str, Any] = {}

        for job in report["jobs"]:
            observed[job["name"]] = {
                "state": job["state"],
                "ok": job["ok"],
                "checked_at": now,
                "issues": job["issues"],
            }
            if job["desired"] != "on":
                continue
            for issue in job["issues"]:
                alert_id = f"{job['name']}:{issue['code']}"
                old = previous.get(alert_id, {})
                alert = {
                    "id": alert_id,
                    "job": job["name"],
                    "code": issue["code"],
                    "detail": issue["detail"],
                    "first_seen": str(old.get("first_seen") or now),
                    "last_seen": now,
                }
                active[alert_id] = alert
                if alert_id not in previous:
                    new_alerts.append(alert)

        resolved = [
            {**value, "resolved_at": now}
            for key, value in previous.items()
            if key not in active
        ]
        self.state["observed"].update(observed)
        self.state["active_alerts"] = active
        self._save_state()
        report["active_alerts"] = list(active.values())
        report["new_alerts"] = new_alerts
        report["resolved_alerts"] = resolved
        report["notification"] = self._notify_openclaw(
            new_alerts=new_alerts,
            resolved_alerts=resolved,
        )

    def _install_units(self, spec: JobSpec) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        self.unit_dir.mkdir(parents=True, exist_ok=True)
        for unit in (spec.service_unit, spec.timer_unit):
            source = self.workspace_root / "legacy/systemd/units" / unit
            destination = self.unit_dir / unit
            if destination.exists():
                results.append({"unit": unit, "status": "exists", "path": str(destination), "ok": True})
                continue
            if not source.exists():
                results.append({"unit": unit, "status": "missing-package", "path": str(source), "ok": False})
                continue
            shutil.copy2(source, destination)
            os.chmod(destination, 0o644)
            results.append({"unit": unit, "status": "installed", "path": str(destination), "ok": True})
        return results

    def _repair_mail_folders(self, spec: JobSpec) -> dict[str, Any]:
        health = self._health(spec)
        missing = health.get("missing_folders") or []
        if not missing:
            return {"attempted": False, "ok": bool(health.get("ok")), "before": health, "detail": "Keine fehlenden Mailordner erkannt"}
        repair = self._run(spec.repair_command, timeout=180)
        after = self._health(spec)
        return {
            "attempted": True,
            "ok": repair.returncode == 0 and bool(after.get("ok")),
            "missing_before": missing,
            "setup": {"returncode": repair.returncode, "stdout": repair.stdout[-4000:], "stderr": repair.stderr[-4000:]},
            "after": after,
        }

    def _container_activate(self, spec: JobSpec, *, restart: bool, run_now: bool) -> dict[str, Any]:
        self.container_status_dir.mkdir(parents=True, exist_ok=True)
        wake_name = "mail" if spec.name == "mail-index" else spec.name
        wake = self.container_status_dir / f"{wake_name}.wake"
        if run_now or restart:
            wake.touch()
        return {
            "name": spec.name,
            "ok": True,
            "container": True,
            "restart_requested": bool(restart),
            "run_now_requested": bool(run_now),
            "wake_file": str(wake),
            "detail": "Container-Worker uebernimmt den geaenderten Sollzustand",
        }

    def _activate(self, spec: JobSpec, *, restart: bool, run_now: bool) -> dict[str, Any]:
        if self.container_mode:
            return self._container_activate(spec, restart=restart, run_now=run_now)
        install = self._install_units(spec)
        if not all(item["ok"] for item in install):
            return {"name": spec.name, "ok": False, "install": install, "detail": "Systemd-Unit fehlt im Paket"}

        commands: list[dict[str, Any]] = []
        reload_result = self._run(["systemctl", "--user", "daemon-reload"], timeout=30)
        commands.append({"command": "daemon-reload", "returncode": reload_result.returncode, "detail": reload_result.stderr.strip()})
        for unit in (spec.timer_unit, spec.service_unit):
            result = self._run(["systemctl", "--user", "reset-failed", unit], timeout=30)
            commands.append({
                "command": f"reset-failed {unit}",
                "returncode": result.returncode,
                "detail": result.stderr.strip(),
                "required": False,
            })

        repair: dict[str, Any] = {"attempted": False, "ok": True}
        reporting: dict[str, Any] = {"attempted": False, "ok": True}
        production_gate: dict[str, Any] = {"attempted": False, "ok": True}
        if spec.name == "mail":
            repair = self._repair_mail_folders(spec)
            if repair.get("ok"):
                production_gate = self._recover_mail_production_gate(spec, force_attempt=True)
        if spec.name == "supervisor":
            reporting = self._enable_heartbeat_reporting()

        if not repair.get("ok", True) or not production_gate.get("ok", True):
            return {
                "name": spec.name,
                "ok": False,
                "install": install,
                "repair": repair,
                "production_gate": production_gate,
                "reporting": reporting,
                "commands": commands,
                "detail": "Sicherer Vorlauf fehlgeschlagen; produktiver Dienst wurde nicht gestartet",
            }

        command = ["systemctl", "--user", "enable", "--now", spec.timer_unit]
        result = self._run(command, timeout=60)
        commands.append({"command": f"enable --now {spec.timer_unit}", "returncode": result.returncode, "detail": result.stderr.strip()})
        if restart:
            result = self._run(["systemctl", "--user", "restart", spec.timer_unit], timeout=60)
            commands.append({"command": f"restart {spec.timer_unit}", "returncode": result.returncode, "detail": result.stderr.strip()})

        if run_now and spec.name != "supervisor":
            result = self._run(["systemctl", "--user", "start", "--no-block", spec.service_unit], timeout=30)
            commands.append({"command": f"start --no-block {spec.service_unit}", "returncode": result.returncode, "detail": result.stderr.strip()})

        ok = (
            all(
                item["returncode"] == 0
                for item in commands
                if item.get("required", True)
            )
            and bool(repair.get("ok", True))
            and bool(production_gate.get("ok", True))
            and bool(reporting.get("ok", True))
        )
        return {
            "name": spec.name,
            "ok": ok,
            "install": install,
            "repair": repair,
            "production_gate": production_gate,
            "reporting": reporting,
            "commands": commands,
        }


    def check(self, *, target: str = "all", deep: bool = False) -> dict[str, Any]:
        """Check desired jobs and perform only allowlisted, non-destructive recovery.

        Currently automatic recovery is limited to the mail safety gate: run a
        bounded dry-run, verify the machine-readable production gate, then reset
        and start the existing service without ever using ``--force``.
        """

        initial = self.status(target=target, deep=deep, record=False)
        recoveries: list[dict[str, Any]] = []
        observer_only = (
            self.container_mode
            and os.environ.get("OPENCLAW_ROLE", "").strip() == "supervisor-worker"
        )
        for job in initial["jobs"]:
            if observer_only or job.get("name") != "mail" or job.get("desired") != "on":
                continue
            spec = self.specs["mail"]
            # Always verify the machine-readable production gate. A previous
            # stop/reset can clear systemd's failed state while the fingerprint
            # is still stale; relying only on ExecMainStatus=4 would then leave
            # the interface blocked indefinitely. The readiness check is read-only
            # and returns immediately when the gate is already valid.
            recovery = self._recover_mail_production_gate(spec)
            if not recovery.get("attempted") and recovery.get("ok"):
                continue
            restart = {"attempted": False, "ok": False}
            if recovery.get("ok"):
                restart = {"attempted": True, **self._restart_mail_after_recovery(spec)}
            notification = {"attempted": False, "ok": True, "detail": "Keine neue automatische Aktion"}
            if recovery.get("attempted") and not recovery.get("transient"):
                overall_ok = bool(recovery.get("ok") and restart.get("ok"))
                detail = (
                    f"{recovery.get('detail', '')}; "
                    f"Dienststart={'erfolgreich' if restart.get('ok') else 'nicht ausgefuehrt oder fehlgeschlagen'}"
                )
                notification = self._notify_recovery(ok=overall_ok, detail=detail)
            elif recovery.get("transient"):
                notification = {
                    "attempted": False,
                    "ok": True,
                    "detail": "Voruebergehender Lock-Konflikt; vorhandener Job-Alert bleibt ausreichend",
                }
            recoveries.append({
                "job": "mail",
                "recovery": recovery,
                "restart": restart,
                "notification": notification,
            })

        report = self.status(target=target, deep=deep, record=True)
        report["automatic_recoveries"] = recoveries
        if observer_only:
            report["automatic_recovery_owner"] = "mail-worker"
            supervisor = next(
                (job for job in report["jobs"] if job.get("name") == "supervisor"),
                None,
            )
            own_issues = [
                issue
                for issue in (supervisor or {}).get("issues", [])
                # Exit 1 from the previous observer cycle represented an
                # observed business-job degradation, not a broken observer.
                # Ignore that historical self-result so a successful next
                # observation can clear it.
                if issue.get("code") != "service-degraded"
            ]
            observer_notification = report.get("notification")
            notification_ok = not isinstance(observer_notification, dict) or bool(
                observer_notification.get("ok")
            )
            observer_ok = bool(supervisor is not None and not own_issues and notification_ok)
            report["observer_cycle"] = {
                "ok": observer_ok,
                "observed_jobs_ok": bool(report.get("ok")),
                "own_issues": own_issues,
                "notification_ok": notification_ok,
                "detail": (
                    "Beobachtung und Alarmzustellung erfolgreich"
                    if observer_ok
                    else "Supervisor-Beobachtung oder Alarmzustellung fehlgeschlagen"
                ),
            }
        if recoveries:
            report["ok"] = report["ok"] and all(
                bool(item["recovery"].get("ok")) and bool(item["restart"].get("ok"))
                for item in recoveries
            )
        return report

    def on(self, *, target: str = "standard", restart: bool = False, run_now: bool = True) -> dict[str, Any]:
        selected = self._select(target)
        if not self.container_mode and not self.legacy_activation_allowed:
            operation = "restart" if restart else "on"
            return {
                "ok": False,
                "operation": operation,
                "target": target,
                "actions": [{
                    "name": spec.name,
                    "ok": False,
                    "detail": (
                        "Legacy-systemd-Aktivierung ist eingefroren; den Docker-Jobpfad "
                        "verwenden oder fuer einen verifizierten Legacy-Rollback explizit "
                        "OPENCLAW_ENABLE_LEGACY_SYSTEMD=YES setzen"
                    ),
                } for spec in selected],
                "status": self.status(target=target, deep=False, record=False),
            }
        for spec in selected:
            self.state["desired"][spec.name] = True
        self._save_state()
        actions = [self._activate(spec, restart=restart, run_now=run_now) for spec in selected]
        report = self.status(target=target, deep=True, record=True)
        return {
            "ok": all(item["ok"] for item in actions) and report["ok"],
            "operation": "restart" if restart else "on",
            "target": target,
            "actions": actions,
            "status": report,
        }

    def off(self, *, target: str = "standard") -> dict[str, Any]:
        selected = self._select(target, for_off=True)
        actions: list[dict[str, Any]] = []
        for spec in selected:
            self.state["desired"][spec.name] = False
            if self.container_mode:
                self.container_status_dir.mkdir(parents=True, exist_ok=True)
                wake_name = "mail" if spec.name == "mail-index" else spec.name
                wake = self.container_status_dir / f"{wake_name}.wake"
                with suppress(FileNotFoundError):
                    wake.unlink()
                actions.append({
                    "name": spec.name,
                    "ok": True,
                    "container": True,
                    "detail": "Container-Worker stoppt nach dem aktuellen begrenzten Lauf",
                })
                continue
            stop = self._run(["systemctl", "--user", "stop", spec.service_unit], timeout=60)
            disable = self._run(["systemctl", "--user", "disable", "--now", spec.timer_unit], timeout=60)
            actions.append({
                "name": spec.name,
                "ok": stop.returncode == 0 and disable.returncode == 0,
                "commands": [
                    {"command": f"stop {spec.service_unit}", "returncode": stop.returncode, "detail": stop.stderr.strip()},
                    {"command": f"disable --now {spec.timer_unit}", "returncode": disable.returncode, "detail": disable.stderr.strip()},
                ],
            })
        self._save_state()
        report = self.status(target="all", deep=False, record=True)
        supervisor_stays_on = target in {"standard", "all"}
        return {
            "ok": all(item["ok"] for item in actions),
            "operation": "off",
            "target": target,
            "note": (
                "Der Supervisor bleibt aktiv, damit bewusste OFF-Zustaende und spaetere Fehler erkennbar bleiben."
                if supervisor_stays_on
                else "Der ausgewaehlte Job wurde bewusst ausgeschaltet."
            ),
            "actions": actions,
            "status": report,
        }

    def alerts(self) -> dict[str, Any]:
        return {
            "ok": not bool(self.state.get("active_alerts")),
            "updated_at": self.state.get("updated_at", ""),
            "active_alerts": list(self.state.get("active_alerts", {}).values()),
            "observed": self.state.get("observed", {}),
        }
