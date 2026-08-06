from __future__ import annotations

import os
import re
from collections.abc import Mapping
from pathlib import Path

_ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _decode(raw: str) -> str:
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


def load_env(path: Path, *, override: bool = False) -> list[str]:
    try:
        lines = path.expanduser().read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return []
    loaded: list[str] = []
    for line_number, raw in enumerate(lines, 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        if "=" not in line:
            raise ValueError(f"Ungueltige Zeile {path}:{line_number}")
        name, value = line.split("=", 1)
        name = name.strip()
        if not _ENV_NAME.fullmatch(name):
            raise ValueError(f"Ungueltiger Variablenname {path}:{line_number}: {name!r}")
        if override or name not in os.environ:
            os.environ[name] = _decode(value)
        loaded.append(name)
    return loaded


def _encode(value: str) -> str:
    if not value:
        return '""'
    if re.fullmatch(r"[A-Za-z0-9_./:@+,%=-]+", value):
        return value
    return '"' + value.replace("\\", "\\\\").replace('"', r'\"').replace("\n", r"\n") + '"'


def update_env(path: Path, values: Mapping[str, str]) -> None:
    env_path = path.expanduser().resolve()
    env_path.parent.mkdir(parents=True, exist_ok=True)
    existing = env_path.read_text(encoding="utf-8") if env_path.exists() else ""
    pending = {str(k): str(v) for k, v in values.items()}
    output: list[str] = []
    seen: set[str] = set()
    for raw in existing.splitlines():
        candidate = raw.strip()
        if candidate.startswith("export "):
            candidate = candidate[7:].lstrip()
        if candidate and not candidate.startswith("#") and "=" in candidate:
            name = candidate.split("=", 1)[0].strip()
            if name in pending:
                output.append(f"{name}={_encode(pending[name])}")
                seen.add(name)
                continue
        output.append(raw)
    if not existing:
        output.extend([
            "# Central Personal-Assistant secrets. Never commit this file.",
            "# Managed through ./scripts/assistant.sh setup ...",
        ])
    if output and output[-1].strip():
        output.append("")
    for name, value in pending.items():
        if not _ENV_NAME.fullmatch(name):
            raise ValueError(f"Ungueltiger Variablenname: {name!r}")
        if name not in seen:
            output.append(f"{name}={_encode(value)}")
    temp = env_path.with_suffix(env_path.suffix + ".tmp")
    temp.write_text("\n".join(output).rstrip() + "\n", encoding="utf-8")
    os.chmod(temp, 0o600)
    temp.replace(env_path)
    os.chmod(env_path, 0o600)
