from __future__ import annotations

import argparse
from collections.abc import Callable
from typing import Any


def handle(args: argparse.Namespace, assistant: Any, emit: Callable[[Any], None]) -> int:
    if args.security_command != "antivirus":
        raise ValueError(f"Unbekannter Sicherheitsbefehl: {args.security_command}")
    if args.antivirus_command == "doctor":
        result = assistant.antivirus_doctor(live_scan=not args.no_live_scan)
        emit(result)
        return 0 if result.get("ok") else 1
    if args.antivirus_command == "self-test":
        result = assistant.antivirus_self_test()
        emit(result)
        return 0 if result.get("ok") else 1
    if args.antivirus_command == "scan":
        result = assistant.antivirus_scan_path(args.file, use_cache=not args.no_cache)
        emit(result)
        return 0 if result.get("status") == "clean" else 1
    raise ValueError(f"Unbekannter Antivirus-Befehl: {args.antivirus_command}")
