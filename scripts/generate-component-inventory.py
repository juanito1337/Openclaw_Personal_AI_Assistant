#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DECISIONS = ROOT / "docs/architecture/legacy-decisions.json"
INVENTORY = ROOT / "docs/architecture/component-inventory.json"
EXCLUDED = {"SOURCE_MANIFEST.sha256", "docs/architecture/component-inventory.json"}
CLASSIFICATIONS = {"active", "compatibility", "migration-only", "deprecated", "unused"}


def _inside_git_worktree() -> bool:
    result = subprocess.run(
        ["git", "rev-parse", "--is-inside-work-tree"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    return result.returncode == 0 and result.stdout.strip() == "true"


def _manifest_paths() -> list[str]:
    paths: list[str] = []
    for line in (ROOT / "SOURCE_MANIFEST.sha256").read_text(encoding="utf-8").splitlines():
        _digest, separator, raw_path = line.partition("  ")
        if separator and raw_path.startswith("./"):
            paths.append(raw_path.removeprefix("./"))
    return sorted(path for path in paths if path not in EXCLUDED)


def source_paths() -> list[str]:
    if not _inside_git_worktree():
        return _manifest_paths()
    result = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        cwd=ROOT,
        check=True,
        capture_output=True,
    )
    return sorted(
        item.decode("utf-8")
        for item in result.stdout.split(b"\0")
        if item
        and item.decode("utf-8") not in EXCLUDED
        and (ROOT / item.decode("utf-8")).is_file()
    )


def component_types(path: str) -> list[str]:
    result: list[str] = []
    if path.endswith(".py"):
        result.append("python-module")
    if path.endswith(".sh"):
        result.append("shell-entrypoint")
    if path.startswith("skills/"):
        result.append("skill")
    if path.startswith("legacy/systemd/units/"):
        result.append("systemd-unit")
    if "migrat" in path.casefold() and not path.startswith("docs/archive/"):
        result.append("migration")
    if path.endswith(".md"):
        result.append("document")
    return result


def owner(path: str) -> str:
    if path.startswith("mail_agent/"):
        return "Mail Domain Maintainers"
    if path.startswith("personal_assistant/"):
        return "Core Maintainers"
    if path.startswith(("docker/", "legacy/systemd/")):
        return "Operations Maintainers"
    if path.startswith("skills/"):
        return "Tool Contract Maintainers"
    if path.startswith("tests/"):
        return "Quality Maintainers"
    if path.startswith(("scripts/", ".github/")):
        return "Build Maintainers"
    if path.startswith("docs/architecture/"):
        return "Architecture Maintainers"
    if path.startswith("docs/") or path.endswith(".md"):
        return "Documentation Maintainers"
    return "Repository Maintainers"


def last_git_changes() -> dict[str, str]:
    if not _inside_git_worktree():
        return {}
    result = subprocess.run(
        ["git", "log", "--format=@@%aI", "--name-only", "--"],
        cwd=ROOT,
        check=True,
        text=True,
        capture_output=True,
    )
    current = ""
    found: dict[str, str] = {}
    for line in result.stdout.splitlines():
        if line.startswith("@@"):
            current = line[2:]
        elif line and current and line not in found:
            found[line] = current
    return found


def coverage_snapshot() -> dict[str, float]:
    path = ROOT / "build/coverage.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    result: dict[str, float] = {}
    for name, item in payload.get("files", {}).items():
        summary = item.get("summary", {}) if isinstance(item, dict) else {}
        value = summary.get("percent_covered") if isinstance(summary, dict) else None
        if isinstance(value, (int, float)):
            result[str(name)] = round(float(value), 2)
    return result


def reference_tokens(path: str) -> set[str]:
    value = Path(path)
    tokens = {path, value.name}
    if path.endswith(".py"):
        tokens.add(path.removesuffix(".py").replace("/", "."))
    return {token for token in tokens if len(token) >= 5}


def text_sources(paths: list[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for path in paths:
        source = ROOT / path
        try:
            if source.stat().st_size <= 2_000_000:
                result[path] = source.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
    return result


def references(path: str, sources: dict[str, str]) -> dict[str, list[str]]:
    tokens = reference_tokens(path)
    found = [
        candidate
        for candidate, text in sources.items()
        if candidate != path and any(token in text for token in tokens)
    ]
    return {
        "productive_callers": sorted(
            candidate for candidate in found
            if not candidate.startswith(("tests/", "docs/")) and not candidate.endswith(".md")
        ),
        "test_evidence": sorted(candidate for candidate in found if candidate.startswith("tests/")),
        "documentation_evidence": sorted(
            candidate for candidate in found
            if candidate.startswith("docs/") or candidate.endswith(".md")
        ),
    }


def apply_override(path: str, decisions: dict[str, Any]) -> tuple[str, str]:
    classification = "deprecated" if path.startswith("docs/archive/") else "active"
    rollback = "none"
    if "migration" in component_types(path):
        classification = "migration-only"
        rollback = "migration"
    for item in decisions.get("classification_overrides", []):
        if not isinstance(item, dict):
            continue
        matches = item.get("path") == path or (
            isinstance(item.get("prefix"), str) and path.startswith(item["prefix"])
        )
        if matches:
            classification = str(item.get("classification") or classification)
            rollback = str(item.get("rollback_relevance") or rollback)
    return classification, rollback


def build_payload(*, preserve_snapshots: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
    decisions = json.loads(DECISIONS.read_text(encoding="utf-8"))
    paths = source_paths()
    selected = [path for path in paths if component_types(path)]
    sources = text_sources(paths)
    git_dates = last_git_changes()
    coverage = coverage_snapshot()
    snapshots = preserve_snapshots or {}
    components: list[dict[str, Any]] = []
    for path in selected:
        classification, rollback = apply_override(path, decisions)
        if classification not in CLASSIFICATIONS:
            raise ValueError(f"Ungueltige Klassifikation fuer {path}: {classification}")
        refs = references(path, sources)
        prior = snapshots.get(path, {})
        components.append({
            "path": path,
            "types": component_types(path),
            "owner": owner(path),
            "classification": classification,
            "productive_callers": refs["productive_callers"],
            "test_evidence": refs["test_evidence"],
            "documentation_evidence": refs["documentation_evidence"],
            "line_coverage_percent": prior.get("line_coverage_percent", coverage.get(path)),
            "last_git_change": prior.get("last_git_change", git_dates.get(path)),
            "runtime_telemetry": {
                "status": "not-collected",
                "reason": "M6 inventory stores no user content or production telemetry"
            },
            "rollback_relevance": rollback,
        })
    return {
        "schema_version": 1,
        "inventory_date": decisions["inventory_date"],
        "source_scope": (
            "all Python modules, shell entrypoints, skills, systemd units, "
            "migrations and Markdown documents"
        ),
        "component_count": len(components),
        "classification_values": sorted(CLASSIFICATIONS),
        "components": components,
        "removed_components": decisions["removed_components"],
    }


def write_inventory() -> None:
    rendered = json.dumps(build_payload(), ensure_ascii=False, indent=2) + "\n"
    INVENTORY.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=".component-inventory.", dir=INVENTORY.parent, text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(rendered)
            handle.flush()
            os.fsync(handle.fileno())
        Path(temporary_name).replace(INVENTORY)
    finally:
        Path(temporary_name).unlink(missing_ok=True)


def verify_inventory() -> list[str]:
    try:
        found = json.loads(INVENTORY.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [f"Inventar unlesbar: {exc}"]
    snapshots = {
        str(item.get("path")): item
        for item in found.get("components", [])
        if isinstance(item, dict) and item.get("path")
    }
    expected = build_payload(preserve_snapshots=snapshots)
    if found != expected:
        found_paths = {
            str(item.get("path"))
            for item in found.get("components", [])
            if isinstance(item, dict)
        }
        expected_paths = {str(item.get("path")) for item in expected["components"]}
        detail = []
        if missing := sorted(expected_paths - found_paths):
            detail.append("fehlend: " + ", ".join(missing[:10]))
        if extra := sorted(found_paths - expected_paths):
            detail.append("zusaetzlich: " + ", ".join(extra[:10]))
        suffix = " (" + "; ".join(detail) + ")" if detail else ""
        return ["Komponenten-Inventar ist nicht deterministisch aktuell" + suffix]
    for item in found["components"]:
        changed = item.get("last_git_change")
        if changed is not None and not re.fullmatch(r"\d{4}-\d{2}-\d{2}T.+", str(changed)):
            return [f"Ungueltiges Git-Datum fuer {item['path']}"]
    removed = {
        path
        for item in found["removed_components"]
        for path in item.get("paths", [])
    }
    existing = set(source_paths())
    if overlap := sorted(removed & existing):
        return ["Als entfernt dokumentierte Pfade existieren noch: " + ", ".join(overlap)]
    return []


def main() -> int:
    parser = argparse.ArgumentParser(description="M6-Komponenten- und Legacy-Inventar")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("generate")
    commands.add_parser("verify")
    args = parser.parse_args()
    if args.command == "generate":
        write_inventory()
    errors = verify_inventory()
    if errors:
        print(json.dumps({"ok": False, "errors": errors}, ensure_ascii=False, indent=2))
        return 1
    payload = json.loads(INVENTORY.read_text(encoding="utf-8"))
    print(json.dumps({
        "ok": True,
        "components": payload["component_count"],
        "removed_groups": len(payload["removed_components"]),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
