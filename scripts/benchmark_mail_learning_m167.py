#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mail_agent.learning_quality import LearningQualityAnalyzer  # noqa: E402
from mail_agent.models import Classification, Envelope  # noqa: E402
from mail_agent.parser import parse_eml  # noqa: E402
from mail_agent.storage import Storage  # noqa: E402

CORPUS = ROOT / "tests" / "fixtures" / "m16" / "mail-learning-quality.json"


def _message(case: dict[str, str]):
    raw = (
        f"From: Synthetic <{case['sender']}@example.invalid>\r\n"
        "To: Recipient <recipient@example.invalid>\r\n"
        "Subject: Synthetic status 1001\r\n"
        f"Message-ID: <{case['id']}@example.invalid>\r\n"
        f"References: <{case['thread']}@example.invalid>\r\n"
        "Content-Type: text/plain; charset=utf-8\r\n\r\nSynthetic evaluation body.\r\n"
    ).encode()
    return parse_eml(raw, Envelope(case["id"]), "INBOX")


def _classification(case: dict[str, str]) -> Classification:
    return Classification(
        case["combined"],
        0.91,
        7,
        case["combined"] == "relevant",
        "Synthetic decision",
        source="synthetic-combined",
        decision_evidence={
            "rule": {"category": case["rule"], "confidence": 0.9, "source": "rule"},
            "model": {"category": case["model"], "confidence": 0.8, "source": "model"},
        },
    )


def build_report() -> dict[str, Any]:
    corpus_bytes = CORPUS.read_bytes()
    payload = json.loads(corpus_bytes)
    with tempfile.TemporaryDirectory(prefix="openclaw-m167-") as temporary:
        storage = Storage(Path(temporary) / "mail.sqlite3")
        try:
            for index, case in enumerate(payload["cases"], start=1):
                message = _message(case)
                storage.upsert_message(message, _classification(case), status="classified")
                storage.record_feedback(message, case["actual"], "Agent/Korrektur")
                storage.connection.execute(
                    "UPDATE feedback SET created_at=? WHERE stable_key=?",
                    (f"2026-09-{index:02d}T10:00:00+00:00", message.stable_key),
                )
            storage.connection.commit()
            report = LearningQualityAnalyzer(storage).report(limit=100)
        finally:
            storage.close()
    return {
        "schema_version": 1,
        "milestone": "M16.7",
        "corpus": {
            "path": str(CORPUS.relative_to(ROOT)),
            "sha256": hashlib.sha256(corpus_bytes).hexdigest(),
            "cases": len(payload["cases"]),
            "privacy": payload["privacy"],
        },
        "data_quality": report["data_quality"],
        "evaluation": report["evaluation"],
        "review_priority": report["review_priority"],
        "limitations": [
            "Synthetic evidence is a regression contract, not an estimate of productive mailbox quality.",
            "No productive mail, rule or job was read or changed.",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Reproduce the synthetic M16.7 mail-quality baseline")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    serialized = json.dumps(build_report(), ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(serialized, encoding="utf-8")
    print(serialized, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
