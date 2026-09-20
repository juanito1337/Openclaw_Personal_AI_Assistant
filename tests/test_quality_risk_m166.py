from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from scripts.check_risk_coverage import verify

ROOT = Path(__file__).resolve().parents[1]


def fixture(percent: float) -> dict[str, object]:
    return {
        "files": {
            "risk.py": {
                "summary": {
                    "percent_covered": percent,
                    "percent_statements_covered": percent,
                    "percent_branches_covered": percent,
                }
            }
        }
    }


def contract(percent: float) -> dict[str, object]:
    return {
        "comparison_revision": "fixture",
        "modules": {
            "risk.py": {
                "combined_percent": percent,
                "statement_percent": percent,
                "branch_percent": percent,
            }
        },
    }


def test_risk_coverage_requires_real_combined_and_branch_improvement() -> None:
    assert verify(fixture(51), contract(50))["ok"] is True
    report = verify(fixture(50), contract(50))
    assert report["ok"] is False
    assert {item["metric"] for item in report["failures"]} == {
        "combined_percent",
        "branch_percent",
    }


def test_risk_coverage_rejects_missing_or_regressed_module() -> None:
    missing = verify({"files": {}}, contract(50))
    assert missing["failures"] == [
        {"path": "risk.py", "metric": "module", "reason": "missing"}
    ]
    regressed = verify(fixture(49), contract(50))
    assert {item["reason"] for item in regressed["failures"]} == {"regressed"}


def test_internal_import_graph_remains_acyclic_after_extraction() -> None:
    environment = os.environ.copy()
    environment.pop("OPENCLAW_ENFORCE_TEST_BASELINE", None)
    completed = subprocess.run(
        [".venv/bin/python", "-m", "pytest", "-q", "tests/test_architecture_docs.py"],
        cwd=ROOT,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr


def test_coverage_contract_is_bound_to_existing_commit() -> None:
    payload = json.loads(
        (ROOT / "docs/architecture/m16.6-risk-coverage.json").read_text(encoding="utf-8")
    )
    revision = str(payload["comparison_revision"])
    completed = subprocess.run(
        ["git", "cat-file", "-e", f"{revision}^{{commit}}"],
        cwd=ROOT,
        check=False,
    )
    assert completed.returncode == 0
