from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, time, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .config import WORKSPACE_ROOT
from .connectors.nextcloud.deck import NextcloudDeck
from .policy import PolicyEngine
from .registry import ResourceRegistry
from .storage import AssistantStorage
from .tool_settings import DeckOrdersToolSettings

DEFAULT_ORDERS_DB = WORKSPACE_ROOT / "personal_assistant/data/orders.sqlite3"
MANAGED_BEGIN = "<!-- PERSONAL_ASSISTANT_ORDER_BEGIN -->"
MANAGED_END = "<!-- PERSONAL_ASSISTANT_ORDER_END -->"
STACKS = (
    ("ordered", "Bestellt"),
    ("confirmed", "Auftragsbestätigung"),
    ("preparing", "Versandvorbereitung"),
    ("shipped", "Versendet"),
    ("out_for_delivery", "In Zustellung"),
    ("delivered", "Zugestellt"),
    ("return", "Retoure"),
    ("refunded", "Erstattet / Abgeschlossen"),
    ("review", "Prüfen"),
)
EVENT_TO_STATUS = {
    "order_placed": "ordered",
    "order_confirmation": "confirmed",
    "preparing": "preparing",
    "shipping": "shipped",
    "tracking": "shipped",
    "out_for_delivery": "out_for_delivery",
    "delivered": "delivered",
    "return_started": "return",
    "return_shipped": "return",
    "return_received": "return",
    "refund": "refunded",
    "cancelled": "refunded",
    "unknown": "review",
}
RANK = {name: index for index, (name, _) in enumerate(STACKS)}
DECK_TIMEZONE = ZoneInfo("Europe/Berlin")
MIN_PLAUSIBLE_DATE = date(2000, 1, 1)
MAX_PLAUSIBLE_DATE = date(2100, 12, 31)


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _norm(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").casefold())


def _mail_received_iso(value: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    try:
        parsed = parsedate_to_datetime(raw)
        if parsed is not None:
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=UTC)
            return parsed.isoformat(timespec="seconds")
    except (TypeError, ValueError, OverflowError):
        pass
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed.isoformat(timespec="seconds")
    except ValueError:
        return ""


def _date_only(value: str) -> date | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    if len(raw) >= 10:
        prefix = raw[:10]
        try:
            parsed = date.fromisoformat(prefix)
            return parsed if MIN_PLAUSIBLE_DATE <= parsed <= MAX_PLAUSIBLE_DATE else None
        except ValueError:
            pass
    try:
        parsed_dt = parsedate_to_datetime(raw)
        if parsed_dt is not None:
            parsed = parsed_dt.date()
            return parsed if MIN_PLAUSIBLE_DATE <= parsed <= MAX_PLAUSIBLE_DATE else None
    except (TypeError, ValueError, OverflowError):
        pass
    try:
        parsed_dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        parsed = parsed_dt.date()
        return parsed if MIN_PLAUSIBLE_DATE <= parsed <= MAX_PLAUSIBLE_DATE else None
    except ValueError:
        pass
    for fmt in ("%d.%m.%Y", "%d/%m/%Y"):
        try:
            parsed = datetime.strptime(raw, fmt).date()
            return parsed if MIN_PLAUSIBLE_DATE <= parsed <= MAX_PLAUSIBLE_DATE else None
        except ValueError:
            continue
    return None


def _row_value(row: sqlite3.Row | dict[str, Any], key: str) -> str:
    try:
        value = row[key]
    except (IndexError, KeyError):
        value = ""
    return str(value or "").strip()


@dataclass(frozen=True, slots=True)
class DueDateDecision:
    date_value: str
    deck_value: str
    source: str
    confidence: float


def _deck_due_value(value: date) -> str:
    return datetime.combine(value, time(23, 59), tzinfo=DECK_TIMEZONE).isoformat(timespec="seconds")


def _plausible_relative(candidate: date, reference: date | None) -> bool:
    if not (MIN_PLAUSIBLE_DATE <= candidate <= MAX_PLAUSIBLE_DATE):
        return False
    if reference is None:
        return True
    return reference - timedelta(days=3650) <= candidate <= reference + timedelta(days=3650)


def _select_due_date(row: sqlite3.Row | dict[str, Any]) -> DueDateDecision:
    status = _row_value(row, "status").casefold()
    received_reference = (
        _date_only(_row_value(row, "last_mail_received_at"))
        or _date_only(_row_value(row, "first_mail_received_at"))
        or _date_only(_row_value(row, "created_at"))
    )
    candidates: list[tuple[str, str, float]] = []
    if status == "return":
        candidates.append((_row_value(row, "return_deadline"), "return-deadline", 0.99))
    candidates.extend([
        (_row_value(row, "expected_delivery"), "expected-delivery", 0.97),
        (_row_value(row, "ordered_at"), "order-date", 0.88),
        (_row_value(row, "last_mail_received_at"), "mail-received-date", 1.0),
        (_row_value(row, "first_mail_received_at"), "first-mail-received-date", 1.0),
        (_row_value(row, "last_event_at"), "processing-date-fallback", 0.50),
        (_row_value(row, "created_at"), "record-created-date-fallback", 0.45),
    ])
    for raw, source, confidence in candidates:
        parsed = _date_only(raw)
        if parsed is not None and _plausible_relative(parsed, received_reference):
            return DueDateDecision(parsed.isoformat(), _deck_due_value(parsed), source, confidence)
    today = datetime.now(DECK_TIMEZONE).date()
    return DueDateDecision(today.isoformat(), _deck_due_value(today), "current-date-last-resort", 0.25)


@dataclass(slots=True)
class OrderEvent:
    event_type: str
    confidence: float
    merchant: str = ""
    order_number: str = ""
    ordered_at: str = ""
    expected_delivery: str = ""
    carrier: str = ""
    tracking_numbers: tuple[str, ...] = ()
    items: tuple[str, ...] = ()
    amount: str = ""
    currency: str = "EUR"
    return_deadline: str = ""
    reason: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> OrderEvent:
        event_type = str(data.get("event_type") or "unknown").strip().casefold()
        if event_type not in EVENT_TO_STATUS:
            event_type = "unknown"
        try:
            confidence = max(0.0, min(1.0, float(data.get("confidence", 0.0))))
        except (TypeError, ValueError):
            confidence = 0.0
        tracking = data.get("tracking_numbers") or []
        items = data.get("items") or []
        return cls(
            event_type=event_type,
            confidence=confidence,
            merchant=str(data.get("merchant") or "").strip()[:200],
            order_number=str(data.get("order_number") or "").strip()[:200],
            ordered_at=str(data.get("ordered_at") or "").strip()[:80],
            expected_delivery=str(data.get("expected_delivery") or "").strip()[:80],
            carrier=str(data.get("carrier") or "").strip()[:100],
            tracking_numbers=tuple(str(v).strip()[:200] for v in tracking if str(v).strip())[:20],
            items=tuple(str(v).strip()[:300] for v in items if str(v).strip())[:50],
            amount=str(data.get("amount") or "").strip()[:80],
            currency=str(data.get("currency") or "EUR").strip().upper()[:8],
            return_deadline=str(data.get("return_deadline") or "").strip()[:80],
            reason=str(data.get("reason") or "").strip()[:1000],
        )


class OrderStore:
    def __init__(self, path: Path = DEFAULT_ORDERS_DB) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA busy_timeout=5000")
        self.db.executescript(
            """
            CREATE TABLE IF NOT EXISTS orders (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              order_key TEXT NOT NULL UNIQUE,
              merchant TEXT, order_number TEXT, status TEXT NOT NULL,
              items_json TEXT NOT NULL DEFAULT '[]', amount TEXT, currency TEXT,
              ordered_at TEXT, expected_delivery TEXT, carrier TEXT,
              tracking_json TEXT NOT NULL DEFAULT '[]', return_deadline TEXT,
              last_event_at TEXT, source_subject TEXT, source_sender TEXT,
              first_mail_received_at TEXT, last_mail_received_at TEXT,
              created_from_category TEXT,
              deck_due_date TEXT, deck_due_source TEXT, deck_due_confidence REAL,
              deck_board_id INTEGER, deck_stack_id INTEGER, deck_card_id INTEGER,
              sync_status TEXT NOT NULL DEFAULT 'pending', sync_error TEXT,
              created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_orders_status ON orders(status);
            CREATE INDEX IF NOT EXISTS idx_orders_delivery ON orders(expected_delivery);
            CREATE TABLE IF NOT EXISTS order_events (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              event_key TEXT NOT NULL UNIQUE,
              order_id INTEGER NOT NULL,
              stable_key TEXT, event_type TEXT NOT NULL, confidence REAL NOT NULL,
              payload_json TEXT NOT NULL, source_subject TEXT, source_sender TEXT,
              received_at TEXT, source_category TEXT, created_at TEXT NOT NULL,
              FOREIGN KEY(order_id) REFERENCES orders(id)
            );
            """
        )
        order_columns = {str(row[1]) for row in self.db.execute("PRAGMA table_info(orders)").fetchall()}
        for column, declaration in (
            ("first_mail_received_at", "TEXT"),
            ("last_mail_received_at", "TEXT"),
            ("created_from_category", "TEXT"),
            ("deck_due_date", "TEXT"),
            ("deck_due_source", "TEXT"),
            ("deck_due_confidence", "REAL"),
        ):
            if column not in order_columns:
                self.db.execute(f"ALTER TABLE orders ADD COLUMN {column} {declaration}")
        event_columns = {str(row[1]) for row in self.db.execute("PRAGMA table_info(order_events)").fetchall()}
        for column, declaration in (("received_at", "TEXT"), ("source_category", "TEXT")):
            if column not in event_columns:
                self.db.execute(f"ALTER TABLE order_events ADD COLUMN {column} {declaration}")
        self.db.commit()

    def close(self) -> None:
        self.db.close()

    def find(self, order_key: str) -> sqlite3.Row | None:
        return self.db.execute("SELECT * FROM orders WHERE order_key=?", (order_key,)).fetchone()

    def find_by_order_number(self, order_number_key: str) -> sqlite3.Row | None:
        if not order_number_key:
            return None
        rows = self.db.execute("SELECT * FROM orders WHERE order_number!='' ORDER BY updated_at DESC").fetchall()
        return next((row for row in rows if _norm(str(row["order_number"])) == order_number_key), None)

    def find_by_tracking(self, tracking: tuple[str, ...]) -> sqlite3.Row | None:
        for number in tracking:
            row = self.db.execute("SELECT * FROM orders WHERE tracking_json LIKE ? ORDER BY updated_at DESC LIMIT 1", (f'%"{number}"%',)).fetchone()
            if row:
                return row
        return None

    def list(self, *, status: str = "", limit: int = 100) -> list[dict[str, Any]]:
        if status:
            rows = self.db.execute("SELECT * FROM orders WHERE status=? ORDER BY updated_at DESC LIMIT ?", (status, limit)).fetchall()
        else:
            rows = self.db.execute("SELECT * FROM orders ORDER BY updated_at DESC LIMIT ?", (limit,)).fetchall()
        return [self._dict(row) for row in rows]

    def pending(self, limit: int = 500) -> list[sqlite3.Row]:
        return self.db.execute("SELECT * FROM orders WHERE sync_status!='ok' ORDER BY updated_at LIMIT ?", (limit,)).fetchall()

    def with_deck_cards(self, limit: int = 500) -> list[sqlite3.Row]:
        return self.db.execute(
            "SELECT * FROM orders WHERE deck_card_id IS NOT NULL AND deck_card_id>0 ORDER BY updated_at DESC LIMIT ?",
            (limit,),
        ).fetchall()

    def upsert_event(
        self, event: OrderEvent, *, stable_key: str, subject: str, sender: str,
        received_at: str = "", source_category: str = "",
    ) -> tuple[sqlite3.Row, bool, bool]:
        merchant_key = _norm(event.merchant or sender.rsplit("@", 1)[-1])
        order_no = _norm(event.order_number)
        tracking_key = _norm(event.tracking_numbers[0]) if event.tracking_numbers else ""
        if order_no:
            existing_by_number = self.find_by_order_number(order_no)
            order_key = str(existing_by_number["order_key"]) if existing_by_number else f"{merchant_key}:{order_no}"
        elif tracking_key:
            existing = self.find_by_tracking(event.tracking_numbers)
            order_key = str(existing["order_key"]) if existing else f"tracking:{tracking_key}"
        else:
            order_key = f"review:{_norm(stable_key)}"
        row = self.find(order_key)
        event_key = f"{stable_key}:{event.event_type}:{order_key}"
        if row is not None:
            duplicate = self.db.execute(
                "SELECT 1 FROM order_events WHERE event_key=?", (event_key,)
            ).fetchone()
            if duplicate:
                return row, False, True
        created = row is None
        current_status = str(row["status"]) if row else "review"
        proposed = EVENT_TO_STATUS[event.event_type]
        if current_status == "refunded":
            status = "refunded"
        elif current_status == "return":
            status = "refunded" if proposed == "refunded" else "return"
        elif current_status == "review" and proposed != "review" or proposed in {"return", "refunded"} or RANK.get(proposed, 0) >= RANK.get(current_status, 0):
            status = proposed
        else:
            status = current_status
        old_items = json.loads(str(row["items_json"])) if row else []
        old_tracking = json.loads(str(row["tracking_json"])) if row else []
        items = list(dict.fromkeys([*old_items, *event.items]))
        tracking = list(dict.fromkeys([*old_tracking, *event.tracking_numbers]))
        timestamp = _now()
        received_iso = _mail_received_iso(received_at)
        pending_card_creation = bool(row is not None and not int(row["deck_card_id"] or 0))
        first_mail_received_at = (
            received_iso
            if row is None or (pending_card_creation and not str(row["first_mail_received_at"] or ""))
            else str(row["first_mail_received_at"] or "")
        )
        last_mail_received_at = received_iso or (str(row["last_mail_received_at"] or "") if row else "")
        created_from_category = (
            str(source_category or "").strip().casefold()
            if row is None or (pending_card_creation and not str(row["created_from_category"] or ""))
            else str(row["created_from_category"] or "")
        )
        values = (
            order_key,
            event.merchant or (str(row["merchant"]) if row else ""),
            event.order_number or (str(row["order_number"]) if row else ""),
            status,
            json.dumps(items, ensure_ascii=False),
            event.amount or (str(row["amount"]) if row else ""),
            event.currency or (str(row["currency"]) if row else "EUR"),
            event.ordered_at or (str(row["ordered_at"]) if row else ""),
            event.expected_delivery or (str(row["expected_delivery"]) if row else ""),
            event.carrier or (str(row["carrier"]) if row else ""),
            json.dumps(tracking, ensure_ascii=False),
            event.return_deadline or (str(row["return_deadline"]) if row else ""),
            timestamp,
            subject,
            sender,
            first_mail_received_at,
            last_mail_received_at,
            created_from_category,
            str(row["deck_due_date"] or "") if row else "",
            str(row["deck_due_source"] or "") if row else "",
            float(row["deck_due_confidence"] or 0.0) if row else 0.0,
            timestamp,
            timestamp,
        )
        self.db.execute(
            """
            INSERT INTO orders(order_key,merchant,order_number,status,items_json,amount,currency,ordered_at,expected_delivery,carrier,tracking_json,return_deadline,last_event_at,source_subject,source_sender,first_mail_received_at,last_mail_received_at,created_from_category,deck_due_date,deck_due_source,deck_due_confidence,created_at,updated_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(order_key) DO UPDATE SET
              merchant=excluded.merchant,order_number=excluded.order_number,status=excluded.status,
              items_json=excluded.items_json,amount=excluded.amount,currency=excluded.currency,
              ordered_at=excluded.ordered_at,expected_delivery=excluded.expected_delivery,
              carrier=excluded.carrier,tracking_json=excluded.tracking_json,
              return_deadline=excluded.return_deadline,last_event_at=excluded.last_event_at,
              source_subject=excluded.source_subject,source_sender=excluded.source_sender,
              first_mail_received_at=excluded.first_mail_received_at,
              last_mail_received_at=excluded.last_mail_received_at,
              created_from_category=excluded.created_from_category,
              deck_due_date=excluded.deck_due_date,
              deck_due_source=excluded.deck_due_source,
              deck_due_confidence=excluded.deck_due_confidence,
              sync_status='pending',sync_error='',updated_at=excluded.updated_at
            """,
            values,
        )
        row = self.find(order_key)
        assert row is not None
        if not int(row["deck_card_id"] or 0) or not str(row["deck_due_date"] or "").strip():
            due = _select_due_date(row)
            self.set_due_metadata(
                int(row["id"]), date_value=due.date_value,
                source=due.source, confidence=due.confidence,
            )
            row = self.find(order_key)
            assert row is not None
        self.db.execute(
            "INSERT OR IGNORE INTO order_events(event_key,order_id,stable_key,event_type,confidence,payload_json,source_subject,source_sender,received_at,source_category,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (
                event_key, int(row["id"]), stable_key, event.event_type, event.confidence,
                json.dumps(asdict(event), ensure_ascii=False), subject, sender, received_iso,
                str(source_category or "").strip().casefold(), timestamp,
            ),
        )
        self.db.commit()
        return row, created, False

    def set_due_metadata(self, order_id: int, *, date_value: str, source: str, confidence: float) -> None:
        self.db.execute(
            "UPDATE orders SET deck_due_date=?,deck_due_source=?,deck_due_confidence=? WHERE id=?",
            (date_value, source, float(confidence), order_id),
        )

    def set_deck(self, order_id: int, *, board_id: int, stack_id: int, card_id: int) -> None:
        self.db.execute("UPDATE orders SET deck_board_id=?,deck_stack_id=?,deck_card_id=?,sync_status='ok',sync_error='',updated_at=? WHERE id=?", (board_id, stack_id, card_id, _now(), order_id))
        self.db.commit()

    def set_sync_error(self, order_id: int, error: str) -> None:
        self.db.execute("UPDATE orders SET sync_status='error',sync_error=?,updated_at=? WHERE id=?", (error[:2000], _now(), order_id))
        self.db.commit()

    @staticmethod
    def _dict(row: sqlite3.Row) -> dict[str, Any]:
        data = dict(row)
        data["items"] = json.loads(str(data.pop("items_json") or "[]"))
        data["tracking_numbers"] = json.loads(str(data.pop("tracking_json") or "[]"))
        return data


