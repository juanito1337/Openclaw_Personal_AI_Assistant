from __future__ import annotations

import json
from typing import Any

from .client import NextcloudClient, NextcloudError


class NextcloudDeck:
    """Strict wrapper for the official Nextcloud Deck REST API."""

    API_ROOT = "/index.php/apps/deck/api/v1.1"

    def __init__(self, client: NextcloudClient) -> None:
        self.client = client

    def _request(
        self,
        method: str,
        path: str,
        *,
        payload: dict[str, Any] | None = None,
        expected: set[int] | None = None,
    ) -> Any:
        data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
        response = self.client.request(
            method,
            self.API_ROOT + "/" + path.lstrip("/"),
            data=data,
            headers={
                "OCS-APIRequest": "true",
                "Accept": "application/json",
                "Content-Type": "application/json; charset=utf-8",
            },
            expected=expected or {200},
        )
        if not response.data:
            return {}
        try:
            return json.loads(response.data.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise NextcloudError("Deck API lieferte kein gueltiges JSON") from exc

    def list_boards(self, *, details: bool = True) -> list[dict[str, Any]]:
        result = self._request("GET", f"boards?details={'true' if details else 'false'}")
        return [item for item in result if isinstance(item, dict)] if isinstance(result, list) else []

    def get_board(self, board_id: int) -> dict[str, Any]:
        result = self._request("GET", f"boards/{int(board_id)}")
        if not isinstance(result, dict):
            raise NextcloudError("Deck Board-Antwort hat ein unerwartetes Format")
        return result

    def create_board(self, title: str, color: str = "317CCC") -> dict[str, Any]:
        result = self._request("POST", "boards", payload={"title": title[:100], "color": color.lstrip("#")[:6]})
        if not isinstance(result, dict):
            raise NextcloudError("Deck Board konnte nicht angelegt werden")
        return result

    def list_stacks(self, board_id: int) -> list[dict[str, Any]]:
        result = self._request("GET", f"boards/{int(board_id)}/stacks")
        return [item for item in result if isinstance(item, dict)] if isinstance(result, list) else []

    def create_stack(self, board_id: int, title: str, order: int) -> dict[str, Any]:
        result = self._request(
            "POST",
            f"boards/{int(board_id)}/stacks",
            payload={"title": title[:100], "order": int(order)},
        )
        if not isinstance(result, dict):
            raise NextcloudError("Deck-Spalte konnte nicht angelegt werden")
        return result

    def get_card(self, board_id: int, stack_id: int, card_id: int) -> dict[str, Any]:
        result = self._request("GET", f"boards/{int(board_id)}/stacks/{int(stack_id)}/cards/{int(card_id)}")
        if not isinstance(result, dict):
            raise NextcloudError("Deck-Karte hat ein unerwartetes Format")
        return result

    def create_card(
        self,
        board_id: int,
        stack_id: int,
        *,
        title: str,
        description: str,
        duedate: str | None = None,
        order: int = 999,
    ) -> dict[str, Any]:
        result = self._request(
            "POST",
            f"boards/{int(board_id)}/stacks/{int(stack_id)}/cards",
            payload={
                "title": title[:255],
                "type": "plain",
                "order": int(order),
                "description": description,
                "duedate": duedate,
            },
        )
        if not isinstance(result, dict):
            raise NextcloudError("Deck-Karte konnte nicht angelegt werden")
        return result

    def update_card(
        self,
        board_id: int,
        stack_id: int,
        card_id: int,
        *,
        title: str,
        description: str,
        owner: str,
        order: int = 999,
        duedate: str | None = None,
        archived: bool = False,
        done: str | None = None,
    ) -> dict[str, Any]:
        result = self._request(
            "PUT",
            f"boards/{int(board_id)}/stacks/{int(stack_id)}/cards/{int(card_id)}",
            payload={
                "title": title[:255],
                "description": description,
                "type": "plain",
                "owner": owner,
                "order": int(order),
                "duedate": duedate,
                "archived": bool(archived),
                "done": done,
            },
        )
        return result if isinstance(result, dict) else {}

    def move_card(self, board_id: int, stack_id: int, card_id: int, destination_stack_id: int, *, order: int = 999) -> dict[str, Any]:
        result = self._request(
            "PUT",
            f"boards/{int(board_id)}/stacks/{int(stack_id)}/cards/{int(card_id)}/reorder",
            payload={"order": int(order), "stackId": int(destination_stack_id)},
        )
        return result if isinstance(result, dict) else {}
