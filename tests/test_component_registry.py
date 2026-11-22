from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/component_registry.py"
PROVENANCE = "docs/architecture/upstream-sources.json"
REGISTRY = "docs/architecture/external-components.json"
LOCK = "docker/supply-chain.lock.json"
ACTION_COMMIT = "a" * 40
IMAGE_DIGEST = "b" * 64
INTEGRITY = "sha512-YWJjZA=="


def write(root: Path, path: str, content: str) -> None:
    destination = root / path
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(content, encoding="utf-8")


def write_json(root: Path, path: str, payload: dict) -> None:
    write(root, path, json.dumps(payload, indent=2) + "\n")


def read_json(root: Path, path: str) -> dict:
    return json.loads((root / path).read_text(encoding="utf-8"))


def run_registry(root: Path, command: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), command, "--root", str(root)],
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    plugin = "@vendor/chat"
    plugin_pin = {"version": "2.3.4", "integrity": INTEGRITY, "shasum": "c" * 40}
    overlay_pin = {"version": "5.6.7", "integrity": INTEGRITY, "shasum": "d" * 40}
    lock_groups = {}
    for directory, group, name, pin, transitive_path in (
        (
            "openclaw-plugins",
            "immutable_openclaw_plugins",
            plugin,
            plugin_pin,
            f"node_modules/{plugin}/node_modules/codec",
        ),
        (
            "openclaw-security-overrides",
            "runtime_security_overrides",
            "sanitizer",
            overlay_pin,
            "node_modules/optional-types",
        ),
    ):
        package_dir = f"docker/{directory}"
        deps = {name: pin["version"]}
        manifest = {"dependencies": deps}
        if directory == "openclaw-plugins":
            manifest["packageManager"] = "npm@11.2.3"
        write_json(tmp_path, f"{package_dir}/package.json", manifest)
        lock_path = f"{package_dir}/package-lock.json"
        write_json(
            tmp_path,
            lock_path,
            {
                "lockfileVersion": 3,
                "packages": {
                    "": {"dependencies": deps},
                    f"node_modules/{name}": {
                        "version": pin["version"],
                        "integrity": INTEGRITY,
                        "resolved": f"https://registry.example.test/{name}.tgz",
                    },
                    transitive_path: {
                        "version": "1.1.0",
                        "integrity": INTEGRITY,
                        "license": "MIT",
                        "resolved": "https://registry.example.test/transitive.tgz",
                        "inBundle": directory == "openclaw-plugins",
                        "optional": directory == "openclaw-security-overrides",
                    },
                },
            },
        )
        lock_groups[group] = {
            "package_lock_sha256": hashlib.sha256((tmp_path / lock_path).read_bytes()).hexdigest(),
            "packages": {name: {**pin, **({"reason": "test advisory"} if name == "sanitizer" else {})}},
        }
    write_json(
        tmp_path,
        "docker/openclaw-plugins/contract.json",
        {
            "schema_version": 1,
            "openclaw_core_version": "1.2.3",
            "plugins": {
                "chat": {**plugin_pin, "package": plugin, "path": f"/opt/plugins/node_modules/{plugin}"}
            },
        },
    )
    base_image = f"registry.example.test/agent:1.2.3@sha256:{IMAGE_DIGEST}"
    archive = "https://upstream.example.test/himalaya/v2.0.0/amd64.tgz"
    write_json(
        tmp_path,
        LOCK,
        {
            "schema_version": 1,
            "base_images": {"openclaw_source": base_image},
            "scanner_images": {"syft": f"registry.example.test/scanner:v1@sha256:{IMAGE_DIGEST}"},
            "himalaya": {
                "version": "2.0.0",
                "archive_url": archive,
                "archive_sha256": "1" * 64,
                "sha256_linux_amd64": "2" * 64,
            },
            "runtime_package_pins": {"curl": "1.2.3-r0"},
            "control_ui_setup_patch": {
                "description": "Apply the local setup link.",
                "files": {"sw.js": {"upstream_sha256": "3" * 64, "patched_sha256": "4" * 64}},
                "vendor_module": {"path": "assets/sanitizer.js", "sha256": "5" * 64},
            },
            "github_actions": {"actions/checkout": ACTION_COMMIT},
            **lock_groups,
        },
    )
    write(
        tmp_path,
        "Dockerfile",
        (
            "# syntax=docker/dockerfile:1.7\n"
            f"ARG BASE={base_image}\n"
            "FROM ${BASE} AS openclaw-security-override-builder\n"
            "FROM ${BASE} AS runtime\n"
            "RUN apk add --no-cache \\\n"
            "    curl=1.2.3-r0 \\\n"
            "    tini=0.1-r0 && echo done\n"
            "COPY --from=openclaw-security-override-builder "
            "/overrides/node_modules/sanitizer /app/node_modules/sanitizer\n"
            "FROM ${BASE} AS proxy-runtime\n"
            "RUN apk add --no-cache curl=1.2.3-r0\n"
        ),
    )
    write(tmp_path, "requirements-dev.lock", "# Toolchain\npytest==9.1.1\nsetuptools==83.0.0\n")
    write(tmp_path, "pyproject.toml", '[build-system]\nrequires = ["setuptools==83.0.0"]\n')
    write(
        tmp_path,
        "scripts/bootstrap-dev.sh",
        (
            '"$VENV/bin/python" -m pip install --upgrade "pip==26.2.1"\n'
            "SHELLCHECK_VERSION=0.11.0\nHADOLINT_VERSION=2.15.1\n"
            'case "$(uname -s)-$(uname -m)" in\n'
            "  Linux-x86_64)\n"
            f"    shellcheck_arch=x86_64\n    shellcheck_sha={'6' * 64}\n"
            f"    hadolint_arch=x86_64\n    hadolint_sha={'7' * 64}\n"
            "    ;;\nesac\n"
        ),
    )
    write(
        tmp_path,
        ".github/workflows/ci.yml",
        (
            "jobs:\n  test:\n    strategy:\n      matrix:\n"
            '        python-version: ["3.11", "3.13"]\n    steps:\n'
            f"      - uses: actions/checkout@{ACTION_COMMIT} # v5\n"
        ),
    )
    ids = [
        "alpine:curl",
        "alpine:tini",
        "action:actions/checkout",
        "binary:hadolint",
        "binary:himalaya",
        "binary:shellcheck",
        "build:dockerfile-frontend",
        "build:npm",
        "build:workflow-python",
        "image:openclaw_source",
        "image:syft",
        f"npm:plugins:node_modules/{plugin}",
        f"npm:plugins:node_modules/{plugin}/node_modules/codec",
        "npm:security-overrides:node_modules/sanitizer",
        "npm:security-overrides:node_modules/optional-types",
        "patch:control-ui",
        "pypi:pip",
        "pypi:pytest",
        "pypi:setuptools",
    ]
    provenance = {
        "source": "https://upstream.example.test/",
        "owner": "Fixture Maintainers",
        "update_source": "https://upstream.example.test/releases",
    }
    write_json(
        tmp_path,
        PROVENANCE,
        {
            "schema_version": 1,
            "components": {
                **{item: provenance for item in reversed(ids)},
                "external:test-service": {
                    **provenance,
                    "kind": "service",
                    "deployment_scope": "external",
                    "source_files": [PROVENANCE],
                },
            },
        },
    )
    return tmp_path


