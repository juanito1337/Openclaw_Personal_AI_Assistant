from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

import benchmark_mail_acceptance_m118 as acceptance  # noqa: E402
from check_artifact import forbidden_path  # noqa: E402


def test_m118_aggregates_real_synthetic_milestone_benchmarks_without_regression() -> None:
    report = acceptance.build_report(samples=3)

    assert report["ok"] is True
    assert report["milestone"] == "M11.8"
    assert report["corpus"]["messages"] == 13
    assert report["lexical"]["regressions_visible"] == []
    assert report["lexical"]["quality"]["mean_recall_at_10"] >= report["lexical"][
        "baseline_m110"
    ]["mean_recall_at_10"]
    assert report["threads"]["pair_precision"] == 1.0
    assert report["threads"]["pair_recall"] == 1.0


def test_m118_report_never_claims_fixture_embeddings_are_activatable() -> None:
    report = acceptance.build_report(samples=3)

    semantic = report["semantic_contract"]
    assert semantic["target_hardware_measured"] is False
    assert semantic["model_selected"] is False
    assert semantic["activation_allowed"] is False
    assert all(item["eligible_for_activation"] is False for item in semantic["models"])
    assert report["acceptance"]["productive_rollout"].startswith("not-executed")


def test_m118_report_is_content_free_and_writes_only_requested_output(tmp_path: Path) -> None:
    output = tmp_path / "acceptance.json"
    completed = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/benchmark_mail_acceptance_m118.py"),
            "--samples",
            "3",
            "--output",
            str(output),
        ],
        cwd=ROOT,
        check=True,
        text=True,
        capture_output=True,
    )
    report = json.loads(output.read_text(encoding="utf-8"))

    assert completed.returncode == 0 and report["privacy"]["synthetic_only"] is True
    rendered = output.read_text(encoding="utf-8")
    assert "@example.invalid" not in rendered
    assert "Polarstern" not in rendered
    assert "body_text" not in rendered
    assert "returned_ids" not in rendered
    assert '"queries": [' not in rendered


def test_artifact_guard_rejects_standalone_embedding_and_mail_index_payloads() -> None:
    for path in (
        "embeddings.npy",
        "embeddings.npz",
        "embedding-cache.json",
        "embeddings.json",
        "mail-index.json",
        "mail-search.vec",
    ):
        assert forbidden_path(path) is not None


def test_m11_compose_is_hermetic_and_valid() -> None:
    completed = subprocess.run(
        [
            "docker",
            "compose",
            "-f",
            str(ROOT / "tests/integration/m11/compose.yaml"),
            "config",
            "--format",
            "json",
        ],
        cwd=ROOT,
        check=True,
        text=True,
        capture_output=True,
    )
    compose = json.loads(completed.stdout)

    assert compose.get("secrets") in (None, {})
    assert all("ports" not in service for service in compose["services"].values())
    assert compose["networks"]["m11"]["internal"] is True
    assert all(
        "/srv/openclaw" not in json.dumps(service)
        for service in compose["services"].values()
    )
