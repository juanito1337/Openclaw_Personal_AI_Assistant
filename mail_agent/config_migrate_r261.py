from __future__ import annotations

import argparse
import json
import os
import re
import tempfile
from pathlib import Path


_SECTION_RE = re.compile(r"^\s*\[([^]]+)]\s*(?:#.*)?$")
_KEY_RE = re.compile(r"^(\s*)([A-Za-z0-9_]+)(\s*=\s*)(.*?)(\s*(?:#.*)?)$")

# Only exact R25/R26 defaults are replaced. Deliberate user tuning is preserved.
_REPLACEMENTS = {
    "batch_size": ({"5"}, "3"),
    "batch_prefetch": ({"15"}, "9"),
    "batch_retry_timeout_seconds": ({"180"}, "300"),
    "batch_max_split_depth": ({"1"}, "2"),
    "batch_max_total_chars": ({"28000", "28_000"}, "18000"),
    "batch_adaptive_target_chars": ({"18000", "18_000"}, "14000"),
    "parallel_requests": ({"2"}, "1"),
    "background_burst": ({"true"}, "false"),
}

_DEFAULTS = [
    ("single_retry_num_predict", "1024"),
]


def _atomic_write(path: Path, text: str) -> None:
    mode = path.stat().st_mode & 0o777
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent), text=True)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, mode)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def migrate_mail_config(path: Path) -> dict[str, object]:
    """Apply the conservative R26.1 Ollama reliability profile."""
    path = Path(path).expanduser().resolve()
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)

    start: int | None = None
    end = len(lines)
    for index, line in enumerate(lines):
        match = _SECTION_RE.match(line.rstrip("\r\n"))
        if not match:
            continue
        section = match.group(1).strip().casefold()
        if section == "ollama":
            start = index + 1
            continue
        if start is not None:
            end = index
            break
    if start is None:
        raise ValueError("Abschnitt [ollama] fehlt in der Mail-Agent-Konfiguration")

    seen: set[str] = set()
    changes: list[dict[str, str]] = []
    for index in range(start, end):
        raw = lines[index]
        newline = "\r\n" if raw.endswith("\r\n") else "\n" if raw.endswith("\n") else ""
        content = raw[:-len(newline)] if newline else raw
        match = _KEY_RE.match(content)
        if not match:
            continue
        key = match.group(2)
        seen.add(key)
        replacement = _REPLACEMENTS.get(key)
        if replacement is None:
            continue
        accepted, target = replacement
        current = match.group(4).strip()
        if current.casefold() not in {value.casefold() for value in accepted}:
            continue
        lines[index] = f"{match.group(1)}{key}{match.group(3)}{target}{match.group(5)}{newline}"
        changes.append({"key": key, "from": current, "to": target})

    missing = [(key, value) for key, value in _DEFAULTS if key not in seen]
    if missing:
        insertion: list[str] = []
        if end > start and lines[end - 1].strip():
            insertion.append("\n")
        insertion.append("# R26.1: begrenzter Retry nur bei nachweislich abgeschnittener Einzel-JSON-Antwort.\n")
        for key, value in missing:
            insertion.append(f"{key} = {value}\n")
            changes.append({"key": key, "from": "<missing>", "to": value})
        lines[end:end] = insertion

    updated = "".join(lines)
    changed = updated != text
    if changed:
        _atomic_write(path, updated)
    return {"ok": True, "changed": changed, "changes": changes, "path": str(path)}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Migriert Ollama-JSON- und Timeout-Schutz auf R26.1")
    parser.add_argument("path", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = migrate_mail_config(args.path)
    except (OSError, ValueError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
