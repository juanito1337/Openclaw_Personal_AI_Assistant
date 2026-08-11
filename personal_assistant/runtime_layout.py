from __future__ import annotations

import argparse
import fcntl
import hashlib
import io
import json
import os
import re
import shutil
import sqlite3
import tarfile
import time
import tomllib
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from .ollama_priority_config import (
    ensure_gateway_timeouts,
    ensure_model_override_timeouts,
    normalize_gateway_base_url,
    normalize_model_overrides,
    read_mail_base_url,
    set_mail_base_url,
)

LAYOUT_VERSION = 3
LAYOUT_SCHEMA = 1
MARKER_NAME = ".container-layout.json"
LEGACY_LAYOUT_VERSION = 1
MUTABLE_PACKAGE_ROOTS = {
    "mail_agent": {"config.toml", "rules.toml", "data"},
    "personal_assistant": {
        "config.toml",
        "resources.toml",
        "policies.toml",
        "tools.toml",
        "data",
    },
}
CONFIG_TEMPLATES = {
    "mail_agent/config.toml": "mail_agent/config.example.toml",
    "mail_agent/rules.toml": "mail_agent/rules.example.toml",
    "personal_assistant/config.toml": "personal_assistant/config.example.toml",
    "personal_assistant/resources.toml": "personal_assistant/resources.example.toml",
    "personal_assistant/policies.toml": "personal_assistant/policies.example.toml",
    "personal_assistant/tools.toml": "personal_assistant/tools.example.toml",
}
RELEASE_DOCUMENT_LINKS = {
    "AGENTS.md": "AGENTS.md",
    "HEARTBEAT.md": "HEARTBEAT.md",
}
RELEASE_SKILL_RELATIVE = "skills/personal-assistant"
OBSOLETE_RELEASE_DOCUMENTS = ("README.md", "CHANGELOG.md", "RELEASE.json", "VERSION")
V3_ROOT_NAME = "v3"
V3_DIRECTORIES = (
    "instance/mail_agent",
    "instance/personal_assistant",
    "gateway",
    "domains/mail",
    "domains/orders",
    "domains/portfolio",
    "domains/monitoring",
    "domains/knowledge",
    "shared/core",
    "shared/security",
    "shared/coordination/container_jobs",
    "shared/coordination/container_logs",
)
CONTAINER_OLLAMA_BASE_URL = "http://ollama-proxy:11435"
CONTAINER_OLLAMA_PROVIDER_TIMEOUT_SECONDS = 1800
CONTAINER_AGENT_TIMEOUT_SECONDS = 3600
CONTAINER_GATEWAY_WORKSPACE = "/home/node/.openclaw/workspace"
CONTAINER_SKILL_ROOT = "/opt/openclaw-agent/skills"
CONTAINER_TOOL_PATHS = {
    ("nextcloud.workspace", "outbox"): "/var/lib/openclaw/core/workspace_outbox",
    ("nextcloud.deck_orders", "database"): "/var/lib/openclaw/orders/orders.sqlite3",
    ("security.antivirus", "temp_dir"): "/var/lib/openclaw/security/tmp",
    ("portfolio", "database"): "/var/lib/openclaw/portfolio/portfolio.sqlite3",
    ("portfolio", "import_root"): "/var/lib/openclaw/portfolio/inbox",
}
WORKSPACE_PROFILE_FILENAMES = ("IDENTITY.md", "SOUL.md", "USER.md")
WORKSPACE_SETUP_STATE_FILENAME = "openclaw-workspace-state.json"
WORKSPACE_ATTESTATION_HEADER = "openclaw-workspace-attestation:v1"
MAX_WORKSPACE_PROFILE_BYTES = 2 * 1024 * 1024
MAX_WORKSPACE_ATTESTATION_BYTES = 2048


@dataclass(slots=True)
class LayoutReport:
    ok: bool
    layout: int
    previous_layout: int
    image_root: str
    state_root: str
    workspace: str
    release: str
    revision: str
    backup: str | None
    backup_sha256: str | None
    removed_runtime_paths: list[str]
    release_links: dict[str, str]
    created_configs: list[str]
    changed: bool


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_text(path: Path, default: str = "") -> str:
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return default


def _read_layout(marker: Path) -> int:
    if not marker.exists():
        return LEGACY_LAYOUT_VERSION
    try:
        payload = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Container-Layoutmarker ist unlesbar: {marker}: {exc}") from exc
    layout = payload.get("layout") if isinstance(payload, dict) else None
    if not isinstance(layout, int) or layout < LEGACY_LAYOUT_VERSION:
        raise RuntimeError(f"Container-Layoutmarker enthaelt keine gueltige Layoutversion: {marker}")
    return layout


@contextmanager
def _layout_lock(state_root: Path, timeout: float = 60.0) -> Iterator[None]:
    lock_path = state_root / ".container-layout.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+b") as handle:
        deadline = time.monotonic() + timeout
        while True:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError as exc:
                if time.monotonic() >= deadline:
                    raise TimeoutError(
                        f"Container-Layoutsperre blieb belegt: {lock_path}"
                    ) from exc
                time.sleep(0.1)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _validate_roots(image_root: Path, state_root: Path, workspace: Path) -> None:
    if not image_root.is_dir():
        raise RuntimeError(f"Image-Root fehlt: {image_root}")
    if image_root == state_root or _is_relative_to(image_root, state_root):
        raise RuntimeError("Image-Root darf nicht im beschreibbaren State liegen")
    if not _is_relative_to(workspace, state_root):
        raise RuntimeError(f"Workspace liegt nicht im State-Root: {workspace}")
    for source in RELEASE_DOCUMENT_LINKS.values():
        target = image_root / source
        if not target.exists():
            raise RuntimeError(f"Erforderliches Release-Dokument fehlt im Image: {target}")
    skill = image_root / RELEASE_SKILL_RELATIVE / "SKILL.md"
    if not skill.is_file():
        raise RuntimeError(f"Erforderlicher Runtime-Skill fehlt im Image: {skill}")
    for source in CONFIG_TEMPLATES.values():
        target = image_root / source
        if not target.is_file():
            raise RuntimeError(f"Konfigurationsvorlage fehlt im Image: {target}")


