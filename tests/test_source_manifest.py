from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from personal_assistant.release import verify_release
from personal_assistant.source_manifest import (
    render_source_manifest,
    verify_source_manifest,
    write_source_manifest,
)


def _write_manifest(root: Path, paths: list[str]) -> None:
    (root / "SOURCE_MANIFEST.sha256").write_text(
        render_source_manifest(root, paths), encoding="utf-8"
    )


def test_source_manifest_accepts_exact_file_set(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("print('a')\n", encoding="utf-8")
    (tmp_path / "b.sh").write_text("#!/bin/sh\n", encoding="utf-8")
    _write_manifest(tmp_path, ["a.py", "b.sh"])

    report = verify_source_manifest(tmp_path, ["a.py", "b.sh"])

    assert report.ok
    assert report.entry_count == 2


def test_source_manifest_detects_changed_content(tmp_path: Path) -> None:
    source = tmp_path / "a.py"
    source.write_text("old\n", encoding="utf-8")
    _write_manifest(tmp_path, ["a.py"])
    source.write_text("new\n", encoding="utf-8")

    report = verify_source_manifest(tmp_path, ["a.py"])

    assert report.changed_files == ["a.py"]
    assert not report.ok


def test_source_manifest_detects_missing_entry(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("a\n", encoding="utf-8")
    (tmp_path / "b.py").write_text("b\n", encoding="utf-8")
    _write_manifest(tmp_path, ["a.py"])

    report = verify_source_manifest(tmp_path, ["a.py", "b.py"])

    assert report.missing_entries == ["b.py"]
    assert not report.ok


def test_source_manifest_detects_additional_entry(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("a\n", encoding="utf-8")
    (tmp_path / "b.py").write_text("b\n", encoding="utf-8")
    _write_manifest(tmp_path, ["a.py", "b.py"])

    report = verify_source_manifest(tmp_path, ["a.py"])

    assert report.extra_entries == ["b.py"]
    assert not report.ok


def test_source_manifest_detects_missing_file(tmp_path: Path) -> None:
    source = tmp_path / "a.py"
    source.write_text("a\n", encoding="utf-8")
    _write_manifest(tmp_path, ["a.py"])
    source.unlink()

    report = verify_source_manifest(tmp_path, ["a.py"])

    assert report.missing_files == ["a.py"]
    assert not report.ok


def test_exported_manifest_detects_unlisted_additional_file(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("a\n", encoding="utf-8")
    _write_manifest(tmp_path, ["a.py"])
    (tmp_path / "unlisted.py").write_text("unexpected\n", encoding="utf-8")

    report = verify_source_manifest(tmp_path)

    assert report.missing_entries == ["unlisted.py"]
    assert not report.ok


def test_generator_uses_real_git_set_deterministically_and_excludes_itself(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / "tracked.py").write_text("tracked\n", encoding="utf-8")
    subprocess.run(["git", "add", "tracked.py"], cwd=tmp_path, check=True)

    first = write_source_manifest(tmp_path).read_bytes()
    second = write_source_manifest(tmp_path).read_bytes()
    report = verify_source_manifest(tmp_path)

    assert first == second
    assert b"SOURCE_MANIFEST.sha256" not in first
    assert report.ok

    (tmp_path / "new.py").write_text("new\n", encoding="utf-8")
    report = verify_source_manifest(tmp_path)
    assert report.missing_entries == ["new.py"]
    assert not report.ok


def test_generator_excludes_tracked_files_deleted_in_worktree(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    kept = tmp_path / "kept.py"
    removed = tmp_path / "removed.py"
    kept.write_text("kept\n", encoding="utf-8")
    removed.write_text("removed\n", encoding="utf-8")
    subprocess.run(["git", "add", "kept.py", "removed.py"], cwd=tmp_path, check=True)
    removed.unlink()

    write_source_manifest(tmp_path)
    report = verify_source_manifest(tmp_path)

    self_manifest = (tmp_path / "SOURCE_MANIFEST.sha256").read_text(encoding="utf-8")
    assert "kept.py" in self_manifest
    assert "removed.py" not in self_manifest
    assert report.ok


def test_release_verification_reports_source_manifest_failure(tmp_path: Path) -> None:
    release = {
        "schema_version": 1,
        "version": "test-r1",
        "release": "r1",
        "history": [{"version": "test-r1"}],
    }
    (tmp_path / "RELEASE.json").write_text(json.dumps(release), encoding="utf-8")
    for relative in ("AGENTS.md", "README.md", "CHANGELOG.md", "skills/personal-assistant/SKILL.md"):
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("test-r1\n", encoding="utf-8")
    paths = [
        "AGENTS.md",
        "CHANGELOG.md",
        "README.md",
        "RELEASE.json",
        "skills/personal-assistant/SKILL.md",
    ]
    _write_manifest(tmp_path, paths)
    (tmp_path / "README.md").write_text("test-r1 changed\n", encoding="utf-8")

    result = verify_release(tmp_path)
    environment = os.environ.copy()
    environment["OPENCLAW_WORKSPACE"] = str(tmp_path)
    cli = subprocess.run(
        [sys.executable, "-m", "personal_assistant", "version", "--verify"],
        check=False,
        cwd=Path(__file__).resolve().parents[1],
        env=environment,
        text=True,
        capture_output=True,
    )

    assert not result["ok"]
    assert "README.md" in result["source_manifest"]["changed_files"]
    assert cli.returncode == 1
    assert "SOURCE_MANIFEST.sha256" in cli.stdout
