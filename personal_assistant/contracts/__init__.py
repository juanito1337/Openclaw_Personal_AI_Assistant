"""Stable, infrastructure-neutral contracts shared by assistant domains."""

from .ports import MailMessagePort, MailOperationsPort
from .time import now_utc_iso
from .tools import AgentTool, ToolDefinition, ToolMode

__all__ = [
    "AgentTool",
    "MailMessagePort",
    "MailOperationsPort",
    "ToolDefinition",
    "ToolMode",
    "now_utc_iso",
]
