from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from .command import CommandRunner
from .config import Config
from .himalaya import HimalayaClient
from .imap_inventory import native_backend
from .search_backfill import HimalayaBackfillBackend
from .search_reconcile import HimalayaReconcileBackend


@contextmanager
def backfill_backend(
    config: Config,
    runner: CommandRunner,
    *,
    total_timeout_seconds: float | None = None,
) -> Iterator[Any]:
    if config.mailbox.index_connector == "native-imap-readonly":
        with native_backend(
            config,
            total_timeout_seconds=total_timeout_seconds,
        ) as backend:
            yield backend
        return
    yield HimalayaBackfillBackend(HimalayaClient(config, runner, dry_run=True))


@contextmanager
def reconcile_backend(
    config: Config,
    runner: CommandRunner,
    *,
    total_timeout_seconds: float | None = None,
) -> Iterator[Any]:
    if config.mailbox.index_connector == "native-imap-readonly":
        with native_backend(
            config,
            total_timeout_seconds=total_timeout_seconds,
        ) as backend:
            yield backend
        return
    yield HimalayaReconcileBackend(
        HimalayaBackfillBackend(HimalayaClient(config, runner, dry_run=True))
    )
