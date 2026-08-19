#!/usr/bin/env python3
"""Offline M11.0 baseline for the current server and SQLite mail-search paths."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import resource
import statistics
import sys
import tempfile
import time
import tracemalloc
from collections.abc import Callable
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from email.message import EmailMessage
from email.policy import SMTP
from email.utils import format_datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mail_agent.models import Envelope, ParsedMessage  # noqa: E402
from mail_agent.parser import parse_eml  # noqa: E402
from mail_agent.search_snapshot import SearchSnapshotWriter  # noqa: E402
from personal_assistant.adapters.mail import MailMoveService  # noqa: E402
from personal_assistant.config import (  # noqa: E402
    AssistantConfig,
    RuntimeConfig,
    SearchConfig,
)
from personal_assistant.knowledge import KnowledgeIndexer  # noqa: E402
from personal_assistant.models import Resource  # noqa: E402
from personal_assistant.policy import PolicyEngine  # noqa: E402
from personal_assistant.registry import ResourceRegistry  # noqa: E402
from personal_assistant.storage import AssistantStorage  # noqa: E402
from personal_assistant.tool_settings import MailMoveToolSettings  # noqa: E402

DEFAULT_CORPUS = ROOT / "tests/fixtures/mail_search/m110_synthetic_corpus.json"


@dataclass(slots=True)
class SyntheticMail:
    fixture_id: str
    projected: bool
    thread_id: str
    parsed: ParsedMessage


class FakeImapClient:
    """Minimal current-contract fake; it deliberately has no delta or UID state."""

    def __init__(self, folders: list[str], messages: list[SyntheticMail]) -> None:
        self.folders = list(folders)
        self.messages = list(messages)
        self.error_folders: set[str] = set()
        self.reset_counters()

    def reset_counters(self) -> None:
        self.folder_list_calls = 0
        self.search_calls = 0
        self.response_bytes = 0
        self.raw_fetches = 0
        self.body_fetches = 0

    def list_folders(self) -> tuple[list[str], str]:
        self.folder_list_calls += 1
        self.response_bytes += len(json.dumps(self.folders).encode("utf-8"))
        return list(self.folders), ""

    def search_envelopes(
        self,
        folder: str,
        terms: list[str],
        *,
        limit: int = 50,
    ) -> tuple[list[Envelope], str]:
        self.search_calls += 1
        if folder in self.error_folders:
            return [], f"synthetic search failure: {folder}"
        folded_terms = [term.casefold() for term in terms]
        matches: list[SyntheticMail] = []
        for item in self.messages:
            if item.parsed.source_folder != folder:
                continue
            searchable = "\n".join(
                (
                    item.parsed.sender_name,
                    item.parsed.sender_addr,
                    item.parsed.subject,
                    item.parsed.body_text,
                )
            ).casefold()
            if all(term in searchable for term in folded_terms):
                matches.append(item)
        matches.sort(key=lambda item: item.parsed.received_at or item.parsed.date, reverse=True)
        envelopes = [
            Envelope(
                mailbox_id=item.fixture_id,
                subject=item.parsed.subject,
                sender_name=item.parsed.sender_name,
                sender_addr=item.parsed.sender_addr,
                date=item.parsed.date,
                received_at=item.parsed.received_at,
            )
            for item in matches[:limit]
        ]
        self.response_bytes += len(
            json.dumps([asdict(item) for item in envelopes], ensure_ascii=False).encode("utf-8")
        )
        return envelopes, ""

    def add(self, item: SyntheticMail) -> None:
        self.messages.append(item)
        if item.parsed.source_folder not in self.folders:
            self.folders.append(item.parsed.source_folder)

    def copy_occurrence(self, fixture_id: str, new_id: str, folder: str) -> None:
        source = next(item for item in self.messages if item.fixture_id == fixture_id)
        self.add(
            SyntheticMail(
                fixture_id=new_id,
                projected=False,
                thread_id=source.thread_id,
                parsed=replace(source.parsed, mailbox_id=new_id, source_folder=folder),
            )
        )

    def move_occurrence(self, fixture_id: str, new_id: str, folder: str) -> None:
        source = next(item for item in self.messages if item.fixture_id == fixture_id)
        self.messages.remove(source)
        self.add(
            SyntheticMail(
                fixture_id=new_id,
                projected=source.projected,
                thread_id=source.thread_id,
                parsed=replace(source.parsed, mailbox_id=new_id, source_folder=folder),
            )
        )

    def counters(self) -> dict[str, int]:
        return {
            "folder_list_calls": self.folder_list_calls,
            "folder_search_calls": self.search_calls,
            "raw_fetches": self.raw_fetches,
            "body_fetches": self.body_fetches,
            "response_bytes": self.response_bytes,
        }


def load_corpus(path: Path = DEFAULT_CORPUS) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    privacy = payload.get("privacy") if isinstance(payload, dict) else None
    if (
        not isinstance(payload, dict)
        or payload.get("schema_version") != 1
        or not isinstance(privacy, dict)
        or privacy.get("synthetic") is not True
        or privacy.get("productive_data") is not False
    ):
        raise ValueError("M11.0-Korpus ist nicht als rein synthetisch ausgewiesen")
    return payload


def synthetic_eml(row: dict[str, Any]) -> bytes:
    message = EmailMessage(policy=SMTP)
    message["From"] = f"{row['from_name']} <{row['from_addr']}>"
    message["To"] = ", ".join(str(item) for item in row.get("to", []))
    message["Subject"] = str(row["subject"])
    message["Message-ID"] = f"<{row['message_id']}>"
    message["Date"] = format_datetime(datetime.fromisoformat(str(row["date"])))
    if row.get("in_reply_to"):
        message["In-Reply-To"] = f"<{row['in_reply_to']}>"
    references = [f"<{item}>" for item in row.get("references", [])]
    if references:
        message["References"] = " ".join(references)
    message.set_content(str(row["body"]), charset="utf-8")
    for attachment in row.get("attachments", []):
        content_type = str(attachment.get("content_type") or "application/octet-stream")
        maintype, _, subtype = content_type.partition("/")
        message.add_attachment(
            str(attachment.get("content") or "").encode("utf-8"),
            maintype=maintype or "application",
            subtype=subtype or "octet-stream",
            filename=str(attachment.get("filename") or "attachment.bin"),
        )
    return message.as_bytes(policy=SMTP)


def materialize_messages(corpus: dict[str, Any]) -> list[SyntheticMail]:
    messages: list[SyntheticMail] = []
    for raw_row in corpus.get("messages", []):
        row = dict(raw_row)
        envelope = Envelope(
            mailbox_id=str(row["mailbox_id"]),
            subject=str(row["subject"]),
            sender_name=str(row["from_name"]),
            sender_addr=str(row["from_addr"]),
            date=str(row["date"]),
            received_at=str(row["date"]),
        )
        parsed = parse_eml(synthetic_eml(row), envelope, str(row["folder"]))
        messages.append(
            SyntheticMail(
                fixture_id=str(row["id"]),
                projected=bool(row.get("projected")),
                thread_id=str(row.get("thread_id") or ""),
                parsed=parsed,
            )
        )
    return messages


def build_runtime(
    root: Path,
    corpus: dict[str, Any],
    messages: list[SyntheticMail],
) -> tuple[MailMoveService, FakeImapClient, AssistantStorage, dict[str, Any]]:
    snapshot_root = root / "search_documents"
    writer = SearchSnapshotWriter(snapshot_root)
    for item in messages:
        if item.projected:
            writer.write(item.parsed)

    config = AssistantConfig(
        runtime=RuntimeConfig(
            database=root / "assistant.sqlite3",
            log_file=root / "assistant.log",
            resources_file=root / "resources.toml",
            policies_file=root / "policies.toml",
            secrets_file=root / "secrets.env",
        ),
        search=SearchConfig(mail_snapshot_dir=snapshot_root),
        path=root / "config.toml",
    )
    storage = AssistantStorage(config.runtime.database)
    projection_stats = KnowledgeIndexer(config, storage).index_mail_snapshots()
    registry = ResourceRegistry(config.runtime.resources_file)
    registry.resources["mail-agent"] = Resource(
        id="mail-agent",
        kind="tool",
        connector="local",
        permissions=("read", "move", "forward"),
    )
    client = FakeImapClient(list(corpus["folders"]), messages)
    service = MailMoveService(
        MailMoveToolSettings(enabled=True),
        registry,
        PolicyEngine(config.runtime.policies_file, registry),
        storage,
        client,
    )
    return service, client, storage, projection_stats


def percentile(values: list[float], percent: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    rank = max(0, math.ceil(percent * len(ordered)) - 1)
    return round(ordered[rank], 4)


def latency_summary(values: list[float], first_ms: float) -> dict[str, float | int]:
    return {
        "samples": len(values),
        "cold_first_ms": round(first_ms, 4),
        "minimum_ms": round(min(values), 4),
        "p50_ms": round(statistics.median(values), 4),
        "p95_ms": percentile(values, 0.95),
        "p99_ms": percentile(values, 0.99),
        "maximum_ms": round(max(values), 4),
    }


def ranking_metrics(relevance: dict[str, int], retrieved: list[str]) -> dict[str, Any]:
    if not relevance:
        return {
            "expected_empty": True,
            "empty_result": not retrieved,
            "unexpected_result_count": len(retrieved),
        }
    unique_retrieved = list(dict.fromkeys(retrieved))
    expected = set(relevance)

    def recall_at(k: int) -> float:
        return len(expected.intersection(unique_retrieved[:k])) / len(expected)

    reciprocal_rank = 0.0
    for index, item in enumerate(unique_retrieved, 1):
        if item in expected:
            reciprocal_rank = 1.0 / index
            break

    dcg = sum(
        (2 ** relevance.get(item, 0) - 1) / math.log2(index + 1)
        for index, item in enumerate(unique_retrieved[:10], 1)
    )
    ideal = sorted(relevance.values(), reverse=True)[:10]
    ideal_dcg = sum((2**grade - 1) / math.log2(index + 1) for index, grade in enumerate(ideal, 1))
    return {
        "expected_empty": False,
        "recall_at_5": round(recall_at(5), 4),
        "recall_at_10": round(recall_at(10), 4),
        "reciprocal_rank": round(reciprocal_rank, 4),
        "ndcg_at_10": round(dcg / ideal_dcg if ideal_dcg else 0.0, 4),
        "duplicate_hits": len(retrieved) - len(unique_retrieved),
    }


def aggregate_quality(rows: list[dict[str, Any]]) -> dict[str, Any]:
    scored = [row for row in rows if not row["metrics"]["expected_empty"]]
    negative = [row for row in rows if row["metrics"]["expected_empty"]]

    def average(field: str) -> float:
        values = [float(row["metrics"][field]) for row in scored]
        return round(statistics.fmean(values), 4) if values else 0.0

    return {
        "evaluated_queries": len(rows),
        "scored_queries": len(scored),
        "negative_queries": len(negative),
        "negative_queries_correct": sum(bool(row["metrics"]["empty_result"]) for row in negative),
        "mean_recall_at_5": average("recall_at_5"),
        "mean_recall_at_10": average("recall_at_10"),
        "mrr": average("reciprocal_rank"),
        "mean_ndcg_at_10": average("ndcg_at_10"),
        "duplicate_hits": sum(int(row["metrics"].get("duplicate_hits", 0)) for row in scored),
    }


def quality_by_kind(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    kinds = sorted({str(row["kind"]) for row in rows})
    return {
        kind: aggregate_quality([row for row in rows if row["kind"] == kind])
        for kind in kinds
    }


def timed_search(
    callback: Callable[[], Any],
    *,
    samples: int,
) -> tuple[Any, list[float], float]:
    started = time.perf_counter_ns()
    result = callback()
    first_ms = (time.perf_counter_ns() - started) / 1_000_000
    values = [first_ms]
    for _index in range(samples - 1):
        started = time.perf_counter_ns()
        result = callback()
        values.append((time.perf_counter_ns() - started) / 1_000_000)
    return result, values, first_ms


def evaluate_paths(
    corpus: dict[str, Any],
    service: MailMoveService,
    client: FakeImapClient,
    storage: AssistantStorage,
    stable_to_fixture: dict[str, str],
    *,
    samples: int,
) -> dict[str, Any]:
    server_rows: list[dict[str, Any]] = []
    local_rows: list[dict[str, Any]] = []
    server_latencies: list[float] = []
    local_latencies: list[float] = []
    server_first = 0.0
    local_first = 0.0
    client.reset_counters()
    for query_index, raw_query in enumerate(corpus["queries"]):
        query = dict(raw_query)
        server, timings, cold = timed_search(
            lambda query_text=str(query["query"]): service.search_messages(query_text, limit=10),
            samples=samples,
        )
        if query_index == 0:
            server_first = cold
        server_latencies.extend(timings)
        server_ids = [str(item["mailbox_id"]) for item in server["messages"]]
        server_rows.append(
            {
                "query_id": query["id"],
                "kind": query["kind"],
                "returned_ids": server_ids,
                "complete": bool(server["complete"]),
                "truncated": bool(server["results_may_be_truncated"]),
                "metrics": ranking_metrics(dict(query["relevance"]), server_ids),
            }
        )

        local, timings, cold = timed_search(
            lambda query_text=str(query["query"]): storage.search(
                query_text,
                limit=10,
                source_type="email",
            ),
            samples=samples,
        )
        if query_index == 0:
            local_first = cold
        local_latencies.extend(timings)
        local_ids = [stable_to_fixture.get(item.source_id, item.source_id) for item in local]
        local_rows.append(
            {
                "query_id": query["id"],
                "kind": query["kind"],
                "returned_ids": local_ids,
                "metrics": ranking_metrics(dict(query["relevance"]), local_ids),
            }
        )
    return {
        "server": {
            "quality": aggregate_quality(server_rows),
            "quality_by_kind": quality_by_kind(server_rows),
            "latency": latency_summary(server_latencies, server_first),
            "backend": client.counters(),
            "queries": server_rows,
        },
        "local_fts": {
            "quality": aggregate_quality(local_rows),
            "quality_by_kind": quality_by_kind(local_rows),
            "latency": latency_summary(local_latencies, local_first),
            "queries": local_rows,
        },
    }


def server_probe(
    service: MailMoveService,
    client: FakeImapClient,
    query: str,
    *,
    limit: int = 10,
) -> dict[str, Any]:
    client.reset_counters()
    result = service.search_messages(query, limit=limit)
    return {
        "complete": result["complete"],
        "count": result["count"],
        "truncated": result["results_may_be_truncated"],
        "failed_folders": result["failed_folders"],
        "limited_folder_count": len(result["limited_folders"]),
        "backend": client.counters(),
    }


def characterize_failures(
    corpus: dict[str, Any],
    messages: list[SyntheticMail],
    service: MailMoveService,
) -> dict[str, Any]:
    null_client = FakeImapClient(list(corpus["folders"]), messages)
    service._client_override = null_client
    null_result = server_probe(service, null_client, "Polarstation")

    partial_client = FakeImapClient(list(corpus["folders"]), messages)
    partial_client.error_folders.add("Spamverdacht")
    service._client_override = partial_client
    partial_result = server_probe(service, partial_client, "Projekt")

    limited_client = FakeImapClient(list(corpus["folders"]), messages)
    service._client_override = limited_client
    limited_result = server_probe(service, limited_client, "Projekt", limit=2)

    all_failed_client = FakeImapClient(list(corpus["folders"]), messages)
    all_failed_client.error_folders.update(corpus["folders"])
    service._client_override = all_failed_client
    all_failed_error = ""
    try:
        service.search_messages("Projekt", limit=10)
    except RuntimeError as exc:
        all_failed_error = type(exc).__name__
    return {
        "null_result": null_result,
        "one_folder_error": partial_result,
        "global_limit": limited_result,
        "all_folders_failed": {
            "failed_closed": all_failed_error == "RuntimeError",
            "error_type": all_failed_error,
            "backend": all_failed_client.counters(),
        },
    }


def scenario_result(
    service: MailMoveService,
    client: FakeImapClient,
    storage: AssistantStorage,
    stable_to_fixture: dict[str, str],
    query: str,
) -> dict[str, Any]:
    client.reset_counters()
    server = service.search_messages(query, limit=10)
    local = storage.search(query, limit=10, source_type="email")
    return {
        "server_ids": [str(item["mailbox_id"]) for item in server["messages"]],
        "server_folders": sorted({str(item["folder"]) for item in server["messages"]}),
        "local_ids": [stable_to_fixture.get(item.source_id, item.source_id) for item in local],
        "local_source_folders": sorted(
            {
                str(item.metadata.get("source_folder") or "")
                for item in local
                if item.metadata.get("source_folder")
            }
        ),
        "backend": client.counters(),
    }


def characterize_changes(
    corpus: dict[str, Any],
    messages: list[SyntheticMail],
    service: MailMoveService,
    storage: AssistantStorage,
) -> dict[str, Any]:
    folders = list(corpus["folders"])
    stable_to_fixture = {item.parsed.stable_key: item.fixture_id for item in messages}

    noop_client = FakeImapClient(folders, messages)
    service._client_override = noop_client
    service.search_messages("Tankprüfung Nordhafen", limit=10)
    noop = scenario_result(
        service,
        noop_client,
        storage,
        stable_to_fixture,
        "Tankprüfung Nordhafen",
    )

    new_client = FakeImapClient(folders, messages)
    new_row = {
        "id": "new-delta-mail",
        "mailbox_id": "new-delta-mail",
        "folder": "INBOX",
        "message_id": "new-delta-mail@example.invalid",
        "from_name": "Delta Example",
        "from_addr": "delta@example.invalid",
        "to": ["alex.owner@example.invalid"],
        "date": "2026-04-14T08:00:00+00:00",
        "subject": "Neuzugang Deltafeder",
        "body": "Die synthetische Deltafeder ist neu im Postfach.",
        "thread_id": "thread-delta",
        "projected": False,
        "attachments": [],
    }
    new_client.add(materialize_messages({"messages": [new_row]})[0])
    service._client_override = new_client
    new_mail = scenario_result(service, new_client, storage, stable_to_fixture, "Deltafeder")

    copy_client = FakeImapClient(folders, messages)
    copy_client.copy_occurrence("pump-invoice-en", "pump-invoice-copy", "Archiv/2025")
    service._client_override = copy_client
    copied = scenario_result(
        service,
        copy_client,
        storage,
        stable_to_fixture,
        "invoice ZX-2048",
    )

    move_client = FakeImapClient(folders, messages)
    move_client.move_occurrence("aurora-handover-de", "aurora-handover-moved", "Archiv/2025")
    service._client_override = move_client
    moved = scenario_result(
        service,
        move_client,
        storage,
        stable_to_fixture,
        "Übergabeprotokoll",
    )

    quarantine_client = FakeImapClient(folders, messages)
    quarantine_client.move_occurrence("rail-change-de", "rail-change-quarantine", "Spamverdacht")
    service._client_override = quarantine_client
    quarantine = scenario_result(
        service,
        quarantine_client,
        storage,
        stable_to_fixture,
        "Fahrplanänderung",
    )

    return {
        "implemented": False,
        "reason": (
            "Der aktuelle Serverpfad sucht jedes Mal alle Ordner; der lokale Index hat keinen "
            "Crawler- oder Locatorvertrag."
        ),
        "noop": {**noop, "incremental_local_update": False},
        "new_mail": {**new_mail, "incremental_local_update": False},
        "copy": {**copied, "copy_relation_tracked": False},
        "external_move": {**moved, "locator_updated_locally": False},
        "quarantine_move": {**quarantine, "locator_updated_locally": False},
        "uidvalidity_reset": {
            "supported": False,
            "reason": "Der aktuelle Envelope-/Projektionsvertrag enthaelt weder UIDVALIDITY noch UID-Cursor.",
            "folder_list_calls": 0,
            "folder_search_calls": 0,
            "raw_fetches": 0,
            "body_fetches": 0,
            "response_bytes": 0,
        },
    }


def sqlite_counts(storage: AssistantStorage) -> dict[str, int | float | None]:
    connection = storage.knowledge_connection
    documents = int(
        connection.execute("SELECT COUNT(*) FROM documents WHERE source_type='email'").fetchone()[0]
    )
    chunks = int(
        connection.execute(
            "SELECT COUNT(*) FROM chunks c JOIN documents d ON d.id=c.document_id "
            "WHERE d.source_type='email'"
        ).fetchone()[0]
    )
    locatorless = 0
    for row in connection.execute(
        "SELECT metadata_json FROM documents WHERE source_type='email'"
    ).fetchall():
        metadata = json.loads(str(row["metadata_json"] or "{}"))
        if not metadata.get("mailbox_id") and not metadata.get("uid"):
            locatorless += 1
    latest_row = connection.execute(
        "SELECT MAX(indexed_at) AS latest FROM documents WHERE source_type='email'"
    ).fetchone()
    latest_value = str(latest_row["latest"] or "") if latest_row else ""
    index_age_seconds: float | None = None
    if latest_value:
        indexed_at = datetime.fromisoformat(latest_value)
        if indexed_at.tzinfo is None:
            indexed_at = indexed_at.replace(tzinfo=UTC)
        index_age_seconds = round(
            max(0.0, (datetime.now(UTC) - indexed_at.astimezone(UTC)).total_seconds()),
            3,
        )
    return {
        "documents": documents,
        "chunks": chunks,
        "locatorless_documents": locatorless,
        "index_age_seconds": index_age_seconds,
    }


def build_report(corpus_path: Path = DEFAULT_CORPUS, *, samples: int = 7) -> dict[str, Any]:
    if samples < 3:
        raise ValueError("samples muss mindestens 3 sein")
    corpus = load_corpus(corpus_path)
    messages = materialize_messages(corpus)
    stable_to_fixture = {item.parsed.stable_key: item.fixture_id for item in messages}
    raw_bytes = sum(len(item.parsed.raw) for item in messages)
    cpu_started = time.process_time()
    wall_started = time.perf_counter()
    tracemalloc.start()
    with tempfile.TemporaryDirectory(prefix="m110-mail-search-") as temp:
        root = Path(temp)
        service, client, storage, projection_stats = build_runtime(root, corpus, messages)
        try:
            path_results = evaluate_paths(
                corpus,
                service,
                client,
                storage,
                stable_to_fixture,
                samples=samples,
            )
            failure_results = characterize_failures(corpus, messages, service)
            change_results = characterize_changes(corpus, messages, service, storage)
            inventory = sqlite_counts(storage)
            fts_enabled = storage.fts_enabled
        finally:
            storage.close()
        sqlite_bytes = sum(
            path.stat().st_size
            for path in root.rglob("*")
            if path.is_file() and ".sqlite" in path.name
        )
    _current, peak_bytes = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    projected = sum(item.projected for item in messages)
    return {
        "schema_version": 1,
        "milestone": "M11.0",
        "ok": True,
        "privacy": {
            "synthetic_only": True,
            "productive_data_read": False,
            "productive_state_written": False,
            "query_text_in_report": False,
            "mail_content_in_report": False,
        },
        "environment": {
            "python": sys.version.split()[0],
            "sqlite": __import__("sqlite3").sqlite_version,
            "fts5": fts_enabled,
            "samples_per_query": samples,
            "corpus_sha256": hashlib.sha256(corpus_path.read_bytes()).hexdigest(),
        },
        "inventory": {
            "folders": len(corpus["folders"]),
            "messages": len(messages),
            "generated_eml_bytes": raw_bytes,
            "projection_records": projected,
            "projection_coverage": round(projected / len(messages), 4),
            "projection_state": projection_stats.get("state"),
            "projection_generation_present": bool(projection_stats.get("source_generation")),
            "fts_documents": inventory["documents"],
            "fts_chunks": inventory["chunks"],
            "locatorless_documents": inventory["locatorless_documents"],
            "index_age_seconds": inventory["index_age_seconds"],
            "blocked_contents": "not-measured-current-path-has-no-full-account-crawler",
            "stale_contents": "not-provable-current-index-has-no-account-coverage-contract",
        },
        "search": path_results,
        "characterization": failure_results,
        "change_tracking": change_results,
        "resources": {
            "wall_ms": round((time.perf_counter() - wall_started) * 1000, 3),
            "cpu_ms": round((time.process_time() - cpu_started) * 1000, 3),
            "python_peak_allocated_bytes": peak_bytes,
            "process_max_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
            "temporary_sqlite_bytes": sqlite_bytes,
        },
        "known_gaps": [
            "projection-does-not-cover-full-account",
            "no-account-coverage-proof",
            "no-live-mail-locator",
            "no-incremental-move-copy-delete-reconciliation",
            "local-fts-may-return-multiple-chunks-per-mail",
            "local-snippet-is-chunk-prefix-not-query-centered",
            "no-thread-context",
            "no-attachment-filter",
            "no-structured-date-range-filter",
            "semantic-provider-disabled",
        ],
    }


def atomic_write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".m110-baseline-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        Path(temporary).replace(path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Reproduzierbare, rein synthetische M11.0-Mail-Suchbaseline"
    )
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    parser.add_argument("--samples", type=int, default=7)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.samples < 3:
        parser.error("--samples muss mindestens 3 sein")
    report = build_report(args.corpus.resolve(), samples=args.samples)
    if args.output:
        atomic_write(args.output.resolve(), report)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
