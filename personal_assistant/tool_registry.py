from __future__ import annotations

from typing import Any

from .contracts.tools import AgentTool, ToolDefinition
from .tool_catalog import TOOLS
from .tool_settings import ToolSettings

CATALOG_SCHEMA_VERSION = 1


def tool_definitions() -> tuple[ToolDefinition, ...]:
    """Return the configuration-free, immutable tool source contract."""
    return TOOLS


def build_tool_registry(settings: ToolSettings) -> list[AgentTool]:
    """Project the static catalog onto the configured live tool permissions."""
    return [
        definition.live(_rendered_command(definition, settings))
        for definition in TOOLS
        if _enabled(definition, settings)
    ]


def _enabled(definition: ToolDefinition, settings: ToolSettings) -> bool:
    checks = {
        "always": True,
        "mail-move": settings.mail.move.enabled,
        "workspace": settings.nextcloud.workspace.enabled,
        "workspace-mkdir": settings.nextcloud.workspace.enabled and settings.nextcloud.workspace.allow_mkdir,
        "workspace-write-text": settings.nextcloud.workspace.enabled
        and settings.nextcloud.workspace.allow_write_text,
        "workspace-upload": settings.nextcloud.workspace.enabled
        and settings.nextcloud.workspace.allow_upload,
        "workspace-move": settings.nextcloud.workspace.enabled and settings.nextcloud.workspace.allow_move,
        "calendar": settings.nextcloud.calendar.enabled,
        "calendar-list": settings.nextcloud.calendar.enabled and settings.nextcloud.calendar.allow_list,
        "calendar-create": settings.nextcloud.calendar.enabled and settings.nextcloud.calendar.allow_create,
        "calendar-update": settings.nextcloud.calendar.enabled and settings.nextcloud.calendar.allow_update,
        "tasks": settings.nextcloud.tasks.enabled,
        "tasks-list": settings.nextcloud.tasks.enabled and settings.nextcloud.tasks.allow_list,
        "tasks-create": settings.nextcloud.tasks.enabled and settings.nextcloud.tasks.allow_create,
        "tasks-update": settings.nextcloud.tasks.enabled and settings.nextcloud.tasks.allow_update,
        "contacts": settings.nextcloud.contacts.enabled,
        "contacts-list": settings.nextcloud.contacts.enabled and settings.nextcloud.contacts.allow_list,
        "contacts-create": settings.nextcloud.contacts.enabled and settings.nextcloud.contacts.allow_create,
        "contacts-update": settings.nextcloud.contacts.enabled and settings.nextcloud.contacts.allow_update,
        "orders": settings.nextcloud.deck_orders.enabled,
        "invoices": settings.mail.invoices.enabled,
        "calendar-mail": settings.mail.calendar_mail.enabled,
    }
    try:
        return checks[definition.availability]
    except KeyError as exc:
        raise ValueError(
            f"Unbekannte Verfuegbarkeitsregel fuer {definition.id}: {definition.availability}"
        ) from exc


def _rendered_command(definition: ToolDefinition, settings: ToolSettings) -> str:
    return definition.command.replace("{workspace_root}", settings.nextcloud.workspace.root).replace(
        "{calendar_subject_prefix}", settings.mail.calendar_mail.subject_prefix
    )


def static_tool_catalog() -> dict[str, Any]:
    """Return all known tools without reading config, secrets or live resources."""
    return {
        "schema_version": CATALOG_SCHEMA_VERSION,
        "view": "static-catalog",
        "configured": False,
        "authoritative_for_permissions": False,
        "tools": [definition.catalog_dict() for definition in TOOLS],
    }


def capability_schema() -> dict[str, Any]:
    """Describe the live capabilities response without opening runtime state."""
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "https://openclaw.local/schemas/live-capabilities-v1.json",
        "title": "OpenClaw live capabilities",
        "description": "Configured instance view; use tools list --catalog for all known tools.",
        "type": "object",
        "required": [
            "view",
            "configured",
            "operations_profile",
            "resources",
            "hard_denied",
            "safe_settings",
            "self_management",
            "tools",
            "principles",
        ],
        "properties": {
            "view": {"const": "live-capabilities"},
            "configured": {"const": True},
            "operations_profile": {
                "type": "object",
                "required": ["name", "automatic_at_process_start"],
                "properties": {
                    "name": {"enum": ["standard", "restricted"]},
                    "automatic_at_process_start": {"type": "boolean"},
                    "resource_selection_unchanged": {"const": True},
                    "server_permissions_unchanged": {"const": True},
                    "concrete_write_approval_still_required": {"const": True},
                },
                "additionalProperties": True,
            },
            "resources": {"type": "array"},
            "hard_denied": {"type": "array", "items": {"type": "string"}},
            "safe_settings": {"type": ["object", "array"]},
            "self_management": {"type": "object"},
            "tools": {"type": "array"},
            "principles": {"type": "array", "items": {"type": "string"}},
        },
        "additionalProperties": True,
    }


__all__ = [
    "AgentTool",
    "build_tool_registry",
    "capability_schema",
    "static_tool_catalog",
    "tool_definitions",
]
