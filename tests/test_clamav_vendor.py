"""Vendor equality, bounded evidence and actual generation identity gates."""
from __future__ import annotations

import json
import os
import stat
import subprocess
from pathlib import Path

import pytest

from personal_assistant import clamav_vendor as vendor

NOW = 1_795_780_000.0
VERSIONS = {"main": 63, "daily": 28194, "bytecode": 339}


def databases(path: Path) -> None:
    for group, version in VERSIONS.items():
        # Old build date deliberately does not determine vendor currentness.
        header = f"ClamAV-VDB:25 Nov 2026 06-26 +0000:{version}:80:90:md5:signature:builder:1795588019"
        (path / f"{group}.cvd").write_bytes(header.encode().ljust(512, b" ") + group.encode())


def current(path: Path, *, checked_at: float = NOW) -> dict:
    databases(path)
    result = {
        "source": "current.cvd.clamav.net", "checked_at": checked_at,
        "record_timestamp": NOW - 3600, "versions": dict(VERSIONS), "state": "current",
        "database": vendor.database_snapshot(path),
    }
    vendor.publish_status(path, result)
    return result


def test_old_build_is_current_only_with_all_three_vendor_versions(tmp_path):
    current(tmp_path)
    report = vendor.vendor_readiness(tmp_path, now=NOW)
    assert report["ok"] is True
    assert report["versions"] == VERSIONS
    assert len(report["generation"]) == 64


@pytest.mark.parametrize("group", ["main", "daily", "bytecode"])
def test_any_new_vendor_group_immediately_invalidates_current_receipt(tmp_path, group):
    proof = current(tmp_path)
    proof["versions"][group] += 1
    vendor.publish_status(tmp_path, proof)
    assert vendor.vendor_readiness(tmp_path, now=NOW)["ok"] is False


@pytest.mark.parametrize("checked,ready", [(NOW - 660, True), (NOW - 660.01, False), (NOW + 1, False)])
def test_observation_expiry_and_future_cannot_grant_currentness(tmp_path, checked, ready):
    current(tmp_path, checked_at=checked)
    assert vendor.vendor_readiness(tmp_path, now=NOW)["ok"] is ready


@pytest.mark.parametrize("state", ["failed", "update-required", "updating"])
def test_failed_or_pending_update_never_reuses_previous_equality(tmp_path, state):
    proof = current(tmp_path)
    proof["state"] = state
    vendor.publish_status(tmp_path, proof)
    assert vendor.vendor_readiness(tmp_path, now=NOW)["ok"] is False


def test_changed_database_content_or_metadata_invalidates_proof(tmp_path):
    proof = current(tmp_path)
    file = tmp_path / "main.cvd"
    before = file.read_bytes()
    file.write_bytes(before[:-1] + b"x")
    assert vendor.disk_matches(tmp_path, proof["database"]) is False
    assert vendor.vendor_readiness(tmp_path, now=NOW)["ok"] is False


def test_content_generation_survives_identical_copy_but_old_metadata_does_not(tmp_path):
    proof = current(tmp_path)
    file = tmp_path / "bytecode.cvd"
    replacement = tmp_path / "replacement"
    replacement.write_bytes(file.read_bytes())
    replacement.replace(file)
    assert vendor.database_snapshot(tmp_path)["generation"] == proof["database"]["generation"]
    assert vendor.disk_matches(tmp_path, proof["database"]) is False


@pytest.mark.parametrize("bad", ["missing", "ambiguous", "symlink", "invalid-header", "fifo"])
def test_database_snapshot_rejects_unproven_files(tmp_path, bad):
    databases(tmp_path)
    file = tmp_path / "daily.cvd"
    if bad == "ambiguous":
        (tmp_path / "daily.cld").write_bytes(file.read_bytes())
    elif bad == "invalid-header":
        file.write_bytes(b"not a clamav header".ljust(512, b" "))
    else:
        file.unlink()
        if bad == "symlink":
            file.symlink_to(tmp_path / "main.cvd")
        elif bad == "fifo":
            os.mkfifo(file)
    with pytest.raises(vendor.VendorError):
        vendor.database_snapshot(tmp_path)


