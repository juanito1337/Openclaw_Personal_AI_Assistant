from __future__ import annotations

import re
import uuid
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from typing import Any

RUN_RESULTS = (
    "completed",
    "degraded",
    "interrupted",
    "skipped-not-due",
    "in-progress",
    "blocked",
    "failed",
)
TERMINAL_RUN_RESULTS = tuple(value for value in RUN_RESULTS if value != "in-progress")

_LEGACY_RESULTS = {
    "ok": "completed",
    "success": "completed",
    "error": "failed",
    "running": "in-progress",
    "deferred": "blocked",
    "cancelled": "blocked",
}
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,159}$")


def canonical_run_result(value: object, *, allow_legacy: bool = False) -> str:
    """Return one result from the closed M16 runtime vocabulary.

    Legacy conversion is intentionally opt-in and exists only for reading old
    telemetry. New writers must use the closed vocabulary directly.
    """

    clean = str(value or "").strip().casefold()
    if allow_legacy:
        clean = _LEGACY_RESULTS.get(clean, clean)
    if clean not in RUN_RESULTS:
        raise ValueError(f"Unbekanntes Laufergebnis: {value}")
    return clean


def result_from_exit_code(exit_code: int, *, interrupted: bool = False) -> str:
    if interrupted:
        return "interrupted"
    if int(exit_code) == 0:
        return "completed"
    if int(exit_code) == 1:
        return "degraded"
    return "failed"


def _identifier(value: object, *, field: str, required: bool) -> str:
    clean = str(value or "").strip()
    if not clean and not required:
        return ""
    if not _IDENTIFIER.fullmatch(clean):
        raise ValueError(f"Ungueltige {field}")
    return clean


@dataclass(frozen=True, slots=True)
class RunIdentity:
    run_id: str
    attempt_id: str
    job_id: str
    parent_run_id: str = ""

    @classmethod
    def create(
        cls,
        *,
        job_id: str,
        run_id: str = "",
        attempt_id: str = "",
        parent_run_id: str = "",
    ) -> RunIdentity:
        return cls(
            run_id=_identifier(run_id or uuid.uuid4().hex, field="Run-ID", required=True),
            attempt_id=_identifier(
                attempt_id or uuid.uuid4().hex,
                field="Attempt-ID",
                required=True,
            ),
            job_id=_identifier(job_id, field="Job-ID", required=True),
            parent_run_id=_identifier(
                parent_run_id,
                field="Parent-Run-ID",
                required=False,
            ),
        )

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


def logical_run_id(record: Mapping[str, Any]) -> str:
    """Return the root identity used for cross-component aggregation."""

    return str(record.get("parent_run_id") or record.get("run_id") or "")
