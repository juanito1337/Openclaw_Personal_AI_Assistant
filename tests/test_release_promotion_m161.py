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
        "version": "3.4.0-r29.0.6",
        "release": "r29.0.6",
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


def _verify_action(
    contract: dict[str, Any], action: str, release: Path, head: str, main_before: str
) -> dict[str, Any]:
    return promotion.verify_action(
        contract,
        action,
        release_path=release,
        head=head,
        main_before=main_before,
        tag_target=head,
        tag_verified=True,
    )


def _approve(contract: dict[str, Any], action: str, report: dict[str, Any]) -> None:
    contract["approval_gates"][action].update(
        {
            "status": "approved",
            "approved_action": action,
            "approved_by": "synthetic-operator",
            "approved_at": "2020-01-01T20:00:00Z",
            "ready_contract_sha256": report["ready_contract_sha256"],
        }
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


@pytest.mark.parametrize("invalid", (True, False, 1.0, "1", None))
@pytest.mark.parametrize("schema_target", ("contract", "release"))
def test_ready_schema_versions_are_exact_integers(
    tmp_path: Path, invalid: Any, schema_target: str
) -> None:
    contract, release, head, main_before = _ready_contract(tmp_path)
    if schema_target == "contract":
        contract["schema_version"] = invalid
    else:
        payload = promotion.load_json(release)
        payload["schema_version"] = invalid
        release.write_text(json.dumps(payload), encoding="utf-8")
        contract["candidate"]["release_manifest_sha256"] = promotion.sha256_file(release)
    _approve(contract, "main-promotion", {
        "ready_contract_sha256": promotion._ready_contract_sha256(contract),
    })
    with pytest.raises(promotion.PromotionContractError, match="[Vv]ersion"):
        _verify(contract, release, head, main_before)
    with pytest.raises(promotion.PromotionContractError, match="[Vv]ersion"):
        _verify_action(contract, "main-promotion", release, head, main_before)


@pytest.mark.parametrize("field", ("version", "release"))
@pytest.mark.parametrize("invalid", (None, "", " ", True, 123, [], {}, "OpenClaw 2026.7.1"))
def test_matching_invalid_release_identifiers_never_become_ready(
    tmp_path: Path, field: str, invalid: Any
) -> None:
    contract, release, head, main_before = _ready_contract(tmp_path)
    payload = promotion.load_json(release)
    payload[field] = invalid
    contract["candidate"]["planned_" + field] = invalid
    if field == "version":
        for image in contract["images"].values():
            image["version"] = invalid
    release.write_text(json.dumps(payload), encoding="utf-8")
    contract["candidate"]["release_manifest_sha256"] = promotion.sha256_file(release)
    # Even a matching new digest and approval cannot cure malformed identities.
    _approve(contract, "main-promotion", {
        "ready_contract_sha256": promotion._ready_contract_sha256(contract),
    })
    with pytest.raises(promotion.PromotionContractError, match="Produktversion|Releasebezeichner"):
        _verify(contract, release, head, main_before)
    with pytest.raises(promotion.PromotionContractError, match="Produktversion|Releasebezeichner"):
        _verify_action(contract, "main-promotion", release, head, main_before)


@pytest.mark.parametrize("version", ("3.4.0-r29", "3.4.0-r29.0.6", "4.0.0-r1"))
def test_product_release_format_accepts_base_and_patch_releases(tmp_path: Path, version: str) -> None:
    contract, release, head, main_before = _ready_contract(tmp_path)
    payload = promotion.load_json(release)
    payload.update(version=version, release=version.split("-", 1)[1])
    contract["candidate"].update(planned_version=payload["version"], planned_release=payload["release"])
    for image in contract["images"].values():
        image["version"] = version
    release.write_text(json.dumps(payload), encoding="utf-8")
    contract["candidate"]["release_manifest_sha256"] = promotion.sha256_file(release)
    assert _verify(contract, release, head, main_before)["version"] == version


def test_valid_but_mismatched_release_suffix_fails_closed(tmp_path: Path) -> None:
    contract, release, head, main_before = _ready_contract(tmp_path)
    payload = promotion.load_json(release)
    payload["release"] = "r1"
    contract["candidate"]["planned_release"] = "r1"
    release.write_text(json.dumps(payload), encoding="utf-8")
    contract["candidate"]["release_manifest_sha256"] = promotion.sha256_file(release)
    with pytest.raises(promotion.PromotionContractError, match="passt nicht zur Produktversion"):
        _verify(contract, release, head, main_before)


@pytest.mark.parametrize(
    ("mutation", "message"),
    (
        (lambda row: row["rollback"].__setitem__("version", None), "Rollbackversion"),
        (lambda row: row["rollback"].__setitem__("version", "r28"), "Rollbackversion"),
        (lambda row: row["rollback"].__setitem__("source_revision", int("1" * 40)), "Rollbackcommit"),
        (lambda row: row["images"]["runtime"].__setitem__("sbom_sha256", int("1" * 64)), "sbom_sha256"),
        (lambda row: row["images"]["proxy"].__setitem__("provenance_sha256", int("1" * 64)), "provenance"),
    ),
)
def test_other_required_identifiers_are_typed_and_nonempty(
    tmp_path: Path, mutation: Any, message: str
) -> None:
    contract, release, head, main_before = _ready_contract(tmp_path)
    mutation(contract)
    with pytest.raises(promotion.PromotionContractError, match=message):
        _verify(contract, release, head, main_before)


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
    contract, release, head, main_before = _ready_contract(tmp_path)
    ready = _verify(contract, release, head, main_before)
    for action in promotion.ACTIONS:
        with pytest.raises(promotion.PromotionContractError, match="Separate Freigabe"):
            _verify_action(contract, action, release, head, main_before)
    for selected in promotion.ACTIONS:
        _approve(contract, selected, ready)
        assert _verify_action(contract, selected, release, head, main_before)["approved"] is True
        assert _verify(contract, release, head, main_before)["ready_contract_sha256"] == ready[
            "ready_contract_sha256"
        ]
    # Recording another independent approval must not invalidate the first one.
    assert _verify_action(contract, promotion.ACTIONS[0], release, head, main_before)["ok"] is True


def test_ready_label_and_unbound_success_report_cannot_authorize_action(tmp_path: Path) -> None:
    contract, release, head, main_before = _ready_contract(tmp_path)
    malformed = {
        "state": "ready",
        "approval_gates": {
            "main-promotion": {
                "status": "approved",
                "approved_by": "synthetic-review",
                "approved_at": "not-a-timestamp",
            }
        },
    }
    with pytest.raises(promotion.PromotionContractError, match="Ready-Pruefkontext"):
        promotion.verify_action(malformed, "main-promotion")
    with pytest.raises(promotion.PromotionContractError, match="Promotionsvertragsversion"):
        _verify_action(malformed, "main-promotion", release, head, main_before)
    report = _verify(contract, release, head, main_before)
    with pytest.raises(promotion.PromotionContractError, match="Promotionsvertragsversion"):
        _verify_action(report, "main-promotion", release, head, main_before)


@pytest.mark.parametrize("missing", ("candidate", "images", "rollback", "ready_evidence"))
def test_action_revalidates_complete_ready_contract(tmp_path: Path, missing: str) -> None:
    contract, release, head, main_before = _ready_contract(tmp_path)
    _approve(contract, "main-promotion", _verify(contract, release, head, main_before))
    del contract[missing]
    with pytest.raises(promotion.PromotionContractError):
        _verify_action(contract, "main-promotion", release, head, main_before)


@pytest.mark.parametrize(
    "mutation",
    (
        lambda row: row["images"]["runtime"].__setitem__("digest", "sha256:" + "f" * 64),
        lambda row: row["images"]["maintenance"].__setitem__("provenance_sha256", "f" * 64),
        lambda row: row["rollback"]["images"]["proxy"].__setitem__("digest", "sha256:" + "f" * 64),
        lambda row: row["release_scope"].__setitem__("report", "changed-release-scope.md"),
    ),
)
def test_approval_does_not_transfer_to_changed_but_valid_ready_contract(
    tmp_path: Path, mutation: Any
) -> None:
    contract, release, head, main_before = _ready_contract(tmp_path)
    _approve(contract, "main-promotion", _verify(contract, release, head, main_before))
    mutation(contract)
    assert _verify(contract, release, head, main_before)["ok"] is True
    with pytest.raises(promotion.PromotionContractError, match="unveraenderten Ready-Vertrag"):
        _verify_action(contract, "main-promotion", release, head, main_before)


def test_approval_cannot_be_copied_to_another_action(tmp_path: Path) -> None:
    contract, release, head, main_before = _ready_contract(tmp_path)
    _approve(contract, "main-promotion", _verify(contract, release, head, main_before))
    contract["approval_gates"]["production-deployment"] = copy.deepcopy(
        contract["approval_gates"]["main-promotion"]
    )
    with pytest.raises(promotion.PromotionContractError, match="an Aktion gebunden"):
        _verify_action(contract, "production-deployment", release, head, main_before)


@pytest.mark.parametrize(
    ("field", "invalid", "message"),
    (
        ("approved_by", "  ", "Freigabeverantwortlicher"),
        ("approved_by", ["synthetic-operator"], "Freigabeverantwortlicher"),
        ("approved_at", None, "Freigabezeit"),
        ("approved_at", True, "Freigabezeit"),
        ("approved_at", "not-a-timestamp", "Freigabezeit"),
        ("approved_at", "2020-01-01", "Freigabezeit"),
        ("approved_at", "2020-01-01T20:00:00", "Freigabezeit"),
        ("approved_at", "2020-02-30T20:00:00Z", "Freigabezeit"),
        ("approved_at", "2020-01-01T20:00:00+00:99", "Freigabezeit"),
        ("approved_at", "2999-01-01T20:00:00Z", "Zukunft"),
        ("ready_contract_sha256", "f" * 64, "unveraenderten Ready-Vertrag"),
        ("ready_contract_sha256", None, "unveraenderten Ready-Vertrag"),
    ),
)
def test_action_rejects_invalid_approval_fields(
    tmp_path: Path, field: str, invalid: Any, message: str
) -> None:
    contract, release, head, main_before = _ready_contract(tmp_path)
    _approve(contract, "main-promotion", _verify(contract, release, head, main_before))
    contract["approval_gates"]["main-promotion"][field] = invalid
    with pytest.raises(promotion.PromotionContractError, match=message):
        _verify_action(contract, "main-promotion", release, head, main_before)


def test_action_revalidates_manifest_and_tag_evidence(tmp_path: Path) -> None:
    contract, release, head, main_before = _ready_contract(tmp_path)
    _approve(contract, "main-promotion", _verify(contract, release, head, main_before))
    with pytest.raises(promotion.PromotionContractError, match="nicht kryptografisch"):
        promotion.verify_action(
            contract,
            "main-promotion",
            release_path=release,
            head=head,
            main_before=main_before,
            tag_target=head,
        )
    # Even a semantically identical reserialization changes the approved bytes.
    release.write_text(release.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    with pytest.raises(promotion.PromotionContractError, match="RELEASE.json-Digest"):
        _verify_action(contract, "main-promotion", release, head, main_before)


def test_action_cli_requires_and_rechecks_ready_context(tmp_path: Path) -> None:
    contract, release, head, main_before = _ready_contract(tmp_path)
    _approve(contract, "image-publication", _verify(contract, release, head, main_before))
    contract_path = tmp_path / "ready-contract.json"
    contract_path.write_text(json.dumps(contract), encoding="utf-8")
    command = [
        sys.executable,
        str(ROOT / "scripts/release_promotion.py"),
        "verify-action",
        "--contract", str(contract_path),
        "--action", "image-publication",
    ]
    missing_context = subprocess.run(command, capture_output=True, text=True, check=False)
    assert missing_context.returncode == 2
    command.extend([
        "--release", str(release), "--head", head, "--main-before", main_before,
        "--tag-target", head, "--tag-verified",
    ])
    approved = subprocess.run(command, capture_output=True, text=True, check=False)
    assert approved.returncode == 0, approved.stdout + approved.stderr
    assert json.loads(approved.stdout)["approved"] is True
    contract["images"]["proxy"]["signature_verified"] = False
    contract_path.write_text(json.dumps(contract), encoding="utf-8")
    invalidated = subprocess.run(command, capture_output=True, text=True, check=False)
    assert invalidated.returncode == 1
    assert json.loads(invalidated.stdout)["ok"] is False


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
