from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONTRACT = ROOT / "docs/architecture/m16.6-risk-coverage.json"
METRICS = {
    "combined_percent": "percent_covered",
    "statement_percent": "percent_statements_covered",
    "branch_percent": "percent_branches_covered",
}


def _percentage(value: object) -> float | None:
    # bool is an int subclass; strings and non-finite JSON extensions are not
    # measured coverage. Missing metrics must never silently become zero.
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return None
    if not 0 <= value <= 100 or not math.isfinite(value):
        return None
    return float(value)


def verify(coverage: object, contract: object) -> dict[str, Any]:
    failures: list[dict[str, object]] = []
    observed: dict[str, dict[str, float]] = {}
    coverage_data = coverage if isinstance(coverage, dict) else {}
    contract_data = contract if isinstance(contract, dict) else {}
    files = coverage_data.get("files")
    modules = contract_data.get("modules")
    revision = contract_data.get("comparison_revision")
    if type(contract_data.get("schema_version")) is not int or contract_data.get("schema_version") != 1:
        failures.append({"path": "contract", "metric": "schema_version", "reason": "invalid"})
    if not isinstance(revision, str) or not revision.strip():
        failures.append({"path": "contract", "metric": "comparison_revision", "reason": "invalid"})
        revision = None
    if not isinstance(modules, dict) or not modules:
        failures.append({"path": "contract", "metric": "modules", "reason": "missing-or-empty"})
        modules = {}
    if not isinstance(files, dict):
        failures.append({"path": "coverage", "metric": "files", "reason": "invalid"})
        files = {}
    for path, baseline_value in modules.items():
        if not isinstance(path, str) or not path.strip():
            failures.append({"path": "contract", "metric": "module-path", "reason": "invalid"})
            continue
        baseline = baseline_value if isinstance(baseline_value, dict) else {}
        entry = files.get(path)
        summary = entry.get("summary") if isinstance(entry, dict) else None
        if not isinstance(summary, dict):
            failures.append({"path": path, "metric": "module", "reason": "missing"})
        values: dict[str, float] = {}
        for metric, source_metric in METRICS.items():
            reference = _percentage(baseline.get(metric))
            if reference is None:
                failures.append({"path": path, "metric": metric, "reason": "invalid-reference"})
            if not isinstance(summary, dict):
                continue
            actual = _percentage(summary.get(source_metric))
            if actual is None:
                failures.append({"path": path, "metric": metric, "reason": "invalid-measurement"})
                continue
            values[metric] = actual
            if reference is None:
                continue
            regressed = actual < reference
            not_improved = metric in {"combined_percent", "branch_percent"} and actual <= reference
            if regressed or not_improved:
                failures.append(
                    {
                        "path": path,
                        "metric": metric,
                        "reference": reference,
                        "actual": actual,
                        "reason": "regressed" if regressed else "not-improved",
                    }
                )
        if values:
            observed[path] = values
    return {
        "ok": not failures,
        "comparison_revision": revision,
        "observed": observed,
        "failures": failures,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify M16.6 risk-module coverage")
    parser.add_argument("coverage", type=Path)
    parser.add_argument("--contract", type=Path, default=DEFAULT_CONTRACT)
    args = parser.parse_args()
    try:
        report = verify(
            json.loads(args.coverage.read_text(encoding="utf-8")),
            json.loads(args.contract.read_text(encoding="utf-8")),
        )
    except (OSError, UnicodeError, json.JSONDecodeError):
        report = {
            "ok": False,
            "comparison_revision": None,
            "observed": {},
            "failures": [{"path": "input", "metric": "json", "reason": "unreadable-or-invalid"}],
        }
    print(json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
