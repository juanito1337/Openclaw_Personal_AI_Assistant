from __future__ import annotations

import argparse
import os
import subprocess
import sys
from typing import Any


def run_external(args: argparse.Namespace) -> int:
    command = [sys.executable, "-m", "mail_agent", "invoices", args.invoices_command]
    if args.invoices_command == "list":
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
    return subprocess.run(command, check=False, env=environment).returncode


def handle(args: argparse.Namespace, assistant: Any, emit: Any) -> int:
    del assistant, emit
    return run_external(args)
