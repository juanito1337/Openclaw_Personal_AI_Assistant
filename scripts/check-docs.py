#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import deque
from pathlib import Path
from urllib.parse import unquote

LINK_RE = re.compile(r"(?<!!)\[[^\]]+\]\(([^)]+)\)")
RELEASE_RE = re.compile(r"\b\d+\.\d+\.\d+-r\d+(?:\.\d+)*\b")
SKIP_PARTS = {".git", ".mypy_cache", ".pytest_cache", ".ruff_cache", ".tools", ".venv", "build"}
SKIP_ROOTS = {"srv"}


def active_markdown_files(root: Path) -> list[Path]:
    result: list[Path] = []
    for current, directories, filenames in os.walk(root, topdown=True, followlinks=False):
        current_path = Path(current)
        relative_current = current_path.relative_to(root)
        directories[:] = sorted(
            name
            for name in directories
            if name not in SKIP_PARTS
            and not (current_path == root and name in SKIP_ROOTS)
        )
        if relative_current.parts[:2] == ("docs", "archive"):
            directories[:] = []
            continue
        for name in sorted(filenames):
            if name.endswith(".md"):
                result.append(relative_current / name)
    return sorted(result)


def link_target(raw: str) -> str:
    value = raw.strip()
    if value.startswith("<") and ">" in value:
        return value[1 : value.index(">")]
    return value.split(maxsplit=1)[0]


def internal_links(root: Path, source: Path) -> list[Path]:
    text = (root / source).read_text(encoding="utf-8")
    targets: list[Path] = []
    for match in LINK_RE.finditer(text):
        raw = unquote(link_target(match.group(1)))
        if not raw or raw.startswith("#") or re.match(r"^[a-z][a-z0-9+.-]*:", raw, re.I):
            continue
        clean = raw.split("#", 1)[0]
        if not clean:
            continue
        target = (source.parent / clean).as_posix()
        resolved = (root / target).resolve()
        try:
            targets.append(resolved.relative_to(root.resolve()))
        except ValueError:
            targets.append(Path("..") / "OUTSIDE_REPOSITORY")
    return targets


def validate_links(root: Path) -> list[str]:
    errors: list[str] = []
    for source in active_markdown_files(root):
        for target in internal_links(root, source):
            if "OUTSIDE_REPOSITORY" in target.parts or not (root / target).exists():
                errors.append(f"{source}: ungueltiger interner Link auf {target}")
    return errors


