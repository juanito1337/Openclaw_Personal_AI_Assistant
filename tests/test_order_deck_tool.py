from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from mail_agent.classifier import OllamaClassifier
from personal_assistant.models import Resource
from personal_assistant.orders import MANAGED_BEGIN, STACKS, OrderDeckService, OrderEvent, OrderStore
from personal_assistant.policy import PolicyEngine
from personal_assistant.registry import ResourceRegistry
from personal_assistant.storage import AssistantStorage
from personal_assistant.tool_settings import DeckOrdersToolSettings


class FakeDeck:
    def __init__(self) -> None:
        self.client = SimpleNamespace(username="openclaw")
        self.stacks = [
            {"id": index, "title": title}
            for index, (_, title) in enumerate(STACKS, start=1)
        ]
        self.cards: dict[int, dict] = {}
        self.next_id = 100
        self.moves: list[tuple[int, int]] = []

    def list_boards(self, details: bool = True):
        return [{"id": 7, "title": "Bestellungen"}]

    def get_board(self, board_id: int):
        return {"id": board_id, "title": "Bestellungen"}

    def list_stacks(self, board_id: int):
        return list(self.stacks)

    def create_card(self, board_id: int, stack_id: int, **kwargs):
        card_id = self.next_id
        self.next_id += 1
        card = {"id": card_id, "stackId": stack_id, "owner": "openclaw", "order": 999, "archived": False, "done": None, **kwargs}
        self.cards[card_id] = card
        return card

    def get_card(self, board_id: int, stack_id: int, card_id: int):
        card = self.cards.get(card_id)
        if not card or int(card["stackId"]) != int(stack_id):
            raise RuntimeError("not found")
        return dict(card)

    def update_card(self, board_id: int, stack_id: int, card_id: int, **kwargs):
        self.cards[card_id].update(kwargs)
        return dict(self.cards[card_id])

    def move_card(self, board_id: int, stack_id: int, card_id: int, destination_stack_id: int, order: int = 999):
        self.cards[card_id]["stackId"] = destination_stack_id
        self.moves.append((stack_id, destination_stack_id))
        return {}


class OrderDeckTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.registry = ResourceRegistry(root / "resources.toml")
        self.registry.upsert(Resource(
            id="nextcloud-deck-orders", kind="deck-board", connector="nextcloud",
            enabled=True, remote_id="7", permissions=("read", "create", "update", "move"),
            metadata={"name": "Bestellungen", "managed_by": "personal-assistant"},
        ))
        self.storage = AssistantStorage(root / "assistant.sqlite3")
        self.policy = PolicyEngine(root / "policies.toml", self.registry)
        self.deck = FakeDeck()
        self.settings = DeckOrdersToolSettings(
            enabled=True, resource_id="nextcloud-deck-orders", board_id=7,
            database=root / "orders.sqlite3", min_confidence=0.8,
        )
        self.service = OrderDeckService(
            self.settings, self.registry, self.policy, self.storage, self.deck,
            store=OrderStore(self.settings.database),
        )

    def tearDown(self) -> None:
        self.service.close()
        self.storage.close()
        self.tmp.cleanup()

    def event(self, event_type: str, **extra):
        data = {
            "event_type": event_type,
            "confidence": 0.95,
            "merchant": "Beispiel Shop",
            "order_number": "AB-1234",
            "items": ["Akkuschrauber"],
            "amount": "189.90",
            "currency": "EUR",
            **extra,
        }
        return data

    def test_full_lifecycle_uses_one_managed_card(self):
        first = self.service.process_event(self.event("order_confirmation"), stable_key="m1", subject="Auftragsbestätigung", sender="shop@example.test")
        self.assertTrue(first["ok"])
        self.assertEqual(first["status"], "order-created")
        self.assertEqual(len(self.deck.cards), 1)
        card_id = next(iter(self.deck.cards))
        self.assertIn(MANAGED_BEGIN, self.deck.cards[card_id]["description"])

        shipped = self.service.process_event(self.event("shipping", carrier="DHL", tracking_numbers=["00340434"], expected_delivery="2026-07-25"), stable_key="m2", subject="Versandt", sender="dhl@example.test")
        self.assertEqual(shipped["status"], "order-updated")
        self.assertEqual(len(self.deck.cards), 1)
        orders = self.service.list_orders()["orders"]
        self.assertEqual(orders[0]["status"], "shipped")
        self.assertEqual(orders[0]["tracking_numbers"], ["00340434"])

        delivered = self.service.process_event(self.event("delivered"), stable_key="m3", subject="Zugestellt", sender="dhl@example.test")
        self.assertEqual(delivered["status"], "order-updated")
        returned = self.service.process_event(self.event("return_started", return_deadline="2026-08-10"), stable_key="m4", subject="Retoure", sender="shop@example.test")
        self.assertEqual(returned["status"], "order-updated")
        refunded = self.service.process_event(self.event("refund"), stable_key="m5", subject="Erstattung", sender="shop@example.test")
        self.assertEqual(refunded["status"], "order-updated")
        self.assertEqual(self.service.list_orders()["orders"][0]["status"], "refunded")
        self.assertEqual(len(self.deck.cards), 1)

    def test_status_requires_exact_managed_board_permissions(self):
        status = self.service.status(live=False)
        self.assertTrue(status["ok"])
        self.assertTrue(status["resource_valid"])
        self.assertEqual(status["required_permissions"], ["create", "move", "read", "update"])
        self.assertFalse(status["delete_allowed"])
        self.assertFalse(status["share_allowed"])

    def test_same_mail_event_is_idempotent(self):
        first = self.service.process_event(
            self.event("order_confirmation"), stable_key="same-mail",
            subject="Auftragsbestätigung", sender="shop@example.test",
        )
        second = self.service.process_event(
            self.event("order_confirmation"), stable_key="same-mail",
            subject="Auftragsbestätigung", sender="shop@example.test",
        )
        self.assertEqual(first["status"], "order-created")
        self.assertEqual(second["status"], "order-duplicate")
        self.assertEqual(len(self.deck.cards), 1)

    def test_low_confidence_is_not_written(self):
        data = self.event("shipping")
        data["confidence"] = 0.5
        result = self.service.process_event(data, stable_key="low", subject="x", sender="x@example.test")
        self.assertEqual(result["status"], "order-low-confidence")
        self.assertEqual(self.service.list_orders()["orders"], [])

    def test_unmanaged_card_is_never_overwritten(self):
        first = self.service.process_event(self.event("order_confirmation"), stable_key="m1", subject="x", sender="x@example.test")
        card_id = first["deck"]["card_id"]
        self.deck.cards[card_id]["description"] = "manual card"
        result = self.service.process_event(self.event("shipping"), stable_key="m2", subject="y", sender="y@example.test")
        self.assertEqual(result["status"], "order-recorded-sync-pending")
        self.assertEqual(self.deck.cards[card_id]["description"], "manual card")

    def test_routine_created_card_prefers_expected_delivery(self):
        first = self.service.process_event(
            self.event("order_confirmation", ordered_at="2026-07-01", expected_delivery="2026-08-10"),
            stable_key="routine-1", subject="Auftragsbestätigung", sender="shop@example.test",
            received_at="Fri, 24 Jul 2026 14:35:00 +0200", source_category="routine",
        )
        card_id = first["deck"]["card_id"]
        self.assertEqual(self.deck.cards[card_id]["duedate"], "2026-08-10T23:59:00+02:00")
        self.assertIn("Erste Quellmail eingegangen:** 2026-07-24", self.deck.cards[card_id]["description"])
        self.assertIn("Datumsquelle:** expected-delivery", self.deck.cards[card_id]["description"])

        self.service.process_event(
            self.event("shipping", expected_delivery="2026-08-12"),
            stable_key="routine-2", subject="Versandt", sender="shop@example.test",
            received_at="Sat, 25 Jul 2026 09:10:00 +0200", source_category="routine",
        )
        self.assertEqual(self.deck.cards[card_id]["duedate"], "2026-08-10T23:59:00+02:00")
        order = self.service.list_orders()["orders"][0]
        self.assertEqual(order["first_mail_received_at"], "2026-07-24T14:35:00+02:00")
        self.assertEqual(order["last_mail_received_at"], "2026-07-25T09:10:00+02:00")
        self.assertEqual(order["created_from_category"], "routine")
        self.assertEqual(order["deck_due_source"], "expected-delivery")

    def test_order_date_is_used_when_delivery_is_missing(self):
        first = self.service.process_event(
            self.event("order_confirmation", ordered_at="2026-07-01", expected_delivery=""),
            stable_key="ordered-date", subject="Auftragsbestätigung", sender="shop@example.test",
            received_at="Fri, 24 Jul 2026 14:35:00 +0200", source_category="routine",
        )
        card_id = first["deck"]["card_id"]
        self.assertEqual(self.deck.cards[card_id]["duedate"], "2026-07-01T23:59:00+02:00")

    def test_mail_arrival_is_safe_fallback_when_no_content_date_exists(self):
        first = self.service.process_event(
            self.event("order_confirmation", ordered_at="", expected_delivery=""),
            stable_key="mail-date", subject="Auftragsbestätigung", sender="shop@example.test",
            received_at="Fri, 24 Jul 2026 14:35:00 +0200", source_category="routine",
        )
        card_id = first["deck"]["card_id"]
        self.assertEqual(self.deck.cards[card_id]["duedate"], "2026-07-24T23:59:00+02:00")
        self.assertIn("Datumsquelle:** mail-received-date", self.deck.cards[card_id]["description"])

    def test_due_date_is_always_present_even_without_mail_header(self):
        first = self.service.process_event(
            self.event("order_confirmation", ordered_at="", expected_delivery=""),
            stable_key="last-resort", subject="Auftragsbestätigung", sender="shop@example.test",
        )
        card_id = first["deck"]["card_id"]
        self.assertTrue(self.deck.cards[card_id]["duedate"])

    def test_pending_legacy_order_uses_best_date_when_card_is_first_created(self):
        row, created, duplicate = self.service.store.upsert_event(
            OrderEvent.from_dict(self.event("order_confirmation", expected_delivery="2026-08-10")),
            stable_key="legacy-pending", subject="Alt", sender="shop@example.test",
        )
        self.assertTrue(created)
        self.assertFalse(duplicate)
        result = self.service.process_event(
            self.event("shipping", expected_delivery="2026-08-12"),
            stable_key="legacy-pending-update", subject="Versandt", sender="shop@example.test",
            received_at="Sat, 25 Jul 2026 09:10:00 +0200", source_category="routine",
        )
        card_id = result["deck"]["card_id"]
        self.assertEqual(self.deck.cards[card_id]["duedate"], "2026-08-12T23:59:00+02:00")

    def test_relevant_created_card_keeps_expected_delivery_date(self):
        first = self.service.process_event(
            self.event("order_confirmation", expected_delivery="2026-08-10"),
            stable_key="relevant-1", subject="Auftragsbestätigung", sender="shop@example.test",
            received_at="Fri, 24 Jul 2026 14:35:00 +0200", source_category="relevant",
        )
        card_id = first["deck"]["card_id"]
        self.assertEqual(self.deck.cards[card_id]["duedate"], "2026-08-10T23:59:00+02:00")

    def test_existing_plausible_due_date_is_not_overwritten(self):
        first = self.service.process_event(
            self.event("order_confirmation", expected_delivery="2026-08-10"),
            stable_key="keep-due-1", subject="Auftragsbestätigung", sender="shop@example.test",
            received_at="Fri, 24 Jul 2026 14:35:00 +0200", source_category="routine",
        )
        card_id = first["deck"]["card_id"]
        self.deck.cards[card_id]["duedate"] = "2026-09-01T12:00:00+02:00"
        self.service.process_event(
            self.event("shipping", expected_delivery="2026-08-12"),
            stable_key="keep-due-2", subject="Versandt", sender="shop@example.test",
            received_at="Sat, 25 Jul 2026 09:10:00 +0200", source_category="routine",
        )
        self.assertEqual(self.deck.cards[card_id]["duedate"], "2026-09-01T12:00:00+02:00")

    def test_implausible_existing_due_date_is_replaced(self):
        first = self.service.process_event(
            self.event("order_confirmation", expected_delivery="2026-08-10"),
            stable_key="replace-bad-due-1", subject="Auftragsbestätigung", sender="shop@example.test",
            received_at="Fri, 24 Jul 2026 14:35:00 +0200", source_category="routine",
        )
        card_id = first["deck"]["card_id"]
        self.deck.cards[card_id]["duedate"] = "2099-01-01T12:00:00+01:00"
        self.service.process_event(
            self.event("shipping", expected_delivery="2026-08-12"),
            stable_key="replace-bad-due-2", subject="Versandt", sender="shop@example.test",
            received_at="Sat, 25 Jul 2026 09:10:00 +0200", source_category="routine",
        )
        self.assertEqual(self.deck.cards[card_id]["duedate"], "2026-08-12T23:59:00+02:00")

    def test_due_date_backfill_updates_only_missing_managed_cards(self):
        first = self.service.process_event(
            self.event("order_confirmation", expected_delivery="2026-08-10"),
            stable_key="backfill-1", subject="Auftragsbestätigung", sender="shop@example.test",
            received_at="Fri, 24 Jul 2026 14:35:00 +0200", source_category="routine",
        )
        card_id = first["deck"]["card_id"]
        self.deck.cards[card_id]["duedate"] = None
        preview = self.service.backfill_missing_due_dates(limit=100, dry_run=True)
        self.assertTrue(preview["ok"])
        self.assertEqual(preview["would_update"], 1)
        self.assertIsNone(self.deck.cards[card_id]["duedate"])
        applied = self.service.backfill_missing_due_dates(limit=100, dry_run=False)
        self.assertTrue(applied["ok"])
        self.assertEqual(applied["updated"], 1)
        self.assertEqual(self.deck.cards[card_id]["duedate"], "2026-08-10T23:59:00+02:00")

    def test_due_date_backfill_preserves_existing_due(self):
        first = self.service.process_event(
            self.event("order_confirmation", expected_delivery="2026-08-10"),
            stable_key="backfill-keep", subject="Auftragsbestätigung", sender="shop@example.test",
            received_at="Fri, 24 Jul 2026 14:35:00 +0200", source_category="routine",
        )
        card_id = first["deck"]["card_id"]
        self.deck.cards[card_id]["duedate"] = "2026-09-01T12:00:00+02:00"
        applied = self.service.backfill_missing_due_dates(limit=100, dry_run=False)
        self.assertTrue(applied["ok"])
        self.assertEqual(applied["updated"], 0)
        self.assertEqual(applied["preserved_existing"], 1)
        self.assertEqual(self.deck.cards[card_id]["duedate"], "2026-09-01T12:00:00+02:00")

    def test_classifier_parses_order_signal(self):
        classification = OllamaClassifier._classification_from_data({
            "category": "routine", "confidence": 0.9, "importance": 2,
            "forward": False, "reason": "ok", "summary": "", "expected_action": "",
            "calendar_event": None,
            "invoice": {"is_invoice": False, "confidence": 0.0, "reason": "", "pdf_filenames": []},
            "order": {"is_order_event": True, "event_type": "shipping", "confidence": 0.93,
                      "merchant": "Shop", "order_number": "1", "ordered_at": "", "expected_delivery": "",
                      "carrier": "DHL", "tracking_numbers": ["123"], "items": ["Teil"], "amount": "",
                      "currency": "EUR", "return_deadline": "", "reason": "Versandmail"},
        }, source="test")
        self.assertIsNotNone(classification.order)
        self.assertTrue(classification.order.is_order_event)
        self.assertEqual(classification.order.event_type, "shipping")


