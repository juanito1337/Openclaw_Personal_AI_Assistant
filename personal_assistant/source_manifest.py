from __future__ import annotations

import hashlib
import re
import subprocess
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

MANIFEST_NAME = "SOURCE_MANIFEST.sha256"
_LINE = re.compile(r"^(?P<digest>[0-9a-f]{64})  \./(?P<path>.+)$")


@dataclass(slots=True)
class SourceManifestReport:
    manifest: str
    expected_count: int = 0
    entry_count: int = 0
    missing_entries: list[str] = field(default_factory=list)
    extra_entries: list[str] = field(default_factory=list)
    missing_files: list[str] = field(default_factory=list)
    changed_files: list[str] = field(default_factory=list)
    invalid_entries: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not any(
            (
                self.missing_entries,
                self.extra_entries,
                self.missing_files,
                self.changed_files,
                self.invalid_entries,
            )
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "ok": self.ok,
            "manifest": self.manifest,
            "expected_count": self.expected_count,
            "entry_count": self.entry_count,
            "missing_entries": self.missing_entries,
            "extra_entries": self.extra_entries,
            "missing_files": self.missing_files,
            "changed_files": self.changed_files,
            "invalid_entries": self.invalid_entries,
        }


def _normalize_path(value: str) -> str:
    if "\n" in value or "\r" in value:
        raise ValueError(f"ungueltiger Zeilenumbruch im Quellpfad: {value!r}")
    path = PurePosixPath(value.removeprefix("./"))
    if path.is_absolute() or not path.parts or ".." in path.parts:
        raise ValueError(f"ungueltiger Quellpfad: {value!r}")
    normalized = path.as_posix()
    if normalized == MANIFEST_NAME:
        raise ValueError(f"{MANIFEST_NAME} darf sich nicht selbst enthalten")
    return normalized


def git_source_paths(root: Path | str) -> list[str]:
    """Return the exact tracked and staged source set, including new untracked files."""
    base = Path(root).resolve()
    result = subprocess.run(
        ["git", "-C", str(base), "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        check=False,
        capture_output=True,
    )
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"Git-Quellmenge konnte nicht gelesen werden: {detail}")
    paths = set()
    for item in result.stdout.split(b"\0"):
        if not item:
            continue
        decoded = item.decode("utf-8", errors="strict")
        if decoded == MANIFEST_NAME:
            continue
        normalized = _normalize_path(decoded)
        if (base / normalized).is_file():
            paths.add(normalized)
    return sorted(paths)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def render_source_manifest(root: Path | str, paths: Iterable[str] | None = None) -> str:
    base = Path(root).resolve()
    selected = git_source_paths(base) if paths is None else sorted({_normalize_path(path) for path in paths})
    lines: list[str] = []
    for relative in selected:
        source = base / relative
        if not source.is_file():
            raise FileNotFoundError(f"Quelldatei fehlt: {relative}")
        lines.append(f"{sha256_file(source)}  ./{relative}")
    return "\n".join(lines) + "\n"


def write_source_manifest(root: Path | str, paths: Iterable[str] | None = None) -> Path:
    base = Path(root).resolve()
    target = base / MANIFEST_NAME
    rendered = render_source_manifest(base, paths)
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(rendered, encoding="utf-8", newline="\n")
    temporary.replace(target)
    return target


def _read_entries(path: Path) -> tuple[dict[str, str], list[str]]:
    entries: dict[str, str] = {}
    invalid: list[str] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        return {}, [f"Manifest unlesbar: {exc}"]
    for number, line in enumerate(lines, start=1):
        match = _LINE.fullmatch(line)
        if match is None:
            invalid.append(f"Zeile {number}: ungueltiges Format")
            continue
        try:
            relative = _normalize_path(match.group("path"))
        except ValueError as exc:
            invalid.append(f"Zeile {number}: {exc}")
            continue
        if relative in entries:
            invalid.append(f"Zeile {number}: doppelter Eintrag {relative}")
            continue
        entries[relative] = match.group("digest")
    return entries, invalid


def verify_source_manifest(
    root: Path | str,
    expected_paths: Iterable[str] | None = None,
) -> SourceManifestReport:
    base = Path(root).resolve()
    manifest = base / MANIFEST_NAME
    report = SourceManifestReport(manifest=str(manifest))
    has_git_index = (base / ".git").exists()
    if expected_paths is None:
        if has_git_index:
            expected = set(git_source_paths(base))
        else:
            expected = {
                _normalize_path(path.relative_to(base).as_posix())
                for path in base.rglob("*")
                if path.is_file() and path != base / MANIFEST_NAME
            }
    else:
        expected = {_normalize_path(path) for path in expected_paths}
    entries, invalid = _read_entries(manifest)
    report.expected_count = len(expected)
    report.entry_count = len(entries)
    report.invalid_entries.extend(invalid)
    report.missing_entries.extend(sorted(expected - entries.keys()))
    report.extra_entries.extend(sorted(entries.keys() - expected))
    for relative, expected_digest in sorted(entries.items()):
        source = base / relative
        if not source.is_file():
            report.missing_files.append(relative)
        elif sha256_file(source) != expected_digest:
            report.changed_files.append(relative)
    return report
