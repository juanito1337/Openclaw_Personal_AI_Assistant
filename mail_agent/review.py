from __future__ import annotations

from enum import StrEnum


class ReviewReason(StrEnum):
    """Closed technical reasons for a mail requiring human review.

    Values are deliberately content-free.  Category, routing status and the
    human-readable explanation remain separate fields.
    """

    CLASSIFICATION_UNCERTAIN = "classification-uncertain"
    SPAM_BELOW_THRESHOLD = "spam-below-threshold"
    ROUTINE_BELOW_THRESHOLD = "routine-below-threshold"
    RELEVANT_NOT_FORWARDED = "relevant-not-forwarded"
    INVOICE_REVIEW = "invoice-review"
    APPOINTMENT_REVIEW = "appointment-review"
    SAFETY_BLOCKED = "safety-blocked"
    UNKNOWN_LEGACY = "unknown-legacy"


REVIEW_REASON_VALUES = frozenset(reason.value for reason in ReviewReason)


def parse_review_reason(value: str | ReviewReason) -> ReviewReason:
    """Return one known reason and reject silent taxonomy expansion."""

    if isinstance(value, ReviewReason):
        return value
    normalized = str(value or "").strip().casefold()
    if not normalized:
        raise ValueError("Review-Grund fehlt")
    try:
        return ReviewReason(normalized)
    except ValueError as exc:
        raise ValueError(f"Unbekannter Review-Grund: {normalized}") from exc
