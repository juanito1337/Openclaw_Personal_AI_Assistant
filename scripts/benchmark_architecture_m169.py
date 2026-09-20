#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from personal_assistant.prototypes.semantic_search import (  # noqa: E402
    EphemeralEmbeddingIndex,
    LocalDigestEmbeddingModel,
    corpus_digest,
    evaluate_semantic_architecture,
)
from personal_assistant.prototypes.tool_executor import (  # noqa: E402
    GatewaySandboxProfile,
    PrototypeToolExecutor,
    PrototypeToolSpec,
    build_gateway_request,
)

CORPUS = ROOT / "tests/fixtures/m16/semantic-search-eval.json"


def _executor_report() -> dict[str, Any]:
    key = bytes.fromhex("ab" * 32)
    spec = PrototypeToolSpec(
        tool_id="prototype.echo.read",
        schema_version=1,
        argument_types={"value": str},
        approval="none",
        handler=lambda arguments: {
            "value_sha256": hashlib.sha256(arguments["value"].encode()).hexdigest(),
            "postcondition_verified": True,
        },
    )
    executor = PrototypeToolExecutor([spec], rpc_auth_key=key)
    now = int(time.time() * 1000)
    request = build_gateway_request(
        tool_id=spec.tool_id,
        tool_schema_version=1,
        arguments={"value": "synthetic"},
        approval="none",
        evidence={"source": "synthetic-contract"},
        deadline_epoch_ms=now + 5_000,
        idempotency_key="benchmark-1",
        request_id="benchmark-1",
        nonce="benchmark-nonce-1",
        rpc_auth_key=key,
    )
    response = executor.handle(request, now_epoch_ms=now)
    replay = executor.handle(request, now_epoch_ms=now)
    sandbox = GatewaySandboxProfile()
    sandbox.validate()
    return {
        "ok": response.ok,
        "registered_tools": len(executor.registered_tool_ids),
        "gateway_domain_secrets": len(sandbox.domain_secrets),
        "gateway_rw_domain_mounts": len(sandbox.read_write_domain_mounts),
        "rpc_boundary": "af-unix-framed-json-hmac-sha256",
        "replay_blocker": replay.blocker.get("code"),
        "audit_rows": len(executor.audit),
        "audit_content_recorded": any(bool(row["content_recorded"]) for row in executor.audit),
        "production_registered": False,
    }


def build_report() -> dict[str, Any]:
    corpus = json.loads(CORPUS.read_text(encoding="utf-8"))
    semantic = evaluate_semantic_architecture(corpus)
    with tempfile.TemporaryDirectory(prefix="openclaw-m169-") as temporary:
        root = Path(temporary)
        source = root / "source-mail.json"
        lexical = root / "lexical.sqlite3"
        source.write_text("synthetic immutable source\n", encoding="utf-8")
        lexical.write_bytes(b"synthetic lexical index")
        original = {
            "source": hashlib.sha256(source.read_bytes()).hexdigest(),
            "lexical": hashlib.sha256(lexical.read_bytes()).hexdigest(),
        }
        index = EphemeralEmbeddingIndex(root / "derived-semantic", LocalDigestEmbeddingModel())
        first = index.rebuild(corpus["documents"])
        removed = index.remove()
        second = index.rebuild(corpus["documents"])
        final = {
            "source": hashlib.sha256(source.read_bytes()).hexdigest(),
            "lexical": hashlib.sha256(lexical.read_bytes()).hexdigest(),
        }
    return {
        "schema_version": 1,
        "milestone": "M16.9",
        "corpus": {
            "path": str(CORPUS.relative_to(ROOT)),
            "sha256": corpus_digest(corpus),
            "document_count": len(corpus["documents"]),
            "query_count": len(corpus["queries"]),
        },
        "semantic_search": semantic,
        "embedding_lifecycle": {
            "first_build": first,
            "removed": removed,
            "rebuild_identical": first["sha256"] == second["sha256"],
            "source_unchanged": original["source"] == final["source"],
            "lexical_index_unchanged": original["lexical"] == final["lexical"],
        },
        "tool_executor": _executor_report(),
        "decision": {
            "semantic_search": "disabled-pending-target-hardware-canary",
            "tool_executor": "prototype-only-not-runtime-registered",
            "production_activation": False,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Reproduce the hermetic M16.9 architecture baseline")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = build_report()
    serialized = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(serialized, encoding="utf-8")
    print(serialized, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
