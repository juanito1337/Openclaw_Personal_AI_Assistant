from __future__ import annotations

import argparse
import json
import logging
import os
import sqlite3
import subprocess
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

from .bootstrap import create_personal_assistant
from .cli_handlers import dispatch as dispatch_domain_command
from .cli_handlers.invoices import run_external as run_invoice_external
from .cli_handlers.mail import run_external as run_mail_external
from .cli_parsers.core import add_commands as add_core_commands
from .cli_parsers.mail import add_commands as add_mail_commands
from .cli_parsers.nextcloud import add_commands as add_nextcloud_commands
from .cli_parsers.portfolio import add_commands as add_portfolio_commands
from .cli_parsers.runtime import add_commands as add_runtime_commands
from .cli_parsers.setup import add_commands as add_setup_commands
from .config import DEFAULT_CONFIG, DEFAULT_SECRETS, load_config
from .env import load_env
from .job_control import JobController
from .mail_source_setup import configure_mail_sources
from .release import release_report
from .setup import configure_nextcloud, initialize_local_files
from .tool_registry import capability_schema, static_tool_catalog
from .tool_setup import (
    configure_calendar_tools,
    configure_deck_orders_tools,
    configure_mail_move_tools,
    configure_mail_tools,
    configure_portfolio_tools,
    configure_tasks_tools,
    configure_workspace_tools,
)
from .work_scheduler import AdaptiveWorkScheduler

# Compatibility names retained for callers that characterized the pre-M5 helper.
_run_mail_tool = run_mail_external
_run_invoice_tool = run_invoice_external


def _logging(path: Path, verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    root = logging.getLogger()
    root.setLevel(level)
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    if not root.handlers:
        console = logging.StreamHandler()
        console.setFormatter(formatter)
        root.addHandler(console)
        path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(path, maxBytes=3_000_000, backupCount=5, encoding="utf-8")
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description="Lokale Personal-Assistant-Plattform")
    root.add_argument("--config", default=str(DEFAULT_CONFIG))
    root.add_argument("--verbose", action="store_true")
    sub = root.add_subparsers(dest="command", required=True)

    add_setup_commands(sub)
    add_runtime_commands(sub)
    add_portfolio_commands(sub)
    add_mail_commands(sub)
    add_nextcloud_commands(sub)
    add_core_commands(sub)

    return root


def _load_secrets(config_path: Path | None = None) -> None:
    # Central file wins. The legacy file remains a compatibility fallback for the
    # existing mail agent during migration.
    load_env(DEFAULT_SECRETS)
    load_env(Path("~/.config/mail-agent.env").expanduser())


def _print(value: Any) -> None:
    print(json.dumps(value, indent=2, ensure_ascii=False, default=str))


