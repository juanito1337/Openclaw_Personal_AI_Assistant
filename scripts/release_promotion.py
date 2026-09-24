#!/usr/bin/env python3
"""Validate the staged M16 Git, release and promotion contract."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONTRACT = ROOT / "docs/architecture/release-candidate-m16.json"
SHA40 = re.compile(r"^[0-9a-f]{40}$")
DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
MILESTONES = tuple(f"M{number}" for number in range(11, 17))
ROLES = ("runtime", "proxy", "maintenance")
ACTIONS = ("main-promotion", "image-publication", "production-deployment")


class PromotionContractError(ValueError):
    pass


def load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise PromotionContractError(f"JSON-Objekt erwartet: {path}")
    return payload


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise PromotionContractError(message)


def _object(value: Any, message: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise PromotionContractError(message)
    return value


def _git_is_ancestor(ancestor: str, revision: str, *, root: Path = ROOT) -> bool:
    # A commit is always its own ancestor.  Besides avoiding an unnecessary
    # subprocess, this keeps the pure promotion contract testable from an
    # installed wheel where no Git object database is expected to exist.
    if ancestor == revision:
        return True
    result = subprocess.run(
        ["git", "merge-base", "--is-ancestor", ancestor, revision],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


def verify_common(contract: dict[str, Any]) -> None:
    _require(contract.get("schema_version") == 1, "Unbekannte Promotionsvertragsversion")
    _require(contract.get("strategy") == "fast-forward-only", "Promotion muss fast-forward-only sein")
    _require(contract.get("force_push_allowed") is False, "Force-Push muss ausgeschlossen sein")
    _require(contract.get("tested_commit_mutable") is False, "Getestete Commits duerfen nicht mutieren")
    ready_evidence = _object(contract.get("ready_evidence"), "Ready-Evidenzvertrag fehlt")
    _require(
        ready_evidence.get("tracked_in_candidate_commit") is False,
        "Commitgebundene Ready-Evidenz darf keine selbstreferenzielle Quelldatei sein",
    )
    _require(
        ready_evidence.get("format") == "external-json-release-artifact",
        "Ready-Evidenz muss als externes JSON-Release-Artefakt vorliegen",
    )
    scope = _object(contract.get("release_scope"), "Releaseumfang fehlt")
    milestones = scope.get("milestones")
    if not isinstance(milestones, list):
        raise PromotionContractError("Releaseumfang ist keine Liste")
    observed = tuple(str(item) for item in milestones)
    _require(observed == MILESTONES, "Releasebericht muss M11 bis M16 lueckenlos abdecken")
    gates = _object(contract.get("approval_gates"), "Getrennte Freigabegates fehlen")
    _require(tuple(gates) == ACTIONS, "Freigabegates muessen geordnet und getrennt sein")
    for action in ACTIONS:
        gate = _object(gates[action], f"Ungueltiges Gate: {action}")
        _require(gate.get("approval") == "explicit-separate", f"{action} braucht eigene Freigabe")
    installation = contract.get("installation_evidence")
    _require(installation is None, "Installerevidenz darf nicht in den Buildvertrag geschrieben werden")


def verify_draft(contract: dict[str, Any]) -> dict[str, Any]:
    verify_common(contract)
    _require(contract.get("state") == "draft", "Getrackter M16-Vertrag muss vor M16.10 draft sein")
    _require(contract.get("ready_for_promotion") is False, "Draft darf nicht promotionsbereit sein")
    candidate = _object(contract.get("candidate"), "Kandidat fehlt")
    _require(candidate.get("planned_version") == "3.4.0-r29.0.3", "Naechste Version muss festgelegt sein")
    _require(candidate.get("planned_release") == "r29.0.3", "Naechstes Release muss festgelegt sein")
    for field in ("source_revision", "release_manifest_sha256", "signed_tag_target"):
        _require(candidate.get(field) is None, f"Draftfeld {field} muss bis zur Abnahme leer bleiben")
    images = _object(contract.get("images"), "Drei Rollenimages fehlen")
    _require(tuple(images) == ROLES, "Drei Rollenimages fehlen")
    for role in ROLES:
        image = _object(images[role], f"Ungueltiges Rollenimage: {role}")
        for field in ("digest", "oci_revision", "sbom_sha256", "provenance_sha256"):
            _require(image.get(field) is None, f"Draftimage {role}.{field} muss leer sein")
        _require(image.get("signature_verified") is False, f"Draftimage {role} darf nicht signiert gelten")
    rollback = _object(contract.get("rollback"), "Rollbackvertrag fehlt")
    _require(rollback.get("status") == "not-established", "Draft-Rollback muss offen ausgewiesen sein")
    for action in ACTIONS:
        _require(
            contract["approval_gates"][action].get("status") == "pending",
            f"{action} ist vorzeitig freigegeben",
        )
    return {
        "ok": True,
        "state": "draft",
        "planned_version": candidate["planned_version"],
        "promotion_blocked": True,
        "missing_immutable_evidence": True,
    }


def verify_ready(
    contract: dict[str, Any],
    *,
    release_path: Path,
    head: str,
    main_before: str,
    tag_target: str,
    tag_verified: bool,
    root: Path = ROOT,
) -> dict[str, Any]:
    verify_common(contract)
    _require(contract.get("state") == "ready", "Kandidat ist nicht ready")
    _require(contract.get("ready_for_promotion") is True, "Promotionsfreigabe fehlt")
    _require(SHA40.fullmatch(head) is not None, "Ungueltiger Quellcommit")
    _require(SHA40.fullmatch(main_before) is not None, "Ungueltiger Main-Ausgangscommit")
    candidate = _object(contract.get("candidate"), "Kandidat fehlt")
    _require(candidate.get("source_revision") == head, "Kandidat und getesteter HEAD weichen ab")
    _require(candidate.get("main_before") == main_before, "Main-Ausgangscommit weicht ab")
    _require(_git_is_ancestor(main_before, head, root=root), "Main ist kein Vorfahr des Kandidaten")
    release = load_json(release_path)
    _require(release.get("version") == candidate.get("planned_version"), "Releaseversion driftet")
    _require(release.get("release") == candidate.get("planned_release"), "Releasebezeichner driftet")
    _require(release.get("installed_at") is None, "Buildmanifest darf installed_at nicht vortaeuschen")
    _require(release.get("installation_id") is None, "Buildmanifest darf installation_id nicht vortaeuschen")
    _require(
        candidate.get("release_manifest_sha256") == sha256_file(release_path),
        "RELEASE.json-Digest driftet",
    )
    _require(
        candidate.get("signed_tag_target") == tag_target == head,
        "Signierter Tag zeigt nicht auf den Kandidaten",
    )
    _require(tag_verified, "Signierter Tag ist nicht kryptografisch verifiziert")
    images = _object(contract.get("images"), "Drei Rollenimages fehlen")
    _require(tuple(images) == ROLES, "Drei Rollenimages fehlen")
    for role in ROLES:
        image = _object(images[role], f"Ungueltiges Rollenimage: {role}")
        _require(DIGEST.fullmatch(str(image.get("digest") or "")) is not None, f"{role}-Digest fehlt")
        _require(image.get("oci_revision") == head, f"{role}-OCI-Revision driftet")
        _require(image.get("version") == release["version"], f"{role}-Version driftet")
        for field in ("sbom_sha256", "provenance_sha256"):
            _require(
                re.fullmatch(r"[0-9a-f]{64}", str(image.get(field) or "")) is not None,
                f"{role}-{field} fehlt",
            )
        _require(image.get("signature_verified") is True, f"{role}-Signatur ist nicht verifiziert")
    rollback = _object(contract.get("rollback"), "Rollbackvertrag fehlt")
    _require(rollback.get("status") == "verified", "Rollbackziel ist nicht verifiziert")
    _require(SHA40.fullmatch(str(rollback.get("source_revision") or "")) is not None, "Rollbackcommit fehlt")
    rollback_images = _object(rollback.get("images"), "Rollbackimages fehlen")
    _require(tuple(rollback_images) == ROLES, "Rollbackimages fehlen")
    for role in ROLES:
        row = _object(rollback_images[role], f"Ungueltiges Rollbackimage: {role}")
        _require(DIGEST.fullmatch(str(row.get("digest") or "")) is not None, f"Rollback-{role}-Digest fehlt")
        _require(row.get("signature_verified") is True, f"Rollback-{role} ist nicht signiert verifiziert")
    return {
        "ok": True,
        "state": "ready",
        "source_revision": head,
        "version": release["version"],
        "main_before": main_before,
        "separate_approvals_pending": list(ACTIONS),
    }


def verify_action(contract: dict[str, Any], action: str) -> dict[str, Any]:
    _require(action in ACTIONS, f"Unbekannte Promotionsaktion: {action}")
    _require(contract.get("state") == "ready", "Technischer Ready-Vertrag fehlt")
    gates = _object(contract.get("approval_gates"), "Freigabegates fehlen")
    gate = _object(gates.get(action), f"Freigabegate fehlt: {action}")
    _require(gate.get("status") == "approved", f"Separate Freigabe fehlt: {action}")
    _require(bool(gate.get("approved_by")), f"Freigabeverantwortlicher fehlt: {action}")
    _require(bool(gate.get("approved_at")), f"Freigabezeit fehlt: {action}")
    return {"ok": True, "action": action, "approved": True}


def main() -> int:
    parser = argparse.ArgumentParser(description="M16-Git-/Release-/Promotionsvertrag")
    commands = parser.add_subparsers(dest="command", required=True)
    draft = commands.add_parser("verify-draft")
    draft.add_argument("--contract", type=Path, default=DEFAULT_CONTRACT)
    ready = commands.add_parser("verify-ready")
    ready.add_argument("--contract", type=Path, required=True)
    ready.add_argument("--release", type=Path, required=True)
    ready.add_argument("--head", required=True)
    ready.add_argument("--main-before", required=True)
    ready.add_argument("--tag-target", required=True)
    ready.add_argument("--tag-verified", action="store_true")
    action = commands.add_parser("verify-action")
    action.add_argument("--contract", type=Path, required=True)
    action.add_argument("--action", choices=ACTIONS, required=True)
    args = parser.parse_args()
    try:
        contract = load_json(args.contract)
        if args.command == "verify-draft":
            report = verify_draft(contract)
        elif args.command == "verify-ready":
            report = verify_ready(
                contract,
                release_path=args.release,
                head=args.head,
                main_before=args.main_before,
                tag_target=args.tag_target,
                tag_verified=bool(args.tag_verified),
            )
        else:
            report = verify_action(contract, args.action)
    except (OSError, json.JSONDecodeError, PromotionContractError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2))
        return 1
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