def _legacy_runtime_paths(image_root: Path, state_root: Path, workspace: Path) -> list[Path]:
    selected: set[Path] = set()
    scripts = workspace / "scripts"
    if scripts.exists() or scripts.is_symlink():
        selected.add(scripts)
    for package, mutable_names in MUTABLE_PACKAGE_ROOTS.items():
        source_root = image_root / package
        target_root = workspace / package
        if target_root.is_symlink():
            selected.add(target_root)
            continue
        if not target_root.is_dir():
            continue
        for source in source_root.rglob("*"):
            if not (source.is_file() or source.is_symlink()):
                continue
            relative = source.relative_to(source_root)
            if relative.parts and relative.parts[0] in mutable_names:
                continue
            target = target_root / relative
            if target.exists() or target.is_symlink():
                selected.add(target)
        for cache in target_root.rglob("__pycache__"):
            if cache.is_dir() and not cache.is_symlink():
                selected.add(cache)
        for compiled in target_root.rglob("*.py[co]"):
            if compiled.is_file() or compiled.is_symlink():
                selected.add(compiled)
    for link_name in (
        *RELEASE_DOCUMENT_LINKS,
        RELEASE_SKILL_RELATIVE,
        *OBSOLETE_RELEASE_DOCUMENTS,
    ):
        target = workspace / link_name
        if target.exists() or target.is_symlink():
            selected.add(target)
    legacy_marker = state_root / ".container-source-version"
    if legacy_marker.exists() or legacy_marker.is_symlink():
        selected.add(legacy_marker)
    ordered = sorted(selected, key=lambda item: (len(item.parts), item.as_posix()))
    compact: list[Path] = []
    for candidate in ordered:
        if not any(_is_relative_to(candidate, parent) for parent in compact):
            compact.append(candidate)
    return compact


def _archive_name(path: Path, state_root: Path) -> str:
    return path.relative_to(state_root).as_posix()


def _verified_backup(
    state_root: Path,
    candidates: list[Path],
    previous_layout: int,
    *,
    archive_base: Path | None = None,
) -> tuple[Path, str]:
    backup_root = state_root / ".layout-migrations" / "backups"
    backup_root.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S.%fZ")
    archive = backup_root / f"layout-v{previous_layout}-to-v{LAYOUT_VERSION}-{stamp}.tar.gz"
    temporary = archive.with_suffix(archive.suffix + ".tmp")
    members: list[str] = []
    with tarfile.open(temporary, "w:gz", dereference=False) as handle:
        for candidate in candidates:
            if not (candidate.exists() or candidate.is_symlink()):
                continue
            name = (
                candidate.relative_to(archive_base).as_posix()
                if archive_base is not None
                else _archive_name(candidate, state_root)
            )
            handle.add(candidate, arcname=name, recursive=True)
            members.append(name)
        manifest = json.dumps(
            {
                "schema": 1,
                "from_layout": previous_layout,
                "to_layout": LAYOUT_VERSION,
                "members": members,
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ).encode("utf-8") + b"\n"
        info = tarfile.TarInfo("MIGRATION_MANIFEST.json")
        info.size = len(manifest)
        info.mode = 0o600
        info.mtime = int(time.time())
        handle.addfile(info, io.BytesIO(manifest))
    os.chmod(temporary, 0o600)
    temporary.replace(archive)
    digest = _sha256(archive)
    checksum = archive.with_suffix(archive.suffix + ".sha256")
    checksum.write_text(f"{digest}  {archive.name}\n", encoding="utf-8")
    os.chmod(checksum, 0o600)
    if _sha256(archive) != digest:
        raise RuntimeError(f"Fixture-/Layout-Sicherung konnte nicht verifiziert werden: {archive}")
    with tarfile.open(archive, "r:gz") as handle:
        if "MIGRATION_MANIFEST.json" not in handle.getnames():
            raise RuntimeError(f"Layout-Sicherung enthaelt kein Manifest: {archive}")
    return archive, digest


def _sqlite_database(path: Path) -> bool:
    try:
        if not path.is_file():
            return False
        with path.open("rb") as handle:
            return handle.read(16) == b"SQLite format 3\x00"
    except OSError:
        return False


def _sqlite_integrity(path: Path) -> None:
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=10)
    try:
        row = connection.execute("PRAGMA quick_check").fetchone()
        if not row or str(row[0]) != "ok":
            raise RuntimeError(f"SQLite-Integritaetspruefung fehlgeschlagen: {path}: {row}")
    finally:
        connection.close()


def _consistent_copy(source: Path, target: Path) -> None:
    """Copy one tree while checkpointing SQLite databases through backup()."""
    if source.is_symlink():
        target.parent.mkdir(parents=True, exist_ok=True)
        target.symlink_to(os.readlink(source), target_is_directory=source.is_dir())
        return
    if source.is_dir():
        target.mkdir(parents=True, exist_ok=True)
        for child in sorted(source.iterdir(), key=lambda item: item.name):
            if child.name.endswith(("-wal", "-shm")):
                continue
            _consistent_copy(child, target / child.name)
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    if _sqlite_database(source):
        source_db = sqlite3.connect(f"file:{source}?mode=ro", uri=True, timeout=30)
        target_db = sqlite3.connect(target)
        try:
            source_db.execute("PRAGMA busy_timeout=30000")
            source_db.backup(target_db)
        finally:
            target_db.close()
            source_db.close()
        _sqlite_integrity(target)
    else:
        shutil.copy2(source, target, follow_symlinks=False)


