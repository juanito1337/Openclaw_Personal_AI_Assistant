from __future__ import annotations

import json
import os
import re
import shutil
import tomllib
from datetime import datetime
from pathlib import Path
from typing import Any

from .storage import AssistantStorage


SAFE_SETTINGS: dict[str, tuple[str, str, type, Any, Any]] = {
    "search.default_limit": ("search", "default_limit", int, 1, 200),
    "search.nextcloud_max_depth": ("search", "nextcloud_max_depth", int, 1, 20),
    "search.nextcloud_max_items": ("search", "nextcloud_max_items", int, 10, 10000),
    "search.max_file_bytes": ("search", "max_file_bytes", int, 1024, 250_000_000),
    "nextcloud.enabled": ("nextcloud", "enabled", bool, None, None),
    "self_management.allow_resource_discovery": ("self_management", "allow_resource_discovery", bool, None, None),
}


class SettingsService:
    def __init__(self, config_path: Path, storage: AssistantStorage) -> None:
        self.config_path = config_path
        self.storage = storage

    def list_safe(self) -> dict[str, object]:
        with self.config_path.open("rb") as handle:
            data = tomllib.load(handle)
        result: dict[str, object] = {}
        for public_key, (section, key, _, _, _) in SAFE_SETTINGS.items():
            result[public_key] = data.get(section, {}).get(key)
        return result

    def set_safe(self, public_key: str, raw_value: str, *, actor: str = "user") -> Path:
        if public_key not in SAFE_SETTINGS:
            raise ValueError(f"Setting darf nicht autonom geaendert werden: {public_key}")
        section, key, value_type, minimum, maximum = SAFE_SETTINGS[public_key]
        value = self._convert(raw_value, value_type)
        if isinstance(value, int):
            if minimum is not None and value < minimum:
                raise ValueError(f"{public_key} muss mindestens {minimum} sein")
            if maximum is not None and value > maximum:
                raise ValueError(f"{public_key} darf hoechstens {maximum} sein")
        with self.config_path.open("rb") as handle:
            before = tomllib.load(handle).get(section, {}).get(key)
        backup = self._update_toml({(section, key): value})
        self.storage.connection.execute(
            "INSERT INTO settings_history(setting_key,old_value,new_value,actor,approved,created_at) VALUES(?,?,?,?,?,datetime('now'))",
            (public_key, json.dumps(before, ensure_ascii=False), json.dumps(value, ensure_ascii=False), actor, 1),
        )
        self.storage.connection.commit()
        self.storage.audit("settings.safe_update", {"key": public_key, "old": before, "new": value}, actor=actor)
        return backup

    @staticmethod
    def _convert(raw: str, value_type: type) -> object:
        if value_type is bool:
            normalized = raw.strip().casefold()
            if normalized in {"1", "true", "yes", "ja", "on"}:
                return True
            if normalized in {"0", "false", "no", "nein", "off"}:
                return False
            raise ValueError("Boolescher Wert erwartet: true oder false")
        return value_type(raw)

    def _update_toml(self, changes: dict[tuple[str, str], object]) -> Path:
        text = self.config_path.read_text(encoding="utf-8")
        backup_dir = self.config_path.parent / "data/backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        backup = backup_dir / (self.config_path.name + ".backup-" + datetime.now().strftime("%Y%m%d-%H%M%S-%f"))
        shutil.copy2(self.config_path, backup)
        os.chmod(backup, 0o600)
        for stale in sorted(backup_dir.glob(self.config_path.name + ".backup-*"))[:-20]:
            stale.unlink(missing_ok=True)
        for (section, key), value in changes.items():
            section_pattern = re.compile(rf"(?ms)(^\[{re.escape(section)}\][ \t]*\n)(.*?)(?=^\[|\Z)")
            match = section_pattern.search(text)
            line = f"{key} = {self._literal(value)}"
            if not match:
                text = text.rstrip() + f"\n\n[{section}]\n{line}\n"
                continue
            body = match.group(2)
            key_pattern = re.compile(rf"(?m)^[ \t]*{re.escape(key)}[ \t]*=.*$")
            if key_pattern.search(body):
                body = key_pattern.sub(line, body, count=1)
            else:
                body = body.rstrip() + "\n" + line + "\n"
            text = text[:match.start(2)] + body + text[match.end(2):]
        tomllib.loads(text)
        temp = self.config_path.with_suffix(self.config_path.suffix + ".tmp")
        temp.write_text(text, encoding="utf-8")
        os.chmod(temp, 0o600)
        temp.replace(self.config_path)
        os.chmod(self.config_path, 0o600)
        return backup

    @staticmethod
    def _literal(value: object) -> str:
        if isinstance(value, bool):
            return str(value).lower()
        if isinstance(value, int):
            return str(value)
        return json.dumps(str(value), ensure_ascii=False)
