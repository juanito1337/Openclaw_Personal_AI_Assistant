#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
import zipfile
from pathlib import Path, PurePosixPath

RUNTIME_SUFFIXES = {
    ".db",
    ".eml",
    ".lock",
    ".log",
    ".msg",
    ".npy",
    ".npz",
    ".sqlite",
    ".sqlite3",
    ".vec",
    ".vector",
}
DOCUMENT_SUFFIXES = {".pdf"}
RUNTIME_NAMES = {
    ".env",
    "config.json",
    "config.toml",
    "config.yaml",
    "config.yml",
    "embedding-cache.json",
    "embeddings.json",
    "mail-index.json",
    "policies.json",
    "policies.toml",
    "policies.yaml",
    "policies.yml",
    "resources.json",
    "resources.toml",
    "resources.yaml",
    "resources.yml",
    "rules.json",
    "rules.toml",
    "rules.yaml",
    "rules.yml",
    "tools.json",
    "tools.toml",
    "tools.yaml",
    "tools.yml",
}
SENSITIVE_NAMES = {
    ".netrc",
    ".npmrc",
    ".pypirc",
    "credentials.json",
    "credentials.toml",
    "id_dsa",
    "id_ed25519",
    "id_ecdsa",
    "id_rsa",
    "secrets.json",
    "secrets.toml",
}
SENSITIVE_SUFFIXES = {".jks", ".key", ".kdbx", ".p12", ".pem", ".pfx"}
SECRET_PATTERNS = {
    "private key": re.compile(rb"-----BEGIN (?:[A-Z0-9 ]+ )?PRIVATE KEY-----"),
    "GitHub token": re.compile(rb"\bgh[pousr]_[A-Za-z0-9_]{30,}\b"),
    "OpenAI-style key": re.compile(rb"\bsk-[A-Za-z0-9_-]{32,}\b"),
    "AWS access key": re.compile(rb"\bAKIA[0-9A-Z]{16}\b"),
}
SECRET_ASSIGNMENT = re.compile(
    rb"(?im)^\s*(?:export\s+)?[\"']?(?P<key>"
    rb"[A-Z0-9_-]*(?:PASSWORD|PASSWD|API_KEY|ACCESS_TOKEN|CLIENT_SECRET|PRIVATE_KEY|SECRET)[A-Z0-9_-]*|"
    rb"password|passwd|api[_-]?key|access[_-]?token|client[_-]?secret|private[_-]?key|secret"
    rb")[\"']?\s*[:=]\s*(?P<value>\"[^\"\r\n]*\"|'[^'\r\n]*'|[^\s#,}\]]+)"
)
SECRET_PATTERN_FIXTURES = {
    "tests/test_artifact_hygiene.py",
    "tests/test_performance_telemetry.py",
}


def _is_example(path: PurePosixPath) -> bool:
    name = path.name.casefold()
    return name.endswith(".example") or ".example." in name or name == ".env.example"


def _contains_parts(path: PurePosixPath, expected: tuple[str, ...]) -> bool:
    parts = tuple(part.casefold() for part in path.parts)
    width = len(expected)
    return any(parts[index : index + width] == expected for index in range(len(parts) - width + 1))


def _has_secret_assignment(name: str, content: bytes) -> bool:
    path = PurePosixPath(name)
    lowered = path.name.casefold()
    config_like = path.suffix.casefold() in {".env", ".json", ".sh", ".toml", ".yaml", ".yml"}
    config_like = config_like or lowered == ".env" or lowered.startswith(".env.")
    if not config_like:
        return False
    for match in SECRET_ASSIGNMENT.finditer(content):
        key = match.group("key").decode("ascii", errors="ignore").casefold()
        if key.endswith(("_dir", "_env", "_file", "_name", "_path")):
            continue
        value = match.group("value").strip().strip(b"\"'").lower()
        if not value or value in {b"false", b"none", b"null"}:
            continue
        if value.startswith((b"$", b"<")) or any(
            marker in value for marker in (b"changeme", b"dummy", b"example", b"placeholder")
        ):
            continue
        return True
    return False


