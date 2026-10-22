"""Exact resource routing for an operator-connected Nextcloud account."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from typing import Any


class NextcloudAccountMixin:
    tool_settings: Any
    registry: Any
    nextcloud_client: Any

    def _verify_nextcloud_connection_snapshot(self) -> None:
        from ..nextcloud_connection import load_connection

        current_revision = str((load_connection() or {}).get("revision", ""))
        client_revision = str((self.nextcloud_client.connection or {}).get("revision", ""))
        if current_revision != client_revision or current_revision != self.registry.connection_revision:
            raise RuntimeError("nextcloud-connection-changed: Status und Auswahl erneut pruefen")

    def _require_legacy_nextcloud_configuration(self) -> None:
        if getattr(self.registry, "managed_connection", False):
            raise PermissionError("nextcloud-managed-configuration: OpenClaw-Einrichtung verwenden")

    @contextmanager
    def select_nextcloud_resource(self, domain: str, resource_id: str = "") -> Iterator[None]:
        if not resource_id:
            yield
            return
        settings = getattr(self.tool_settings.nextcloud, domain)
        allowed = settings.resource_ids or (settings.resource_id,)
        if resource_id not in allowed:
            raise PermissionError("nextcloud-resource-not-authorized")
        resource = self.registry.get(resource_id)
        if not resource.enabled:
            raise PermissionError("nextcloud-resource-disabled")
        component = {"calendar": "VEVENT", "tasks": "VTODO", "contacts": "VCARD"}[domain]
        expected_kind = "addressbook" if domain == "contacts" else "calendar"
        components = resource.metadata.get("components", [])
        if resource.kind != expected_kind or (components and component not in components):
            raise PermissionError("nextcloud-resource-component-mismatch")
        previous = self.tool_settings
        selected = replace(
            settings,
            resource_id=resource_id,
            resource_ids=(),
            allow_list="read" in resource.permissions,
            allow_create="create" in resource.permissions,
            allow_update="update" in resource.permissions,
        )
        self.tool_settings = replace(previous, nextcloud=replace(previous.nextcloud, **{domain: selected}))
        try:
            yield
        finally:
            self.tool_settings = previous

    def _multiple_nextcloud_resources(self, domain: str) -> bool:
        settings = getattr(self.tool_settings.nextcloud, domain)
        return bool(settings.resource_ids and not settings.resource_id)

    def require_nextcloud_target(self, domain: str) -> None:
        if self._multiple_nextcloud_resources(domain):
            raise ValueError("nextcloud-resource-selection-required: --resource aus status/list angeben")

    def _account_domain_status(self, domain: str, *, live: bool = True) -> dict[str, Any]:
        settings = getattr(self.tool_settings.nextcloud, domain)
        method = getattr(self, f"direct_{domain}_status")
        results = []
        for resource_id in settings.resource_ids:
            try:
                with self.select_nextcloud_resource(domain, resource_id):
                    result = method() if domain == "calendar" else method(live=live)
                    results.append(result)
            except Exception as exc:
                results.append({"ok": False, "resource_id": resource_id, "code": type(exc).__name__})
        complete = all(result.get("ok") is True for result in results)
        return {
            "ok": complete,
            "complete": complete,
            "enabled": settings.enabled,
            "resource_id": "",
            "resource_ids": list(settings.resource_ids),
            "resources": results,
            "explicit_write_target_required": True,
            "update_allowed": any(
                result.get("ok") is True and result.get("update_allowed") is True for result in results
            ),
            "delete_allowed": False,
        }

    def _account_domain_list(
        self, domain: str, *, limit: int, query: str | None = None, **kwargs: Any
    ) -> dict[str, Any]:
        settings = getattr(self.tool_settings.nextcloud, domain)
        method_name = {"calendar": "calendar_list", "tasks": "tasks_list", "contacts": "contacts_list"}[
            domain
        ]
        if query is not None:
            if not str(query).strip():
                raise ValueError("Nextcloud-Suchbegriff fehlt")
            method_name = {"calendar": "calendar_search", "contacts": "contacts_search"}[domain]
            kwargs["query"] = query
        items_key = {"calendar": "events", "tasks": "tasks", "contacts": "contacts"}[domain]
        resource_key = {
            "calendar": "calendar_resource_id",
            "tasks": "task_resource_id",
            "contacts": "resource_id",
        }[domain]
        bounded = max(1, min(int(limit), 500))
        rows: list[dict[str, Any]] = []
        errors: list[dict[str, str]] = []
        complete = True
        for resource_id in settings.resource_ids:
            try:
                with self.select_nextcloud_resource(domain, resource_id):
                    result = getattr(self, method_name)(limit=bounded, **kwargs)
                rows.extend({**item, "resource_id": resource_id} for item in result[items_key])
                complete = (
                    complete
                    and result.get("ok") is True
                    and result.get("complete", len(result[items_key]) < bounded)
                )
                if result.get("ok") is not True:
                    errors.append({"resource_id": resource_id, "code": "resource-read-incomplete"})
            except Exception as exc:
                # Never echo remote response bodies into a cross-account error.
                errors.append({"resource_id": resource_id, "code": type(exc).__name__})
                complete = False
        if domain == "calendar":
            rows.sort(key=lambda item: (str(item.get("start", "")), str(item.get("uid", ""))))
        truncated = len(rows) > bounded or not complete
        return {
            "ok": not errors,
            resource_key: "",
            "resource_ids": list(settings.resource_ids),
            "count": min(len(rows), bounded),
            items_key: rows[:bounded],
            "complete": not truncated,
            "results_may_be_truncated": truncated,
            "resource_errors": errors,
            **({"query": query} if query is not None else {}),
        }
