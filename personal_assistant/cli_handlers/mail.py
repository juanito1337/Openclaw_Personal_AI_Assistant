from __future__ import annotations

import argparse
import os
import subprocess
import sys
from collections.abc import Callable
from typing import Any


def run_external(args: argparse.Namespace) -> int:
    command = [sys.executable, "-m", "mail_agent"]
    if args.mail_command in {"status", "doctor", "guide"}:
        command.append(args.mail_command)
    elif args.mail_command == "dry-run":
        command += ["run", "--dry-run", "--no-digest", "--limit", str(max(1, args.limit))]
    elif args.mail_command == "run":
        command += ["run", "--no-digest", "--limit", str(max(1, args.limit))]
        if args.drain:
            command += [
                "--drain",
                "--batch-size",
                str(max(1, args.batch_size)),
                "--max-messages",
                str(max(1, args.max_messages)),
                "--max-runtime",
                str(max(1, args.max_runtime)),
                "--max-batches",
                str(max(1, args.max_batches)),
            ]
    elif args.mail_command == "orders-import":
        command += ["orders-import", "--limit", str(max(1, args.limit))]
        if args.dry_run:
            command.append("--dry-run")
    elif args.mail_command == "spam-review":
        command += ["spam-review", "--limit", str(max(1, args.limit))]
        if args.dry_run:
            command.append("--dry-run")
    elif args.mail_command == "review":
        command += ["review", args.review_command]
        if args.review_command == "status":
            command += ["--days", str(max(1, args.days))]
        elif args.review_command == "list":
            command += ["--reason", args.reason, "--limit", str(max(1, args.limit))]
        elif args.review_command == "suggest":
            command += [
                "--folder",
                args.folder,
                "--message-id",
                args.message_id,
                "--expected-subject",
                args.expected_subject,
            ]
    elif args.mail_command == "folders":
        command += ["folders", args.folders_command]
        if args.folders_command == "apply" and args.yes:
            command.append("--yes")
    elif args.mail_command == "learning":
        command += ["training", args.learning_command]
        if args.learning_command in {"feedback", "not-spam", "mixed-senders", "conflicts", "evaluate"}:
            command += ["--limit", str(max(1, args.limit))]
            if args.learning_command == "conflicts" and args.id:
                command += ["--id", args.id]
        elif args.learning_command == "dataset-export":
            command += ["--output", args.output, "--limit", str(max(1, args.limit))]
        elif args.learning_command == "folder-create":
            command += ["--parent", args.parent, "--name", args.name]
            if args.label:
                command += ["--label", args.label]
            if args.yes:
                command.append("--yes")
        elif args.learning_command == "folder-disable":
            command += ["--folder", args.folder]
            if args.yes:
                command.append("--yes")
    else:
        raise ValueError(f"Unbekanntes Mail-Werkzeug: {args.mail_command}")
    environment = os.environ.copy()
    environment["OPENCLAW_OLLAMA_PRIORITY"] = "interactive"
    environment["OPENCLAW_OLLAMA_SOURCE"] = "openclaw-mail-tool"
    return subprocess.run(command, check=False, env=environment).returncode


def handle(args: argparse.Namespace, assistant: Any, emit: Callable[[Any], None]) -> int:
    command = args.mail_command
    if command == "review" and args.review_command == "correct":
        result = assistant.mail_correct_review(
            source=args.source,
            message_id=args.message_id,
            expected_subject=args.expected_subject,
            verdict=args.verdict,
            label=args.label,
            approved=args.yes,
        )
    elif command == "move-status":
        result = assistant.mail_move_status()
    elif command == "list":
        result = assistant.mail_list_messages(args.folder, limit=args.limit)
    elif command == "search":
        result = assistant.mail_search_messages(args.query, limit=args.limit)
    elif command == "read":
        result = assistant.mail_read_message(
            args.folder,
            args.message_id,
            expected_subject=args.expected_subject,
        )
    elif command == "reply-draft":
        result = assistant.mail_draft_reply(
            args.folder,
            args.message_id,
            args.body,
            expected_subject=args.expected_subject,
        )
    elif command == "reply-send":
        result = assistant.mail_send_reply(args.draft_id, approved=args.yes)
    elif command == "compose-draft":
        result = assistant.mail_draft_message(args.to, args.subject, args.body)
    elif command == "compose-send":
        result = assistant.mail_send_message(args.draft_id, approved=args.yes)
    elif command == "move":
        result = assistant.mail_move_message(
            source=args.source,
            destination=args.destination,
            message_id=args.message_id,
            expected_subject=args.expected_subject,
            dry_run=args.dry_run,
        )
    else:
        raise ValueError(f"Unbekannter direkter Mail-Befehl: {command}")
    emit(result)
    return 0 if result.get("ok") else 1
