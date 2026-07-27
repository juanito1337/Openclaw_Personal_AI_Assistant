from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True, frozen=True)
class Resource:
    id: str
    kind: str
    connector: str
    enabled: bool = True
    remote_id: str = ""
    permissions: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True, frozen=True)
class PolicyDecision:
    allowed: bool
    requires_approval: bool
    reason: str


@dataclass(slots=True, frozen=True)
class ActionPlan:
    id: str
    idempotency_key: str
    action_type: str
    resource_id: str
    payload: dict[str, Any]
    status: str
    requires_approval: bool
    created_at: str
    updated_at: str
    error: str = ""


@dataclass(slots=True, frozen=True)
class SearchResult:
    document_id: int
    source_type: str
    resource_id: str
    source_id: str
    title: str
    uri: str
    snippet: str
    score: float
    metadata: dict[str, Any] = field(default_factory=dict)