def test_generate_projects_real_pins_and_a_pin_change(tree: Path) -> None:
    result = run_registry(tree, "generate")
    assert result.returncode == 0, result.stderr
    payload = read_json(tree, REGISTRY)
    assert payload["schema_version"] == 1
    components = {component["id"]: component for component in payload["components"]}
    assert list(components) == sorted(components)
    assert components["image:openclaw_source"]["pin"]["digest"] == "sha256:" + IMAGE_DIGEST
    assert components["image:openclaw_source"]["pin"]["embedded_version"] == "1.2.3"
    assert components["alpine:curl"]["pin"]["stages"] == ["proxy-runtime", "runtime"]
    assert components["alpine:tini"]["pin"]["version"] == "0.1-r0"
    assert components["pypi:setuptools"]["pin"]["roles"] == ["development", "wheel-build"]
    assert components["pypi:pip"]["pin"]["version"] == "26.2.1"
    assert components["binary:himalaya"]["pin"]["archive_sha256"] == "1" * 64
    assert components["binary:shellcheck"]["pin"]["sha256_by_arch"] == {"x86_64": "6" * 64}
    assert components["patch:control-ui"]["pin"]["files"]["sw.js"]["patched_sha256"] == "4" * 64
    assert components["action:actions/checkout"]["pin"]["commit"] == ACTION_COMMIT
    assert components["build:workflow-python"]["pin"]["selectors"] == ["3.11", "3.13"]
    assert components["npm:plugins:node_modules/@vendor/chat"]["pin"]["runtime_id"] == "chat"
    assert components["npm:plugins:node_modules/@vendor/chat/node_modules/codec"]["pin"]["in_bundle"] is True
    assert components["npm:security-overrides:node_modules/sanitizer"]["deployment_scope"] == "product"
    assert components["npm:security-overrides:node_modules/optional-types"]["deployment_scope"] == "build"
    assert components["external:test-service"]["pin"] is None
    assert components["external:test-service"]["review"] == {"status": "unknown"}

    write(tree, "requirements-dev.lock", "pytest==9.1.2\nsetuptools==83.0.0\n")
    result = run_registry(tree, "generate")
    assert result.returncode == 0, result.stderr
    changed = [
        item["pin"]["version"]
        for item in read_json(tree, REGISTRY)["components"]
        if item["id"] == "pypi:pytest"
    ]
    assert changed == ["9.1.2"]


