from unittest.mock import ANY, Mock

import pytest

from personal_assistant import clamav_update as updater

VERSIONS = {"main": 1, "daily": 2, "bytecode": 3}


@pytest.fixture(autouse=True)
def native_verification(monkeypatch):
    # These tests use synthetic headers; real native verification has its own suite.
    monkeypatch.setattr(updater, "verify_snapshot", lambda *_: None)


def test_equal_header_bootstrap_cannot_publish_without_native_authentication(tmp_path, monkeypatch):
    from test_clamav_vendor import NOW, databases
    from test_clamav_vendor import VERSIONS as installed

    databases(tmp_path)
    observation = {"source": "current.cvd.clamav.net", "checked_at": NOW,
                   "record_timestamp": NOW, "versions": installed}
    monkeypatch.setattr(updater, "query_vendor", lambda: observation)
    monkeypatch.setattr(updater, "download", lambda *_: None)
    def reject(*_):
        raise updater.VendorError("database-signature-invalid")
    monkeypatch.setattr(updater, "verify_snapshot", reject)
    assert updater.run_once(tmp_path) is False
    status = updater.read_status(tmp_path)
    assert status["state"] == "failed"
    assert status["error"] == "database-signature-invalid"


@pytest.mark.parametrize("group", ["main", "daily", "bytecode"])
def test_new_group_blocks_before_download_and_rechecks(tmp_path, monkeypatch, group):
    offered = {**VERSIONS, group: VERSIONS[group] + 1}
    probe = Mock(return_value={"versions": offered})
    states = []
    monkeypatch.setattr(updater, "query_vendor", probe)
    monkeypatch.setattr(
        updater,
        "database_snapshot",
        Mock(
            side_effect=[
                {"versions": VERSIONS},
                {"versions": offered},
            ]
        ),
    )
    monkeypatch.setattr(updater, "publish_status", lambda _, status: states.append(status["state"]))

    def download(path, lock_fd):
        assert path == tmp_path and states == ["update-required", "updating"]

    monkeypatch.setattr(updater, "download", download)
    assert updater.run_once(tmp_path)
    assert states[-1] == "current" and probe.call_count == 2


def test_matching_old_latest_skips_download(tmp_path, monkeypatch):
    from test_clamav_vendor import NOW, current

    prior = current(tmp_path)
    monkeypatch.setattr(updater, "query_vendor", lambda: {**prior, "checked_at": NOW + 300})
    monkeypatch.setattr(updater, "download", lambda *_: pytest.fail("unexpected download"))
    status = Mock()
    monkeypatch.setattr(updater, "publish_status", status)
    assert updater.run_once(tmp_path)
    assert status.call_args.args[1]["state"] == "current"


@pytest.mark.parametrize("failure", ["dns", "download", "cdn", "regression"])
def test_failed_pass_never_renews_success(tmp_path, monkeypatch, failure):
    versions = {**VERSIONS, "main": 2 if failure != "regression" else 0}

    def vendor():
        if failure == "dns":
            raise updater.VendorError("vendor-unavailable")
        return {"versions": versions}

    def download(_, lock_fd):
        if failure == "download":
            raise updater.VendorError("freshclam-failed")
        assert failure == "cdn"

    monkeypatch.setattr(updater, "query_vendor", vendor)
    monkeypatch.setattr(updater, "download", download)
    monkeypatch.setattr(updater, "database_snapshot", lambda _, **kwargs: {"versions": VERSIONS})
    status = Mock()
    monkeypatch.setattr(updater, "publish_status", status)
    assert not updater.run_once(tmp_path)
    assert status.call_args.args[1]["state"] == "failed"


