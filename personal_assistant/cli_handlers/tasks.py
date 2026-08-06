from __future__ import annotations

import argparse
from collections.abc import Callable
from typing import Any


def handle(args: argparse.Namespace, assistant: Any, emit: Callable[[Any], None]) -> int:
    command = args.tasks_command
    if command == "discover":
        result = assistant.tasks_discover()
    elif command == "configure":
        if not args.yes:
            raise PermissionError("Aufgabenlistenauswahl benoetigt --yes nach ausdruecklichem Nutzerauftrag")
        if args.read_only and args.create_only:
            raise ValueError("--read-only und --create-only koennen nicht gemeinsam verwendet werden")
        if args.create_only and args.allow_update:
            raise ValueError(
                "--allow-update benoetigt Leserechte und kann nicht mit --create-only verwendet werden"
            )
        result = assistant.tasks_configure(
            resource_id=args.resource,
            timezone_name=args.timezone,
            allow_create=not args.read_only,
            allow_list=not args.create_only,
            max_future_days=args.max_future_days,
            allow_update=bool(args.allow_update),
        )
    elif command == "status":
        result = assistant.direct_tasks_status(live=not args.no_live)
    elif command == "list":
        result = assistant.tasks_list(include_completed=args.include_completed, limit=args.limit)
    elif command == "create":
        result = assistant.task_create(
            title=args.title,
            due=args.due,
            start=args.start,
            description=args.description,
            priority=args.priority,
            categories=tuple(args.category or []),
            uid=args.uid,
        )
    elif command == "update":
        if not args.yes:
            raise PermissionError(
                "Aufgaben-Aktualisierung benoetigt --yes nach ausdruecklichem Nutzerauftrag"
            )
        result = assistant.task_update(
            uid=args.uid,
            title=args.title,
            due=args.due,
            clear_due=bool(args.clear_due),
            start=args.start,
            clear_start=bool(args.clear_start),
            description=args.description,
            clear_description=bool(args.clear_description),
            priority=args.priority,
            categories=tuple(args.category) if args.category is not None else None,
            clear_categories=bool(args.clear_categories),
            status=args.status,
            percent_complete=args.percent_complete,
            expected_title=args.expected_title,
            expected_due=args.expected_due,
            allow_recurring=bool(args.allow_recurring),
        )
    else:
        raise ValueError(f"Unbekannter Aufgabenbefehl: {command}")
    emit(result)
    return 0 if result.get("ok") else 1
