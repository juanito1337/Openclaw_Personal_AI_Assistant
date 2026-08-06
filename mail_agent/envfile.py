from __future__ import annotations

import os
import re
import shutil
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path

_ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def default_env_file() -> Path:
    configured = os.environ.get("MAIL_AGENT_ENV_FILE", "~/.config/mail-agent.env")
    return Path(configured).expanduser().resolve()


def _decode_value(raw: str) -> str:
    value = raw.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        quote = value[0]
        value = value[1:-1]
        if quote == '"':
            value = (
                value.replace(r"\n", "\n")
                .replace(r"\r", "\r")
                .replace(r"\t", "\t")
                .replace(r'\"', '"')
                .replace(r"\\", "\\")
            )
    return value


def load_env_file(path: Path | None = None, *, override: bool = False) -> list[str]:
    """Load a systemd-style KEY=VALUE file without executing shell code.

    The parser intentionally supports only simple assignments. Lines beginning
    with ``export`` are accepted for convenience, but command substitution,
    variable expansion, and shell expressions are never evaluated.
    """

    env_path = (path or default_env_file()).expanduser().resolve()
    try:
        lines = env_path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return []
    loaded: list[str] = []
    for line_number, raw_line in enumerate(lines, 1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        if "=" not in line:
            raise ValueError(f"Ungueltige Umgebungszeile {env_path}:{line_number}: '=' fehlt")
        name, raw_value = line.split("=", 1)
        name = name.strip()
        if not _ENV_NAME.fullmatch(name):
            raise ValueError(f"Ungueltiger Variablenname {env_path}:{line_number}: {name!r}")
        value = _decode_value(raw_value)
        if override or name not in os.environ:
            os.environ[name] = value
        loaded.append(name)
    return loaded


def _encode_value(value: str) -> str:
    if not value:
        return '""'
    if re.fullmatch(r"[A-Za-z0-9_./:@+,%=-]+", value):
        return value
    escaped = value.replace("\\", "\\\\").replace('"', r'\"').replace("\n", r"\n")
    return f'"{escaped}"'


def update_env_file(path: Path, values: Mapping[str, str]) -> Path | None:
    """Update selected variables while preserving comments and unknown keys."""

    env_path = path.expanduser().resolve()
    env_path.parent.mkdir(parents=True, exist_ok=True)
    existing = env_path.read_text(encoding="utf-8") if env_path.exists() else ""
    backup: Path | None = None
    if env_path.exists():
        backup = env_path.with_name(env_path.name + ".backup-" + datetime.now().strftime("%Y%m%d-%H%M%S-%f"))
        shutil.copy2(env_path, backup)
        # Backups contain the same credentials and therefore need the same
        # restrictive permissions, even if a pre-existing env file was looser.
        os.chmod(backup, 0o600)

    pending = {str(key): str(value) for key, value in values.items()}
    output: list[str] = []
    seen: set[str] = set()
    for raw_line in existing.splitlines():
        stripped = raw_line.strip()
        candidate = stripped[7:].lstrip() if stripped.startswith("export ") else stripped
        if candidate and not candidate.startswith("#") and "=" in candidate:
            name = candidate.split("=", 1)[0].strip()
            if name in pending and _ENV_NAME.fullmatch(name):
                output.append(f"{name}={_encode_value(pending[name])}")
                seen.add(name)
                continue
        output.append(raw_line)

    if output and output[-1].strip():
        output.append("")
    if not existing:
        output.extend([
            "# Mail-Agent secrets. Keep this file outside the workspace and chmod 600.",
            "# Generated/updated by ./scripts/mail-agent.sh nextcloud setup",
        ])
    for name, value in pending.items():
        if not _ENV_NAME.fullmatch(name):
            raise ValueError(f"Ungueltiger Variablenname: {name!r}")
        if name not in seen:
            output.append(f"{name}={_encode_value(value)}")

    temp = env_path.with_suffix(env_path.suffix + ".tmp")
    temp.write_text("\n".join(output).rstrip() + "\n", encoding="utf-8")
    os.chmod(temp, 0o600)
    temp.replace(env_path)
    os.chmod(env_path, 0o600)
    return backup
