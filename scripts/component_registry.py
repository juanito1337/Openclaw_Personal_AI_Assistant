#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
import tomllib
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

if __package__:
    from .m7_supply_chain import workflow_actions, workflow_files
else:
    from m7_supply_chain import workflow_actions, workflow_files

ROOT = Path(__file__).resolve().parents[1]
PROVENANCE = "docs/architecture/upstream-sources.json"
REGISTRY = "docs/architecture/external-components.json"
LOCK = "docker/supply-chain.lock.json"
BOOTSTRAP = "scripts/bootstrap-dev.sh"
PYPROJECT = "pyproject.toml"
REQUIREMENTS = "requirements-dev.lock"
DOCKERFILE = "Dockerfile"
WORKFLOWS = ".github/workflows"
PLUGIN_CONTRACT = "docker/openclaw-plugins/contract.json"
DEPLOYMENT_SCOPES = {"product", "build", "external", "reference"}
EVIDENCE_FILES = {
    "docker/patch-control-ui-setup.py",
    "docs/NEXTCLOUD_REUSE_COMPARISON.md",
    "docs/STACK_UPDATE_ROADMAP.md",
    "personal_assistant/connectors/nextcloud/tasks.py",
    "personal_assistant/ical_edit.py",
}
METADATA_FIELDS = {
    "source",
    "owner",
    "update_source",
    "license_status",
    "source_files",
    "tests",
    "local_changes",
    "review",
}
SHA256 = re.compile(r"[0-9a-f]{64}")
COMMIT = re.compile(r"[0-9a-f]{40}")
REQUIREMENT = re.compile(r"([a-zA-Z0-9][a-zA-Z0-9_.-]*)==([a-zA-Z0-9][a-zA-Z0-9_.+!-]*)")


def object_value(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"object required for {label}")
    return value


def string_value(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"nonempty text required for {label}")
    return value


def string_list(value: Any, label: str) -> list[str]:
    if not isinstance(value, list):
        raise ValueError(f"list required for {label}")
    return [string_value(item, label) for item in value]


def matching(value: Any, pattern: re.Pattern[str], label: str) -> str:
    value = string_value(value, label)
    if not pattern.fullmatch(value):
        raise ValueError(f"invalid {label}")
    return value


def add_unique(destination: dict[str, Any], key: str, value: Any) -> None:
    if key in destination:
        raise ValueError(f"duplicate key or component ID: {key}")
    destination[key] = value


def unique_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        add_unique(result, key, value)
    return result


def local_path(root: Path, name: str) -> Path:
    # Callers supply fixed inputs, files in the workflow directory, or validated test paths.
    path = root
    for part in name.split("/"):
        path /= part
        if path.is_symlink():
            raise ValueError(f"symlink is not an allowed registry path: {name}")
    return path


def read_source(root: Path, name: str) -> str:
    try:
        return local_path(root, name).read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise ValueError(f"unreadable registry file: {name}") from exc


def parse_json(source: str, name: str) -> dict[str, Any]:
    try:
        return object_value(json.loads(source, object_pairs_hook=unique_pairs), name)
    except ValueError as exc:
        raise ValueError(f"invalid {name}: {exc}") from exc


def read_json(root: Path, name: str) -> dict[str, Any]:
    return parse_json(read_source(root, name), name)


def check_version(payload: dict[str, Any], name: str) -> None:
    if type(payload.get("schema_version")) is not int or payload["schema_version"] != 1:
        raise ValueError(f"unsupported schema version for {name}")


def entry(kind: str, scope: str, source_files: list[str], **pin: Any) -> dict[str, Any]:
    return {"kind": kind, "deployment_scope": scope, "source_files": source_files, "pin": pin}


def dockerfile_instructions(dockerfile: str) -> list[tuple[str, str]]:
    instructions = []
    continuation = ""
    for raw in dockerfile.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            if re.match(r"#\s*escape\s*=", line, re.IGNORECASE) and not re.fullmatch(
                r"#\s*escape\s*=\s*\\", line
            ):
                raise ValueError("unsupported Dockerfile escape directive")
            continue
        if line.endswith("\\"):
            continuation += line[:-1] + " "
            continue
        instruction, body = (continuation + line).split(maxsplit=1)
        continuation = ""
        if instruction.upper() in {"ADD", "COPY", "RUN"} and re.search(r"(?:^|\s)<<-?\S", body):
            raise ValueError("unsupported Dockerfile here-document")
        instructions.append((instruction.upper(), body))
    if continuation:
        raise ValueError("unterminated Dockerfile continuation")
    return instructions


