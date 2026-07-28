#!/usr/bin/env python3
from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
import tomllib
from datetime import datetime
from pathlib import Path
from typing import Any

KNOWN = {"id", "kind", "connector", "enabled", "remote_id", "permissions"}


def quote(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', r'\"')
    return f'"{escaped}"'


def render_value(value: Any) -> str:
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return repr(value)
    if isinstance(value, list):
        return "[" + ", ".join(render_value(item) for item in value) + "]"
    return quote(str(value))


def render(resources: list[dict[str, Any]]) -> str:
    lines = [
        "# Resource registry for the Personal Assistant.",
        "# Secrets are referenced by environment variable names and never stored here.",
        "",
    ]
    for item in sorted(resources, key=lambda value: (str(value.get("kind", "")), str(value.get("id", "")))):
        lines.extend(
            [
                "[[resources]]",
                f"id = {quote(str(item['id']))}",
                f"kind = {quote(str(item.get('kind') or ''))}",
                f"connector = {quote(str(item.get('connector') or ''))}",
                f"enabled = {str(bool(item.get('enabled', True))).lower()}",
                f"remote_id = {quote(str(item.get('remote_id') or ''))}",
                "permissions = ["
                + ", ".join(quote(str(value)) for value in item.get("permissions", []))
                + "]",
            ]
        )
        for key in sorted(value for value in item if value not in KNOWN):
            lines.append(f"{key} = {render_value(item[key])}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def load(path: Path) -> list[dict[str, Any]]:
    with path.open("rb") as handle:
        data = tomllib.load(handle)
    values = data.get("resources", [])
    if not isinstance(values, list):
        raise ValueError("resources.toml: [[resources]] fehlt oder ist ungueltig")
    result: list[dict[str, Any]] = []
    for item in values:
        if not isinstance(item, dict):
            continue
        resource_id = str(item.get("id") or "").strip()
        if not resource_id:
            raise ValueError("resources.toml: resource.id fehlt")
        normalized = dict(item)
        normalized["id"] = resource_id
        result.append(normalized)
    return result


def deduplicate(resources: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[str]]:
    by_id: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    duplicates: list[str] = []
    for item in resources:
        resource_id = str(item["id"])
        if resource_id in by_id:
            duplicates.append(resource_id)
            order.remove(resource_id)
        by_id[resource_id] = item
        order.append(resource_id)
    return [by_id[resource_id] for resource_id in order], duplicates


def repair(path: Path, *, backup_dir: Path | None = None) -> dict[str, Any]:
    resources = load(path)
    unique, duplicates = deduplicate(resources)
    if not duplicates:
        return {
            "changed": False,
            "before": len(resources),
            "after": len(unique),
            "duplicates": [],
            "backup": "",
        }

    if backup_dir is None:
        backup_dir = path.parent
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    backup = backup_dir / f"{path.name}.before-dedup-{stamp}"
    shutil.copy2(path, backup)

    content = render(unique)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, prefix=path.name + ".", suffix=".tmp", delete=False
    ) as handle:
        temp = Path(handle.name)
        handle.write(content)
        handle.flush()
    try:
        temp.chmod(path.stat().st_mode & 0o777)
        # Validate before replacing the live registry.
        load(temp)
        temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)

    # Strict postcondition: no duplicate remains.
    repaired = load(path)
    _, remaining = deduplicate(repaired)
    if remaining:
        shutil.copy2(backup, path)
        raise RuntimeError("Registry-Reparatur unvollstaendig; Original wurde wiederhergestellt")

    return {
        "changed": True,
        "before": len(resources),
        "after": len(repaired),
        "duplicates": sorted(set(duplicates)),
        "backup": str(backup),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Doppelte IDs in resources.toml sicher bereinigen")
    parser.add_argument("path", type=Path)
    parser.add_argument("--backup-dir", type=Path)
    args = parser.parse_args()
    try:
        result = repair(args.path.expanduser().resolve(), backup_dir=args.backup_dir)
    except Exception as exc:
        print(f"FEHLER: {exc}", file=sys.stderr)
        return 1
    print(f"Ressourcen: {result['before']} -> {result['after']}")
    if result["duplicates"]:
        print("Entfernte doppelte IDs: " + ", ".join(result["duplicates"]))
    else:
        print("Keine doppelten IDs gefunden.")
    if result["backup"]:
        print("Registry-Backup: " + result["backup"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
