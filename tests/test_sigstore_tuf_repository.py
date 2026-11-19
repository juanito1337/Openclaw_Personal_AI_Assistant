"""Filesystem staging is bounded transport preparation, never a trust decision."""

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

HELPER = Path(__file__).resolve().parents[1] / "docker/scripts/prepare-sigstore-tuf.py"


def repository(path):
    path.mkdir(mode=0o700)
    for name in ("timestamp.json", "1.root.json", "2.snapshot.json", "3.targets.json"):
        (path / name).write_bytes(b"untrusted bytes")
    (path / "targets").mkdir()
    (path / "targets" / "hash.trusted_root.json").write_bytes(b"untrusted target")
    return path


def run_helper(source, destination):
    return subprocess.run(
        [sys.executable, str(HELPER), str(source), str(destination)],
        text=True, capture_output=True, timeout=10, check=False,
    )


@pytest.fixture
def paths(tmp_path):
    source = repository(tmp_path / "source")
    destination = tmp_path / "destination"
    destination.mkdir(mode=0o700)
    return source, destination


def test_copies_versioned_repository_bytes_without_trusting_content(paths):
    source, destination = paths
    result = run_helper(source, destination)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"files": 5, "directories": 1, "bytes": 76}
    assert result.stderr == ""
    for original in source.rglob("*"):
        copied = destination / original.relative_to(source)
        if original.is_file():
            assert copied.read_bytes() == original.read_bytes()
            assert copied.stat().st_mode & 0o777 == 0o600
        else:
            assert copied.stat().st_mode & 0o777 == 0o700
    assert "untrusted" not in result.stdout


@pytest.mark.parametrize("name", ["timestamp.json", "1.root.json", "2.snapshot.json", "3.targets.json"])
def test_missing_required_metadata_is_rejected(paths, name):
    source, destination = paths
    (source / name).unlink()
    assert run_helper(source, destination).returncode == 1


def test_unversioned_metadata_and_nested_targets_are_supported(paths):
    source, destination = paths
    for name in ("1.root.json", "2.snapshot.json", "3.targets.json"):
        (source / name).rename(source / name.split(".", 1)[1])
    nested = source / "targets/registry.npmjs.org"
    nested.mkdir()
    (nested / "keys.json").write_bytes(b"keys")
    assert run_helper(source, destination).returncode == 0
    assert (destination / "targets/registry.npmjs.org/keys.json").read_bytes() == b"keys"


@pytest.mark.parametrize("kind", ["source-file", "destination-file", "nonempty", "public", "missing"])
def test_requires_directories_and_empty_private_destination(paths, tmp_path, kind):
    source, destination = paths
    existing = tmp_path / "existing"
    existing.write_bytes(b"preserve me")
    if kind == "source-file":
        source = existing
    elif kind == "destination-file":
        destination = existing
    elif kind == "nonempty":
        (destination / "timestamp.json").write_bytes(b"preserve me")
    elif kind == "public":
        destination.chmod(0o755)
    else:
        destination.rmdir()
    assert run_helper(source, destination).returncode == 1
    assert existing.read_bytes() == b"preserve me"
    if kind == "nonempty":
        assert (destination / "timestamp.json").read_bytes() == b"preserve me"


@pytest.mark.parametrize("kind", ["relative-source", "relative-destination", "parent", "same", "nested"])
def test_unsafe_source_or_destination_paths_are_rejected(paths, kind):
    source, destination = paths
    if kind == "relative-source":
        source = "relative"
    elif kind == "relative-destination":
        destination = "relative"
    elif kind == "parent":
        source = str(source) + "/../source"
    elif kind == "same":
        destination = source
    else:
        destination = source / "nested"
        destination.mkdir(mode=0o700)
    assert run_helper(source, destination).returncode == 1


@pytest.mark.parametrize(
    "kind", ["file", "directory", "source", "destination", "ancestor", "fifo", "unsafe-name"]
)
def test_symlinks_special_files_and_unsafe_names_are_rejected(paths, tmp_path, kind):
    source, destination = paths
    outside = tmp_path / "outside"
    outside.mkdir()
    secret = outside / "private"
    secret.write_bytes(b"outside-sentinel")
    if kind == "file":
        (source / "linked").symlink_to(secret)
    elif kind == "directory":
        (source / "linked").symlink_to(outside, target_is_directory=True)
    elif kind == "source":
        link = tmp_path / "linked"
        link.symlink_to(source, target_is_directory=True)
        source = link
    elif kind == "destination":
        link = tmp_path / "linked"
        link.symlink_to(destination, target_is_directory=True)
        destination = link
    elif kind == "ancestor":
        link = tmp_path / "linked"
        link.symlink_to(tmp_path, target_is_directory=True)
        source = link / "source"
    elif kind == "fifo":
        os.mkfifo(source / "pipe")
    else:
        (source / "bad\\name").write_bytes(b"invalid")
    result = run_helper(source, destination)
    assert result.returncode == 1
    assert "outside-sentinel" not in result.stdout + result.stderr
    assert "Traceback" not in result.stderr
    assert secret.read_bytes() == b"outside-sentinel"


@pytest.mark.parametrize("kind", ["file", "total", "entries"])
def test_repository_copy_is_bounded(paths, kind):
    source, destination = paths
    if kind == "entries":
        for index in range(2049):
            (source / f"entry-{index}").touch()
    else:
        for index in range(1 if kind == "file" else 9):
            with (source / f"large-{index}").open("wb") as handle:
                handle.truncate(8 * 1024 * 1024 + (1 if kind == "file" else 0))
    result = run_helper(source, destination)
    assert result.returncode == 1
    assert not result.stdout
    assert sum(item.stat().st_size for item in destination.rglob("*") if item.is_file()) <= 64 * 1024 * 1024


@pytest.mark.parametrize("kind", ["file", "directory"])
def test_replacement_with_symlink_between_listing_and_open_never_reads_outside(
    paths, tmp_path, monkeypatch, kind
):
    source, destination = paths
    spec = importlib.util.spec_from_file_location("prepare_sigstore_tuf", HELPER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    outside = tmp_path / "outside"
    outside.mkdir()
    secret = outside / "private"
    secret.write_bytes(b"outside-sentinel")
    selected = "timestamp.json" if kind == "file" else "targets"
    original_open = os.open
    replaced = False

    def racing_open(path, flags, mode=0o777, *, dir_fd=None):
        nonlocal replaced
        if path == selected and dir_fd is not None and not replaced:
            replaced = True
            (source / selected).rename(tmp_path / "parked")
            (source / selected).symlink_to(secret if kind == "file" else outside)
        return original_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(module.os, "open", racing_open)
    with pytest.raises((ValueError, OSError)):
        module.stage_repository(str(source), str(destination))
    assert replaced
    assert all(item.read_bytes() != b"outside-sentinel" for item in destination.rglob("*") if item.is_file())