def dockerfile_pins(dockerfile: str) -> tuple[dict[str, Any], dict[str, dict[str, str]]]:
    args: dict[str, str] = {}
    aliases: set[str] = set()
    seen_stages = 0
    stage = ""
    copy_sources = []
    image_uses: dict[str, Any] = {}
    packages: dict[str, dict[str, str]] = {}
    for instruction, body in dockerfile_instructions(dockerfile):
        if instruction == "ARG" and not seen_stages:
            arg = re.fullmatch(r"([a-zA-Z_][a-zA-Z0-9_]*)(?:=(\S+))?", body)
            if arg is None:
                raise ValueError("unsupported global Dockerfile ARG")
            if arg[2] is not None:
                args[arg[1]] = arg[2]
        elif instruction == "FROM":
            declaration = re.fullmatch(
                r"(?:--platform=\S+\s+)?(\S+)(?:\s+AS\s+([a-z0-9_-]+))?", body, re.IGNORECASE
            )
            if declaration is None:
                raise ValueError("unsupported Dockerfile FROM declaration")
            image = declaration[1]
            variable = re.fullmatch(r"\$(?:([a-zA-Z_][a-zA-Z0-9_]*)|\{([a-zA-Z_][a-zA-Z0-9_]*)\})", image)
            if variable:
                image = args.get(variable[1] or variable[2], "")
            if not image or "$" in image:
                raise ValueError("unresolved Dockerfile FROM image ARG")
            alias = declaration[2].lower() if declaration[2] else None
            stage = alias or str(seen_stages)
            if image.lower() != "scratch" and image.lower() not in aliases:
                image_uses.setdefault(image, {}).setdefault("from_stages", set()).add(stage)
            if alias is not None:
                if alias in aliases:
                    raise ValueError(f"duplicate Dockerfile stage: {alias}")
                aliases.add(alias)
            seen_stages += 1
        elif instruction == "COPY":
            sources = re.findall(r"(?:^|\s)--from(?:=([^\s]+))?(?=\s|$)", body)
            if sources:
                if len(sources) != 1 or not sources[0] or "$" in sources[0] or not stage:
                    raise ValueError("unsupported Dockerfile COPY --from declaration")
                copy_sources.append((sources[0], stage))
        elif instruction == "RUN":
            for declaration in re.finditer(r"\bapk\s+add\b([^;&|]*)", body):
                options = re.fullmatch(r"\s+--no-cache\s+(.+?)\s*", declaration[1])
                if not options or not stage:
                    raise ValueError("unsupported apk add declaration in Dockerfile")
                for token in options[1].split():
                    package = re.fullmatch(r"([a-z0-9+_.-]+)=([a-zA-Z0-9+_.-]+)", token)
                    if not package:
                        raise ValueError(f"unpinned Alpine package in stage {stage}")
                    add_unique(packages.setdefault(package[1], {}), stage, package[2])
    if not seen_stages:
        raise ValueError("Dockerfile has no FROM stages")
    for image, consumer in copy_sources:
        if image.lower() not in aliases and not (re.fullmatch(r"[0-9]+", image) and int(image) < seen_stages):
            image_uses.setdefault(image, {}).setdefault("copy_stages", set()).add(consumer)
    return image_uses, packages


