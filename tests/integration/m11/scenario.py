#!/usr/bin/env python3
"""Exercise the real M11 projection, sync, FTS, embedding and hybrid-search code."""

from __future__ import annotations

import argparse
import base64
import imaplib
import json
import os
import socket
import struct
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Sequence
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from mail_agent.search_backfill import (
    BackfillEnvelope,
    BackfillFolder,
    BackfillLimits,
    BackfillPage,
    ConnectorCapabilities,
    MailSearchBackfill,
)
from mail_agent.search_reconcile import (
    FolderReconcileScan,
    MailSearchReconciler,
    ReconcileLimits,
    ReconcileObservation,
)
from personal_assistant.config import AssistantConfig, RuntimeConfig, SearchConfig
from personal_assistant.contracts.mail_projection import load_search_projection
from personal_assistant.contracts.mail_projection_v2 import MailLocator, locator_identity
from personal_assistant.knowledge import KnowledgeIndexer
from personal_assistant.mail_embeddings import EmbeddingModel
from personal_assistant.mail_hybrid_search import MailHybridSearch
from personal_assistant.storage import AssistantStorage

HOST = "fake-services"
HTTP = f"http://{HOST}:8080"
STATE = Path("/state")
PROJECTION = STATE / "projection"
KNOWLEDGE = Path("/knowledge/assistant.sqlite3")
MODEL = EmbeddingModel("m11-fixture-4d", "sha256:" + "8" * 64, 4, 2048)


