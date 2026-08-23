#!/usr/bin/env python3
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from mail_agent.imap_inventory import ImapConnectionSettings, NativeImapInventoryBackend
from mail_agent.search_backfill import BackfillLimits, MailSearchBackfill
from mail_agent.search_reconcile import MailSearchReconciler, ReconcileLimits
from personal_assistant.contracts.mail_index_authority import MailSearchEvidence
from personal_assistant.contracts.mail_projection import load_search_projection


def raw_mail(name: str) -> bytes:
    return (
        "From: Fixture <sender@example.invalid>\r\n"
        "To: User <user@example.invalid>\r\n"
        f"Subject: {name}\r\n"
        f"Message-ID: <{name.casefold()}@example.invalid>\r\n"
        "Date: Sun, 23 Aug 2026 10:00:00 +0000\r\n\r\n"
        f"Synthetic body {name}\r\n"
    ).encode()


@dataclass
class Scan:
    clean: bool = True
    status: str = "clean"


class Scanner:
    def __init__(self) -> None:
        self.calls = 0

    def scanner_identity(self, *, refresh: bool = False) -> str:
        del refresh
        return "fixture-scanner:v1"

    def scan_bytes(self, data: bytes, **kwargs: object) -> Scan:
        del data, kwargs
        self.calls += 1
        return Scan()


class State:
    def __init__(self) -> None:
        self.uidvalidity = {"INBOX": "10", "Archive": "20", "Copies": "30"}
        self.messages: dict[str, dict[int, bytes]] = {
            "INBOX": {1: raw_mail("Alpha")},
            "Archive": {2: raw_mail("Beta")},
            "Copies": {},
        }
        self.transcript: list[str] = []


class Transport:
    def __init__(self, settings: ImapConnectionSettings, state: State) -> None:
        del settings
        self.state = state
        self.commands = state.transcript
        self.selected = ""

    def connect(self) -> None:
        return

    def capabilities(self) -> set[str]:
        self.commands.append("CAPABILITY")
        return {"IMAP4REV1", "UIDPLUS", "CONDSTORE", "IDLE"}

    def list_folders(self) -> list[tuple[str, str]]:
        self.commands.append("LIST")
        return [(name, name) for name in sorted(self.state.messages)]

    def examine(self, encoded_name: str) -> dict[str, str]:
        self.commands.append("EXAMINE")
        self.selected = encoded_name
        rows = self.state.messages[encoded_name]
        return {
            "uidvalidity": self.state.uidvalidity[encoded_name],
            "uidnext": str(max(rows, default=0) + 1),
            "messages": str(len(rows)),
            "highestmodseq": "1",
        }

    def uid_search_all(self) -> tuple[int, ...]:
        self.commands.append("UID SEARCH")
        return tuple(sorted(self.state.messages[self.selected]))

    def uid_fetch_headers(self, uid: int) -> bytes:
        self.commands.append("UID FETCH")
        raw = self.state.messages[self.selected][uid]
        return raw.split(b"\r\n\r\n", 1)[0] + b"\r\n\r\n"

    def uid_fetch_raw(self, uid: int) -> bytes:
        self.commands.append("UID FETCH")
        return self.state.messages[self.selected][uid]

    def logout(self) -> None:
        self.commands.append("LOGOUT")


def backend(state: State, secret: Path) -> NativeImapInventoryBackend:
    settings = ImapConnectionSettings(
        "fixture", "imap.example.invalid", 993, "fixture", "tls", secret
    )
    return NativeImapInventoryBackend(
        settings, transport_factory=lambda configured: Transport(configured, state)
    )


def reconcile(root: Path, state: State, secret: Path, scanner: Scanner) -> dict[str, Any]:
    selected = backend(state, secret)
    try:
        return MailSearchReconciler(
            selected,
            scanner,
            projection_root=root / "projection",
            state_path=root / "reconcile/state.json",
            limits=ReconcileLimits(
                max_runtime_seconds=60,
                request_interval_seconds=0,
            ),
        ).run(approved=True)
    finally:
        selected.close()


def main() -> int:
    root = Path(os.environ.get("M12_STATE_DIR", "/state"))
    output = Path(os.environ.get("M12_OUTPUT", "/output/m12-integration.json"))
    root.mkdir(parents=True, exist_ok=True)
    secret = root / "imap-password"
    secret.write_text("fixture-value\n", encoding="utf-8")
    state = State()
    scanner = Scanner()

    initial_backend = backend(state, secret)
    initial = MailSearchBackfill(
        initial_backend,
        scanner,
        projection_root=root / "projection",
        checkpoint_path=root / "backfill/checkpoint.json",
        limits=BackfillLimits(
            page_size=1,
            max_runtime_seconds=60,
            request_interval_seconds=0,
        ),
    ).run(approved=True)
    initial_backend.close()
    assert initial["complete"] is True
    initial_scans = scanner.calls

    alpha = state.messages["INBOX"].pop(1)
    state.messages["Archive"][9] = alpha
    moved = reconcile(root, state, secret, scanner)
    assert moved["ok"] is True
    assert moved["metrics"]["moved"] == 1
    assert moved["metrics"]["body_fetches"] == 1
    assert moved["metrics"]["parser_calls"] == 0
    assert moved["metrics"]["clamav_calls"] == 0
    assert scanner.calls == initial_scans

    state.messages["Copies"][11] = alpha
    copied = reconcile(root, state, secret, scanner)
    assert copied["metrics"]["copied"] == 1
    assert copied["metrics"]["parser_calls"] == 0
    assert scanner.calls == initial_scans

    state.messages["Copies"].pop(11)
    deleted = reconcile(root, state, secret, scanner)
    assert deleted["metrics"]["removed"] == 1
    projection = load_search_projection(root / "projection")
    assert projection.complete is True
    assert len(projection.records) == 2

    no_match = MailSearchEvidence(0, True, True, True).to_contract()
    partial = MailSearchEvidence(0, False, True, True).to_contract()
    assert no_match["decision"] == "no-match"
    assert partial["decision"] == "inconclusive"
    assert not any(
        command in {"STORE", "COPY", "MOVE", "EXPUNGE", "APPEND", "DELETE"}
        for command in state.transcript
    )

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "milestone": "M12.8-development",
                "scope": "hermetic in-memory IMAP inventory with synthetic example.invalid mail",
                "checks": {
                    "native_inventory_adapter": True,
                    "complete_uid_snapshots": True,
                    "external_move_copy_delete": True,
                    "ambiguous_move_single_raw_fetch": True,
                    "content_parser_clamav_reuse": True,
                    "authoritative_search_decision": True,
                    "imap_write_commands_absent": True,
                },
                "metrics": {
                    "move": moved["metrics"],
                    "copy": copied["metrics"],
                    "delete": deleted["metrics"],
                    "protocol_command_count": len(state.transcript),
                },
                "production_accounts_or_secrets": False,
                "productive_mounts": False,
                "published_host_ports": 0,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print("M12 hermetic native IMAP reconciliation: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
