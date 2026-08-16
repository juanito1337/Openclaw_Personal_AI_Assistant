#!/usr/bin/env python3
"""M7 image identity, lock, SBOM and provenance contracts."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from pathlib import Path
from typing import Any, cast

ROOT = Path(__file__).resolve().parents[1]
LOCK_PATH = ROOT / "docker/supply-chain.lock.json"
DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
ACTION_RE = re.compile(r"(?m)^\s*-?\s*uses:\s*([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)@([^\s#]+)")


class ContractError(ValueError):
    pass


def load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ContractError(f"JSON object expected: {path}")
    return payload


def load_lock(path: Path = LOCK_PATH) -> dict[str, Any]:
    payload = load_json(path)
    if payload.get("schema_version") != 1:
        raise ContractError("unsupported supply-chain lock schema")
    return payload


def split_reference(reference: str) -> tuple[str, str]:
    if "@" not in reference:
        raise ContractError(f"image reference is not digest-pinned: {reference}")
    name, digest = reference.rsplit("@", 1)
    if not name or not DIGEST_RE.fullmatch(digest):
        raise ContractError(f"invalid immutable image reference: {reference}")
    return name, digest


def workflow_job_permissions(text: str) -> dict[str, dict[str, str]]:
    """Read the deliberately simple job permission maps without a YAML dependency."""
    lines = text.splitlines()
    if "permissions: {}" not in {line for line in lines if not line.startswith(" ")}:
        raise ContractError("workflow must start with empty top-level permissions")
    jobs: dict[str, dict[str, str]] = {}
    current_job = ""
    index = 0
    while index < len(lines):
        line = lines[index]
        job_match = re.fullmatch(r"  ([A-Za-z0-9_-]+):", line)
        if job_match and job_match.group(1) != "jobs":
            current_job = job_match.group(1)
        if line == "    permissions:":
            if not current_job:
                raise ContractError("job permission map has no job")
            values: dict[str, str] = {}
            index += 1
            while index < len(lines):
                item = re.fullmatch(r"      ([a-z-]+): (read|write|none)", lines[index])
                if not item:
                    break
                values[item.group(1)] = item.group(2)
                index += 1
            jobs[current_job] = values
            continue
        index += 1
    return jobs


def verify_lock(root: Path = ROOT) -> dict[str, Any]:
    lock = load_lock(root / "docker/supply-chain.lock.json")
    errors: list[str] = []
    for group in ("base_images", "scanner_images"):
        values = lock.get(group)
        if not isinstance(values, dict) or not values:
            errors.append(f"{group} missing")
            continue
        for name, reference in values.items():
            try:
                split_reference(str(reference))
            except ContractError as exc:
                errors.append(f"{group}.{name}: {exc}")
    himalaya = cast(dict[str, Any], lock.get("himalaya") if isinstance(lock.get("himalaya"), dict) else {})
    for key in ("archive_sha256", "sha256_linux_amd64"):
        if not re.fullmatch(r"[0-9a-f]{64}", str(himalaya.get(key) or "")):
            errors.append(f"himalaya {key} missing")
    if not str(himalaya.get("archive_url") or "").startswith(
        "https://github.com/pimalaya/himalaya/releases/download/v"
    ):
        errors.append("Himalaya release URL is not pinned upstream")
    actions = cast(
        dict[str, Any],
        lock.get("github_actions") if isinstance(lock.get("github_actions"), dict) else {},
    )
    for name, revision in actions.items():
        if not re.fullmatch(r"[0-9a-f]{40}", str(revision)):
            errors.append(f"GitHub Action is not commit-pinned: {name}@{revision}")
    workflows = sorted((root / ".github/workflows").glob("*.yml"))
    observed: dict[str, set[str]] = {}
    for workflow in workflows:
        workflow_text = workflow.read_text(encoding="utf-8")
        try:
            permissions = workflow_job_permissions(workflow_text)
        except ContractError as exc:
            errors.append(f"{workflow.name}: {exc}")
            permissions = {}
        expected_permissions = {
            "ci.yml": {
                "test": {"contents": "read"},
                "container": {"contents": "read"},
            },
            "container.yml": {
                "test": {"contents": "read"},
                "build-scan-sign": {
                    "contents": "read",
                    "id-token": "write",
                    "packages": "write",
                },
            },
        }
        if permissions != expected_permissions.get(workflow.name, {}):
            errors.append(f"{workflow.name}: job permissions exceed or miss the M7 contract")
        for name, revision in ACTION_RE.findall(workflow_text):
            observed.setdefault(name, set()).add(revision)
            expected = actions.get(name)
            if expected is None:
                errors.append(f"unlocked GitHub Action: {name}@{revision} in {workflow.name}")
            elif revision != expected:
                errors.append(f"GitHub Action mismatch: {name}@{revision}; expected {expected}")
    for name in actions:
        if name not in observed:
            errors.append(f"locked GitHub Action is unused: {name}")
    dockerfile = (root / "Dockerfile").read_text(encoding="utf-8")
    base_images = cast(dict[str, Any], lock.get("base_images") or {})
    for name, reference in base_images.items():
        if str(reference) not in dockerfile:
            errors.append(f"Dockerfile does not use locked base image {name}")
    runtime_package_pins = cast(dict[str, Any], lock.get("runtime_package_pins") or {})
    for package, version in runtime_package_pins.items():
        if not re.fullmatch(r"[a-z0-9][a-z0-9+_.-]*", str(package)):
            errors.append(f"invalid locked runtime package name: {package}")
            continue
        if not re.fullmatch(r"[0-9][a-zA-Z0-9+_.-]*", str(version)):
            errors.append(f"invalid locked runtime package version: {package}={version}")
            continue
        if f"{package}={version}" not in dockerfile:
            errors.append(f"Dockerfile does not use locked runtime package {package}={version}")
    deploy_verifier = (root / "docker/scripts/verify-image-supply-chain.sh").read_text(encoding="utf-8")
    scanner_images = cast(dict[str, Any], lock.get("scanner_images") or {})
    cosign_reference = str(scanner_images.get("cosign") or "")
    if not cosign_reference or cosign_reference not in deploy_verifier:
        errors.append("deployment verifier does not use the locked Cosign image")
    for key in ("version", "archive_url", "archive_sha256", "sha256_linux_amd64"):
        if str(himalaya.get(key) or "") not in dockerfile:
            errors.append(f"Dockerfile does not use locked Himalaya {key}")
    plugin_lock = cast(
        dict[str, Any],
        lock.get("immutable_openclaw_plugins")
        if isinstance(lock.get("immutable_openclaw_plugins"), dict)
        else {},
    )
    plugin_package_lock = root / "docker/openclaw-plugins/package-lock.json"
    expected_lock_digest = str(plugin_lock.get("package_lock_sha256") or "")
    if not plugin_package_lock.is_file() or sha256_file(plugin_package_lock) != expected_lock_digest:
        errors.append("immutable OpenClaw plugin package-lock does not match the supply-chain lock")
    plugin_package_json = load_json(root / "docker/openclaw-plugins/package.json")
    plugin_runtime_contract = load_json(root / "docker/openclaw-plugins/contract.json")
    declared_plugins = plugin_package_json.get("dependencies")
    locked_plugins = plugin_lock.get("packages")
    if not isinstance(declared_plugins, dict) or not isinstance(locked_plugins, dict):
        errors.append("immutable OpenClaw plugin declarations are missing")
    else:
        expected_dependencies = {
            name: str(contract.get("version") or "")
            for name, contract in locked_plugins.items()
            if isinstance(name, str) and isinstance(contract, dict)
        }
        if declared_plugins != expected_dependencies:
            errors.append("immutable OpenClaw plugin versions differ from package.json")
        package_lock = load_json(plugin_package_lock)
        package_entries = package_lock.get("packages")
        if not isinstance(package_entries, dict):
            errors.append("immutable OpenClaw package-lock has no package entries")
        else:
            for name, contract in locked_plugins.items():
                entry = package_entries.get(f"node_modules/{name}")
                if not isinstance(contract, dict) or not isinstance(entry, dict):
                    errors.append(f"immutable OpenClaw plugin is absent from package-lock: {name}")
                    continue
                if entry.get("version") != contract.get("version"):
                    errors.append(f"immutable OpenClaw plugin version mismatch: {name}")
                if entry.get("integrity") != contract.get("integrity"):
                    errors.append(f"immutable OpenClaw plugin integrity mismatch: {name}")
        runtime_plugins = plugin_runtime_contract.get("plugins")
        if not isinstance(runtime_plugins, dict):
            errors.append("immutable OpenClaw runtime plugin contract is missing")
        else:
            runtime_by_package = {
                value.get("package"): value for value in runtime_plugins.values() if isinstance(value, dict)
            }
            for name, contract in locked_plugins.items():
                runtime_contract = runtime_by_package.get(name)
                if not isinstance(contract, dict) or runtime_contract != {
                    "package": name,
                    "version": contract.get("version"),
                    "integrity": contract.get("integrity"),
                    "shasum": contract.get("shasum"),
                    "path": f"/opt/openclaw-plugins/node_modules/{name}",
                }:
                    errors.append(f"immutable OpenClaw runtime contract mismatch: {name}")
    if "COPY --from=openclaw-plugin-builder" not in dockerfile or "OPENCLAW_NIX_MODE=1" not in dockerfile:
        errors.append("runtime image does not enforce immutable OpenClaw plugins")
    if "COPY docker/openclaw-plugins/contract.json" not in dockerfile:
        errors.append("runtime image does not contain the immutable plugin contract")
    dockerignore = (root / ".dockerignore").read_text(encoding="utf-8").splitlines()
    if "!docker/openclaw-plugins/package-lock.json" not in dockerignore:
        errors.append("immutable OpenClaw plugin lock is excluded from the Docker context")
    policy = cast(dict[str, Any], lock.get("vulnerability_policy") or {})
    if policy.get("fail_severities") != ["CRITICAL"]:
        errors.append("critical vulnerabilities are not fail-closed")
    if policy.get("ignore_unfixed") is not False or policy.get("exceptions") != []:
        errors.append("vulnerability policy contains an exception or ignores unfixed issues")
    if errors:
        raise ContractError("\n".join(errors))
    return {
        "ok": True,
        "base_images": len(lock["base_images"]),
        "runtime_package_pins": len(runtime_package_pins),
        "scanner_images": len(lock["scanner_images"]),
        "immutable_openclaw_plugins": len(plugin_lock.get("packages", {})),
        "github_actions": len(actions),
        "workflows": len(workflows),
    }


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def inspect_image(image: str) -> dict[str, Any]:
    result = subprocess.run(
        ["docker", "image", "inspect", image],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise ContractError(result.stderr.strip() or f"cannot inspect image: {image}")
    payload = json.loads(result.stdout)
    if not isinstance(payload, list) or len(payload) != 1 or not isinstance(payload[0], dict):
        raise ContractError("unexpected docker inspect response")
    return payload[0]


def image_identity(image: str) -> tuple[str, dict[str, str]]:
    payload = inspect_image(image)
    image_id = str(payload.get("Id") or "")
    if not DIGEST_RE.fullmatch(image_id):
        raise ContractError(f"image has no immutable local ID: {image}")
    config = cast(dict[str, Any], payload.get("Config") if isinstance(payload.get("Config"), dict) else {})
    labels = cast(dict[str, Any], config.get("Labels") if isinstance(config.get("Labels"), dict) else {})
    return image_id, {str(key): str(value) for key, value in labels.items()}


def verify_labels(labels: dict[str, str], *, role: str, revision: str, version: str) -> None:
    expected = {
        "org.opencontainers.image.version": version,
        "org.opencontainers.image.revision": revision,
        "org.opencontainers.image.openclaw.role": role,
    }
    for key, value in expected.items():
        if labels.get(key) != value:
            raise ContractError(f"OCI label {key}={labels.get(key)!r}; expected {value!r}")
    for key in (
        "org.opencontainers.image.created",
        "org.opencontainers.image.source",
        "org.opencontainers.image.base.name",
        "org.opencontainers.image.base.digest",
    ):
        if not labels.get(key):
            raise ContractError(f"OCI label missing: {key}")


def verify_sbom_payload(payload: dict[str, Any]) -> None:
    if not str(payload.get("spdxVersion") or "").startswith("SPDX-2."):
        raise ContractError("SBOM is not SPDX JSON")
    packages = payload.get("packages")
    if not isinstance(packages, list) or not packages:
        raise ContractError("SBOM contains no packages")
    creation = payload.get("creationInfo")
    if not isinstance(creation, dict) or not creation.get("created"):
        raise ContractError("SBOM creation metadata missing")


def verify_vulnerability_payload(payload: dict[str, Any], policy: dict[str, Any]) -> dict[str, Any]:
    """Apply the locked release policy to a Trivy JSON report."""
    blocked = {str(item).upper() for item in policy.get("fail_severities", [])}
    if not blocked:
        raise ContractError("vulnerability policy has no blocking severity")
    counts: dict[str, int] = {}
    rejected: list[str] = []
    results = payload.get("Results")
    if results is None:
        results = []
    if not isinstance(results, list):
        raise ContractError("Trivy report Results must be a list")
    for result in results:
        if not isinstance(result, dict):
            raise ContractError("Trivy report contains an invalid result")
        vulnerabilities = result.get("Vulnerabilities") or []
        if not isinstance(vulnerabilities, list):
            raise ContractError("Trivy vulnerabilities must be a list")
        for finding in vulnerabilities:
            if not isinstance(finding, dict):
                raise ContractError("Trivy report contains an invalid vulnerability")
            severity = str(finding.get("Severity") or "UNKNOWN").upper()
            counts[severity] = counts.get(severity, 0) + 1
            if severity in blocked:
                identifier = str(finding.get("VulnerabilityID") or "unknown")
                package = str(finding.get("PkgName") or "unknown")
                rejected.append(f"{identifier}:{package}")
    if rejected:
        raise ContractError(
            f"{len(rejected)} unapproved vulnerability findings: " + ", ".join(sorted(rejected))
        )
    return {"ok": True, "severity_counts": dict(sorted(counts.items())), "rejected": 0}


def make_provenance(*, image: str, role: str, revision: str, sbom: Path, version: str) -> dict[str, Any]:
    image_id, labels = image_identity(image)
    verify_labels(labels, role=role, revision=revision, version=version)
    lock = load_lock()
    return {
        "_type": "https://in-toto.io/Statement/v1",
        "subject": [{"name": image, "digest": {"sha256": image_id.removeprefix("sha256:")}}],
        "predicateType": "https://slsa.dev/provenance/v1",
        "predicate": {
            "buildDefinition": {
                "buildType": "https://openclaw.local/buildtypes/docker-m7/v1",
                "externalParameters": {
                    "role": role,
                    "revision": revision,
                    "release": version,
                    "sbom_sha256": sha256_file(sbom),
                },
                "resolvedDependencies": [
                    {
                        "uri": reference,
                        "digest": {"sha256": split_reference(reference)[1].removeprefix("sha256:")},
                    }
                    for reference in lock["base_images"].values()
                ],
            },
            "runDetails": {
                "builder": {"id": "https://github.com/docker/buildx"},
                "metadata": {"invocationId": f"local:{revision}:{role}"},
            },
        },
    }


def verify_provenance_payload(
    payload: dict[str, Any],
    *,
    image_id: str,
    role: str,
    revision: str,
    version: str,
    sbom_sha256: str,
) -> None:
    if payload.get("_type") != "https://in-toto.io/Statement/v1":
        raise ContractError("invalid in-toto statement type")
    if payload.get("predicateType") != "https://slsa.dev/provenance/v1":
        raise ContractError("invalid SLSA predicate type")
    subjects = payload.get("subject")
    observed = ""
    if isinstance(subjects, list) and subjects and isinstance(subjects[0], dict):
        digest = subjects[0].get("digest")
        if isinstance(digest, dict):
            observed = str(digest.get("sha256") or "")
    if observed != image_id.removeprefix("sha256:"):
        raise ContractError("provenance subject does not match image digest")
    predicate = cast(
        dict[str, Any],
        payload.get("predicate") if isinstance(payload.get("predicate"), dict) else {},
    )
    definition_value = predicate.get("buildDefinition")
    definition = cast(dict[str, Any], definition_value if isinstance(definition_value, dict) else {})
    parameters_value = definition.get("externalParameters")
    parameters = cast(dict[str, Any], parameters_value if isinstance(parameters_value, dict) else {})
    expected = {"role": role, "revision": revision, "release": version, "sbom_sha256": sbom_sha256}
    for key, value in expected.items():
        if parameters.get(key) != value:
            raise ContractError(f"provenance parameter mismatch: {key}")
    dependencies = definition.get("resolvedDependencies")
    if not isinstance(dependencies, list) or len(dependencies) < 3:
        raise ContractError("provenance materials are incomplete")


def command_verify_artifacts(args: argparse.Namespace) -> dict[str, Any]:
    lock = load_lock()
    version = str(lock["release"])
    image_id, labels = image_identity(args.image)
    verify_labels(labels, role=args.role, revision=args.revision, version=version)
    sbom = load_json(args.sbom)
    verify_sbom_payload(sbom)
    provenance = load_json(args.provenance)
    verify_provenance_payload(
        provenance,
        image_id=image_id,
        role=args.role,
        revision=args.revision,
        version=version,
        sbom_sha256=sha256_file(args.sbom),
    )
    return {"ok": True, "image": args.image, "image_id": image_id, "role": args.role}


def main() -> int:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("verify-lock")
    value = commands.add_parser("lock-value")
    value.add_argument("path", help="dot-separated path")
    provenance = commands.add_parser("provenance")
    provenance.add_argument("--image", required=True)
    provenance.add_argument("--role", required=True)
    provenance.add_argument("--revision", required=True)
    provenance.add_argument("--sbom", type=Path, required=True)
    provenance.add_argument("--output", type=Path, required=True)
    verify = commands.add_parser("verify-artifacts")
    verify.add_argument("--image", required=True)
    verify.add_argument("--role", required=True)
    verify.add_argument("--revision", required=True)
    verify.add_argument("--sbom", type=Path, required=True)
    verify.add_argument("--provenance", type=Path, required=True)
    vulnerabilities = commands.add_parser("verify-vulnerabilities")
    vulnerabilities.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == "verify-lock":
            report = verify_lock()
        elif args.command == "lock-value":
            selected: Any = load_lock()
            for part in args.path.split("."):
                selected = selected[part]
            print(selected if isinstance(selected, str) else json.dumps(selected, sort_keys=True))
            return 0
        elif args.command == "provenance":
            report = make_provenance(
                image=args.image,
                role=args.role,
                revision=args.revision,
                sbom=args.sbom,
                version=str(load_lock()["release"]),
            )
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            print(args.output)
            return 0
        elif args.command == "verify-vulnerabilities":
            report = verify_vulnerability_payload(load_json(args.report), load_lock()["vulnerability_policy"])
        else:
            report = command_verify_artifacts(args)
    except (ContractError, KeyError, OSError, json.JSONDecodeError) as exc:
        print(f"M7 supply-chain verification failed: {exc}")
        return 1
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