def test_generation_is_deterministic_even_if_metadata_order_changes(tree: Path) -> None:
    result = run_registry(tree, "generate")
    assert result.returncode == 0, result.stderr
    before = (tree / REGISTRY).read_bytes()
    data = read_json(tree, PROVENANCE)
    data["components"] = dict(reversed(list(data["components"].items())))
    write_json(tree, PROVENANCE, data)

    result = run_registry(tree, "generate")
    assert result.returncode == 0, result.stderr
    assert (tree / REGISTRY).read_bytes() == before


@pytest.mark.parametrize("missing", ["provenance record", "upstream source", "lock file"])
def test_generate_rejects_missing_provenance_or_input(tree: Path, missing: str) -> None:
    if missing == "lock file":
        (tree / LOCK).unlink()
        expected = LOCK
    else:
        data = read_json(tree, PROVENANCE)
        if missing == "provenance record":
            del data["components"]["image:syft"]
        else:
            del data["components"]["image:syft"]["source"]
        write_json(tree, PROVENANCE, data)
        expected = "image:syft"
    result = run_registry(tree, "generate")
    assert result.returncode != 0
    assert expected in result.stderr
    assert not (tree / REGISTRY).exists()


def test_duplicate_provenance_ids_are_rejected(tree: Path) -> None:
    path = tree / PROVENANCE
    duplicate_key = '"image:syft": {'
    write(
        tree,
        PROVENANCE,
        path.read_text(encoding="utf-8").replace(
            duplicate_key,
            '"image:syft": {}, ' + duplicate_key,
        ),
    )
    result = run_registry(tree, "generate")
    assert result.returncode != 0
    assert "duplicate" in result.stderr.lower()
    assert "image:syft" in result.stderr


def test_verify_rejects_a_missing_projection(tree: Path) -> None:
    result = run_registry(tree, "verify")
    assert result.returncode != 0
    assert REGISTRY in result.stderr
    assert not (tree / REGISTRY).exists()


def test_verify_rejects_duplicate_projection_ids_without_fixing_the_file(tree: Path) -> None:
    result = run_registry(tree, "generate")
    assert result.returncode == 0, result.stderr
    payload = read_json(tree, REGISTRY)
    payload["components"].insert(0, payload["components"][0])
    write_json(tree, REGISTRY, payload)
    before = (tree / REGISTRY).read_bytes()
    result = run_registry(tree, "verify")
    assert result.returncode != 0
    assert "duplicate" in result.stderr.lower()
    assert (tree / REGISTRY).read_bytes() == before


def test_verify_detects_source_drift_without_modifying_projection(tree: Path) -> None:
    result = run_registry(tree, "generate")
    assert result.returncode == 0, result.stderr
    result = run_registry(tree, "verify")
    assert result.returncode == 0, result.stderr

    before = (tree / REGISTRY).read_bytes()
    write(tree, "requirements-dev.lock", "pytest==9.1.2\nsetuptools==83.0.0\n")
    result = run_registry(tree, "verify")
    assert result.returncode != 0
    assert "stale" in result.stderr.lower()
    assert (tree / REGISTRY).read_bytes() == before


def test_source_paths_cannot_redirect_to_an_unlisted_file(tree: Path) -> None:
    (tree / LOCK).rename(tree / "unlisted.txt")
    (tree / LOCK).symlink_to(tree / "unlisted.txt")
    result = run_registry(tree, "generate")
    assert result.returncode != 0
    assert "symlink" in result.stderr.lower()
    assert not (tree / REGISTRY).exists()


def test_generate_rejects_an_empty_alpine_pin(tree: Path) -> None:
    text = (tree / "Dockerfile").read_text(encoding="utf-8")
    write(tree, "Dockerfile", text.replace("tini=0.1-r0", "tini="))
    result = run_registry(tree, "generate")
    assert result.returncode != 0
    assert "unpinned Alpine package" in result.stderr