def image_pins(lock: dict[str, Any], runtime: dict[str, Any], image_uses: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    locked_bases = object_value(lock.get("base_images"), "base_images")
    for group, scope in (("base_images", "product"), ("scanner_images", "build")):
        for name, value in object_value(lock.get(group), group).items():
            reference = string_value(value, f"image reference for {name}")
            image, _, digest = reference.rpartition("@")
            if not image or not digest.startswith("sha256:") or not SHA256.fullmatch(digest[7:]):
                raise ValueError(f"digest-pinned image required for {name}")
            paths = [LOCK]
            if group == "base_images":
                paths.append(DOCKERFILE)
            item = entry("oci-image", scope, paths, reference=reference, digest=digest)
            if group == "base_images":
                item["pin"].update(
                    {key: sorted(stages) for key, stages in image_uses.get(reference, {}).items()}
                )
            if name == "openclaw_source":
                item["pin"]["embedded_version"] = string_value(
                    runtime.get("openclaw_core_version"), "embedded OpenClaw version"
                )
                paths.append(PLUGIN_CONTRACT)
            add_unique(result, f"image:{name}", item)
    for reference, usage in image_uses.items():
        if reference not in locked_bases.values():
            instruction = "FROM" if "from_stages" in usage else "COPY --from"
            raise ValueError(f"unlocked Dockerfile {instruction} image: {reference}")
    for name, reference in locked_bases.items():
        if reference not in image_uses:
            raise ValueError(f"locked base image is unused in the Dockerfile: {name}")
    return result


def product_pins(lock: dict[str, Any], dockerfile: str, stages: dict[str, dict[str, str]]) -> dict[str, Any]:
    himalaya = object_value(lock.get("himalaya"), "Himalaya pin")
    binary = entry(
        "binary",
        "product",
        [LOCK, DOCKERFILE],
        version=string_value(himalaya.get("version"), "Himalaya version"),
        archive_sha256=matching(himalaya.get("archive_sha256"), SHA256, "Himalaya archive digest"),
        sha256_linux_amd64=matching(himalaya.get("sha256_linux_amd64"), SHA256, "Himalaya binary digest"),
    )
    binary["source"] = string_value(himalaya.get("archive_url"), "Himalaya archive URL")

    patch = object_value(lock.get("control_ui_setup_patch"), "Control UI patch")
    files = object_value(patch.get("files"), "Control UI patch files")
    if not files:
        raise ValueError("Control UI patch files missing")
    for name, value in files.items():
        hashes = object_value(value, f"Control UI patch file {name}")
        matching(hashes.get("upstream_sha256"), SHA256, f"upstream digest for {name}")
        matching(hashes.get("patched_sha256"), SHA256, f"patched digest for {name}")
    vendor = object_value(patch.get("vendor_module"), "Control UI vendor module")
    string_value(vendor.get("path"), "Control UI vendor module path")
    matching(vendor.get("sha256"), SHA256, "Control UI vendor module digest")
    ui = entry("local-patch", "product", [LOCK, DOCKERFILE], files=files, vendor_module=vendor)
    ui["local_changes"] = [string_value(patch.get("description"), "Control UI patch description")]

    result = {"binary:himalaya": binary, "patch:control-ui": ui}
    locked = object_value(lock.get("runtime_package_pins"), "runtime_package_pins")
    for name, versions in stages.items():
        paths = [DOCKERFILE]
        if name in locked:
            paths.append(LOCK)
        unique = set(versions.values())
        if len(unique) == 1:
            pin = {"version": next(iter(unique)), "stages": sorted(versions)}
        else:
            pin = {"versions_by_stage": versions}
        result[f"alpine:{name}"] = entry("alpine-package", "product", paths, **pin)
    for name, version in locked.items():
        if set(stages.get(name, {}).values()) != {version}:
            raise ValueError(f"Alpine pin for {name} differs from Dockerfile")

    tar = re.search(r"/npm/node_modules/tar/package\.json\"\)\.version'\)\" = \"([^\"]+)\"", dockerfile)
    if tar:
        result["npm:runtime:tar"] = entry("runtime-copy", "product", [DOCKERFILE, LOCK], version=tar[1])
    return result


def npm_pins(root: Path, lock: dict[str, Any], runtime: dict[str, Any], dockerfile: str) -> dict[str, Any]:
    plugin_ids: dict[str, Any] = {}
    for plugin_id, value in object_value(runtime.get("plugins"), "plugin runtime contract").items():
        plugin = object_value(value, f"plugin {plugin_id}")
        add_unique(plugin_ids, string_value(plugin.get("package"), "contract package"), (plugin_id, plugin))

    result: dict[str, Any] = {}
    for name, group in (
        ("plugins", "immutable_openclaw_plugins"),
        ("security-overrides", "runtime_security_overrides"),
    ):
        manifest_path = f"docker/openclaw-{name}/package.json"
        lock_path = f"docker/openclaw-{name}/package-lock.json"
        manifest = read_json(root, manifest_path)
        locked_tree = object_value(lock.get(group), group)
        npm_lock = local_path(root, lock_path).read_bytes()
        expected_digest = matching(locked_tree.get("package_lock_sha256"), SHA256, f"{name} lock digest")
        if hashlib.sha256(npm_lock).hexdigest() != expected_digest:
            raise ValueError(f"{lock_path} differs from supply-chain digest")
        tree = parse_json(npm_lock.decode("utf-8"), lock_path)
        if tree.get("lockfileVersion") != 3:
            raise ValueError(f"npm lockfile version 3 required for {lock_path}")
        packages = object_value(tree.get("packages"), f"packages in {lock_path}")
        tree_root = object_value(packages.get(""), f"root declaration in {lock_path}")
        direct = object_value(manifest.get("dependencies"), f"dependencies in {manifest_path}")
        locked_direct = object_value(locked_tree.get("packages"), f"direct packages in {group}")
        if direct != tree_root.get("dependencies") or set(direct) != set(locked_direct):
            raise ValueError(f"direct npm package declarations differ for {name}")
        if name == "plugins" and set(plugin_ids) != set(direct):
            raise ValueError("runtime plugin contract differs from direct plugin dependencies")

        for path, value in packages.items():
            if not path:
                continue
            if not re.fullmatch(r"node_modules/(?:[@a-z0-9._-]+/)*[a-z0-9._-]+", path):
                raise ValueError(f"unsupported npm install path in {lock_path}")
            package = object_value(value, path)
            version = string_value(package.get("version"), f"version of {path}")
            integrity = matching(
                package.get("integrity"), re.compile(r"sha512-[a-zA-Z0-9+/=]+"), f"integrity of {path}"
            )
            package_name = path.rsplit("node_modules/", 1)[-1]
            pin = {"version": version, "integrity": integrity, "install_path": path}
            paths = [LOCK, lock_path, manifest_path, DOCKERFILE]
            kind = "npm-transitive"
            if path == f"node_modules/{package_name}" and package_name in direct:
                contract = object_value(locked_direct[package_name], f"lock for {package_name}")
                if version != direct[package_name] or (version, integrity) != (
                    contract.get("version"),
                    contract.get("integrity"),
                ):
                    raise ValueError(f"npm pin mismatch for {package_name}")
                pin["shasum"] = matching(contract.get("shasum"), COMMIT, f"shasum for {package_name}")
                if name == "plugins":
                    kind = "openclaw-plugin"
                    paths.append(PLUGIN_CONTRACT)
                    plugin_id, plugin = plugin_ids[package_name]
                    if any(pin[key] != plugin.get(key) for key in ("version", "integrity", "shasum")):
                        raise ValueError(f"plugin contract pin mismatch for {package_name}")
                    pin["runtime_id"] = plugin_id
                    pin["runtime_path"] = string_value(plugin.get("path"), f"runtime path for {package_name}")
                else:
                    kind = "npm-security-override"
                    pin["reason"] = string_value(contract.get("reason"), f"reason for {package_name}")
            for npm_flag, output in (("inBundle", "in_bundle"), ("optional", "optional"), ("dev", "dev")):
                if package.get(npm_flag) is True:
                    pin[output] = True
            copied_overlay = re.search(rf"/overrides/{re.escape(path)}(?=\s|$)", dockerfile)
            scope = "product" if (name == "plugins" and not pin.get("dev")) or copied_overlay else "build"
            item = entry(kind, scope, paths, **pin)
            item["source"] = string_value(package.get("resolved"), f"archive URL of {path}")
            if package.get("license"):
                item["license_status"] = {
                    "status": "declared",
                    "expression": package["license"],
                    "evidence": [lock_path],
                }
            result[f"npm:{name}:{path}"] = item
        missing = set(direct) - {path.removeprefix("node_modules/") for path in packages}
        if missing:
            raise ValueError(f"direct packages missing from {lock_path}: {', '.join(sorted(missing))}")

        manager = manifest.get("packageManager")
        if manager is not None:
            version = matching(manager, re.compile(r"npm@[a-zA-Z0-9.+-]+"), "npm packageManager declaration")
            add_unique(
                result,
                "build:npm",
                entry(
                    "build-tool",
                    "build",
                    [manifest_path],
                    version=version.removeprefix("npm@"),
                    resolution="manifest-declaration",
                ),
            )
    return result


def build_tool_pins(root: Path, dockerfile: str) -> dict[str, Any]:
    frontend = re.search(r"^#\s*syntax\s*=\s*(\S+)\s*$", dockerfile, re.MULTILINE)
    if not frontend:
        raise ValueError("Dockerfile frontend declaration missing")
    result = {"build:dockerfile-frontend": entry("build-tool", "build", [DOCKERFILE], reference=frontend[1])}
    bootstrap = read_source(root, BOOTSTRAP)
    pip = re.search(r'^.* -m pip install [^\n]*"(pip==[^\"]+)"', bootstrap, re.MULTILINE)
    if not pip:
        raise ValueError("bootstrap pip pin missing")
    build_system = object_value(
        tomllib.loads(read_source(root, PYPROJECT)).get("build-system"), "build system"
    )
    python_sources = (
        (REQUIREMENTS, "development", read_source(root, REQUIREMENTS).splitlines()),
        (PYPROJECT, "wheel-build", string_list(build_system.get("requires"), "build-system.requires")),
        (BOOTSTRAP, "bootstrap", [pip[1]]),
    )
    for path, role, lines in python_sources:
        seen: set[str] = set()
        for line in lines:
            requirement = line.partition("#")[0].strip()
            if not requirement:
                continue
            match = REQUIREMENT.fullmatch(requirement)
            if not match:
                raise ValueError(f"exact Python package pin required in {path}")
            name = re.sub(r"[-_.]+", "-", match[1]).lower()
            component_id = f"pypi:{name}"
            if name in seen:
                raise ValueError(f"duplicate Python package pin for {name} in {path}")
            seen.add(name)
            item = result.setdefault(
                component_id, entry("python-package", "build", [], version=match[2], roles=[])
            )
            if item["pin"]["version"] != match[2]:
                raise ValueError(f"conflicting Python package pin for {name} in {path}")
            item["source"] = f"https://pypi.org/project/{name}/"
            item["source_files"].append(path)
            item["pin"]["roles"].append(role)

    for tool in ("shellcheck", "hadolint"):
        version = re.search(rf"^{tool.upper()}_VERSION=([a-zA-Z0-9.+-]+)$", bootstrap, re.MULTILINE)
        if not version:
            raise ValueError(f"bootstrap version missing for {tool}")
        digests: dict[str, Any] = {}
        for branch in re.findall(
            r"^[ \t]+Linux-[^\n]+\)\n(.*?)^[ \t]+;;", bootstrap, re.MULTILINE | re.DOTALL
        ):
            arch = re.search(rf"^[ \t]+{tool}_arch=([a-zA-Z0-9_]+)$", branch, re.MULTILINE)
            digest = re.search(rf"^[ \t]+{tool}_sha=([0-9a-f]+)$", branch, re.MULTILINE)
            if arch is None or digest is None:
                raise ValueError(f"bootstrap archive pin missing for {tool}")
            add_unique(digests, arch[1], matching(digest[1], SHA256, f"archive digest for {tool}/{arch[1]}"))
        if not digests:
            raise ValueError(f"bootstrap archive pins missing for {tool}")
        result[f"binary:{tool}"] = entry(
            "binary", "build", [BOOTSTRAP], version=version[1], sha256_by_arch=digests
        )
    return result


def workflow_pins(root: Path, lock: dict[str, Any]) -> tuple[dict[str, Any], set[str]]:
    actions = object_value(lock.get("github_actions"), "GitHub Actions lock")
    files = [path.relative_to(root).as_posix() for path in workflow_files(local_path(root, WORKFLOWS))]
    result: dict[str, Any] = {}
    python_selectors: set[str] = set()
    python_workflows = []
    for path in files:
        workflow = read_source(root, path)
        try:
            declarations = workflow_actions(workflow)
        except ValueError as exc:
            raise ValueError(f"{path}: {exc}") from exc
        for name, revision in declarations:
            matching(revision, COMMIT, f"commit of {name} in {path}")
            if actions.get(name) != revision:
                raise ValueError(f"unlocked or mismatched GitHub Action {name} in {path}")
            item = result.setdefault(
                f"action:{name}", entry("github-action", "build", [LOCK], commit=revision)
            )
            item["source"] = f"https://github.com/{name}"
            item["source_files"].append(path)
        for value in re.findall(r"^[ \t]+python-version:[ \t]*(.+)$", workflow, re.MULTILINE):
            value = value.partition("#")[0].strip()
            if value == "${{ matrix.python-version }}":
                continue
            selected = value.removeprefix("[").removesuffix("]").split(",")
            for selector in selected:
                python_selectors.add(
                    matching(
                        selector.strip().strip("'\""),
                        re.compile(r"[0-9]+(?:\.[0-9]+)+"),
                        f"Python selector in {path}",
                    )
                )
            python_workflows.append(path)
    if {f"action:{name}" for name in actions} != set(result):
        raise ValueError("GitHub Actions lock contains entries missing from workflows")
    if python_selectors:
        result["build:workflow-python"] = entry(
            "build-tool",
            "build",
            python_workflows,
            selectors=sorted(python_selectors),
            resolution="version-selector",
        )
    return result, set(files)


def validate_registry(payload: dict[str, Any]) -> None:
    check_version(payload, "external component registry")
    components = payload.get("components")
    if not isinstance(components, list) or not components:
        raise ValueError("nonempty components list required")
    ids = set()
    previous_id = ""
    for raw in components:
        item = object_value(raw, "component entry")
        component_id = matching(item.get("id"), re.compile(r"[a-z0-9][a-zA-Z0-9:@/._+-]*"), "component ID")
        if component_id in ids:
            raise ValueError(f"duplicate component ID: {component_id}")
        if component_id < previous_id:
            raise ValueError("components must be sorted by ID")
        ids.add(component_id)
        previous_id = component_id
        if set(item) != METADATA_FIELDS | {"id", "kind", "pin", "deployment_scope"}:
            raise ValueError(f"required fields missing or unexpected fields for {component_id}")
        for field in ("kind", "owner"):
            string_value(item[field], f"{field} of {component_id}")
        for field in ("source", "update_source"):
            location = urlsplit(string_value(item[field], f"{field} of {component_id}"))
            if location.scheme != "https" or not location.hostname or location.username or location.password:
                raise ValueError(f"public HTTPS {field} required for {component_id}")
        scope = string_value(item["deployment_scope"], f"deployment scope of {component_id}")
        if scope not in DEPLOYMENT_SCOPES:
            raise ValueError(f"invalid deployment scope for {component_id}")
        if scope == "external":
            if item["pin"] is not None:
                raise ValueError(f"external installed version must be unknown for {component_id}")
        elif not object_value(item["pin"], f"pin for {component_id}"):
            raise ValueError(f"missing pin for {component_id}")
        if scope == "reference":
            matching(item["pin"].get("commit"), COMMIT, f"reference commit for {component_id}")
        if not string_list(item["source_files"], f"source files for {component_id}"):
            raise ValueError(f"source files required for {component_id}")
        for field in ("tests", "local_changes"):
            string_list(item[field], f"{field} for {component_id}")
        license_status = object_value(item["license_status"], f"license evidence for {component_id}")
        if license_status.get("status") not in ("unknown", "declared", "reviewed"):
            raise ValueError(f"invalid license status for {component_id}")
        if license_status["status"] != "unknown":
            string_value(license_status.get("expression"), f"license expression for {component_id}")
            if not string_list(license_status.get("evidence"), f"license evidence for {component_id}"):
                raise ValueError(f"license evidence required for {component_id}")
        review = object_value(item["review"], f"review for {component_id}")
        if review.get("status") == "reviewed":
            date.fromisoformat(
                matching(
                    review.get("date"), re.compile(r"\d{4}-\d{2}-\d{2}"), f"review date for {component_id}"
                )
            )
            if not string_list(review.get("evidence"), f"review evidence for {component_id}"):
                raise ValueError(f"review evidence required for {component_id}")
        elif review != {"status": "unknown"}:
            raise ValueError(f"invalid review status for {component_id}")


def build_registry(root: Path) -> dict[str, Any]:
    root = root.absolute()
    lock = read_json(root, LOCK)
    check_version(lock, LOCK)
    runtime = read_json(root, PLUGIN_CONTRACT)
    check_version(runtime, PLUGIN_CONTRACT)
    dockerfile = read_source(root, DOCKERFILE)
    image_uses, alpine_stages = dockerfile_pins(dockerfile)
    actions, workflows = workflow_pins(root, lock)
    pins: dict[str, Any] = {}
    for group in (
        image_pins(lock, runtime, image_uses),
        product_pins(lock, dockerfile, alpine_stages),
        npm_pins(root, lock, runtime, dockerfile),
        build_tool_pins(root, dockerfile),
        actions,
    ):
        for component_id, item in group.items():
            add_unique(pins, component_id, item)
    metadata = read_json(root, PROVENANCE)
    check_version(metadata, PROVENANCE)
    records = object_value(metadata.get("components"), "component provenance records")
    defaults = object_value(metadata.get("defaults", {}), "component provenance defaults")
    if missing := pins.keys() - records.keys():
        raise ValueError(f"missing provenance for {', '.join(sorted(missing))}")
    components = []
    for component_id, raw in sorted(records.items()):
        record = object_value(raw, f"provenance for {component_id}")
        derived = pins.get(component_id)
        if derived is None:
            scope = record.get("deployment_scope")
            if scope not in ("external", "reference"):
                raise ValueError(f"no pinned source for {component_id}")
            derived = {
                "kind": record.get("kind"),
                "deployment_scope": scope,
                "source_files": [],
                "pin": record.get("pin"),
            }
            record = {
                key: value for key, value in record.items() if key not in {"kind", "deployment_scope", "pin"}
            }
        profile = object_value(defaults.get(derived.get("kind"), {}), f"metadata defaults for {component_id}")
        if (record.keys() | profile.keys()) - METADATA_FIELDS:
            raise ValueError(f"unsupported metadata fields for {component_id}")
        item = {
            "id": component_id,
            "review": {"status": "unknown"},
            "license_status": {"status": "unknown"},
            "local_changes": [],
            "tests": [],
            **profile,
            **record,
            **derived,
        }
        for field in ("source_files", "tests", "local_changes"):
            item[field] = sorted(
                {
                    path
                    for values in (profile, record, derived)
                    for path in string_list(values.get(field, []), f"{field} for {component_id}")
                }
            )
        allowed_files = set(derived["source_files"]) | EVIDENCE_FILES | workflows | {PROVENANCE}
        for name in item["source_files"]:
            if name not in allowed_files or not local_path(root, name).is_file():
                raise ValueError(f"missing or unapproved source file for {component_id}: {name}")
        for name in item["tests"]:
            if not re.fullmatch(r"tests/(?:test_[a-zA-Z0-9_-]+\.py|[a-zA-Z0-9_-]+\.mjs)", name):
                raise ValueError(f"unapproved test file for {component_id}: {name}")
            if not local_path(root, name).is_file():
                raise ValueError(f"missing test file for {component_id}: {name}")
        components.append(item)
    payload = {"schema_version": 1, "components": components}
    validate_registry(payload)
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description="Offline external component registry")
    parser.add_argument("--root", type=Path, default=ROOT)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("generate", "verify"):
        commands.add_parser(name).add_argument("--root", type=Path, default=argparse.SUPPRESS)
    args = parser.parse_args()
    try:
        rendered = json.dumps(build_registry(args.root), ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        if args.command == "verify":
            try:
                found = local_path(args.root, REGISTRY).read_bytes().decode("utf-8")
            except (OSError, UnicodeError) as exc:
                raise ValueError(f"unreadable registry file: {REGISTRY}") from exc
            validate_registry(parse_json(found, REGISTRY))
            if found != rendered:
                raise ValueError(f"stale {REGISTRY}; run scripts/component_registry.py generate")
        else:
            path = local_path(args.root, REGISTRY)
            fd, temporary = tempfile.mkstemp(prefix=".external-components-", dir=path.parent)
            try:
                with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                    os.fchmod(handle.fileno(), 0o644)
                    handle.write(rendered)
                Path(temporary).replace(path)
            finally:
                Path(temporary).unlink(missing_ok=True)
    except (OSError, ValueError) as exc:
        print(f"External component registry: {exc}", file=sys.stderr)
        return 1
    print(f"External component registry {args.command}: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
