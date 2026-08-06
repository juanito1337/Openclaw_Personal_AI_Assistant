from __future__ import annotations

from datetime import UTC, datetime


def now_utc_iso() -> str:
    """Return the canonical UTC timestamp without depending on a domain adapter."""
    return datetime.now(UTC).isoformat()
