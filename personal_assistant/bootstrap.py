from __future__ import annotations

from .adapters.mail import MailMoveService
from .config import AssistantConfig
from .service import PersonalAssistant


def create_personal_assistant(config: AssistantConfig) -> PersonalAssistant:
    """Composition root for concrete infrastructure adapters."""
    return PersonalAssistant(config, mail_operations_factory=MailMoveService)
