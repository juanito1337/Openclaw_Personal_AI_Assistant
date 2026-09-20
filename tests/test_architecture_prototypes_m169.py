from __future__ import annotations

import hashlib
import json
import socket
import subprocess
import sys
import threading
import time
from dataclasses import replace
from pathlib import Path

import pytest

from personal_assistant.prototypes import PROTOTYPE_RUNTIME_ENABLED
from personal_assistant.prototypes.semantic_search import (
    MODEL_DIGEST,
    EphemeralEmbeddingIndex,
    LocalDigestEmbeddingModel,
    corpus_digest,
    evaluate_semantic_architecture,
    forbidden_content_fields,
)
from personal_assistant.prototypes.tool_executor import (
    DEFAULT_MAX_RESPONSE_BYTES,
    MAX_REQUEST_BYTES,
    ExecutorRoleProfile,
    GatewaySandboxProfile,
    OneShotUnixExecutorServer,
    PrototypeToolExecutor,
    PrototypeToolSpec,
    RpcRequest,
    approval_binding,
    build_gateway_request,
    encode_frame,
    receive_frame,
    sign_request,
    unix_rpc_call,
)
from personal_assistant.tool_catalog import TOOLS

ROOT = Path(__file__).resolve().parents[1]
CORPUS_PATH = ROOT / "tests/fixtures/m16/semantic-search-eval.json"
BASELINE_PATH = ROOT / "docs/architecture/m16.9-architecture-decision-baseline.json"
AUTH_KEY = bytes.fromhex("41" * 32)


def corpus() -> dict:
    return json.loads(CORPUS_PATH.read_text(encoding="utf-8"))


def spec(handler=None, *, maximum=DEFAULT_MAX_RESPONSE_BYTES) -> PrototypeToolSpec:
    return PrototypeToolSpec(
        tool_id="prototype.synthetic.read",
        schema_version=1,
        argument_types={"value": str},
        approval="none",
        handler=handler or (lambda arguments: {"value": arguments["value"], "postcondition_verified": True}),
        max_response_bytes=maximum,
    )


def request(
    *,
    nonce: str = "nonce-1",
    idempotency: str = "idem-1",
    tool_id: str = "prototype.synthetic.read",
    schema_version: int = 1,
    arguments: dict | None = None,
    approval: str = "none",
    deadline: int | None = None,
) -> RpcRequest:
    return build_gateway_request(
        tool_id=tool_id,
        tool_schema_version=schema_version,
        arguments={"value": "synthetic"} if arguments is None else arguments,
        approval=approval,
        evidence={"kind": "synthetic-contract"},
        deadline_epoch_ms=deadline or int(time.time() * 1000) + 10_000,
        idempotency_key=idempotency,
        request_id=f"request-{nonce}",
        nonce=nonce,
        rpc_auth_key=AUTH_KEY,
    )


def test_semantic_eval_contains_all_required_intent_classes() -> None:
    payload = corpus()
    assert {row["kind"] for row in payload["queries"]} == {
        "exact",
        "contextual",
        "synonym",
        "negative",
    }
    assert payload["privacy"]["synthetic"] is True


def test_digest_bound_local_model_rejects_foreign_identity() -> None:
    model = LocalDigestEmbeddingModel()
    assert model.digest == MODEL_DIGEST
    with pytest.raises(ValueError, match="Digest"):
        LocalDigestEmbeddingModel(digest="sha256:" + "0" * 64)


def test_semantic_eval_compares_four_modes_and_reports_resource_costs() -> None:
    report = evaluate_semantic_architecture(corpus(), iterations=3)
    assert [row["mode"] for row in report["retrieval_modes"]] == [
        "lexical",
        "thread-tags",
        "local-embedding",
        "hybrid",
    ]
    for row in report["retrieval_modes"]:
        assert row["latency_ms"]["samples"] == 24
        assert row["latency_ms"]["p50"] <= row["latency_ms"]["p95"]
        assert row["compute"]["deterministic_feature_operations"] > 0
        assert "misclassification_count" in row["quality"]
    assert report["resources"]["estimated_vector_bytes"] > 0


