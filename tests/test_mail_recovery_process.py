from __future__ import annotations

import fcntl
import os
import subprocess
import sys
import time
from dataclasses import replace

import pytest

from personal_assistant import mail_worker
from personal_assistant.job_control import JobController, default_job_specs


@pytest.mark.parametrize("stage", ["worker", "controller-readiness", "controller-dry-run"])
def test_watchdog_cleans_up_a_descendant_holding_capture_pipes(stage, monkeypatch, tmp_path):
    lock_path = tmp_path / "lock"
    ready_path = tmp_path / "ready"
    spawn = "import subprocess, sys; subprocess.run(sys.argv[1:6], check=True)"
    hold = """\
import fcntl, pathlib, sys, time
with open(sys.argv[1], 'a') as handle:
    fcntl.flock(handle, fcntl.LOCK_EX)
    pathlib.Path(sys.argv[2]).touch()
    time.sleep(4)
"""
    real_popen = subprocess.Popen

    def short_watchdog(*args, **kwargs):
        child = real_popen(*args, **kwargs)
        communicate = child.communicate

        def expire(input=None, timeout=None):
            if timeout is not None:
                deadline = time.monotonic() + 2
                while not ready_path.exists() and time.monotonic() < deadline:
                    time.sleep(0.01)
                assert ready_path.exists()
                timeout = 0.05
            return communicate(input, timeout)

        child.communicate = expire
        return child

    monkeypatch.setattr(subprocess, "Popen", short_watchdog)
    command = (sys.executable, "-c", spawn, sys.executable, "-c", hold, str(lock_path), str(ready_path))
    spec = next(item for item in default_job_specs() if item.name == "mail")
    controller = JobController(state_path=tmp_path / "state.json", specs=(spec,))
    start = time.monotonic()
    if stage == "worker":
        result = mail_worker._run_capture(command, environment=dict(os.environ))
        returncode = result.returncode
    elif stage == "controller-readiness":
        readiness = controller._mail_readiness(replace(spec, readiness_command=command))
        returncode = readiness["returncode"]
    else:
        monkeypatch.setattr(controller, "_quiesce_mail_for_recovery", lambda _: {"ok": True})
        monkeypatch.setattr(controller, "_mail_lock_status", lambda: {"locked": False})
        monkeypatch.setattr(controller, "_restore_mail_timer", lambda _: {"ok": True})
        monkeypatch.setattr(controller, "_mail_readiness", lambda _: {
            "ok": False, "auto_recoverable": True, "returncode": 4,
            "gate": {"current_fingerprint": "2" * 64},
        })
        recovery = controller._recover_mail_production_gate(replace(spec, automatic_recovery_command=command))
        returncode = recovery["dry_run"]["returncode"]
    assert time.monotonic() - start < 3
    assert returncode == 124
    with lock_path.open() as handle:
        deadline = time.monotonic() + 0.5
        while True:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                assert time.monotonic() < deadline, "capture descendant still owns its lock"
                time.sleep(0.01)
    assert mail_worker.CHILD is None
