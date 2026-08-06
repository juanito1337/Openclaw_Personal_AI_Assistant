from __future__ import annotations

from typing import Any

from personal_assistant.orders import STACKS


class OrderApplicationMixin:
    nextcloud_deck: Any
    order_service: Any

    def deck_discover(self) -> dict[str, Any]:
        return {"ok": True, "boards": self.nextcloud_deck.list_boards(details=True)}

    def deck_prepare_orders_board(
        self, *, board_id: int = 0, board_title: str = "Bestellungen", create_board: bool = False
    ) -> dict[str, Any]:
        boards = self.nextcloud_deck.list_boards(details=True)
        board = (
            next((item for item in boards if int(item.get("id") or 0) == int(board_id)), None)
            if board_id
            else None
        )
        if board is None:
            board = next(
                (
                    item
                    for item in boards
                    if str(item.get("title") or "").casefold() == board_title.casefold()
                ),
                None,
            )
        if board is None and create_board:
            board = self.nextcloud_deck.create_board(board_title)
        if board is None:
            raise ValueError("Deck-Board nicht gefunden; --board-id angeben oder --create-board verwenden")
        resolved_id = int(board["id"])
        stacks = self.nextcloud_deck.list_stacks(resolved_id)
        existing = {str(item.get("title") or "").casefold() for item in stacks}
        created = []
        for index, (_, title) in enumerate(STACKS, start=1):
            if title.casefold() not in existing:
                item = self.nextcloud_deck.create_stack(resolved_id, title, index * 1000)
                created.append({"id": item.get("id"), "title": title})
        return {
            "ok": True,
            "board_id": resolved_id,
            "board_title": str(board.get("title") or board_title),
            "created_stacks": created,
            "stacks": [title for _, title in STACKS],
        }

    def deck_orders_status(self, *, live: bool = True) -> dict[str, Any]:
        return self.order_service.status(live=live)

    def orders_list(self, *, status: str = "", limit: int = 100) -> dict[str, Any]:
        return self.order_service.list_orders(status=status, limit=limit)

    def orders_process_event(
        self,
        data: dict[str, Any],
        *,
        stable_key: str,
        subject: str = "",
        sender: str = "",
        received_at: str = "",
        source_category: str = "",
        dry_run: bool = False,
    ) -> dict[str, Any]:
        return self.order_service.process_event(
            data,
            stable_key=stable_key,
            subject=subject,
            sender=sender,
            received_at=received_at,
            source_category=source_category,
            dry_run=dry_run,
        )

    def orders_sync(self, *, limit: int = 500) -> dict[str, Any]:
        return self.order_service.sync_pending(limit=limit)

    def orders_due_date_backfill(self, *, limit: int = 500, dry_run: bool = True) -> dict[str, Any]:
        return self.order_service.backfill_missing_due_dates(limit=limit, dry_run=dry_run)
