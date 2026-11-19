"""The deadline helper consumes only synthetic, already-validated Cosign status."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "docker/scripts/sigstore-tuf-deadline.py"
NOW = 1_795_000_000
DEADLINE = 1_795_526_340  # 2026-11-24 13:19:00 UTC; printed minutes are conservative.
ERROR = "sigstore-tuf-deadline-invalid-or-expired\n"


def status() -> dict:
    return {
        "local": "/work/cache",
        "remote": "file:///repository",
        "metadata": {
            "root.json": {"version": 16, "len": 2048, "expiration": "22 Apr 27 19:27 UTC", "error": ""},
            "timestamp.json": {"version": 837, "len": 512, "expiration": "24 Nov 26 13:19 UTC", "error": ""},
            "snapshot.json": {"version": 166, "len": 512, "expiration": "04 Oct 36 08:51 UTC", "error": ""},
            "targets.json": {"version": 14, "len": 1024, "expiration": "09 May 36 09:00 UTC", "error": ""},
        },
        "targets": ["trusted_root.json"],
    }


def invoke(*arguments: object, now: float = NOW) -> subprocess.CompletedProcess[str]:
    # Time is the only test double: execute the actual CLI and real file parsing.
    runner = (
        "import runpy,sys,time; "
        f"time.time=lambda: {now!r}; "
        "sys.argv=sys.argv[1:]; runpy.run_path(sys.argv[0],run_name='__main__')"
    )
    return subprocess.run(
        [sys.executable, "-c", runner, str(SCRIPT), *(str(arg) for arg in arguments)],
        capture_output=True, text=True, check=False, timeout=5,
    )


def write_status(tmp_path: Path, payload: dict | None = None) -> Path:
    path = tmp_path / "initialize.stdout"
    path.write_text("Root status: \n " + json.dumps(status() if payload is None else payload) + "\n")
    return path


def rejected(result: subprocess.CompletedProcess[str]) -> None:
    assert result.returncode == 1
    assert result.stdout == ""
    assert result.stderr == ERROR


def test_returns_earliest_expiry_from_complete_pinned_status(tmp_path):
    result = invoke(write_status(tmp_path))
    assert result.returncode == 0, result.stderr
    assert result.stdout == f"{DEADLINE}\n"
    assert result.stderr == ""


def test_delegated_metadata_can_shorten_the_deadline(tmp_path):
    payload = status()
    payload["metadata"]["delegated.json"] = {
        "version": 2, "len": 100, "expiration": "23 Nov 26 12:00 UTC",
    }
    result = invoke(write_status(tmp_path, payload))
    assert result.returncode == 0, result.stderr
    assert result.stdout == "1795435200\n"


@pytest.mark.parametrize("role", ["root.json", "timestamp.json", "snapshot.json", "targets.json"])
def test_missing_required_role_never_produces_a_deadline(tmp_path, role):
    payload = status()
    del payload["metadata"][role]
    rejected(invoke(write_status(tmp_path, payload)))


@pytest.mark.parametrize("mutation", [
    lambda p: p.pop("metadata"),
    lambda p: p.update(metadata=[]),
    lambda p: p["metadata"].update({"delegated.json": None}),
    lambda p: p["metadata"].update({"delegated.json": {"version": 1, "len": 100}}),
    lambda p: p["metadata"]["root.json"].update(error="private-response-content"),
    lambda p: p["metadata"]["root.json"].update(error=False),
    lambda p: p["metadata"]["root.json"].update(version=True),
    lambda p: p["metadata"]["root.json"].update(version=0),
    lambda p: p["metadata"]["root.json"].update(len=-1),
])
def test_partial_or_malformed_metadata_fails_without_echoing_content(tmp_path, mutation):
    payload = status()
    mutation(payload)
    rejected(invoke(write_status(tmp_path, payload)))


@pytest.mark.parametrize("expiration", [
    "", None, 1795526340, "private-response-content", "24 Nov 26 13:19 PST",
    "24 Nov 26 13:19", "24 Nov 26 13:19 +0000", "24 Nov 26 13:19:56 UTC",
    "2026-11-24T13:19:56Z", "31 Nov 26 13:19 UTC", "24 Foo 26 13:19 UTC",
    "24 Nov 26 25:19 UTC", "24 nov 26 13:19 UTC", "24 Nov 26 13:19 UTC extra",
])
def test_rejects_ambiguous_or_invalid_expiration_in_any_role(tmp_path, expiration):
    payload = status()
    payload["metadata"]["delegated.json"] = {"version": 1, "len": 100, "expiration": expiration}
    rejected(invoke(write_status(tmp_path, payload)))


@pytest.mark.parametrize("now", [DEADLINE, DEADLINE + 1])
def test_expired_parsed_status_is_rejected(tmp_path, now):
    rejected(invoke(write_status(tmp_path), now=now))


@pytest.mark.parametrize("content", [
    "", "{}", "Root status: \n{}", "Root status: \n{bad JSON}",
    "private-response-content\nRoot status: \n{}", "Root status: \n[]",
])
def test_missing_or_invalid_output_envelope_is_rejected(tmp_path, content):
    path = tmp_path / "initialize.stdout"
    path.write_text(content)
    rejected(invoke(path))


def test_duplicate_fields_and_trailing_output_are_rejected(tmp_path):
    path = write_status(tmp_path)
    original = path.read_text()
    path.write_text(original.replace('"version": 16', '"version": 16, "version": 17'))
    rejected(invoke(path))
    path.write_text(original + "private-response-content\n")
    rejected(invoke(path))


def test_missing_directory_symlink_and_fifo_inputs_are_rejected(tmp_path):
    rejected(invoke(tmp_path / "missing"))
    rejected(invoke(tmp_path))
    path = write_status(tmp_path)
    link = tmp_path / "linked.stdout"
    link.symlink_to(path)
    rejected(invoke(link))
    fifo = tmp_path / "fifo.stdout"
    os.mkfifo(fifo)
    rejected(invoke(fifo))


def test_oversized_status_is_rejected_without_reading_unbounded_output(tmp_path):
    path = tmp_path / "initialize.stdout"
    path.write_text("Root status: \n" + " " * (64 * 1024))
    rejected(invoke(path))


def test_check_accepts_only_a_future_integer_and_expires_at_boundary():
    result = invoke("--check", DEADLINE, now=DEADLINE - 0.25)
    assert result.returncode == 0, result.stderr
    assert result.stdout == result.stderr == ""
    rejected(invoke("--check", DEADLINE, now=DEADLINE))
    rejected(invoke("--check", DEADLINE, now=DEADLINE + 1))


@pytest.mark.parametrize("arguments", [
    (), ("--check",), ("--check", "nan"), ("--check", "inf"), ("--check", "0"),
    ("--check", "-1"), ("--check", "1795526340.0"), ("--check", "1e99"),
    ("--check", "9" * 1000), ("--check", "2082758400", "extra"),
])
def test_invalid_cli_and_epoch_values_fail_without_traceback(arguments):
    rejected(invoke(*arguments))
