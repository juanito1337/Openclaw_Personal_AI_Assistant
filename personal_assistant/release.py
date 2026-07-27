from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1


def workspace_root(root: Path | str | None = None) -> Path:
    if root is not None:
        return Path(root).expanduser().resolve()
    return Path(__file__).resolve().parents[1]


def release_path(root: Path | str | None = None) -> Path:
    return workspace_root(root) / "RELEASE.json"


def _load_manifest(root: Path | str | None = None) -> tuple[dict[str, Any] | None, list[str]]:
    path = release_path(root)
    issues: list[str] = []
    if not path.is_file():
        return None, [f"Release-Manifest fehlt: {path}"]
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return None, [f"Release-Manifest ist unlesbar: {exc}"]
    if not isinstance(payload, dict):
        return None, ["Release-Manifest muss ein JSON-Objekt sein"]
    if payload.get("schema_version") != SCHEMA_VERSION:
        issues.append(
            f"Unbekannte Release-Schema-Version: {payload.get('schema_version')!r}; erwartet {SCHEMA_VERSION}"
        )
    version = str(payload.get("version") or "").strip()
    if not version:
        issues.append("Release-Version fehlt")
    history = payload.get("history")
    if not isinstance(history, list) or not history:
        issues.append("Release-Historie fehlt")
    elif version and str((history[0] or {}).get("version") or "") != version:
        issues.append("Erster Historieneintrag stimmt nicht mit der installierten Version ueberein")
    return payload, issues


def _document_checks(root: Path, version: str) -> dict[str, Any]:
    checks: dict[str, Any] = {}
    for name in ("AGENTS.md", "README.md", "CHANGELOG.md", "skills/personal-assistant/SKILL.md"):
        path = root / name
        try:
            text = path.read_text(encoding="utf-8")
            checks[name] = {
                "ok": bool(version and version in text),
                "path": str(path),
                "detail": "Version enthalten" if version and version in text else "Aktuelle Version fehlt",
            }
        except OSError as exc:
            checks[name] = {"ok": False, "path": str(path), "detail": str(exc)}
    return checks


def verify_release(root: Path | str | None = None) -> dict[str, Any]:
    base = workspace_root(root)
    manifest, issues = _load_manifest(base)
    if manifest is None:
        return {
            "ok": False,
            "version": "unknown",
            "manifest": str(release_path(base)),
            "issues": issues,
            "documents": {},
        }
    version = str(manifest.get("version") or "")
    documents = _document_checks(base, version)
    for name, result in documents.items():
        if not result.get("ok"):
            issues.append(f"{name}: {result.get('detail')}")
    installed_at = manifest.get("installed_at")
    warnings: list[str] = []
    if not installed_at:
        warnings.append("Installationszeit ist noch nicht gesetzt (normal in einem ungeinstallierten Paket/Staging-Verzeichnis)")
    return {
        "ok": not issues,
        "version": version or "unknown",
        "release": manifest.get("release") or "",
        "title": manifest.get("title") or "",
        "released_at": manifest.get("released_at"),
        "installed_at": installed_at,
        "previous_version": manifest.get("previous_version"),
        "installation_id": manifest.get("installation_id"),
        "manifest": str(release_path(base)),
        "issues": issues,
        "warnings": warnings,
        "documents": documents,
    }


def _history_slice(history: list[dict[str, Any]], *, since: str = "", limit: int = 10) -> tuple[list[dict[str, Any]], bool | None]:
    limit = max(1, min(int(limit), 100))
    if not since:
        return history[:limit], None
    normalized = since.strip().lower()
    match_index: int | None = None
    for index, item in enumerate(history):
        version = str(item.get("version") or "").lower()
        release = version.rsplit("-", 1)[-1]
        if normalized in {version, release}:
            match_index = index
            break
    if match_index is None:
        return history[:limit], False
    return history[:match_index][:limit], True


def release_report(
    root: Path | str | None = None,
    *,
    verify: bool = False,
    include_history: bool = False,
    since: str = "",
    limit: int = 10,
) -> dict[str, Any]:
    base = workspace_root(root)
    manifest, load_issues = _load_manifest(base)
    if manifest is None:
        return {
            "ok": False,
            "version": "unknown",
            "manifest": str(release_path(base)),
            "issues": load_issues,
        }
    validation = verify_release(base) if verify else None
    history = [item for item in manifest.get("history", []) if isinstance(item, dict)]
    selected_history, since_found = _history_slice(history, since=since, limit=limit)
    payload: dict[str, Any] = {
        "ok": bool(validation.get("ok")) if validation is not None else not load_issues,
        "version": manifest.get("version"),
        "release": manifest.get("release"),
        "product": manifest.get("product"),
        "title": manifest.get("title"),
        "released_at": manifest.get("released_at"),
        "installed_at": manifest.get("installed_at"),
        "previous_version": manifest.get("previous_version"),
        "installation_id": manifest.get("installation_id"),
        "changes": list(manifest.get("changes") or []),
        "manifest": str(release_path(base)),
    }
    if verify:
        payload["verification"] = validation
    if include_history or since:
        payload["history"] = selected_history
        payload["history_total"] = len(history)
        if since:
            payload["since"] = since
            payload["since_found"] = since_found
            if since_found is False:
                payload.setdefault("warnings", []).append(
                    f"Ausgangsversion {since!r} wurde nicht in der Release-Historie gefunden; zeige die neuesten Eintraege"
                )
    return payload


def stamp_installation(path: Path | str, *, previous_version: str | None, installation_id: str | None = None) -> dict[str, Any]:
    target = Path(path)
    payload = json.loads(target.read_text(encoding="utf-8"))
    payload["installed_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    current_version = str(payload.get("version") or "")
    if previous_version and previous_version != current_version:
        payload["previous_version"] = previous_version
    else:
        payload["previous_version"] = payload.get("previous_version") or None
    payload["installation_id"] = installation_id or payload["installed_at"]
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(target)
    return payload