def _backup_candidates(
    state_root: Path,
    workspace: Path,
    *,
    include_v3: bool = False,
) -> list[Path]:
    candidates = [] if include_v3 else [workspace]
    for child in sorted(state_root.iterdir(), key=lambda item: item.name):
        if child == workspace or child.name in {
            ".container-layout.lock",
            ".layout-migrations",
        }:
            continue
        if child.name == MARKER_NAME and not include_v3:
            continue
        if child.name == V3_ROOT_NAME and not include_v3:
            continue
        candidates.append(child)
    return candidates


def _verified_state_backup(
    state_root: Path,
    workspace: Path,
    previous_layout: int,
    *,
    include_v3: bool = False,
) -> tuple[Path, str]:
    snapshot = state_root / ".layout-migrations" / f"snapshot-{os.getpid()}"
    snapshot.mkdir(parents=True, exist_ok=False)
    try:
        for candidate in _backup_candidates(
            state_root, workspace, include_v3=include_v3
        ):
            _consistent_copy(candidate, snapshot / _archive_name(candidate, state_root))
        return _verified_backup(
            state_root,
            sorted(snapshot.iterdir()),
            previous_layout,
            archive_base=snapshot,
        )
    finally:
        shutil.rmtree(snapshot, ignore_errors=True)


def _preflight_v3(state_root: Path, workspace: Path) -> None:
    if not os.access(state_root, os.W_OK | os.X_OK):
        raise RuntimeError(f"State-Root ist nicht beschreibbar: {state_root}")
    if os.geteuid() != 0:
        for path in (state_root, workspace):
            stat = path.stat()
            if stat.st_uid != os.geteuid() or stat.st_gid != os.getegid():
                raise RuntimeError(
                    f"{path} gehoert UID:GID {stat.st_uid}:{stat.st_gid}, "
                    f"erwartet {os.geteuid()}:{os.getegid()}"
                )
    bytes_used = 0
    for candidate in _backup_candidates(state_root, workspace):
        paths = candidate.rglob("*") if candidate.is_dir() else (candidate,)
        for path in paths:
            if path.is_file() and not path.is_symlink():
                bytes_used += path.stat().st_size
                if _sqlite_database(path):
                    _sqlite_integrity(path)
    required = max(1_048_576, bytes_used * 3)
    free = shutil.disk_usage(state_root).free
    if free < required:
        raise RuntimeError(
            f"Zu wenig freier Speicher fuer Layoutmigration: {free} < {required} Bytes"
        )


def _copy_if_present(source: Path, target: Path) -> None:
    if source.exists() or source.is_symlink():
        _consistent_copy(source, target)


def _validate_workspace_profile_file(path: Path) -> None:
    if path.is_symlink() or not path.is_file():
        raise RuntimeError(f"Workspace-Profildatei muss eine regulaere Datei sein: {path}")
    if path.stat().st_size > MAX_WORKSPACE_PROFILE_BYTES:
        raise RuntimeError(f"Workspace-Profildatei ist zu gross: {path}")


def _atomic_copy_profile(source: Path, target: Path) -> None:
    _validate_workspace_profile_file(source)
    if target.is_symlink() or (target.exists() and not target.is_file()):
        raise RuntimeError(f"Workspace-Profilziel ist keine regulaere Datei: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(
        f".{target.name}.profile-{os.getpid()}-{time.monotonic_ns()}"
    )
    try:
        shutil.copy2(source, temporary, follow_symlinks=False)
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)


def _read_workspace_setup_state(path: Path) -> dict[str, object] | None:
    if not path.exists() and not path.is_symlink():
        return None
    _validate_workspace_profile_file(path)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Workspace-Setupstatus ist ungueltig: {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"Workspace-Setupstatus muss ein JSON-Objekt sein: {path}")
    return payload


def _workspace_setup_completed(payload: dict[str, object]) -> bool:
    value = payload.get("setupCompletedAt") or payload.get("onboardingCompletedAt")
    return isinstance(value, str) and bool(value.strip())


def _workspace_bootstrap_seeded(payload: dict[str, object]) -> bool:
    value = payload.get("bootstrapSeededAt")
    return isinstance(value, str) and bool(value.strip())


def _workspace_setup_is_canonical_pending(payload: dict[str, object]) -> bool:
    version = payload.get("version")
    return (
        set(payload) == {"version", "bootstrapSeededAt"}
        and isinstance(version, int)
        and not isinstance(version, bool)
        and version == 1
        and _workspace_bootstrap_seeded(payload)
    )