def test_published_proof_is_atomic_readable_by_scanner_group(tmp_path):
    current(tmp_path)
    file = tmp_path / "openclaw-vendor-status.json"
    assert stat.S_IMODE(file.stat().st_mode) == 0o640
    assert json.loads(file.read_text())["schema"] == 1


@pytest.mark.parametrize("bad", ["missing", "oversize", "json", "symlink", "fifo"])
def test_unreadable_receipts_are_explicitly_unready(tmp_path, bad):
    file = tmp_path / "openclaw-vendor-status.json"
    if bad == "oversize":
        file.write_text(" " * 16385)
    elif bad == "json":
        file.write_text("{")
    elif bad == "symlink":
        target = tmp_path / "other"
        target.write_text('{}')
        file.symlink_to(target)
    elif bad == "fifo":
        os.mkfifo(file)
    report = vendor.vendor_readiness(tmp_path, now=NOW)
    assert report["ok"] is False
    assert report["reasons"]


def output(stamp=NOW - 3600, daily="28194"):
    return ('Server: 127.0.0.1\ncurrent.cvd.clamav.net\ttext = '
            f'"1.4.7:63:{daily}:{int(stamp)}:1:90:49192:339"\n')


def test_query_uses_bounded_official_dns_only(monkeypatch):
    def run(args, **kwargs):
        assert args == ["/bin/busybox", "nslookup", "-type=TXT", "current.cvd.clamav.net"]
        assert kwargs["timeout"] == 10
        return subprocess.CompletedProcess(args, 0, output(), "")
    monkeypatch.setattr(vendor.subprocess, "run", run)
    report = vendor.query_vendor(now=NOW)
    assert report["versions"] == VERSIONS
    assert report["checked_at"] == NOW


@pytest.mark.parametrize("response", [
    output(NOW + 1), output(NOW - 43201), output(daily="0"), output(daily="-1"),
    output(daily="nan"), output(daily="28194:extra"), output() + output(NOW - 1800),
    "x" * 16385, 'other.invalid text = "1.4.7:63:28194:1795776400:1:90:49192:339"',
])
def test_invalid_or_ambiguous_vendor_reply_cannot_refresh_proof(monkeypatch, response):
    monkeypatch.setattr(vendor.subprocess, "run",
                        lambda args, **kw: subprocess.CompletedProcess(args, 0, response, ""))
    with pytest.raises(vendor.VendorError):
        vendor.query_vendor(now=NOW)


def test_dns_timeout_returns_safe_category(monkeypatch):
    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired("private text must not appear", 10)
    monkeypatch.setattr(vendor.subprocess, "run", timeout)
    with pytest.raises(vendor.VendorError, match="vendor-probe-timeout"):
        vendor.query_vendor(now=NOW)


