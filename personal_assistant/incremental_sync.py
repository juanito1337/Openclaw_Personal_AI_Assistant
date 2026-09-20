from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from typing import Any

SYNC_STAGES = (
    "discovery",
    "metadata_compare",
    "download",
    "parse",
    "index_update",
    "commit",
)


@dataclass(frozen=True, slots=True)
class RemoteObject:
    remote_id: str
    etag: str = ""
    modified_at: str = ""


@dataclass(frozen=True, slots=True)
class BatchPlan:
    generation: str
    objects: tuple[RemoteObject, ...]
    offset: int
    next_offset: int
    total: int
    resumed: bool
    cursor_reset: bool
    complete: bool

    @property
    def resume_required(self) -> bool:
        return not self.complete

    def cursor(self) -> str:
        if self.complete:
            return ""
        return json.dumps(
            {"version": 1, "generation": self.generation, "offset": self.next_offset},
            sort_keys=True,
            separators=(",", ":"),
        )


def remote_generation(objects: list[RemoteObject]) -> str:
    digest = hashlib.sha256()
    for item in sorted(objects, key=lambda value: value.remote_id):
        digest.update(item.remote_id.encode("utf-8"))
        digest.update(b"\0")
        digest.update(item.etag.encode("utf-8"))
        digest.update(b"\0")
        digest.update(item.modified_at.encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest()


def plan_batch(
    objects: list[RemoteObject],
    *,
    cursor: str = "",
    batch_size: int = 100,
) -> BatchPlan:
    ordered = sorted(objects, key=lambda value: value.remote_id)
    generation = remote_generation(ordered)
    offset = 0
    resumed = False
    cursor_reset = False
    if str(cursor or "").strip():
        try:
            payload = json.loads(cursor)
            cursor_generation = str(payload.get("generation") or "")
            cursor_offset = int(payload.get("offset") or 0)
            if (
                int(payload.get("version") or 0) == 1
                and cursor_generation == generation
                and 0 <= cursor_offset <= len(ordered)
            ):
                offset = cursor_offset
                resumed = offset > 0
            else:
                cursor_reset = True
        except (TypeError, ValueError, json.JSONDecodeError):
            cursor_reset = True
    size = max(1, int(batch_size))
    next_offset = min(len(ordered), offset + size)
    return BatchPlan(
        generation=generation,
        objects=tuple(ordered[offset:next_offset]),
        offset=offset,
        next_offset=next_offset,
        total=len(ordered),
        resumed=resumed,
        cursor_reset=cursor_reset,
        complete=next_offset >= len(ordered),
    )


@dataclass(slots=True)
class StageTelemetry:
    """Content-free timing and I/O counters for one incremental sync batch."""

    durations_ms: dict[str, float] = field(default_factory=lambda: {stage: 0.0 for stage in SYNC_STAGES})
    counters: dict[str, int] = field(default_factory=dict)
    _started: dict[str, float] = field(default_factory=dict, repr=False)

    def start(self, stage: str) -> None:
        if stage not in SYNC_STAGES:
            raise ValueError(f"Unbekannte Sync-Stufe: {stage}")
        self._started[stage] = time.perf_counter()

    def stop(self, stage: str) -> None:
        started = self._started.pop(stage, None)
        if started is None:
            raise RuntimeError(f"Sync-Stufe wurde nicht gestartet: {stage}")
        self.durations_ms[stage] = round(
            self.durations_ms[stage] + (time.perf_counter() - started) * 1000.0,
            3,
        )

    def add(self, name: str, value: int = 1) -> None:
        clean = str(name or "").strip()
        if not clean:
            raise ValueError("Telemetriezaehler benoetigt einen Namen")
        self.counters[clean] = self.counters.get(clean, 0) + int(value)

    def stop_running(self) -> None:
        for stage in tuple(self._started):
            self.stop(stage)

    def to_dict(self) -> dict[str, Any]:
        if self._started:
            raise RuntimeError("Laufende Sync-Stufen koennen nicht publiziert werden")
        return {
            "stages_ms": dict(self.durations_ms),
            "counters": dict(sorted(self.counters.items())),
            "content_fields": [],
        }
