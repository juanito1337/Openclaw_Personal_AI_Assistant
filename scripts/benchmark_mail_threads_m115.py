#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from itertools import combinations
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from personal_assistant.mail_threads import (  # noqa: E402
    MAIL_THREAD_VERSION,
    build_mail_threads,
)

DEFAULT_CORPUS = ROOT / "tests/fixtures/mail_search/m110_synthetic_corpus.json"


def _record(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "content_id": f"content:{row['id']}",
        "message_id": str(row.get("message_id") or ""),
        "title": str(row.get("subject") or ""),
        "modified_at": str(row.get("date") or ""),
        "metadata": {
            "sender_addr": str(row.get("from_addr") or ""),
            "recipients": [str(value) for value in row.get("to", [])],
            "received_at": str(row.get("date") or ""),
            "in_reply_to": (
                [str(row["in_reply_to"])] if row.get("in_reply_to") else []
            ),
            "references": [str(value) for value in row.get("references", [])],
        },
    }


def benchmark(path: Path) -> dict[str, Any]:
    corpus = json.loads(path.read_text(encoding="utf-8"))
    privacy = corpus.get("privacy") if isinstance(corpus, dict) else None
    if (
        not isinstance(corpus, dict)
        or corpus.get("schema_version") != 1
        or not isinstance(privacy, dict)
        or privacy.get("synthetic") is not True
        or privacy.get("productive_data") is not False
    ):
        raise ValueError("M11.0-Korpus ist nicht als rein synthetisch ausgewiesen")
    raw_messages = [dict(row) for row in corpus.get("messages", [])]
    result = build_mail_threads(
        [_record(row) for row in raw_messages], generation="m115-benchmark"
    )
    predicted = {
        str(member["content_id"]).removeprefix("content:"): str(member["thread_id"])
        for member in result.members
    }
    expected = {str(row["id"]): str(row.get("thread_id") or row["id"]) for row in raw_messages}
    expected_pairs: set[tuple[str, str]] = set()
    predicted_pairs: set[tuple[str, str]] = set()
    for left, right in combinations(sorted(expected), 2):
        if expected[left] == expected[right]:
            expected_pairs.add((left, right))
        if predicted[left] == predicted[right]:
            predicted_pairs.add((left, right))
    true_pairs = expected_pairs & predicted_pairs
    false_pairs = predicted_pairs - expected_pairs
    missed_pairs = expected_pairs - predicted_pairs
    precision = len(true_pairs) / len(predicted_pairs) if predicted_pairs else 1.0
    recall = len(true_pairs) / len(expected_pairs) if expected_pairs else 1.0
    return {
        "ok": not false_pairs and not missed_pairs,
        "milestone": "M11.5",
        "corpus": str(path.relative_to(ROOT)),
        "synthetic": True,
        "thread_version": MAIL_THREAD_VERSION,
        "messages": len(raw_messages),
        "expected_threads": len(set(expected.values())),
        "predicted_threads": len(set(predicted.values())),
        "expected_linked_pairs": len(expected_pairs),
        "predicted_linked_pairs": len(predicted_pairs),
        "true_linked_pairs": len(true_pairs),
        "false_linked_pairs": [list(pair) for pair in sorted(false_pairs)],
        "missed_linked_pairs": [list(pair) for pair in sorted(missed_pairs)],
        "pair_precision": round(precision, 4),
        "pair_recall": round(recall, 4),
        "mislink_rate": round(len(false_pairs) / max(1, len(predicted_pairs)), 4),
        "diagnostics": result.diagnostics,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="M11.5 thread-quality benchmark")
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    payload = benchmark(args.corpus.resolve())
    rendered = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0 if payload["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
