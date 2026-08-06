#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

ERROR = re.compile(
    r"^(?P<path>[^:]+):\d+: error: (?P<message>.*?)(?:  \[(?P<code>[-a-z0-9]+)\])?$"
)


def _fingerprints(output: str) -> Counter[tuple[str, str, str]]:
    found: Counter[tuple[str, str, str]] = Counter()
    for line in output.splitlines():
        match = ERROR.match(line)
        if match:
            found[(match.group("path"), match.group("code") or "unknown", match.group("message"))] += 1
    return found


def _load(path: Path) -> Counter[tuple[str, str, str]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return Counter(
        {
            (item["path"], item["code"], item["message"]): int(item.get("count", 1))
            for item in payload["fingerprints"]
        }
    )


def _write(path: Path, found: Counter[tuple[str, str, str]], version: str) -> None:
    payload = {
        "schema_version": 1,
        "mypy_version": version,
        "policy": "Only these exact legacy path/code/message fingerprints are accepted; every new error fails.",
        "error_count": sum(found.values()),
        "fingerprints": [
            {"path": key[0], "code": key[1], "message": key[2], "count": count}
            for key, count in sorted(found.items())
        ],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="mypy with an exact, non-growing M0 legacy baseline")
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--write-baseline", action="store_true")
    parser.add_argument("paths", nargs="+")
    args = parser.parse_args()

    result = subprocess.run(
        [sys.executable, "-m", "mypy", *args.paths],
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    print(result.stdout, end="")
    found = _fingerprints(result.stdout)
    version_result = subprocess.run(
        [sys.executable, "-m", "mypy", "--version"],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
    )
    version = version_result.stdout.strip()
    if result.returncode not in {0, 1} or (result.returncode == 1 and not found):
        print("mypy konnte nicht als normale Typpruefung ausgewertet werden.", file=sys.stderr)
        return 2
    if args.write_baseline:
        _write(args.baseline, found, version)
        print(f"mypy-Baseline mit {sum(found.values())} exakten Altbefunden geschrieben: {args.baseline}")
        return 0

    allowed = _load(args.baseline)
    unexpected = found - allowed
    if unexpected:
        print("Neue, nicht baselinierte mypy-Befunde:", file=sys.stderr)
        for (path, code, message), count in sorted(unexpected.items()):
            print(f"- {path} [{code}] x{count}: {message}", file=sys.stderr)
        return 1
    resolved = allowed - found
    print(
        f"mypy: keine neuen Befunde; {sum(found.values())} bekannte Altbefunde, "
        f"{sum(resolved.values())} seit der Baseline behoben."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
