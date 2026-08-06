#!/usr/bin/env python3
"""Reproducible fixture benchmark for M3 state initialization and SQLite I/O."""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
import time
from pathlib import Path
from statistics import median

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from personal_assistant.antivirus import AntivirusStore  # noqa: E402
from personal_assistant.monitoring import MonitoringStore  # noqa: E402
from personal_assistant.portfolio import PortfolioStore  # noqa: E402
from personal_assistant.storage import AssistantStorage  # noqa: E402
from personal_assistant.work_scheduler import AdaptiveWorkScheduler  # noqa: E402


def io_bytes() -> tuple[int, int]:
    values: dict[str, int] = {}
    for line in Path("/proc/self/io").read_text(encoding="utf-8").splitlines():
        key, value = line.split(":", 1)
        values[key] = int(value)
    return values.get("read_bytes", 0), values.get("write_bytes", 0)


def initialize(root: Path, *, split: bool) -> None:
    core = root / ("core" if split else "shared")
    portfolio = root / ("portfolio" if split else "shared")
    monitoring = root / ("monitoring" if split else "shared")
    security = root / ("security" if split else "shared")
    coordination = root / ("coordination" if split else "shared")
    knowledge_root = root / "knowledge" if split else None
    previous_knowledge_root = os.environ.pop("OPENCLAW_KNOWLEDGE_DATA_DIR", None)
    if knowledge_root is not None:
        os.environ["OPENCLAW_KNOWLEDGE_DATA_DIR"] = str(knowledge_root)
    try:
        assistant = AssistantStorage(core / "assistant.sqlite3")
    finally:
        os.environ.pop("OPENCLAW_KNOWLEDGE_DATA_DIR", None)
        if previous_knowledge_root is not None:
            os.environ["OPENCLAW_KNOWLEDGE_DATA_DIR"] = previous_knowledge_root
    stores = [
        assistant,
        AntivirusStore(security / "antivirus.sqlite3"),
        PortfolioStore(portfolio / "portfolio.sqlite3"),
        MonitoringStore(monitoring / "monitoring.sqlite3"),
        AdaptiveWorkScheduler(coordination / "work_scheduler.sqlite3", arbitration_seconds=0),
    ]
    for store in stores:
        store.close()


def sqlite_roundtrip(path: Path, count: int = 500) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("CREATE TABLE events(id INTEGER PRIMARY KEY, payload TEXT)")
    with connection:
        connection.executemany(
            "INSERT INTO events(payload) VALUES(?)",
            ((f"fixture-{index}",) for index in range(count)),
        )
    connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    connection.close()


def measure(mode: str, runs: int) -> dict[str, object]:
    times: list[float] = []
    read_before, write_before = io_bytes()
    with tempfile.TemporaryDirectory() as folder:
        for index in range(runs):
            root = Path(folder) / f"{mode}-{index}"
            started = time.perf_counter()
            initialize(root, split=mode == "layout3")
            sqlite_roundtrip(
                root / ("core" if mode == "layout3" else "shared") / "roundtrip.sqlite3"
            )
            times.append((time.perf_counter() - started) * 1000)
    read_after, write_after = io_bytes()
    return {
        "runs": runs,
        "median_ms": round(median(times), 3),
        "min_ms": round(min(times), 3),
        "max_ms": round(max(times), 3),
        "read_bytes": read_after - read_before,
        "write_bytes": write_after - write_before,
    }


def main() -> int:
    result = {
        "schema": 1,
        "method": "six logical SQLite schemas plus one 500-row WAL transaction in a temporary host fixture",
        "legacy_shared_directory": measure("legacy", 7),
        "layout3_split_directories": measure("layout3", 7),
    }
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
