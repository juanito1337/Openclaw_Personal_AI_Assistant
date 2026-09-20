#!/usr/bin/env python3
"""Reproducible, content-free M16.3 incremental-sync benchmark."""

from __future__ import annotations

import argparse
import json
import math
import os
import statistics
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

from personal_assistant.config import AssistantConfig, RuntimeConfig, SearchConfig
from personal_assistant.connectors.nextcloud.files import NextcloudFiles, RemoteFile
from personal_assistant.storage import AssistantStorage
from personal_assistant.work_scheduler import AdaptiveWorkScheduler

ROOT = Path(__file__).resolve().parents[1]


class _Client:
    username = "benchmark"

    @staticmethod
    def validate_url() -> str:
        return "https://benchmark.invalid"


class _Files(NextcloudFiles):
    def __init__(self, config: AssistantConfig, count: int) -> None:
        super().__init__(config, _Client())
        self.entries = [self._entry(index, 1) for index in range(count)]
        self.download_count = 0
        self.download_bytes = 0

    @staticmethod
    def _entry(index: int, revision: int) -> RemoteFile:
        path = f"Assistent/fixture-{index:05d}.txt"
        return RemoteFile(
            href=f"/remote.php/dav/files/benchmark/{path}",
            path=path,
            name=Path(path).name,
            is_collection=False,
            content_type="text/plain",
            size=64,
            etag=f'"{index}-{revision}"',
            modified_at=f"2026-09-20T10:{index % 60:02d}:00Z",
        )

    def list_folder(self, path: str) -> list[RemoteFile]:
        return list(self.entries) if path == "Assistent" else []

    def download(self, path: str) -> bytes:
        payload = ("synthetic fixture " + path.rsplit("-", 1)[-1]).encode("utf-8")
        self.download_count += 1
        self.download_bytes += len(payload)
        return payload

    def change_one(self, index: int, revision: int) -> None:
        self.entries[index] = self._entry(index, revision)


class _Indexer:
    def __init__(self, storage: AssistantStorage) -> None:
        self.storage = storage
        self.writes = 0

    def index_binary_document(self, **values: object) -> bool:
        data = bytes(values["data"])
        self.storage.index_document(
            source_type=str(values["source_type"]),
            resource_id=str(values["resource_id"]),
            source_id=str(values["source_id"]),
            uri=str(values["uri"]),
            title=str(values["title"]),
            mime_type=str(values["mime_type"]),
            modified_at=str(values["modified_at"]),
            etag=str(values["etag"]),
            metadata=dict(values["metadata"]),
            chunks=[data.decode("utf-8")],
        )
        self.writes += 1
        return True


def _percentile(values: list[float], value: float) -> float:
    ordered = sorted(values)
    return round(ordered[max(0, math.ceil(len(ordered) * value) - 1)], 3)


def _summary(values: list[float]) -> dict[str, Any]:
    return {
        "samples": len(values),
        "p50": round(statistics.median(values), 3),
        "p95": _percentile(values, 0.95),
        "minimum": round(min(values), 3),
        "maximum": round(max(values), 3),
        "unit": "ms",
    }


def _sync(files: _Files, storage: AssistantStorage, indexer: _Indexer) -> dict[str, Any]:
    return files.sync_index(
        storage,
        indexer,  # type: ignore[arg-type]
        resource_id="nextcloud-files-main",
        roots=("Assistent",),
        max_items=10_000,
        max_depth=2,
        batch_size=10_000,
    )


def _measure(action) -> tuple[dict[str, Any], float, float]:
    cpu_started = time.process_time()
    wall_started = time.perf_counter()
    result = action()
    wall_ms = (time.perf_counter() - wall_started) * 1000.0
    cpu_ms = (time.process_time() - cpu_started) * 1000.0
    return result, wall_ms, cpu_ms