class OrderDeckService:
    def __init__(
        self,
        settings: DeckOrdersToolSettings,
        registry: ResourceRegistry,
        policy: PolicyEngine,
        audit: AssistantStorage,
        deck: NextcloudDeck,
        *,
        store: OrderStore | None = None,
    ) -> None:
        self.settings = settings
        self.registry = registry
        self.policy = policy
        self.audit = audit
        self.deck = deck
        self.store = store or OrderStore(settings.database)

    def close(self) -> None:
        self.store.close()

    def discover(self) -> list[dict[str, Any]]:
        return self.deck.list_boards(details=True)

    def status(self, *, live: bool = True) -> dict[str, Any]:
        resource = self.registry.resources.get(self.settings.resource_id)
        required_permissions = {
            permission
            for enabled, permission in (
                (self.settings.allow_read, "read"),
                (self.settings.allow_create, "create"),
                (self.settings.allow_update, "update"),
                (self.settings.allow_move, "move"),
            )
            if enabled
        }
        resource_permissions = set(resource.permissions) if resource else set()
        resource_ok = bool(
            resource
            and resource.enabled
            and resource.kind == "deck-board"
            and resource.connector == "nextcloud"
            and str(resource.remote_id) == str(self.settings.board_id)
            and required_permissions.issubset(resource_permissions)
        )
        result: dict[str, Any] = {
            "ok": bool(self.settings.enabled and resource_ok),
            "enabled": self.settings.enabled,
            "resource_id": self.settings.resource_id,
            "board_id": self.settings.board_id,
            "board_title": self.settings.board_title,
            "auto_process_mail": self.settings.auto_process_mail,
            "min_confidence": self.settings.min_confidence,
            "permissions": sorted(resource_permissions),
            "required_permissions": sorted(required_permissions),
            "resource_valid": resource_ok,
            "database": str(self.settings.database),
            "managed_only": True,
            "delete_allowed": False,
            "share_allowed": False,
        }
        if live and result["ok"]:
            try:
                board = self.deck.get_board(self.settings.board_id)
                stacks = self.deck.list_stacks(self.settings.board_id)
                result["live"] = {"ok": True, "board": board.get("title", ""), "stacks": [s.get("title", "") for s in stacks]}
            except Exception as exc:
                result["ok"] = False
                result["live"] = {"ok": False, "error": str(exc)}
        return result

    def list_orders(self, *, status: str = "", limit: int = 100) -> dict[str, Any]:
        return {"ok": True, "orders": self.store.list(status=status, limit=max(1, min(limit, 1000)))}

    def process_event(
        self, data: dict[str, Any], *, stable_key: str, subject: str, sender: str,
        received_at: str = "", source_category: str = "", dry_run: bool = False,
    ) -> dict[str, Any]:
        event = OrderEvent.from_dict(data)
        if not self.settings.enabled or not self.settings.auto_process_mail:
            return {"ok": True, "status": "orders-disabled"}
        if event.confidence < self.settings.min_confidence:
            return {"ok": True, "status": "order-low-confidence", "confidence": event.confidence}
        if dry_run:
            return {"ok": True, "status": "would-process-order", "event": asdict(event)}
        row, created, duplicate = self.store.upsert_event(
            event, stable_key=stable_key, subject=subject, sender=sender,
            received_at=received_at, source_category=source_category,
        )
        if duplicate:
            return {
                "ok": True,
                "status": "order-duplicate",
                "order": self.store._dict(row),
                "deck": {"action": "unchanged", "card_id": int(row["deck_card_id"] or 0)},
            }
        try:
            synced = self._sync_row(row)
            self.audit.audit("orders.event.processed", {"stable_key": stable_key, "event": asdict(event), "created": created, "sync": synced}, resource_id=self.settings.resource_id, actor="mail-order-tool")
            return {"ok": True, "status": "order-created" if created else "order-updated", "order": self.store._dict(self.store.find(str(row["order_key"]))) if self.store.find(str(row["order_key"])) else {}, "deck": synced}
        except Exception as exc:
            self.store.set_sync_error(int(row["id"]), str(exc))
            self.audit.audit("orders.deck.sync_failed", {"stable_key": stable_key, "order_key": row["order_key"], "error": str(exc)}, resource_id=self.settings.resource_id, actor="mail-order-tool")
            return {"ok": True, "status": "order-recorded-sync-pending", "error": str(exc), "order_key": row["order_key"]}

    def sync_pending(self, *, limit: int = 500) -> dict[str, Any]:
        ok = 0
        errors: list[dict[str, Any]] = []
        for row in self.store.pending(limit=max(1, min(limit, 5000))):
            try:
                self._sync_row(row)
                ok += 1
            except Exception as exc:
                self.store.set_sync_error(int(row["id"]), str(exc))
                errors.append({"order_key": row["order_key"], "error": str(exc)})
        return {"ok": not errors, "synced": ok, "errors": errors}

    def backfill_missing_due_dates(self, *, limit: int = 500, dry_run: bool = True) -> dict[str, Any]:
        resource = self.registry.get(self.settings.resource_id)
        if resource.kind != "deck-board" or resource.connector != "nextcloud":
            raise PermissionError("Bestellressource ist kein freigegebenes Nextcloud Deck-Board")
        result: dict[str, Any] = {
            "ok": True,
            "dry_run": bool(dry_run),
            "scanned": 0,
            "would_update": 0,
            "updated": 0,
            "preserved_existing": 0,
            "unmanaged": 0,
            "missing_cards": 0,
            "errors": [],
            "items": [],
        }
        for row in self.store.with_deck_cards(limit=max(1, min(limit, 5000))):
            result["scanned"] += 1
            card_id = int(row["deck_card_id"] or 0)
            stack_id = int(row["deck_stack_id"] or 0)
            try:
                card = self.deck.get_card(self.settings.board_id, stack_id, card_id)
            except Exception as exc:
                result["missing_cards"] += 1
                result["errors"].append({"order_key": row["order_key"], "card_id": card_id, "error": str(exc)})
                continue
            description = str(card.get("description") or "")
            if MANAGED_BEGIN not in description or MANAGED_END not in description:
                result["unmanaged"] += 1
                continue
            existing_due = self._existing_card_due(card, row)
            if existing_due:
                result["preserved_existing"] += 1
                result["items"].append({
                    "order_key": row["order_key"], "card_id": card_id,
                    "action": "preserved", "due_date": existing_due,
                    "source": "existing-deck-date",
                })
                continue
            decision = _select_due_date(row)
            result["would_update"] += 1
            result["items"].append({
                "order_key": row["order_key"], "card_id": card_id,
                "action": "would-update" if dry_run else "updated",
                "due_date": decision.deck_value, "source": decision.source,
                "confidence": decision.confidence,
            })
            if dry_run:
                continue
            policy = self.policy.decide(
                resource.id, "deck.card.update",
                {"board_id": self.settings.board_id, "card_id": card_id, "managed": True},
            )
            if not policy.allowed:
                result["errors"].append({"order_key": row["order_key"], "card_id": card_id, "error": policy.reason})
                continue
            owner = str(card.get("owner") or self.deck.client.username)
            updated_description = self._replace_managed(description, self._description(row, decision))
            self.deck.update_card(
                self.settings.board_id, stack_id, card_id,
                title=str(card.get("title") or self._title(row)),
                description=updated_description,
                owner=owner,
                order=int(card.get("order") or 999),
                duedate=decision.deck_value,
                archived=bool(card.get("archived", False)),
                done=card.get("done"),
            )
            self.store.set_due_metadata(
                int(row["id"]), date_value=decision.date_value,
                source=decision.source, confidence=decision.confidence,
            )
            self.store.db.commit()
            self.audit.audit(
                "orders.deck.due_date_backfilled",
                {"order_key": row["order_key"], "card_id": card_id, "due_date": decision.date_value, "source": decision.source},
                resource_id=self.settings.resource_id, actor="deck-order-tool",
            )
            result["updated"] += 1
        if result["errors"]:
            result["ok"] = False
        if len(result["items"]) > 200:
            result["items"] = result["items"][:200]
            result["items_truncated"] = True
        return result

    def _stack_map(self) -> dict[str, int]:
        stacks = self.deck.list_stacks(self.settings.board_id)
        by_name = {str(item.get("title") or "").casefold(): int(item["id"]) for item in stacks if item.get("id") is not None}
        missing = [title for _, title in STACKS if title.casefold() not in by_name]
        if missing:
            raise RuntimeError("Deck-Spalten fehlen: " + ", ".join(missing))
        return {status: by_name[title.casefold()] for status, title in STACKS}

    def _sync_row(self, row: sqlite3.Row) -> dict[str, Any]:
        resource = self.registry.get(self.settings.resource_id)
        if resource.kind != "deck-board" or resource.connector != "nextcloud":
            raise PermissionError("Bestellressource ist kein freigegebenes Nextcloud Deck-Board")
        stacks = self._stack_map()
        status = str(row["status"])
        target_stack = stacks.get(status, stacks["review"])
        title = self._title(row)
        due_decision = _select_due_date(row)
        due = due_decision.deck_value
        card_id = int(row["deck_card_id"] or 0)
        current_stack = int(row["deck_stack_id"] or 0)
        if not card_id:
            self.store.set_due_metadata(
                int(row["id"]), date_value=due_decision.date_value,
                source=due_decision.source, confidence=due_decision.confidence,
            )
            self.store.db.commit()
            description = self._description(row, due_decision)
            decision = self.policy.decide(resource.id, "deck.card.create", {"board_id": self.settings.board_id, "managed": True})
            if not decision.allowed:
                raise PermissionError(decision.reason)
            card = self.deck.create_card(self.settings.board_id, target_stack, title=title, description=description, duedate=due)
            card_id = int(card["id"])
            self.store.set_deck(int(row["id"]), board_id=self.settings.board_id, stack_id=target_stack, card_id=card_id)
            return {"action": "created", "card_id": card_id, "stack_id": target_stack}
        try:
            card = self.deck.get_card(self.settings.board_id, current_stack, card_id)
        except Exception:
            card = {}
        if not card:
            self.store.set_due_metadata(
                int(row["id"]), date_value=due_decision.date_value,
                source=due_decision.source, confidence=due_decision.confidence,
            )
            self.store.db.commit()
            description = self._description(row, due_decision)
            new_card = self.deck.create_card(self.settings.board_id, target_stack, title=title, description=description, duedate=due)
            card_id = int(new_card["id"])
            self.store.set_deck(int(row["id"]), board_id=self.settings.board_id, stack_id=target_stack, card_id=card_id)
            return {"action": "recreated", "card_id": card_id, "stack_id": target_stack}
        old_description = str(card.get("description") or "")
        if MANAGED_BEGIN not in old_description or MANAGED_END not in old_description:
            raise PermissionError("Deck-Karte besitzt keine Agenten-Markierung und wird nicht veraendert")
        decision = self.policy.decide(resource.id, "deck.card.update", {"board_id": self.settings.board_id, "card_id": card_id, "managed": True})
        if not decision.allowed:
            raise PermissionError(decision.reason)
        owner = str(card.get("owner") or self.deck.client.username)
        existing_due = self._existing_card_due(card, row)
        effective_due = existing_due or due
        stored_due_date = _row_value(row, "deck_due_date")
        stored_due_source = _row_value(row, "deck_due_source")
        stored_due_confidence = float(row["deck_due_confidence"] or 0.0)
        if existing_due and stored_due_date == existing_due[:10] and stored_due_source:
            effective_decision = DueDateDecision(
                stored_due_date, existing_due, stored_due_source,
                stored_due_confidence or 1.0,
            )
        elif existing_due:
            effective_decision = DueDateDecision(existing_due[:10], existing_due, "existing-deck-date", 1.0)
        else:
            effective_decision = due_decision
        if existing_due:
            self.store.set_due_metadata(
                int(row["id"]), date_value=effective_decision.date_value,
                source=effective_decision.source, confidence=effective_decision.confidence,
            )
            self.store.db.commit()
        else:
            self.store.set_due_metadata(
                int(row["id"]), date_value=effective_decision.date_value,
                source=effective_decision.source, confidence=effective_decision.confidence,
            )
            self.store.db.commit()
        description = self._description(row, effective_decision)
        self.deck.update_card(self.settings.board_id, current_stack, card_id, title=title, description=self._replace_managed(old_description, description), owner=owner, order=int(card.get("order") or 999), duedate=effective_due, archived=bool(card.get("archived", False)), done=card.get("done"))
        if current_stack != target_stack:
            move = self.policy.decide(resource.id, "deck.card.move", {"board_id": self.settings.board_id, "card_id": card_id, "managed": True})
            if not move.allowed:
                raise PermissionError(move.reason)
            self.deck.move_card(self.settings.board_id, current_stack, card_id, target_stack)
        self.store.set_deck(int(row["id"]), board_id=self.settings.board_id, stack_id=target_stack, card_id=card_id)
        return {"action": "updated", "card_id": card_id, "stack_id": target_stack}

    @staticmethod
    def _replace_managed(old: str, new: str) -> str:
        before, rest = old.split(MANAGED_BEGIN, 1)
        _, after = rest.split(MANAGED_END, 1)
        managed = new.split(MANAGED_BEGIN, 1)[1].split(MANAGED_END, 1)[0]
        return before.rstrip() + "\n\n" + MANAGED_BEGIN + managed + MANAGED_END + after

    @staticmethod
    def _existing_card_due(card: dict[str, Any], row: sqlite3.Row | dict[str, Any] | None = None) -> str:
        raw = str(card.get("duedate") or card.get("dueDate") or "").strip()
        parsed = _date_only(raw)
        if parsed is None:
            return ""
        reference = None
        if row is not None:
            reference = (
                _date_only(_row_value(row, "last_mail_received_at"))
                or _date_only(_row_value(row, "first_mail_received_at"))
                or _date_only(_row_value(row, "created_at"))
            )
        return raw if _plausible_relative(parsed, reference) else ""

    @classmethod
    def _deck_card_date(cls, row: sqlite3.Row) -> str:
        return _select_due_date(row).deck_value

    @staticmethod
    def _deck_due(value: str) -> str | None:
        parsed = _date_only(value)
        return _deck_due_value(parsed) if parsed is not None else None

    @staticmethod
    def _title(row: sqlite3.Row) -> str:
        merchant = str(row["merchant"] or "Unbekannter Händler")
        number = str(row["order_number"] or "ohne Bestellnummer")
        items = json.loads(str(row["items_json"] or "[]"))
        item = str(items[0]) if items else "Bestellung"
        return f"{merchant} – {item} – {number}"[:255]

    @staticmethod
    def _description(row: sqlite3.Row, due: DueDateDecision | None = None) -> str:
        items = json.loads(str(row["items_json"] or "[]"))
        tracking = json.loads(str(row["tracking_json"] or "[]"))
        due = due or _select_due_date(row)
        lines = [
            MANAGED_BEGIN,
            "## Automatisch verwaltete Bestellung",
            f"- **Status:** {row['status']}",
            f"- **Händler:** {row['merchant'] or 'unbekannt'}",
            f"- **Bestellnummer:** {row['order_number'] or 'unbekannt'}",
            f"- **Erste Quellmail eingegangen:** {str(row['first_mail_received_at'] or '')[:10] or 'unbekannt'}",
            f"- **Bestellt am (falls aus Mailinhalt erkannt):** {row['ordered_at'] or 'unbekannt'}",
            f"- **Betrag:** {(str(row['amount']) + ' ' + str(row['currency'] or '')).strip() or 'unbekannt'}",
            f"- **Erwartete Lieferung:** {row['expected_delivery'] or 'unbekannt'}",
            f"- **Versanddienstleister:** {row['carrier'] or 'unbekannt'}",
            f"- **Tracking:** {', '.join(tracking) if tracking else 'noch nicht vorhanden'}",
            f"- **Retourenfrist:** {row['return_deadline'] or 'unbekannt'}",
            f"- **Deck-Fälligkeitsdatum:** {due.date_value}",
            f"- **Datumsquelle:** {due.source} (Konfidenz {due.confidence:.2f})",
            "",
            "### Artikel",
            *(f"- {item}" for item in items),
            "" if items else "- noch nicht extrahiert",
            "",
            f"Letzte Quellmail: {row['source_subject'] or 'unbekannt'}",
            f"Letzte Aktualisierung: {row['updated_at']}",
            MANAGED_END,
        ]
        return "\n".join(lines)