def _workspace_attestation_hashes(
    gateway: Path,
) -> dict[str, str]:
    workspace_id = hashlib.sha256(
        CONTAINER_GATEWAY_WORKSPACE.encode("utf-8")
    ).hexdigest()
    path = gateway / "workspace-attestations" / f"{workspace_id}.attested"
    if not path.is_file() or path.is_symlink():
        return {}
    if path.stat().st_size > MAX_WORKSPACE_ATTESTATION_BYTES:
        raise RuntimeError(f"Workspace-Attestierung ist zu gross: {path}")
    lines = path.read_text(encoding="utf-8").splitlines()
    if not lines or lines[0] != WORKSPACE_ATTESTATION_HEADER:
        raise RuntimeError(f"Workspace-Attestierung ist ungueltig: {path}")
    hashes: dict[str, str] = {}
    for line in lines[1:]:
        if not line.startswith("generated:"):
            continue
        _prefix, separator, remainder = line.partition(":")
        filename, digest_separator, digest = remainder.rpartition(":")
        if (
            not separator
            or not digest_separator
            or not filename
            or len(digest) != 64
            or any(character not in "0123456789abcdef" for character in digest)
        ):
            raise RuntimeError(f"Workspace-Attestierung enthaelt einen ungueltigen Eintrag: {path}")
        if filename in hashes:
            raise RuntimeError(f"Workspace-Attestierung enthaelt einen doppelten Eintrag: {path}")
        hashes[filename] = digest
    return hashes


def _promote_completed_legacy_workspace_profile(
    instance: Path,
    gateway: Path,
) -> bool:
    """Recover a completed profile quarantined by the former layout-v3 rule.

    An already completed active profile always wins. A differing pending file
    may only be replaced when OpenClaw's workspace attestation proves that its
    current contents were generated automatically. Legacy sources remain in
    local-workspace as recovery evidence.
    """

    legacy = instance / "local-workspace"
    legacy_state_path = legacy / WORKSPACE_SETUP_STATE_FILENAME
    legacy_state = _read_workspace_setup_state(legacy_state_path)
    if legacy_state is None or not _workspace_setup_completed(legacy_state):
        return False

    active_state_path = instance / WORKSPACE_SETUP_STATE_FILENAME
    active_state = _read_workspace_setup_state(active_state_path)
    if active_state is not None and _workspace_setup_completed(active_state):
        return False
    if active_state is not None and not _workspace_setup_is_canonical_pending(
        active_state
    ):
        raise RuntimeError(
            "Aktiver Workspace-Setupstatus ist kein unveraenderter "
            "OpenClaw-Bootstrapstatus; Legacy-Profil wird nicht automatisch "
            "daruebergeschrieben"
        )
    if active_state is not None and not (instance / "BOOTSTRAP.md").is_file():
        raise RuntimeError(
            "Aktiver Bootstrap wurde bereits bearbeitet oder entfernt; "
            "Legacy-Profil wird nicht automatisch daruebergeschrieben"
        )

    sources = [
        legacy / filename
        for filename in WORKSPACE_PROFILE_FILENAMES
        if (legacy / filename).exists() or (legacy / filename).is_symlink()
    ]
    if not sources:
        raise RuntimeError(
            "Abgeschlossener Legacy-Setupstatus existiert ohne Workspace-Profildatei"
        )
    for source in [*sources, legacy_state_path]:
        _validate_workspace_profile_file(source)

    attested = _workspace_attestation_hashes(gateway)
    replacements: list[tuple[Path, Path]] = []
    for source in sources:
        target = instance / source.name
        if target.is_symlink() or (target.exists() and not target.is_file()):
            raise RuntimeError(f"Workspace-Profilziel ist keine regulaere Datei: {target}")
        if target.is_file() and _sha256(target) == _sha256(source):
            continue
        if target.is_file() and attested.get(source.name) != _sha256(target):
            raise RuntimeError(
                f"Aktive Workspace-Profildatei wurde veraendert; "
                f"Legacy-Profil wird nicht daruebergeschrieben: {target}"
            )
        replacements.append((source, target))

    state_differs = (
        not active_state_path.is_file()
        or _sha256(active_state_path) != _sha256(legacy_state_path)
    )
    for source, target in replacements:
        _atomic_copy_profile(source, target)
    if state_differs:
        _atomic_copy_profile(legacy_state_path, active_state_path)
    return bool(replacements or state_differs)


def _prune_assistant_database(path: Path, tables: tuple[str, ...]) -> None:
    compacted = path.with_name(
        f".{path.name}.vacuum-{os.getpid()}-{time.monotonic_ns()}"
    )
    try:
        connection = sqlite3.connect(path)
        try:
            connection.execute("PRAGMA foreign_keys=OFF")
            for table in tables:
                connection.execute(f'DROP TABLE IF EXISTS "{table}"')
            connection.commit()
            # Keep the compacted copy explicitly beside the staged database.
            # This ties all required capacity to the state filesystem checked
            # by the migration preflight instead of relying on SQLite's
            # implicit transient-file placement.
            connection.execute("VACUUM INTO ?", (str(compacted),))
        finally:
            connection.close()
        _sqlite_integrity(compacted)
        os.chmod(compacted, path.stat().st_mode & 0o777)
        compacted.replace(path)
    finally:
        compacted.unlink(missing_ok=True)
    _sqlite_integrity(path)


def _clear_v3_staging(state_root: Path) -> None:
    staging_root = state_root / ".layout-migrations" / "staging"
    if not staging_root.exists():
        return
    for candidate in staging_root.iterdir():
        if (
            not candidate.name.startswith("v3-")
            or candidate.is_symlink()
            or not candidate.is_dir()
        ):
            raise RuntimeError(
                f"Unerwarteter Eintrag im Layout-Staging; Restore/Pruefung erforderlich: {candidate}"
            )
        shutil.rmtree(candidate)


