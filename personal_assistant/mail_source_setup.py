from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path

from .config import WORKSPACE_ROOT

DEFAULT_MAIL_CONFIG = WORKSPACE_ROOT / "mail_agent/config.toml"
_KEY_RE = re.compile(r"^\s*(source_folder|quarantine_folders|quarantine_max_per_run|quarantine_rescue_only)\s*=")
_SECTION_RE = re.compile(r"^\s*\[([^]]+)\]\s*(?:#.*)?$")


def _clean_folder(value: str) -> str:
    folder = str(value or "").strip()
    if not folder or "\r" in folder or "\n" in folder:
        raise ValueError("Mailordner darf nicht leer sein oder Zeilenumbrueche enthalten")
    return folder


def _toml_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _render_settings(primary: str, quarantine: tuple[str, ...], max_per_run: int, rescue_only: bool) -> list[str]:
    values = ", ".join(_toml_string(folder) for folder in quarantine)
    return [
        f"source_folder = {_toml_string(primary)}\n",
        f"quarantine_folders = [{values}]\n",
        f"quarantine_max_per_run = {max_per_run}\n",
        f"quarantine_rescue_only = {'true' if rescue_only else 'false'}\n",
    ]


def configure_mail_sources(
    *,
    config_path: Path = DEFAULT_MAIL_CONFIG,
    primary: str = "INBOX",
    quarantine_folders: Iterable[str] = ("Spam",),
    max_per_run: int = 10,
    rescue_only: bool = True,
) -> dict[str, object]:
    config_path = Path(config_path).expanduser().resolve()
    if not config_path.is_file():
        raise FileNotFoundError(config_path)
    primary = _clean_folder(primary)
    quarantine = tuple(dict.fromkeys(_clean_folder(item) for item in quarantine_folders))
    if primary.casefold() in {item.casefold() for item in quarantine}:
        raise ValueError("Primaerordner und Quarantaeneordner muessen verschieden sein")
    if not isinstance(max_per_run, int) or not 0 <= max_per_run <= 500:
        raise ValueError("max_per_run muss zwischen 0 und 500 liegen")

    original = config_path.read_text(encoding="utf-8")
    lines = original.splitlines(keepends=True)
    output: list[str] = []
    in_mailbox = False
    mailbox_found = False
    inserted = False

    for line in lines:
        section = _SECTION_RE.match(line)
        if section:
            if in_mailbox and not inserted:
                output.extend(_render_settings(primary, quarantine, max_per_run, rescue_only))
                inserted = True
            in_mailbox = section.group(1).strip().casefold() == "mailbox"
            mailbox_found = mailbox_found or in_mailbox
            output.append(line)
            continue
        if in_mailbox and _KEY_RE.match(line):
            continue
        output.append(line)

    if in_mailbox and not inserted:
        output.extend(_render_settings(primary, quarantine, max_per_run, rescue_only))
        inserted = True
    if not mailbox_found:
        prefix = "" if not output or output[-1].endswith("\n\n") else "\n"
        output.extend([prefix, "[mailbox]\n", *_render_settings(primary, quarantine, max_per_run, rescue_only)])

    rendered = "".join(output)
    if rendered == original:
        backup = ""
    else:
        stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S-%f")
        backup_path = config_path.with_name(config_path.name + f".backup-{stamp}")
        shutil.copy2(config_path, backup_path)
        mode = config_path.stat().st_mode & 0o777
        fd, temp_name = tempfile.mkstemp(prefix=config_path.name + ".", dir=config_path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(rendered)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temp_name, mode)
            os.replace(temp_name, config_path)
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)
        backup = str(backup_path)

    # Validate with the production loader after the atomic write.
    from mail_agent.config import load_config

    loaded = load_config(config_path)
    return {
        "ok": True,
        "config": str(config_path),
        "backup": backup,
        "primary": loaded.mailbox.source_folder,
        "quarantine_folders": list(loaded.mailbox.quarantine_folders),
        "quarantine_max_per_run": loaded.mailbox.quarantine_max_per_run,
        "quarantine_rescue_only": loaded.mailbox.quarantine_rescue_only,
    }
