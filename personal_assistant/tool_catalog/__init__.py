from __future__ import annotations

from personal_assistant.contracts.tools import ToolDefinition

from ._order import TOOL_ORDER
from .calendar import TOOLS as CALENDAR_TOOLS
from .contacts import TOOLS as CONTACT_TOOLS
from .invoices import TOOLS as INVOICE_TOOLS
from .mail import TOOLS as MAIL_TOOLS
from .nextcloud import TOOLS as NEXTCLOUD_TOOLS
from .orders import TOOLS as ORDER_TOOLS
from .portfolio import TOOLS as PORTFOLIO_TOOLS
from .runtime import TOOLS as RUNTIME_TOOLS
from .security import TOOLS as SECURITY_TOOLS
from .tasks import TOOLS as TASK_TOOLS

DOMAIN_TOOLS: tuple[tuple[ToolDefinition, ...], ...] = (
    RUNTIME_TOOLS,
    PORTFOLIO_TOOLS,
    SECURITY_TOOLS,
    NEXTCLOUD_TOOLS,
    MAIL_TOOLS,
    CONTACT_TOOLS,
    CALENDAR_TOOLS,
    TASK_TOOLS,
    ORDER_TOOLS,
    INVOICE_TOOLS,
)
_ORDER_INDEX = {tool_id: index for index, tool_id in enumerate(TOOL_ORDER)}
TOOLS: tuple[ToolDefinition, ...] = tuple(
    sorted(
        (tool for domain in DOMAIN_TOOLS for tool in domain),
        key=lambda tool: _ORDER_INDEX[tool.id],
    )
)

__all__ = ["DOMAIN_TOOLS", "TOOLS"]
