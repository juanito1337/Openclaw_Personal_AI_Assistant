from __future__ import annotations

import getpass
import os
import shutil
from pathlib import Path

from .config import AssistantConfig, WORKSPACE_ROOT
from .env import update_env
from mail_agent.setup_assistant import update_toml_values


def initialize_local_files() -> list[str]:
    created: list[str] = []
    for source_name, target_name in (
        ("config.example.toml", "config.toml"),
        ("resources.example.toml", "resources.toml"),
        ("policies.example.toml", "policies.toml"),
    ):
        source = WORKSPACE_ROOT / "personal_assistant" / source_name
        target = WORKSPACE_ROOT / "personal_assistant" / target_name
        if not target.exists():
            shutil.copy2(source, target)
            os.chmod(target, 0o600)
            created.append(str(target))
    (WORKSPACE_ROOT / "personal_assistant/data").mkdir(parents=True, exist_ok=True)
    return created


def configure_nextcloud(
    config: AssistantConfig,
    *,
    url: str = "",
    username: str = "",
    token: str = "",
    interactive: bool = True,
    use_existing: bool = False,
) -> dict[str, object]:
    if use_existing:
        url = os.environ.get(config.nextcloud.base_url_env, "").strip()
        username = os.environ.get(config.nextcloud.username_env, "").strip()
        token = os.environ.get(config.nextcloud.token_env, "").strip()
    elif interactive:
        url = url or input("Nextcloud URL (https://...): ").strip()
        username = username or input("Nextcloud Benutzer: ").strip()
        token = token or getpass.getpass("Nextcloud App-Passwort: ").strip()
    if not url.startswith("https://"):
        raise ValueError("Fuer die zentrale Nextcloud-Konfiguration ist HTTPS erforderlich")
    if not username or not token:
        raise ValueError("Benutzername und App-Passwort duerfen nicht leer sein")
    if not use_existing:
        update_env(config.runtime.secrets_file, {
            config.nextcloud.base_url_env: url.rstrip("/"),
            config.nextcloud.username_env: username,
            config.nextcloud.token_env: token,
        })
    backup = update_toml_values(config.path, {("nextcloud", "enabled"): True})
    return {
        "ok": True,
        "secrets_file": str(config.runtime.secrets_file),
        "config_backup": str(backup),
        "next_step": "./scripts/assistant.sh nextcloud discover",
    }
