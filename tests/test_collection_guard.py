from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import enforce_collection_baseline


def test_collection_guard_accepts_equal_or_larger_collection() -> None:
    enforce_collection_baseline(
        total=362,
        unittest_count=349,
        invoice_pytest_count=13,
        baseline={
            "minimum_total": 362,
            "minimum_unittest": 349,
            "minimum_invoice_pytest": 13,
        },
    )


def test_collection_guard_rejects_smaller_collection() -> None:
    with pytest.raises(pytest.UsageError, match="minimum_unittest"):
        enforce_collection_baseline(
            total=361,
            unittest_count=348,
            invoice_pytest_count=13,
            baseline={
                "minimum_total": 362,
                "minimum_unittest": 349,
                "minimum_invoice_pytest": 13,
            },
        )


def test_pytest_hook_rejects_a_real_partial_collection() -> None:
    root = Path(__file__).resolve().parents[1]
    environment = os.environ.copy()
    environment["OPENCLAW_ENFORCE_TEST_BASELINE"] = "1"
    environment.pop("OPENCLAW_TEST_INSTALLED", None)

    result = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "tests/test_artifact_hygiene.py"],
        cwd=root,
        env=environment,
        check=False,
        text=True,
        capture_output=True,
    )

    assert result.returncode == pytest.ExitCode.USAGE_ERROR
    assert "Testcollection ist kleiner als die M0-Baseline" in result.stderr
