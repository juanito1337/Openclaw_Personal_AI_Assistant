#!/usr/bin/env python3
"""Validate the staged M16 Git, release and promotion contract."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONTRACT = ROOT / "docs/architecture/release-candidate-m16.json"
SHA40 = re.compile(r"^[0-9a-f]{40}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
# Product release format shared with the repository's documentation checker.
PRODUCT_VERSION = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+-r[0-9]+(?:\.[0-9]+)*")
RELEASE_NAME = re.compile(r"r[0-9]+(?:\.[0-9]+)*")
MILESTONES = tuple(f"M{number}" for number in range(11, 17))
ROLES = ("runtime", "proxy", "maintenance")
ACTIONS = ("main-promotion", "image-publication", "production-deployment")
APPROVAL_TIME = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-](?:[01]\d|2[0-3]):[0-5]\d)$"
)


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


def _matches(pattern: re.Pattern[str], value: Any) -> bool:
    return isinstance(value, str) and pattern.fullmatch(value) is not None


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
    _require(
        type(contract.get("schema_version")) is int and contract["schema_version"] == 1,
        "Unbekannte Promotionsvertragsversion",
    )
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
    _require(candidate.get("planned_version") == "3.4.0-r29.0.6", "Naechste Version muss festgelegt sein")
    _require(candidate.get("planned_release") == "r29.0.6", "Naechstes Release muss festgelegt sein")
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
    _require(_matches(SHA40, head), "Ungueltiger Quellcommit")
    _require(_matches(SHA40, main_before), "Ungueltiger Main-Ausgangscommit")
    candidate = _object(contract.get("candidate"), "Kandidat fehlt")
    _require(candidate.get("source_revision") == head, "Kandidat und getesteter HEAD weichen ab")
    _require(candidate.get("main_before") == main_before, "Main-Ausgangscommit weicht ab")
    _require(_git_is_ancestor(main_before, head, root=root), "Main ist kein Vorfahr des Kandidaten")
    release = load_json(release_path)
    _require(
        type(release.get("schema_version")) is int and release["schema_version"] == 1,
        "Unbekannte Release-Manifestschemaversion",
    )
    _require(_matches(PRODUCT_VERSION, release.get("version")), "Ungueltige Produktversion")
    _require(_matches(RELEASE_NAME, release.get("release")), "Ungueltiger Releasebezeichner")
    _require(
        release["version"].split("-", 1)[1] == release["release"],
        "Releasebezeichner passt nicht zur Produktversion",
    )
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
    _require(tag_verified is True, "Signierter Tag ist nicht kryptografisch verifiziert")
    images = _object(contract.get("images"), "Drei Rollenimages fehlen")
    _require(tuple(images) == ROLES, "Drei Rollenimages fehlen")
    for role in ROLES:
        image = _object(images[role], f"Ungueltiges Rollenimage: {role}")
        _require(_matches(DIGEST, image.get("digest")), f"{role}-Digest fehlt")
        _require(image.get("oci_revision") == head, f"{role}-OCI-Revision driftet")
        _require(image.get("version") == release["version"], f"{role}-Version driftet")
        for field in ("sbom_sha256", "provenance_sha256"):
            _require(
                _matches(SHA256, image.get(field)),
                f"{role}-{field} fehlt",
            )
        _require(image.get("signature_verified") is True, f"{role}-Signatur ist nicht verifiziert")
    rollback = _object(contract.get("rollback"), "Rollbackvertrag fehlt")
    _require(rollback.get("status") == "verified", "Rollbackziel ist nicht verifiziert")
    _require(_matches(PRODUCT_VERSION, rollback.get("version")), "Ungueltige Rollbackversion")
    _require(_matches(SHA40, rollback.get("source_revision")), "Rollbackcommit fehlt")
    rollback_images = _object(rollback.get("images"), "Rollbackimages fehlen")
    _require(tuple(rollback_images) == ROLES, "Rollbackimages fehlen")
    for role in ROLES:
        row = _object(rollback_images[role], f"Ungueltiges Rollbackimage: {role}")
        _require(_matches(DIGEST, row.get("digest")), f"Rollback-{role}-Digest fehlt")
        _require(row.get("signature_verified") is True, f"Rollback-{role} ist nicht signiert verifiziert")
    return {
        "ok": True,
        "state": "ready",
        "source_revision": head,
        "version": release["version"],
        "main_before": main_before,
        "ready_contract_sha256": _ready_contract_sha256(contract),
        "separate_approvals_pending": list(ACTIONS),
    }


def _ready_contract_sha256(contract: dict[str, Any]) -> str:
    """Bind approvals to the technical contract, not to other approval decisions."""
    technical_contract = {
        key: value for key, value in contract.items() if key != "approval_gates"
    }
    technical_contract["approval_gates"] = {
        action: {"approval": contract["approval_gates"][action]["approval"]}
        for action in ACTIONS
    }
    try:
        canonical = json.dumps(
            technical_contract, sort_keys=True, separators=(",", ":"), allow_nan=False
        )
    except (TypeError, ValueError) as exc:
        raise PromotionContractError("Ready-Vertrag ist nicht kanonisch serialisierbar") from exc
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def verify_action(
    contract: dict[str, Any],
    action: str,
    *,
    release_path: Path | None = None,
    head: str | None = None,
    main_before: str | None = None,
    tag_target: str | None = None,
    tag_verified: bool = False,
    root: Path = ROOT,
) -> dict[str, Any]:
    _require(action in ACTIONS, f"Unbekannte Promotionsaktion: {action}")
    if release_path is None or head is None or main_before is None or tag_target is None:
        raise PromotionContractError("Vollstaendiger externer Ready-Pruefkontext fehlt")
    # Never accept the self-asserted state or an old success report as proof.
    # Candidate images are already signed immutable build artifacts here; this
    # does not require the later, separately approved release publication.
    ready = verify_ready(
        contract,
        release_path=release_path,
        head=head,
        main_before=main_before,
        tag_target=tag_target,
        tag_verified=tag_verified,
        root=root,
    )
    gates = _object(contract.get("approval_gates"), "Freigabegates fehlen")
    gate = _object(gates.get(action), f"Freigabegate fehlt: {action}")
    _require(gate.get("status") == "approved", f"Separate Freigabe fehlt: {action}")
    _require(gate.get("approved_action") == action, f"Freigabe ist nicht an Aktion gebunden: {action}")
    _require(
        gate.get("ready_contract_sha256") == ready["ready_contract_sha256"],
        f"Freigabe ist nicht an unveraenderten Ready-Vertrag gebunden: {action}",
    )
    approved_by = gate.get("approved_by")
    _require(
        isinstance(approved_by, str) and bool(approved_by.strip()),
        f"Freigabeverantwortlicher fehlt: {action}",
    )
    approved_at = gate.get("approved_at")
    _require(
        isinstance(approved_at, str) and APPROVAL_TIME.fullmatch(approved_at) is not None,
        f"Ungueltige Freigabezeit mit Zeitzone: {action}",
    )
    try:
        approval_time = datetime.fromisoformat(approved_at.replace("Z", "+00:00"))
    except ValueError as exc:
        raise PromotionContractError(f"Ungueltige Freigabezeit: {action}") from exc
    _require(approval_time <= datetime.now(UTC), f"Freigabezeit liegt in Zukunft: {action}")
    return {
        "ok": True,
        "action": action,
        "approved": True,
        "source_revision": ready["source_revision"],
        "ready_contract_sha256": ready["ready_contract_sha256"],
    }


def _ready_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--release", type=Path, required=True)
    parser.add_argument("--head", required=True)
    parser.add_argument("--main-before", required=True)
    parser.add_argument("--tag-target", required=True)
    parser.add_argument("--tag-verified", action="store_true")


def main() -> int:
    parser = argparse.ArgumentParser(description="M16-Git-/Release-/Promotionsvertrag")
    commands = parser.add_subparsers(dest="command", required=True)
    draft = commands.add_parser("verify-draft")
    draft.add_argument("--contract", type=Path, default=DEFAULT_CONTRACT)
    ready = commands.add_parser("verify-ready")
    ready.add_argument("--contract", type=Path, required=True)
    _ready_arguments(ready)
    action = commands.add_parser("verify-action")
    action.add_argument("--contract", type=Path, required=True)
    action.add_argument("--action", choices=ACTIONS, required=True)
    _ready_arguments(action)
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
            report = verify_action(
                contract,
                args.action,
                release_path=args.release,
                head=args.head,
                main_before=args.main_before,
                tag_target=args.tag_target,
                tag_verified=bool(args.tag_verified),
            )
    except (OSError, json.JSONDecodeError, PromotionContractError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2))
        return 1
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
