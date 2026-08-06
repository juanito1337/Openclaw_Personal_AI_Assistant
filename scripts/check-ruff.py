#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]


def _relative_filename(value: str) -> str:
    path = Path(value)
    resolved = path.resolve() if path.is_absolute() else (ROOT / path).resolve()
    try:
        return resolved.relative_to(ROOT).as_posix()
    except ValueError:
        return resolved.as_posix()


def _line_digest(filename: str, row: int) -> str:
    path = Path(filename)
    resolved = path if path.is_absolute() else ROOT / path
    try:
        lines = resolved.read_bytes().splitlines()
        line = lines[row - 1]
    except (OSError, IndexError):
        line = b"<missing-source-line>"
    return hashlib.sha256(line).hexdigest()


def fingerprints(diagnostics: list[dict[str, Any]]) -> Counter[tuple[str, str, str, str]]:
    found: Counter[tuple[str, str, str, str]] = Counter()
    for item in diagnostics:
        filename = str(item["filename"])
        row = int(item["location"]["row"])
        found[
            (
                _relative_filename(filename),
                str(item["code"]),
                str(item["message"]),
                _line_digest(filename, row),
            )
        ] += 1
    return found


def _load(path: Path) -> tuple[Counter[tuple[str, str, str, str]], str]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != 1:
        raise ValueError(f"Unbekannte Ruff-Baseline-Version: {payload.get('schema_version')!r}")
    found = Counter(
        {
            (item["path"], item["code"], item["message"], item["line_sha256"]): int(
                item.get("count", 1)
            )
            for item in payload["fingerprints"]
        }
    )
    if int(payload.get("error_count", -1)) != sum(found.values()):
        raise ValueError("Ruff-Baseline enthaelt eine inkonsistente Fehlerzahl")
    return found, str(payload.get("ruff_version") or "")


def _write(
    path: Path,
    found: Counter[tuple[str, str, str, str]],
    version: str,
) -> None:
    payload = {
        "schema_version": 1,
        "ruff_version": version,
        "policy": (
            "Only these exact legacy path/code/message/source-line fingerprints are accepted; "
            "every new or changed finding fails."
        ),
        "error_count": sum(found.values()),
        "fingerprints": [
            {
                "path": key[0],
                "code": key[1],
                "message": key[2],
                "line_sha256": key[3],
                "count": count,
            }
            for key, count in sorted(found.items())
        ],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Ruff with an exact, non-growing M0 legacy baseline")
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--write-baseline", action="store_true")
    parser.add_argument("paths", nargs="+")
    args = parser.parse_args()

    result = subprocess.run(
        [sys.executable, "-m", "ruff", "check", "--no-cache", "--output-format=json", *args.paths],
        check=False,
        text=True,
        capture_output=True,
    )
    if result.returncode not in {0, 1}:
        print(result.stderr or result.stdout, file=sys.stderr, end="")
        return 2
    try:
        diagnostics = json.loads(result.stdout or "[]")
    except json.JSONDecodeError as exc:
        print(f"Ruff-Ausgabe ist kein gueltiges JSON: {exc}", file=sys.stderr)
        return 2
    found = fingerprints(diagnostics)
    version_result = subprocess.run(
        [sys.executable, "-m", "ruff", "--version"],
        check=True,
        text=True,
        capture_output=True,
    )
    version = version_result.stdout.strip()
    if args.write_baseline:
        _write(args.baseline, found, version)
        print(f"Ruff-Baseline mit {sum(found.values())} Altbefunden geschrieben: {args.baseline}")
        return 0

    try:
        allowed, baseline_version = _load(args.baseline)
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        print(f"Ruff-Baseline ist ungueltig: {exc}", file=sys.stderr)
        return 2
    if baseline_version != version:
        print(
            f"Ruff-Version stimmt nicht mit der Baseline ueberein: {version!r} != {baseline_version!r}",
            file=sys.stderr,
        )
        return 2
    unexpected = found - allowed
    if unexpected:
        print("Neue oder veraenderte, nicht baselinierte Ruff-Befunde:", file=sys.stderr)
        for (path, code, message, line_digest), count in sorted(unexpected.items()):
            print(
                f"- {path} [{code}] x{count}, Zeilen-SHA {line_digest[:12]}: {message}",
                file=sys.stderr,
            )
        return 1
    resolved = allowed - found
    print(
        f"Ruff: keine neuen Befunde; {sum(found.values())} bekannte Altbefunde, "
        f"{sum(resolved.values())} seit der Baseline behoben."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
