"""Compatibility import for the mail infrastructure adapter.

Core application services depend on ``MailOperationsPort``. Direct imports of
this module are retained only for existing integrations and regression tests.
"""

from .adapters.mail import MailMoveService

__all__ = ["MailMoveService"]
