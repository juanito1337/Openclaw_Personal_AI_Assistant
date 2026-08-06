from __future__ import annotations

import argparse
from collections.abc import Callable
from typing import Any

from . import calendar, contacts, core, mail, nextcloud, orders, portfolio, runtime, security, tasks

Emitter = Callable[[Any], None]

HANDLERS = {
    "calendar": calendar.handle,
    "actions": core.handle,
    "contacts": contacts.handle,
    "index": core.handle,
    "deck": orders.handle,
    "monitor": runtime.handle,
    "mail": mail.handle,
    "nextcloud": nextcloud.handle,
    "orders": orders.handle,
    "portfolio": portfolio.handle,
    "resources": core.handle,
    "search": core.handle,
    "security": security.handle,
    "tasks": tasks.handle,
    "settings": core.handle,
    "doctor": runtime.handle,
    "status": runtime.handle,
    "capabilities": runtime.handle,
    "tools": runtime.handle,
}


def dispatch(args: argparse.Namespace, assistant: Any, emit: Emitter) -> int | None:
    handler = HANDLERS.get(str(args.command or ""))
    return None if handler is None else handler(args, assistant, emit)


__all__ = ["HANDLERS", "dispatch"]
