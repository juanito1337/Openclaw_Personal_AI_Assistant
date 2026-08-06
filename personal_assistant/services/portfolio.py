from __future__ import annotations

import os
import re
import tempfile
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from personal_assistant.portfolio import MAX_IMPORT_BYTES


class PortfolioApplicationMixin:
    portfolio: Any
    nextcloud_files: Any
    tool_settings: Any
    policy: Any
    storage: Any

    if TYPE_CHECKING:

        def _workspace(self) -> Any: ...

    def portfolio_import_csv(
        self,
        *,
        local_file: str = "",
        nextcloud_path: str = "",
        dry_run: bool = True,
    ) -> dict[str, Any]:
        if bool(local_file) == bool(nextcloud_path):
            raise ValueError("Genau eine CSV-Quelle angeben: --file oder --nextcloud-path")
        if local_file:
            return self.portfolio.import_csv(local_file, dry_run=dry_run)
        if not self.portfolio.settings.enabled:
            raise PermissionError("Portfolio-Werkzeug ist in tools.toml nicht aktiviert")

        remote = self.nextcloud_files.clean_path(nextcloud_path)
        configured_root = self.nextcloud_files.clean_path(self.tool_settings.portfolio.nextcloud_folder)
        if not remote.startswith(configured_root + "/") or not remote.casefold().endswith(".csv"):
            raise PermissionError(f"Portfolio-CSV muss direkt unter {configured_root}/ liegen")
        relative = remote[len(configured_root) + 1 :]
        if not relative or "/" in relative:
            raise PermissionError(f"Portfolio-CSV muss direkt unter {configured_root}/ liegen")
        filename_match = re.search(r"(\d{2})\.(\d{2})\.(\d{4})", relative)
        if not filename_match:
            raise ValueError("Nextcloud-Portfolio-CSV benoetigt ein Datum DD.MM.YYYY im Dateinamen")
        try:
            filename_date = datetime.strptime(filename_match.group(0), "%d.%m.%Y").date().isoformat()
        except ValueError as exc:
            raise ValueError("Nextcloud-Portfolio-CSV enthaelt ein ungueltiges Dateidatum") from exc

        workspace = self._workspace()
        decision = self.policy.decide(
            workspace.resource_id,
            "files.read",
            {"path": remote, "portfolio_csv": True},
        )
        if not decision.allowed:
            raise PermissionError(decision.reason)
        entries = [
            item
            for item in self.nextcloud_files.list_folder(configured_root)
            if item.path == remote and not item.is_collection
        ]
        if len(entries) != 1:
            raise FileNotFoundError(f"Nextcloud-Portfolio-CSV nicht eindeutig gefunden: {remote}")
        entry = entries[0]
        if not entry.etag:
            raise ValueError("Nextcloud-Portfolio-CSV besitzt keinen pruefbaren ETag")
        if entry.size <= 0 or entry.size > MAX_IMPORT_BYTES:
            raise ValueError("Nextcloud-Portfolio-CSV ist leer oder groesser als 25 MB")
        payload = self.nextcloud_files.download(remote, expected_etag=entry.etag)
        if not payload or len(payload) > MAX_IMPORT_BYTES:
            raise ValueError("Nextcloud-Portfolio-CSV ist leer oder groesser als 25 MB")

        import_root = self.tool_settings.portfolio.import_root.expanduser().resolve()
        staging_root = import_root / ".nextcloud-staging"
        staging_root.mkdir(parents=True, exist_ok=True)
        os.chmod(import_root, 0o700)
        os.chmod(staging_root, 0o700)
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                prefix="portfolio-", suffix=".csv", dir=staging_root, delete=False
            ) as handle:
                handle.write(payload)
                temporary_path = Path(handle.name)
            os.chmod(temporary_path, 0o600)
            result = self.portfolio.import_csv(
                temporary_path,
                dry_run=dry_run,
                source_name=entry.name,
                expected_as_of=filename_date,
            )
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)
        self.storage.audit(
            "portfolio.csv.nextcloud_import",
            {
                "path": remote,
                "etag": entry.etag,
                "sha256": result.get("sha256"),
                "dry_run": dry_run,
                "as_of": result.get("as_of"),
            },
            resource_id=workspace.resource_id,
        )
        return {
            **result,
            "nextcloud_path": remote,
            "nextcloud_etag": entry.etag,
        }
