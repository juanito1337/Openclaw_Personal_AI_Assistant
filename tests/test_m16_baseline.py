from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
BASELINE = ROOT / "docs/architecture/m16-baseline.json"
RISKS = ROOT / "docs/architecture/m16-risk-register.json"
SCHEMA = ROOT / "docs/architecture/m16-baseline.schema.json"


def _measurement_nodes(value: Any) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    if isinstance(value, dict):
        if value.get("status") in {"measured", "not-measured"}:
            found.append(value)
        else:
            for child in value.values():
                found.extend(_measurement_nodes(child))
    return found


def test_committed_m16_baseline_matches_schema_contract() -> None:
    payload = json.loads(BASELINE.read_text(encoding="utf-8"))
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    assert payload["schema_version"] == schema["properties"]["schema_version"]["const"]
    assert payload["milestone"] == "M16.0"
    assert set(schema["required"]) <= set(payload)
    assert set(schema["properties"]["measurements"]["required"]) <= set(
        payload["measurements"]
    )
    assert len(payload["source_revision"]) == 40


def test_every_baseline_measurement_has_reproducible_evidence() -> None:
    payload = json.loads(BASELINE.read_text(encoding="utf-8"))
    nodes = _measurement_nodes(payload["measurements"])
    assert len(nodes) >= 20
    for node in nodes:
        evidence = node["evidence"]
        assert evidence["command"]
        assert len(evidence["source_revision"]) == 40
        assert evidence["environment"].startswith("local-development-")
        assert evidence["measured_at"]
        assert isinstance(evidence["sample_count"], int)
        if node["status"] == "not-measured":
            assert node["value"] is None
            assert node["reason"]
            assert evidence["sample_count"] == 0


def test_repeated_timings_expose_p50_p95_and_minimum_sample_count() -> None:
    payload = json.loads(BASELINE.read_text(encoding="utf-8"))
    identity = payload["measurements"]["identity"]
    assert identity["evidence"]["sample_count"] >= 3
    for name in ("version_verify_timing", "manifest_verify_timing", "git_status_timing"):
        timing = identity["value"][name]
        assert timing["samples"] >= 3
        assert timing["minimum"] <= timing["p50"] <= timing["p95"] <= timing["maximum"]
    warm = payload["measurements"]["mail_cache_classes"]["warm"]
    assert warm["value"]["p50_ms"] <= warm["value"]["p95_ms"]


def test_productive_domains_are_not_reported_as_success_without_canary() -> None:
    payload = json.loads(BASELINE.read_text(encoding="utf-8"))
    assert payload["privacy"]["productive_data_read"] is False
    assert payload["privacy"]["productive_state_written"] is False
    assert payload["measurement_policy"]["live_canary_performed"] is False
    for name in (
        "artifacts",
        "image",
        "container_runtime",
        "scheduler",
        "sync",
        "mail_live",
        "nextcloud",
        "invoices",
        "portfolio",
    ):
        assert payload["measurements"][name]["status"] == "not-measured"
    assert set(payload["measurements"]["sync_work_classes"]) == {"full", "delta", "no-op"}


def test_report_contains_no_content_or_secret_fields() -> None:
    raw = BASELINE.read_text(encoding="utf-8").casefold()
    for forbidden in (
        "/srv/openclaw",
        "/home/jan/",
        "@gmail.",
        "@gmx.",
        "begin private key",
        "nextcloud_token",
        "portfolio_eodhd_api_key",
    ):
        assert forbidden not in raw
    payload = json.loads(raw)
    assert payload["privacy"]["command_stdout_stored"] is False
    assert "mail-content" in payload["privacy"]["discarded_fields"]
    assert "credentials" in payload["privacy"]["discarded_fields"]


def test_risk_register_is_prioritized_owned_and_routed() -> None:
    payload = json.loads(RISKS.read_text(encoding="utf-8"))
    assert payload["schema_version"] == 1
    assert len(payload["risks"]) >= 10
    allowed_packages = {f"M16.{number}" for number in range(1, 10)}
    identifiers = set()
    for risk in payload["risks"]:
        assert risk["id"] not in identifiers
        identifiers.add(risk["id"])
        assert risk["severity"] in payload["priority_order"]
        assert risk["owner"]
        assert risk["target_package"] in allowed_packages


def test_harness_rejects_less_than_three_samples(tmp_path: Path) -> None:
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/benchmark_m16.py"),
            "--samples",
            "2",
            "--collection-items",
            "1",
            "--output",
            str(tmp_path / "baseline.json"),
            "--risk-output",
            str(tmp_path / "risks.json"),
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "mindestens 3" in result.stderr