def test_semantic_eval_reports_negative_abstention_and_no_forced_activation() -> None:
    report = evaluate_semantic_architecture(corpus(), iterations=3)
    assert all("negative_abstention" in row["quality"] for row in report["retrieval_modes"])
    assert report["decision"]["activation_allowed"] is False
    assert report["decision"]["state"] == "disabled"
    assert report["decision"]["requires_target_hardware_measurement"] is True


def test_semantic_report_records_no_mail_or_query_content() -> None:
    report = evaluate_semantic_architecture(corpus(), iterations=3)
    assert forbidden_content_fields(report) == set()
    assert report["privacy"]["content_exfiltration"] is False


def test_semantic_eval_rejects_non_synthetic_or_too_small_measurement() -> None:
    payload = corpus()
    payload["privacy"]["synthetic"] = False
    with pytest.raises(ValueError, match="synthetische"):
        evaluate_semantic_architecture(payload)
    with pytest.raises(ValueError, match="mindestens drei"):
        evaluate_semantic_architecture(corpus(), iterations=2)


def test_embedding_index_is_rebuildable_removable_and_source_neutral(tmp_path: Path) -> None:
    source = tmp_path / "source.eml"
    lexical = tmp_path / "lexical.sqlite3"
    source.write_bytes(b"synthetic source")
    lexical.write_bytes(b"synthetic lexical")
    before = (hashlib.sha256(source.read_bytes()).digest(), hashlib.sha256(lexical.read_bytes()).digest())
    index = EphemeralEmbeddingIndex(tmp_path / "derived", LocalDigestEmbeddingModel())
    first = index.rebuild(corpus()["documents"])
    assert index.remove() is True
    assert index.remove() is False
    second = index.rebuild(corpus()["documents"])
    after = (hashlib.sha256(source.read_bytes()).digest(), hashlib.sha256(lexical.read_bytes()).digest())
    assert first["sha256"] == second["sha256"]
    assert before == after


def test_corpus_digest_is_order_and_content_bound() -> None:
    payload = corpus()
    original = corpus_digest(payload)
    payload["documents"][0]["body"] += " changed"
    assert corpus_digest(payload) != original


def test_gateway_profile_rejects_domain_secrets_and_rw_mounts() -> None:
    GatewaySandboxProfile().validate()
    with pytest.raises(ValueError, match="Fach-Secrets"):
        GatewaySandboxProfile(domain_secrets=("NEXTCLOUD_TOKEN",)).validate()
    with pytest.raises(ValueError, match="Fach-Secrets"):
        GatewaySandboxProfile(read_write_domain_mounts=("/var/lib/openclaw/mail",)).validate()


def test_executor_role_is_limited_to_registered_tools_and_domain_state() -> None:
    profile = ExecutorRoleProfile(
        role="mail-read",
        allowed_tool_ids=("prototype.synthetic.read",),
        domain_secrets=("IMAP_PASSWORD",),
        read_write_mounts=("/var/lib/openclaw/mail",),
    )
    profile.validate({"prototype.synthetic.read"})
    with pytest.raises(ValueError, match="nicht registriertes"):
        profile.validate({"another.tool"})
    with pytest.raises(ValueError, match="ausserhalb"):
        replace(profile, read_write_mounts=("/srv/openclaw",)).validate(
            {"prototype.synthetic.read"}
        )


def test_executor_accepts_authenticated_registered_request() -> None:
    executor = PrototypeToolExecutor([spec()], rpc_auth_key=AUTH_KEY)
    response = executor.handle(request())
    assert response.ok is True
    assert response.status == "completed"
    assert response.result["value"] == "synthetic"
    assert response.postcondition_verified is True


def test_changed_arguments_fail_authentication() -> None:
    executor = PrototypeToolExecutor([spec()], rpc_auth_key=AUTH_KEY)
    changed = replace(request(), arguments={"value": "changed"})
    response = executor.handle(changed)
    assert response.blocker["code"] == "authentication-failed"


