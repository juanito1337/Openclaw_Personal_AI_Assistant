from __future__ import annotations

import argparse
import os
import sys
from typing import Any

from .mail import run_mail_child


def run_external(args: argparse.Namespace, *, initial=None) -> int:
    command = [sys.executable, "-m", "mail_agent", "invoices", args.invoices_command]
    if args.invoices_command in {"status", "audit"}:
        pass
    elif args.invoices_command == "list":
        if args.year:
            command += ["--year", str(args.year)]
        if args.status:
            command += ["--status", args.status]
        command += ["--limit", str(max(1, args.limit))]
    elif args.invoices_command == "review":
        command += ["--limit", str(max(1, args.limit))]
    elif args.invoices_command == "export":
        command += ["--year", str(args.year)]
        if args.nextcloud:
            command.append("--nextcloud")
        if args.filename:
            command += ["--filename", args.filename]
        if args.dry_run:
            command.append("--dry-run")
        if args.yes:
            command.append("--yes")
    elif args.invoices_command == "backfill":
        command += ["--year", str(args.year), "--limit", str(max(1, args.limit))]
        if args.dry_run:
            command.append("--dry-run")
        if args.yes:
            command.append("--yes")
    elif args.invoices_command == "reprocess":
        command += [
            "--status",
            args.status,
            "--source-year",
            str(args.source_year),
            "--limit",
            str(max(1, args.limit)),
            "--dry-run",
        ]
    elif args.invoices_command == "reprocess-apply":
        command += [
            "--hash",
            args.attachment_hash,
            "--expected-preview-sha256",
            args.expected_preview_sha256,
        ]
        if args.yes:
            command.append("--yes")
    elif args.invoices_command == "correct":
        command += [
            "--hash",
            args.attachment_hash,
            "--date",
            args.invoice_date,
            "--number",
            args.invoice_number,
            "--supplier",
            args.supplier,
            "--category",
            args.category,
            "--gross",
            args.gross,
            "--currency",
            args.currency,
        ]
        if args.net:
            command += ["--net", args.net]
        if args.tax:
            command += ["--tax", args.tax]
        if args.due_date:
            command += ["--due-date", args.due_date]
        if args.yes:
            command.append("--yes")
    environment = os.environ.copy()
    environment["OPENCLAW_OLLAMA_PRIORITY"] = "interactive"
    environment["OPENCLAW_OLLAMA_SOURCE"] = "openclaw-invoice-tool"
    reads_nextcloud = args.invoices_command in {"backfill", "reprocess", "reprocess-apply", "correct"} or (
        args.invoices_command == "export" and not args.dry_run
    )
    return run_mail_child(command, environment, initial, reads_nextcloud=reads_nextcloud)


def handle(args: argparse.Namespace, assistant: Any, emit: Any) -> int:
    if args.invoices_command == "files":
        result = assistant.list_invoice_files(limit=max(1, args.limit))
        emit(result)
        return 0 if result.get("ok", False) else 1
    return run_external(args)
