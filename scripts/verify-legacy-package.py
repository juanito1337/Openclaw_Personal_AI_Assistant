#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "legacy/systemd"
MANIFEST = PACKAGE / "manifest.json"


def digest(path: Path) -> str:
    result = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            result.update(chunk)
    return result.hexdigest()


def package_files(package: Path = PACKAGE) -> list[Path]:
    return sorted(
        path.relative_to(package)
        for path in package.rglob("*")
        if path.is_file() and path.name != "manifest.json"
    )


def payload(package: Path = PACKAGE) -> dict[str, object]:
    return {
        "schema_version": 1,
        "package": "legacy-systemd",
        "status": "compatibility",
        "frozen_from": "3.4.0-r27.2.5",
        "active_deployment": False,
        "files": [
            {"path": path.as_posix(), "sha256": digest(package / path)}
            for path in package_files(package)
        ],
    }


def write_manifest(package: Path = PACKAGE) -> Path:
    target = package / "manifest.json"
    package.mkdir(parents=True, exist_ok=True)
    rendered = json.dumps(payload(package), ensure_ascii=False, indent=2) + "\n"
    fd, temporary_name = tempfile.mkstemp(prefix=".manifest.", dir=package, text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(rendered)
            handle.flush()
            os.fsync(handle.fileno())
        Path(temporary_name).replace(target)
    finally:
        Path(temporary_name).unlink(missing_ok=True)
    return target


def verify(package: Path = PACKAGE) -> list[str]:
    manifest = package / "manifest.json"
    try:
        found = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [f"Legacy-Manifest unlesbar: {exc}"]
    errors: list[str] = []
    if found.get("schema_version") != 1:
        errors.append("schema_version muss 1 sein")
    if found.get("package") != "legacy-systemd":
        errors.append("package muss legacy-systemd sein")
    if found.get("frozen_from") != "3.4.0-r27.2.5":
        errors.append("frozen_from muss 3.4.0-r27.2.5 sein")
    if found.get("status") != "compatibility" or found.get("active_deployment") is not False:
        errors.append("Legacy-Paket darf nicht als aktive Bereitstellung markiert sein")
    entries = found.get("files")
    if not isinstance(entries, list):
        return [*errors, "files muss eine Liste sein"]
    actual_paths = {path.as_posix() for path in package_files(package)}
    recorded: dict[str, str] = {}
    for item in entries:
        if not isinstance(item, dict):
            errors.append("Dateieintrag ist kein Objekt")
            continue
        relative = str(item.get("path") or "")
        path = Path(relative)
        if not relative or path.is_absolute() or ".." in path.parts:
            errors.append(f"Ungueltiger Legacy-Pfad: {relative!r}")
            continue
        if relative in recorded:
            errors.append(f"Doppelter Legacy-Eintrag: {relative}")
            continue
        recorded[relative] = str(item.get("sha256") or "")
    for relative in sorted(actual_paths - recorded.keys()):
        errors.append(f"Fehlender Legacy-Eintrag: {relative}")
    for relative in sorted(recorded.keys() - actual_paths):
        errors.append(f"Zusaetzlicher oder fehlender Legacy-Pfad: {relative}")
    for relative in sorted(actual_paths & recorded.keys()):
        current = digest(package / relative)
        if recorded[relative] != current:
            errors.append(f"Geaenderter Legacy-Inhalt: {relative}")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description="Eingefrorenes Legacy-systemd-Paket pruefen")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("generate")
    commands.add_parser("verify")
    args = parser.parse_args()
    if args.command == "generate":
        write_manifest()
    errors = verify()
    if errors:
        print(json.dumps({"ok": False, "errors": errors}, ensure_ascii=False, indent=2))
        return 1
    current = json.loads(MANIFEST.read_text(encoding="utf-8"))
    print(json.dumps(
        {"ok": True, "files": len(current["files"]), "manifest": str(MANIFEST)},
        ensure_ascii=False,
        indent=2,
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