def _run_json_command(command: list[str], *, timeout: int = 60) -> tuple[int, dict[str, Any]]:
    try:
        completed = subprocess.run(command, check=False, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return 124, {"ok": False, "error": "Zeitlimit ueberschritten", "command": command[0]}
    except OSError as exc:
        return 127, {"ok": False, "error": str(exc), "command": command[0]}
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError:
        payload = {
            "ok": completed.returncode == 0,
            "returncode": completed.returncode,
            "stdout": completed.stdout[-2000:],
            "stderr": completed.stderr[-2000:],
        }
    if not isinstance(payload, dict):
        payload = {"ok": completed.returncode == 0, "result": payload}
    payload.setdefault("returncode", completed.returncode)
    if completed.stderr.strip() and "stderr" not in payload:
        payload["stderr"] = completed.stderr.strip()[-2000:]
    return completed.returncode, payload


def _handle_ollama(args: argparse.Namespace) -> int:
    code_root = (
        Path(os.environ.get("OPENCLAW_IMAGE_ROOT") or Path(__file__).resolve().parents[1])
        .expanduser()
        .resolve()
    )
    script = str(code_root / "scripts/ollama-priority-proxy.sh")
    if args.ollama_command == "status":
        code, payload = _run_json_command([script, "status"], timeout=30)
    elif args.ollama_command == "check":
        code, payload = _run_json_command([script, "check-upstream"], timeout=30)
    elif args.ollama_command == "queue":
        code, status = _run_json_command([script, "status"], timeout=30)
        payload = {
            "ok": bool(status.get("ok")),
            "queue": status.get("queue") or {},
            "stats": status.get("stats") or {},
            "detail": status.get("detail", ""),
            "returncode": code,
        }
    elif args.ollama_command in {"start", "restart"}:
        action = args.ollama_command
        service = "ollama-priority-proxy.service"
        control = subprocess.run(
            ["systemctl", "--user", action, service],
            check=False,
            capture_output=True,
            text=True,
            timeout=60,
        )
        code, status = (
            _run_json_command([script, "status"], timeout=30)
            if control.returncode == 0
            else (control.returncode, {})
        )
        upstream_code, upstream = (
            _run_json_command([script, "check-upstream"], timeout=30)
            if control.returncode == 0
            else (control.returncode, {})
        )
        payload = {
            "ok": control.returncode == 0
            and code == 0
            and upstream_code == 0
            and bool(status.get("ok"))
            and bool(upstream.get("ok")),
            "operation": action,
            "service": service,
            "control": {
                "returncode": control.returncode,
                "detail": (control.stderr.strip() or control.stdout.strip())[-2000:],
            },
            "status": status,
            "upstream": upstream,
        }
        code = 0 if payload["ok"] else 1
    else:
        payload = {"ok": False, "error": f"Unbekannter Ollama-Befehl: {args.ollama_command}"}
        code = 2
    _print(payload)
    return 0 if code == 0 and payload.get("ok") else 1


def _handle_performance(args: argparse.Namespace) -> int:
    if args.performance_command != "mail":
        _print({"ok": False, "error": f"Unbekannter Performance-Befehl: {args.performance_command}"})
        return 2
    command = [
        sys.executable,
        "-m",
        "mail_agent",
        "performance",
        "--limit",
        str(max(1, min(args.limit, 500))),
    ]
    if args.raw:
        command.append("--raw")
    return subprocess.run(command, check=False).returncode


def _handle_version(args: argparse.Namespace) -> int:
    payload = release_report(
        verify=bool(args.verify),
        include_history=bool(args.history),
        since=str(args.since or ""),
        limit=max(1, min(int(args.limit), 100)),
    )
    _print(payload)
    return 0 if payload.get("ok") else 1


def _jobs_result_ok(result: dict[str, Any]) -> bool:
    observer_cycle = result.get("observer_cycle")
    if isinstance(observer_cycle, dict):
        return bool(observer_cycle.get("ok"))
    return bool(result.get("ok"))


def _handle_jobs(args: argparse.Namespace) -> int:
    controller = JobController()
    try:
        if args.jobs_command == "status":
            result = controller.status(target=args.target, deep=args.deep, record=False)
        elif args.jobs_command == "check":
            result = controller.check(target=args.target, deep=args.deep)
        elif args.jobs_command == "alerts":
            result = controller.alerts()
        elif args.jobs_command == "on":
            result = controller.on(target=args.target, restart=False, run_now=not args.no_run_now)
        elif args.jobs_command == "restart":
            result = controller.on(target=args.target, restart=True, run_now=not args.no_run_now)
        elif args.jobs_command == "off":
            result = controller.off(target=args.target)
        else:
            raise ValueError(f"Unbekannter Jobs-Befehl: {args.jobs_command}")
        _print(result)
        return 0 if _jobs_result_ok(result) else 1
    except (OSError, ValueError) as exc:
        _print({"ok": False, "error": str(exc)})
        return 1


def _handle_scheduler(args: argparse.Namespace) -> int:
    scheduler = AdaptiveWorkScheduler()
    try:
        if args.scheduler_command == "status":
            result = scheduler.snapshot(recent_limit=max(1, min(int(args.limit), 500)))
        elif args.scheduler_command == "doctor":
            result = scheduler.doctor()
        elif args.scheduler_command == "activity":
            snapshot = scheduler.snapshot(recent_limit=1)
            result = {
                "ok": True,
                "generated_at": snapshot["generated_at"],
                "activity": snapshot["activity"],
            }
        elif args.scheduler_command == "focus":
            result = scheduler.record_activity(
                args.topic,
                source=args.source,
                boost_minutes=args.minutes,
            )
        else:
            raise ValueError(f"Unbekannter Scheduler-Befehl: {args.scheduler_command}")
        _print(result)
        return 0 if result.get("ok") else 1
    except (OSError, sqlite3.Error, ValueError) as exc:
        _print({"ok": False, "error": str(exc)})
        return 1
    finally:
        scheduler.close()


def _interactive_topic(args: argparse.Namespace) -> str:
    mapping = {
        "mail": "mail",
        "invoices": "mail",
        "orders": "mail",
        "portfolio": "portfolio",
        "nextcloud": "knowledge",
        "search": "knowledge",
        "index": "knowledge",
        "calendar": "planning",
        "tasks": "planning",
        "contacts": "planning",
    }
    return mapping.get(str(args.command or ""), "")


def _record_interactive_activity(args: argparse.Namespace) -> None:
    if os.environ.get("OPENCLAW_SCHEDULER_SOURCE", "").strip().casefold() in {
        "background-worker",
        "supervisor",
    }:
        return
    topic = _interactive_topic(args)
    if not topic:
        return
    scheduler = AdaptiveWorkScheduler()
    try:
        scheduler.record_activity(topic, source="interactive-cli", boost_minutes=30)
    finally:
        scheduler.close()


def main(argv: list[str] | None = None) -> int:
    os.umask(0o077)
    args = parser().parse_args(argv)
    config_path = Path(args.config).expanduser().resolve()

    # Job recovery must remain available even when the assistant configuration is
    # damaged. Deep tool checks will still report the configuration error.
    if args.command == "version":
        return _handle_version(args)

    # Static introspection is intentionally available in clean checkouts and
    # damaged installations. It must not load config, secrets or runtime state.
    if args.command == "tools" and args.tools_command == "list" and args.catalog:
        _print(static_tool_catalog())
        return 0

    if args.command == "capabilities" and args.schema:
        _print(capability_schema())
        return 0

    if args.command == "jobs":
        return _handle_jobs(args)

    if args.command == "scheduler":
        return _handle_scheduler(args)

    if args.command == "ollama":
        return _handle_ollama(args)

    if args.command == "performance":
        return _handle_performance(args)

    if args.command == "setup" and args.setup_command == "init":
        _print({"created": initialize_local_files(), "config": str(DEFAULT_CONFIG)})
        return 0

    if not config_path.exists():
        print(
            "Personal-Assistant-Konfiguration fehlt. Zuerst: ./scripts/assistant.sh setup init",
            file=sys.stderr,
        )
        return 2
    try:
        _load_secrets(config_path)
        config = load_config(config_path)
    except Exception as exc:
        print(f"Konfigurationsfehler: {exc}", file=sys.stderr)
        return 2
    _logging(config.runtime.log_file, args.verbose)

    try:
        _record_interactive_activity(args)
    except (OSError, sqlite3.Error, ValueError) as exc:
        print(f"Scheduler-Warnung: Aktivitaet konnte nicht gespeichert werden: {exc}", file=sys.stderr)

    if args.command == "invoices" and args.invoices_command != "files":
        return run_invoice_external(args)

    if args.command == "setup" and args.setup_command == "tools":
        try:
            result = configure_mail_tools(
                owner_email=args.owner_email,
                calendar_resource_id=args.calendar_resource,
                invoice_folder=args.invoice_folder,
                enable_invoices=not args.disable_invoices,
                enable_calendar_mail=not args.disable_calendar_mail,
                approve_permissions=args.approve_permissions,
            )
            _print(result)
            return 0
        except Exception as exc:
            print(f"Tool-Setup fehlgeschlagen: {exc}", file=sys.stderr)
            return 1

    if args.command == "setup" and args.setup_command == "mail-move":
        try:
            result = configure_mail_move_tools(
                enable=not args.disable,
                max_batch=args.max_batch,
                approve_permissions=args.approve_permissions,
            )
            _print(result)
            return 0
        except Exception as exc:
            print(f"Direktes Mail-Setup fehlgeschlagen: {exc}", file=sys.stderr)
            return 1

    if args.command == "setup" and args.setup_command == "portfolio":
        try:
            result = configure_portfolio_tools(
                enable=not args.disable,
                provider=args.provider,
                interval_minutes=args.interval_minutes,
                stale_warning_minutes=args.stale_warning_minutes,
                stale_critical_minutes=args.stale_critical_minutes,
                approve_permissions=args.approve_permissions,
            )
            _print(result)
            return 0
        except Exception as exc:
            print(f"Portfolio-Setup fehlgeschlagen: {exc}", file=sys.stderr)
            return 1

    if args.command == "setup" and args.setup_command == "mail-sources":
        try:
            result = configure_mail_sources(
                primary=args.primary,
                quarantine_folders=tuple(args.quarantine_folder or ["Spam"]),
                max_per_run=args.max_per_run,
                rescue_only=not args.full_triage,
            )
            _print(result)
            return 0
        except Exception as exc:
            print(f"Mailquellen-Setup fehlgeschlagen: {exc}", file=sys.stderr)
            return 1

    if args.command == "setup" and args.setup_command == "workspace":
        try:
            outbox = Path(args.outbox).expanduser()
            if not outbox.is_absolute():
                outbox = (config.path.parents[1] / outbox).resolve()
            result = configure_workspace_tools(
                resource_id=args.resource,
                root=args.root,
                outbox=outbox,
                allow_mkdir=not args.disable_mkdir,
                allow_upload=not args.disable_upload,
                allow_write_text=not args.disable_write_text,
                allow_move=not args.disable_move,
                approve_permissions=args.approve_permissions,
            )
            _print(result)
            return 0
        except Exception as exc:
            print(f"Workspace-Setup fehlgeschlagen: {exc}", file=sys.stderr)
            return 1

    if args.command == "setup" and args.setup_command == "calendar":
        try:
            result = configure_calendar_tools(
                resource_id=args.resource,
                timezone=args.timezone,
                default_duration_minutes=args.default_duration_minutes,
                max_duration_hours=args.max_duration_hours,
                max_future_days=args.max_future_days,
                approve_permissions=args.approve_permissions,
            )
            _print(result)
            return 0
        except Exception as exc:
            print(f"Kalender-Setup fehlgeschlagen: {exc}", file=sys.stderr)
            return 1

    if args.command == "setup" and args.setup_command == "tasks":
        try:
            result = configure_tasks_tools(
                resource_id=args.resource,
                timezone=args.timezone,
                allow_create=not args.disable_create,
                allow_list=not args.disable_list,
                max_future_days=args.max_future_days,
                approve_permissions=args.approve_permissions,
            )
            _print(result)
            return 0
        except Exception as exc:
            print(f"Aufgaben-Setup fehlgeschlagen: {exc}", file=sys.stderr)
            return 1

    direct_mail_commands = {
        "move-status",
        "list",
        "search",
        "search-local",
        "read",
        "reply-draft",
        "reply-send",
        "compose-draft",
        "compose-send",
        "move",
    }
    direct_review_correction = bool(
        args.command == "mail"
        and args.mail_command == "review"
        and getattr(args, "review_command", "") == "correct"
    )
    direct_index_diagnostic = bool(
        args.command == "mail"
        and args.mail_command == "index"
        and getattr(args, "index_command", "") in {"status", "doctor"}
    )
    if (
        args.command == "mail"
        and args.mail_command not in direct_mail_commands
        and not direct_review_correction
        and not direct_index_diagnostic
    ):
        return run_mail_external(args)

    if args.command == "setup" and args.setup_command == "nextcloud":
        try:
            result = configure_nextcloud(
                config,
                url=args.url or "",
                username=args.username or "",
                token=args.token or "",
                interactive=not args.non_interactive and not args.use_existing,
                use_existing=args.use_existing,
            )
            _print(result)
            return 0
        except Exception as exc:
            print(f"Nextcloud-Setup fehlgeschlagen: {exc}", file=sys.stderr)
            return 1

    assistant = create_personal_assistant(config)
    try:
        if args.command == "setup" and args.setup_command == "standard-operations":
            result = assistant.standard_operations_configure(
                approve_permissions=bool(args.yes),
            )
            _print(result)
            return 0
        if args.command == "setup" and args.setup_command == "deck-orders":
            if not args.approve_permissions or not sys.stdin.isatty():
                raise PermissionError(
                    "Deck-Setup mit Schreibrechten erfordert --approve-permissions in einem "
                    "interaktiven Terminal"
                )
            confirmation = input(
                "Deck-Board und fehlende Spalten anlegen/verwenden? Tippe exakt APPROVE: "
            ).strip()
            if confirmation != "APPROVE":
                raise PermissionError("Deck-Setup abgebrochen")
            prepared = assistant.deck_prepare_orders_board(
                board_id=args.board_id, board_title=args.board_title, create_board=args.create_board
            )
            configured = configure_deck_orders_tools(
                board_id=int(prepared["board_id"]),
                board_title=str(prepared["board_title"]),
                auto_process_mail=not args.disable_auto_mail,
                min_confidence=args.min_confidence,
                approve_permissions=True,
            )
            _print({"ok": True, "prepared": prepared, "configured": configured})
            return 0
        handled = dispatch_domain_command(args, assistant, _print)
        if handled is not None:
            return handled
        raise ValueError(f"Unbekannter Assistant-Befehl: {args.command}")
    except (ValueError, KeyError, PermissionError, FileNotFoundError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except Exception as exc:
        logging.getLogger(__name__).exception("Assistant command failed")
        print(f"Fehler: {exc}", file=sys.stderr)
        return 1
    finally:
        assistant.close()
    return 0