def request(method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    data = None if payload is None else json.dumps(payload).encode()
    value = urllib.request.Request(
        HTTP + path,
        data=data,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(value, timeout=5) as response:
        result = json.loads(response.read())
    if not isinstance(result, dict):
        raise RuntimeError("fixture response is not an object")
    return result


def inventory() -> dict[str, Any]:
    return request("GET", "/mail/inventory")


def _folder(payload: dict[str, Any], folder_id: str) -> dict[str, Any]:
    return next(item for item in payload["folders"] if item["folder_id"] == folder_id)


class NetworkBackend:
    def __init__(self) -> None:
        self.keys: dict[tuple[str, str], str] = {}

    def capabilities(self) -> ConnectorCapabilities:
        return ConnectorCapabilities(
            paging=True,
            raw_fetch=True,
            uid=True,
            uidvalidity=True,
            uidnext=True,
            modseq=True,
            condstore=True,
            qresync=True,
            idle=True,
            folder_stable_id=True,
            cursor_contract="m11-hermetic-authoritative",
        )

    def inventory(self) -> list[BackfillFolder]:
        payload = inventory()
        return [
            BackfillFolder(str(row["folder_id"]), str(row["name"]), str(row["uidvalidity"]))
            for row in payload["folders"]
        ]

    def fetch_page(self, folder: BackfillFolder, *, page: int, page_size: int) -> BackfillPage:
        rows = list(_folder(inventory(), folder.folder_id)["messages"])
        start = (page - 1) * page_size
        selected = rows[start : start + page_size]
        items = []
        for row in selected:
            key = str(row["key"])
            uid = str(row["uid"])
            self.keys[(folder.folder_id, uid)] = key
            items.append(
                BackfillEnvelope(
                    mailbox_id=str(row["mailbox_id"]),
                    uid=uid,
                    subject=str(row["subject"]),
                    date=str(row["date"]),
                    received_at=str(row["date"]),
                )
            )
        return BackfillPage(tuple(items), start + len(selected) < len(rows))

    def scan_folder(
        self,
        folder: BackfillFolder,
        *,
        previous_cursor: str,
        max_messages: int,
    ) -> FolderReconcileScan:
        del previous_cursor
        payload = inventory()
        raw_folder = _folder(payload, folder.folder_id)
        observations = []
        for row in list(raw_folder["messages"])[:max_messages]:
            uid = str(row["uid"])
            self.keys[(folder.folder_id, uid)] = str(row["key"])
            move_from = row.get("move_from")
            move_from_id = ""
            if isinstance(move_from, dict):
                move_from_id = locator_identity(
                    MailLocator(
                        resource_id="mail-agent",
                        folder_id=str(move_from["folder_id"]),
                        folder_name=str(move_from["folder_name"]),
                        mailbox_id=str(move_from["mailbox_id"]),
                        uidvalidity=str(move_from["uidvalidity"]),
                        uid=str(move_from["uid"]),
                        observed_at="2026-08-20T08:00:00+00:00",
                    )
                )
            observations.append(
                ReconcileObservation(
                    mailbox_id=str(row["mailbox_id"]),
                    uid=uid,
                    subject=str(row["subject"]),
                    date=str(row["date"]),
                    received_at=str(row["date"]),
                    raw_sha256=str(row["raw_sha256"]),
                    raw_sha256_verified=True,
                    move_from_locator_id=move_from_id,
                )
            )
        complete = bool(payload["complete"])
        return FolderReconcileScan(
            tuple(observations),
            f"fixture:{payload['generation']}",
            complete,
            bool(payload["authoritative"]),
            "" if complete else "fixture-network-partial",
        )

    def fetch_raw(self, folder: BackfillFolder, envelope: BackfillEnvelope) -> bytes:
        key = self.keys[(folder.folder_id, envelope.uid)]
        payload = request("GET", "/mail/raw?key=" + urllib.parse.quote(key))
        return base64.b64decode(str(payload["raw_base64"]), validate=True)


class ScanResult:
    def __init__(self, status: str) -> None:
        self.status = status

    @property
    def clean(self) -> bool:
        return self.status == "clean"


class NetworkScanner:
    def scanner_identity(self, *, refresh: bool = False) -> str:
        del refresh
        return "clamav:m11-hermetic-v1"

    def scan_bytes(
        self,
        data: bytes,
        *,
        name: str,
        source_type: str,
        use_cache: bool = True,
    ) -> ScanResult:
        del name, source_type, use_cache
        with socket.create_connection((HOST, 3310), timeout=5) as client:
            client.sendall(
                b"zINSTREAM\0"
                + struct.pack("!I", len(data))
                + data
                + struct.pack("!I", 0)
            )
            response = client.recv(4096)
        if b"ERROR" in response:
            return ScanResult("error")
        if b"FOUND" in response:
            return ScanResult("infected")
        if b"OK" not in response:
            return ScanResult("error")
        return ScanResult("clean")


class HttpEmbeddingProvider:
    def verify_installed_model(self) -> dict[str, Any]:
        return {"verified": True, "name": MODEL.name, "digest": MODEL.digest}

    def embed(
        self,
        texts: Sequence[str],
        *,
        priority: str,
    ) -> tuple[list[list[float]], dict[str, Any]]:
        started = time.perf_counter()
        try:
            payload = request("POST", "/api/embed", {"texts": list(texts), "priority": priority})
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"fixture embedding HTTP {exc.code}") from exc
        return list(payload["vectors"]), {
            "latency_ms": round((time.perf_counter() - started) * 1000, 4),
            "queue_wait_ms": 0.0,
            "priority": priority,
        }


class LiveInventory:
    def __init__(self) -> None:
        self.search_calls = 0
        self.resolve_calls = 0

    def search_messages(self, query: str, *, limit: int = 50) -> dict[str, Any]:
        del query, limit
        self.search_calls += 1
        return {
            "ok": True,
            "complete": True,
            "messages": [],
            "searched_folders": 0,
            "total_folders": 0,
            "folder_errors": [],
            "results_may_be_truncated": False,
        }

    def resolve_live_locators(self, candidates: list[dict[str, Any]]) -> dict[str, Any]:
        self.resolve_calls += 1
        payload = inventory()
        live = {
            (str(folder["name"]), str(row["mailbox_id"]))
            for folder in payload["folders"]
            for row in folder["messages"]
        }
        results = []
        for candidate in candidates:
            locators = [dict(item) for item in candidate.get("locators") or []]
            selected = next(
                (
                    item
                    for item in locators
                    if item.get("current_in_index")
                    and (str(item.get("folder") or ""), str(item.get("mailbox_id") or "")) in live
                ),
                None,
            )
            if selected is not None:
                selected = {**selected, "live_state": "validated", "selected": True}
            results.append(
                {
                    "content_id": candidate["content_id"],
                    "state": "validated" if selected else "missing",
                    "live_locator": selected,
                    "locators": locators,
                    "complete": selected is not None,
                }
            )
        complete = all(item["complete"] for item in results)
        return {
            "ok": complete,
            "complete": complete,
            "results": results,
            "folder_errors": [],
            "backend_calls": {
                "list_folders": 1 if results else 0,
                "list_envelopes": len(results),
                "search_envelopes": 0,
            },
        }


def config() -> AssistantConfig:
    return AssistantConfig(
        runtime=RuntimeConfig(database=KNOWLEDGE, log_file=STATE / "fixture.log"),
        search=SearchConfig(
            mail_snapshot_dir=PROJECTION,
            mail_projection_max_age_seconds=86400,
        ),
        path=STATE / "fixture.toml",
    )


def write_json(name: str, payload: dict[str, Any]) -> None:
    path = STATE / name
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def validate_imap() -> None:
    client = imaplib.IMAP4(HOST, 1143)
    assert client.login("fixture", "fixture")[0] == "OK"
    assert client.select("INBOX")[0] == "OK"
    status, ids = client.search(None, "ALL")
    assert status == "OK" and ids == [b"1"]
    status, raw = client.fetch(b"1", "(RFC822)")
    assert status == "OK" and b"Projekt Aurora" in repr(raw).encode()
    client.logout()


def backfill() -> None:
    request("POST", "/control", {"action": "reset"})
    validate_imap()
    scanner = NetworkScanner()
    assert scanner.scan_bytes(b"clean", name="clean", source_type="fixture").clean
    eicar = b"EICAR-STANDARD-ANTIVIRUS-TEST-FILE"
    assert scanner.scan_bytes(eicar, name="eicar", source_type="fixture").status == "infected"
    assert scanner.scan_bytes(
        b"M11-SCANNER-ERROR", name="error", source_type="fixture"
    ).status == "error"
    result = MailSearchBackfill(
        NetworkBackend(),
        scanner,
        projection_root=PROJECTION,
        checkpoint_path=STATE / "backfill.json",
        quarantine_folders=("Spamverdacht",),
        limits=BackfillLimits(page_size=2, request_interval_seconds=0),
    ).run(approved=True)
    projection = load_search_projection(PROJECTION)
    server_count = sum(len(row["messages"]) for row in inventory()["folders"])
    assert result["ok"] is True and projection.complete
    assert len(projection.records) == server_count == 2
    write_json(
        "backfill-result.json",
        {
            "ok": True,
            "server_count": server_count,
            "projection_count": len(projection.records),
            "blocked_count": result["blocked_count"],
            "metrics": result["metrics"],
        },
    )
    print("M11 backfill, Fake-IMAP and ClamAV fixtures: OK")


def sync_once() -> dict[str, Any]:
    storage = AssistantStorage(KNOWLEDGE)
    try:
        result = KnowledgeIndexer(config(), storage).index_mail_snapshots()
        status = storage.mail_index_status(max_age_seconds=86400, semantic_model=MODEL)
        assert result["published"] is True and status["coverage"]["ratio"] == 1.0
        return {"ok": True, "sync": result, "status": status}
    finally:
        storage.close()


def sync_daemon() -> None:
    result = sync_once()
    write_json("sync-result.json", result)
    (STATE / "sync-ready").write_text(str(result["sync"]["source_generation"]), encoding="utf-8")
    print("M11 projection import and FTS sync: OK", flush=True)
    while True:
        time.sleep(1)


def clear_ready() -> None:
    (STATE / "sync-ready").unlink(missing_ok=True)


def semantic_config() -> SimpleNamespace:
    return SimpleNamespace(
        mail_projection_max_age_seconds=86400,
        semantic_provider="ollama",
        semantic_model=MODEL.name,
        semantic_model_digest=MODEL.digest,
        semantic_dimension=MODEL.dimension,
        semantic_context_limit=MODEL.context_limit,
        ollama_coordinator_url="http://127.0.0.1:11435",
    )


def gateway(expect: str) -> None:
    storage = AssistantStorage(KNOWLEDGE)
    live = LiveInventory()
    try:
        storage.build_mail_embeddings(
            model=MODEL,
            provider=HttpEmbeddingProvider(),
            max_chunks=100,
            batch_size=4,
        )
        service = MailHybridSearch(
            storage,
            live,
            semantic_config(),
            semantic_provider_factory=lambda _model: HttpEmbeddingProvider(),
        )
        query = "Morgenrot" if expect == "new" else "Polarstern"
        result = service.search(query, limit=20)
        assert result["ok"] is True and result["complete"] is True
        assert result["backend"] == "local-hybrid" and live.search_calls == 0, json.dumps(
            {
                "backend": result.get("backend"),
                "fallback_reason": result.get("fallback_reason"),
                "index": result.get("index"),
                "status": storage.mail_index_status(max_age_seconds=86400),
            },
            sort_keys=True,
        )
        query_hits = [item for item in result["results"] if item["query_match"]]
        assert len(query_hits) == 1
        hit = query_hits[0]
        assert hit["query_match"] is True and hit["evidence_for_query"] is True
        assert hit["source_reference"]["locator_validation"] == "validated"
        if expect == "move":
            assert hit["live_locator"]["folder"] == "Archiv/2026"
        if expect == "locator":
            current_folders = {
                str(item["folder"])
                for item in hit["locators"]
                if item["current_in_index"]
            }
            assert current_folders == {"Archiv/2026", "Spamverdacht"}
        if expect == "copy":
            assert len(hit["occurrence_ids"]) == 2
        if expect == "quarantine":
            assert any(item["quarantine"] for item in hit["locators"])
        if expect == "deleted":
            missing = service.search("ZX-2048", limit=20)
            assert not any(item["query_match"] for item in missing["results"])
            assert missing["complete"] is True

        request("POST", "/control", {"action": "embedding-error-on"})
        degraded = service.search("Polarstern", limit=20)
        request("POST", "/control", {"action": "embedding-error-off"})
        assert degraded["count"] == 1
        assert degraded["semantic_state"] == "degraded-lexical-only"
        assert degraded["results"][0]["evidence_for_query"] is True
        write_json(
            f"gateway-{expect}.json",
            {
                "ok": True,
                "backend": result["backend"],
                "complete": result["complete"],
                "candidate_count": result["count"],
                "query_hit_count": len(query_hits),
                "semantic_state": result["semantic_state"],
                "degraded_semantic_state": degraded["semantic_state"],
                "server_search_calls": live.search_calls,
                "live_resolve_calls": live.resolve_calls,
            },
        )
    finally:
        storage.close()
    print(f"M11 gateway hybrid search ({expect}): OK")


def reconcile(expect: str, crash_at: str) -> None:
    hook = None
    if crash_at:
        def crash_hook(boundary: str) -> None:
            if boundary == crash_at:
                raise RuntimeError(f"simulated-crash:{boundary}")

        hook = crash_hook
    result = MailSearchReconciler(
        NetworkBackend(),
        NetworkScanner(),
        projection_root=PROJECTION,
        state_path=STATE / "reconcile.json",
        quarantine_folders=("Spamverdacht",),
        limits=ReconcileLimits(request_interval_seconds=0, retention_generations=3),
        hook=hook,
    ).run(approved=True)
    if expect == "partial":
        assert result["ok"] is False and result["published"] is False
        assert result["error"]["code"] == "partial-folder-scan"
    else:
        assert result["ok"] is True and result["complete"] is True
        metrics = result["metrics"]
        if expect in {"move", "rename", "quarantine", "uidvalidity"}:
            assert metrics["body_fetches"] == 0
            assert metrics["parser_calls"] == 0
            assert metrics["ocr_calls"] == 0
            assert metrics["clamav_calls"] == 0
            assert metrics["model_calls"] == 0
            assert metrics["fts_rows_changed"] == 0
        if expect == "new":
            assert metrics["new"] == 1 and metrics["body_fetches"] == 1
        if expect == "copy":
            assert metrics["copied"] == 1 and metrics["body_fetches"] == 0
        if expect == "delete":
            assert metrics["removed"] >= 1
    write_json(f"reconcile-{expect}.json", result)
    print(f"M11 authoritative reconciliation ({expect}): OK")


def mutate(action: str) -> None:
    result = request("POST", "/control", {"action": action})
    assert result["ok"] is True
    print(f"M11 fixture mutation {action}: OK")


def metrics(name: str, compare: str) -> None:
    current = request("GET", "/metrics")
    if compare:
        previous = json.loads((STATE / f"metrics-{compare}.json").read_text(encoding="utf-8"))
        for field in ("raw_fetches", "clamav_calls", "embedding_calls"):
            assert current[field] == previous[field], f"unexpected {field} work"
    write_json(f"metrics-{name}.json", current)
    print(f"M11 resource counters {name}: OK")


def network(expect_unreachable: bool) -> None:
    try:
        request("GET", "/health")
    except Exception as exc:
        if expect_unreachable:
            print(f"M11 network loss observed: {type(exc).__name__}")
            return
        raise
    if expect_unreachable:
        raise RuntimeError("fake service unexpectedly reachable")
    print("M11 network restored")


def summary() -> None:
    def load(name: str) -> dict[str, Any]:
        value = json.loads((STATE / name).read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise RuntimeError(f"invalid summary fixture: {name}")
        return value

    backfill_result = load("backfill-result.json")
    incremental = load("reconcile-new.json")["metrics"]
    move = load("reconcile-move.json")["metrics"]
    before_move = load("metrics-before-move.json")
    after_move = load("metrics-after-move.json")
    before_locator = load("metrics-before-locator-only.json")
    after_locator = load("metrics-after-locator-only.json")
    payload = {
        "backfill": backfill_result,
        "incremental_new": incremental,
        "pure_move": move,
        "pure_move_external_work_delta": {
            key: int(after_move[key]) - int(before_move[key])
            for key in ("raw_fetches", "clamav_calls", "embedding_calls")
        },
        "locator_only_external_work_delta": {
            key: int(after_locator[key]) - int(before_locator[key])
            for key in ("raw_fetches", "clamav_calls", "embedding_calls")
        },
    }
    print(json.dumps(payload, sort_keys=True))


def main() -> None:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("backfill")
    commands.add_parser("sync-daemon")
    commands.add_parser("clear-ready")
    commands.add_parser("summary")
    gateway_parser = commands.add_parser("gateway")
    gateway_parser.add_argument("--expect", required=True)
    reconcile_parser = commands.add_parser("reconcile")
    reconcile_parser.add_argument("--expect", required=True)
    reconcile_parser.add_argument("--crash-at", default="")
    mutate_parser = commands.add_parser("mutate")
    mutate_parser.add_argument("action")
    metrics_parser = commands.add_parser("metrics")
    metrics_parser.add_argument("name")
    metrics_parser.add_argument("--compare", default="")
    network_parser = commands.add_parser("network")
    network_parser.add_argument("--expect-unreachable", action="store_true")
    args = parser.parse_args()
    if args.command == "backfill":
        backfill()
    elif args.command == "sync-daemon":
        sync_daemon()
    elif args.command == "clear-ready":
        clear_ready()
    elif args.command == "summary":
        summary()
    elif args.command == "gateway":
        gateway(args.expect)
    elif args.command == "reconcile":
        reconcile(args.expect, args.crash_at)
    elif args.command == "mutate":
        mutate(args.action)
    elif args.command == "metrics":
        metrics(args.name, args.compare)
    elif args.command == "network":
        network(args.expect_unreachable)


if __name__ == "__main__":
    main()
