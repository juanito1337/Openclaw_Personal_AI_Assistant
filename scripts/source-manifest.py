#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.dont_write_bytecode = True

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from personal_assistant.source_manifest import verify_source_manifest, write_source_manifest  # noqa: E402


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Deterministisches OpenClaw-Quellmanifest")
    commands = result.add_subparsers(dest="command", required=True)
    commands.add_parser("generate", help="Manifest atomar aus der exakten Git-Quellmenge erzeugen")
    commands.add_parser("verify", help="Dateimenge und SHA-256-Werte exakt verifizieren")
    return result


def main() -> int:
    args = parser().parse_args()
    root = ROOT
    if args.command == "generate":
        target = write_source_manifest(root)
        report = verify_source_manifest(root)
        print(json.dumps(report.as_dict(), ensure_ascii=False, indent=2))
        return 0 if target.is_file() and report.ok else 1
    report = verify_source_manifest(root)
    print(json.dumps(report.as_dict(), ensure_ascii=False, indent=2))
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
