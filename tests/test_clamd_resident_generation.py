from pathlib import Path
from unittest.mock import patch

from personal_assistant.clamd_health import resident_readiness


def test_resident_without_receipt_is_unverified(tmp_path: Path):
    current = {"ok": True, "generation": "a"*64, "versions": {"daily": 7}}
    with patch("personal_assistant.clamd_health.vendor_readiness", return_value=current):
        result = resident_readiness(tmp_path / "socket", database_dir=tmp_path,
                                    version="ClamAV 1/7/Mon May  1 00:00:00 2023")
    assert not result["ok"]
    assert "resident-unverified" in result["reasons"]


def test_supervisor_restarts_for_main_and_bytecode_changes_with_same_daily(tmp_path, monkeypatch):
    from unittest.mock import Mock

    from personal_assistant import clamd_runner as runner

    path = tmp_path / "clamd.sock"
    stop = Mock()
    stop.is_set.return_value = False
    versions = {"main": 1, "daily": 7, "bytecode": 2}
    children = []
    proofs = []

    def disk(_):
        return {"ok": True, "database": dict(versions), "generation": str(tuple(versions.values())),
                "versions": dict(versions)}

    def spawn(_):
        child = Mock(pid=100 + len(children))
        child.poll.return_value = None
        children.append(child)
        return child

    def next_pass(_):
        if len(proofs) == 1:
            versions["main"] += 1
        elif len(proofs) == 2:
            versions["bytecode"] += 1
        else:
            stop.is_set.return_value = True

    stop.wait.side_effect = next_pass
    monkeypatch.setattr(runner.subprocess, "Popen", spawn)
    monkeypatch.setattr(runner, "vendor_readiness", disk)
    monkeypatch.setattr(runner, "verify_child", lambda path, pid: None)
    monkeypatch.setattr(runner, "socket_identity", lambda path: [1, 2, 3])
    client = Mock()
    client.version.return_value = "ClamAV 1.4.3/7/Mon May  1 00:00:00 2023"
    monkeypatch.setattr(runner, "ClamdUnixClient", lambda *a, **kw: client)
    monkeypatch.setattr(runner, "publish", lambda path, proof: proofs.append(proof))
    assert runner.supervise(tmp_path, path, stop, 30) == 0
    assert len(children) == len(proofs) == 3
    assert len({proof["instance"] for proof in proofs}) == 3
    assert len({proof["generation"] for proof in proofs}) == 3
    for child in children:
        child.terminate.assert_called_once()


def test_dead_child_cannot_attest_and_stale_receipt_is_revoked(tmp_path, monkeypatch):
    import threading
    from unittest.mock import Mock

    from personal_assistant import clamd_runner as runner

    receipt = tmp_path / runner.RECEIPT
    receipt.write_text("old")
    child = Mock()
    child.poll.return_value = 1
    monkeypatch.setattr(runner.subprocess, "Popen", lambda *_: child)
    monkeypatch.setattr(runner, "vendor_readiness", lambda _: {"ok": True})
    assert runner.supervise(tmp_path, tmp_path / "socket", threading.Event(), 10) == 1
    assert not receipt.exists()