def benchmark(samples: int, object_count: int) -> dict[str, Any]:
    full_wall: list[float] = []
    full_cpu: list[float] = []
    noop_wall: list[float] = []
    noop_cpu: list[float] = []
    delta_wall: list[float] = []
    delta_cpu: list[float] = []
    scheduler_wall: list[float] = []
    logical_io: dict[str, list[int]] = {
        "full_downloads": [],
        "noop_downloads": [],
        "delta_downloads": [],
        "full_projection_writes": [],
        "noop_projection_writes": [],
        "delta_projection_writes": [],
    }
    with tempfile.TemporaryDirectory(prefix="openclaw-m163-") as temporary:
        root = Path(temporary)
        for sample in range(samples):
            database = root / f"sample-{sample}.sqlite3"
            config = AssistantConfig(
                runtime=RuntimeConfig(database=database),
                search=SearchConfig(
                    chunk_chars=500,
                    chunk_overlap_chars=0,
                    max_file_bytes=1_000_000,
                ),
            )
            storage = AssistantStorage(database)
            files = _Files(config, object_count)
            indexer = _Indexer(storage)
            try:
                before_downloads = files.download_count
                before_writes = indexer.writes
                _result, wall, cpu = _measure(
                    lambda files=files, storage=storage, indexer=indexer: _sync(files, storage, indexer)
                )
                full_wall.append(wall)
                full_cpu.append(cpu)
                logical_io["full_downloads"].append(files.download_count - before_downloads)
                logical_io["full_projection_writes"].append(indexer.writes - before_writes)

                before_downloads = files.download_count
                before_writes = indexer.writes
                _result, wall, cpu = _measure(
                    lambda files=files, storage=storage, indexer=indexer: _sync(files, storage, indexer)
                )
                noop_wall.append(wall)
                noop_cpu.append(cpu)
                logical_io["noop_downloads"].append(files.download_count - before_downloads)
                logical_io["noop_projection_writes"].append(indexer.writes - before_writes)

                files.change_one(sample % object_count, sample + 2)
                before_downloads = files.download_count
                before_writes = indexer.writes
                _result, wall, cpu = _measure(
                    lambda files=files, storage=storage, indexer=indexer: _sync(files, storage, indexer)
                )
                delta_wall.append(wall)
                delta_cpu.append(cpu)
                logical_io["delta_downloads"].append(files.download_count - before_downloads)
                logical_io["delta_projection_writes"].append(indexer.writes - before_writes)
            finally:
                storage.close()

            scheduler = AdaptiveWorkScheduler(root / f"scheduler-{sample}.sqlite3", arbitration_seconds=0)
            try:
                sync_ticket = scheduler.enqueue("sync", owner="sync-worker")
                started = time.perf_counter()
                first = scheduler.claim(sync_ticket, owner="sync-worker")
                scheduler.finish(
                    first.lease_token,
                    owner="sync-worker",
                    result="completed",
                    exit_code=75,
                )
                scheduler.enqueue("sync", owner="sync-worker", parent_run_id=first.run_id)
                mail = scheduler.enqueue("mail", owner="mail-worker")
                claimed = scheduler.claim(mail, owner="mail-worker")
                scheduler_wall.append((time.perf_counter() - started) * 1000.0)
                if not claimed.granted:
                    raise RuntimeError("Zeitkritische Mailarbeit wurde nicht bevorzugt")
            finally:
                scheduler.close()

    revision = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    return {
        "schema": 1,
        "milestone": "M16.3",
        "source_revision": revision,
        "sample_count": samples,
        "object_count": object_count,
        "data_policy": "synthetic-content-only-aggregate-metrics",
        "m16_0_comparison": {
            "status": "not-comparable",
            "reason": "M16.0 kennzeichnet Full-, Delta- und No-op-Sync sowie Scheduler als not-measured.",
            "regression_claim": "not-evaluated",
        },
        "measurements": {
            "full": {"wall": _summary(full_wall), "cpu": _summary(full_cpu)},
            "noop": {"wall": _summary(noop_wall), "cpu": _summary(noop_cpu)},
            "single_delta": {"wall": _summary(delta_wall), "cpu": _summary(delta_cpu)},
            "scheduler_mail_between_batches": {"wall": _summary(scheduler_wall)},
            "logical_io": {
                key: {"samples": values, "maximum": max(values)} for key, values in logical_io.items()
            },
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--samples", type=int, default=5)
    parser.add_argument("--objects", type=int, default=100)
    parser.add_argument("--output", type=Path, default=ROOT / "build/m16.3-sync-benchmark.json")
    args = parser.parse_args()
    if args.samples < 3:
        raise SystemExit("Mindestens drei Samples sind erforderlich")
    if args.objects < 2:
        raise SystemExit("Mindestens zwei synthetische Objekte sind erforderlich")
    payload = benchmark(args.samples, args.objects)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_suffix(args.output.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, args.output)
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