def _split_assistant_database(source: Path, stage: Path) -> None:
    if source.is_symlink():
        raise RuntimeError(
            f"Assistant-Datenbank darf fuer die Layoutmigration kein Symlink sein: {source}"
        )
    seed = source
    temporary_seed = stage / ".combined-assistant.sqlite3"
    if not source.is_file():
        from .storage import AssistantStorage

        knowledge_root = os.environ.pop("OPENCLAW_KNOWLEDGE_DATA_DIR", None)
        try:
            AssistantStorage(temporary_seed).close()
        finally:
            if knowledge_root is not None:
                os.environ["OPENCLAW_KNOWLEDGE_DATA_DIR"] = knowledge_root
        seed = temporary_seed

    core = stage / "shared/core/assistant.sqlite3"
    knowledge = stage / "domains/knowledge/knowledge.sqlite3"
    _consistent_copy(seed, core)
    _consistent_copy(seed, knowledge)
    _prune_assistant_database(
        core,
        ("knowledge_fts", "chunks", "documents", "sync_state"),
    )
    _prune_assistant_database(
        knowledge,
        ("action_plans", "audit_log", "settings_history", "resources"),
    )
    for suffix in ("", "-wal", "-shm"):
        Path(f"{temporary_seed}{suffix}").unlink(missing_ok=True)


def _build_v3_stage(image_root: Path, state_root: Path, workspace: Path) -> Path:
    staging_root = state_root / ".layout-migrations" / "staging"
    staging_root.mkdir(parents=True, exist_ok=True)
    stage = staging_root / f"v3-{os.getpid()}-{time.monotonic_ns()}"
    stage.mkdir(mode=0o700)
    for relative in V3_DIRECTORIES:
        (stage / relative).mkdir(parents=True, exist_ok=True)

    for relative in CONFIG_TEMPLATES:
        source = workspace / relative
        target = stage / "instance" / relative
        if source.exists() or source.is_symlink():
            _consistent_copy(source, target)
        else:
            _consistent_copy(image_root / CONFIG_TEMPLATES[relative], target)
    _copy_if_present(
        workspace / "personal_assistant/resources.toml",
        stage / "shared/core/resources.toml",
    )
    if not (stage / "shared/core/resources.toml").exists():
        _consistent_copy(
            image_root / CONFIG_TEMPLATES["personal_assistant/resources.toml"],
            stage / "shared/core/resources.toml",
        )

    for filename in (*WORKSPACE_PROFILE_FILENAMES, WORKSPACE_SETUP_STATE_FILENAME):
        source = workspace / filename
        if source.exists() or source.is_symlink():
            _atomic_copy_profile(source, stage / "instance" / filename)

    mail_source = workspace / "mail_agent/data"
    if mail_source.exists():
        _consistent_copy(mail_source, stage / "domains/mail")
    _copy_if_present(
        workspace / "mail_agent/learning_folders.json",
        stage / "domains/mail/learning_folders.json",
    )

    assistant_data = workspace / "personal_assistant/data"
    _split_assistant_database(assistant_data / "assistant.sqlite3", stage)
    destinations = {
        "assistant.log": "shared/core/assistant.log",
        "action_payloads": "shared/core/action_payloads",
        "workspace_outbox": "shared/core/workspace_outbox",
        "orders.sqlite3": "domains/orders/orders.sqlite3",
        "portfolio.sqlite3": "domains/portfolio/portfolio.sqlite3",
        "portfolio_inbox": "domains/portfolio/inbox",
        "monitoring.sqlite3": "domains/monitoring/monitoring.sqlite3",
        "antivirus.sqlite3": "shared/security/antivirus.sqlite3",
        "antivirus_tmp": "shared/security/tmp",
        "work_scheduler.sqlite3": "shared/coordination/work_scheduler.sqlite3",
        "job_control.json": "shared/coordination/job_control.json",
        "container_jobs": "shared/coordination/container_jobs",
        "container_logs": "shared/coordination/container_logs",
    }
    known = {*destinations, "assistant.sqlite3"}
    if assistant_data.exists():
        for name, relative in destinations.items():
            _copy_if_present(assistant_data / name, stage / relative)
        for child in sorted(assistant_data.iterdir(), key=lambda item: item.name):
            if child.name in known or child.name.endswith(("-wal", "-shm")):
                continue
            _consistent_copy(child, stage / "shared/core/legacy-data" / child.name)

    reserved = {
        "scripts", "mail_agent", "personal_assistant", "skills",
        *WORKSPACE_PROFILE_FILENAMES,
        WORKSPACE_SETUP_STATE_FILENAME,
        *RELEASE_DOCUMENT_LINKS, *OBSOLETE_RELEASE_DOCUMENTS,
    }
    for child in sorted(workspace.iterdir(), key=lambda item: item.name):
        if child.name in reserved:
            continue
        _consistent_copy(child, stage / "instance/local-workspace" / child.name)

    for child in sorted(state_root.iterdir(), key=lambda item: item.name):
        if child == workspace or child.name in {
            MARKER_NAME,
            ".container-layout.lock",
            ".layout-migrations",
            V3_ROOT_NAME,
        }:
            continue
        _consistent_copy(child, stage / "gateway" / child.name)

    for database in stage.rglob("*.sqlite3"):
        if _sqlite_database(database):
            _sqlite_integrity(database)
    marker = stage / "instance/.layout-version.json"
    marker.write_text('{"layout":3,"schema":1}\n', encoding="utf-8")
    os.chmod(marker, 0o600)
    return stage


def _publish_v3(state_root: Path, stage: Path) -> None:
    target = state_root / V3_ROOT_NAME
    if target.exists():
        raise RuntimeError(f"Unveroeffentlichtes Layout-v3-Ziel existiert bereits: {target}")
    stage.replace(target)


