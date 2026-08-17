from __future__ import annotations

import argparse
import sys
from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path
from typing import Any

from personal_assistant.models import Resource


def handle(args: argparse.Namespace, assistant: Any, emit: Callable[[Any], None]) -> int:
    if args.command == "resources":
        if args.resources_command == "list":
            emit([asdict(item) for item in assistant.registry.list(kind=args.kind)])
            return 0
        permissions = tuple(value.strip() for value in args.permissions.split(",") if value.strip())
        existing = assistant.registry.resources.get(args.id)
        existing_permissions = set(existing.permissions) if existing else set()
        expanded = set(permissions) - existing_permissions - {"read"}
        if expanded:
            if not args.approve_permissions or not sys.stdin.isatty():
                raise PermissionError(
                    "Neue oder erweiterte Schreibrechte benoetigen --approve-permissions "
                    "in einem interaktiven Terminal"
                )
            confirmation = input(
                "Berechtigungen erweitern (" + ", ".join(sorted(expanded)) + ")? Tippe exakt APPROVE: "
            ).strip()
            if confirmation != "APPROVE":
                raise PermissionError("Berechtigungserweiterung abgebrochen")
        resource = Resource(
            id=args.id,
            kind=args.kind,
            connector=args.connector,
            enabled=not args.disabled,
            remote_id=args.remote_id,
            permissions=permissions,
        )
        backup = assistant.registry.upsert(resource)
        assistant.storage.audit("resource.upsert", asdict(resource), resource_id=resource.id, actor="user")
        emit({"resource": asdict(resource), "backup": str(backup or "")})
        return 0
    if args.command == "index":
        result = assistant.sync_mail() if args.index_command == "mail" else assistant.sync_all()
        emit(result)
        return 0 if result.get("ok", True) else 1
    if args.command == "search":
        limit = args.limit or assistant.config.search.default_limit
        results = assistant.storage.search(
            args.query,
            limit=limit,
            source_type=args.source_type,
            resource_id=args.resource,
        )
        emit([asdict(item) for item in results])
        return 0
    if args.command == "actions":
        return _actions(args, assistant, emit)
    if args.command == "settings":
        if args.settings_command == "list":
            emit(assistant.settings.list_safe())
        else:
            backup = assistant.settings.set_safe(args.key, args.value, actor="user")
            emit({"ok": True, "backup": str(backup), "key": args.key, "value": args.value})
        return 0
    raise ValueError(f"Unbekannter Core-Befehl: {args.command}")


def _actions(args: argparse.Namespace, assistant: Any, emit: Callable[[Any], None]) -> int:
    if args.actions_command == "list":
        emit([asdict(item) for item in assistant.storage.list_actions(args.status, args.limit)])
        return 0
    if args.actions_command == "plan-upload":
        local_path = Path(args.local_path).expanduser().resolve()
        if not local_path.is_file():
            raise FileNotFoundError(local_path)
        scan = assistant.antivirus.scan_path(local_path, source_type="action-plan-upload")
        if not scan.clean:
            raise PermissionError(
                "Upload-ActionPlan durch Virenscanner blockiert: "
                + (scan.signature or scan.detail or scan.status)
            )
        plan = assistant.actions.plan(
            "files.create",
            args.resource,
            {
                "local_path": str(local_path),
                "path": args.remote_path,
                "content_type": args.content_type,
                "overwrite": False,
            },
        )
        emit(asdict(plan))
        return 0
    if args.actions_command in {"plan-event", "plan-task"}:
        path = Path(args.ics_file).expanduser().resolve()
        ics = path.read_text(encoding="utf-8")
        action_type = "calendar.create" if args.actions_command == "plan-event" else "tasks.create"
        plan = assistant.actions.plan(action_type, args.resource, {"ics": ics, "uid": args.uid})
        emit(asdict(plan))
        return 0
    if args.actions_command == "approve":
        if not sys.stdin.isatty():
            raise PermissionError("ActionPlan-Freigaben sind derzeit nur interaktiv erlaubt")
        if input("ActionPlan freigeben? Tippe exakt APPROVE: ").strip() != "APPROVE":
            raise PermissionError("Freigabe abgebrochen")
        emit(asdict(assistant.actions.approve(args.action_id)))
        return 0
    result = assistant.actions.execute(args.action_id)
    emit(asdict(result))
    return 0 if result.status == "completed" else 1
