from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path
from typing import Any

from .state_paths import state_paths


def _resolved(value: str | Path) -> Path:
    return Path(value).expanduser().resolve()


def _inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _release_version(path: Path) -> str:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""
    return str(payload.get("version") or "") if isinstance(payload, dict) else ""


def _module_origin(name: str) -> str:
    spec = importlib.util.find_spec(name)
    if spec is None or not spec.origin:
        return ""
    return str(Path(spec.origin).resolve())


def runtime_identity() -> dict[str, Any]:
    runtime = os.environ.get("OPENCLAW_RUNTIME", "local")
    default_root = Path(__file__).resolve().parents[1]
    image_root = _resolved(os.environ.get("OPENCLAW_IMAGE_ROOT", default_root))
    code_root = _resolved(os.environ.get("OPENCLAW_CODE_ROOT", image_root))
    release_root = _resolved(os.environ.get("OPENCLAW_RELEASE_ROOT", default_root))
    workspace = _resolved(os.environ.get("OPENCLAW_WORKSPACE", default_root))
    state_root = _resolved(os.environ.get("OPENCLAW_STATE_ROOT", workspace.parent))
    source_revision = _text(image_root / "SOURCE_REVISION") or "unknown"
    oci_revision = os.environ.get("OPENCLAW_IMAGE_REVISION", "").strip() or "unknown"
    version_file = _text(release_root / "VERSION") or "unknown"
    release_manifest = release_root / "RELEASE.json"
    release_version = _release_version(release_manifest) or "unknown"
    module_paths = {
        "personal_assistant": _module_origin("personal_assistant"),
        "mail_agent": _module_origin("mail_agent"),
    }
    executable_paths = {
        "python": str(Path(sys.executable).resolve()),
        "assistant": str(image_root / "scripts/assistant.sh"),
        "mail_agent": str(image_root / "scripts/mail-agent.sh"),
        "worker_loop": str(image_root / "docker/job_loop.py"),
    }
    issues: list[str] = []
    if version_file != release_version:
        issues.append(
            f"VERSION ({version_file}) stimmt nicht mit RELEASE.json ({release_version}) ueberein"
        )
    if runtime == "container":
        if release_root != image_root:
            issues.append("Release-Manifest wird nicht aus dem Image gelesen")
        if code_root != image_root:
            issues.append("Code-Root stimmt nicht mit dem Image-Root ueberein")
        if image_root == state_root or _inside(image_root, state_root):
            issues.append("Image-Code liegt innerhalb des beschreibbaren State-Roots")
        if source_revision == "unknown" or oci_revision == "unknown":
            issues.append("Image-/OCI-Revision fehlt")
        elif source_revision != oci_revision:
            issues.append(
                f"OCI-Revision ({oci_revision}) stimmt nicht mit SOURCE_REVISION ({source_revision}) ueberein"
            )
        for name, value in module_paths.items():
            origin = _resolved(value) if value else None
            if origin is None or not _inside(origin, code_root):
                issues.append(f"Python-Modul {name} wird nicht aus dem Image-Codepfad geladen: {value}")
            elif _inside(origin, state_root):
                issues.append(f"Python-Modul {name} wird aus dem beschreibbaren State geladen")
        for name, value in executable_paths.items():
            if name == "python":
                continue
            path = _resolved(value)
            if not _inside(path, image_root):
                issues.append(f"Executable {name} liegt nicht im Image: {path}")
        for value in sys.path:
            if not value:
                issues.append("Unsicherer leerer Python-Suchpfad ist im Container aktiv")
                continue
            try:
                candidate = _resolved(value)
            except OSError:
                continue
            if candidate == workspace or _inside(candidate, workspace):
                issues.append(f"Beschreibbarer Workspace ist im Python-Suchpfad: {candidate}")
    return {
        "ok": not issues,
        "runtime": runtime,
        "layout": 3 if runtime == "container" else None,
        "role": (
            os.environ.get("OPENCLAW_ROLE", "agent-cli")
            if runtime == "container" else None
        ),
        "state_paths": state_paths().as_dict(),
        "image_root": str(image_root),
        "code_root": str(code_root),
        "state_root": str(state_root),
        "workspace": str(workspace),
        "release_root": str(release_root),
        "release_manifest": str(release_manifest),
        "release_version": release_version,
        "version_file": version_file,
        "oci_revision": oci_revision,
        "source_revision": source_revision,
        "python_safe_path": bool(getattr(sys.flags, "safe_path", False)),
        "module_paths": module_paths,
        "executable_paths": executable_paths,
        "issues": issues,
    }
