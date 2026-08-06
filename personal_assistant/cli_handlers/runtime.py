from __future__ import annotations

import argparse
from collections.abc import Callable
from typing import Any

from personal_assistant.release import release_report


def handle(args: argparse.Namespace, assistant: Any, emit: Callable[[Any], None]) -> int:
    if args.command == "doctor":
        result = assistant.doctor(live=True)
        result["release"] = release_report(verify=True)
        emit(result)
        return (
            0
            if (
                result["database"]["ok"]
                and result["resources"]["ok"]
                and result["scheduler"]["ok"]
                and result["release"].get("ok")
                and result["runtime"].get("ok")
            )
            else 1
        )
    if args.command == "status":
        doctor = assistant.doctor(live=False)
        emit(
            {
                "release": release_report(verify=True),
                "runtime": doctor["runtime"],
                "doctor": doctor,
                "resources": len(assistant.registry.resources),
                "actions": {
                    status: len(assistant.storage.list_actions(status=status, limit=10000))
                    for status in ("proposed", "approved", "failed")
                },
            }
        )
        return 0
    if args.command == "capabilities":
        emit(assistant.capabilities())
        return 0
    if args.command == "tools":
        emit(assistant.tools())
        return 0
    if args.command == "monitor":
        if args.monitor_command == "status":
            emit(assistant.monitor.report(days=args.days, live=args.live))
        elif args.monitor_command == "record":
            emit(assistant.monitor.record(days=args.days, live=args.live))
        else:
            emit(assistant.monitor.history(days=args.days, limit=args.limit))
        return 0
    raise ValueError(f"Unbekannter Runtime-Befehl: {args.command}")
