from __future__ import annotations

import argparse
from collections.abc import Callable
from typing import Any


def handle(args: argparse.Namespace, assistant: Any, emit: Callable[[Any], None]) -> int:
    command = args.contacts_command
    if command == "discover":
        result = assistant.contacts_discover()
    elif command == "configure":
        if not args.yes:
            raise PermissionError("Adressbuchauswahl benoetigt --yes nach ausdruecklichem Nutzerauftrag")
        if args.read_only and args.create_only:
            raise ValueError("--read-only und --create-only koennen nicht gemeinsam verwendet werden")
        if args.create_only and args.allow_update:
            raise ValueError(
                "--allow-update benoetigt Leserechte und kann nicht mit --create-only verwendet werden"
            )
        result = assistant.contacts_configure(
            resource_id=args.resource,
            allow_create=not args.read_only,
            allow_list=not args.create_only,
            allow_update=bool(args.allow_update),
            max_results=args.max_results,
        )
    elif command == "status":
        result = assistant.direct_contacts_status(live=not args.no_live)
    elif command == "list":
        result = assistant.contacts_list(limit=args.limit)
    elif command == "search":
        result = assistant.contacts_search(args.query, limit=args.limit)
    elif command == "create":
        if not args.yes:
            raise PermissionError("Kontaktanlage benoetigt --yes nach ausdruecklichem Nutzerauftrag")
        result = assistant.contact_create(
            name=args.name,
            emails=tuple(args.email or []),
            phones=tuple(args.phone or []),
            organization=args.organization,
            note=args.note,
            allow_name_collision=bool(args.allow_name_collision),
        )
    elif command == "update":
        if not args.yes:
            raise PermissionError("Kontakt-Aktualisierung benoetigt --yes nach ausdruecklichem Nutzerauftrag")
        for clear, value, clear_option, value_option in (
            (args.clear_emails, args.email, "--clear-emails", "--email"),
            (args.clear_phones, args.phone, "--clear-phones", "--phone"),
            (
                args.clear_organization,
                args.organization,
                "--clear-organization",
                "--organization",
            ),
            (args.clear_note, args.note, "--clear-note", "--note"),
        ):
            if clear and value is not None:
                raise ValueError(
                    f"{clear_option} und {value_option} koennen nicht gemeinsam verwendet werden"
                )
        result = assistant.contact_update(
            uid=args.uid,
            name=args.name,
            emails=() if args.clear_emails else (tuple(args.email) if args.email is not None else None),
            phones=() if args.clear_phones else (tuple(args.phone) if args.phone is not None else None),
            organization="" if args.clear_organization else args.organization,
            note="" if args.clear_note else args.note,
            expected_name=args.expected_name,
            expected_email=args.expected_email,
            allow_name_collision=bool(args.allow_name_collision),
        )
    elif command == "from-mail":
        if args.dry_run and args.yes:
            raise ValueError("--dry-run und --yes koennen nicht gemeinsam verwendet werden")
        if not args.dry_run and not args.yes:
            raise PermissionError("Mail-Kontaktvorschlag benoetigt --dry-run oder --yes")
        result = assistant.contact_from_mail(
            folder=args.folder,
            message_id=args.message_id,
            expected_subject=args.expected_subject,
            dry_run=bool(args.dry_run),
            name=args.name,
            organization=args.organization,
            phones=tuple(args.phone or []),
            note=args.note,
            allow_name_collision=bool(args.allow_name_collision),
        )
    else:
        raise ValueError(f"Unbekannter Kontaktbefehl: {command}")
    emit(result)
    return 0 if result.get("ok") else 1
