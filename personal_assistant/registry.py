from __future__ import annotations

import shutil
import tomllib
from datetime import datetime
from pathlib import Path
from typing import Any

from .models import Resource


class ResourceRegistry:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.resources: dict[str, Resource] = {}
        self.duplicate_ids: list[str] = []
        self.load()

    def load(self) -> None:
        self.resources.clear()
        self.duplicate_ids.clear()
        if not self.path.exists():
            return
        with self.path.open("rb") as handle:
            data = tomllib.load(handle)
        values = data.get("resources", [])
        if not isinstance(values, list):
            raise ValueError("resources.toml: [[resources]] fehlt oder ist ungueltig")
        for item in values:
            if not isinstance(item, dict):
                continue
            resource_id = str(item.get("id") or "").strip()
            if not resource_id:
                raise ValueError("resources.toml: resource.id fehlt")
            if resource_id in self.resources:
                # A generated duplicate must not brick the whole assistant.
                # Keep the last definition, expose the warning to doctor, and
                # let the standalone repair runbook canonicalize the file.
                self.duplicate_ids.append(resource_id)
            known = {"id", "kind", "connector", "enabled", "remote_id", "permissions"}
            metadata = {str(k): v for k, v in item.items() if k not in known}
            self.resources[resource_id] = Resource(
                id=resource_id,
                kind=str(item.get("kind") or "").strip(),
                connector=str(item.get("connector") or "").strip(),
                enabled=bool(item.get("enabled", True)),
                remote_id=str(item.get("remote_id") or "").strip(),
                permissions=tuple(str(v) for v in item.get("permissions", [])),
                metadata=metadata,
            )

    def list(self, *, kind: str = "", enabled_only: bool = False) -> list[Resource]:
        result = list(self.resources.values())
        if kind:
            result = [item for item in result if item.kind == kind]
        if enabled_only:
            result = [item for item in result if item.enabled]
        return sorted(result, key=lambda item: (item.kind, item.id))

    def get(self, resource_id: str) -> Resource:
        try:
            return self.resources[resource_id]
        except KeyError as exc:
            raise KeyError(f"Unbekannte Ressource: {resource_id}") from exc

    def has_permission(self, resource_id: str, permission: str) -> bool:
        resource = self.get(resource_id)
        return resource.enabled and permission in resource.permissions

    def write(self, resources: list[Resource]) -> Path | None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        backup: Path | None = None
        if self.path.exists():
            backup = self.path.with_name(
                self.path.name + ".backup-" + datetime.now().strftime("%Y%m%d-%H%M%S-%f")
            )
            shutil.copy2(self.path, backup)
        lines = [
            "# Resource registry for the Personal Assistant.",
            "# Secrets are referenced by environment variable names and never stored here.",
            "",
        ]
        for resource in sorted(resources, key=lambda item: (item.kind, item.id)):
            lines.extend([
                "[[resources]]",
                f'id = {self._quote(resource.id)}',
                f'kind = {self._quote(resource.kind)}',
                f'connector = {self._quote(resource.connector)}',
                f'enabled = {str(resource.enabled).lower()}',
                f'remote_id = {self._quote(resource.remote_id)}',
                "permissions = [" + ", ".join(self._quote(v) for v in resource.permissions) + "]",
            ])
            for key, value in sorted(resource.metadata.items()):
                if isinstance(value, bool):
                    lines.append(f"{key} = {str(value).lower()}")
                elif isinstance(value, int):
                    lines.append(f"{key} = {value}")
                elif isinstance(value, list):
                    lines.append(f"{key} = [" + ", ".join(self._quote(str(v)) for v in value) + "]")
                else:
                    lines.append(f"{key} = {self._quote(str(value))}")
            lines.append("")
        temp = self.path.with_suffix(self.path.suffix + ".tmp")
        temp.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
        temp.replace(self.path)
        self.load()
        return backup

    def upsert(self, resource: Resource) -> Path | None:
        values = list(self.resources.values())
        values = [item for item in values if item.id != resource.id]
        values.append(resource)
        return self.write(values)

    @staticmethod
    def _quote(value: str) -> str:
        escaped = value.replace("\\", "\\\\").replace('"', r'\"')
        return f'"{escaped}"'
