from __future__ import annotations

import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


def _path(name: str, fallback: Path) -> Path:
    return Path(os.environ.get(name) or fallback).expanduser().resolve()


@dataclass(frozen=True, slots=True)
class RuntimeStatePaths:
    """Canonical persistent roots for container layout 3.

    Local development keeps the historical workspace defaults. Container roles
    receive every root explicitly from Compose, so an accidental fallback to the
    writable agent workspace remains visible in status and tests.
    """

    state: Path
    workspace: Path
    gateway: Path
    mail: Path
    core: Path
    knowledge: Path
    orders: Path
    portfolio: Path
    monitoring: Path
    security: Path
    coordination: Path

    @classmethod
    def from_environment(cls) -> RuntimeStatePaths:
        workspace = _path(
            "OPENCLAW_WORKSPACE",
            Path(__file__).resolve().parents[1],
        )
        state = _path("OPENCLAW_STATE_ROOT", workspace.parent)
        legacy_assistant = workspace / "personal_assistant/data"
        return cls(
            state=state,
            workspace=workspace,
            gateway=_path("OPENCLAW_GATEWAY_DATA_DIR", state),
            mail=_path("OPENCLAW_MAIL_DATA_DIR", workspace / "mail_agent/data"),
            core=_path("OPENCLAW_CORE_DATA_DIR", legacy_assistant),
            knowledge=_path("OPENCLAW_KNOWLEDGE_DATA_DIR", legacy_assistant),
            orders=_path("OPENCLAW_ORDERS_DATA_DIR", legacy_assistant),
            portfolio=_path("OPENCLAW_PORTFOLIO_DATA_DIR", legacy_assistant),
            monitoring=_path("OPENCLAW_MONITORING_DATA_DIR", legacy_assistant),
            security=_path("OPENCLAW_SECURITY_DATA_DIR", legacy_assistant),
            coordination=_path("OPENCLAW_COORDINATION_DATA_DIR", legacy_assistant),
        )

    def as_dict(self) -> dict[str, Any]:
        return {name: str(value) for name, value in asdict(self).items()}


def state_paths() -> RuntimeStatePaths:
    return RuntimeStatePaths.from_environment()
