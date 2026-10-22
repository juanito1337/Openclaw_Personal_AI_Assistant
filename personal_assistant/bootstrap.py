from __future__ import annotations

from typing import TYPE_CHECKING

from .config import AssistantConfig

if TYPE_CHECKING:
    from .service import PersonalAssistant


def create_personal_assistant(config: AssistantConfig) -> PersonalAssistant:
    """Composition root for concrete infrastructure adapters."""
    # Static CLI introspection and external mail subprocess dispatch do not need
    # the full service graph or the mail adapter. Construct them only when used.
    from .adapters.mail import MailMoveService
    from .service import PersonalAssistant

    return PersonalAssistant(config, mail_operations_factory=MailMoveService)