def test_foreign_authentication_key_is_rejected() -> None:
    unsigned = replace(request(), signature="")
    foreign = sign_request(unsigned, bytes.fromhex("42" * 32))
    response = PrototypeToolExecutor([spec()], rpc_auth_key=AUTH_KEY).handle(foreign)
    assert response.blocker["code"] == "authentication-failed"


def test_replay_and_reused_idempotency_are_rejected() -> None:
    executor = PrototypeToolExecutor([spec()], rpc_auth_key=AUTH_KEY)
    first = request()
    assert executor.handle(first).ok is True
    assert executor.handle(first).blocker["code"] == "replay-detected"
    repeated = request(nonce="nonce-2", idempotency="idem-1")
    assert executor.handle(repeated).blocker["code"] == "idempotency-conflict"


def test_expired_request_is_rejected() -> None:
    now = int(time.time() * 1000)
    response = PrototypeToolExecutor([spec()], rpc_auth_key=AUTH_KEY).handle(
        request(deadline=now - 1), now_epoch_ms=now
    )
    assert response.blocker["code"] == "deadline-exceeded"


def test_unknown_tool_and_schema_are_rejected_without_fallback() -> None:
    executor = PrototypeToolExecutor([spec()], rpc_auth_key=AUTH_KEY)
    unknown = executor.handle(request(tool_id="prototype.unknown", nonce="unknown"))
    schema = executor.handle(request(schema_version=2, nonce="schema", idempotency="schema"))
    assert unknown.blocker == {
        "code": "unknown-tool",
        "detail": "Tool-ID ist nicht registriert",
        "shell_fallback": False,
    }
    assert schema.blocker["code"] == "schema-version-mismatch"


@pytest.mark.parametrize("arguments", [{"value": 7}, {"value": "x", "extra": True}, {}])
def test_argument_schema_is_closed(arguments: dict) -> None:
    response = PrototypeToolExecutor([spec()], rpc_auth_key=AUTH_KEY).handle(
        request(arguments=arguments, nonce=str(arguments), idempotency=str(arguments))
    )
    assert response.blocker["code"] == "argument-schema-mismatch"


def test_approval_label_and_binding_are_enforced() -> None:
    executor = PrototypeToolExecutor([spec()], rpc_auth_key=AUTH_KEY)
    wrong_label = request(approval="broad-approval", nonce="label", idempotency="label")
    assert executor.handle(wrong_label).blocker["code"] == "approval-mismatch"
    original = request(nonce="binding", idempotency="binding")
    rebound = sign_request(replace(original, approval_binding="0" * 64, signature=""), AUTH_KEY)
    assert executor.handle(rebound).blocker["code"] == "approval-mismatch"


def test_executor_crash_is_typed_blocker_without_shell_fallback() -> None:
    def crash(_arguments):
        raise RuntimeError("synthetic crash")

    response = PrototypeToolExecutor([spec(crash)], rpc_auth_key=AUTH_KEY).handle(request())
    assert response.blocker["code"] == "executor-unavailable"
    assert response.blocker["shell_fallback"] is False
    assert "synthetic crash" not in response.blocker["detail"]


def test_response_size_is_bounded() -> None:
    oversized = spec(lambda _arguments: {"value": "x" * 1024}, maximum=200)
    response = PrototypeToolExecutor([oversized], rpc_auth_key=AUTH_KEY).handle(request())
    assert response.blocker["code"] == "response-too-large"


def test_concurrent_capacity_applies_fail_closed_backpressure() -> None:
    started = threading.Event()
    release = threading.Event()

    def slow(arguments):
        started.set()
        assert release.wait(2)
        return {"value": arguments["value"]}

    executor = PrototypeToolExecutor([spec(slow)], rpc_auth_key=AUTH_KEY, max_inflight=1)
    first_result = []
    thread = threading.Thread(target=lambda: first_result.append(executor.handle(request())))
    thread.start()
    assert started.wait(1)
    blocked = executor.handle(request(nonce="second", idempotency="second"))
    release.set()
    thread.join(2)
    assert blocked.blocker["code"] == "backpressure"
    assert first_result[0].ok is True