class OrderStoreMigrationTests(unittest.TestCase):
    def test_existing_order_database_gets_mail_arrival_columns(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "orders.sqlite3"
            db = sqlite3.connect(path)
            db.executescript(
                """
                CREATE TABLE orders (
                  id INTEGER PRIMARY KEY AUTOINCREMENT, order_key TEXT NOT NULL UNIQUE,
                  merchant TEXT, order_number TEXT, status TEXT NOT NULL,
                  items_json TEXT NOT NULL DEFAULT '[]', amount TEXT, currency TEXT,
                  ordered_at TEXT, expected_delivery TEXT, carrier TEXT,
                  tracking_json TEXT NOT NULL DEFAULT '[]', return_deadline TEXT,
                  last_event_at TEXT, source_subject TEXT, source_sender TEXT,
                  deck_board_id INTEGER, deck_stack_id INTEGER, deck_card_id INTEGER,
                  sync_status TEXT NOT NULL DEFAULT 'pending', sync_error TEXT,
                  created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE TABLE order_events (
                  id INTEGER PRIMARY KEY AUTOINCREMENT, event_key TEXT NOT NULL UNIQUE,
                  order_id INTEGER NOT NULL, stable_key TEXT, event_type TEXT NOT NULL,
                  confidence REAL NOT NULL, payload_json TEXT NOT NULL,
                  source_subject TEXT, source_sender TEXT, created_at TEXT NOT NULL
                );
                """
            )
            db.commit()
            db.close()
            store = OrderStore(path)
            try:
                order_columns = {row[1] for row in store.db.execute("PRAGMA table_info(orders)")}
                event_columns = {row[1] for row in store.db.execute("PRAGMA table_info(order_events)")}
                self.assertTrue({
                    "first_mail_received_at", "last_mail_received_at", "created_from_category",
                    "deck_due_date", "deck_due_source", "deck_due_confidence",
                } <= order_columns)
                self.assertTrue({"received_at", "source_category"} <= event_columns)
                self.assertEqual(store.db.execute("PRAGMA quick_check").fetchone()[0], "ok")
            finally:
                store.close()


if __name__ == "__main__":
    unittest.main()