def test_readonly_bootstrap_checks_vendor_without_creating_receipt(tmp_path, monkeypatch, capsys):
    databases(tmp_path)
    monkeypatch.setenv("CLAMAV_DATABASE_DIR", str(tmp_path))
    monkeypatch.setattr(vendor.time, "time", lambda: NOW)
    # Native authentication is exercised separately against actual signed databases.
    monkeypatch.setattr(vendor, "verify_snapshot", lambda *_: None)
    monkeypatch.setattr(vendor, "query_vendor", lambda: {
        "source": "current.cvd.clamav.net", "checked_at": NOW,
        "record_timestamp": NOW - 3600, "versions": dict(VERSIONS),
    })
    assert vendor.main(["--check"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["ok"] is True
    assert report["read_only"] is True
    assert not (tmp_path / "openclaw-vendor-status.json").exists()


def test_partial_bootstrap_inventory_preserves_present_versions(tmp_path):
    databases(tmp_path)
    (tmp_path / "daily.cvd").unlink()
    partial = vendor.database_snapshot(tmp_path, allow_missing=True)
    assert partial["versions"] == {"main": 63, "bytecode": 339}
    assert vendor.disk_matches(tmp_path, partial) is False


def test_malformed_large_numeric_evidence_fails_without_diagnostic_crash(tmp_path):
    proof = current(tmp_path)
    proof["checked_at"] = 10**1000
    vendor.publish_status(tmp_path, proof)
    assert vendor.vendor_readiness(tmp_path, now=NOW)["ok"] is False


def test_readonly_bootstrap_rechecks_disk_after_snapshot(tmp_path, monkeypatch, capsys):
    databases(tmp_path)
    snapshot = vendor.database_snapshot(tmp_path)
    monkeypatch.setenv("CLAMAV_DATABASE_DIR", str(tmp_path))
    monkeypatch.setattr(vendor, "query_vendor", lambda: {
        "source": "current.cvd.clamav.net", "checked_at": NOW,
        "record_timestamp": NOW - 3600, "versions": dict(VERSIONS),
    })
    def changing_snapshot(path):
        (path / "daily.cvd").unlink()
        return snapshot
    monkeypatch.setattr(vendor, "database_snapshot", changing_snapshot)
    assert vendor.main(["--check"]) == 1
    assert json.loads(capsys.readouterr().out)["ok"] is False


def test_readiness_rechecks_vendor_revocation_after_database_check(tmp_path, monkeypatch):
    proof = current(tmp_path)
    original = vendor.disk_matches
    def revoke(path, snapshot):
        vendor.publish_status(path, {**proof, "state": "update-required"})
        return original(path, snapshot)
    monkeypatch.setattr(vendor, "disk_matches", revoke)
    assert vendor.vendor_readiness(tmp_path, now=NOW)["ok"] is False


def test_deeply_nested_bounded_json_is_unready_not_a_crash(tmp_path):
    (tmp_path / "openclaw-vendor-status.json").write_text("[" * 2000 + "]" * 2000)
    assert vendor.vendor_readiness(tmp_path, now=NOW)["ok"] is False


def test_same_size_body_mutation_with_restored_mtime_is_detected(tmp_path):
    proof = current(tmp_path)
    path = tmp_path / "main.cvd"
    old = path.stat()
    path.write_bytes(path.read_bytes()[:-1] + b"z")
    os.utime(path, ns=(old.st_atime_ns, old.st_mtime_ns))
    assert vendor.disk_matches(tmp_path, proof["database"]) is False


def test_bootstrap_does_not_authorize_expired_probe_after_slow_hash(tmp_path, monkeypatch, capsys):
    databases(tmp_path)
    monkeypatch.setenv("CLAMAV_DATABASE_DIR", str(tmp_path))
    monkeypatch.setattr(vendor.time, "time", lambda: NOW + 661)
    monkeypatch.setattr(vendor, "query_vendor", lambda: {
        "source": "current.cvd.clamav.net", "checked_at": NOW,
        "record_timestamp": NOW - 3600, "versions": dict(VERSIONS),
    })
    assert vendor.main(["--check"]) == 1
    assert json.loads(capsys.readouterr().out)["ok"] is False


@pytest.mark.parametrize("previous", [True, False])
def test_failed_updater_reason_remains_visible_without_false_currentness(tmp_path, previous):
    proof = current(tmp_path) if previous else {}
    vendor.publish_status(tmp_path, {**proof, "state": "failed", "error": "vendor-probe-timeout"})
    report = vendor.vendor_readiness(tmp_path, now=NOW)
    assert report["ok"] is False
    assert report["error"] == "vendor-probe-timeout"


def test_updater_error_does_not_echo_unrecognized_private_text(tmp_path):
    vendor.publish_status(tmp_path, {"state": "failed", "error": "private-server-location"})
    report = vendor.vendor_readiness(tmp_path, now=NOW)
    assert "private-server-location" not in json.dumps(report)


def test_bootstrap_rejects_equal_header_versions_when_signature_verification_fails(
    tmp_path, monkeypatch, capsys,
):
    databases(tmp_path)
    monkeypatch.setenv("CLAMAV_DATABASE_DIR", str(tmp_path))
    monkeypatch.setattr(vendor.time, "time", lambda: NOW)
    monkeypatch.setattr(vendor, "query_vendor", lambda: {
        "source": "current.cvd.clamav.net", "checked_at": NOW,
        "record_timestamp": NOW - 3600, "versions": dict(VERSIONS),
    })
    monkeypatch.setattr(vendor.subprocess, "run",
                        lambda args, **kw: subprocess.CompletedProcess(args, 1, "", ""))
    assert vendor.main(["--check"]) == 1
    assert json.loads(capsys.readouterr().out)["error"] == "database-signature-invalid"