def test_detection_revokes_previous_receipt_before_inspecting_disk(tmp_path, monkeypatch):
    newer = {**VERSIONS, "main": 4}
    previous = {
        "source": "current.cvd.clamav.net",
        "record_timestamp": 1,
        "checked_at": 2,
        "versions": VERSIONS,
        "state": "current",
        "database": {"versions": VERSIONS},
    }
    monkeypatch.setattr(updater, "read_status", lambda _: previous)
    monkeypatch.setattr(updater, "query_vendor", lambda: {**previous, "versions": newer})
    states = []
    monkeypatch.setattr(updater, "publish_status", lambda _, result: states.append(result["state"]))

    def disk_matches(*args):
        assert states == ["update-required"]
        return True

    monkeypatch.setattr(updater, "disk_matches", disk_matches)
    monkeypatch.setattr(updater, "database_snapshot", lambda _: {"versions": newer})
    monkeypatch.setattr(updater, "download", lambda *_: None)
    assert updater.run_once(tmp_path)


def test_external_lock_covers_probe_until_publication(tmp_path, monkeypatch):
    import fcntl

    def check_lock():
        with (tmp_path / "openclaw-updater.lock").open("rb") as other, pytest.raises(BlockingIOError):
            fcntl.flock(other.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    def query():
        check_lock()
        return {"versions": VERSIONS}

    monkeypatch.setattr(updater, "query_vendor", query)
    monkeypatch.setattr(updater, "database_snapshot", lambda _, **kwargs: {"versions": VERSIONS})
    monkeypatch.setattr(updater, "download", lambda *_: check_lock())
    monkeypatch.setattr(updater, "publish_status", lambda *_: check_lock())
    assert updater.run_once(tmp_path)


def test_bootstrap_checks_again_after_download(tmp_path, monkeypatch):
    probe = Mock(return_value={"versions": VERSIONS})
    download = Mock()
    monkeypatch.setattr(updater, "query_vendor", probe)
    monkeypatch.setattr(updater, "download", download)
    monkeypatch.setattr(updater, "database_snapshot", lambda _, **kwargs: {"versions": VERSIONS})
    monkeypatch.setattr(updater, "publish_status", Mock())
    assert updater.run_once(tmp_path)
    download.assert_called_once_with(tmp_path, ANY)
    assert probe.call_count == 2


def test_symlink_database_never_creates_a_lock_in_target(tmp_path):
    external = tmp_path / "external"
    external.mkdir()
    database = tmp_path / "db"
    database.symlink_to(external)
    with pytest.raises((OSError, updater.VendorError)):
        updater.run_once(database)
    assert not list(external.iterdir())


def test_periodic_download_inherits_lock_until_freshclam_exits(tmp_path, monkeypatch):
    import os
    import subprocess

    def run(args, **kwargs):
        [lock_fd] = kwargs["pass_fds"]
        assert os.fstat(lock_fd).st_ino == (tmp_path / "openclaw-updater.lock").stat().st_ino
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr(updater.subprocess, "run", run)
    monkeypatch.setattr(updater, "query_vendor", lambda: {"versions": VERSIONS})
    monkeypatch.setattr(updater, "database_snapshot", lambda _, **kwargs: {"versions": VERSIONS})
    monkeypatch.setattr(updater, "publish_status", Mock())
    assert updater.run_once(tmp_path, maintenance=True)


def test_poll_schedule_includes_time_spent_in_probe(monkeypatch):
    import sys

    clock = [1000.0]
    monkeypatch.setattr(sys, "argv", ["clamav-update"])
    monkeypatch.setattr(updater.time, "monotonic", lambda: clock[0])

    def run_once(*args, **kwargs):
        clock[0] += 9
        return True

    monkeypatch.setattr(updater, "run_once", run_once)

    def sleep(seconds):
        assert seconds == 291
        raise KeyboardInterrupt

    monkeypatch.setattr(updater.time, "sleep", sleep)
    with pytest.raises(KeyboardInterrupt):
        updater.main()


@pytest.mark.parametrize("previous", ["updating", "failed"])
def test_partial_update_requires_successful_freshclam_even_when_headers_match(
    tmp_path, monkeypatch, previous
):
    obs = {"source": "current.cvd.clamav.net", "record_timestamp": 1, "checked_at": 2, "versions": VERSIONS}
    monkeypatch.setattr(updater, "read_status", lambda _: {**obs, "state": previous, "retry_required": True})
    monkeypatch.setattr(updater, "query_vendor", lambda: obs)
    monkeypatch.setattr(updater, "database_snapshot", lambda _, **kwargs: {"versions": VERSIONS})
    download = Mock(side_effect=updater.VendorError("freshclam-failed"))
    monkeypatch.setattr(updater, "download", download)
    status = Mock()
    monkeypatch.setattr(updater, "publish_status", status)
    assert not updater.run_once(tmp_path)
    download.assert_called_once()
    assert status.call_args.args[1]["retry_required"] is True


def test_failed_probe_preserves_observed_floor_and_blocks_vendor_regression(tmp_path, monkeypatch):
    import time

    from test_clamav_vendor import VERSIONS as installed
    from test_clamav_vendor import databases

    from personal_assistant.clamav_vendor import vendor_readiness

    databases(tmp_path)
    now = time.time()
    base = {"source": "current.cvd.clamav.net", "checked_at": now, "record_timestamp": now}
    current = {**base, "versions": installed}
    newer = {**base, "versions": {**installed, "main": installed["main"] + 1}}
    monkeypatch.setattr(
        updater,
        "query_vendor",
        Mock(
            side_effect=[
                current,
                current,
                newer,
                updater.VendorError("vendor-probe-timeout"),
                current,
            ]
        ),
    )
    monkeypatch.setattr(
        updater, "download", Mock(side_effect=[None, updater.VendorError("freshclam-failed")])
    )
    assert updater.run_once(tmp_path)
    assert vendor_readiness(tmp_path)["ok"]
    for expected in ("freshclam-failed", "vendor-probe-timeout", "vendor-version-regression"):
        assert not updater.run_once(tmp_path)
        receipt = updater.read_status(tmp_path)
        assert receipt["error"] == expected
        assert receipt["versions"] == newer["versions"]
        assert not vendor_readiness(tmp_path)["ok"]


def test_periodic_revalidation_keeps_verified_generation_current(tmp_path, monkeypatch):
    from test_clamav_vendor import current

    prior = current(tmp_path)
    monkeypatch.setattr(updater, "query_vendor", lambda: prior)
    snapshot = Mock(wraps=updater.database_snapshot)
    monkeypatch.setattr(updater, "database_snapshot", snapshot)
    publish = Mock(wraps=updater.publish_status)
    monkeypatch.setattr(updater, "publish_status", publish)
    monkeypatch.setattr(updater, "download", lambda *_: pytest.fail("unnecessary write"))
    assert updater.run_once(tmp_path, maintenance=True)
    snapshot.assert_called_once()
    assert [call.args[1]["state"] for call in publish.call_args_list] == ["current"]


@pytest.mark.parametrize(
    "offers,installed,states",
    [
        ([2, 3, 3], [1, 2, 3], ["update-required", "updating", "update-required", "updating", "current"]),
        ([2, 2], [1, 1], ["update-required", "updating", "failed"]),
        ([2, 3, 4], [1, 2, 3], ["update-required", "updating", "update-required", "updating", "failed"]),
    ],
)
def test_distinct_newer_offer_gets_one_immediate_followup(tmp_path, monkeypatch, offers, installed, states):
    def observations(values):
        return [{"versions": {**VERSIONS, "main": value}} for value in values]

    monkeypatch.setattr(updater, "query_vendor", Mock(side_effect=observations(offers)))
    monkeypatch.setattr(updater, "database_snapshot", Mock(side_effect=observations(installed)))
    download = Mock()
    monkeypatch.setattr(updater, "download", download)
    publish = Mock()
    monkeypatch.setattr(updater, "publish_status", publish)
    assert updater.run_once(tmp_path) is (states[-1] == "current")
    assert [call.args[1]["state"] for call in publish.call_args_list] == states
    assert download.call_count == states.count("updating")
    assert publish.call_args.args[1]["versions"]["main"] == offers[-1]