def forbidden_path(value: str) -> str | None:
    normalized = value[2:] if value.startswith("./") else value
    path = PurePosixPath(normalized.lstrip("/"))
    lowered = path.name.casefold()
    example = _is_example(path)
    if (lowered == ".env" or lowered.startswith(".env.") or path.suffix.casefold() == ".env") and not example:
        return "produktive Konfiguration"
    if lowered in RUNTIME_NAMES and not example:
        return "produktive Konfiguration"
    if lowered in SENSITIVE_NAMES or (path.suffix.casefold() in SENSITIVE_SUFFIXES and not example):
        return "private Schluessel-/Zugangsdaten"
    if path.suffix.casefold() in RUNTIME_SUFFIXES or ".sqlite3-" in lowered:
        return "Laufzeitdaten"
    if path.suffix.casefold() in DOCUMENT_SUFFIXES:
        return "Dokumentinhalt"
    if _contains_parts(path, ("mail_agent", "data")) or _contains_parts(
        path, ("personal_assistant", "data")
    ):
        return "Laufzeitdaten"
    if "__pycache__" in path.parts or lowered.endswith((".pyc", ".pyo")):
        return "Python-Laufzeitartefakt"
    if any(
        part.casefold()
        in {
            ".mypy_cache",
            ".openclaw",
            ".pytest_cache",
            ".ruff_cache",
            ".tools",
            ".venv",
            "backups",
            "build",
            "container_jobs",
            "container_logs",
            "dist",
            "htmlcov",
            "runtime",
            "venv",
        }
        or part.casefold().endswith(".egg-info")
        for part in path.parts
    ):
        return "Laufzeitverzeichnis"
    return None


def inspect_files(files: list[tuple[str, bytes]]) -> list[str]:
    issues: list[str] = []
    for name, content in files:
        reason = forbidden_path(name)
        if reason:
            issues.append(f"{name}: {reason}")
        # This exact regression fixture contains an intentionally incomplete key marker.
        if PurePosixPath(name).as_posix() not in SECRET_PATTERN_FIXTURES:
            for label, pattern in SECRET_PATTERNS.items():
                if pattern.search(content):
                    issues.append(f"{name}: moegliches Secret ({label})")
            if _has_secret_assignment(name, content):
                issues.append(f"{name}: moegliches Secret (nichtleere Secret-Zuweisung)")
    return issues


def inspect_wheel(path: Path) -> list[str]:
    with zipfile.ZipFile(path) as archive:
        files = [
            (name, archive.read(name))
            for name in archive.namelist()
            if not name.endswith("/")
        ]
    return inspect_files(files)


def inspect_tree(root: Path) -> list[str]:
    files: list[tuple[str, bytes]] = []
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        relative = path.relative_to(root).as_posix()
        try:
            content = path.read_bytes()
        except OSError as exc:
            files.append((relative, f"UNREADABLE:{exc}".encode()))
        else:
            files.append((relative, content))
    return inspect_files(files)


def inspect_image_root(root: Path) -> list[str]:
    """Inspect every product-owned image path without treating base-OS config as product data."""
    issues: list[str] = []
    owned_roots = (
        root / "opt/openclaw-agent",
        root / "home/node/.openclaw",
        root / "etc/openclaw-agent",
        root / "etc/openclaw-env",
        root / "run/openclaw-env",
        root / "run/openclaw-secrets",
    )
    application = owned_roots[0]
    if not application.is_dir():
        issues.append("opt/openclaw-agent: Anwendungswurzel fehlt")
    for owned in owned_roots:
        if owned.exists():
            issues.extend(inspect_tree(owned))
    return issues


def main() -> int:
    parser = argparse.ArgumentParser(description="Wheel-/Image-Inhalt auf Laufzeitdaten und Secrets pruefen")
    commands = parser.add_subparsers(dest="command", required=True)
    wheel = commands.add_parser("wheel")
    wheel.add_argument("path", type=Path)
    tree = commands.add_parser("tree")
    tree.add_argument("path", type=Path)
    image_root = commands.add_parser("image-root")
    image_root.add_argument("path", type=Path)
    args = parser.parse_args()
    if args.command == "wheel":
        issues = inspect_wheel(args.path)
    elif args.command == "image-root":
        issues = inspect_image_root(args.path)
    else:
        issues = inspect_tree(args.path)
    if issues:
        print("Artefaktpruefung fehlgeschlagen:")
        for issue in issues:
            print(f"- {issue}")
        return 1
    print(f"Artefaktpruefung erfolgreich: {args.path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