def _normalize_container_mail_config(workspace: Path) -> bool:
    """Enforce the internal proxy endpoint in the active container instance.

    The host-to-container migration already rewrites the legacy workspace.  The
    v3 layout owns a second, staged copy and is also reused across later image
    upgrades.  Normalizing that active copy here prevents an old host-loopback
    URL from surviving either the first publication or a subsequent restart.
    """

    path = workspace / "mail_agent/config.toml"
    if not path.is_file():
        return False
    text = path.read_text(encoding="utf-8")
    if not any(
        line.strip() == "[ollama]" for line in text.splitlines()
    ):
        return False
    current = read_mail_base_url(path)
    if current == CONTAINER_OLLAMA_BASE_URL:
        return False
    set_mail_base_url(path, CONTAINER_OLLAMA_BASE_URL)
    return True


def _normalize_container_tool_paths(workspace: Path) -> bool:
    """Repair only mount-owned paths while preserving instance grants.

    Layout-v3 runtime paths are fixed by Compose and are not user-selectable
    permissions.  Keep every other tools.toml value byte-for-byte so an image
    update cannot grant a capability or replace a resource selection.
    """

    path = workspace / "personal_assistant/tools.toml"
    if not path.exists() and not path.is_symlink():
        return False
    if path.is_symlink() or not path.is_file():
        raise RuntimeError(f"Aktive Toolkonfiguration muss eine regulaere Datei sein: {path}")
    try:
        parsed = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise RuntimeError(f"Aktive Toolkonfiguration ist ungueltig: {path}: {exc}") from exc

    required: set[tuple[str, str]] = set()
    for (section, key), _expected in CONTAINER_TOOL_PATHS.items():
        current: object = parsed
        for name in section.split("."):
            if not isinstance(current, dict) or name not in current:
                current = None
                break
            current = current[name]
        if isinstance(current, dict) and key in current:
            if not isinstance(current[key], str):
                raise RuntimeError(
                    f"Aktive Toolkonfiguration erwartet String fuer [{section}] {key}: {path}"
                )
            required.add((section, key))

    table_pattern = re.compile(r"^\s*\[([^\]]+)]\s*(?:#.*)?(?:\r?\n)?$")
    assignment_pattern = re.compile(
        r"^(?P<prefix>\s*(?P<key>[A-Za-z0-9_-]+)\s*=\s*)"
        r"(?P<value>\"(?:\\.|[^\"\\])*\"|'[^']*')"
        r"(?P<suffix>\s*(?:#.*)?)(?P<newline>\r?\n)?$"
    )
    section = ""
    found: set[tuple[str, str]] = set()
    changed = False
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    for index, line in enumerate(lines):
        table = table_pattern.match(line)
        if table:
            section = table.group(1).strip()
            continue
        assignment = assignment_pattern.match(line)
        if assignment is None:
            continue
        identity = (section, assignment.group("key"))
        if identity not in required:
            continue
        if identity in found:
            raise RuntimeError(f"Doppelter Toolpfad in aktiver Konfiguration: {identity}")
        found.add(identity)
        expected = CONTAINER_TOOL_PATHS[identity]
        if assignment.group("value") in {json.dumps(expected), repr(expected)}:
            continue
        lines[index] = (
            f"{assignment.group('prefix')}{json.dumps(expected)}"
            f"{assignment.group('suffix')}{assignment.group('newline') or ''}"
        )
        changed = True

    missing = required - found
    if missing:
        rendered = ", ".join(f"[{section}] {key}" for section, key in sorted(missing))
        raise RuntimeError(
            "Aktive Toolpfade koennen nicht sicher normalisiert werden: " + rendered
        )
    if not changed:
        return False

    temporary = path.with_name(f".{path.name}.paths-{os.getpid()}-{time.monotonic_ns()}")
    try:
        temporary.write_text("".join(lines), encoding="utf-8")
        os.chmod(temporary, path.stat().st_mode & 0o777)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
    return True


def _normalize_container_gateway_config(gateway: Path) -> bool:
    """Enforce the internal proxy in active global and per-agent config."""

    config_path = gateway / "openclaw.json"
    global_changed = normalize_gateway_base_url(
        config_path,
        CONTAINER_OLLAMA_BASE_URL,
    )
    timeout_changed = ensure_gateway_timeouts(
        config_path,
        provider_timeout_seconds=CONTAINER_OLLAMA_PROVIDER_TIMEOUT_SECONDS,
        agent_timeout_seconds=CONTAINER_AGENT_TIMEOUT_SECONDS,
    )
    skills_changed = _normalize_container_skill_root(config_path)
    override_changes = normalize_model_overrides(
        gateway / "agents",
        CONTAINER_OLLAMA_BASE_URL,
    )
    override_timeout_changes = ensure_model_override_timeouts(
        gateway / "agents",
        provider_timeout_seconds=CONTAINER_OLLAMA_PROVIDER_TIMEOUT_SECONDS,
    )
    return bool(
        global_changed
        or timeout_changed
        or skills_changed
        or override_changes
        or override_timeout_changes
    )


