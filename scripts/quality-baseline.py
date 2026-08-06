#!/usr/bin/env python3
from __future__ import annotations

import argparse
import ast
import importlib.metadata
import json
import platform
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

CRITICAL_MODULES = (
    "mail_agent/assistant_bridge.py",
    "personal_assistant/actions.py",
    "personal_assistant/antivirus.py",
    "personal_assistant/job_control.py",
    "personal_assistant/policy.py",
    "personal_assistant/release.py",
    "personal_assistant/source_manifest.py",
)


def _version(command: list[str]) -> str | None:
    try:
        result = subprocess.run(command, check=True, text=True, capture_output=True)
    except (OSError, subprocess.CalledProcessError):
        return None
    return (result.stdout or result.stderr).strip().splitlines()[0]


def _shellcheck_version() -> str | None:
    try:
        result = subprocess.run(
            ["shellcheck", "--version"], check=True, text=True, capture_output=True
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    for line in result.stdout.splitlines():
        if line.startswith("version:"):
            return line.removeprefix("version:").strip()
    return None


def _test_result(path: Path) -> dict[str, Any]:
    root = ET.parse(path).getroot()
    suite = root if root.tag == "testsuite" else root.find("testsuite")
    if suite is None:
        raise ValueError(f"Keine testsuite in {path}")
    return {
        "collected": int(suite.attrib.get("tests", 0)),
        "failures": int(suite.attrib.get("failures", 0)),
        "errors": int(suite.attrib.get("errors", 0)),
        "skipped": int(suite.attrib.get("skipped", 0)),
        "duration_seconds": float(suite.attrib.get("time", 0.0)),
    }


def _coverage(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    files = payload.get("files", {})
    critical = {
        name: files.get(name, {}).get("summary", {}).get("percent_covered")
        for name in CRITICAL_MODULES
    }
    totals = payload["totals"]
    branch_total = int(totals.get("num_branches") or 0)
    branch_covered = int(totals.get("covered_branches") or 0)
    return {
        "overall_percent": payload["totals"]["percent_covered"],
        "branch_percent": (100.0 * branch_covered / branch_total) if branch_total else None,
        "critical_modules_percent": critical,
    }


def _source_sizes(root: Path) -> dict[str, Any]:
    modules: list[dict[str, Any]] = []
    functions: list[dict[str, Any]] = []
    for folder in (root / "mail_agent", root / "personal_assistant", root / "docker"):
        for path in sorted(folder.rglob("*.py")):
            relative = path.relative_to(root).as_posix()
            text = path.read_text(encoding="utf-8")
            modules.append({"path": relative, "lines": len(text.splitlines())})
            tree = ast.parse(text, filename=relative)
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.end_lineno:
                    functions.append(
                        {
                            "path": relative,
                            "name": node.name,
                            "lines": node.end_lineno - node.lineno + 1,
                            "line": node.lineno,
                        }
                    )
    return {
        "largest_modules": sorted(modules, key=lambda item: (-item["lines"], item["path"]))[:10],
        "largest_functions": sorted(
            functions,
            key=lambda item: (-item["lines"], item["path"], item["line"]),
        )[:10],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Reproduzierbare M0-Qualitaetsbaseline")
    parser.add_argument("--coverage", type=Path, default=Path("build/coverage.json"))
    parser.add_argument("--junit", type=Path, default=Path("build/pytest.xml"))
    parser.add_argument("--output", type=Path, default=Path("build/m0-baseline.json"))
    parser.add_argument("--image-bytes", type=int)
    parser.add_argument("--image-build-seconds", type=float)
    parser.add_argument("--image-startup-ms", type=float)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    payload = {
        "schema_version": 1,
        "tests": _test_result(root / args.junit),
        "coverage": _coverage(root / args.coverage),
        "source": _source_sizes(root),
        "container": {
            "image_bytes": args.image_bytes,
            "build_seconds": args.image_build_seconds,
            "cli_cold_start_ms": args.image_startup_ms,
            "measurement": "CI Docker runner; null when no daemon measurement was supplied",
        },
        "tools": {
            "python": platform.python_version(),
            "pytest": _version([sys.executable, "-m", "pytest", "--version"]),
            "pytest_cov": importlib.metadata.version("pytest-cov"),
            "coverage": _version([sys.executable, "-m", "coverage", "--version"]),
            "ruff": _version([sys.executable, "-m", "ruff", "--version"]),
            "mypy": _version([sys.executable, "-m", "mypy", "--version"]),
            "build": _version([sys.executable, "-m", "build", "--version"]),
            "pip": _version([sys.executable, "-m", "pip", "--version"]),
            "setuptools": importlib.metadata.version("setuptools"),
            "shellcheck": _shellcheck_version(),
            "hadolint": _version(["hadolint", "--version"]),
            "docker": _version(["docker", "--version"]),
            "compose": _version(["docker", "compose", "version"]),
            "git": _version(["git", "--version"]),
        },
    }
    output = root / args.output
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