def test_verify_requires_canonical_projection_bytes(tree: Path) -> None:
    result = run_registry(tree, "generate")
    assert result.returncode == 0, result.stderr
    path = tree / REGISTRY
    changed = path.read_bytes().replace(b"\n", b"\r\n")
    path.write_bytes(changed)

    result = run_registry(tree, "verify")
    assert result.returncode != 0
    assert "stale" in result.stderr.lower()
    assert path.read_bytes() == changed


@pytest.mark.parametrize("instruction", ["FROM", "COPY"])
def test_verify_rejects_unlocked_dockerfile_images(tree: Path, instruction: str) -> None:
    result = run_registry(tree, "generate")
    assert result.returncode == 0, result.stderr
    source = tree / "Dockerfile"
    dockerfile = source.read_text(encoding="utf-8")
    unlocked = "registry.example.test/unknown@sha256:" + "8" * 64
    if instruction == "FROM":
        dockerfile = dockerfile.replace("FROM ${BASE} AS runtime", f"FROM {unlocked} AS runtime")
    else:
        dockerfile += f"COPY --from={unlocked} /file /file\n"
    source.write_text(dockerfile, encoding="utf-8")

    result = run_registry(tree, "verify")
    assert result.returncode != 0
    assert "unlocked" in result.stderr.lower()
    assert instruction in result.stderr


def test_verify_detects_image_stage_usage_drift_with_the_same_image_pin(tree: Path) -> None:
    result = run_registry(tree, "generate")
    assert result.returncode == 0, result.stderr
    source = tree / "Dockerfile"
    dockerfile = source.read_text(encoding="utf-8")
    source.write_text(
        dockerfile.replace("FROM ${BASE} AS runtime", "FROM openclaw-security-override-builder AS runtime"),
        encoding="utf-8",
    )

    result = run_registry(tree, "verify")
    assert result.returncode != 0
    assert "stale" in result.stderr.lower()


def test_generate_records_external_copy_from_a_locked_image(tree: Path) -> None:
    source = tree / "Dockerfile"
    reference = read_json(tree, LOCK)["base_images"]["openclaw_source"]
    write(tree, "Dockerfile", source.read_text(encoding="utf-8") + f"COPY --from={reference} /file /file\n")

    result = run_registry(tree, "generate")
    assert result.returncode == 0, result.stderr
    images = {
        item["id"]: item["pin"]
        for item in read_json(tree, REGISTRY)["components"]
        if item["kind"] == "oci-image"
    }
    assert images["image:openclaw_source"]["copy_stages"] == ["proxy-runtime"]


def test_verify_checks_actions_in_both_workflow_suffixes(tree: Path) -> None:
    result = run_registry(tree, "generate")
    assert result.returncode == 0, result.stderr
    path = ".github/workflows/additional.yaml"
    write(tree, path, f"jobs:\n  build:\n    steps:\n      - uses: outside/action@{ACTION_COMMIT}\n")

    result = run_registry(tree, "verify")
    assert result.returncode != 0
    assert path in result.stderr
    assert "outside/action" in result.stderr


def test_verify_rejects_workflow_action_flow_maps(tree: Path) -> None:
    result = run_registry(tree, "generate")
    assert result.returncode == 0, result.stderr
    workflow = ".github/workflows/ci.yml"
    text = (tree / workflow).read_text(encoding="utf-8")
    write(tree, workflow, text + f"      - {{uses: outside/action@{ACTION_COMMIT}}}\n")

    result = run_registry(tree, "verify")
    assert result.returncode != 0
    assert workflow in result.stderr
    assert "unsupported" in result.stderr.lower()


def test_verify_detects_a_second_apk_installation_in_one_run(tree: Path) -> None:
    result = run_registry(tree, "generate")
    assert result.returncode == 0, result.stderr
    source = tree / "Dockerfile"
    dockerfile = source.read_text(encoding="utf-8").replace(
        "&& echo done", "&& apk add --no-cache jq=1.2.3-r0"
    )
    write(tree, "Dockerfile", dockerfile)

    result = run_registry(tree, "verify")
    assert result.returncode != 0
    assert "alpine:jq" in result.stderr

    provenance = read_json(tree, PROVENANCE)
    provenance["components"]["alpine:jq"] = provenance["components"]["alpine:tini"]
    write_json(tree, PROVENANCE, provenance)
    result = run_registry(tree, "generate")
    assert result.returncode == 0, result.stderr
    components = {item["id"]: item for item in read_json(tree, REGISTRY)["components"]}
    assert components["alpine:jq"]["pin"] == {"version": "1.2.3-r0", "stages": ["runtime"]}
    assert components["alpine:tini"]["pin"]["version"] == "0.1-r0"
