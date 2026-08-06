#!/usr/bin/env python3
"""Trace persistent file access and compare it with the M3 role contract."""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONTRACT = ROOT / "docs/architecture/state-access.json"
PATH_RE = re.compile(r'"((?:[^"\\]|\\.)+)"')
WRITE_FLAGS = ("O_WRONLY", "O_RDWR", "O_CREAT", "O_TRUNC", "O_APPEND")


def parse_trace(text: str) -> list[dict[str, str]]:
    observed: dict[tuple[str, str], dict[str, str]] = {}
    for line in text.splitlines():
        if not any(call in line for call in ("open(", "openat(", "creat(", "rename(", "unlink(")):
            continue
        matches = list(PATH_RE.finditer(line))
        if not matches:
            continue
        mode = "write" if any(flag in line for flag in WRITE_FLAGS) or any(
            call in line for call in ("creat(", "rename(", "unlink(")
        ) else "read"
        selected = matches if "rename(" in line else matches[:1]
        for match in selected:
            path = bytes(match.group(1), "utf-8").decode("unicode_escape")
            observed[(path, mode)] = {"path": path, "mode": mode}
    return [observed[key] for key in sorted(observed)]


def evaluate(
    role: str,
    observations: list[dict[str, str]],
    contract: dict[str, Any],
) -> list[dict[str, str]]:
    role_access = contract["roles"][role]
    roots = contract["roots"]
    violations: list[dict[str, str]] = []
    for item in observations:
        path = Path(item["path"])
        matched = [name for name, root in roots.items() if path.is_relative_to(root)]
        if not matched:
            continue
        root_name = max(matched, key=lambda name: len(roots[name]))
        permission = role_access.get(root_name)
        if permission is None or (item["mode"] == "write" and permission != "rw"):
            violations.append({**item, "root": root_name, "permission": permission or "none"})
    return violations


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--role", required=True)
    parser.add_argument("--contract", type=Path, default=DEFAULT_CONTRACT)
    parser.add_argument("--trace-file", type=Path)
    parser.add_argument(
        "--root",
        action="append",
        default=[],
        metavar="NAME=PATH",
        help="override one contract root for an isolated fixture trace",
    )
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    contract = json.loads(args.contract.read_text(encoding="utf-8"))
    for value in args.root:
        name, separator, path = value.partition("=")
        if not separator or name not in contract["roots"] or not Path(path).is_absolute():
            parser.error(f"ungueltiger Root-Override: {value}")
        contract["roots"][name] = str(Path(path).resolve())
    if args.role not in contract["roles"]:
        parser.error(f"unbekannte Rolle: {args.role}")
    if args.trace_file:
        trace = args.trace_file.read_text(encoding="utf-8", errors="replace")
        returncode = 0
    else:
        command = args.command[1:] if args.command[:1] == ["--"] else args.command
        if not command:
            parser.error("Kommando oder --trace-file erforderlich")
        if shutil.which("strace") is None:
            raise SystemExit("strace fehlt; instrumentierte Zustandsinventur nicht moeglich")
        completed = subprocess.run(
            ["strace", "-f", "-qq", "-e", "trace=%file", *command],
            text=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            check=False,
        )
        trace = completed.stderr
        returncode = completed.returncode
    observations = parse_trace(trace)
    violations = evaluate(args.role, observations, contract)
    report = {
        "ok": returncode == 0 and not violations,
        "role": args.role,
        "command_returncode": returncode,
        "observations": observations,
        "violations": violations,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
