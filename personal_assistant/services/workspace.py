from __future__ import annotations

import hashlib
import mimetypes
import os
from dataclasses import asdict
from pathlib import Path
from typing import Any

from ..nextcloud_access import path_segments
from ..nextcloud_connection import admit_projection
from ..nextcloud_file_access import file_context


class WorkspaceServiceMixin:
    """Nextcloud file/workspace application service with its own policy boundary."""

    nextcloud_files: Any
    registry: Any
    config: Any
    tool_settings: Any
    actions: Any
    antivirus: Any

    def list_nextcloud_files(
        self,
        path: str | None = None,
        *,
        max_depth: int = 3,
        resource_id: str = "",
    ) -> dict[str, Any]:
        settings = getattr(self, "tool_settings", None)
        workspace = settings.nextcloud.workspace if settings else None
        resource_id = resource_id or (workspace.resource_id if workspace else "nextcloud-files-main")
        default_root = workspace.root if workspace else "Assistent"
        requested = self.nextcloud_files.clean_path(default_root if path is None else path)
        if max_depth < 0 or max_depth > 10:
            raise ValueError("max_depth muss zwischen 0 und 10 liegen")
        resource = self.registry.get(resource_id)
        account_root = resource.metadata.get("account_root") is True
        client = getattr(self.nextcloud_files, "client", None)
        publication = admit_projection(getattr(client, "access_snapshot", None))
        if getattr(self.nextcloud_files, "managed_access", False) is True:
            effective = resource.metadata.get("folder_operations", {})
            recheck = resource.metadata.get("folder_recheck_operations", {})
            entries = sorted(
                {root for by_path in (effective, recheck) for root, ops in by_path.items() if "list" in ops}
            )
            if path is None and len(entries) > 1:
                entry_points = []
                for root in entries:
                    self.nextcloud_files.verified_entry(root)
                    entry_points.append(
                        {"path": root, "name": root.rpartition("/")[2] or "/", "kind": "folder"}
                    )
                with publication:
                    return {
                        "ok": True,
                        "connector": "native-nextcloud-webdav",
                        "resource_id": resource_id,
                        "root": None,
                        "max_depth": 0,
                        "count": len(entry_points),
                        "complete": True,
                        "results_may_be_truncated": False,
                        "item_limit_reached": False,
                        "unexpanded_folder_count": 0,
                        "items": entry_points,
                        "scope": "selected-entry-points",
                    }
            requested = self._managed_path(path, entries, "list")
        else:
            if not requested and not account_root:
                raise ValueError("Nextcloud-Pfad darf nicht leer sein")
            if "read" not in resource.permissions:
                raise PermissionError("Nextcloud-Ressource besitzt kein Leserecht")
            roots = tuple(str(v).strip("/") for v in resource.metadata.get("allowed_roots", []))
            if not roots:
                roots = tuple(str(v).strip("/") for v in self.config.nextcloud.allowed_file_roots)
            if not account_root and not any(
                requested == root or requested.startswith(root + "/") for root in roots
            ):
                raise PermissionError("Pfad liegt ausserhalb der erlaubten Nextcloud-Wurzeln")

        queue: list[tuple[str, int]] = [(requested, 0)]
        seen: set[str] = set()
        items: list[dict[str, Any]] = []
        unexpanded_folders = 0
        item_limit = max(1, getattr(getattr(self.config, "search", None), "nextcloud_max_items", 2000))
        item_limit_reached = False
        while queue:
            folder, depth = queue.pop(0)
            if folder in seen:
                continue
            seen.add(folder)
            for entry in self.nextcloud_files.list_folder(folder):
                if len(items) >= item_limit:
                    item_limit_reached = True
                    break
                item = {
                    "path": entry.path,
                    "name": entry.name,
                    "kind": "folder" if entry.is_collection else "file",
                    "size": entry.size,
                    "mime_type": entry.content_type,
                    "modified_at": entry.modified_at,
                    "etag": entry.etag,
                }
                items.append(item)
                if entry.is_collection:
                    if depth < max_depth:
                        queue.append((entry.path, depth + 1))
                    else:
                        unexpanded_folders += 1
            if item_limit_reached:
                break
        with publication:
            return {
                "ok": True,
                "connector": "native-nextcloud-webdav",
                "resource_id": resource_id,
                "root": requested,
                "max_depth": max_depth,
                "count": len(items),
                "complete": unexpanded_folders == 0 and not item_limit_reached,
                "results_may_be_truncated": unexpanded_folders > 0 or item_limit_reached,
                "item_limit_reached": item_limit_reached,
                "unexpanded_folder_count": unexpanded_folders,
                "items": items,
            }

    def list_invoice_files(self, *, limit: int = 100) -> dict[str, Any]:
        """List the configured remote invoice archive through controlled WebDAV."""
        invoices = self.tool_settings.mail.invoices
        if not invoices.enabled:
            raise PermissionError("Rechnungswerkzeug ist deaktiviert")
        bounded_limit = max(1, min(int(limit), 500))
        listing = self.list_nextcloud_files(
            invoices.folder,
            max_depth=3,
            resource_id=invoices.resource_id,
        )
        items = sorted(
            (item for item in listing["items"] if item.get("kind") == "file"),
            key=lambda item: (str(item.get("path", "")).casefold(), str(item.get("path", ""))),
        )
        truncated = len(items) > bounded_limit
        return {
            "ok": True,
            "connector": "native-nextcloud-webdav",
            "resource_id": invoices.resource_id,
            "root": listing["root"],
            "max_depth": listing["max_depth"],
            "count": len(items),
            "returned": min(len(items), bounded_limit),
            "complete": not truncated,
            "results_may_be_truncated": truncated,
            "items": items[:bounded_limit],
        }

    def _workspace(self):
        workspace = self.tool_settings.nextcloud.workspace
        if not workspace.enabled:
            raise PermissionError("Nextcloud-Arbeitsbereich ist deaktiviert")
        return workspace

    def _managed_path(self, value, roots, operation):
        if not roots:
            raise PermissionError("scope-off")
        if value is None:
            if len(roots) != 1:
                raise PermissionError("scope-invalid")
            value = roots[0]
        parts = path_segments(value)
        if not any(parts[: len(path_segments(root))] == path_segments(root) for root in roots):
            if len(roots) != 1 or not value:
                raise PermissionError("scope-invalid")
            value = roots[0] + "/" + value
        file_context(self.nextcloud_files.client.access_snapshot, value, operation)
        return value

    def _workspace_path(self, value: str, operation: str = "read") -> str:
        workspace = self._workspace()
        if getattr(self.nextcloud_files, "managed_access", False) is True:
            roots = self.registry.get(workspace.resource_id).metadata.get("allowed_roots", [])
            return self._managed_path(value, roots, operation)
        path = self.nextcloud_files.clean_path(value)
        root = self.nextcloud_files.clean_path(workspace.root)
        if not root and path:
            account_root = self.registry.get(workspace.resource_id).metadata.get("account_root") is True
            if account_root:
                return path
        if not path or not (path == root or path.startswith(root + "/")):
            raise PermissionError(f"Pfad liegt ausserhalb des Nextcloud-Arbeitsbereichs {root}/")
        return path

    def _execute_workspace_plan(self, plan) -> dict[str, Any]:
        if plan.status not in {"approved", "completed"}:
            return {"ok": False, "action": asdict(plan), "detail": "ActionPlan benoetigt Freigabe"}
        result, duplicate = self.actions.execute_workspace(plan.id, recreate_missing=True)
        response = {"ok": result.status == "completed", "duplicate": duplicate, "action": asdict(result)}
        if result.status == "failed" and result.error:
            response["detail"] = result.error
        return response

    def workspace_mkdir(self, path: str) -> dict[str, Any]:
        workspace = self._workspace()
        if not workspace.allow_mkdir:
            raise PermissionError("Ordner anlegen ist fuer den Nextcloud-Arbeitsbereich deaktiviert")
        remote = self._workspace_path(path, "mkdir")
        plan = self.actions.plan(
            "files.mkdir",
            workspace.resource_id,
            {"path": remote, "overwrite": False},
            idempotency_key=f"workspace-mkdir:{workspace.resource_id}:{remote}",
        )
        return self._execute_workspace_plan(plan)

    def workspace_upload(
        self, local_path: str | Path, remote_path: str, *, content_type: str = ""
    ) -> dict[str, Any]:
        workspace = self._workspace()
        if not workspace.allow_upload:
            raise PermissionError("Datei-Upload ist fuer den Nextcloud-Arbeitsbereich deaktiviert")
        local = Path(local_path).expanduser().resolve()
        outbox = workspace.outbox.expanduser().resolve()
        try:
            local.relative_to(outbox)
        except ValueError as exc:
            raise PermissionError(
                f"Lokale Datei muss innerhalb der kontrollierten Outbox liegen: {outbox}"
            ) from exc
        if not local.is_file():
            raise FileNotFoundError(local)
        size = local.stat().st_size
        if size > self.config.search.max_file_bytes:
            raise ValueError(f"Datei ist groesser als das konfigurierte Limit: {size} Byte")
        remote = self._workspace_path(remote_path, "create_file")
        payload = local.read_bytes()
        antivirus = getattr(self, "antivirus", None)
        if antivirus is not None:
            scan = antivirus.scan_bytes(payload, name=local.name, source_type="workspace-upload")
            if not scan.clean:
                raise PermissionError(
                    "Upload durch Virenscanner blockiert: " + (scan.signature or scan.detail or scan.status)
                )
        digest = hashlib.sha256(payload).hexdigest()
        mime = content_type or mimetypes.guess_type(local.name)[0] or "application/octet-stream"
        plan = self.actions.plan(
            "files.create",
            workspace.resource_id,
            {
                "local_path": str(local),
                "path": remote,
                "content_type": mime,
                "overwrite": False,
                "sha256": digest,
                "workspace_tool": True,
            },
            idempotency_key=f"workspace-upload:{workspace.resource_id}:{remote}:{digest}",
        )
        return self._execute_workspace_plan(plan)

    def workspace_write_text(
        self, remote_path: str, text: str, *, content_type: str = "text/plain; charset=utf-8"
    ) -> dict[str, Any]:
        workspace = self._workspace()
        if not workspace.allow_write_text:
            raise PermissionError("Textdateien anlegen ist fuer den Nextcloud-Arbeitsbereich deaktiviert")
        data = text.encode("utf-8")
        if len(data) > self.config.search.max_file_bytes:
            raise ValueError("Text ist groesser als das konfigurierte Dateilimit")
        remote = self._workspace_path(remote_path, "create_file")
        digest = hashlib.sha256(data).hexdigest()
        outbox = workspace.outbox.expanduser().resolve()
        generated = outbox / ".generated"
        generated.mkdir(parents=True, exist_ok=True)
        os.chmod(outbox, 0o700)
        os.chmod(generated, 0o700)
        staging = generated / f"{digest}.txt"
        if not staging.exists():
            tmp = generated / f".{digest}.tmp"
            tmp.write_bytes(data)
            os.chmod(tmp, 0o600)
            os.replace(tmp, staging)
        plan = self.actions.plan(
            "files.create",
            workspace.resource_id,
            {
                "local_path": str(staging),
                "path": remote,
                "content_type": content_type,
                "overwrite": False,
                "sha256": digest,
                "workspace_tool": True,
            },
            idempotency_key=f"workspace-text:{workspace.resource_id}:{remote}:{digest}",
        )
        result = self._execute_workspace_plan(plan)
        if result.get("ok") and staging.exists():
            staging.unlink()
        return result

    def workspace_move(self, source: str, destination: str) -> dict[str, Any]:
        workspace = self._workspace()
        if not workspace.allow_move:
            raise PermissionError("Verschieben/Umbenennen ist fuer den Nextcloud-Arbeitsbereich deaktiviert")
        source_path = self._workspace_path(source)
        destination_path = self._workspace_path(destination)
        plan = self.actions.plan(
            "files.move",
            workspace.resource_id,
            {"source": source_path, "destination": destination_path, "overwrite": False},
            idempotency_key=f"workspace-move:{workspace.resource_id}:{source_path}:{destination_path}",
        )
        return self._execute_workspace_plan(plan)
