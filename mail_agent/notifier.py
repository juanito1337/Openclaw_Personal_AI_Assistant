from __future__ import annotations

import os

from .command import CommandRunner
from .config import Config
from .models import OperationResult


class Notifier:
    def __init__(self, config: Config, runner: CommandRunner, dry_run: bool = False) -> None:
        self.config = config
        self.runner = runner
        self.dry_run = dry_run

    def critical(self, text: str) -> OperationResult:
        settings = self.config.notifications
        if not settings.signal_enabled or not settings.signal_recipient:
            return OperationResult(True, "notification-disabled")
        if self.dry_run:
            return OperationResult(True, "would-notify")
        env = os.environ.copy()
        result = self.runner.run(
            [str(settings.signal_script), settings.signal_recipient, text[:1500]],
            env=env,
        )
        return OperationResult(result.ok, "notified" if result.ok else "notification-failed", result.combined)
