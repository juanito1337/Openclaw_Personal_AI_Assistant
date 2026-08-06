from __future__ import annotations

import argparse
import sys
from collections.abc import Callable
from typing import Any


def handle(args: argparse.Namespace, assistant: Any, emit: Callable[[Any], None]) -> int:
    command = args.nextcloud_command
    if command == "doctor":
        result = assistant.nextcloud_discovery.root_health()
    elif command == "discover":
        result = assistant.discover_nextcloud(persist=not args.no_persist)
    elif command == "sync":
        result = assistant.sync_nextcloud()
    elif command == "list":
        result = assistant.list_nextcloud_files(args.path, max_depth=args.max_depth)
    elif command == "mkdir":
        result = assistant.workspace_mkdir(args.path)
    elif command == "upload":
        result = assistant.workspace_upload(args.local, args.path, content_type=args.content_type)
    elif command == "write-text":
        if args.text is None:
            if sys.stdin.isatty():
                raise ValueError("Text fehlt: --text verwenden oder Inhalt ueber stdin uebergeben")
            content = sys.stdin.read()
        else:
            content = args.text
        result = assistant.workspace_write_text(args.path, content, content_type=args.content_type)
    elif command == "move":
        result = assistant.workspace_move(args.source, args.destination)
    else:
        raise ValueError(f"Unbekannter Nextcloud-Befehl: {command}")
    emit(result)
    return 0
