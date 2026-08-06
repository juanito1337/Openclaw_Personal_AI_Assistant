from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest


def _assert_installed_product_modules() -> None:
    root = Path(__file__).resolve().parents[1]
    loaded = {
        name: module
        for name, module in sys.modules.items()
        if name == "mail_agent"
        or name.startswith("mail_agent.")
        or name == "personal_assistant"
        or name.startswith("personal_assistant.")
    }
    for name, module in loaded.items():
        filename = getattr(module, "__file__", None)
        if not filename:
            continue
        package_path = Path(filename).resolve()
        if (
            root == package_path
            or root in package_path.parents
            or "site-packages" not in package_path.parts
        ):
            raise pytest.UsageError(
                f"Wheel-Test importiert {name} nicht aus site-packages: {package_path}"
            )


def pytest_sessionstart(session: pytest.Session) -> None:
    del session
    if os.environ.get("OPENCLAW_TEST_INSTALLED") != "1":
        return
    import mail_agent  # noqa: F401
    import personal_assistant  # noqa: F401

    _assert_installed_product_modules()
    root = Path(__file__).resolve().parents[1]
    sys.path[:0] = [str(root / "tests"), str(root)]


def enforce_collection_baseline(
    *,
    total: int,
    unittest_count: int,
    invoice_pytest_count: int,
    baseline: dict[str, int],
) -> None:
    observed = {
        "minimum_total": total,
        "minimum_unittest": unittest_count,
        "minimum_invoice_pytest": invoice_pytest_count,
    }
    failures = [
        f"{name}: gesammelt {observed[name]}, erwartet mindestens {minimum}"
        for name, minimum in baseline.items()
        if observed.get(name, -1) < minimum
    ]
    if failures:
        raise pytest.UsageError("Testcollection ist kleiner als die M0-Baseline: " + "; ".join(failures))


def pytest_collection_finish(session: pytest.Session) -> None:
    if os.environ.get("OPENCLAW_TEST_INSTALLED") == "1":
        _assert_installed_product_modules()
    if os.environ.get("OPENCLAW_ENFORCE_TEST_BASELINE") != "1":
        return
    baseline_path = Path(__file__).with_name("test-baseline.json")
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    unittest_count = sum(getattr(item, "_testcase", None) is not None for item in session.items)
    invoice_pytest_count = sum(
        Path(str(item.path)).name == "test_invoice_ocr_register.py"
        and getattr(item, "_testcase", None) is None
        for item in session.items
    )
    enforce_collection_baseline(
        total=len(session.items),
        unittest_count=unittest_count,
        invoice_pytest_count=invoice_pytest_count,
        baseline=baseline,
    )
