from __future__ import annotations

import argparse
from collections.abc import Callable
from typing import Any


def handle(args: argparse.Namespace, assistant: Any, emit: Callable[[Any], None]) -> int:
    command = args.calendar_command
    if command == "discover":
        result = assistant.calendar_discover()
    elif command == "status":
        result = assistant.direct_calendar_status()
    elif command == "configure":
        if not args.yes:
            raise PermissionError("Kalenderauswahl benoetigt --yes nach ausdruecklichem Nutzerauftrag")
        result = assistant.calendar_configure(
            resource_id=args.resource,
            timezone_name=args.timezone,
            default_duration_minutes=args.default_duration_minutes,
            max_duration_hours=args.max_duration_hours,
            max_future_days=args.max_future_days,
            allow_update=bool(args.allow_update),
        )
    elif command == "create":
        result = assistant.calendar_create(
            title=args.title,
            start=args.start,
            end=args.end,
            duration_minutes=args.duration_minutes,
            location=args.location,
            description=args.description,
            uid=args.uid,
        )
    elif command == "list":
        result = assistant.calendar_list(limit=args.limit)
    elif command == "search":
        result = assistant.calendar_search(args.query, limit=args.limit)
    elif command == "from-mail":
        if args.dry_run:
            if args.yes or args.preview_digest or args.candidate_id:
                raise ValueError(
                    "Die read-only Vorschau akzeptiert keine Freigabe oder Kandidatenbindung"
                )
            result = assistant.calendar_from_mail_preview(
                folder=args.folder,
                message_id=args.message_id,
                expected_subject=args.expected_subject,
            )
        else:
            if not args.yes:
                raise PermissionError(
                    "Mail-zu-Kalender-Create benoetigt --yes nach ausdruecklicher Einzelfreigabe"
                )
            if not args.preview_digest or not args.candidate_id:
                raise ValueError("Preview-Digest und Kandidaten-ID sind fuer Create erforderlich")
            result = assistant.calendar_from_mail_create(
                folder=args.folder,
                message_id=args.message_id,
                expected_subject=args.expected_subject,
                preview_digest=args.preview_digest,
                candidate_id=args.candidate_id,
                approved=True,
            )
    elif command == "update":
        if not args.yes:
            raise PermissionError(
                "Kalender-Aktualisierung benoetigt --yes nach ausdruecklichem Nutzerauftrag"
            )
        result = assistant.calendar_update(
            uid=args.uid,
            title=args.title,
            start=args.start,
            end=args.end,
            duration_minutes=args.duration_minutes,
            location=args.location,
            clear_location=bool(args.clear_location),
            description=args.description,
            clear_description=bool(args.clear_description),
            expected_title=args.expected_title,
            expected_start=args.expected_start,
            allow_recurring_series=bool(args.allow_recurring_series),
        )
    else:
        raise ValueError(f"Unbekannter Kalenderbefehl: {command}")
    emit(result)
    return 0 if result.get("ok") else 1
