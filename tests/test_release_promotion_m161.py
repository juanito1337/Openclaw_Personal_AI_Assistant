from __future__ import annotations

import copy
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import release_promotion as promotion  # noqa: E402


def _git(command: str) -> str:
    if os.environ.get("OPENCLAW_TEST_INSTALLED") == "1":
        if command in {"rev-parse HEAD", "rev-parse refs/remotes/origin/main"}:
            return "a" * 40
        raise AssertionError(f"Nicht unterstuetzter installierter Git-Fixture-Befehl: {command}")
    return subprocess.run(
        ["git", *command.split()],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _ready_contract(tmp_path: Path) -> tuple[dict[str, Any], Path, str, str]:
    release = {
        "schema_version": 1,
        "version": "3.4.0-r29.0.3",
        "release": "r29.0.3",
        "installed_at": None,
        "installation_id": None,
    }
    release_path = tmp_path / "RELEASE.json"
    release_path.write_text(json.dumps(release, sort_keys=True) + "\n", encoding="utf-8")
    head = _git("rev-parse HEAD")
    main_before = _git("rev-parse refs/remotes/origin/main")
    digest = "sha256:" + "a" * 64
    image = {
        "digest": digest,
        "oci_revision": head,
        "version": release["version"],
        "sbom_sha256": "b" * 64,
        "provenance_sha256": "c" * 64,
        "signature_verified": True,
    }
    contract = {
        "schema_version": 1,
        "state": "ready",
        "ready_for_promotion": True,
        "ready_evidence": {
            "tracked_in_candidate_commit": False,
            "format": "external-json-release-artifact",
            "reason": "fixture",
        },
        "strategy": "fast-forward-only",
        "force_push_allowed": False,
        "tested_commit_mutable": False,
        "candidate": {
            "planned_version": release["version"],
            "planned_release": release["release"],
            "source_revision": head,
            "main_before": main_before,
            "release_manifest_sha256": hashlib.sha256(release_path.read_bytes()).hexdigest(),
            "signed_tag_target": head,
        },
        "release_scope": {
            "milestones": ["M11", "M12", "M13", "M14", "M15", "M16"],
        },
        "images": {role: copy.deepcopy(image) for role in promotion.ROLES},
        "rollback": {
            "status": "verified",
            "version": "3.4.0-r28",
            "source_revision": main_before,
            "images": {
                role: {"digest": "sha256:" + "d" * 64, "signature_verified": True}
                for role in promotion.ROLES
            },
        },
        "approval_gates": {
            action: {"approval": "explicit-separate", "status": "pending"}
            for action in promotion.ACTIONS
        },
        "installation_evidence": None,
    }
    return contract, release_path, head, main_before


def _verify(contract: dict[str, Any], release: Path, head: str, main_before: str) -> dict[str, Any]:
    return promotion.verify_ready(
        contract,
        release_path=release,
        head=head,
        main_before=main_before,
        tag_target=head,
        tag_verified=True,
    )


def test_tracked_candidate_is_an_explicitly_blocked_draft() -> None:
    contract = promotion.load_json(ROOT / "docs/architecture/release-candidate-m16.json")
    report = promotion.verify_draft(contract)
    assert report["ok"] is True
    assert report["promotion_blocked"] is True
    assert contract["ready_evidence"]["tracked_in_candidate_commit"] is False


def test_ready_evidence_cannot_be_self_referential(tmp_path: Path) -> None:
    contract, release, head, main_before = _ready_contract(tmp_path)
    contract["ready_evidence"]["tracked_in_candidate_commit"] = True
    with pytest.raises(promotion.PromotionContractError, match="selbstreferenzielle"):
        _verify(contract, release, head, main_before)


def test_ready_contract_binds_release_images_tag_and_rollback(tmp_path: Path) -> None:
    contract, release, head, main_before = _ready_contract(tmp_path)
    report = _verify(contract, release, head, main_before)
    assert report["ok"] is True
    assert report["source_revision"] == head
    assert report["separate_approvals_pending"] == list(promotion.ACTIONS)


@pytest.mark.parametrize(
    ("mutation", "message"),
    (
        (lambda row: row["candidate"].__setitem__("source_revision", "f" * 40), "getesteter HEAD"),
        (lambda row: row["images"]["runtime"].__setitem__("oci_revision", "f" * 40), "OCI-Revision"),
        (lambda row: row["images"]["proxy"].__setitem__("signature_verified", False), "Signatur"),
        (lambda row: row["rollback"].__setitem__("status", "not-established"), "Rollbackziel"),
    ),
)
def test_identity_or_supply_chain_drift_fails_closed(
    tmp_path: Path,
    mutation: Any,
    message: str,
) -> None:
    contract, release, head, main_before = _ready_contract(tmp_path)
    mutation(contract)
    with pytest.raises(promotion.PromotionContractError, match=message):
        _verify(contract, release, head, main_before)


def test_release_manifest_drift_and_installer_fields_fail_closed(tmp_path: Path) -> None:
    contract, release, head, main_before = _ready_contract(tmp_path)
    payload = json.loads(release.read_text(encoding="utf-8"))
    payload["installed_at"] = "2026-09-20T20:00:00+00:00"
    release.write_text(json.dumps(payload) + "\n", encoding="utf-8")
    with pytest.raises(promotion.PromotionContractError, match="installed_at"):
        _verify(contract, release, head, main_before)


def test_foreign_main_commit_cannot_be_promoted_silently(tmp_path: Path) -> None:
    contract, release, head, _main_before = _ready_contract(tmp_path)
    foreign = "f" * 40
    contract["candidate"]["main_before"] = foreign
    with pytest.raises(promotion.PromotionContractError, match="kein Vorfahr"):
        _verify(contract, release, head, foreign)


def test_each_external_promotion_action_needs_its_own_approval(tmp_path: Path) -> None:
    contract, _release, _head, _main_before = _ready_contract(tmp_path)
    for action in promotion.ACTIONS:
        with pytest.raises(promotion.PromotionContractError, match="Separate Freigabe"):
            promotion.verify_action(contract, action)
    selected = promotion.ACTIONS[0]
    contract["approval_gates"][selected].update(
        {"status": "approved", "approved_by": "operator", "approved_at": "2026-09-20T20:00:00Z"}
    )
    assert promotion.verify_action(contract, selected)["approved"] is True


def test_container_workflow_uses_release_manifest_version() -> None:
    workflow = (ROOT / ".github/workflows/container.yml").read_text(encoding="utf-8")
    assert "version=$(python3 -c" in workflow
    assert "OPENCLAW_VERSION=${{ steps.refs.outputs.version }}" in workflow
    assert "OPENCLAW_VERSION=3.4.0-r28" not in workflow


def test_local_image_paths_derive_version_from_release_manifest() -> None:
    build = (ROOT / "docker/scripts/build-local.sh").read_text(encoding="utf-8")
    smoke = (ROOT / "scripts/check-role-images.sh").read_text(encoding="utf-8")
    verifier = (ROOT / "docker/scripts/verify-image-supply-chain.sh").read_text(
        encoding="utf-8"
    )
    marker = 'json.load(open("RELEASE.json", encoding="utf-8"))["version"]'
    assert marker in build
    assert marker in smoke
    assert "OPENCLAW_VERSION=$release" in build
    assert "release=3.4.0-r28" not in smoke
    assert "OPENCLAW_EXPECTED_RELEASE:?" in verifier
    assert "OPENCLAW_EXPECTED_RELEASE:-3.4.0-r28" not in verifier


def test_normal_ci_runs_all_hermetic_release_scenarios_and_reproducibility() -> None:
    workflow = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    container_workflow = (ROOT / ".github/workflows/container.yml").read_text(
        encoding="utf-8"
    )
    assert workflow.count("fetch-depth: 0") == 2
    assert container_workflow.count("fetch-depth: 0") == 2
    ci_container_job = workflow.split("\n  container:\n", maxsplit=1)[1]
    assert (
        "docker/setup-buildx-action@8d2750c68a42422c14e847fe6c8ac0403b4cbd6f"
        in ci_container_job
    )
    for command in (
        "./scripts/check-m11-integration.sh",
        "./scripts/check-m12-integration.sh",
        "./scripts/check-m13-integration.sh",
        "./scripts/check-m14-integration.sh",
        "./scripts/check-m15-integration.sh",
        "./scripts/check-reproducible-images.sh",
    ):
        assert command in workflow


def test_m16_acceptance_cannot_claim_success_without_immutable_evidence() -> None:
    report = promotion.load_json(ROOT / "docs/architecture/m16.10-acceptance.json")
    assert report["verdict"] == "M16 NICHT ABGENOMMEN"
    assert report["productive_changes"] is False
    assert report["local_quality"]["ok"] is True
    assert report["promotion"] == {
        "release_candidate_state": "r29-local-candidate",
        "release_identity": "3.4.0-r29",
        "planned_release_identity": "3.4.0-r29",
        "ci_for_exact_commit": "not-measured",
        "signed_git_tag_verified": False,
        "registry_digests_verified": False,
        "cosign_verified": False,
        "rollback_registry_roles_verified": True,
        "rollback_set_verified": False,
        "main_promoted": False,
        "image_published": False,
        "production_deployed": False,
        "pending_separate_approvals": list(promotion.ACTIONS),
    }
    assert len(report["blockers"]) == 5


def test_r29_release_report_matches_machine_readable_test_totals() -> None:
    report = promotion.load_json(ROOT / "docs/architecture/m16.10-acceptance.json")
    release_report = (ROOT / "docs/RELEASE_3_4_0_R29.md").read_text(encoding="utf-8")
    collection = f'{int(report["local_quality"]["collection_items"]):,}'.replace(",", ".")
    junit = f'{int(report["local_quality"]["junit_executed_including_subtests"]):,}'.replace(
        ",", "."
    )
    subtests = int(report["local_quality"]["subtests"])
    assert f"{collection} pytest-Items" in release_report
    assert f"{subtests} Subtests {junit} JUnit-Faelle" in release_report
