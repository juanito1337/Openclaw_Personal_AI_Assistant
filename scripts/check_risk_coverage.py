from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONTRACT = ROOT / "docs/architecture/m16.6-risk-coverage.json"


def verify(coverage: dict[str, Any], contract: dict[str, Any]) -> dict[str, Any]:
    failures: list[dict[str, object]] = []
    observed: dict[str, dict[str, float]] = {}
    files_value = coverage.get("files")
    files = files_value if isinstance(files_value, dict) else {}
    modules_value = contract.get("modules")
    modules = modules_value if isinstance(modules_value, dict) else {}
    for path, baseline_value in modules.items():
        baseline = baseline_value if isinstance(baseline_value, dict) else {}
        entry = files.get(path) if isinstance(files, dict) else None
        summary = entry.get("summary") if isinstance(entry, dict) else None
        if not isinstance(summary, dict):
            failures.append({"path": path, "metric": "module", "reason": "missing"})
            continue
        values = {
            "combined_percent": float(summary.get("percent_covered") or 0.0),
            "statement_percent": float(summary.get("percent_statements_covered") or 0.0),
            "branch_percent": float(summary.get("percent_branches_covered") or 0.0),
        }
        observed[path] = values
        for metric, actual in values.items():
            reference = float(baseline.get(metric) or 0.0)
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
    return {
        "ok": not failures,
        "comparison_revision": contract.get("comparison_revision"),
        "observed": observed,
        "failures": failures,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify M16.6 risk-module coverage")
    parser.add_argument("coverage", type=Path)
    parser.add_argument("--contract", type=Path, default=DEFAULT_CONTRACT)
    args = parser.parse_args()
    report = verify(
        json.loads(args.coverage.read_text(encoding="utf-8")),
        json.loads(args.contract.read_text(encoding="utf-8")),
    )
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