def _normalize_container_skill_root(path: Path) -> bool:
    """Expose the immutable image skill through OpenClaw's shared-skill API."""

    if not path.exists() and not path.is_symlink():
        return False
    if path.is_symlink() or not path.is_file():
        raise RuntimeError(f"OpenClaw-Konfiguration muss eine regulaere Datei sein: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError(f"openclaw.json muss ein JSON-Objekt enthalten: {path}")
    skills = payload.get("skills")
    if skills is None:
        skills = {}
        payload["skills"] = skills
    if not isinstance(skills, dict):
        raise RuntimeError(f"skills in openclaw.json muss ein Objekt sein: {path}")
    load = skills.get("load")
    if load is None:
        load = {}
        skills["load"] = load
    if not isinstance(load, dict):
        raise RuntimeError(f"skills.load in openclaw.json muss ein Objekt sein: {path}")
    extra_dirs = load.get("extraDirs")
    if extra_dirs is None:
        extra_dirs = []
    if not isinstance(extra_dirs, list) or not all(
        isinstance(item, str) and item.strip() for item in extra_dirs
    ):
        raise RuntimeError(
            f"skills.load.extraDirs in openclaw.json muss eine String-Liste sein: {path}"
        )
    expected = [
        *[item for item in extra_dirs if item != CONTAINER_SKILL_ROOT],
        CONTAINER_SKILL_ROOT,
    ]
    if load.get("extraDirs") == expected:
        return False
    load["extraDirs"] = expected
    temporary = path.with_name(
        f".{path.name}.skills-{os.getpid()}-{time.monotonic_ns()}"
    )
    try:
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.chmod(temporary, path.stat().st_mode & 0o777)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
    return True


def _remove_obsolete_workspace_skill_link(instance: Path, image_root: Path) -> bool:
    link = instance / RELEASE_SKILL_RELATIVE
    if not link.is_symlink():
        return False
    expected = str(image_root / RELEASE_SKILL_RELATIVE)
    if os.readlink(link) != expected:
        raise RuntimeError(
            f"Unerwartetes Ziel fuer veralteten Workspace-Skill-Link: {link}"
        )
    link.unlink()
    with suppress(OSError):
        link.parent.rmdir()
    return True


def restore_backup(archive: Path, destination: Path) -> None:
    archive = archive.expanduser().resolve()
    checksum = archive.with_suffix(archive.suffix + ".sha256")
    if not checksum.is_file():
        raise RuntimeError(f"Backup-Pruefsumme fehlt: {checksum}")
    expected = checksum.read_text(encoding="utf-8").split()[0]
    if _sha256(archive) != expected:
        raise RuntimeError(f"Backup-Pruefsumme stimmt nicht: {archive}")
    destination = destination.expanduser().resolve()
    if destination.exists() and any(destination.iterdir()):
        raise RuntimeError(f"Restore-Ziel ist nicht leer: {destination}")
    destination.mkdir(parents=True, exist_ok=True)
    release_links = {
        "workspace/AGENTS.md",
        "workspace/HEARTBEAT.md",
        "workspace/skills/personal-assistant",
        "v3/instance/AGENTS.md",
        "v3/instance/HEARTBEAT.md",
        "v3/instance/skills/personal-assistant",
    }

    def restore_filter(
        member: tarfile.TarInfo,
        path: str,
    ) -> tarfile.TarInfo | None:
        if member.issym() and Path(member.linkname).is_absolute():
            if member.name in release_links:
                # Release-owned links are recreated by the previous/current
                # image entrypoint and do not belong in a restored state root.
                # Drop the exact known member even when an archive was created
                # from a source checkout with a different absolute image root.
                return None
            raise RuntimeError(f"Unsicherer absoluter Backup-Link: {member.name}")
        return tarfile.data_filter(member, path)

    with tarfile.open(archive, "r:gz") as handle:
        for member in handle.getmembers():
            target = (destination / member.name).resolve()
            if not _is_relative_to(target, destination):
                raise RuntimeError(f"Unsicherer Backup-Eintrag: {member.name}")
        handle.extractall(destination, filter=restore_filter)
    for database in destination.rglob("*.sqlite3"):
        if _sqlite_database(database):
            _sqlite_integrity(database)


def backup_state(state_root: Path, workspace: Path) -> tuple[Path, str]:
    state_root = state_root.expanduser().resolve()
    workspace = workspace.expanduser().resolve()
    marker = state_root / MARKER_NAME
    with _layout_lock(state_root):
        layout = _read_layout(marker)
        if layout >= LAYOUT_VERSION and not (state_root / V3_ROOT_NAME).is_dir():
            raise RuntimeError("Layout-v3-State fehlt trotz aktuellem Marker")
        return _verified_state_backup(
            state_root,
            workspace,
            layout,
            include_v3=layout >= LAYOUT_VERSION,
        )


def _remove(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink(missing_ok=True)
    elif path.is_dir():
        shutil.rmtree(path)


def _install_link(link: Path, target: Path) -> bool:
    expected = str(target)
    if link.is_symlink() and os.readlink(link) == expected:
        return False
    if link.exists() or link.is_symlink():
        _remove(link)
    link.parent.mkdir(parents=True, exist_ok=True)
    temporary = link.with_name(f".{link.name}.m2-{os.getpid()}")
    temporary.unlink(missing_ok=True)
    temporary.symlink_to(target, target_is_directory=target.is_dir())
    temporary.replace(link)
    return True


def _create_configs(image_root: Path, workspace: Path) -> list[str]:
    created: list[str] = []
    for target_name, source_name in CONFIG_TEMPLATES.items():
        target = workspace / target_name
        if target.exists() or target.is_symlink():
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(f".{target.name}.m2-{os.getpid()}")
        shutil.copyfile(image_root / source_name, temporary)
        os.chmod(temporary, 0o600)
        try:
            os.link(temporary, target)
        except FileExistsError:
            pass
        finally:
            temporary.unlink(missing_ok=True)
        if target.exists():
            created.append(target_name)
    return created


def _write_marker(
    marker: Path,
    *,
    previous_layout: int,
    release: str,
    revision: str,
) -> bool:
    migrated_from = previous_layout
    if marker.exists():
        try:
            existing = json.loads(marker.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            existing = {}
        if isinstance(existing, dict) and isinstance(existing.get("migrated_from"), int):
            migrated_from = int(existing["migrated_from"])
    payload = {
        "schema": LAYOUT_SCHEMA,
        "layout": LAYOUT_VERSION,
        "migrated_from": migrated_from,
        "release": release,
        "revision": revision,
    }
    rendered = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if _read_text(marker) == rendered.strip():
        return False
    temporary = marker.with_suffix(marker.suffix + f".tmp-{os.getpid()}")
    temporary.write_text(rendered, encoding="utf-8")
    os.chmod(temporary, 0o600)
    temporary.replace(marker)
    return True


def migrate_layout(image_root: Path, state_root: Path, workspace: Path) -> LayoutReport:
    image_root = image_root.resolve()
    state_root = state_root.resolve()
    workspace = workspace.resolve()
    _validate_roots(image_root, state_root, workspace)
    state_root.mkdir(parents=True, exist_ok=True)
    workspace.mkdir(parents=True, exist_ok=True)
    marker = state_root / MARKER_NAME
    with _layout_lock(state_root):
        previous_layout = _read_layout(marker)
        if previous_layout > LAYOUT_VERSION:
            raise RuntimeError(
                f"State-Layout {previous_layout} ist neuer als dieses Image (maximal {LAYOUT_VERSION})"
            )
        release = _read_text(image_root / "VERSION", "unknown")
        revision = _read_text(image_root / "SOURCE_REVISION", "unknown")
        candidates = _legacy_runtime_paths(image_root, state_root, workspace)
        backup: Path | None = None
        backup_digest: str | None = None
        removed: list[str] = []
        runtime_config_changed = False
        if previous_layout < LAYOUT_VERSION:
            published = state_root / V3_ROOT_NAME
            published_marker = published / "instance/.layout-version.json"
            if published.exists():
                try:
                    published_layout = json.loads(
                        published_marker.read_text(encoding="utf-8")
                    ).get("layout")
                except (OSError, json.JSONDecodeError):
                    published_layout = None
                if published_layout != LAYOUT_VERSION:
                    raise RuntimeError(
                        f"Unvollstaendiges Layout-v3-Ziel erfordert Restore: {published}"
                    )
            else:
                _preflight_v3(state_root, workspace)
                _clear_v3_staging(state_root)
                backup, backup_digest = _verified_state_backup(
                    state_root, workspace, previous_layout
                )
                try:
                    stage = _build_v3_stage(image_root, state_root, workspace)
                    _publish_v3(state_root, stage)
                finally:
                    _clear_v3_staging(state_root)
            for candidate in candidates:
                if candidate.exists() or candidate.is_symlink():
                    removed.append(_archive_name(candidate, state_root))
                    _remove(candidate)
        active_instance = state_root / V3_ROOT_NAME / "instance"
        if not active_instance.is_dir():
            raise RuntimeError(
                f"Aktiver Layout-v3-Instanzbereich fehlt: {active_instance}"
            )
        active_gateway = state_root / V3_ROOT_NAME / "gateway"
        runtime_config_changed = _promote_completed_legacy_workspace_profile(
            active_instance,
            active_gateway,
        )
        runtime_config_changed = (
            _remove_obsolete_workspace_skill_link(active_instance, image_root)
            or runtime_config_changed
        )
        runtime_config_changed = (
            _normalize_container_mail_config(active_instance)
            or runtime_config_changed
        )
        runtime_config_changed = (
            _normalize_container_tool_paths(active_instance)
            or runtime_config_changed
        )
        runtime_config_changed = (
            _normalize_container_gateway_config(active_gateway)
            or runtime_config_changed
        )
        created_configs = _create_configs(image_root, active_instance)
        links: dict[str, str] = {}
        links_changed = False
        for link_name, target_name in RELEASE_DOCUMENT_LINKS.items():
            link = active_instance / link_name
            target = image_root / target_name
            links_changed = _install_link(link, target) or links_changed
            links[link_name] = str(target)
        marker_changed = _write_marker(
            marker,
            previous_layout=previous_layout,
            release=release,
            revision=revision,
        )
    return LayoutReport(
        ok=True,
        layout=LAYOUT_VERSION,
        previous_layout=previous_layout,
        image_root=str(image_root),
        state_root=str(state_root),
        workspace=str(workspace),
        release=release,
        revision=revision,
        backup=str(backup) if backup else None,
        backup_sha256=backup_digest,
        removed_runtime_paths=removed,
        release_links=links,
        created_configs=created_configs,
        changed=bool(
            backup
            or removed
            or created_configs
            or links_changed
            or marker_changed
            or runtime_config_changed
        ),
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="OpenClaw container state layout migration")
    subparsers = parser.add_subparsers(dest="command", required=True)
    migrate = subparsers.add_parser("migrate")
    migrate.add_argument("--image-root", type=Path, required=True)
    migrate.add_argument("--state-root", type=Path, required=True)
    migrate.add_argument("--workspace", type=Path, required=True)
    restore = subparsers.add_parser("restore")
    restore.add_argument("--archive", type=Path, required=True)
    restore.add_argument("--destination", type=Path, required=True)
    backup = subparsers.add_parser("backup")
    backup.add_argument("--state-root", type=Path, required=True)
    backup.add_argument("--workspace", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "restore":
            restore_backup(args.archive, args.destination)
            print(json.dumps({"ok": True, "destination": str(args.destination)}))
            return 0
        if args.command == "backup":
            archive, digest = backup_state(args.state_root, args.workspace)
            print(json.dumps({"ok": True, "archive": str(archive), "sha256": digest}))
            return 0
        report = migrate_layout(args.image_root, args.state_root, args.workspace)
    except (OSError, RuntimeError, TimeoutError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 1
    print(json.dumps(asdict(report), ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
