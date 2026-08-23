from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..mail_hybrid_search import MailHybridSearch, _configured_model
from ..mail_search import MailSearchFilters


class MailApplicationMixin:
    mail_move_service: Any
    storage: Any
    config: Any

    def mail_move_status(self) -> dict[str, Any]:
        return self.mail_move_service.status()

    def mail_list_messages(self, folder: str, *, limit: int = 50) -> dict[str, Any]:
        return self.mail_move_service.list_messages(folder, limit=limit)

    def mail_search_messages(
        self,
        query: str,
        *,
        limit: int = 50,
        filters: MailSearchFilters | None = None,
        mode: str = "auto",
        context_limit: int = 0,
    ) -> dict[str, Any]:
        return MailHybridSearch(
            self.storage,
            self.mail_move_service,
            self.config.search,
        ).search(
            query,
            limit=limit,
            filters=filters,
            mode=mode,
            context_limit=context_limit,
        )

    def mail_index_status(self) -> dict[str, Any]:
        model, configuration = _configured_model(self.config.search)
        result = self.storage.mail_index_status(
            max_age_seconds=self.config.search.mail_projection_max_age_seconds,
            semantic_model=model,
        )
        result["semantic_configuration"] = configuration
        result["reconciliation"] = self._reconcile_runtime_status()
        return result

    def mail_index_doctor(self) -> dict[str, Any]:
        model, configuration = _configured_model(self.config.search)
        result = self.storage.mail_index_doctor(
            max_age_seconds=self.config.search.mail_projection_max_age_seconds,
            semantic_model=model,
        )
        result["semantic_configuration"] = configuration
        result["reconciliation"] = self._reconcile_runtime_status()
        return result

    def mail_index_shadow(self, query: str, *, limit: int = 50) -> dict[str, Any]:
        search = MailHybridSearch(
            self.storage,
            self.mail_move_service,
            self.config.search,
        )
        local = search.search(query, limit=limit, mode="local")
        server = search.search(query, limit=limit, mode="server")
        comparable = bool(local.get("complete") and server.get("complete"))
        local_count = int(local.get("count") or 0)
        server_count = int(server.get("count") or 0)
        return {
            "ok": bool(local.get("ok") and server.get("ok")),
            "read_only": True,
            "query": {"stored": False, "logged": False},
            "comparable": comparable,
            "server_is_ground_truth": comparable,
            "local": {
                "decision": str(local.get("decision") or "inconclusive"),
                "complete": bool(local.get("complete")),
                "count": local_count,
                "results_may_be_truncated": bool(local.get("results_may_be_truncated")),
            },
            "server": {
                "decision": str(server.get("decision") or "inconclusive"),
                "complete": bool(server.get("complete")),
                "count": server_count,
                "folder_error_count": len(server.get("folder_errors") or []),
                "filter_limitations": list(server.get("filter_limitations") or []),
                "results_may_be_truncated": bool(server.get("results_may_be_truncated")),
            },
            "difference": {
                "count_delta": local_count - server_count if comparable else None,
                "classification": (
                    "equal-count"
                    if comparable and local_count == server_count
                    else "different-count" if comparable else "inconclusive"
                ),
            },
        }

    def mail_read_message(
        self, folder: str, message_id: str, *, expected_subject: str = ""
    ) -> dict[str, Any]:
        return self.mail_move_service.read(folder, message_id, expected_subject=expected_subject)

    def mail_draft_reply(
        self, folder: str, message_id: str, body: str, *, expected_subject: str = ""
    ) -> dict[str, Any]:
        return self.mail_move_service.draft_reply(folder, message_id, body, expected_subject=expected_subject)

    def mail_send_reply(self, draft_id: str, *, approved: bool = False) -> dict[str, Any]:
        return self.mail_move_service.send_reply(draft_id, approved=approved)

    def mail_draft_message(self, recipient: str, subject: str, body: str) -> dict[str, Any]:
        return self.mail_move_service.draft_message(recipient, subject, body)

    def mail_send_message(self, draft_id: str, *, approved: bool = False) -> dict[str, Any]:
        return self.mail_move_service.send_message(draft_id, approved=approved)

    def mail_move_message(
        self,
        *,
        source: str,
        destination: str,
        message_id: str,
        expected_subject: str = "",
        dry_run: bool = False,
    ) -> dict[str, Any]:
        return self.mail_move_service.move(
            source=source,
            destination=destination,
            message_id=message_id,
            expected_subject=expected_subject,
            dry_run=dry_run,
        )

    def mail_correct_review(
        self,
        *,
        source: str,
        message_id: str,
        expected_subject: str,
        verdict: str,
        label: str = "",
        approved: bool = False,
    ) -> dict[str, Any]:
        return self.mail_move_service.review_correct(
            source=source,
            message_id=message_id,
            expected_subject=expected_subject,
            verdict=verdict,
            label=label,
            approved=approved,
        )
    def _reconcile_runtime_status(self) -> dict[str, Any]:
        projection = Path(self.config.search.mail_snapshot_dir)
        state_path = projection.parent.parent / "search_reconcile_v3" / "state.json"
        try:
            payload = json.loads(state_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {
                "state": "missing",
                "last_complete_generation": "",
                "folder_cursor_count": 0,
                "metrics": {},
            }
        except (OSError, UnicodeError, json.JSONDecodeError):
            return {
                "state": "corrupt",
                "last_complete_generation": "",
                "folder_cursor_count": 0,
                "metrics": {},
            }
        cursors = payload.get("folder_cursors")
        metrics = payload.get("metrics")
        return {
            "state": "ready",
            "updated_at": str(payload.get("updated_at") or ""),
            "last_complete_generation": str(payload.get("root_generation") or ""),
            "folder_identity_assurance": str(
                payload.get("folder_identity_assurance") or "unknown"
            ),
            "folder_cursor_count": len(cursors) if isinstance(cursors, dict) else 0,
            "metrics": dict(metrics) if isinstance(metrics, dict) else {},
        }
