#!/usr/bin/env python3
"""Build the privacy-minimal, local-only M16 stabilization baseline."""

from __future__ import annotations

import argparse
import ast
import importlib.metadata
import json
import math
import os
import platform
import re
import statistics
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "build/m16-baseline.json"
DEFAULT_RISKS = ROOT / "docs/architecture/m16-risk-register.json"
MINIMUM_SAMPLES = 3


def _command_text(command: list[str]) -> str:
    return " ".join(command)


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        raise ValueError("Perzentil benoetigt mindestens einen Messwert")
    ordered = sorted(values)
    rank = max(0, math.ceil(percentile * len(ordered)) - 1)
    return round(ordered[rank], 3)


def _timing(values: list[float]) -> dict[str, Any]:
    return {
        "unit": "ms",
        "samples": len(values),
        "p50": round(statistics.median(values), 3),
        "p95": _percentile(values, 0.95),
        "minimum": round(min(values), 3),
        "maximum": round(max(values), 3),
    }


def _atomic_write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        Path(temporary_name).replace(path)
    finally:
        Path(temporary_name).unlink(missing_ok=True)


def _run(command: list[str]) -> subprocess.CompletedProcess[str]:
    path = os.pathsep.join(
        (
            str(ROOT / ".venv/bin"),
            str(ROOT / ".tools/bin"),
            os.environ.get("PATH", ""),
        )
    )
    try:
        return subprocess.run(
            command,
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
            env={**os.environ, "PATH": path, "PYTHONDONTWRITEBYTECODE": "1"},
        )
    except OSError as exc:
        return subprocess.CompletedProcess(command, 127, stdout="", stderr=type(exc).__name__)


def _repeat(
    command: list[str],
    *,
    samples: int,
    parser: Callable[[str], dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any]]:
    durations: list[float] = []
    parsed: dict[str, Any] | None = None
    exit_codes: list[int] = []
    for _index in range(samples):
        started = time.perf_counter()
        result = _run(command)
        durations.append((time.perf_counter() - started) * 1000)
        exit_codes.append(result.returncode)
        if result.returncode == 0:
            parsed = parser(result.stdout)
    if any(code != 0 for code in exit_codes) or parsed is None:
        raise RuntimeError(
            f"Baseline-Befehl fehlgeschlagen: {_command_text(command)}; exit_codes={exit_codes}"
        )
    return parsed, _timing(durations)


def _parse_json(text: str) -> dict[str, Any]:
    payload = json.loads(text)
    if not isinstance(payload, dict):
        raise ValueError("Befehlsausgabe ist kein JSON-Objekt")
    return payload


def _parse_empty(_text: str) -> dict[str, Any]:
    return {"ok": True}


def _environment_id() -> str:
    return "-".join(
        (
            "local-development",
            platform.system().casefold(),
            platform.machine().casefold(),
            f"python-{platform.python_version()}",
        )
    )


def _evidence(
    *,
    command: str,
    source_revision: str,
    branch: str,
    measured_at: str,
    samples: int,
) -> dict[str, Any]:
    return {
        "command": command,
        "source_revision": source_revision,
        "branch": branch,
        "environment": _environment_id(),
        "measured_at": measured_at,
        "sample_count": samples,
        "stored_output_policy": "allowlisted-aggregate-fields-only",
    }


def _measured(
    value: dict[str, Any],
    *,
    command: str,
    source_revision: str,
    branch: str,
    measured_at: str,
    samples: int = 1,
) -> dict[str, Any]:
    return {
        "status": "measured",
        "value": value,
        "evidence": _evidence(
            command=command,
            source_revision=source_revision,
            branch=branch,
            measured_at=measured_at,
            samples=samples,
        ),
    }


def _not_measured(
    *,
    reason: str,
    command: str,
    source_revision: str,
    branch: str,
    measured_at: str,
) -> dict[str, Any]:
    return {
        "status": "not-measured",
        "reason": reason,
        "value": None,
        "evidence": _evidence(
            command=command,
            source_revision=source_revision,
            branch=branch,
            measured_at=measured_at,
            samples=0,
        ),
    }


def _git_value(arguments: list[str]) -> str:
    result = _run(["git", *arguments])
    if result.returncode != 0:
        raise RuntimeError(f"Git-Befehl fehlgeschlagen: {arguments}")
    return result.stdout.strip()


