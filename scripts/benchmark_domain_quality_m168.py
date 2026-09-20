#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mail_agent.invoice_migration import compare_extractor_snapshots  # noqa: E402
from personal_assistant.portfolio import PortfolioService  # noqa: E402

FIXTURE = ROOT / "tests/fixtures/m16/domain-quality-m168.json"


def main() -> int:
    raw = FIXTURE.read_bytes()
    fixture = json.loads(raw)
    invoice = compare_extractor_snapshots(
        attachment_hash=fixture["attachment_hash"],
        legacy=fixture["legacy"],
        current=fixture["current"],
        expected=fixture["expected"],
    )
    provider = [
        {
            "input": value,
            **PortfolioService._provider_failure(value),
        }
        for value in fixture["provider_errors"]
    ]
    result = {
        "ok": bool(invoice["improved"]),
        "milestone": "M16.8",
        "fixture_sha256": hashlib.sha256(raw).hexdigest(),
        "invoice": invoice,
        "portfolio_provider_failures": provider,
        "external_calls": 0,
        "writes": 0,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
