from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

VALID_CATEGORIES = {"spam", "relevant", "appointment", "routine", "uncertain"}
VALID_EVENT_STATUSES = {"confirmed", "tentative", "proposed"}


def coerce_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "ja", "on"}
    return False


@dataclass(slots=True)
class Envelope:
    mailbox_id: str
    subject: str = ""
    sender_name: str = ""
    sender_addr: str = ""
    date: str = ""
    received_at: str = ""


@dataclass(slots=True)
class AttachmentInfo:
    filename: str
    content_type: str
    size: int = 0


@dataclass(slots=True)
class ParsedMessage:
    stable_key: str
    mailbox_id: str
    source_folder: str
    raw: bytes
    message_id: str = ""
    subject: str = ""
    sender_name: str = ""
    sender_addr: str = ""
    recipients: list[str] = field(default_factory=list)
    date: str = ""
    received_at: str = ""
    body_text: str = ""
    attachments: list[AttachmentInfo] = field(default_factory=list)
    calendar_invites: list[str] = field(default_factory=list)

    @property
    def sender_domain(self) -> str:
        if "@" not in self.sender_addr:
            return ""
        return self.sender_addr.rsplit("@", 1)[-1].lower().strip()


@dataclass(slots=True)
class CalendarEvent:
    title: str
    start: str
    end: str | None = None
    all_day: bool = False
    timezone: str = "Europe/Berlin"
    location: str = ""
    participants: list[str] = field(default_factory=list)
    notes: str = ""
    confidence: float = 0.0
    status: str = "proposed"
    uid: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> CalendarEvent | None:
        if not isinstance(data, dict):
            return None
        title = str(data.get("title") or "").strip()
        start = str(data.get("start") or "").strip()
        if not title or not start:
            return None
        status = str(data.get("status") or "proposed").strip().lower()
        if status not in VALID_EVENT_STATUSES:
            status = "proposed"
        participants = data.get("participants") or []
        if not isinstance(participants, list):
            participants = []
        try:
            confidence = float(data.get("confidence", 0.0))
        except (TypeError, ValueError):
            confidence = 0.0
        return cls(
            title=title[:300],
            start=start,
            end=str(data.get("end") or "").strip() or None,
            all_day=coerce_bool(data.get("all_day", False)),
            timezone=str(data.get("timezone") or "Europe/Berlin").strip(),
            location=str(data.get("location") or "").strip()[:500],
            participants=[str(item).strip() for item in participants if str(item).strip()][:50],
            notes=str(data.get("notes") or "").strip()[:4000],
            confidence=max(0.0, min(1.0, confidence)),
            status=status,
            uid=str(data.get("uid") or "").strip()[:500],
        )




@dataclass(slots=True)
class InvoiceSignal:
    is_invoice: bool = False
    confidence: float = 0.0
    reason: str = ""
    pdf_filenames: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> InvoiceSignal | None:
        if not isinstance(data, dict):
            return None
        try:
            confidence = float(data.get("confidence", 0.0))
        except (TypeError, ValueError):
            confidence = 0.0
        filenames = data.get("pdf_filenames") or []
        if not isinstance(filenames, list):
            filenames = []
        return cls(
            is_invoice=coerce_bool(data.get("is_invoice", False)),
            confidence=max(0.0, min(1.0, confidence)),
            reason=str(data.get("reason") or "").strip()[:1000],
            pdf_filenames=[str(item).strip()[:500] for item in filenames if str(item).strip()][:20],
        )


@dataclass(slots=True)
class OrderSignal:
    is_order_event: bool = False
    event_type: str = "unknown"
    confidence: float = 0.0
    merchant: str = ""
    order_number: str = ""
    ordered_at: str = ""
    expected_delivery: str = ""
    carrier: str = ""
    tracking_numbers: list[str] = field(default_factory=list)
    items: list[str] = field(default_factory=list)
    amount: str = ""
    currency: str = "EUR"
    return_deadline: str = ""
    reason: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> OrderSignal | None:
        if not isinstance(data, dict):
            return None
        try:
            confidence = max(0.0, min(1.0, float(data.get("confidence", 0.0))))
        except (TypeError, ValueError):
            confidence = 0.0
        allowed = {"order_placed", "order_confirmation", "preparing", "shipping", "tracking", "out_for_delivery", "delivered", "return_started", "return_shipped", "return_received", "refund", "cancelled", "unknown"}
        event_type = str(data.get("event_type") or "unknown").strip().casefold()
        if event_type not in allowed:
            event_type = "unknown"
        tracking = data.get("tracking_numbers") or []
        items = data.get("items") or []
        return cls(
            is_order_event=coerce_bool(data.get("is_order_event", False)),
            event_type=event_type, confidence=confidence,
            merchant=str(data.get("merchant") or "").strip()[:200],
            order_number=str(data.get("order_number") or "").strip()[:200],
            ordered_at=str(data.get("ordered_at") or "").strip()[:80],
            expected_delivery=str(data.get("expected_delivery") or "").strip()[:80],
            carrier=str(data.get("carrier") or "").strip()[:100],
            tracking_numbers=[str(v).strip()[:200] for v in tracking if str(v).strip()][:20],
            items=[str(v).strip()[:300] for v in items if str(v).strip()][:50],
            amount=str(data.get("amount") or "").strip()[:80],
            currency=str(data.get("currency") or "EUR").strip().upper()[:8],
            return_deadline=str(data.get("return_deadline") or "").strip()[:80],
            reason=str(data.get("reason") or "").strip()[:1000],
        )


@dataclass(slots=True)
class Classification:
    category: str
    confidence: float
    importance: int
    forward: bool
    reason: str
    summary: str = ""
    expected_action: str = ""
    calendar_event: CalendarEvent | None = None
    invoice: InvoiceSignal | None = None
    order: OrderSignal | None = None
    source: str = "model"

    def __post_init__(self) -> None:
        if self.category not in VALID_CATEGORIES:
            self.category = "uncertain"
        self.confidence = max(0.0, min(1.0, float(self.confidence)))
        self.importance = max(1, min(10, int(self.importance)))
        self.reason = self.reason.strip()[:1000]
        self.summary = self.summary.strip()[:2000]
        self.expected_action = self.expected_action.strip()[:1000]
        if self.category not in {"relevant", "appointment"}:
            self.forward = False

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        return data


@dataclass(slots=True)
class OperationResult:
    ok: bool
    status: str
    detail: str = ""
    destination: str = ""
    path: str = ""