def test_audit_is_content_minimal() -> None:
    executor = PrototypeToolExecutor([spec()], rpc_auth_key=AUTH_KEY)
    executor.handle(request(arguments={"value": "private synthetic value"}))
    serialized = json.dumps(executor.audit)
    assert "private synthetic value" not in serialized
    assert executor.audit[0]["content_recorded"] is False
    assert executor.audit[0]["argument_names"] == ["value"]


def test_frame_codec_rejects_oversize_and_truncation() -> None:
    with pytest.raises(ValueError, match="too-large"):
        encode_frame({"value": "x" * 200}, max_bytes=10)
    left, right = socket.socketpair()
    try:
        left.sendall(b"\x00\x00\x00\x10short")
        left.shutdown(socket.SHUT_WR)
        with pytest.raises(ValueError, match="truncated"):
            receive_frame(right, max_bytes=MAX_REQUEST_BYTES)
    finally:
        left.close()
        right.close()


def test_unix_socket_boundary_roundtrip_and_permissions(tmp_path: Path) -> None:
    executor = PrototypeToolExecutor([spec()], rpc_auth_key=AUTH_KEY)
    path = tmp_path / "executor.sock"
    server = OneShotUnixExecutorServer(path, executor)
    ready = threading.Event()
    thread = threading.Thread(target=lambda: server.serve(ready))
    thread.start()
    assert ready.wait(1)
    assert path.stat().st_mode & 0o777 == 0o600
    response = unix_rpc_call(path, request())
    thread.join(2)
    assert response.ok is True
    assert not path.exists()


def test_prototypes_are_not_runtime_registered() -> None:
    assert PROTOTYPE_RUNTIME_ENABLED is False
    ids = {tool.id for tool in TOOLS}
    assert not any(tool_id.startswith("prototype.") for tool_id in ids)


def test_approval_digest_binds_tool_schema_arguments_and_idempotency() -> None:
    base = approval_binding(
        tool_id="tool", tool_schema_version=1, arguments={"value": "a"}, idempotency_key="one"
    )
    assert base != approval_binding(
        tool_id="tool", tool_schema_version=1, arguments={"value": "b"}, idempotency_key="one"
    )
    assert base != approval_binding(
        tool_id="tool", tool_schema_version=2, arguments={"value": "a"}, idempotency_key="one"
    )


def test_benchmark_is_reproducible_and_keeps_both_features_disabled(tmp_path: Path) -> None:
    output = tmp_path / "report.json"
    completed = subprocess.run(
        [sys.executable, "scripts/benchmark_architecture_m169.py", "--output", str(output)],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["embedding_lifecycle"]["rebuild_identical"] is True
    assert payload["embedding_lifecycle"]["source_unchanged"] is True
    assert payload["embedding_lifecycle"]["lexical_index_unchanged"] is True
    assert payload["tool_executor"]["replay_blocker"] == "replay-detected"
    assert payload["decision"]["production_activation"] is False


def test_committed_architecture_baseline_binds_quality_and_safety_contract() -> None:
    baseline = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    current = evaluate_semantic_architecture(corpus(), iterations=3)
    assert baseline["corpus"]["sha256"] == corpus_digest(corpus())
    assert baseline["semantic_search"]["model"] == current["model"]
    assert [row["quality"] for row in baseline["semantic_search"]["retrieval_modes"]] == [
        row["quality"] for row in current["retrieval_modes"]
    ]
    assert baseline["semantic_search"]["decision"] == current["decision"]
    assert baseline["embedding_lifecycle"]["rebuild_identical"] is True
    assert baseline["tool_executor"]["audit_content_recorded"] is False
    assert baseline["decision"]["production_activation"] is False
