from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

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
        "schema_version": 1,
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


@pytest.mark.parametrize("invalid", [None, [], {}, {"modules": {}}, {"modules": []}])
def test_risk_coverage_rejects_missing_or_empty_contract(invalid: object) -> None:
    assert verify(fixture(75), invalid)["ok"] is False


@pytest.mark.parametrize("invalid", [None, [], {}, {"files": []}])
def test_risk_coverage_rejects_missing_or_invalid_coverage(invalid: object) -> None:
    assert verify(invalid, contract(50))["ok"] is False


@pytest.mark.parametrize("invalid", [None, True, "80", -1, 101, float("nan"), float("inf"), -float("inf")])
@pytest.mark.parametrize("metric", ["combined_percent", "statement_percent", "branch_percent"])
def test_risk_coverage_rejects_invalid_reference_or_measurement(invalid: object, metric: str) -> None:
    source_metrics = {
        "combined_percent": "percent_covered",
        "statement_percent": "percent_statements_covered",
        "branch_percent": "percent_branches_covered",
    }
    baseline = contract(50)
    baseline["modules"]["risk.py"][metric] = invalid
    reference_report = verify(fixture(75), baseline)
    assert reference_report["ok"] is False
    assert any(item["reason"] == "invalid-reference" for item in reference_report["failures"])
    coverage = fixture(75)
    coverage["files"]["risk.py"]["summary"][source_metrics[metric]] = invalid
    report = verify(coverage, contract(50))
    assert report["ok"] is False
    assert any(item["reason"] == "invalid-measurement" for item in report["failures"])
    json.dumps(report, allow_nan=False)
    json.dumps(reference_report, allow_nan=False)


def test_risk_coverage_rejects_missing_metrics_and_unknown_schema() -> None:
    baseline = contract(50)
    baseline["schema_version"] = True
    assert verify(fixture(75), baseline)["ok"] is False
    baseline["schema_version"] = 2
    assert verify(fixture(75), baseline)["ok"] is False
    baseline = contract(50)
    del baseline["modules"]["risk.py"]["branch_percent"]
    assert verify(fixture(75), baseline)["ok"] is False
    coverage = fixture(75)
    del coverage["files"]["risk.py"]["summary"]["percent_branches_covered"]
    assert verify(coverage, contract(50))["ok"] is False


def test_risk_coverage_cli_reports_invalid_json_without_success(tmp_path: Path) -> None:
    coverage = tmp_path / "coverage.json"
    coverage.write_text("{broken", encoding="utf-8")
    completed = subprocess.run(
        [sys.executable, str(ROOT / "scripts/check-risk-coverage.py"), str(coverage)],
        check=False, capture_output=True, text=True,
    )
    assert completed.returncode == 1
    assert json.loads(completed.stdout)["ok"] is False
    assert not completed.stderr


def _runner_fixture(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    # A stub interpreter proves actual shell flow without nesting the full suite
    # or touching its reports. The gate's deliberate failure must win.
    root = tmp_path / "repository"
    (root / "scripts").mkdir(parents=True)
    runner = root / "scripts/run-tests.sh"
    runner.write_text((ROOT / "scripts/run-tests.sh").read_text(encoding="utf-8"), encoding="utf-8")
    interpreter = tmp_path / "python"
    interpreter.write_text(
        f"#!{sys.executable}\n" + """
import json
import os
import sys
from pathlib import Path
if sys.argv[1].endswith('check-risk-coverage.py'):
    assert Path(sys.argv[2]).parent.name.startswith('coverage-run.')
    report = json.loads(Path(sys.argv[2]).read_text())
    assert report == {'fresh': True}
    sys.exit(19)
assert not Path(os.environ['COVERAGE_FILE']).exists()
report = None
for argument in sys.argv[1:]:
    if argument.startswith('--cov-report=json:'):
        report = Path(argument.split(':', 1)[1])
if '--no-cov' not in sys.argv:
    assert report is not None
    report.write_text(json.dumps({'fresh': True}))
sys.exit(0)
""",
        encoding="utf-8",
    )
    interpreter.chmod(0o700)
    environment = os.environ.copy()
    environment.pop("OPENCLAW_TEST_INSTALLED", None)
    environment["OPENCLAW_DEV_PYTHON"] = str(interpreter)
    return runner, environment


@pytest.mark.parametrize("arguments", [[], ["-q"], ["-v", "--disable-warnings"], ["--cov-append"]])
def test_full_suite_runner_always_checks_fresh_risk_coverage(tmp_path: Path, arguments: list[str]) -> None:
    runner, environment = _runner_fixture(tmp_path)
    completed = subprocess.run(
        ["bash", str(runner), *arguments],
        env=environment, check=False, capture_output=True, text=True,
    )
    assert completed.returncode == 19, completed.stdout + completed.stderr


def test_disabled_coverage_cannot_reuse_previous_success(tmp_path: Path) -> None:
    runner, environment = _runner_fixture(tmp_path)
    build = runner.parents[1] / "build"
    build.mkdir()
    previous = build / "coverage.json"
    previous.write_text(json.dumps({"old": True}), encoding="utf-8")
    completed = subprocess.run(
        ["bash", str(runner), "-q", "--no-cov"],
        env=environment, check=False, capture_output=True, text=True,
    )
    assert completed.returncode == 1
    assert "Frischer Coveragebericht fehlt" in completed.stderr
    assert json.loads(previous.read_text(encoding="utf-8")) == {"old": True}


def test_user_report_option_cannot_redirect_the_required_report(tmp_path: Path) -> None:
    runner, environment = _runner_fixture(tmp_path)
    redirected = tmp_path / "not-the-required-report.json"
    completed = subprocess.run(
        ["bash", str(runner), "-q", f"--cov-report=json:{redirected}"],
        env=environment, check=False, capture_output=True, text=True,
    )
    assert completed.returncode == 19, completed.stdout + completed.stderr
    assert not redirected.exists()


def test_internal_import_graph_remains_acyclic_after_extraction() -> None:
    environment = os.environ.copy()
    environment.pop("OPENCLAW_ENFORCE_TEST_BASELINE", None)
    # This nested check analyses the source tree.  The outer wheel suite has
    # already asserted that its product imports resolve from site-packages.
    environment.pop("OPENCLAW_TEST_INSTALLED", None)
    completed = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "tests/test_architecture_docs.py"],
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
    assert re.fullmatch(r"[0-9a-f]{7,40}", revision)
    if os.environ.get("OPENCLAW_TEST_INSTALLED") == "1":
        return
    completed = subprocess.run(
        ["git", "cat-file", "-e", f"{revision}^{{commit}}"],
        cwd=ROOT,
        check=False,
    )
    assert completed.returncode == 0
