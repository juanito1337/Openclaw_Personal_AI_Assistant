from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from .models import VALID_CATEGORIES, ParsedMessage

TAG_SOURCE_VERSION = "mail-storage-schema-v4"


def _confidence(value: object) -> float | None:
    if value is None:
        return None
    if not isinstance(value, (str, int, float)):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if 0.0 <= parsed <= 1.0 else None


def _json_object(value: object) -> dict[str, Any]:
    try:
        parsed = json.loads(str(value or "{}"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _tag(
    namespace: str,
    value: str,
    *,
    source: str,
    confidence: float | None,
    field: str,
) -> dict[str, Any]:
    return {
        "namespace": namespace,
        "value": value,
        "source": source,
        "source_version": TAG_SOURCE_VERSION,
        "confidence": confidence,
        "evidence": {"field": field},
        "active": True,
        "uncertainty": "",
    }


class LocalMailTagResolver:
    """Read typed local mail decisions without creating or migrating their DB."""

    def __init__(self, database: Path) -> None:
        self.database = database
        self.connection: sqlite3.Connection | None = None
        self.tables: set[str] = set()
        if not database.is_file():
            return
        self.connection = sqlite3.connect(f"{database.resolve().as_uri()}?mode=ro", uri=True)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA query_only=ON")
        self.connection.execute("PRAGMA busy_timeout=5000")
        self.tables = {
            str(row[0])
            for row in self.connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }

    def close(self) -> None:
        if self.connection is not None:
            self.connection.close()
            self.connection = None

    def __enter__(self) -> LocalMailTagResolver:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def resolve(self, message: ParsedMessage) -> tuple[dict[str, Any], ...]:
        connection = self.connection
        if connection is None or "messages" not in self.tables:
            return ()
        row = connection.execute(
            """
            SELECT category,confidence,review_reason,review_confidence,
                   classification_json
            FROM messages WHERE stable_key=?
            """,
            (message.stable_key,),
        ).fetchone()
        tags: list[dict[str, Any]] = []
        classification: dict[str, Any] = {}
        if row is not None:
            category = str(row["category"] or "").strip().casefold()
            if category in VALID_CATEGORIES:
                tags.append(
                    _tag(
                        "category",
                        category,
                        source="classifier",
                        confidence=_confidence(row["confidence"]),
                        field="messages.category",
                    )
                )
            review_reason = str(row["review_reason"] or "").strip().casefold()
            if review_reason:
                tags.append(
                    _tag(
                        "review",
                        review_reason,
                        source="rule",
                        confidence=_confidence(row["review_confidence"]),
                        field="messages.review_reason",
                    )
                )
            classification = _json_object(row["classification_json"])

        invoice = classification.get("invoice")
        if isinstance(invoice, dict) and bool(invoice.get("is_invoice")):
            confidence = _confidence(invoice.get("confidence"))
            tags.extend(
                (
                    _tag(
                        "kind",
                        "invoice",
                        source="extractor",
                        confidence=confidence,
                        field="classification.invoice.is_invoice",
                    ),
                    _tag(
                        "category",
                        "invoice",
                        source="extractor",
                        confidence=confidence,
                        field="classification.invoice.is_invoice",
                    ),
                )
            )
        order = classification.get("order")
        if isinstance(order, dict) and bool(order.get("is_order_event")):
            confidence = _confidence(order.get("confidence"))
            tags.extend(
                (
                    _tag(
                        "kind",
                        "order",
                        source="extractor",
                        confidence=confidence,
                        field="classification.order.is_order_event",
                    ),
                    _tag(
                        "category",
                        "order",
                        source="extractor",
                        confidence=confidence,
                        field="classification.order.is_order_event",
                    ),
                )
            )
        event = classification.get("calendar_event")
        if isinstance(event, dict) and event:
            tags.append(
                _tag(
                    "kind",
                    "calendar",
                    source="extractor",
                    confidence=_confidence(event.get("confidence")),
                    field="classification.calendar_event",
                )
            )

        # Persisted typed extractor results remain useful even when the compact
        # classification snapshot predates the respective signal field.
        if "invoices" in self.tables:
            invoice_row = connection.execute(
                """
                SELECT COUNT(*) AS count,MAX(extraction_confidence) AS confidence
                FROM invoices WHERE stable_key=?
                """,
                (message.stable_key,),
            ).fetchone()
            if invoice_row is not None and int(invoice_row["count"] or 0) > 0:
                confidence = _confidence(invoice_row["confidence"])
                tags.extend(
                    (
                        _tag(
                            "kind",
                            "invoice",
                            source="extractor",
                            confidence=confidence,
                            field="invoices.stable_key",
                        ),
                        _tag(
                            "category",
                            "invoice",
                            source="extractor",
                            confidence=confidence,
                            field="invoices.stable_key",
                        ),
                    )
                )
        if "events" in self.tables:
            event_row = connection.execute(
                "SELECT 1 FROM events WHERE stable_key=? LIMIT 1",
                (message.stable_key,),
            ).fetchone()
            if event_row is not None:
                tags.append(
                    _tag(
                        "kind",
                        "calendar",
                        source="extractor",
                        confidence=None,
                        field="events.stable_key",
                    )
                )
        unique: dict[tuple[str, str, str, str], dict[str, Any]] = {}
        for tag in tags:
            key = (
                str(tag["namespace"]),
                str(tag["value"]),
                str(tag["source"]),
                str(tag["evidence"]["field"]),
            )
            unique[key] = tag
        return tuple(unique[key] for key in sorted(unique))


__all__ = ["LocalMailTagResolver", "TAG_SOURCE_VERSION"]
