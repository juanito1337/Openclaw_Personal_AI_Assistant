from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

PROJECTION_MANIFEST = "_projection.json"
PROJECTION_SCHEMA = 1
PROJECTION_SCHEMA_V2 = 2


class SearchProjectionError(ValueError):
    """Raised when a published projection is incomplete or inconsistent."""


@dataclass(frozen=True, slots=True)
class SearchProjection:
    generation: str
    generated_at: str
    age_seconds: int
    records: tuple[tuple[Path, dict[str, Any]], ...]
    schema: int = PROJECTION_SCHEMA
    complete: bool = True
    coverage: dict[str, Any] = field(default_factory=dict)
    partitions: tuple[dict[str, Any], ...] = ()
    tombstones: tuple[dict[str, Any], ...] = ()