def _junit(path: Path) -> dict[str, Any]:
    root = ET.parse(path).getroot()
    suite = root if root.tag == "testsuite" else root.find("testsuite")
    if suite is None:
        raise ValueError(f"Keine testsuite in {path}")
    return {
        "executed": int(suite.attrib.get("tests", 0)),
        "failures": int(suite.attrib.get("failures", 0)),
        "errors": int(suite.attrib.get("errors", 0)),
        "skipped": int(suite.attrib.get("skipped", 0)),
        "duration_seconds": round(float(suite.attrib.get("time", 0.0)), 3),
    }


def _coverage(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    totals = payload["totals"]
    files = payload.get("files", {})
    critical = (
        "mail_agent/assistant_bridge.py",
        "personal_assistant/actions.py",
        "personal_assistant/antivirus.py",
        "personal_assistant/job_control.py",
        "personal_assistant/policy.py",
        "personal_assistant/release.py",
        "personal_assistant/source_manifest.py",
    )
    return {
        "line_percent": round(float(totals["percent_covered"]), 3),
        "branch_percent": round(float(totals.get("percent_branches_covered") or 0.0), 3),
        "covered_lines": int(totals["covered_lines"]),
        "statements": int(totals["num_statements"]),
        "critical_modules_percent": {
            name: round(float(files.get(name, {}).get("summary", {}).get("percent_covered") or 0.0), 3)
            for name in critical
        },
    }


def _source_complexity() -> dict[str, Any]:
    modules: list[dict[str, Any]] = []
    functions: list[dict[str, Any]] = []
    for folder in (ROOT / "mail_agent", ROOT / "personal_assistant", ROOT / "docker"):
        for path in sorted(folder.rglob("*.py")):
            text = path.read_text(encoding="utf-8")
            relative = path.relative_to(ROOT).as_posix()
            modules.append({"path": relative, "lines": len(text.splitlines())})
            for node in ast.walk(ast.parse(text, filename=relative)):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.end_lineno:
                    functions.append(
                        {
                            "path": relative,
                            "name": node.name,
                            "line": node.lineno,
                            "lines": node.end_lineno - node.lineno + 1,
                        }
                    )
    return {
        "python_lines": sum(item["lines"] for item in modules),
        "largest_modules": sorted(modules, key=lambda row: (-row["lines"], row["path"]))[:10],
        "largest_functions": sorted(
            functions,
            key=lambda row: (-row["lines"], row["path"], row["line"]),
        )[:10],
    }


def _version(command: list[str]) -> str | None:
    result = _run(command)
    if result.returncode != 0:
        return None
    lines = (result.stdout or result.stderr).strip().splitlines()
    return lines[0].replace(str(ROOT), "<repo>") if lines else None


def _shellcheck_version() -> str | None:
    result = _run(["shellcheck", "--version"])
    if result.returncode != 0:
        return None
    for line in result.stdout.splitlines():
        if line.startswith("version:"):
            return line.partition(":")[2].strip()
    return None


def _tool_versions() -> dict[str, Any]:
    return {
        "python": platform.python_version(),
        "pytest": _version([sys.executable, "-m", "pytest", "--version"]),
        "coverage": _version([sys.executable, "-m", "coverage", "--version"]),
        "ruff": _version([sys.executable, "-m", "ruff", "--version"]),
        "mypy": _version([sys.executable, "-m", "mypy", "--version"]),
        "build": _version([sys.executable, "-m", "build", "--version"]),
        "shellcheck": _shellcheck_version(),
        "hadolint": _version(["hadolint", "--version"]),
        "docker": _version(["docker", "--version"]),
        "compose": _version(["docker", "compose", "version"]),
        "git": _version(["git", "--version"]),
        "pytest_cov": importlib.metadata.version("pytest-cov"),
    }


def _static_analysis() -> dict[str, Any]:
    commands = {
        "ruff": [
            sys.executable,
            "scripts/check-ruff.py",
            "--baseline",
            "tests/ruff-baseline.json",
            "mail_agent",
            "personal_assistant",
            "docker",
            "tests",
            "scripts",
        ],
        "mypy": [
            sys.executable,
            "scripts/check-mypy.py",
            "--baseline",
            "tests/mypy-baseline.json",
            "mail_agent",
            "personal_assistant",
            "docker",
        ],
    }
    patterns = {
        "ruff": re.compile(r"(\d+) bekannte Altbefunde, (\d+) seit der Baseline behoben"),
        "mypy": re.compile(r"(\d+) bekannte Altbefunde, (\d+) seit der Baseline behoben"),
    }
    result: dict[str, Any] = {}
    for name, command in commands.items():
        completed = _run(command)
        match = patterns[name].search(completed.stdout)
        result[name] = {
            "ok": completed.returncode == 0,
            "known_findings": int(match.group(1)) if match else None,
            "resolved_since_baseline": int(match.group(2)) if match else None,
            "command": _command_text(command).replace(str(ROOT / ".venv/bin/python"), ".venv/bin/python"),
        }
        if completed.returncode != 0:
            raise RuntimeError(f"{name}-Pruefung fehlgeschlagen")
    return result


def _synthetic_mail(samples: int) -> dict[str, Any]:
    command = [
        sys.executable,
        "scripts/benchmark_mail_acceptance_m118.py",
        "--samples",
        str(samples),
        "--output",
        "build/m16-mail-synthetic.json",
    ]
    completed = _run(command)
    if completed.returncode != 0:
        raise RuntimeError("Synthetischer Mailbenchmark fehlgeschlagen")
    report = json.loads((ROOT / "build/m16-mail-synthetic.json").read_text(encoding="utf-8"))
    latency = report["lexical"]["latency"]
    return {
        "ok": bool(report["ok"]),
        "privacy": report["privacy"],
        "corpus_messages": int(report["corpus"]["messages"]),
        "evaluated_queries": int(report["corpus"]["queries"]),
        "cold_cache_ms": float(latency["cold_first_ms"]),
        "warm_cache_p50_ms": float(latency["p50_ms"]),
        "warm_cache_p95_ms": float(latency["p95_ms"]),
        "latency_samples": int(latency["samples"]),
        "mean_recall_at_10": float(report["lexical"]["quality"]["mean_recall_at_10"]),
        "mrr": float(report["lexical"]["quality"]["mrr"]),
        "semantic_model_selected": bool(report["semantic_contract"]["model_selected"]),
    }


def _risks(source_revision: str, measured_at: str) -> dict[str, Any]:
    rows = [
        (
            "M16-R01",
            "critical",
            "Release- und Deploymentevidenz koennen auseinanderlaufen",
            "Release Maintainers",
            "M16.1",
        ),
        (
            "M16-R02",
            "high",
            "Historische Betriebsmetriken widersprechen aktuellem Health",
            "Runtime Maintainers",
            "M16.2",
        ),
        (
            "M16-R03",
            "high",
            "Langer Vollsync blockiert bei Parallelitaet eins zeitkritische Arbeit",
            "Scheduler Maintainers",
            "M16.3",
        ),
        (
            "M16-R04",
            "high",
            "Persistierte Ressourcen-IDs koennen von aktiver Discovery abweichen",
            "Groupware Maintainers",
            "M16.4",
        ),
        (
            "M16-R05",
            "high",
            "Historischer OOM-Status und volle Swap-Nutzung sind nicht ursachenklar",
            "Runtime Maintainers",
            "M16.5",
        ),
        (
            "M16-R06",
            "medium",
            "Modellantworten dominieren die interaktive Gesamtlatenz",
            "Model Runtime Maintainers",
            "M16.5",
        ),
        (
            "M16-R07",
            "high",
            "Grosse Risikomodule besitzen ungleichmaessige direkte Testabdeckung",
            "Quality Maintainers",
            "M16.6",
        ),
        (
            "M16-R08",
            "medium",
            "Viele Lernmuster beruhen auf Einzelbeobachtungen oder Legacydaten",
            "Mail Domain Maintainers",
            "M16.7",
        ),
        (
            "M16-R09",
            "medium",
            "Rechnungs-Review und Registerabweichungen bleiben fachlich offen",
            "Records Maintainers",
            "M16.8",
        ),
        (
            "M16-R10",
            "medium",
            "Portfolio-Mappings und kostenlose Researchdaten sind unvollstaendig",
            "Portfolio Maintainers",
            "M16.8",
        ),
        (
            "M16-R11",
            "medium",
            "Semantische Suche ist nicht als Produktfaehigkeit aktiviert",
            "Architecture Maintainers",
            "M16.9",
        ),
        (
            "M16-R12",
            "high",
            "Gateway-Schreibmounts und Credentials vergroessern den Blast Radius",
            "Security Maintainers",
            "M16.9",
        ),
        (
            "M16-R13",
            "medium",
            "Lokales SQLite und Dateisystem begrenzen horizontale Skalierung",
            "Architecture Maintainers",
            "M16.9",
        ),
    ]
    return {
        "schema_version": 1,
        "milestone": "M16.0",
        "source_revision": source_revision,
        "measured_at": measured_at,
        "priority_order": ["critical", "high", "medium", "low"],
        "risks": [
            {
                "id": identifier,
                "severity": severity,
                "summary": summary,
                "user_impact": "service-quality-or-availability",
                "security_impact": "review-required" if severity in {"critical", "high"} else "bounded",
                "reproducibility": "baseline-or-documented-operational-observation",
                "owner": owner,
                "target_package": package,
                "status": "open",
            }
            for identifier, severity, summary, owner, package in rows
        ],
    }


def build_report(args: argparse.Namespace) -> tuple[dict[str, Any], dict[str, Any]]:
    if args.samples < MINIMUM_SAMPLES:
        raise ValueError(f"--samples muss mindestens {MINIMUM_SAMPLES} sein")
    measured_at = args.measured_at or datetime.now(UTC).isoformat(timespec="seconds")
    branch = _git_value(["branch", "--show-current"])
    source_revision = args.source_revision or _git_value(["rev-parse", "HEAD"])
    release = json.loads((ROOT / "RELEASE.json").read_text(encoding="utf-8"))
    version_payload, version_timing = _repeat(
        [str(ROOT / "scripts/assistant.sh"), "version", "--verify"],
        samples=args.samples,
        parser=_parse_json,
    )
    manifest_payload, manifest_timing = _repeat(
        [sys.executable, "scripts/source-manifest.py", "verify"],
        samples=args.samples,
        parser=_parse_json,
    )
    _empty, git_timing = _repeat(
        ["git", "status", "--short"],
        samples=args.samples,
        parser=_parse_empty,
    )
    quality = {
        "junit": _junit(ROOT / args.junit),
        "coverage": _coverage(ROOT / args.coverage),
        "collection_items": args.collection_items,
        "quality_gate": args.quality_gate_status,
        "static_analysis": _static_analysis() if args.run_static_analysis else None,
    }
    mail = _synthetic_mail(args.samples)
    measurements: dict[str, Any] = {
        "identity": _measured(
            {
                "source_revision": source_revision,
                "current_head": _git_value(["rev-parse", "HEAD"]),
                "branch": branch,
                "release_version": release["version"],
                "release": release["release"],
                "release_verified": bool(version_payload.get("ok")),
                "source_manifest_ok": bool(manifest_payload.get("ok")),
                "manifest_entries": int(manifest_payload.get("entry_count") or 0),
                "version_verify_timing": version_timing,
                "manifest_verify_timing": manifest_timing,
                "git_status_timing": git_timing,
            },
            command=(
                "./scripts/assistant.sh version --verify; "
                ".venv/bin/python scripts/source-manifest.py verify; git status --short"
            ),
            source_revision=source_revision,
            branch=branch,
            measured_at=measured_at,
            samples=args.samples,
        ),
        "quality": _measured(
            quality,
            command="./scripts/run-tests.sh; ./scripts/check-repo.sh",
            source_revision=source_revision,
            branch=branch,
            measured_at=measured_at,
        ),
        "source_complexity": _measured(
            _source_complexity(),
            command=".venv/bin/python scripts/benchmark_m16.py",
            source_revision=source_revision,
            branch=branch,
            measured_at=measured_at,
        ),
        "tool_versions": _measured(
            _tool_versions(),
            command="version commands listed by scripts/benchmark_m16.py",
            source_revision=source_revision,
            branch=branch,
            measured_at=measured_at,
        ),
        "mail_synthetic": _measured(
            mail,
            command=f".venv/bin/python scripts/benchmark_mail_acceptance_m118.py --samples {args.samples}",
            source_revision=source_revision,
            branch=branch,
            measured_at=measured_at,
            samples=int(mail["latency_samples"]),
        ),
    }
    production_reason = "productive-read-only-canary-requires-separate-explicit-approval"
    measurements["image"] = _not_measured(
        reason="no-M16-image-built-or-selected",
        command="docker image inspect <approved-m16-image>",
        source_revision=source_revision,
        branch=branch,
        measured_at=measured_at,
    )
    measurements["artifacts"] = _not_measured(
        reason="no-M16-wheel-or-image-artifact-built",
        command="./scripts/check-wheel.sh; ./docker/scripts/build-local.sh <approved-tags>",
        source_revision=source_revision,
        branch=branch,
        measured_at=measured_at,
    )
    measurements["container_runtime"] = _not_measured(
        reason=production_reason,
        command="docker inspect <approved-non-production-container-set>",
        source_revision=source_revision,
        branch=branch,
        measured_at=measured_at,
    )
    for name, command in {
        "scheduler": "./scripts/assistant.sh jobs status --target all --deep",
        "sync": "./scripts/assistant.sh jobs status --target sync --deep",
        "mail_live": "./scripts/assistant.sh mail index status",
        "nextcloud": "./scripts/assistant.sh nextcloud status",
        "invoices": "./scripts/assistant.sh invoices status",
        "portfolio": "./scripts/assistant.sh portfolio doctor",
    }.items():
        measurements[name] = _not_measured(
            reason=production_reason,
            command=command,
            source_revision=source_revision,
            branch=branch,
            measured_at=measured_at,
        )
    measurements["sync_work_classes"] = {
        work_class: _not_measured(
            reason=production_reason,
            command=f"instrumented sync benchmark --mode {work_class}",
            source_revision=source_revision,
            branch=branch,
            measured_at=measured_at,
        )
        for work_class in ("full", "delta", "no-op")
    }
    measurements["mail_cache_classes"] = {
        "cold": _measured(
            {"latency_ms": mail["cold_cache_ms"], "synthetic": True},
            command=".venv/bin/python scripts/benchmark_mail_acceptance_m118.py",
            source_revision=source_revision,
            branch=branch,
            measured_at=measured_at,
            samples=args.samples,
        ),
        "warm": _measured(
            {
                "p50_ms": mail["warm_cache_p50_ms"],
                "p95_ms": mail["warm_cache_p95_ms"],
                "synthetic": True,
            },
            command=".venv/bin/python scripts/benchmark_mail_acceptance_m118.py",
            source_revision=source_revision,
            branch=branch,
            measured_at=measured_at,
            samples=int(mail["latency_samples"]),
        ),
        "live": _not_measured(
            reason=production_reason,
            command="./scripts/assistant.sh mail search-local --query <synthetic-probe>",
            source_revision=source_revision,
            branch=branch,
            measured_at=measured_at,
        ),
    }
    report = {
        "schema_version": 1,
        "milestone": "M16.0",
        "generated_at": measured_at,
        "source_revision": source_revision,
        "branch": branch,
        "privacy": {
            "classification": "synthetic-and-aggregate-only",
            "productive_data_read": False,
            "productive_state_written": False,
            "command_stdout_stored": False,
            "discarded_fields": [
                "mail-content",
                "addresses",
                "subjects",
                "queries",
                "calendar-content",
                "invoice-content",
                "portfolio-holdings",
                "credentials",
                "filesystem-user-paths",
            ],
        },
        "measurement_policy": {
            "minimum_repeat_samples": MINIMUM_SAMPLES,
            "percentiles": ["p50", "p95"],
            "missing_evidence_state": "not-measured",
            "live_canary_performed": False,
            "behavior_changed": False,
        },
        "measurements": measurements,
        "risk_register": DEFAULT_RISKS.relative_to(ROOT).as_posix(),
    }
    return report, _risks(source_revision, measured_at)


def main() -> int:
    parser = argparse.ArgumentParser(description="Datenschutzarme M16.0-Gesamtbaseline")
    parser.add_argument("--samples", type=int, default=5)
    parser.add_argument("--source-revision", default="")
    parser.add_argument("--measured-at", default="")
    parser.add_argument("--collection-items", type=int, required=True)
    parser.add_argument("--quality-gate-status", choices=("passed", "failed", "not-run"), default="not-run")
    parser.add_argument("--run-static-analysis", action="store_true")
    parser.add_argument("--junit", type=Path, default=Path("build/pytest.xml"))
    parser.add_argument("--coverage", type=Path, default=Path("build/coverage.json"))
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--risk-output", type=Path, default=DEFAULT_RISKS)
    args = parser.parse_args()
    report, risks = build_report(args)
    output = args.output if args.output.is_absolute() else ROOT / args.output
    risk_output = args.risk_output if args.risk_output.is_absolute() else ROOT / args.risk_output
    _atomic_write(output, report)
    _atomic_write(risk_output, risks)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
