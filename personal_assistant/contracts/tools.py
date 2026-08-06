from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from typing import Any, Literal, cast

ToolMode = Literal["read", "local-write", "write"]
TOOL_MODES: frozenset[str] = frozenset({"read", "local-write", "write"})
_PLACEHOLDER = re.compile(r"<([^>]+)>")


@dataclass(frozen=True, slots=True)
class AgentTool:
    """Backwards-compatible live tool projection used by ``tools list``."""

    id: str
    description: str
    command: str
    mode: ToolMode
    writes_external_data: bool = False
    approval: str = "none"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class ToolDefinition:
    """Static and typed source contract for one agent-facing tool."""

    id: str
    domain: str
    description: str
    command: str
    handler: str
    mode: ToolMode
    writes_external_data: bool
    approval: str
    argument_schema: Mapping[str, Any]
    output_schema: Mapping[str, Any]
    documentation_anchor: str
    test_anchor: str
    error_codes: tuple[str, ...]
    availability: str = "always"

    def __post_init__(self) -> None:
        if self.mode not in TOOL_MODES:
            raise ValueError(f"Ungueltiger Toolmodus fuer {self.id}: {self.mode}")
        if self.mode == "write" and not self.writes_external_data:
            raise ValueError(f"Externes Schreibtool ohne Wirkungsmarkierung: {self.id}")
        if self.mode != "write" and self.writes_external_data:
            raise ValueError(f"Nicht-schreibendes Tool mit externer Wirkung: {self.id}")
        for value, label in (
            (self.id, "id"),
            (self.handler, "handler"),
            (self.approval, "approval"),
            (self.documentation_anchor, "documentation_anchor"),
            (self.test_anchor, "test_anchor"),
        ):
            if not value.strip():
                raise ValueError(f"Leeres {label} im Toolvertrag {self.id}")

    def live(self, command: str | None = None) -> AgentTool:
        return AgentTool(
            id=self.id,
            description=self.description,
            command=command or self.command,
            mode=self.mode,
            writes_external_data=self.writes_external_data,
            approval=self.approval,
        )

    def catalog_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["argument_schema"] = dict(self.argument_schema)
        result["output_schema"] = dict(self.output_schema)
        return result


def _argument_schema(command: str) -> dict[str, Any]:
    names: list[str] = []
    for raw in _PLACEHOLDER.findall(command):
        name = re.sub(r"[^a-z0-9]+", "_", raw.casefold()).strip("_") or "value"
        if name not in names:
            names.append(name)
    properties = {name: {"type": "string"} for name in names}
    return {
        "type": "object",
        "properties": properties,
        "required": names,
        "additionalProperties": True,
    }


def define(
    *,
    id: str,
    domain: str,
    description: str,
    command: str,
    mode: str,
    writes_external_data: bool = False,
    approval: str = "none",
    availability: str = "always",
    documentation_anchor: str,
    test_anchor: str,
) -> ToolDefinition:
    errors = ["configuration-error", "operation-failed"]
    if mode in {"local-write", "write"}:
        errors.insert(1, "permission-denied")
    return ToolDefinition(
        id=id,
        domain=domain,
        description=description,
        command=command,
        handler=f"personal_assistant.cli_handlers.{domain}:handle",
        mode=cast(ToolMode, mode),
        writes_external_data=writes_external_data,
        approval=approval,
        argument_schema=_argument_schema(command),
        output_schema={"type": ["object", "array"], "description": "JSON CLI response"},
        documentation_anchor=documentation_anchor,
        test_anchor=test_anchor,
        error_codes=tuple(errors),
        availability=availability,
    )
