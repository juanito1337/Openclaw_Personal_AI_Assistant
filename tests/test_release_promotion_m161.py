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
        if command in {"rev-parse HEAD", "rev-parse main"}:
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
        "version": "3.4.0-r29",
        "release": "r29",
        "installed_at": None,
        "installation_id": None,
    }
    release_path = tmp_path / "RELEASE.json"
    release_path.write_text(json.dumps(release, sort_keys=True) + "\n", encoding="utf-8")
    head = _git("rev-parse HEAD")
    main_before = _git("rev-parse main")
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
