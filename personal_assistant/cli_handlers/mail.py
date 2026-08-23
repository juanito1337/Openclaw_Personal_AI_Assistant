from __future__ import annotations

import argparse
import os
import subprocess
import sys
from collections.abc import Callable
from typing import Any

from personal_assistant.mail_search import MailSearchFilters


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
        if args.folders_command == "activate-relevant":
            command += ["--relevant", args.relevant]
        if args.folders_command in {"apply", "activate-relevant"} and args.yes:
            command.append("--yes")
    elif args.mail_command == "index":
        command += ["index", args.index_command]
        if args.index_command == "capabilities":
            if args.no_raw_probe:
                command.append("--no-raw-probe")
        elif args.index_command in {"backfill", "canary"}:
            if args.index_command == "canary":
                for folder in args.folder:
                    command += ["--folder", folder]
            command += [
                "--page-size",
                str(args.page_size),
                "--max-pages",
                str(args.max_pages),
                "--max-messages",
                str(args.max_messages),
                "--max-bytes",
                str(args.max_bytes),
                "--max-message-bytes",
                str(args.max_message_bytes),
                "--max-runtime",
                str(args.max_runtime),
                "--request-interval",
                str(args.request_interval),
            ]
            if args.yes:
                command.append("--yes")
        elif args.index_command == "reconcile":
            command += [
                "--max-folders", str(args.max_folders),
                "--max-messages", str(args.max_messages),
                "--max-bytes", str(args.max_bytes),
                "--max-message-bytes", str(args.max_message_bytes),
                "--max-runtime", str(args.max_runtime),
                "--request-interval", str(args.request_interval),
                "--retention-generations", str(args.retention_generations),
            ]
            if args.yes:
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
        elif args.learning_command == "forget-feedback":
            command += [str(args.id)]
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
    elif command == "index" and args.index_command == "status":
        result = assistant.mail_index_status()
    elif command == "index" and args.index_command == "doctor":
        result = assistant.mail_index_doctor()
    elif command == "index" and args.index_command == "shadow":
        result = assistant.mail_index_shadow(args.query, limit=args.limit)
    elif command == "move-status":
        result = assistant.mail_move_status()
    elif command == "list":
        result = assistant.mail_list_messages(args.folder, limit=args.limit)
    elif command == "search":
        has_attachment = (
            None if args.has_attachment is None else args.has_attachment == "yes"
        )
        result = assistant.mail_search_messages(
            args.query,
            limit=args.limit,
            mode=args.mode,
            context_limit=args.context_limit,
            filters=MailSearchFilters(
                sender=args.sender,
                participant=args.participant,
                after=args.after,
                before=args.before,
                folder=args.folder,
                category=args.category,
                review_reason=args.review_reason,
                has_attachment=has_attachment,
                attachment_type=args.attachment_type,
                tags=tuple(args.tag),
            ),
        )
    elif command == "search-local":
        has_attachment = (
            None if args.has_attachment is None else args.has_attachment == "yes"
        )
        result = assistant.storage.search_mail_lexical(
            args.query,
            filters=MailSearchFilters(
                sender=args.sender,
                participant=args.participant,
                after=args.after,
                before=args.before,
                folder=args.folder,
                category=args.category,
                review_reason=args.review_reason,
                has_attachment=has_attachment,
                attachment_type=args.attachment_type,
                tags=tuple(args.tag),
            ),
            limit=args.limit,
            max_age_seconds=assistant.config.search.mail_projection_max_age_seconds,
            context_limit=args.context_limit,
        )
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