def validate_owners(root: Path) -> list[str]:
    errors: list[str] = []
    owner_path = root / "docs/architecture/owners.json"
    try:
        payload = json.loads(owner_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [f"docs/architecture/owners.json ist ungueltig: {exc}"]
    if payload.get("schema_version") != 1 or not isinstance(payload.get("documents"), list):
        return ["docs/architecture/owners.json: schema_version/documents ungueltig"]
    entries: dict[str, str] = {}
    for item in payload["documents"]:
        if not isinstance(item, dict):
            errors.append("owners.json: Dokumenteintrag ist kein Objekt")
            continue
        path = str(item.get("path") or "")
        owner = str(item.get("owner") or "").strip()
        if path in entries:
            errors.append(f"owners.json: doppelter Dokumentpfad {path}")
        if not path.startswith("docs/architecture/") or not owner:
            errors.append(f"owners.json: ungueltiger Owner-Eintrag fuer {path or '<leer>'}")
        entries[path] = owner
    expected = {
        path.relative_to(root).as_posix()
        for path in (root / "docs/architecture").rglob("*.md")
    }
    actual = set(entries)
    for path in sorted(expected - actual):
        errors.append(f"owners.json: Owner fehlt fuer {path}")
    for path in sorted(actual - expected):
        errors.append(f"owners.json: Eintrag ohne aktives Dokument {path}")
    return errors


def validate_release_refs(root: Path) -> list[str]:
    try:
        release = json.loads((root / "RELEASE.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [f"RELEASE.json ist ungueltig: {exc}"]
    version = str(release.get("version") or "")
    errors: list[str] = []
    for relative in ("README.md", "AGENTS.md", "CHANGELOG.md", "skills/personal-assistant/SKILL.md"):
        path = root / relative
        text = path.read_text(encoding="utf-8") if path.exists() else ""
        if version not in text:
            errors.append(f"{relative}: aktuelle Version {version} fehlt")
    heading = (root / "README.md").read_text(encoding="utf-8").splitlines()[0]
    refs = RELEASE_RE.findall(heading)
    if refs != [version]:
        errors.append(f"README.md: Titel muss genau die aktuelle Version {version} enthalten")
    return errors


def validate_readme_reachability(root: Path) -> list[str]:
    required = {
        Path("docs/architecture/README.md"),
        Path("docs/DOCKER_DEPLOYMENT.md"),
        Path("docs/TESTING.md"),
        Path("docs/architecture/EXTENDING.md"),
    }
    seen = {Path("README.md"): 0}
    queue: deque[Path] = deque([Path("README.md")])
    while queue:
        source = queue.popleft()
        depth = seen[source]
        if depth >= 2 or not (root / source).is_file():
            continue
        for target in internal_links(root, source):
            if target.suffix.casefold() != ".md" or not (root / target).is_file():
                continue
            if target not in seen or seen[target] > depth + 1:
                seen[target] = depth + 1
                queue.append(target)
    return [
        f"README.md: {target} ist nicht in hoechstens zwei internen Links erreichbar"
        for target in sorted(required)
        if seen.get(target, 99) > 2
    ]


def markdown_table_rows(path: Path, header_first_cell: str) -> list[list[str]]:
    lines = path.read_text(encoding="utf-8").splitlines()
    for index, line in enumerate(lines):
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if cells and cells[0] == header_first_cell and index + 1 < len(lines):
            rows: list[list[str]] = []
            for row in lines[index + 2 :]:
                if not row.strip().startswith("|"):
                    break
                rows.append([cell.strip() for cell in row.strip().strip("|").split("|")])
            return rows
    return []


def validate_matrices(root: Path) -> list[str]:
    errors: list[str] = []
    role_path = root / "docs/architecture/CONTAINER_ROLES.md"
    role_rows = markdown_table_rows(role_path, "Rolle")
    roles = {row[0].strip("`") for row in role_rows if row}
    expected_roles = {
        "ollama-proxy", "gateway", "mail-worker", "sync-worker",
        "supervisor-worker", "portfolio-worker", "monitor-worker",
        "agent-cli", "clamav-update",
    }
    for role in sorted(expected_roles - roles):
        errors.append(f"CONTAINER_ROLES.md: Rolle {role} fehlt in der Rollenmatrix")
    data_path = root / "docs/architecture/DATA_CATALOG.md"
    database_rows = markdown_table_rows(data_path, "Datei")
    databases = {row[0].strip("`") for row in database_rows if row}
    expected_databases = {
        "domains/mail/mail_agent.sqlite3",
        "shared/core/assistant.sqlite3",
        "shared/security/antivirus.sqlite3",
        "domains/orders/orders.sqlite3",
        "domains/portfolio/portfolio.sqlite3",
        "domains/monitoring/monitoring.sqlite3",
        "domains/knowledge/knowledge.sqlite3",
        "shared/coordination/work_scheduler.sqlite3",
    }
    for database in sorted(expected_databases - databases):
        errors.append(f"DATA_CATALOG.md: Datenbank {database} fehlt im SQLite-Katalog")
    return errors


def validate_runtime_wording(root: Path) -> list[str]:
    forbidden = {
        "The assistant remains one local Python codebase with separate systemd jobs",
        "systemd is the primary runtime",
        "systemd ist der primaere Runtimepfad",
    }
    errors: list[str] = []
    active = [Path("README.md"), Path("docs/ARCHITECTURE.md"), Path("docs/ASSISTANT_ARCHITECTURE.md")]
    active.extend(path.relative_to(root) for path in (root / "docs/architecture").rglob("*.md"))
    for relative in active:
        text = (root / relative).read_text(encoding="utf-8")
        for phrase in forbidden:
            if phrase.casefold() in text.casefold():
                errors.append(f"{relative}: systemd wird als primaerer Runtimepfad beschrieben")
    return errors


def check(root: Path) -> list[str]:
    validators = (
        validate_links,
        validate_owners,
        validate_release_refs,
        validate_readme_reachability,
        validate_matrices,
        validate_runtime_wording,
    )
    return [error for validator in validators for error in validator(root)]


def main() -> int:
    parser = argparse.ArgumentParser(description="M1-Architektur- und Dokumentationsvertrag pruefen")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    errors = check(args.root.resolve())
    if errors:
        print("Dokumentationspruefung fehlgeschlagen:", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1
    print("Dokumentationspruefung erfolgreich.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
