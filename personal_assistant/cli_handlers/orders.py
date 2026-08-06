from __future__ import annotations

import argparse
from collections.abc import Callable
from typing import Any


def handle(args: argparse.Namespace, assistant: Any, emit: Callable[[Any], None]) -> int:
    if args.command == "deck":
        if args.deck_command == "discover":
            emit(assistant.deck_discover())
            return 0
        result = assistant.deck_orders_status(live=not args.no_live)
        emit(result)
        return 0 if result.get("ok") else 1
    if args.orders_command == "status":
        result = assistant.deck_orders_status(live=not args.no_live)
    elif args.orders_command == "list":
        result = assistant.orders_list(status=args.status, limit=args.limit)
    elif args.orders_command == "sync":
        result = assistant.orders_sync(limit=args.limit)
    elif args.orders_command == "due-date-backfill":
        if not args.dry_run and not args.yes:
            raise PermissionError(
                "Produktiver Due-Date-Backfill benoetigt --yes nach ausdruecklichem Nutzerauftrag"
            )
        result = assistant.orders_due_date_backfill(limit=args.limit, dry_run=bool(args.dry_run))
    else:
        raise ValueError(f"Unbekannter Bestellbefehl: {args.orders_command}")
    emit(result)
    return 0 if result.get("ok", True) else 1
